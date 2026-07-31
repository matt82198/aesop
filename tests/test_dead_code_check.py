"""Tests for tools/dead_code_check.py -- AST-based dead code detector."""
import json
import os
import subprocess
import sys
import tempfile
import unittest


# Resolve repo root (parent of tests/)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO_ROOT, "tools", "dead_code_check.py")

sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
from dead_code_check import (  # noqa: E402
    collect_definitions,
    collect_references,
    collect_init_reexports,
    find_python_files,
    has_suppression,
    is_dunder,
    read_file_lines,
    scan,
)


def _write(directory, name, content):
    """Write a Python file in the given directory."""
    path = os.path.join(directory, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestIsDunder(unittest.TestCase):
    def test_dunder_true(self):
        self.assertTrue(is_dunder("__init__"))
        self.assertTrue(is_dunder("__name__"))
        self.assertTrue(is_dunder("__all__"))

    def test_dunder_false(self):
        self.assertFalse(is_dunder("my_func"))
        self.assertFalse(is_dunder("_private"))
        self.assertFalse(is_dunder("__leading"))


class TestHasSuppression(unittest.TestCase):
    def test_suppression_present(self):
        lines = ["def foo():  # dead-code-ok\n"]
        self.assertTrue(has_suppression(lines, 1))

    def test_suppression_absent(self):
        lines = ["def foo():\n"]
        self.assertFalse(has_suppression(lines, 1))

    def test_out_of_range(self):
        self.assertFalse(has_suppression([], 1))
        self.assertFalse(has_suppression(["x\n"], 0))


class TestCollectDefinitions(unittest.TestCase):
    def test_function_def(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _write(td, "mod.py", "def foo():\n    pass\n")
            lines = read_file_lines(fp)
            defs = collect_definitions(fp, td, lines)
            names = [d["name"] for d in defs]
            self.assertIn("foo", names)

    def test_class_def(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _write(td, "mod.py", "class MyClass:\n    pass\n")
            lines = read_file_lines(fp)
            defs = collect_definitions(fp, td, lines)
            names = [d["name"] for d in defs]
            self.assertIn("MyClass", names)

    def test_variable_def(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _write(td, "mod.py", "MY_VAR = 42\n")
            lines = read_file_lines(fp)
            defs = collect_definitions(fp, td, lines)
            names = [d["name"] for d in defs]
            self.assertIn("MY_VAR", names)

    def test_dunder_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _write(td, "mod.py",
                         "def __init__(self):\n    pass\n"
                         "__all__ = ['x']\n")
            lines = read_file_lines(fp)
            defs = collect_definitions(fp, td, lines)
            names = [d["name"] for d in defs]
            self.assertEqual(names, [])

    def test_suppression_excludes(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _write(td, "mod.py",
                         "def old_func():  # dead-code-ok\n    pass\n")
            lines = read_file_lines(fp)
            defs = collect_definitions(fp, td, lines)
            names = [d["name"] for d in defs]
            self.assertNotIn("old_func", names)


class TestCollectReferences(unittest.TestCase):
    def test_function_call(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _write(td, "mod.py", "foo()\n")
            lines = read_file_lines(fp)
            refs = collect_references(fp, lines)
            self.assertIn("foo", refs)

    def test_import_from(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _write(td, "mod.py", "from bar import baz\n")
            lines = read_file_lines(fp)
            refs = collect_references(fp, lines)
            self.assertIn("baz", refs)

    def test_attribute_access(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _write(td, "mod.py", "obj.method()\n")
            lines = read_file_lines(fp)
            refs = collect_references(fp, lines)
            self.assertIn("method", refs)


class TestCollectInitReexports(unittest.TestCase):
    def test_init_reexport(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _write(td, "__init__.py", "from .mod import helper\n")
            exports = collect_init_reexports(fp)
            self.assertIn("helper", exports)

    def test_non_init_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            fp = _write(td, "mod.py", "from .other import helper\n")
            exports = collect_init_reexports(fp)
            self.assertEqual(exports, set())


class TestFindPythonFiles(unittest.TestCase):
    def test_finds_py_files(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "a.py", "x = 1\n")
            _write(td, "sub/b.py", "y = 2\n")
            files = find_python_files(td)
            basenames = [os.path.basename(f) for f in files]
            self.assertIn("a.py", basenames)
            self.assertIn("b.py", basenames)

    def test_excludes_tests_dir(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "mod.py", "x = 1\n")
            _write(td, "tests/test_x.py", "y = 2\n")
            files = find_python_files(td)
            basenames = [os.path.basename(f) for f in files]
            self.assertIn("mod.py", basenames)
            self.assertNotIn("test_x.py", basenames)

    def test_scan_dirs_filter(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "src/a.py", "x = 1\n")
            _write(td, "other/b.py", "y = 2\n")
            files = find_python_files(td, scan_dirs=["src"])
            basenames = [os.path.basename(f) for f in files]
            self.assertIn("a.py", basenames)
            self.assertNotIn("b.py", basenames)


# The lint_core migration changed scan() to return Finding objects instead of dicts.
# Finding has (file, line, type, message); the symbol name is the last token of message
# (e.g. "class Dead" / "function dead_fn"). Tests extract it from there.
class TestScan(unittest.TestCase):
    def test_detects_dead_function(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "mod.py",
                   "def used():\n    pass\n\ndef dead():\n    pass\n\nused()\n")
            findings = scan(td)
            names = [f.message.split()[-1] for f in findings]
            self.assertIn("dead", names)
            self.assertNotIn("used", names)

    def test_clean_no_dead_code(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "mod.py",
                   "def helper():\n    pass\n\nhelper()\n")
            findings = scan(td)
            self.assertEqual(findings, [])

    def test_class_dead(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "mod.py",
                   "class Used:\n    pass\n\nclass Dead:\n    pass\n\nx = Used()\n")
            findings = scan(td)
            names = [f.message.split()[-1] for f in findings]
            self.assertIn("Dead", names)
            self.assertNotIn("Used", names)

    def test_variable_dead(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "mod.py",
                   "USED = 1\nDEAD = 2\nprint(USED)\n")
            findings = scan(td)
            names = [f.message.split()[-1] for f in findings]
            self.assertIn("DEAD", names)
            self.assertNotIn("USED", names)

    def test_cross_file_reference(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "lib.py", "def helper():\n    pass\n")
            _write(td, "main.py", "from lib import helper\nhelper()\n")
            findings = scan(td)
            names = [f.message.split()[-1] for f in findings]
            self.assertNotIn("helper", names)

    def test_test_file_references_count(self):
        """References in tests/ still count as references."""
        with tempfile.TemporaryDirectory() as td:
            _write(td, "mod.py", "def tested():\n    pass\n")
            _write(td, "tests/test_mod.py", "from mod import tested\ntested()\n")
            findings = scan(td)
            names = [f.message.split()[-1] for f in findings]
            # tested() is referenced in tests, so not dead
            self.assertNotIn("tested", names)

    def test_suppression_skips(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "mod.py",
                   "def legacy():  # dead-code-ok\n    pass\n")
            findings = scan(td)
            names = [f.message.split()[-1] for f in findings]
            self.assertNotIn("legacy", names)

    def test_init_reexport_not_dead(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "pkg/__init__.py", "from .impl import worker\n")
            _write(td, "pkg/impl.py", "def worker():\n    pass\n")
            findings = scan(td)
            names = [f.message.split()[-1] for f in findings]
            self.assertNotIn("worker", names)

    def test_syntax_error_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "bad.py", "def broken(\n")
            _write(td, "good.py", "def ok():\n    pass\nok()\n")
            # Should not crash
            findings = scan(td)
            self.assertIsInstance(findings, list)


class TestCLI(unittest.TestCase):
    def test_cli_clean(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "mod.py", "def used():\n    pass\nused()\n")
            r = subprocess.run(
                [sys.executable, TOOL, "--root", td],
                capture_output=True, text=True, cwd=td,  # subprocess-ok
            )
            self.assertEqual(r.returncode, 0)
            self.assertIn("No dead code", r.stdout)

    def test_cli_findings(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "mod.py", "def dead():\n    pass\n")
            r = subprocess.run(
                [sys.executable, TOOL, "--root", td],
                capture_output=True, text=True, cwd=td,  # subprocess-ok
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("dead", r.stdout)

    def test_cli_json_output(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "mod.py", "def dead():\n    pass\n")
            r = subprocess.run(
                [sys.executable, TOOL, "--root", td, "--json"],
                capture_output=True, text=True, cwd=td,  # subprocess-ok
            )
            self.assertEqual(r.returncode, 1)
            data = json.loads(r.stdout)
            self.assertIsInstance(data, list)
            self.assertTrue(len(data) > 0)
            self.assertEqual(data[0]["name"], "dead")

    def test_cli_paths_filter(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "src/a.py", "def only_here():\n    pass\n")
            _write(td, "other/b.py", "def other_dead():\n    pass\n")
            r = subprocess.run(
                [sys.executable, TOOL, "--root", td, "--paths", "src"],
                capture_output=True, text=True, cwd=td,  # subprocess-ok
            )
            # only_here should be found but not other_dead (not in scan dirs)
            self.assertIn("only_here", r.stdout)
            self.assertNotIn("other_dead", r.stdout)


if __name__ == "__main__":
    unittest.main()
