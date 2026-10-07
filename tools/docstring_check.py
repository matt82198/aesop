#!/usr/bin/env python3
"""Docstring coverage checker for Python code.
INDEX: AST-based docstring coverage checker for Python modules

AST-based tool that scans Python files for missing docstrings on public
functions and classes. Calculates coverage percentage and can enforce a
minimum threshold.

Features:
  1. Scans all public functions (not starting with _) and classes
  2. Checks for docstrings (triple-quoted strings or PEP 257 format)
  3. Reports missing docstrings with file path and line number
  4. Calculates docstring coverage percentage
  5. Supports `# docstring-ok` inline suppression
  6. Ignores test files, __init__.py, and private functions
  7. Supports --threshold to enforce minimum coverage

Exit codes: 0=above threshold, 1=below threshold or violations found, 2=error.

CLI:
  docstring_check.py [--check] [--json] [--threshold 50] [--paths DIR...]
                      [--root DIR]

  --check          Validate and report (default action).
  --json           Emit machine-readable JSON instead of ASCII text.
  --threshold N    Fail if coverage % below N (default: 0, report-only).
  --paths ITEM...  Override default scan targets (default: tools/ driver/ state_store/).
  --root PATH      Repository root used to resolve relative --paths (default: cwd).
"""

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SCAN_DIRS = ["tools", "driver", "state_store"]

SUPPRESS_MARKER = "# docstring-ok"


def _has_docstring(node: ast.AST) -> bool:
    """Check if a function or class node has a docstring."""
    return (
        ast.get_docstring(node) is not None
    )


def _get_line_suppression(source: str, lineno: int) -> bool:
    """Check if a line has a docstring-ok suppression marker."""
    lines = source.splitlines()
    if 0 <= lineno - 1 < len(lines):
        return SUPPRESS_MARKER in lines[lineno - 1]
    return False


def find_items_missing_docstrings(
    source: str, filename: str = "<string>"
) -> Tuple[List[Dict[str, Any]], int, int]:
    """AST-scan source for functions/classes missing docstrings.

    Returns a tuple of (findings_list, total_items, items_with_docstrings).
    Findings are dicts: {type, name, lineno, suppressed}.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return [], 0, 0

    findings: List[Dict[str, Any]] = []
    total_items = 0
    items_with_docstrings = 0

    for node in ast.walk(tree):
        # Check for public functions
        if isinstance(node, ast.FunctionDef):
            if node.name.startswith("_"):
                continue

            total_items += 1
            has_doc = _has_docstring(node)
            if has_doc:
                items_with_docstrings += 1
            else:
                suppressed = _get_line_suppression(source, node.lineno)
                findings.append({
                    "type": "function",
                    "name": node.name,
                    "lineno": node.lineno,
                    "suppressed": suppressed,
                })

        # Check for public classes
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue

            total_items += 1
            has_doc = _has_docstring(node)
            if has_doc:
                items_with_docstrings += 1
            else:
                suppressed = _get_line_suppression(source, node.lineno)
                findings.append({
                    "type": "class",
                    "name": node.name,
                    "lineno": node.lineno,
                    "suppressed": suppressed,
                })

    return findings, total_items, items_with_docstrings


def scan_file(path: Path) -> Tuple[List[Dict[str, Any]], int, int, int]:
    """Scan one Python file for missing docstrings.

    Returns a tuple of (findings, total_items, items_with_docs, unsuppressed_findings).
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return [], 0, 0, 0

    findings, total_items, items_with_docs = find_items_missing_docstrings(
        source, filename=str(path)
    )

    # Count unsuppressed findings
    unsuppressed = sum(1 for f in findings if not f["suppressed"])

    results: List[Dict[str, Any]] = []
    for finding in findings:
        results.append({
            "file": str(path),
            "type": finding["type"],
            "name": finding["name"],
            "line": finding["lineno"],
            "suppressed": finding["suppressed"],
        })

    return results, total_items, items_with_docs, unsuppressed


def should_scan_file(path: Path) -> bool:
    """Determine if a file should be scanned."""
    # Ignore test files
    if path.name.startswith("test_") or path.name.endswith(".test.py"):
        return False
    # Ignore __init__.py
    if path.name == "__init__.py":
        return False
    return True


def gather_targets(repo_root: Path, paths: Optional[List[str]]) -> List[Path]:
    """Resolve --paths (or the default scan dirs) into a sorted list of .py files."""
    items = paths if paths else DEFAULT_SCAN_DIRS
    files: List[Path] = []

    for item in items:
        p = Path(item)
        if not p.is_absolute():
            p = repo_root / item
        if p.is_dir():
            files.extend(sorted(f for f in p.glob("*.py") if should_scan_file(f)))
        elif p.is_file() and should_scan_file(p):
            files.append(p)
        # Nonexistent paths are silently skipped

    return sorted(set(files))  # Deduplicate


def run(repo_root: Path, paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run the docstring checker and return a result dict."""
    targets = gather_targets(repo_root, paths)
    findings: List[Dict[str, Any]] = []
    total_items = 0
    items_with_docs = 0
    total_unsuppressed = 0

    for f in targets:
        file_findings, file_total, file_docs, file_unsuppressed = scan_file(f)
        findings.extend(file_findings)
        total_items += file_total
        items_with_docs += file_docs
        total_unsuppressed += file_unsuppressed

    coverage = 0
    if total_items > 0:
        coverage = int((items_with_docs / total_items) * 100)

    return {
        "ok": total_unsuppressed == 0,
        "coverage_percent": coverage,
        "total_items": total_items,
        "items_with_docstrings": items_with_docs,
        "unsuppressed_findings": total_unsuppressed,
        "scanned_files": len(targets),
        "findings": findings,
    }


def format_ascii(result: Dict[str, Any]) -> str:
    """Format result as ASCII text."""
    lines = []
    coverage = result["coverage_percent"]
    total = result["total_items"]
    with_docs = result["items_with_docstrings"]
    unsuppressed = result["unsuppressed_findings"]
    suppressed = len(result["findings"]) - unsuppressed

    if result["findings"]:
        lines.append(
            f"docstring-check: {len(result['findings'])} finding(s) "
            f"in {result['scanned_files']} file(s)"
        )
        for f in result["findings"]:
            status = "[suppressed]" if f["suppressed"] else "[FAIL]"
            lines.append(
                f"  {status} {f['file']}:{f['line']}: {f['type']} "
                f"'{f['name']}' missing docstring"
            )
    else:
        lines.append(f"docstring-check: PASS (no findings)")

    lines.append("")
    lines.append(
        f"Coverage: {with_docs}/{total} ({coverage}%) | "
        f"Unsuppressed: {unsuppressed} | Suppressed: {suppressed}"
    )

    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Docstring coverage checker for Python code"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Validate and report (default action)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON output",
    )
    parser.add_argument(
        "--threshold", type=int, default=0,
        help="Fail if coverage %% below this (default: 0, report-only)",
    )
    parser.add_argument(
        "--paths", nargs="+", default=None,
        help="Override default scan targets (directories globbed for *.py, or individual files)",
    )
    parser.add_argument(
        "--root", default=".",
        help="Repository root used to resolve relative --paths / defaults",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    try:
        repo_root = Path(args.root).resolve()
        result = run(repo_root, args.paths)
    except Exception as e:
        sys.stderr.write(f"ERROR: docstring-check failed: {e}\n")
        return 2

    if args.json:
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    else:
        sys.stdout.write(format_ascii(result))

    # Determine exit code
    findings_exist = result["unsuppressed_findings"] > 0
    threshold_failed = result["coverage_percent"] < args.threshold
    if findings_exist or threshold_failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
