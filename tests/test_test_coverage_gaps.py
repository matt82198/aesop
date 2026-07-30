"""Tests for tools/test_coverage_gaps.py — test coverage gap finder."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestCoverageGaps(unittest.TestCase):
    """Test the coverage gap analysis logic via subprocess."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = self.tmpdir.name
        # Create a minimal source layout
        os.makedirs(os.path.join(self.root, "tools"))
        os.makedirs(os.path.join(self.root, "tests"))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write(self, relpath, content=""):
        path = os.path.join(self.root, relpath.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _run(self, *extra_args):
        """Run the tool as a subprocess and return (returncode, stdout, stderr)."""
        tool = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tools",
            "test_coverage_gaps.py",
        )
        cmd = [sys.executable, tool, "--root", self.root] + list(extra_args)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout, result.stderr

    # -- 1. basic gap detection --
    def test_detects_uncovered_file(self):
        self._write("tools/foo.py", "# some tool\n")
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)  # report-only, no --check
        self.assertIn("foo.py", out)
        self.assertIn("1 files without tests", out)

    # -- 2. covered file recognised --
    def test_covered_file_not_reported(self):
        self._write("tools/foo.py", "# tool\n")
        self._write("tests/test_foo.py", "# test\n")
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertNotIn("files without tests", out)
        self.assertIn("100.0%", out)

    # -- 3. domain-prefixed test recognised --
    def test_domain_prefixed_test(self):
        self._write("tools/bar.py", "# tool\n")
        self._write("tests/test_tools_bar.py", "# test\n")
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("100.0%", out)

    # -- 4. coverage-ok suppression --
    def test_coverage_ok_suppresses(self):
        self._write("tools/baz.py", "#!/usr/bin/env python3\n# coverage-ok\n")
        rc, out, _ = self._run("--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["suppressed_count"], 1)
        self.assertEqual(data["uncovered_count"], 0)

    # -- 5. --check exits 1 on gaps --
    def test_check_mode_exits_1(self):
        self._write("tools/gap.py", "# no test\n")
        rc, _, _ = self._run("--check")
        self.assertEqual(rc, 1)

    # -- 6. --check exits 0 when clean --
    def test_check_mode_exits_0_clean(self):
        self._write("tools/clean.py", "# has test\n")
        self._write("tests/test_clean.py", "# test\n")
        rc, _, _ = self._run("--check")
        self.assertEqual(rc, 0)

    # -- 7. --threshold fails below --
    def test_threshold_fails_below(self):
        self._write("tools/a.py", "# no test\n")
        self._write("tools/b.py", "# has test\n")
        self._write("tests/test_b.py", "# test\n")
        rc, out, _ = self._run("--threshold", "80")
        self.assertEqual(rc, 1)
        self.assertIn("50.0%", out)

    # -- 8. --threshold passes above --
    def test_threshold_passes_above(self):
        self._write("tools/x.py", "# has test\n")
        self._write("tests/test_x.py", "# test\n")
        rc, out, _ = self._run("--threshold", "80")
        self.assertEqual(rc, 0)
        self.assertIn("100.0%", out)

    # -- 9. --json output structure --
    def test_json_output(self):
        self._write("tools/j.py", "# no test\n")
        self._write("tools/k.py", "# has test\n")
        self._write("tests/test_k.py", "# test\n")
        rc, out, _ = self._run("--json")
        data = json.loads(out)
        self.assertIn("covered", data)
        self.assertIn("uncovered", data)
        self.assertIn("coverage_pct", data)
        self.assertEqual(data["total_source_files"], 2)

    # -- 10. __init__.py and common.py skipped --
    def test_skip_patterns(self):
        self._write("tools/__init__.py", "")
        self._write("tools/common.py", "# shared\n")
        rc, out, _ = self._run("--json")
        data = json.loads(out)
        self.assertEqual(data["total_source_files"], 0)

    # -- 11. multiple source dirs --
    def test_multiple_source_dirs(self):
        self._write("tools/t1.py", "#\n")
        self._write("ui/u1.py", "#\n")
        self._write("driver/d1.py", "#\n")
        self._write("tests/test_t1.py", "#\n")
        rc, out, _ = self._run("--json")
        data = json.loads(out)
        # t1 covered, u1 and d1 uncovered
        self.assertEqual(data["covered_count"], 1)
        self.assertEqual(data["uncovered_count"], 2)

    # -- 12. unknown flag exits 2 --
    def test_unknown_flag_exits_2(self):
        rc, _, err = self._run("--bogus")
        self.assertEqual(rc, 2)
        self.assertIn("unknown flag", err)

    # -- 13. empty project is 100% --
    def test_empty_project(self):
        rc, out, _ = self._run("--json")
        data = json.loads(out)
        self.assertEqual(data["coverage_pct"], 100.0)
        self.assertTrue(data["pass"])


if __name__ == "__main__":
    unittest.main()
