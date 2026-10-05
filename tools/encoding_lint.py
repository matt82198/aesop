#!/usr/bin/env python3
"""Guardrail G10: encoding lint — explicit encoding AND an explicit error handler.
INDEX: Encoding lint: flags `open()` without `encoding=`; `subprocess.run/check_output/Popen` with `text=True`/`universal_newlines=True` and no `encoding=` (the Windows cp1252 trap that crashed metrics_gate on a binary diff); and `subprocess.*` that sets `encoding=` without a safe `errors=` handler (strict decoding of one 0x97 byte killed the reader thread and crashed the merge queue on 24+ consecutive passes)

Scans Python files for:
  1. `open()` calls missing explicit `encoding=` parameter
  2. `subprocess.run/check_output/Popen` calls with text=True or universal_newlines=True
     without explicit encoding= parameter
  3. `subprocess.run/check_output/Popen` calls that set `encoding=` but no (or an
     unsafe) `errors=` handler

Flags:
  - `open(path)` or `open(path, 'r')` or `open(path, 'w')` WITHOUT `encoding=`
  - `subprocess.run(..., text=True)` WITHOUT `encoding=`
  - `subprocess.check_output(..., text=True)` WITHOUT `encoding=`
  - `subprocess.Popen(..., universal_newlines=True)` WITHOUT `encoding=`
  - `subprocess.run(..., encoding='utf-8')` WITHOUT `errors=`, or with
    `errors='ignore'` / `errors='strict'`

Allows: binary mode (`'rb'`, `'wb'`) — no encoding needed.
Suppression: `# encoding-ok` inline comment.

This guardrail prevents cp1252/locale-encoding surprises on Windows
(e.g., tracker.json UnicodeDecodeError, subprocess text=True failure) AND the
strict-decoding crash class: `encoding='utf-8'` with the default strict handler
raises UnicodeDecodeError inside subprocess's reader THREAD, which never
propagates to the caller — stdout comes back None and the caller dies on a
misleading `.strip()` AttributeError. Requiring `encoding=` without requiring
`errors=` is why this lint passed while the merge queue was hard down.

CLI:
  --check (default, exit 1 on findings)
  --json (machine-readable output)
  --paths DIR... (scan specific directories instead of defaults)
  --root DIR (repository root, used for relative paths in output)

Exit: 0=clean, 1=findings, 2=error
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


# Error handlers a decoding subprocess call may declare. 'replace' is the
# house default because it keeps an undecodable byte VISIBLE as U+FFFD.
# 'ignore' is deliberately absent: it deletes the byte, so a corrupted branch
# name or PR title silently becomes a different, plausible-looking string that
# the caller then acts on. 'backslashreplace' and 'surrogateescape' are allowed
# because both are lossless and reversible.
ALLOWED_SUBPROCESS_ERROR_HANDLERS = frozenset({
    'replace', 'backslashreplace', 'surrogateescape',
})


def _errors_keyword(node: ast.Call):
    """Return the `errors=` keyword node, or None when the call omits it."""
    for keyword in node.keywords:
        if keyword.arg == 'errors':
            return keyword
    return None


class SubprocessVisitor(ast.NodeVisitor):
    """Finds subprocess.run/check_output/Popen decoding calls that are unsafe.

    Two distinct findings, both of which have crashed production here:

    1. `text=True` with no `encoding=` -- the process inherits the Windows
       cp1252 locale and dies on any non-ASCII byte.
    2. `encoding=` with no (or a lossy) `errors=` -- strict UTF-8 decoding.
       This is the gap that let the merge queue crash on 24+ consecutive
       passes while this very lint reported clean: git emitted a cp1252
       em-dash (0x97), the decode raised UnicodeDecodeError inside
       subprocess's reader THREAD, `result.stdout` came back None, and the
       caller's `.strip()` died with a misleading AttributeError. Guardrail
       G10 mandated the encoding but not the handler, so the rule was prose
       the linter did not enforce.
    """

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

            line_content = (
                self.source_lines[node.lineno - 1]
                if node.lineno <= len(self.source_lines)
                else ""
            )
            has_encoding = _has_encoding_keyword(node)

            # (1) text=True with no encoding= -- inherits the cp1252 locale.
            if _has_text_flag(node) and not has_encoding:
                self.findings.append({
                    'file': str(self.filename),
                    'line': node.lineno,
                    'col': node.col_offset,
                    'message': (
                        f"subprocess.{func_name}() call with text=True/universal_newlines=True "
                        f"without encoding= parameter; use encoding='utf-8' or add # encoding-ok comment"
                    ),
                    'code': line_content.strip(),
                })

            # (2) encoding= with no (or a lossy) errors= -- strict decoding.
            # `encoding=` alone makes this a DECODING call regardless of whether
            # text= was also spelled out, so this check keys off encoding=.
            if has_encoding:
                errors_kw = _errors_keyword(node)
                if errors_kw is None:
                    self.findings.append({
                        'file': str(self.filename),
                        'line': node.lineno,
                        'col': node.col_offset,
                        'message': (
                            f"subprocess.{func_name}() call sets encoding= but not errors=; "
                            f"strict decoding kills subprocess's reader thread on one bad byte "
                            f"and leaves stdout None. Use errors='replace' (or an explicit "
                            f"{sorted(ALLOWED_SUBPROCESS_ERROR_HANDLERS)}), never 'ignore'"
                        ),
                        'code': line_content.strip(),
                    })
                elif isinstance(errors_kw.value, ast.Constant):
                    handler = errors_kw.value.value
                    if handler not in ALLOWED_SUBPROCESS_ERROR_HANDLERS:
                        self.findings.append({
                            'file': str(self.filename),
                            'line': node.lineno,
                            'col': node.col_offset,
                            'message': (
                                f"subprocess.{func_name}() uses errors={handler!r}; "
                                f"undecodable bytes must stay visible. Allowed: "
                                f"{sorted(ALLOWED_SUBPROCESS_ERROR_HANDLERS)} "
                                f"('ignore' and 'strict' are rejected on purpose)"
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


def run(
    paths: Optional[List[str]] = None,
    root: Optional[Path] = None,
    json_output: bool = False,
) -> int:
    """Main scan and report.

    Args:
        paths: Specific directories to scan (or None for defaults)
        root: Repository root (for relative paths in output)
        json_output: If True, output JSON instead of text

    Returns: Exit code (0=clean, 1=findings, 2=error)
    """
    if root is None:
        root = Path.cwd()
    else:
        root = Path(root).resolve()

    # Default scan directories
    if not paths:
        paths = [
            'tools',
            'ui',
            'state_store',
            'driver',
            'monitor',
            'bin',
        ]

    # Resolve paths relative to root
    scan_dirs = []
    for p in paths:
        ppath = Path(p) if Path(p).is_absolute() else root / p
        ppath = ppath.resolve()
        if not ppath.exists():
            print(f"Warning: path {ppath} does not exist", file=sys.stderr)
            continue
        scan_dirs.append(ppath)

    # Scan all directories
    all_findings = []
    for scan_dir in scan_dirs:
        if scan_dir.is_file():
            findings = scan_file(scan_dir)
            all_findings.extend(findings)
        else:
            findings = scan_directory(scan_dir, root)
            all_findings.extend(findings)

    # Output results
    if json_output:
        output = {
            'findings': all_findings,
            'count': len(all_findings),
            'root': str(root),
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

    return 1 if all_findings else 0


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

    args = parser.parse_args()

    try:
        exit_code = run(
            paths=args.paths,
            root=args.root,
            json_output=args.json,
        )
        sys.exit(exit_code)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
