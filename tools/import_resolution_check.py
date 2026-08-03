#!/usr/bin/env python3
"""
Guardrail G5: Python import resolution validator.
INDEX: Guardrail G5: Python import resolution validator (parses staged .py files via AST, resolves imports against repo structure + stdlib, fail-closed on unresolvable modules); catches isolation escapes where agent writes to primary tree with unresolvable imports; CLI: no args (exit 0=all resolvable/1=unresolvable); logs audit trail to state/IMPORT-AUDIT.log; integrated into pre-push-policy.sh after secret_scan

Parses all staged .py files via AST, extracts import/from-import statements,
resolves each module against repo structure and sys.stdlib_module_names.
Fail-closed (exit 1) if any import cannot resolve.

Logs findings to audit trail at $AESOP_ROOT/state/IMPORT-AUDIT.log.

Root cause: Agent wrote file with unresolvable import ("from state_store.materialize import ...")
to primary tree, bypassing any isolation/import-resolution gate during worktree writes.
This guardrail validates that all staged .py imports are resolvable before push.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def get_staged_py_files():
    """
    Get list of staged .py files from git diff --cached --name-only.
    Returns: list of file paths (relative to repo root).
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=True,
            timeout=10,
        )
        all_files = result.stdout.strip().split("\n")
        py_files = [f for f in all_files if f.endswith(".py") and f.strip()]
        return py_files
    except subprocess.CalledProcessError as e:
        print(f"ERROR: git diff failed: {e.stderr}", file=sys.stderr)
        return []
    except subprocess.TimeoutExpired:
        print("ERROR: git diff timed out", file=sys.stderr)
        return []


def get_staged_file_content(filepath):
    """Get staged content of a file from git index."""
    try:
        result = subprocess.run(
            ["git", "show", f":{filepath}"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=True,
            timeout=10,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"ERROR: could not read staged content of {filepath}: {e.stderr}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print(f"ERROR: git show timed out for {filepath}", file=sys.stderr)
        return None


def extract_imports(filepath, content):
    """
    Extract all import statements from Python source via AST.
    Returns: list of (module_name, import_type, lineno).
    import_type: 'import' or 'from'.
    """
    imports = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        print(f"WARNING: {filepath}: syntax error at line {e.lineno}: {e.msg}", file=sys.stderr)
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, "import", node.lineno))
        elif isinstance(node, ast.ImportFrom):
            # ImportFrom.module can be None for relative imports (from . import x)
            if node.module:
                imports.append((node.module, "from", node.lineno))

    return imports


def build_repo_module_map(repo_root):
    """
    Build a map of available Python modules in the repo.
    Returns: set of fully-qualified module names (dot-separated paths).
    """
    modules = set()
    repo_path = Path(repo_root)

    def scan_package(pkg_path, pkg_name=""):
        """Recursively scan package structure."""
        for item in pkg_path.iterdir():
            if item.name.startswith("."):
                continue

            if item.is_dir():
                if (item / "__init__.py").exists():
                    # It's a sub-package
                    subpkg_name = f"{pkg_name}.{item.name}" if pkg_name else item.name
                    modules.add(subpkg_name)
                    scan_package(item, subpkg_name)
            elif item.is_file() and item.suffix == ".py" and item.name != "__init__.py":
                # It's a module file
                module_name = f"{pkg_name}.{item.stem}" if pkg_name else item.stem
                modules.add(module_name)

    # Scan top-level
    for item in repo_path.iterdir():
        if item.name.startswith("."):
            continue

        if item.is_dir() and (item / "__init__.py").exists():
            # It's a top-level package
            modules.add(item.name)
            scan_package(item, item.name)
        elif item.is_file() and item.suffix == ".py" and item.name != "__init__.py":
            # It's a top-level module
            modules.add(item.stem)

    return modules


def get_stdlib_modules():
    """Get set of stdlib module names."""
    try:
        # Python 3.10+ has sys.stdlib_module_names
        if hasattr(sys, "stdlib_module_names"):
            return sys.stdlib_module_names
        else:
            # Fallback: common stdlib modules for older Python
            return {
                "os", "sys", "json", "ast", "subprocess", "tempfile", "pathlib",
                "typing", "collections", "itertools", "functools", "re", "datetime",
                "unittest", "pytest", "hashlib", "base64", "io", "argparse", "shutil",
                "time", "random", "string", "struct", "stat", "signal", "errno",
                "socket", "select", "threading", "multiprocessing", "queue", "copy",
                "pickle", "shelve", "dbm", "sqlite3", "csv", "configparser", "logging",
            }
    except Exception:
        return set()


def resolve_module(module_name, repo_modules, stdlib_modules, repo_root=None):
    """
    Check if a module is resolvable.
    Returns: (is_resolvable, resolution_type) where resolution_type is 'repo', 'stdlib', or 'unknown'.

    For repo modules, checks full path by traversing directory structure.
    For stdlib, checks only top-level (stdlib doesn't have full paths pre-built).
    """
    top_level = module_name.split(".")[0]

    # Check stdlib first (only top-level)
    if top_level in stdlib_modules:
        return True, "stdlib"

    # Check repo modules: exact match first
    if module_name in repo_modules:
        return True, "repo"

    # For submodules (e.g., state_store.materialize), verify actual file existence
    if repo_root and "." in module_name:
        parts = module_name.split(".")
        # Try to resolve as a module file (last part) in the parent package
        module_path = Path(repo_root) / Path(*parts).with_suffix(".py")
        if module_path.exists():
            return True, "repo"

        # Try to resolve as a package (nested __init__.py)
        package_path = Path(repo_root) / Path(*parts) / "__init__.py"
        if package_path.exists():
            return True, "repo"

    # Unknown module: not resolvable
    return False, "unknown"


def check_imports(repo_root):
    """
    Main check: validate all staged .py file imports.
    Returns: (is_valid, findings) where findings is list of dicts.
    """
    findings = []
    is_valid = True

    staged_files = get_staged_py_files()
    if not staged_files:
        print("No staged Python files found.", file=sys.stderr)
        return is_valid, findings

    repo_modules = build_repo_module_map(repo_root)
    stdlib_modules = get_stdlib_modules()

    for filepath in staged_files:
        content = get_staged_file_content(filepath)
        if content is None:
            findings.append({
                "file": filepath,
                "status": "error",
                "message": "Could not read staged content",
            })
            is_valid = False
            continue

        imports = extract_imports(filepath, content)
        for module_name, import_type, lineno in imports:
            resolvable, resolution_type = resolve_module(
                module_name, repo_modules, stdlib_modules, repo_root
            )

            if not resolvable:
                is_valid = False
                findings.append({
                    "file": filepath,
                    "import": module_name,
                    "import_type": import_type,
                    "lineno": lineno,
                    "status": "unresolvable",
                    "message": f"Module '{module_name}' not found in repo or stdlib",
                })

    return is_valid, findings


def log_audit_event(repo_root, is_valid, findings):
    """Log findings to audit trail."""
    state_dir = Path(repo_root) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    audit_log = state_dir / "IMPORT-AUDIT.log"

    # Prepare audit record
    event_type = "import_check_pass" if is_valid else "import_check_fail"
    record = {
        "event": event_type,
        "is_valid": is_valid,
        "finding_count": len(findings),
        "findings": findings,
    }

    try:
        with open(audit_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        print(f"WARNING: could not write audit log: {e}", file=sys.stderr)


def main():
    """
    Main entry point. Exit 0 if all imports valid, 1 if any unresolvable.
    """
    # Determine repo root
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=True,
            timeout=5,
        )
        repo_root = result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("ERROR: not in a git repository", file=sys.stderr)
        return 2

    is_valid, findings = check_imports(repo_root)
    log_audit_event(repo_root, is_valid, findings)

    if not is_valid:
        print("ERROR: Python import resolution check failed", file=sys.stderr)
        for finding in findings:
            if finding["status"] == "unresolvable":
                print(
                    f"  {finding['file']}:{finding['lineno']}: "
                    f"{finding['import_type']} {finding['import']} — {finding['message']}",
                    file=sys.stderr,
                )
            else:
                print(f"  {finding['file']}: {finding['message']}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
