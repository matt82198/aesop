#!/usr/bin/env python3
"""
Guardrail G5: Python import resolution validator.
INDEX: Guardrail G5: Python import resolution validator (parses staged .py files via AST, resolves imports against repo structure + stdlib, fail-closed on unresolvable modules); catches isolation escapes where agent writes to primary tree with unresolvable imports; also understands the sanctioned repo idiom -- a `sys.path.insert/append` whose target is a LITERAL `__file__`-derived in-repo directory (`Path(__file__).parent` / `.parents[N]` / `os.path.dirname` / `os.path.join`, module-level name bindings, `for _p in (DIR_A, DIR_B)` loops) makes imports resolvable against that directory, while dynamic/env-var/out-of-repo targets and modules absent from the inserted directory stay flagged (a resolution rule, never a path or directory exemption); CLI: no args (exit 0=all resolvable/1=unresolvable); logs audit trail to state/IMPORT-AUDIT.log; integrated into pre-push-policy.sh after secret_scan

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


class _Unresolvable(Exception):
    """Raised when an AST node is not a literal, __file__-derived path."""


def _dotted_name(node):
    """Render an ast.Attribute/ast.Name chain as a dotted string, or None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _eval_path_node(node, env, file_path, depth=0):
    """
    Conservatively evaluate an AST node to (Path, uses_file).

    Only literal path algebra rooted in ``__file__`` is understood:
    ``__file__``, ``Path(...)``, ``str(...)``, ``.parent``, ``.resolve()``,
    ``p / "literal"``, ``os.path.dirname/join/abspath/realpath/normpath``,
    and names already bound to such an expression.

    Anything dynamic (function calls, os.environ lookups, f-strings,
    subscripts) raises _Unresolvable so those sys.path targets keep failing.
    """
    if depth > 24:
        raise _Unresolvable()

    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return Path(node.value), False
        raise _Unresolvable()

    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return file_path, True
        bound = env.get(node.id)
        if bound is None:
            raise _Unresolvable()
        return bound

    if isinstance(node, ast.Attribute):
        if node.attr == "parent":
            base, uses_file = _eval_path_node(node.value, env, file_path, depth + 1)
            return base.parent, uses_file
        raise _Unresolvable()

    # Path(...).parents[N] -- N literal, applied as N successive .parent steps.
    if isinstance(node, ast.Subscript):
        value = node.value
        index = node.slice
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "parents"
            and isinstance(index, ast.Constant)
            and isinstance(index.value, int)
            and not isinstance(index.value, bool)
            and 0 <= index.value <= 32
        ):
            base, uses_file = _eval_path_node(value.value, env, file_path, depth + 1)
            for _ in range(index.value + 1):
                base = base.parent
            return base, uses_file
        raise _Unresolvable()

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, left_file = _eval_path_node(node.left, env, file_path, depth + 1)
        right, right_file = _eval_path_node(node.right, env, file_path, depth + 1)
        return left / right, left_file or right_file

    if isinstance(node, ast.Call):
        return _eval_path_call(node, env, file_path, depth)

    raise _Unresolvable()


def _eval_path_call(node, env, file_path, depth):
    """Evaluate the small set of path-producing calls the sanctioned idiom uses."""
    if node.keywords:
        raise _Unresolvable()

    func = node.func

    # Method form: <expr>.resolve() / .absolute() / .joinpath(...)
    if isinstance(func, ast.Attribute) and func.attr in ("resolve", "absolute", "joinpath"):
        base, uses_file = _eval_path_node(func.value, env, file_path, depth + 1)
        if func.attr in ("resolve", "absolute"):
            if node.args:
                raise _Unresolvable()
            return base, uses_file
        for arg in node.args:
            part, part_file = _eval_path_node(arg, env, file_path, depth + 1)
            base = base / part
            uses_file = uses_file or part_file
        return base, uses_file

    # __import__("pathlib").Path(...) -- inline-import spelling of Path().
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "Path"
        and isinstance(func.value, ast.Call)
        and isinstance(func.value.func, ast.Name)
        and func.value.func.id == "__import__"
        and len(func.value.args) == 1
        and isinstance(func.value.args[0], ast.Constant)
        and func.value.args[0].value == "pathlib"
        and len(node.args) == 1
    ):
        return _eval_path_node(node.args[0], env, file_path, depth + 1)

    name = _dotted_name(func)
    if name is None:
        raise _Unresolvable()

    if name in ("str", "Path", "pathlib.Path", "PurePath", "os.fspath"):
        if len(node.args) != 1:
            raise _Unresolvable()
        return _eval_path_node(node.args[0], env, file_path, depth + 1)

    if name in ("os.path.abspath", "os.path.realpath", "os.path.normpath"):
        if len(node.args) != 1:
            raise _Unresolvable()
        return _eval_path_node(node.args[0], env, file_path, depth + 1)

    if name == "os.path.dirname":
        if len(node.args) != 1:
            raise _Unresolvable()
        base, uses_file = _eval_path_node(node.args[0], env, file_path, depth + 1)
        return base.parent, uses_file

    if name == "os.path.join":
        if not node.args:
            raise _Unresolvable()
        base, uses_file = _eval_path_node(node.args[0], env, file_path, depth + 1)
        for arg in node.args[1:]:
            part, part_file = _eval_path_node(arg, env, file_path, depth + 1)
            base = base / part
            uses_file = uses_file or part_file
        return base, uses_file

    raise _Unresolvable()


def _build_path_env(tree, file_path):
    """
    Bind module names to literal __file__-derived paths, in source order.

    A name assigned more than once with differing values is poisoned to None
    (ambiguous) so a later dynamic rebind cannot smuggle a path through.
    """
    env = {}
    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments.append((node.lineno, target.id, node.value))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                assignments.append((node.lineno, node.target.id, node.value))

    for _lineno, name, value in sorted(assignments, key=lambda item: item[0]):
        try:
            resolved = _eval_path_node(value, env, file_path)
        except _Unresolvable:
            resolved = None
        if name in env and env[name] != resolved:
            env[name] = None
        else:
            env[name] = resolved
    return env


def _syspath_mutation_arg(node):
    """Return the path argument of a sys.path.insert/append call, else None."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if _dotted_name(func) not in ("sys.path.insert", "sys.path.append"):
        return None
    if func.attr == "insert":
        return node.args[1] if len(node.args) >= 2 else None
    return node.args[0] if node.args else None


def _iter_syspath_targets(tree, env, file_path):
    """
    Yield (arg_node, env) for every sys.path.insert/append in the module.

    `for _p in (DIR_A, DIR_B): sys.path.insert(0, str(_p))` is expanded once
    per element of the literal tuple/list, with the loop name bound. Loops over
    anything but a literal sequence are left unbound, so their targets stay
    unresolvable.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _syspath_mutation_arg(node)
            if target is not None:
                yield target, env
            continue
        if not isinstance(node, ast.For):
            continue
        if not isinstance(node.target, ast.Name) or not isinstance(node.iter, (ast.Tuple, ast.List)):
            continue
        for element in node.iter.elts:
            try:
                bound = _eval_path_node(element, env, file_path)
            except _Unresolvable:
                continue
            loop_env = dict(env)
            loop_env[node.target.id] = bound
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    target = _syspath_mutation_arg(inner)
                    if target is not None:
                        yield target, loop_env


def sys_path_dirs_for_file(repo_root, filepath, content):
    """
    Directories this file adds to sys.path via the sanctioned repo idiom.

    Returns the absolute, in-repo, on-disk directories named by
    ``sys.path.insert/append`` calls whose target is a literal path derived
    from ``__file__``. Dynamic targets, non-__file__ literals and paths that
    escape the repo are deliberately omitted -- imports behind those stay
    unresolvable, so this is a resolution rule, not an exemption.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    repo_path = Path(repo_root).resolve()
    file_path = Path(os.path.normpath(str(repo_path / filepath)))
    env = _build_path_env(tree, file_path)

    dirs = []
    for target, target_env in _iter_syspath_targets(tree, env, file_path):
        try:
            value, uses_file = _eval_path_node(target, target_env, file_path)
        except _Unresolvable:
            continue
        if not uses_file:
            continue
        resolved = Path(os.path.normpath(str(value)))
        if not resolved.is_absolute():
            continue
        try:
            resolved.relative_to(repo_path)
        except ValueError:
            continue
        if resolved.is_dir() and resolved not in dirs:
            dirs.append(resolved)
    return dirs


def _module_exists_under(directory, module_name):
    """Is module_name importable from `directory` as a real file on disk?"""
    parts = module_name.split(".")
    if any(not part.isidentifier() for part in parts):
        return False
    candidate = Path(directory) / Path(*parts)
    if candidate.with_suffix(".py").is_file():
        return True
    if (candidate / "__init__.py").is_file():
        return True
    # PEP 420 namespace package: a real directory that holds Python modules.
    if candidate.is_dir() and any(candidate.glob("*.py")):
        return True
    return False


def resolve_module(module_name, repo_modules, stdlib_modules, repo_root=None, extra_dirs=()):
    """
    Check if a module is resolvable.
    Returns: (is_resolvable, resolution_type) where resolution_type is
    'repo', 'stdlib', 'syspath', or 'unknown'.

    For repo modules, checks full path by traversing directory structure.
    For stdlib, checks only top-level (stdlib doesn't have full paths pre-built).
    `extra_dirs` are directories the file itself placed on sys.path via the
    sanctioned __file__-derived idiom; each import is also checked against them.
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

    # Directories the file itself put on sys.path via the sanctioned idiom.
    for extra in extra_dirs:
        if _module_exists_under(extra, module_name):
            return True, "syspath"

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
        extra_dirs = sys_path_dirs_for_file(repo_root, filepath, content)
        for module_name, import_type, lineno in imports:
            resolvable, resolution_type = resolve_module(
                module_name, repo_modules, stdlib_modules, repo_root, extra_dirs=extra_dirs
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
