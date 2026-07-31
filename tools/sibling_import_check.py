#!/usr/bin/env python3
"""
tools.sibling_import_check -- Guardrail: detect bare sibling imports in tools/.

Detects bare sibling imports (e.g., `from lint_core import ...`) in tools/
modules that lack a guard. The guard ensures imports work when tools are
loaded by file path (as in the import-gate tests) without tools/ on sys.path.

A sibling import is GUARDED if:
1. It's inside a try/except block (graceful fallback)
2. Module-level sys.path.insert(...) precedes all imports
3. It uses 'from tools.X' (already path-safe)

Exit codes:
  0 = clean (no bare unguarded sibling imports found)
  1 = violations found (unguarded sibling imports)
  2 = usage/scan error (zero files or directory not found)

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


class SiblingImportAnalyzer(ast.NodeVisitor):
    """AST visitor to detect bare sibling imports and their guards."""

    def __init__(self, sibling_modules: set):
        self.sibling_modules = sibling_modules
        self.unguarded_imports = []
        self.in_try = False
        self.has_module_level_sys_path = False

    def visit_Module(self, node: ast.Module):
        """Check for module-level sys.path.insert statements."""
        # First pass: check if module has any sys.path.insert at top level
        for stmt in node.body:
            if self._is_sys_path_insert(stmt):
                self.has_module_level_sys_path = True
                break

        # Second pass: walk the tree for imports
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try):
        """Visit try block - imports inside are considered guarded."""
        old_in_try = self.in_try
        self.in_try = True
        self.generic_visit(node)
        self.in_try = old_in_try

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Detect 'from X import' statements."""
        if node.module and node.module in self.sibling_modules:
            # It's a sibling module
            if not node.module.startswith("tools."):
                # Bare sibling import (not using tools.X form)
                if not self.in_try and not self.has_module_level_sys_path:
                    self.unguarded_imports.append(
                        Finding(
                            "",  # Will be set by caller
                            node.module,
                            node.lineno,
                            f"from {node.module} import ...",
                        )
                    )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        """Detect 'import X' statements."""
        for alias in node.names:
            name = alias.name.split(".")[0]  # Get base module name
            if name in self.sibling_modules:
                # Bare sibling import
                if not self.in_try and not self.has_module_level_sys_path:
                    self.unguarded_imports.append(
                        Finding(
                            "",  # Will be set by caller
                            name,
                            node.lineno,
                            f"import {alias.name}",
                        )
                    )
        self.generic_visit(node)

    @staticmethod
    def _is_sys_path_insert(node):
        """Check if a statement is a sys.path.insert(...) call."""
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute):
                if (isinstance(call.func.value, ast.Attribute) and
                    isinstance(call.func.value.value, ast.Name)):
                    # Check for: sys.path.insert
                    if (call.func.value.value.id == "sys" and
                        call.func.value.attr == "path" and
                        call.func.attr == "insert"):
                        return True
        return False


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
            modules.add(py_file.stem)
    return modules


def extract_sibling_imports(file_path: str, sibling_modules: set) -> List[Finding]:
    """Extract unguarded sibling imports using AST analysis."""
    findings = []

    try:
        with open(file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_path)
    except (SyntaxError, OSError, UnicodeDecodeError):
        return findings

    analyzer = SiblingImportAnalyzer(sibling_modules)
    analyzer.visit(tree)

    # Set file path on findings
    for finding in analyzer.unguarded_imports:
        finding.file_path = file_path

    return analyzer.unguarded_imports


def scan_tools_directory(root: str) -> Tuple[List[Finding], int]:
    """Scan tools/ directory for bare unguarded sibling imports.

    Returns:
        (list of findings, number of files scanned)
    """
    files = find_python_files(root)
    sibling_modules = get_sibling_modules(root)

    if not files:
        # No files to scan
        return [], 0

    all_findings = []
    for file_path in files:
        findings = extract_sibling_imports(file_path, sibling_modules)
        all_findings.extend(findings)

    return all_findings, len(files)


def format_findings_text(findings: List[Finding]) -> str:
    """Format findings as human-readable text."""
    if not findings:
        return "Sibling import check: CLEAN"

    lines = [f"Sibling import check: {len(findings)} violation(s) found"]
    for finding in findings:
        lines.append(
            f"  {finding.file_path}:{finding.line_number}: {finding.import_form}"
        )
    return "\n".join(lines)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Detect bare sibling imports in tools/ without guards",
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
        root_path = os.path.abspath(root_path)

        # Handle both directory and file inputs
        if os.path.isfile(root_path):
            # If it's a file, get its directory
            root_path = os.path.dirname(root_path)

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
