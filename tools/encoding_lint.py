#!/usr/bin/env python3
"""Guardrail G10: encoding lint — enforce explicit encoding='utf-8' on file opens and subprocess.

Scans Python files for:
  1. `open()` calls missing explicit `encoding=` parameter
  2. `subprocess.run/check_output/Popen` calls with text=True or universal_newlines=True
     without explicit encoding= parameter

Flags:
  - `open(path)` or `open(path, 'r')` or `open(path, 'w')` WITHOUT `encoding=`
  - `subprocess.run(..., text=True)` WITHOUT `encoding=`
  - `subprocess.check_output(..., text=True)` WITHOUT `encoding=`
  - `subprocess.Popen(..., universal_newlines=True)` WITHOUT `encoding=`

Allows: binary mode (`'rb'`, `'wb'`) — no encoding needed.
Suppression: `# encoding-ok` inline comment.

This guardrail prevents cp1252/locale-encoding surprises on Windows
(e.g., tracker.json UnicodeDecodeError, subprocess text=True failure).

Scope: tools, ui, state_store, driver, monitor, bin, tests. tests/ is in
scope because subprocess.run(..., text=True) calls that spawn a tool under
test hit the exact same Windows-decode trap as production code -- a test
harness is not exempt from the invariant it is asserting on (root cause of
the fix/merge-train-utf8-stdout CI escape: tests/test_merge_train.py spawns
tools/merge_train.py via subprocess.run(text=True) with no encoding=).

RATCHET MODE (baseline, mirrors tools/stateapi_lint.py):
  Violations are keyed by `<relative-file>@<kind>` (not line numbers, so
  unrelated edits above a finding never shift its key). Without
  --update-baseline:
    - FAILS (exit 1) if the current scan has a key absent from the baseline
      (a genuinely NEW violation)
    - FAILS (exit 1) if the baseline has a key absent from the current scan
      (a STALE baseline entry -- the violation was fixed; the baseline must
      be regenerated so it doesn't silently paper over a future regression)
    - PASSES (exit 0) only when the current scan matches the baseline exactly
  With --update-baseline: writes the current scan as the new baseline and
  exits 0. CI/pre-push must never pass --update-baseline.
  Default baseline file: <root>/.encoding-baseline.json (override with
  --baseline PATH). A missing baseline file is treated as an empty baseline,
  so any existing findings are reported as new (fail-closed) -- there is no
  way to silently accept a debt that was never explicitly baselined.

CLI:
  --check (default, exit 1 on findings)
  --json (machine-readable output)
  --paths DIR... (scan specific directories instead of defaults)
  --root DIR (repository root, used for relative paths in output)
  --baseline PATH (baseline file location; default <root>/.encoding-baseline.json)
  --update-baseline (regenerate the baseline from the current scan; never use in CI)

Exit: 0=clean (matches baseline exactly), 1=findings (new or stale baseline
entries), 2=error (including: no configured scan path exists on disk --
this tool never reports clean having scanned nothing)
"""

import ast
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _is_binary_mode(mode_arg: Optional[str]) -> bool:
    """Check if a mode string indicates binary mode ('rb', 'wb', 'ab', etc.)."""
    if mode_arg is None:
        return False
    mode_arg = mode_arg.strip('\'"')
    return 'b' in mode_arg


def _has_encoding_keyword(node: ast.Call) -> bool:
    """Check if a call node has an `encoding=` keyword argument."""
    for keyword in node.keywords:
        if keyword.arg == 'encoding':
            return True
    return False


def _has_suppression_comment(source_lines: List[str], node_lineno: int) -> bool:
    """Check if the line has a `# encoding-ok` suppression comment."""
    if node_lineno <= 0 or node_lineno > len(source_lines):
        return False
    line = source_lines[node_lineno - 1]
    return '# encoding-ok' in line


class OpenCallVisitor(ast.NodeVisitor):
    """AST visitor that finds `open()` calls without encoding parameter."""

    def __init__(self, source_lines: List[str], filename: Path):
        self.findings: List[Dict] = []
        self.source_lines = source_lines
        self.filename = filename

    def visit_Call(self, node: ast.Call) -> None:
        """Visit a function call node."""
        # Check if this is an `open()` call
        is_open = (
            isinstance(node.func, ast.Name) and
            node.func.id == 'open'
        )

        if is_open:
            # Check if already suppressed
            if _has_suppression_comment(self.source_lines, node.lineno):
                self.generic_visit(node)
                return

            # Extract mode from positional or keyword arguments
            mode_arg = None
            if len(node.args) >= 2:
                # open(path, mode, ...)
                if isinstance(node.args[1], ast.Constant):
                    mode_arg = node.args[1].value
            else:
                # Look for mode= keyword argument
                for keyword in node.keywords:
                    if keyword.arg == 'mode':
                        if isinstance(keyword.value, ast.Constant):
                            mode_arg = keyword.value.value
                        break

            # Check if binary mode (no encoding needed)
            if not _is_binary_mode(mode_arg):
                # Check if encoding= keyword is present
                if not _has_encoding_keyword(node):
                    line_content = (
                        self.source_lines[node.lineno - 1]
                        if node.lineno <= len(self.source_lines)
                        else ""
                    )
                    self.findings.append({
                        'file': str(self.filename),
                        'line': node.lineno,
                        'col': node.col_offset,
                        'kind': 'open-no-encoding',
                        'message': (
                            f"open() call without encoding= parameter "
                            f"(mode={repr(mode_arg) if mode_arg else 'default'}); "
                            f"use encoding='utf-8' or add # encoding-ok comment"
                        ),
                        'code': line_content.strip(),
                    })

        self.generic_visit(node)


def _has_text_flag(node: ast.Call) -> bool:
    """Check if subprocess call has text=True or universal_newlines=True."""
    for keyword in node.keywords:
        if keyword.arg in ('text', 'universal_newlines'):
            if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                return True
    return False


class SubprocessVisitor(ast.NodeVisitor):
    """AST visitor that finds subprocess.run/check_output/Popen calls with text=True but no encoding."""

    def __init__(self, source_lines: List[str], filename: Path):
        self.findings: List[Dict] = []
        self.source_lines = source_lines
        self.filename = filename

    def visit_Call(self, node: ast.Call) -> None:
        """Visit a function call node."""
        # Check if this is a subprocess.run/check_output/Popen call
        is_subprocess = False
        func_name = None

        # Form: subprocess.run(...), subprocess.check_output(...), subprocess.Popen(...)
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess':
                func_name = node.func.attr
                if func_name in ('run', 'check_output', 'Popen'):
                    is_subprocess = True

        if is_subprocess:
            # Check if already suppressed
            if _has_suppression_comment(self.source_lines, node.lineno):
                self.generic_visit(node)
                return

            # Check if this call has text=True or universal_newlines=True
            if _has_text_flag(node):
                # Check if encoding= keyword is present
                if not _has_encoding_keyword(node):
                    line_content = (
                        self.source_lines[node.lineno - 1]
                        if node.lineno <= len(self.source_lines)
                        else ""
                    )
                    self.findings.append({
                        'file': str(self.filename),
                        'line': node.lineno,
                        'col': node.col_offset,
                        'kind': f'subprocess-{func_name}-no-encoding',
                        'message': (
                            f"subprocess.{func_name}() call with text=True/universal_newlines=True "
                            f"without encoding= parameter; use encoding='utf-8' or add # encoding-ok comment"
                        ),
                        'code': line_content.strip(),
                    })

        self.generic_visit(node)


def scan_file(filepath: Path) -> List[Dict]:
    """Scan a single Python file for encoding issues.

    Returns list of finding dicts with 'file', 'line', 'col', 'message', 'code'.
    """
    findings = []

    try:
        source = filepath.read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError) as e:
        return [{
            'file': str(filepath),
            'line': 0,
            'col': 0,
            'kind': 'io-error',
            'message': f"Failed to read file: {e}",
            'code': "",
        }]

    # Parse as AST
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [{
            'file': str(filepath),
            'line': e.lineno or 0,
            'col': e.offset or 0,
            'kind': 'syntax-error',
            'message': f"Syntax error: {e.msg}",
            'code': "",
        }]

    source_lines = source.split('\n')
    findings = []

    # Check for open() calls without encoding
    open_visitor = OpenCallVisitor(source_lines, filepath)
    open_visitor.visit(tree)
    findings.extend(open_visitor.findings)

    # Check for subprocess calls with text=True but no encoding
    subprocess_visitor = SubprocessVisitor(source_lines, filepath)
    subprocess_visitor.visit(tree)
    findings.extend(subprocess_visitor.findings)

    return findings


def violation_key(finding: Dict, root: Path) -> str:
    """Stable baseline key for a finding: `<relative-file>@<kind>`.

    Deliberately NOT line-number-based (mirrors tools/stateapi_lint.py):
    an edit elsewhere in the file must not shift every key below it and
    make the ratchet report phantom new/stale entries. One key is shared
    by every occurrence of the same kind in the same file (dedup), so
    fixing some-but-not-all instances of a kind in an already-baselined
    file doesn't spuriously fail the ratchet either.
    """
    try:
        rel = Path(finding['file']).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        rel = str(finding['file']).replace('\\', '/')
    kind = finding.get('kind', 'unknown')
    return f"{rel}@{kind}"


def load_baseline(baseline_file: Path) -> List[str]:
    """Load the baseline violations list. Missing file -> empty baseline (fail-closed:
    every current finding is then reported as new, never silently accepted)."""
    baseline_file = Path(baseline_file)
    if not baseline_file.exists():
        return []
    try:
        data = json.loads(baseline_file.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        raise ValueError(f"Corrupt baseline file {baseline_file}: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get('violations'), list):
        raise ValueError(f"Malformed baseline file {baseline_file}: expected {{'violations': [...]}}")
    return [str(v) for v in data['violations']]


def save_baseline(baseline_file: Path, violations: List[str]) -> None:
    """Write the baseline file (sorted, deterministic diffs)."""
    baseline_file = Path(baseline_file)
    baseline_file.parent.mkdir(parents=True, exist_ok=True)
    baseline_file.write_text(
        json.dumps({'violations': sorted(set(violations))}, indent=2) + '\n',
        encoding='utf-8',
    )


def check_ratchet(
    baseline_violations: List[str],
    current_violations: List[str],
) -> Tuple[bool, List[str], List[str]]:
    """Compare baseline vs current violation keys.

    Returns (is_ok, stale_entries, new_violations):
      - stale_entries: in baseline but not current (fixed; baseline needs --update-baseline)
      - new_violations: in current but not baseline (a genuinely new finding; fail-closed)
      - is_ok is True only when both lists are empty (exact match).
    """
    baseline_set = set(baseline_violations)
    current_set = set(current_violations)
    stale = sorted(baseline_set - current_set)
    new = sorted(current_set - baseline_set)
    return (not stale and not new), stale, new


def scan_directory(dirpath: Path, repo_root: Optional[Path] = None) -> List[Dict]:
    """Recursively scan a directory for Python files.

    Args:
        dirpath: Directory to scan
        repo_root: Repository root for relative paths in output

    Returns: List of all findings
    """
    all_findings = []

    # Find all .py files
    for pyfile in sorted(dirpath.rglob('*.py')):
        # Skip common junk directories
        parts = pyfile.parts
        if any(p in {'__pycache__', '.git', 'node_modules', 'dist', '.pytest_cache'} for p in parts):
            continue

        findings = scan_file(pyfile)
        all_findings.extend(findings)

    return all_findings


DEFAULT_SCAN_PATHS = [
    'tools',
    'ui',
    'state_store',
    'driver',
    'monitor',
    'bin',
    'tests',
]


def run(
    paths: Optional[List[str]] = None,
    root: Optional[Path] = None,
    json_output: bool = False,
    baseline: Optional[Path] = None,
    update_baseline: bool = False,
) -> int:
    """Main scan and report.

    Args:
        paths: Specific directories to scan (or None for DEFAULT_SCAN_PATHS)
        root: Repository root (for relative paths in output)
        json_output: If True, output JSON instead of text
        baseline: Baseline file path (default: <root>/.encoding-baseline.json)
        update_baseline: If True, write current findings as the new baseline and
            return 0 instead of checking. Never use in CI/pre-push.

    Returns: Exit code (0=clean/matches baseline, 1=findings, 2=error --
        including a scope that resolved to zero existing scan paths, which
        this tool always treats as COULD NOT EVALUATE, never as clean)
    """
    if root is None:
        root = Path.cwd()
    else:
        root = Path(root).resolve()

    # Default scan directories
    requested_paths = paths if paths else list(DEFAULT_SCAN_PATHS)

    # Resolve paths relative to root
    scan_dirs = []
    for p in requested_paths:
        ppath = Path(p) if Path(p).is_absolute() else root / p
        ppath = ppath.resolve()
        if not ppath.exists():
            print(f"Warning: path {ppath} does not exist", file=sys.stderr)
            continue
        scan_dirs.append(ppath)

    # Fail-closed: if none of the requested scan paths exist, this run
    # evaluated nothing. That is a COULD-NOT-EVALUATE condition, not a
    # clean pass -- a gate that reports 0 findings having scanned zero
    # files is indistinguishable from a broken gate and must never look
    # green (repo-wide invariant: exit 2 on "could not evaluate").
    if not scan_dirs:
        print(
            f"Error: none of the requested scan paths exist under {root} "
            f"({requested_paths}); scanned nothing",
            file=sys.stderr,
        )
        return 2

    # Scan all directories
    all_findings = []
    for scan_dir in scan_dirs:
        if scan_dir.is_file():
            findings = scan_file(scan_dir)
            all_findings.extend(findings)
        else:
            findings = scan_directory(scan_dir, root)
            all_findings.extend(findings)

    current_keys = sorted({violation_key(f, root) for f in all_findings})

    if baseline is None:
        baseline_file = root / '.encoding-baseline.json'
    else:
        baseline_file = Path(baseline) if Path(baseline).is_absolute() else root / baseline

    if update_baseline:
        save_baseline(baseline_file, current_keys)
        if json_output:
            print(json.dumps({
                'updated_baseline': str(baseline_file),
                'violation_count': len(current_keys),
            }, indent=2))
        else:
            print(f"[OK] Baseline updated: {baseline_file} ({len(current_keys)} violation keys)")
        return 0

    baseline_violations = load_baseline(baseline_file)
    is_ok, stale_entries, new_violations = check_ratchet(baseline_violations, current_keys)

    # Output results
    if json_output:
        output = {
            'findings': all_findings,
            'count': len(all_findings),
            'root': str(root),
            'baseline_file': str(baseline_file),
            'baseline_count': len(baseline_violations),
            'new_violations': new_violations,
            'stale_baseline_entries': stale_entries,
            'ok': is_ok,
        }
        print(json.dumps(output, indent=2))
    else:
        if all_findings:
            for i, finding in enumerate(all_findings, 1):
                rel_file = (
                    Path(finding['file']).relative_to(root)
                    if Path(finding['file']).is_relative_to(root)
                    else finding['file']
                )
                print(
                    f"{i}. {rel_file}:{finding['line']}:{finding['col']}: "
                    f"{finding['message']}"
                )
                if finding['code']:
                    print(f"   {finding['code']}")
        else:
            print("[OK] No encoding issues found")

        if new_violations:
            print(f"\nNEW violations not in baseline ({len(new_violations)}):")
            for key in new_violations:
                print(f"  + {key}")
        if stale_entries:
            print(f"\nSTALE baseline entries -- fixed? re-run with --update-baseline ({len(stale_entries)}):")
            for key in stale_entries:
                print(f"  - {key}")
        if is_ok and baseline_violations:
            print(f"\n[OK] Matches baseline exactly ({len(baseline_violations)} accepted violation keys)")

    return 0 if is_ok else 1


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Lint Python files for missing encoding= on file opens"
    )
    parser.add_argument(
        '--check',
        action='store_true',
        default=True,
        help='Check for issues (default mode)'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output as JSON'
    )
    parser.add_argument(
        '--paths',
        nargs='+',
        default=None,
        help='Specific directories or files to scan'
    )
    parser.add_argument(
        '--root',
        type=Path,
        default=None,
        help='Repository root (default: cwd)'
    )
    parser.add_argument(
        '--baseline',
        type=Path,
        default=None,
        help='Baseline file path (default: <root>/.encoding-baseline.json)'
    )
    parser.add_argument(
        '--update-baseline',
        action='store_true',
        help='Regenerate the baseline from the current scan and exit 0. '
             'CI/pre-push must never pass this flag.'
    )

    args = parser.parse_args()

    try:
        exit_code = run(
            paths=args.paths,
            root=args.root,
            json_output=args.json,
            baseline=args.baseline,
            update_baseline=args.update_baseline,
        )
        sys.exit(exit_code)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
