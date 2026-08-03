#!/usr/bin/env python3
"""
tools.dead_code_check -- AST-based Python dead code detector.
INDEX: AST-based dead code detector (unused functions/classes/imports)

Finds unused functions, classes, and module-level variables across the
codebase by collecting all definitions (Phase 1), then all references
(Phase 2), and reporting definitions with zero references (Phase 3).

Ignores:
  - __dunder__ methods/attributes
  - Test files (tests/ directory)
  - Files in .git/
  - __init__.py re-exports
  - Lines with ``# dead-code-ok`` suppression comment

Exit codes:
  0 = clean (no dead code found)
  1 = dead code found (--check mode)
  2 = usage/scan error

Usage:
  python tools/dead_code_check.py [--check] [--json] [--paths DIR...] [--root DIR]
"""
import argparse
import ast
import json
import os
import sys
from pathlib import Path

# Ensure this tool's own directory (tools/) is importable so the shared
# linting core resolves regardless of cwd or how the file is loaded
# (the import-gate loads tools by path, without tools/ on sys.path).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lint_core import ASTCache, Finding, exit_code, normalize_path


def find_python_files(root, scan_dirs=None):
    """Find all .py files under scan_dirs (or root), excluding tests/ and .git/."""
    root = os.path.abspath(root)
    files = []

    if scan_dirs:
        dirs_to_scan = [os.path.join(root, d) for d in scan_dirs]
    else:
        dirs_to_scan = [root]

    for base in dirs_to_scan:
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            # Skip .git and tests directories
            rel = os.path.relpath(dirpath, root).replace("\\", "/")
            if rel.startswith(".git") or rel.startswith("tests"):
                dirnames.clear()
                continue
            # Prune hidden and test dirs from traversal
            dirnames[:] = [
                d for d in dirnames
                if d != ".git" and d != "tests" and d != "__pycache__"
                and d != "node_modules"
            ]
            for fn in filenames:
                if fn.endswith(".py"):
                    files.append(os.path.join(dirpath, fn))

    return sorted(files)


def read_file_lines(filepath):
    """Read file lines, returning empty list on error."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.readlines()
    except (UnicodeDecodeError, OSError):
        return []


def _get_ast_cache():
    """Get or create the shared AST cache."""
    if not hasattr(_get_ast_cache, '_cache'):
        _get_ast_cache._cache = ASTCache()
    return _get_ast_cache._cache


def has_suppression(lines, lineno):
    """Check if a line has the # dead-code-ok suppression comment."""
    if lineno < 1 or lineno > len(lines):
        return False
    return "# dead-code-ok" in lines[lineno - 1]


def is_dunder(name):
    """Return True for __dunder__ names."""
    return name.startswith("__") and name.endswith("__")


def collect_definitions(filepath, root, lines):
    """Collect function, class, and top-level variable definitions from a file.

    Returns list of dicts: {name, type, file, line, rel_file}
    """
    try:
        source = "".join(lines)
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    rel_file = os.path.relpath(filepath, root).replace("\\", "/")
    defs = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            name = node.name
            if is_dunder(name) or has_suppression(lines, node.lineno):
                continue
            defs.append({
                "name": name,
                "type": "function",
                "file": filepath,
                "line": node.lineno,
                "rel_file": rel_file,
            })
        elif isinstance(node, ast.ClassDef):
            name = node.name
            if is_dunder(name) or has_suppression(lines, node.lineno):
                continue
            defs.append({
                "name": name,
                "type": "class",
                "file": filepath,
                "line": node.lineno,
                "rel_file": rel_file,
            })
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    if is_dunder(name) or has_suppression(lines, node.lineno):
                        continue
                    defs.append({
                        "name": name,
                        "type": "variable",
                        "file": filepath,
                        "line": node.lineno,
                        "rel_file": rel_file,
                    })

    return defs


def collect_references(filepath, lines):
    """Collect all name references from a file.

    Returns a set of referenced names.
    """
    try:
        source = "".join(lines)
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return set()

    refs = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            refs.add(node.id)
        elif isinstance(node, ast.Attribute):
            refs.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            if node.names:
                for alias in node.names:
                    if alias.name != "*":
                        refs.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # Record the module name (last component)
                parts = alias.name.split(".")
                refs.add(parts[-1])
                if alias.asname:
                    refs.add(alias.asname)

    return refs


def collect_init_reexports(filepath):
    """Collect names re-exported from __init__.py files."""
    if not filepath.endswith("__init__.py"):
        return set()
    lines = read_file_lines(filepath)
    if not lines:
        return set()
    try:
        source = "".join(lines)
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return set()

    exports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.names:
                for alias in node.names:
                    if alias.name != "*":
                        exports.add(alias.name)
    return exports


def scan(root, scan_dirs=None):
    """Run the dead code scan. Returns list of Finding objects."""
    root = os.path.abspath(root)
    py_files = find_python_files(root, scan_dirs)

    # Phase 1: Collect definitions
    all_defs = []
    file_lines_cache = {}
    for fp in py_files:
        lines = read_file_lines(fp)
        file_lines_cache[fp] = lines
        defs = collect_definitions(fp, root, lines)
        all_defs.extend(defs)

    # Phase 2: Collect references from ALL files (including tests, __init__)
    all_refs = set()
    init_exports = set()

    # Scan all Python files under root for references (including tests)
    all_ref_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel.startswith(".git") or rel == "__pycache__":
            dirnames.clear()
            continue
        dirnames[:] = [
            d for d in dirnames
            if d != ".git" and d != "__pycache__" and d != "node_modules"
        ]
        for fn in filenames:
            if fn.endswith(".py"):
                all_ref_files.append(os.path.join(dirpath, fn))

    for fp in all_ref_files:
        if fp in file_lines_cache:
            lines = file_lines_cache[fp]
        else:
            lines = read_file_lines(fp)
        refs = collect_references(fp, lines)
        all_refs.update(refs)
        # Collect __init__.py re-exports
        exports = collect_init_reexports(fp)
        init_exports.update(exports)

    # Phase 3: Report definitions with zero references
    findings = []
    for d in all_defs:
        name = d["name"]
        # Skip if name is referenced anywhere
        if name in all_refs:
            continue
        # Skip if re-exported via __init__.py
        if name in init_exports:
            continue
        # Convert to Finding object
        finding = Finding(
            file=d['rel_file'],
            line=d['line'],
            type=d['type'],
            message=f"{d['type']} {d['name']}"
        )
        findings.append(finding)

    return findings


def main():
    parser = argparse.ArgumentParser(
        description="AST-based Python dead code detector"
    )
    parser.add_argument(
        "--check", action="store_true", default=True,
        help="Run the scan (default action)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output findings as JSON"
    )
    parser.add_argument(
        "--paths", nargs="+", default=None,
        help="Directories to scan for definitions (default: repo root)"
    )
    parser.add_argument(
        "--root", default=None,
        help="Repository root directory (default: auto-detect)"
    )

    args = parser.parse_args()

    # Determine root
    if args.root:
        root = os.path.abspath(args.root)
    else:
        # Auto-detect: walk up from this script to find .git
        here = os.path.dirname(os.path.abspath(__file__))
        root = here
        while root != os.path.dirname(root):
            if os.path.isdir(os.path.join(root, ".git")):
                break
            root = os.path.dirname(root)
        else:
            root = os.getcwd()

    if not os.path.isdir(root):
        print(f"ERROR: root directory not found: {root}", file=sys.stderr)
        return 2

    try:
        findings = scan(root, args.paths)
    except Exception as e:
        print(f"ERROR: scan failed: {e}", file=sys.stderr)
        return 2

    if args.json:
        output = []
        for f in findings:
            # Extract type and name from message
            parts = f.message.split(' ', 1)
            output.append({
                "type": parts[0] if parts else f.type,
                "name": parts[1] if len(parts) > 1 else "",
                "file": f.file,
                "line": f.line,
            })
        print(json.dumps(output, indent=2))
    else:
        if findings:
            print(f"Dead code: {len(findings)} unused definition(s) found\n")
            for f in findings:
                print(f"  {f.file}:{f.line}  {f.message}")
            print()
        else:
            print("No dead code found.")

    return exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
