#!/usr/bin/env python3
"""
Cross-module call-site arity checker.

Root cause this closes: a shared helper module's function signature changes
(new required params added, or removed) and one or more callers elsewhere in
the repo are not updated to match. Nothing statically typed catches this in
Python -- it surfaces only as a runtime TypeError the first time the callee
actually executes. In this repo that has already happened for real: PR #652
("refactor/browser proof helpers") extended
`playwright_common.start_server(root, port)` to
`start_server(root, port, repo, serve_script, ...)`, and one call site
(`verify_wave_telemetry.run_work_proof`) was left calling the old 2-arg form,
which broke the browser-proofs CI job with:

    TypeError: start_server() missing 2 required positional arguments:
    'repo' and 'serve_script'

No existing tool in tools/ checks call-site arity against a function's
definition (import_resolution_check.py / Guardrail G5 only checks that an
imported *module* resolves, never that the *names* pulled out of it via
`from X import Y` exist, and never that a call site's argument count is
compatible with the callee's parameters). This tool closes that hole.

Scope & method (static, conservative -- false negatives are acceptable,
false positives are not):
  - Scans tracked *.py files (via `git ls-files`, or an explicit --paths
    list). Builds a registry of top-level (module-level, non-nested,
    non-decorated) function definitions per file, keyed by name; a later
    `def` of the same name in the same file wins (matches runtime rebinding).
  - Resolves two import shapes to a target file:
      * `from <module> import a, b as c`      (module.py sibling-in-same-dir
                                                first, else repo-root-relative)
      * `import <module> as alias` + `alias.func(...)` attribute calls
    Dotted modules (`state_store.materialize`) resolve repo-root-relative.
  - A name imported from elsewhere but also *defined* at module level in the
    same file is treated as locally shadowed and skipped (the local def
    wins at runtime; this also avoids false positives on files mid-migration
    between a shared helper and a local override -- exactly PR #652's
    eventual shape for verify_wave_telemetry.py etc).
  - Decorated target functions are skipped entirely (decorator may alter the
    effective signature; we cannot know statically) -- signature "unknown",
    never flagged.
  - Call sites using `*args` unpacking or `**kwargs` unpacking are skipped
    entirely for the same reason (argument count/keys are not statically
    knowable).
  - Findings:
      * missing_required   -- a required positional or keyword-only param
                               of the callee is not covered by the call's
                               positional count or keyword names.
      * too_many_positional -- more positional args passed than the callee
                               accepts and the callee has no *args.
      * unexpected_keyword  -- a keyword arg name the callee doesn't accept
                               and the callee has no **kwargs.

Exit codes: 0 = clean, 1 = findings, 2 = could not evaluate (no files
scanned, git unavailable, etc -- never collapsed into 0).

CLI:
  callsite_arity_check.py [--check] [--json] [--paths PATH ...] [--root DIR]
"""

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class FuncSig:
    """Statically-derived call signature of a module-level function."""

    __slots__ = (
        "name", "positional", "defaults_mask", "has_varargs",
        "kwonly", "kwonly_required", "has_varkw", "lineno",
    )

    def __init__(self, name: str, positional: List[str], defaults_mask: List[bool],
                 has_varargs: bool, kwonly: List[str], kwonly_required: List[bool],
                 has_varkw: bool, lineno: int):
        self.name = name
        self.positional = positional          # ordered posonly + pos-or-kw names
        self.defaults_mask = defaults_mask     # parallel to positional: has a default?
        self.has_varargs = has_varargs
        self.kwonly = kwonly
        self.kwonly_required = kwonly_required  # parallel to kwonly: required (no default)?
        self.has_varkw = has_varkw
        self.lineno = lineno

    def valid_keyword_names(self):
        return set(self.positional) | set(self.kwonly)


def build_func_sig(node) -> Optional[FuncSig]:
    """Build a FuncSig from an ast.FunctionDef/AsyncFunctionDef, or None if
    the function is decorated (signature not statically trustworthy)."""
    if node.decorator_list:
        return None

    args = node.args
    posonly = [a.arg for a in getattr(args, "posonlyargs", [])]
    regular = [a.arg for a in args.args]
    positional = posonly + regular

    n_defaults = len(args.defaults)
    defaults_mask = [False] * (len(positional) - n_defaults) + [True] * n_defaults
    if len(defaults_mask) != len(positional):
        # Defensive: shouldn't happen per Python's own AST invariants.
        defaults_mask = [False] * len(positional)

    kwonly = [a.arg for a in args.kwonlyargs]
    kwonly_required = [d is None for d in args.kw_defaults]

    has_varargs = args.vararg is not None
    has_varkw = args.kwarg is not None

    return FuncSig(
        name=node.name,
        positional=positional,
        defaults_mask=defaults_mask,
        has_varargs=has_varargs,
        kwonly=kwonly,
        kwonly_required=kwonly_required,
        has_varkw=has_varkw,
        lineno=node.lineno,
    )


class ModuleInfo:
    """Parsed info about one repo .py file needed for arity checking."""

    def __init__(self, rel_path: str, tree: ast.Module):
        self.rel_path = rel_path
        self.tree = tree
        self.functions: Dict[str, FuncSig] = {}
        self.locally_bound_names = set()  # names assigned/def'd at module level
        self._index()

    def _index(self):
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.locally_bound_names.add(node.name)
                sig = build_func_sig(node)
                if sig is not None:
                    self.functions[node.name] = sig
                else:
                    # Decorated def: mark as locally bound but unknown sig,
                    # so calls to it are never checked (pop any earlier sig).
                    self.functions.pop(node.name, None)
            elif isinstance(node, ast.ClassDef):
                self.locally_bound_names.add(node.name)
            elif isinstance(node, (ast.Assign,)):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        self.locally_bound_names.add(tgt.id)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.locally_bound_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.locally_bound_names.add(alias.asname or alias.name.split(".")[0])


def parse_module(repo_root: Path, rel_path: str) -> Optional[ModuleInfo]:
    abs_path = repo_root / rel_path
    try:
        content = abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        tree = ast.parse(content, filename=rel_path)
    except SyntaxError:
        return None
    return ModuleInfo(rel_path, tree)


def resolve_module_file(repo_root: Path, caller_rel_path: str, module_name: str) -> Optional[str]:
    """Resolve a dotted or bare module name referenced by an import in
    caller_rel_path to a repo-relative .py file path, or None if it cannot
    be resolved to a file on disk (stdlib/third-party/genuinely missing --
    not this tool's job to flag; import_resolution_check.py covers that)."""
    parts = module_name.split(".")

    if len(parts) == 1:
        # Sibling-in-same-directory first (this is the convention used
        # throughout tools/: `from playwright_common import X` inside
        # tools/verify_dash.py resolves via the script's own directory on
        # sys.path, NOT a repo-root-relative package lookup).
        caller_dir = Path(caller_rel_path).parent
        sibling = caller_dir / f"{parts[0]}.py"
        if (repo_root / sibling).is_file():
            return sibling.as_posix()

    # Repo-root-relative (covers dotted packages and top-level modules).
    candidate = Path(*parts).with_suffix(".py")
    if (repo_root / candidate).is_file():
        return candidate.as_posix()

    candidate_pkg = Path(*parts) / "__init__.py"
    if (repo_root / candidate_pkg).is_file():
        return candidate_pkg.as_posix()

    return None


def build_symbol_map(repo_root: Path, mod: ModuleInfo) -> Dict[str, Tuple[str, str]]:
    """name-as-used-in-this-file -> (target_rel_path, original_name_in_target).

    Only includes names NOT shadowed by a later module-level definition in
    the same file (shadowing wins at runtime)."""
    symbol_map: Dict[str, Tuple[str, str]] = {}

    # Track the *last* top-level binding order so a later `def` shadows an
    # earlier `from X import name`.
    for node in mod.tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            target = resolve_module_file(repo_root, mod.rel_path, node.module)
            if target is None:
                continue
            for alias in node.names:
                used_name = alias.asname or alias.name
                symbol_map[used_name] = (target, alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Assign)):
            # A later module-level def/assign shadows an earlier import.
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            else:
                names = [node.name]
            for n in names:
                symbol_map.pop(n, None)

    return symbol_map


def build_module_alias_map(repo_root: Path, mod: ModuleInfo) -> Dict[str, str]:
    """alias-as-used-in-this-file -> target_rel_path, for `import X as alias`."""
    alias_map: Dict[str, str] = {}
    for node in mod.tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                used_name = alias.asname or alias.name.split(".")[0]
                target = resolve_module_file(repo_root, mod.rel_path, alias.name)
                if target is not None:
                    alias_map[used_name] = target
    return alias_map


def call_has_unpacking(call: ast.Call) -> bool:
    for a in call.args:
        if isinstance(a, ast.Starred):
            return True
    for kw in call.keywords:
        if kw.arg is None:
            return True
    return False


def check_call(sig: FuncSig, call: ast.Call) -> List[str]:
    """Return list of finding-description strings for one call site."""
    findings = []
    if call_has_unpacking(call):
        return findings

    positional_count = len(call.args)
    keyword_names = {kw.arg for kw in call.keywords if kw.arg is not None}

    missing = []
    for i, name in enumerate(sig.positional):
        covered = (i < positional_count) or (name in keyword_names) or sig.defaults_mask[i]
        if not covered:
            missing.append(name)

    for name, required in zip(sig.kwonly, sig.kwonly_required):
        if required and name not in keyword_names:
            missing.append(name)

    if missing:
        findings.append(
            f"call to {sig.name}() is missing required argument(s): {', '.join(missing)} "
            f"(callee defined at line {sig.lineno})"
        )

    if not sig.has_varargs and positional_count > len(sig.positional):
        findings.append(
            f"call to {sig.name}() passes {positional_count} positional argument(s), "
            f"callee accepts at most {len(sig.positional)} (defined at line {sig.lineno})"
        )

    if not sig.has_varkw:
        valid = sig.valid_keyword_names()
        unexpected = sorted(keyword_names - valid)
        if unexpected:
            findings.append(
                f"call to {sig.name}() passes unexpected keyword argument(s): "
                f"{', '.join(unexpected)} (callee defined at line {sig.lineno})"
            )

    return findings


def check_file(repo_root: Path, rel_path: str, mod: ModuleInfo,
                module_cache: Dict[str, Optional[ModuleInfo]]) -> List[dict]:
    findings = []
    symbol_map = build_symbol_map(repo_root, mod)
    alias_map = build_module_alias_map(repo_root, mod)

    def get_target_module(target_rel: str) -> Optional[ModuleInfo]:
        if target_rel not in module_cache:
            module_cache[target_rel] = parse_module(repo_root, target_rel)
        return module_cache[target_rel]

    for node in ast.walk(mod.tree):
        if not isinstance(node, ast.Call):
            continue

        target_rel = None
        orig_name = None

        func = node.func
        if isinstance(func, ast.Name):
            entry = symbol_map.get(func.id)
            if entry is not None:
                target_rel, orig_name = entry
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = alias_map.get(func.value.id)
            if base is not None:
                target_rel, orig_name = base, func.attr

        if target_rel is None:
            continue

        target_mod = get_target_module(target_rel)
        if target_mod is None:
            continue

        sig = target_mod.functions.get(orig_name)
        if sig is None:
            continue

        for msg in check_call(sig, node):
            findings.append({
                "file": rel_path,
                "line": node.lineno,
                "target": target_rel,
                "message": msg,
            })

    return findings


def list_tracked_py_files(repo_root: Path) -> Optional[List[str]]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "*.py"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return files


def resolve_root(root_arg: str) -> Path:
    return Path(root_arg).resolve()


def gather_paths(repo_root: Path, explicit_paths: Optional[List[str]]) -> Optional[List[str]]:
    """Return repo-relative .py file paths to scan, or None on hard error
    (distinct from an empty-but-successful scan list)."""
    if explicit_paths:
        out = []
        for p in explicit_paths:
            pth = Path(p)
            abs_pth = pth if pth.is_absolute() else (repo_root / pth)
            if abs_pth.is_file() and abs_pth.suffix == ".py":
                try:
                    out.append(abs_pth.resolve().relative_to(repo_root).as_posix())
                except ValueError:
                    continue
            elif abs_pth.is_dir():
                for f in abs_pth.rglob("*.py"):
                    try:
                        out.append(f.resolve().relative_to(repo_root).as_posix())
                    except ValueError:
                        continue
        return out

    return list_tracked_py_files(repo_root)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-module call-site arity checker (repo-local functions)"
    )
    parser.add_argument("--check", action="store_true", default=True,
                         help="Check mode (default, only mode currently)")
    parser.add_argument("--json", action="store_true", help="Output findings as JSON")
    parser.add_argument("--paths", nargs="*", default=None,
                         help="Explicit files/directories to scan (default: all git-tracked .py files)")
    parser.add_argument("--root", type=str, default=".", help="Repository root directory")
    args = parser.parse_args()

    repo_root = resolve_root(args.root)
    if not repo_root.is_dir():
        print(f"ERROR: root not found: {repo_root}", file=sys.stderr)
        return 2

    rel_paths = gather_paths(repo_root, args.paths)
    if rel_paths is None:
        print("ERROR: could not enumerate Python files (git unavailable?)", file=sys.stderr)
        return 2
    if len(rel_paths) == 0:
        print("ERROR: no Python files found to scan (COULD NOT EVALUATE)", file=sys.stderr)
        return 2

    module_cache: Dict[str, Optional[ModuleInfo]] = {}
    all_findings: List[dict] = []
    parse_errors: List[str] = []

    for rel_path in sorted(set(rel_paths)):
        mod = module_cache.get(rel_path)
        if rel_path not in module_cache:
            mod = parse_module(repo_root, rel_path)
            module_cache[rel_path] = mod
        if mod is None:
            parse_errors.append(rel_path)
            continue
        all_findings.extend(check_file(repo_root, rel_path, mod, module_cache))

    exit_code = 1 if all_findings else 0

    if args.json:
        output = {
            "status": "clean" if exit_code == 0 else "findings",
            "exit_code": exit_code,
            "files_scanned": len(rel_paths),
            "unparseable_files": parse_errors,
            "findings": all_findings,
        }
        print(json.dumps(output, indent=2))
    else:
        if all_findings:
            for f in all_findings:
                print(f"{f['file']}:{f['line']}: {f['message']} [target: {f['target']}]")
        else:
            print(f"[OK] callsite_arity_check: {len(rel_paths)} files scanned, no arity mismatches found")
        if parse_errors:
            print(f"NOTE: {len(parse_errors)} file(s) could not be parsed and were skipped: "
                  f"{', '.join(parse_errors[:10])}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
