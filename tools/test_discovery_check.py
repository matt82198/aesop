#!/usr/bin/env python3
"""
tools.test_discovery_check -- AST guard for tests invisible to unittest discover.

Root cause this closes: `python -m unittest discover` (this repo's test runner;
`npm run test:py`) silently collects ZERO tests from two shapes of file:

  BARE_TEST_FUNCTION -- a module-level `def test_*():` that isn't inside any
    class. unittest discover only walks TestCase subclasses; a bare function
    is never instantiated, never run, and reports nothing -- not even a skip.

  BASELESS_TEST_CLASS -- a `class Test*:` with `test_*` methods but NO base
    class at all (a plain class with zero inheritance). unittest discover
    requires TestCase (directly or via any explicit base); a baseless class
    is invisible to it in exactly the same silent way.

Both shapes pass ordinary review because an alternate discovery-based test
runner (e.g. a CI shard job that happens to invoke a different runner than
unittest) DOES collect and run them, so "tests passed locally" is compatible
with the tests never running under this repo's actual gate.

This logic already exists as a regression test (tests/test_no_bare_test_functions.py)
but that only fires when someone runs `npm run test:py` -- the full ~800-test
suite -- which is not wired into the fast pre-push hook (hooks/pre-push-policy.sh
runs targeted small gates, not the full suite). A file can therefore be pushed,
fail this exact check on every CI run, get re-pushed, and fail again -- costing
a CI round-trip per fix attempt instead of a single quick local check pre-push.

This tool extracts the same AST scan into a standalone, near-instant CLI
(no test execution, just parsing) so it can be run as a fast, targeted
pre-push/CI gate independent of the full suite. It does not replace
tests/test_no_bare_test_functions.py; both keep running (belt and suspenders:
one is a fast local/CI gate, the other is proof the invariant holds inside
the suite itself).

Suppression: append `# discovery-ok` on the flagged function/class's `def`/
`class` line to allow a specific, reviewed exception.

Exit codes:
  0 = no findings (and at least one file was scanned)
  1 = findings detected
  2 = could not evaluate (usage error, no scan target exists, or nothing
      matched *.py under the scan target -- NEVER collapsed into 0)

Usage:
  python tools/test_discovery_check.py [--check] [--json] [--paths PATH [PATH ...]]
                                        [--root DIR]

Options:
  --check          Check mode (default; scans and reports/exits accordingly)
  --json           Output findings as JSON instead of text
  --paths PATH...  Override the default scan location (tests/) with one or
                    more files/directories (recursively scanned for
                    test_*.py files)
  --root DIR       Repository root used to resolve relative --paths / the
                    default scan target (default: this file's repo root)
"""
import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCAN_DIR = "tests"
SUPPRESS_MARKER = "discovery-ok"
SELF_TEST_FILENAME = "test_no_bare_test_functions.py"


def _is_suppressed(source_lines, lineno):
    """Check for a `# discovery-ok` marker on the def/class's own source line."""
    idx = lineno - 1
    if 0 <= idx < len(source_lines):
        return SUPPRESS_MARKER in source_lines[idx]
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

    source_lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.Module):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                    if _is_suppressed(source_lines, item.lineno):
                        continue
                    findings.append({
                        "file": str(relative_path).replace("\\", "/"),
                        "line": item.lineno,
                        "rule": "BARE_TEST_FUNCTION",
                        "message": (
                            f"def {item.name}() is a module-level test function, not "
                            "inside a unittest.TestCase; unittest discover silently "
                            "collects zero tests from it."
                        ),
                    })
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            has_test_methods = any(
                isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
                for item in node.body
            )
            if has_test_methods and not node.bases:
                if _is_suppressed(source_lines, node.lineno):
                    continue
                findings.append({
                    "file": str(relative_path).replace("\\", "/"),
                    "line": node.lineno,
                    "rule": "BASELESS_TEST_CLASS",
                    "message": (
                        f"class {node.name} has test_* methods but no base class; "
                        "unittest discover requires subclassing unittest.TestCase "
                        "(directly or via any explicit base) or its tests never run."
                    ),
                })

    return findings


def _iter_test_files(paths):
    """Yield absolute Path for every test_*.py file under the given files/dirs.

    Mirrors the convention `tests/test_no_bare_test_functions.py` itself uses
    (glob('test_*.py'), non-recursive) so results match the regression test
    exactly for the default scan target; directories other than the default
    are still walked non-recursively per-directory but each explicit
    file argument is always included regardless of name.
    """
    seen = set()
    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if p.is_dir():
            for py_file in sorted(p.glob("test_*.py")):
                if py_file.name == SELF_TEST_FILENAME:
                    continue
                if py_file not in seen:
                    seen.add(py_file)
                    yield py_file
        elif p.is_file() and p.suffix == ".py":
            if p.name == SELF_TEST_FILENAME:
                continue
            if p not in seen:
                seen.add(p)
                yield p
        # Non-existent paths are reported by the caller as a could-not-evaluate
        # condition, not silently skipped (see scan_paths).


def scan_paths(paths, repo_root=None):
    """Scan the given files/dirs for discovery-invisible test shapes.

    Returns (findings, scanned_count, missing_targets):
      findings        -- list of finding dicts, sorted by (file, line, rule)
      scanned_count    -- number of .py files actually read
      missing_targets  -- input paths that resolved to neither an existing
                           file nor an existing directory (caller's signal
                           to fail closed with exit 2 rather than report a
                           false-clean 0 findings from scanning nothing)
    """
    repo_root = Path(repo_root) if repo_root else REPO_ROOT
    all_findings = []
    missing_targets = []
    scanned_count = 0

    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.exists():
            missing_targets.append(str(raw))

    for py_file in _iter_test_files(paths):
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        try:
            relative_path = py_file.relative_to(repo_root)
        except ValueError:
            relative_path = py_file

        scanned_count += 1
        all_findings.extend(scan_source(source, relative_path))

    all_findings.sort(key=lambda f: (f["file"], f["line"], f["rule"]))
    return all_findings, scanned_count, missing_targets


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    parser = argparse.ArgumentParser(
        prog="test_discovery_check.py",
        description="AST guard for test_*.py shapes invisible to `unittest discover` "
                     "(bare module-level test_* functions, baseless Test* classes).",
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
    parser.add_argument(
        "--root",
        default=None,
        help="Repository root used to resolve relative --paths / the default "
             "scan target (default: this file's repo root).",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    repo_root = Path(args.root).resolve() if args.root else REPO_ROOT
    scan_targets = args.paths if args.paths else [str(repo_root / DEFAULT_SCAN_DIR)]

    try:
        findings, scanned_count, missing_targets = scan_paths(scan_targets, repo_root=repo_root)
    except Exception as exc:  # pragma: no cover - defensive, mirrors neighbouring tools
        sys.stderr.write(f"ERROR: test_discovery_check failed: {exc}\n")
        return 2

    if missing_targets:
        message = (
            "test_discovery_check: COULD NOT EVALUATE -- scan target(s) do not "
            "exist: {0}".format(", ".join(missing_targets))
        )
        if args.json:
            import json
            print(json.dumps({
                "ok": False,
                "error": message,
                "missing_targets": missing_targets,
                "findings": [],
            }, indent=2))
        else:
            print(message)
        return 2

    if scanned_count == 0:
        message = (
            "test_discovery_check: COULD NOT EVALUATE -- no test_*.py files found "
            "under scan target(s): {0}".format(", ".join(str(t) for t in scan_targets))
        )
        if args.json:
            import json
            print(json.dumps({
                "ok": False,
                "error": message,
                "missing_targets": [],
                "findings": [],
            }, indent=2))
        else:
            print(message)
        return 2

    if args.json:
        import json
        print(json.dumps({
            "ok": len(findings) == 0,
            "scanned_files": scanned_count,
            "findings": findings,
        }, indent=2))
    else:
        if findings:
            print(f"test_discovery_check: {len(findings)} finding(s) across {scanned_count} file(s)")
            for f in findings:
                print(f"  {f['file']}:{f['line']} [{f['rule']}] {f['message']}")
            print(f"\nFAIL: {len(findings)} discovery-invisible test shape(s) detected")
            print(f"Suppress a reviewed exception with a trailing '# {SUPPRESS_MARKER}' comment "
                  "on the def/class line.")
        else:
            print(f"test_discovery_check: PASS (no findings, {scanned_count} file(s) scanned)")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
