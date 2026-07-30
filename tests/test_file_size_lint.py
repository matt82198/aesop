"""Tests for tools/file_size_lint.py — Python file size linter."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Import the module under test
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import file_size_lint


class TestDiscoverPyFiles(unittest.TestCase):
    """Discovery of .py files under scan paths."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_discovers_py_files_recursively(self):
        """Finds .py files in nested directories."""
        sub = self.root / "pkg"
        sub.mkdir()
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        (sub / "b.py").write_text("y = 2\n", encoding="utf-8")
        (self.root / "readme.txt").write_text("hi\n", encoding="utf-8")

        found = file_size_lint.discover_py_files([self.root], self.root)
        names = [f.name for f in found]
        self.assertIn("a.py", names)
        self.assertIn("b.py", names)
        self.assertNotIn("readme.txt", names)

    def test_skips_node_modules(self):
        """Files inside node_modules are excluded."""
        nm = self.root / "node_modules" / "dep"
        nm.mkdir(parents=True)
        (nm / "index.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "ok.py").write_text("y = 2\n", encoding="utf-8")

        found = file_size_lint.discover_py_files([self.root], self.root)
        names = [f.name for f in found]
        self.assertIn("ok.py", names)
        self.assertNotIn("index.py", names)

    def test_single_file_path(self):
        """Passing a single .py file returns just that file."""
        f = self.root / "single.py"
        f.write_text("pass\n", encoding="utf-8")
        found = file_size_lint.discover_py_files([f], self.root)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "single.py")


class TestSuppressMarker(unittest.TestCase):
    """The # filesize-ok suppression comment."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_suppress_on_first_line(self):
        """Marker on line 1 suppresses the file."""
        f = self.root / "big.py"
        f.write_text("# filesize-ok\n" + "x = 1\n" * 600, encoding="utf-8")
        self.assertTrue(file_size_lint._has_suppress_marker(f))

    def test_suppress_on_third_line(self):
        """Marker on line 3 suppresses the file."""
        f = self.root / "big.py"
        f.write_text("#!/usr/bin/env python3\n# docs\n# filesize-ok\n" + "x = 1\n" * 600, encoding="utf-8")
        self.assertTrue(file_size_lint._has_suppress_marker(f))

    def test_suppress_not_on_line_four(self):
        """Marker on line 4 does NOT suppress."""
        f = self.root / "big.py"
        f.write_text("a\nb\nc\n# filesize-ok\n" + "x = 1\n" * 600, encoding="utf-8")
        self.assertFalse(file_size_lint._has_suppress_marker(f))


class TestLintFile(unittest.TestCase):
    """Core lint_file function."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_clean_file_no_findings(self):
        """A small file produces no findings."""
        f = self.root / "small.py"
        f.write_text("x = 1\n", encoding="utf-8")
        findings = file_size_lint.lint_file(f, self.root, 500, 20000)
        self.assertEqual(findings, [])

    def test_over_line_threshold(self):
        """File exceeding line threshold produces a 'lines' finding."""
        f = self.root / "big.py"
        f.write_text("x = 1\n" * 501, encoding="utf-8")
        findings = file_size_lint.lint_file(f, self.root, 500, 200000)
        types = [fd["type"] for fd in findings]
        self.assertIn("lines", types)

    def test_over_byte_threshold(self):
        """File exceeding byte threshold produces a 'bytes' finding."""
        f = self.root / "heavy.py"
        # 21000 bytes of content (each line ~21 bytes)
        f.write_text("x = 'aaaaaaaaaaaaaaaa'\n" * 1050, encoding="utf-8")
        findings = file_size_lint.lint_file(f, self.root, 50000, 20000)
        types = [fd["type"] for fd in findings]
        self.assertIn("bytes", types)

    def test_suppressed_file_no_findings(self):
        """A file with # filesize-ok produces no findings regardless of size."""
        f = self.root / "big.py"
        f.write_text("# filesize-ok\n" + "x = 1\n" * 600, encoding="utf-8")
        findings = file_size_lint.lint_file(f, self.root, 500, 20000)
        self.assertEqual(findings, [])

    def test_allowed_oversize_raises_threshold(self):
        """ALLOWED_OVERSIZE entry raises the effective threshold for that file."""
        f = self.root / "known_big.py"
        f.write_text("x = 1\n" * 600, encoding="utf-8")

        # Without override: should find it
        findings = file_size_lint.lint_file(f, self.root, 500, 200000)
        self.assertTrue(any(fd["type"] == "lines" for fd in findings))

        # With override: should pass
        with patch.dict(file_size_lint.ALLOWED_OVERSIZE, {"known_big.py": {"max_lines": 700}}):
            findings = file_size_lint.lint_file(f, self.root, 500, 200000)
            self.assertFalse(any(fd["type"] == "lines" for fd in findings))


class TestCLI(unittest.TestCase):
    """CLI integration tests via subprocess."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_cli_clean_exit_zero(self):
        """Clean repo exits 0."""
        f = self.root / "ok.py"
        f.write_text("pass\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "file_size_lint.py"),
             "--root", str(self.root), "--paths", str(self.root)],
            capture_output=True, text=True, cwd=str(self.root),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("[OK]", result.stdout)

    def test_cli_findings_exit_one(self):
        """Oversized file exits 1."""
        f = self.root / "big.py"
        f.write_text("x = 1\n" * 501, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "file_size_lint.py"),
             "--root", str(self.root), "--paths", str(self.root), "--max-lines", "500"],
            capture_output=True, text=True, cwd=str(self.root),
        )
        self.assertEqual(result.returncode, 1)

    def test_cli_json_output(self):
        """--json flag produces valid JSON with findings/count/root keys."""
        f = self.root / "big.py"
        f.write_text("x = 1\n" * 501, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "file_size_lint.py"),
             "--root", str(self.root), "--paths", str(self.root), "--json"],
            capture_output=True, text=True, cwd=str(self.root),
        )
        data = json.loads(result.stdout)
        self.assertIn("findings", data)
        self.assertIn("count", data)
        self.assertIn("root", data)
        self.assertGreater(data["count"], 0)

    def test_cli_bad_root_exit_two(self):
        """Non-existent root exits 2."""
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "file_size_lint.py"),
             "--root", str(self.root / "nonexistent")],
            capture_output=True, text=True, cwd=str(self.root),
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
