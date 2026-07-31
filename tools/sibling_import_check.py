#!/usr/bin/env python3
"""
tools.sibling_import_check -- Guardrail: detect bare sibling imports in tools/.

Detects bare sibling imports (e.g., `from lint_core import ...`) in tools/
modules that lack the sys.path guard. The guard ensures imports work when
tools are loaded by file path (as in the import-gate tests) without tools/
on sys.path.

Detects:
  - Top-level `from X import ...` where X is a tools/ module
  - Top-level `import X` where X is a tools/ module
  - Lacks preceding sys.path.insert(0, os.path.dirname(...)) in the file

Ignores:
  - `from tools.X import ...` (already path-safe, explicit package form)
  - Stdlib imports (sys, os, json, etc.)
  - Third-party imports (non-relative, non-local)
  - Imports in comments or strings

Exit codes:
  0 = clean (no bare sibling imports found)
  1 = violations found (unguarded sibling imports)
  2 = usage/scan error

Usage:
  python tools/sibling_import_check.py [--check] [--json] [--paths PATH...]
"""
import argparse
import ast
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional


class Finding:
    """Represents a single sibling import violation."""

    def __init__(self, file_path: str, module_name: str, line_number: int, import_form: str):
        """
        Args:
            file_path: Path to file with violation
            module_name: Name of sibling module being imported
            line_number: Line number of import statement
            import_form: Type of import (e.g., "from X import", "import X")
        """
        self.file_path = file_path
        self.module_name = module_name
        self.line_number = line_number
        self.import_form = import_form

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file_path,
            "module": self.module_name,
            "line": self.line_number,
            "form": self.import_form,
        }


def find_python_files(root: str) -> List[str]:
    """Find all .py files in tools/ directory."""
    tools_dir = Path(root) / "tools"
    if not tools_dir.exists():
        return []

    files = []
    for py_file in tools_dir.glob("*.py"):
        if py_file.is_file() and py_file.name != "__pycache__":
            files.append(str(py_file))
    return sorted(files)


def get_sibling_modules(root: str) -> set:
    """Get list of module names in tools/ that could be siblings."""
    tools_dir = Path(root) / "tools"
    if not tools_dir.exists():
        return set()

    modules = set()
    for py_file in tools_dir.glob("*.py"):
        if py_file.is_file():
            modules.add(py_file.stem)  # Remove .py extension

    return modules


def has_sys_path_guard(file_path: str) -> bool:
    """Check if file has the sys.path insert guard for tools/ directory."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return False

    # Look for the pattern: sys.path.insert(0, os.path.dirname(...__file__...))
    # This pattern is specifically for making the current file's directory importable
    return "sys.path.insert" in content and "dirname" in content and "__file__" in content


def extract_sibling_imports(
    file_path: str, sibling_modules: set, has_guard: bool
) -> List[Finding]:
    """Extract bare sibling imports from a Python file."""
    findings = []

    try:
        with open(file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_path)
    except (SyntaxError, OSError, UnicodeDecodeError):
        # Can't parse or read file; skip it
        return findings

    # Only flag violations if there's no guard at all
    if has_guard:
        return findings

    for node in ast.walk(tree):
        # Detect: from X import ...
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module in sibling_modules:
                # Check it's not already qualified (from tools.X)
                if not node.module.startswith("tools."):
                    findings.append(
                        Finding(
                            file_path,
                            node.module,
                            node.lineno,
                            f"from {node.module} import ...",
                        )
                    )

        # Detect: import X
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split(".")[0]  # Get base module name
                if name in sibling_modules:
                    findings.append(
                        Finding(file_path, name, node.lineno, f"import {alias.name}")
                    )

    return findings


def scan_tools_directory(root: str) -> Tuple[List[Finding], int]:
    """Scan tools/ directory for bare sibling imports.

    Returns:
        (list of findings, number of files scanned)
    """
    files = find_python_files(root)
    sibling_modules = get_sibling_modules(root)

    if not files:
        # No files to scan = nothing found
        return [], 0

    all_findings = []
    for file_path in files:
        has_guard = has_sys_path_guard(file_path)
        findings = extract_sibling_imports(file_path, sibling_modules, has_guard)
        all_findings.extend(findings)

    return all_findings, len(files)


def format_findings_text(findings: List[Finding]) -> str:
    """Format findings as human-readable text."""
    if not findings:
        return "Sibling import check: CLEAN"

    lines = [f"Sibling import check: {len(findings)} violation(s) found"]
    for finding in findings:
        lines.append(
            f"  {finding.file_path}:{finding.line_number}: {finding.import_form} (missing sys.path guard)"
        )
    return "\n".join(lines)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Detect bare sibling imports in tools/ without sys.path guard",
        epilog="Exit: 0=clean, 1=violations found, 2=error",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Run check (default behavior)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=["."],
        help="Root directories to scan (default: current directory)",
    )

    args = parser.parse_args()

    # Scan all provided paths
    all_findings = []
    total_files = 0

    for root_path in args.paths:
        if not os.path.isdir(root_path):
            if args.json:
                print(
                    json.dumps(
                        {
                            "status": "ERROR",
                            "findings": [],
                            "message": f"Path not found: {root_path}",
                        }
                    )
                )
            else:
                print(f"ERROR: Path not found: {root_path}", file=sys.stderr)
            return 2

        findings, file_count = scan_tools_directory(root_path)
        all_findings.extend(findings)
        total_files += file_count

    # Handle zero files scanned (fail-closed)
    if total_files == 0:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "ERROR",
                        "findings": [],
                        "message": "No .py files found in tools/",
                    }
                )
            )
        else:
            print("ERROR: No .py files found in tools/", file=sys.stderr)
        return 2

    # Format and output results
    if args.json:
        output = {
            "status": "PASS" if not all_findings else "FAIL",
            "findings": [f.to_dict() for f in all_findings],
            "summary": f"{len(all_findings)} violation(s) in {total_files} file(s)",
        }
        print(json.dumps(output, indent=2))
    else:
        print(format_findings_text(all_findings))

    return 1 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
