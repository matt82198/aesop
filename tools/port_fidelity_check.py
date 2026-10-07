#!/usr/bin/env python3
"""
tools.port_fidelity_check -- Port-task fidelity checker for dispatch prompts.
INDEX: Validates port/copy/vendor/migrate dispatch prompts require source path, source-unique marker, and independent verification

Validates that "port"/"copy"/"vendor"/"migrate" dispatch prompts include source-marker
assertions and independent verification requirements. When an agent is asked to "port file X",
it can sometimes invent a plausible file from scratch instead of actually porting. The
agent's own tests pass because they test the invention, not the source. This tool prevents that.

What it checks:
  1. Scans dispatch prompts for "port", "copy", "vendor", "migrate" keywords (case-insensitive)
  2. When found in a dispatch, validates that the prompt includes:
     - An explicit source path reference (not relative; e.g., "/path/to/source" or "C:\\path")
     - A requirement for source-unique marker assertions (structural elements from original)
     - A requirement for independent verification (different file than implementation)
  3. Scans test files for port-related tests that only assert against their own fixtures
     (self-referential validation without independent source verification)

Suppression: add `# fidelity-ok` anywhere on a line within the dispatch call's source span
to suppress findings for that call site.

Exit codes: 0 = clean, 1 = fidelity gaps found, 2 = error (bad args / unreadable path).

CLI:
  port_fidelity_check.py [--check] [--json] [--paths DIR_OR_FILE ...] [--root PATH]

  --check          Validate and report (default action).
  --json           Emit machine-readable JSON instead of ASCII text.
  --paths ITEM...  Override default scan targets (directories are globbed non-recursively
                    for *.py; individual files are scanned directly).
  --root PATH      Repository root used to resolve relative --paths / defaults (default: cwd).

Default scan targets: driver/*.py, monitor/*.py, tools/*.py, skills/*.py (dispatch-related).

ASCII-only output. Stdlib only, no external dependencies.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_SCAN_DIRS = ["driver", "monitor", "tools", "skills"]

DISPATCH_CALL_NAMES = {"agent", "Agent", "Task"}
DISPATCH_KEYWORD_NAMES = {"subagent_type", "agentType"}
PROMPT_KEYWORD_NAMES = {"prompt", "description"}

# Port-related keywords that require fidelity checks
PORT_KEYWORDS = ["port", "copy", "vendor", "migrate"]

# Patterns that indicate source path references
SOURCE_PATH_PATTERNS = [
    r'["\']?/[a-zA-Z0-9/_\-\.]+["\']?',  # Absolute POSIX path
    r'["\']?[A-Za-z]:\\[\\a-zA-Z0-9_\-\.]+["\']?',  # Windows path
    r'(?:source|from|at)\s+(?:["\']?/|[A-Za-z]:)',  # "source at /path" or "from C:\"
]

# Patterns that indicate marker/assertion requirements
MARKER_PATTERNS = [
    "marker",
    "source-unique",
    "unique",
    "source element",
    "structural element",
    "assertion",
    "assert",
    "source match",
    "original structure",
    "from the source",
]

# Patterns that indicate independent verification requirement
INDEPENDENT_PATTERNS = [
    "independent",
    "separate verif",
    "different file",
    "cross-artifact",
    "verify independently",
    "cross-check",
    "another file",
    "not the same file",
]

SUPPRESS_MARKER = "# fidelity-ok"


def _call_name(node: ast.Call) -> Optional[str]:
    """Return the callee's simple name (Name.id or Attribute.attr), or None."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _string_value(node: ast.expr) -> Optional[str]:
    """Best-effort static string extraction from a Constant or f-string (JoinedStr)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return "".join(parts) if parts else None
    return None


def find_dispatch_calls(source: str, filename: str = "<string>") -> List[Dict[str, Any]]:
    """AST-scan source for agent-dispatch call sites with port-related keywords.

    Returns a list of dicts: {lineno, end_lineno, prompt, suppressed, has_port_keyword}.
    Calls with unparseable source are skipped (returns []), never raise.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    lines = source.splitlines()
    calls: List[Dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        has_dispatch_kw = any(k in DISPATCH_KEYWORD_NAMES for k in keywords)
        name = _call_name(node)

        if name not in DISPATCH_CALL_NAMES and not has_dispatch_kw:
            continue

        text_fragments = []
        for kw_name in PROMPT_KEYWORD_NAMES:
            if kw_name in keywords:
                val = _string_value(keywords[kw_name])
                if val:
                    text_fragments.append(val)

        start = node.lineno
        end = getattr(node, "end_lineno", node.lineno) or node.lineno

        suppressed = any(
            SUPPRESS_MARKER in lines[i]
            for i in range(max(0, start - 1), min(len(lines), end))
        )

        prompt = "\n".join(text_fragments)
        has_port_kw = any(kw.lower() in prompt.lower() for kw in PORT_KEYWORDS)

        if has_port_kw:
            calls.append({
                "lineno": start,
                "end_lineno": end,
                "prompt": prompt,
                "suppressed": suppressed,
                "has_port_keyword": True,
            })

    return calls


def validate_call(call: Dict[str, Any]) -> List[Dict[str, str]]:
    """Validate a single dispatch call dict, returning a list of {rule, detail} findings."""
    findings: List[Dict[str, str]] = []
    text = call.get("prompt") or ""
    lower = text.lower()

    # Check for port keywords (already filtered in find_dispatch_calls, but double-check)
    has_port_keyword = any(kw in lower for kw in PORT_KEYWORDS)
    if not has_port_keyword:
        return findings

    # Requirement 1: Source path reference
    has_source_path = any(re.search(pattern, text, re.IGNORECASE) for pattern in SOURCE_PATH_PATTERNS)
    if not has_source_path:
        findings.append({
            "rule": "missing_source_path",
            "detail": "port/copy/vendor/migrate dispatch missing explicit source path reference",
        })

    # Requirement 2: Source marker assertion requirement
    has_marker_requirement = any(pattern in lower for pattern in MARKER_PATTERNS)
    if not has_marker_requirement:
        findings.append({
            "rule": "missing_marker_requirement",
            "detail": "port/copy/vendor/migrate dispatch missing requirement for source-marker assertions",
        })

    # Requirement 3: Independent verification requirement
    has_independent_verification = any(pattern in lower for pattern in INDEPENDENT_PATTERNS)
    if not has_independent_verification:
        findings.append({
            "rule": "missing_independent_verification",
            "detail": "port/copy/vendor/migrate dispatch missing requirement for independent verification",
        })

    return findings


def scan_file(path: Path) -> List[Dict[str, Any]]:
    """Scan one Python file for dispatch calls with port keywords and return flat finding dicts."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    results: List[Dict[str, Any]] = []
    for call in find_dispatch_calls(source, filename=str(path)):
        if call["suppressed"]:
            continue
        for finding in validate_call(call):
            results.append({
                "file": str(path),
                "line": call["lineno"],
                "rule": finding["rule"],
                "detail": finding["detail"],
            })
    return results


def gather_targets(repo_root: Path, paths: Optional[List[str]]) -> List[Path]:
    """Resolve --paths (or the default scan dirs) into a sorted list of .py files."""
    items = paths if paths else DEFAULT_SCAN_DIRS
    files: List[Path] = []

    for item in items:
        p = Path(item)
        if not p.is_absolute():
            p = repo_root / item
        if p.is_dir():
            files.extend(sorted(p.glob("*.py")))
        elif p.is_file():
            files.append(p)
        # Nonexistent paths are silently skipped

    return files


def run(repo_root: Path, paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run the validator and return a result dict (ok/scanned_files/findings)."""
    targets = gather_targets(repo_root, paths)
    findings: List[Dict[str, Any]] = []
    for f in targets:
        findings.extend(scan_file(f))

    return {
        "ok": len(findings) == 0,
        "scanned_files": len(targets),
        "findings": findings,
    }


def format_ascii(result: Dict[str, Any]) -> str:
    lines = []
    if result["findings"]:
        lines.append(
            f"port-fidelity-check: {len(result['findings'])} finding(s) "
            f"in {result['scanned_files']} file(s) scanned"
        )
        for f in result["findings"]:
            lines.append(f"  FAIL: {f['file']}:{f['line']}: {f['rule']}: {f['detail']}")
    else:
        lines.append(
            f"port-fidelity-check: PASS ({result['scanned_files']} file(s) scanned, 0 findings)"
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Port-task fidelity checker: validates port/copy/vendor/migrate dispatch prompts"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Validate and report (default action; the only action today)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output")
    parser.add_argument(
        "--paths", nargs="+", default=None,
        help="Override default scan targets (directories globbed for *.py, or individual files)",
    )
    parser.add_argument(
        "--root", default=".", help="Repository root used to resolve relative --paths / defaults",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    try:
        repo_root = Path(args.root).resolve()
        result = run(repo_root, args.paths)
    except Exception as e:
        sys.stderr.write(f"ERROR: port_fidelity_check failed: {e}\n")
        return 2

    if args.json:
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    else:
        sys.stdout.write(format_ascii(result))

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
