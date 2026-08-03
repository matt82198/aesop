#!/usr/bin/env python3
"""
Guardrail G5: Python import resolution validator.
INDEX: Guardrail G5: Python import resolution validator (AST-parses .py files, resolves imports against repo structure + stdlib + environment, fail-closed on unresolvable modules); CLI: `--range A..B` (files ACTUALLY being pushed, blob read at range tip -- what pre-push-policy.sh uses) | `--staged` (index; pre-commit) | `--files P...` (worktree) | `--repo PATH`; exit 0=all resolvable/1=unresolvable/2=usage-or-git-error (fail-closed, never "clean"); logs audit trail to state/IMPORT-AUDIT.log

Parses .py files via AST, extracts import/from-import statements, resolves each
module against repo structure and sys.stdlib_module_names. Fail-closed (exit 1)
if any import cannot resolve.

WHY --range EXISTS (vacuity finding, guard/g5-import-check-actually-runs):
this tool only had the index mode and pre-push-policy.sh invoked it with no
arguments. A pre-push hook runs AFTER the commit, so `git diff --cached` is
EMPTY: the gate printed "No staged Python files found" and exited 0 on EVERY
normal push. A fail-closed gate that never evaluates anything is vacuously
green. The hook now feeds it the pushed ref range parsed from git pre-push
stdin, reusing the same get_commit_range() helper check_secret_scan() uses.

Fail-closed contract (mirrors tools/secret_scan.py): a git failure -- an
unresolvable range, a git crash, an unreadable blob -- is NOT the same as
"zero files changed". It raises GitError and exits 2 rather than reporting clean.

Logs findings to audit trail at <repo>/state/IMPORT-AUDIT.log.

Root cause: Agent wrote file with unresolvable import ("from state_store.materialize import ...")
to primary tree, bypassing any isolation/import-resolution gate during worktree writes.
This guardrail validates that all pushed .py imports are resolvable before push.
"""

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


class GitError(Exception):
    """A git invocation failed. Callers MUST fail closed, never treat this as
    'nothing to check' -- that is precisely the vacuous-green failure mode."""


def _git(args, cwd=None, timeout=15, binary=False):
    """Run a git command, raising GitError on ANY failure."""
    kwargs = {"capture_output": True, "timeout": timeout, "cwd": cwd}
    if not binary:
        kwargs["text"] = True
        kwargs["encoding"] = "utf-8"
    try:
        result = subprocess.run(["git"] + list(args), **kwargs)
    except Exception as e:
        raise GitError("git %s raised %r" % (" ".join(args), e))
    if result.returncode != 0:
        stderr = result.stderr
        if binary:
            stderr = stderr.decode("utf-8", errors="ignore")
        raise GitError(
            "git %s failed (rc=%d): %s"
            % (" ".join(args), result.returncode, (stderr or "").strip())
        )
    return result.stdout


def _range_tip_ref(commit_range):
    """Extract the right-hand (tip) ref from a two-dot or three-dot range.
    Mirrors tools/secret_scan.py::_range_tip_ref."""
    if "..." in commit_range:
        tip = commit_range.split("...", 1)[1]
    elif ".." in commit_range:
        tip = commit_range.split("..", 1)[1]
    else:
        tip = commit_range
    return tip or "HEAD"


def get_staged_py_files(repo_root=None):
    """
    Get list of staged .py files (deletions excluded).
    Raises GitError if git fails -- fail-closed, NOT "nothing staged".
    """
    out = _git(["diff", "--cached", "--name-only", "--diff-filter=d"],
               cwd=repo_root, timeout=10)
    return [f for f in out.strip().split("\n") if f.strip() and f.endswith(".py")]


def get_range_py_files(commit_range, repo_root=None):
    """
    Get list of .py files changed in a commit range (deletions excluded).
    Raises GitError if git fails (e.g. unresolvable ref) -- fail-closed.
    """
    out = _git(["diff", "--name-only", "--diff-filter=d", commit_range],
               cwd=repo_root, timeout=30)
    return [f for f in out.strip().split("\n") if f.strip() and f.endswith(".py")]


def get_git_blob_text(ref_path, repo_root=None):
    """Read a blob via `git show <ref>:<path>` (or ':<path>' for the index).
    Raises GitError on failure -- callers enumerate with --diff-filter=d, so a
    failure here is a real error, not a legitimately-absent file."""
    raw = _git(["show", ref_path], cwd=repo_root, timeout=15, binary=True)
    return raw.decode("utf-8", errors="replace")


def get_staged_file_content(filepath, repo_root=None):
    """Get staged content of a file from git index."""
    return get_git_blob_text(":%s" % filepath, repo_root=repo_root)


def get_range_file_content(tip_ref, filepath, repo_root=None):
    """Content of a file as it exists at the TIP of the pushed range -- the
    bytes actually being pushed, NOT the (possibly dirty) working tree."""
    return get_git_blob_text("%s:%s" % (tip_ref, filepath), repo_root=repo_root)


def get_worktree_file_content(repo_root, filepath):
    """Read a file from the working tree (manual --files mode)."""
    return (Path(repo_root) / filepath).read_text(encoding="utf-8", errors="replace")


def get_tree_paths(tip_ref, repo_root=None):
    """All tracked paths at a ref, for building the index of the pushed tree
    (rather than of whatever the working tree happens to contain)."""
    out = _git(["ls-tree", "-r", "--name-only", tip_ref], cwd=repo_root, timeout=30)
    return [f for f in out.strip().split("\n") if f.strip()]


def extract_imports(filepath, content):
    """
    Extract all import statements from Python source via AST.
    Returns: list of (module_name, import_type, lineno, level).

    `level` is ast.ImportFrom.level: 0 for absolute, >=1 for explicit relative
    imports. It MUST be carried: `from .api import X` has module == "api" but is
    NOT a request for a top-level module named "api". Dropping the level made
    every relative import in the repo look unresolvable.
    """
    imports = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        print(f"WARNING: {filepath}: syntax error at line {e.lineno}: {e.msg}",
              file=sys.stderr)
        return imports

    optional = _importerror_guarded_lines(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if node.lineno in optional:
                continue
            for alias in node.names:
                imports.append((alias.name, "import", node.lineno, 0))
        elif isinstance(node, ast.ImportFrom):
            if node.lineno in optional:
                continue
            level = node.level or 0
            if node.module:
                imports.append((node.module, "from", node.lineno, level))
            # `from . import x` (module is None) is intentionally NOT checked:
            # `x` may be a submodule OR an attribute defined in the package's
            # __init__.py, and only a runtime import can tell them apart.
            # ui/api/tracker.py's `from . import validate_mutation` binds a
            # FUNCTION defined in ui/api/__init__.py -- treating the name as a
            # required submodule reports working code as broken.

    return imports


def _importerror_guarded_lines(tree):
    """Line numbers of imports written inside `try: ... except ImportError:`.

    These are OPTIONAL BY CONSTRUCTION -- the author wrote a handler for the
    import failing (soft third-party deps, e.g. `try: import yaml except
    ImportError: yaml = None`). Blocking a push on them would report the code's
    intended design as a defect. This is a property of the source, not an
    allowlist: nothing is named, nothing is exempted by path, and an unguarded
    import of the same module is still reported.
    """
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches_import_error = False
        for handler in node.handlers:
            names = []
            if isinstance(handler.type, ast.Name):
                names = [handler.type.id]
            elif isinstance(handler.type, ast.Tuple):
                names = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
            elif handler.type is None:
                names = ["Exception"]
            if any(n in ("ImportError", "ModuleNotFoundError", "Exception")
                   for n in names):
                catches_import_error = True
        if not catches_import_error:
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    guarded.add(sub.lineno)
    return guarded


class PathIndex:
    """Index of the paths that make up the tree being checked.

    Models Python 3 import resolution far more faithfully than a bare
    "dotted name -> file" map:
      * PEP 420 implicit namespace packages -- a directory is importable
        WITHOUT an __init__.py (the old map required one, which is Python 2
        semantics and made e.g. `from tools import cli` look unresolvable).
      * sys.path[0] semantics -- a script run as `python tools/foo.py` gets its
        OWN directory on sys.path, so `import common` inside tools/foo.py
        resolves to tools/common.py. Ignoring this flagged every sibling import
        in the repo.
    """

    def __init__(self, paths):
        self.files = set()
        self.dirs = set()
        for raw in paths:
            p = raw.replace("\\", "/").strip()
            if not p:
                continue
            self.files.add(p)
            parts = p.split("/")
            for i in range(1, len(parts)):
                self.dirs.add("/".join(parts[:i]))

    def has_module(self, dotted, base_dir=""):
        """True if `dotted` is importable relative to base_dir ('' = repo root)."""
        if not dotted:
            return False
        rel = "/".join(dotted.split("."))
        prefix = (base_dir + "/") if base_dir else ""
        candidate = prefix + rel
        if candidate + ".py" in self.files:
            return True
        if candidate + "/__init__.py" in self.files:
            return True
        # PEP 420 namespace package: a plain directory is importable.
        if candidate in self.dirs:
            return True
        return False

    def owns_top_level(self, top_level, bases=()):
        """True if the tree being checked itself defines this top-level name."""
        for base in ("",) + tuple(bases):
            if self.has_module(top_level, base):
                return True
        return False


_INSTALLED_CACHE = {}


def is_installed_module(top_level):
    """True if `top_level` is importable from the environment (stdlib already
    handled separately, so this covers third-party/installed distributions).

    Uses importlib.util.find_spec, which consults the meta-path finders without
    executing the module. Repo-local sys.path entries are stripped first so a
    working-tree file cannot masquerade as an installed distribution -- in
    --range mode the verdict must describe the PUSHED tree.
    """
    if top_level in _INSTALLED_CACHE:
        return _INSTALLED_CACHE[top_level]

    import importlib.util

    saved = list(sys.path)
    here = str(Path(__file__).resolve().parent)
    cwd = str(Path.cwd().resolve())
    blocked = {here, cwd, str(Path(here).parent)}
    try:
        kept = []
        for p in sys.path:
            if not p:
                continue  # '' == cwd
            try:
                rp = str(Path(p).resolve())
            except (OSError, ValueError):
                continue
            if rp in blocked:
                continue
            kept.append(p)
        sys.path = kept
        try:
            found = importlib.util.find_spec(top_level) is not None
        except (ImportError, ValueError, AttributeError, TypeError, OSError):
            found = False
    finally:
        sys.path = saved

    _INSTALLED_CACHE[top_level] = found
    return found


# ---------------------------------------------------------------------------
# Symbolic __file__-path evaluator.
#
# REUSED from PR #724 (guard/import-check-syspath-idiom), which built it to
# teach this checker the repo's sanctioned
#     sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
# idiom instead of blanket-exempting directories. Grafted here verbatim; only
# sys_path_dirs_for_file is re-expressed below against the PathIndex, so the
# verdict describes the PUSHED tree rather than the working tree (#724 has no
# --range mode and could read the working tree directly).
#
# It understands ONLY literal path algebra rooted in __file__. Anything dynamic
# raises _Unresolvable, so imports behind a computed sys.path entry keep failing
# closed. That is what makes this a resolution rule and not an exemption.
# ---------------------------------------------------------------------------

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

_LOADER_CALLS = ("spec_from_file_location", "SourceFileLoader", "load_source")


def _iter_loader_targets(tree):
    """Yield the path argument of each load-a-module-BY-FILE-PATH call.

    Loading a file by path runs that file's top-level code, so an entry-point
    shim (ui/serve.py) installs ITS OWN sys.path entries for the caller. The
    four ui/ test modules rely on exactly this; without it they read as broken
    while passing in CI. Handled by recursing with the SAME evaluator rather
    than by naming any directory.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _LOADER_CALLS:
            continue
        for arg in node.args:
            yield arg


def _dirs_for_source(content, filepath, index, repo_root="/repo"):
    """Repo-relative directories `filepath` puts on sys.path.

    Same evaluation as #724's sys_path_dirs_for_file, but membership is tested
    against the PathIndex (the tree being checked) instead of the working-tree
    filesystem, and both the resolved dirs and any by-path-loaded files come
    back as repo-relative POSIX strings.

    Returns (syspath_dirs, loaded_files).
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [], []

    repo_path = Path(repo_root)
    file_path = Path(os.path.normpath(str(repo_path / filepath)))
    env = _build_path_env(tree, file_path)

    def to_relative(value):
        resolved = Path(os.path.normpath(str(value)))
        try:
            return resolved.relative_to(repo_path).as_posix()
        except ValueError:
            return None

    dirs = []
    for target, target_env in _iter_syspath_targets(tree, env, file_path):
        try:
            value, uses_file = _eval_path_node(target, target_env, file_path)
        except _Unresolvable:
            continue
        if not uses_file:
            continue
        rel = to_relative(value)
        # "." is the repo root, which is always searched anyway.
        if rel and rel != "." and rel in index.dirs and rel not in dirs:
            dirs.append(rel)

    loaded = []
    for arg in _iter_loader_targets(tree):
        try:
            value, uses_file = _eval_path_node(arg, env, file_path)
        except _Unresolvable:
            continue
        if not uses_file:
            continue
        rel = to_relative(value)
        if rel and rel in index.files and rel not in loaded:
            loaded.append(rel)

    return dirs, loaded


def extract_syspath_roots(content, importer_path, index, read_content=None):
    """Every sys.path root in effect for `importer_path` at runtime.

    Three contributors, each a real runtime route, none an allowlist:
      1. the module itself;
      2. conftest.py in the module's directory and every ancestor -- pytest
         imports conftest before collecting the tests beside it, so a conftest
         that inserts ../repo genuinely puts it on sys.path for those tests;
      3. one level of by-path module loading -- a module loaded via
         spec_from_file_location runs and installs its own sys.path entries.

    Every candidate is still evaluated by the #724 evaluator, so a dynamically
    computed path contributes nothing and its imports stay unresolvable.
    """
    roots, loaded = _dirs_for_source(content, importer_path, index)

    if read_content is None:
        return sorted(set(roots))

    def fold(path):
        try:
            more_dirs, _ = _dirs_for_source(read_content(path), path, index)
        except (GitError, OSError):
            return
        for d in more_dirs:
            if d not in roots:
                roots.append(d)

    importer_dir = importer_path.rsplit("/", 1)[0] if "/" in importer_path else ""
    probe = importer_dir
    seen = set()
    while True:
        conftest = (probe + "/conftest.py") if probe else "conftest.py"
        if conftest not in seen and conftest in index.files:
            seen.add(conftest)
            fold(conftest)
        if not probe:
            break
        probe = probe.rsplit("/", 1)[0] if "/" in probe else ""

    for path in loaded:
        fold(path)

    return sorted(set(roots))


def build_repo_file_list(repo_root):
    """Enumerate the working tree (used by --staged / --files, where there is no
    single git ref to read a tree from)."""
    paths = []
    repo_path = Path(repo_root)
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            paths.append(Path(root, name).relative_to(repo_path).as_posix())
    return paths


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


def resolve_module(module_name, index, stdlib_modules, importer_path="", level=0,
                   syspath_roots=()):
    """
    Check whether an import is resolvable, modelling Python 3 semantics.

    Returns (is_resolvable, resolution_type) with resolution_type one of
    'relative', 'stdlib', 'repo', 'sibling', 'ancestor', 'syspath',
    'installed', or 'unknown'.

    Resolution order:
      1. Explicit relative import (level >= 1) -- resolved against the importing
         file's own package. `from .api import X` is NOT a request for a
         top-level module named "api"; the old resolver dropped the level and
         reported every such import unresolvable.
      2. stdlib (top-level name).
      3. Repo-root-relative dotted path, including PEP 420 namespace packages.
      4. Sibling of the importing file -- sys.path[0] semantics for a module run
         as a script (`python tools/foo.py` puts tools/ on sys.path).
      5. An ancestor directory of the importer -- a package whose ROOT is on
         sys.path resolves bare names next to that root.
      6. A directory the module (or its conftest) puts on sys.path.
      7. Installed distribution (importlib.util.find_spec).

    None of these are exemptions: each is a route by which the interpreter
    genuinely resolves the name at runtime. Only names that resolve by NO route
    are reported.

    CRITICAL (this is the exact escape G5 exists for): if the tree being checked
    OWNS the top-level name, the full dotted path must resolve INSIDE that tree.
    We must not fall through to the environment, or `from state_store.materialize
    import x` would be blessed by find_spec("state_store") finding the repo's own
    package while `materialize` does not exist at all.
    """
    importer_dir = importer_path.rsplit("/", 1)[0] if "/" in importer_path else ""

    ancestors = []
    walk = importer_dir
    while walk:
        ancestors.append(walk)
        walk = walk.rsplit("/", 1)[0] if "/" in walk else ""

    if level:
        base = importer_dir
        for _ in range(level - 1):
            base = base.rsplit("/", 1)[0] if "/" in base else ""
        if index.has_module(module_name, base):
            return True, "relative"
        # Only judge a relative import when its base package is part of the tree
        # we can actually see; otherwise report nothing rather than guess.
        if base and base not in index.dirs:
            return True, "relative"
        return False, "unknown"

    top_level = module_name.split(".")[0]

    if top_level in stdlib_modules:
        return True, "stdlib"

    if index.has_module(module_name):
        return True, "repo"

    if importer_dir and index.has_module(module_name, importer_dir):
        return True, "sibling"

    for ancestor in ancestors:
        if index.has_module(module_name, ancestor):
            return True, "ancestor"

    for root in syspath_roots:
        if index.has_module(module_name, root):
            return True, "syspath"

    # Repo-owned top-level name: the submodule genuinely does not exist. Do NOT
    # let the environment bless it.
    search_bases = tuple(ancestors) + tuple(syspath_roots)
    if index.owns_top_level(top_level, search_bases):
        return False, "unknown"

    if is_installed_module(top_level):
        return True, "installed"

    return False, "unknown"


def check_imports(repo_root, mode="staged", commit_range=None, explicit_files=None):
    """
    Main check over one source of files.

    mode is one of "staged" | "range" | "files".
      staged -> git index (pre-commit); content from `git show :<path>`
      range  -> files changed in commit_range; content from the blob at the
                range TIP -- the bytes actually being pushed
      files  -> explicit paths, content from the working tree

    Returns: (is_valid, findings) where findings is a list of dicts.
    Raises GitError on any git failure; callers MUST fail closed.
    """
    findings = []
    is_valid = True

    if mode == "range":
        tip_ref = _range_tip_ref(commit_range)
        target_files = get_range_py_files(commit_range, repo_root=repo_root)
        # The index comes from the PUSHED tree, not from whatever the working
        # tree happens to contain.
        index = PathIndex(get_tree_paths(tip_ref, repo_root=repo_root))
        read_content = lambda p: get_range_file_content(tip_ref, p, repo_root=repo_root)
        empty_msg = "No Python files changed in range %s." % commit_range
    elif mode == "files":
        target_files = [f.replace("\\", "/") for f in (explicit_files or [])
                        if f.endswith(".py")]
        index = PathIndex(build_repo_file_list(repo_root) + target_files)
        read_content = lambda p: get_worktree_file_content(repo_root, p)
        empty_msg = "No Python files given."
    else:
        target_files = get_staged_py_files(repo_root=repo_root)
        index = PathIndex(build_repo_file_list(repo_root) + target_files)
        read_content = lambda p: get_staged_file_content(p, repo_root=repo_root)
        empty_msg = "No staged Python files found."

    if not target_files:
        print(empty_msg, file=sys.stderr)
        return is_valid, findings

    stdlib_modules = get_stdlib_modules()

    for filepath in target_files:
        posix_path = filepath.replace("\\", "/")
        try:
            content = read_content(filepath)
        except (GitError, OSError) as e:
            findings.append({
                "file": filepath,
                "status": "error",
                "message": "Could not read content: %s" % e,
            })
            is_valid = False
            continue

        imports = extract_imports(filepath, content)
        syspath_roots = extract_syspath_roots(
            content, posix_path, index, read_content=read_content
        )
        for module_name, import_type, lineno, level in imports:
            resolvable, resolution_type = resolve_module(
                module_name, index, stdlib_modules,
                importer_path=posix_path, level=level,
                syspath_roots=syspath_roots,
            )

            if not resolvable:
                is_valid = False
                findings.append({
                    "file": filepath,
                    "import": ("." * level) + module_name,
                    "import_type": import_type,
                    "lineno": lineno,
                    "status": "unresolvable",
                    "message": f"Module '{module_name}' not found in repo, stdlib, or environment",
                })

    return is_valid, findings


def log_audit_event(repo_root, is_valid, findings):
    """Log findings to audit trail."""
    state_dir = Path(repo_root) / "state"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"WARNING: could not create state dir: {e}", file=sys.stderr)
        return
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


def build_parser():
    parser = argparse.ArgumentParser(
        prog="import_resolution_check.py",
        description=(
            "Guardrail G5: validate that Python imports resolve against the repo "
            "structure, the stdlib and the environment. Exit 0=all resolvable, "
            "1=unresolvable import(s), 2=usage or git error (fail-closed)."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--range",
        dest="commit_range",
        metavar="A..B",
        help=(
            "Check the files changed in a commit range, reading each file's blob "
            "at the range TIP -- i.e. the content actually being pushed. This is "
            "the mode hooks/pre-push-policy.sh uses; the index is EMPTY at push time."
        ),
    )
    mode.add_argument(
        "--staged",
        action="store_true",
        help="Check the git index (pre-commit use). Default when no mode is given.",
    )
    mode.add_argument(
        "--files",
        nargs="+",
        metavar="PATH",
        help="Check explicit paths, read from the working tree (manual invocation).",
    )
    parser.add_argument(
        "--repo",
        metavar="PATH",
        help="Repo root (default: git rev-parse --show-toplevel from cwd).",
    )
    return parser


def main(argv=None):
    """
    Main entry point. Exit 0 if all imports valid, 1 if any unresolvable,
    2 on usage error or git failure (fail-closed: a git failure is NEVER
    reported as clean).
    """
    args = build_parser().parse_args(argv)

    if args.commit_range:
        mode = "range"
    elif args.files:
        mode = "files"
    else:
        mode = "staged"

    repo_root = args.repo
    if not repo_root:
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
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            print("ERROR: not in a git repository", file=sys.stderr)
            return 2

    try:
        is_valid, findings = check_imports(
            repo_root,
            mode=mode,
            commit_range=args.commit_range,
            explicit_files=args.files,
        )
    except GitError as e:
        # Fail CLOSED: an unresolvable range or a git crash is NOT "zero files
        # changed". Reporting clean here is exactly the vacuous-green class this
        # gate exists to prevent.
        print("FATAL: git failure during import resolution check: %s" % e,
              file=sys.stderr)
        print("Failing CLOSED: refusing to report CLEAN on an unverifiable input.",
              file=sys.stderr)
        return 2

    log_audit_event(repo_root, is_valid, findings)

    if not is_valid:
        print("ERROR: Python import resolution check failed", file=sys.stderr)
        for finding in findings:
            if finding["status"] == "unresolvable":
                print(
                    f"  {finding['file']}:{finding['lineno']}: "
                    f"{finding['import_type']} {finding['import']} -- {finding['message']}",
                    file=sys.stderr,
                )
            else:
                print(f"  {finding['file']}: {finding['message']}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
