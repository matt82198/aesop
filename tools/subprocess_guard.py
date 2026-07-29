#!/usr/bin/env python3
"""
tools.subprocess_guard -- AST guard for subprocess anti-patterns in tests/.

Extends the test-hygiene framework (tests/test_test_hygiene.py) with a
dedicated, reusable scanner for subprocess brittleness patterns that have
bitten Windows CI:

  BARE_BASH_NO_CWD -- subprocess.run(['bash', ...]) / subprocess.Popen(['bash', ...])
    with no cwd= keyword at all. A bare bash invocation inherits whatever the
    test process's current directory happens to be at call time, which is
    exactly the kind of ambient state that breaks under parallel test runs
    and CI runners with a different starting cwd.

  SHELL_TRUE -- subprocess.run(...)/subprocess.Popen(...) with shell=True.
    Shell injection risk; also a common source of Windows vs POSIX shell
    divergence (cmd.exe vs sh).

  CWD_NONE -- subprocess.run(...)/subprocess.Popen(...) with an EXPLICIT
    cwd=None. Signals the author meant to pass a cwd and forgot, or is
    relying on ambient cwd; should be a tempdir or an explicit path.

  OS_SYSTEM -- os.system(...) calls in test files. No structured argument
    handling, no captured output, shell-injection prone; use subprocess
    instead.

Suppression: append `# subprocess-ok` on (or spanning) the flagged call's
source line(s) to allow a specific, reviewed exception.

Exit codes:
  0 = no findings
  1 = findings detected
  2 = usage/argument error

Usage:
  python tools/subprocess_guard.py [--check] [--json] [--paths PATH [PATH ...]]

Options:
  --check          Check mode (default; scans and reports/exits accordingly)
  --json           Output findings as JSON instead of text
  --paths PATH...  Override the default scan location (tests/) with one or
                    more files/directories (recursively scanned for *.py)
"""
import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCAN_DIR = "tests"
SUPPRESS_MARKER = "subprocess-ok"

SUBPROCESS_CALL_NAMES = ("run", "Popen")


class _ImportTracker(ast.NodeVisitor):
    """Resolve local names bound to subprocess.run/Popen and os.system."""

    def __init__(self):
        self.subprocess_alias = None
        self.os_alias = None
        # local_name -> canonical ('run'|'Popen') for `from subprocess import run`
        self.subprocess_funcs = {}
        # local_name for `from os import system`
        self.os_system_name = None

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name == "subprocess":
                self.subprocess_alias = alias.asname or "subprocess"
            elif alias.name == "os":
                self.os_alias = alias.asname or "os"
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_CALL_NAMES:
                    self.subprocess_funcs[alias.asname or alias.name] = alias.name
        elif node.module == "os":
            for alias in node.names:
                if alias.name == "system":
                    self.os_system_name = alias.asname or alias.name
        self.generic_visit(node)


def _call_kind(node, tracker):
    """Classify a Call node as ('subprocess', 'run'|'Popen'), ('os', 'system'), or None."""
    func = node.func
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            if (
                tracker.subprocess_alias
                and func.value.id == tracker.subprocess_alias
                and func.attr in SUBPROCESS_CALL_NAMES
            ):
                return ("subprocess", func.attr)
            if tracker.os_alias and func.value.id == tracker.os_alias and func.attr == "system":
                return ("os", "system")
    elif isinstance(func, ast.Name):
        if func.id in tracker.subprocess_funcs:
            return ("subprocess", tracker.subprocess_funcs[func.id])
        if tracker.os_system_name and func.id == tracker.os_system_name:
            return ("os", "system")
    return None


def _first_command_is_bash(node):
    """True if the call's first positional arg is a list/tuple literal starting with 'bash'."""
    if not node.args:
        return False
    first = node.args[0]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value == "bash"
    return False


def _get_keyword(node, name):
    """Return the ast node for keyword `name` in a Call, or None if absent."""
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_const_true(value_node):
    return isinstance(value_node, ast.Constant) and value_node.value is True


def _is_const_none(value_node):
    return isinstance(value_node, ast.Constant) and value_node.value is None


def _is_suppressed(source_lines, node):
    """Check for a `# subprocess-ok` marker on any physical line spanning the call."""
    start = node.lineno
    end = getattr(node, "end_lineno", node.lineno) or node.lineno
    for lineno in range(start, end + 1):
        idx = lineno - 1
        if 0 <= idx < len(source_lines) and SUPPRESS_MARKER in source_lines[idx]:
            return True
    return False


def scan_source(source, relative_path):
    """AST-scan one file's source text; return a list of finding dicts."""
    findings = []

    try:
        tree = ast.parse(source, filename=str(relative_path))
    except SyntaxError as exc:
        return [{
            "file": str(relative_path).replace("\\", "/"),
            "line": exc.lineno or 0,
            "rule": "PARSE_ERROR",
            "message": f"Could not parse file: {exc.msg}",
        }]

    tracker = _ImportTracker()
    tracker.visit(tree)

    source_lines = source.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        kind = _call_kind(node, tracker)
        if kind is None:
            continue

        if _is_suppressed(source_lines, node):
            continue

        module, func_name = kind
        rules_hit = []

        if module == "os" and func_name == "system":
            rules_hit.append((
                "OS_SYSTEM",
                "os.system() call in test file; use subprocess with captured "
                "output and an explicit cwd instead.",
            ))
        elif module == "subprocess":
            shell_value = _get_keyword(node, "shell")
            cwd_value = _get_keyword(node, "cwd")

            if shell_value is not None and _is_const_true(shell_value):
                rules_hit.append((
                    "SHELL_TRUE",
                    f"subprocess.{func_name}(..., shell=True) is a shell-injection "
                    "risk; pass an argv list instead.",
                ))

            if cwd_value is not None and _is_const_none(cwd_value):
                rules_hit.append((
                    "CWD_NONE",
                    f"subprocess.{func_name}(..., cwd=None) is explicit but "
                    "unresolved; use a tempdir or an explicit path.",
                ))

            if _first_command_is_bash(node) and cwd_value is None:
                rules_hit.append((
                    "BARE_BASH_NO_CWD",
                    f"subprocess.{func_name}(['bash', ...]) with no cwd= "
                    "inherits the caller's ambient cwd; pass cwd= explicitly.",
                ))

        for rule, message in rules_hit:
            findings.append({
                "file": str(relative_path).replace("\\", "/"),
                "line": node.lineno,
                "rule": rule,
                "message": message,
            })

    return findings


def _iter_py_files(paths):
    """Yield (absolute_path) for every .py file under the given files/dirs."""
    seen = set()
    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if p.is_dir():
            for py_file in sorted(p.rglob("*.py")):
                if py_file not in seen:
                    seen.add(py_file)
                    yield py_file
        elif p.is_file() and p.suffix == ".py":
            if p not in seen:
                seen.add(p)
                yield p
        # Silently skip paths that don't exist or aren't .py files/dirs;
        # the caller-provided --paths may include a mix of things.


def scan_paths(paths, repo_root=None):
    """Scan the given files/dirs for subprocess anti-patterns.

    Args:
        paths: iterable of file/dir path strings.
        repo_root: base for relative-path display (default: this file's repo root).

    Returns:
        list of finding dicts, sorted by (file, line, rule).
    """
    repo_root = Path(repo_root) if repo_root else REPO_ROOT
    all_findings = []

    for py_file in _iter_py_files(paths):
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        try:
            relative_path = py_file.relative_to(repo_root)
        except ValueError:
            relative_path = py_file

        all_findings.extend(scan_source(source, relative_path))

    all_findings.sort(key=lambda f: (f["file"], f["line"], f["rule"]))
    return all_findings


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    parser = argparse.ArgumentParser(
        prog="subprocess_guard.py",
        description="AST guard for subprocess anti-patterns in tests/ "
                     "(bare bash without cwd, shell=True, cwd=None, os.system).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode (default): scan and exit 1 if findings exist.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output findings as JSON instead of text.",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=None,
        help="Override the default scan location (tests/) with one or more "
             "files/directories.",
    )

    args = parser.parse_args(argv)

    scan_targets = args.paths if args.paths else [str(REPO_ROOT / DEFAULT_SCAN_DIR)]
    findings = scan_paths(scan_targets)

    if args.json:
        import json
        print(json.dumps({"ok": len(findings) == 0, "findings": findings}, indent=2))
    else:
        if findings:
            print(f"subprocess_guard: {len(findings)} finding(s)")
            for f in findings:
                print(f"  {f['file']}:{f['line']} [{f['rule']}] {f['message']}")
            print(f"\nFAIL: {len(findings)} subprocess anti-pattern(s) detected")
            print(f"Suppress a reviewed exception with a trailing '# {SUPPRESS_MARKER}' comment.")
        else:
            print("subprocess_guard: PASS (no findings)")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
