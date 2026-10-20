#!/usr/bin/env python3
"""Tests for tools/reconcile_log.py: lossless union reconcile of diverged logs.

Each test drives main() on real temp files and asserts observable output:
exit code, file content, and whether anything was written.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import reconcile_log  # noqa: E402


class ReconcileLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data):
        """Write bytes to a temp file and return its path as a string."""
        path = self.dir / name
        path.write_bytes(data)
        return str(path)

    def read_bytes(self, name):
        return (self.dir / name).read_bytes()

    def run_main(self, argv):
        """Run main(argv) and return (exit_code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = reconcile_log.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_interior_insertions_on_both_sides_are_all_kept(self):
        base = self.write("base.md", b"h\na\nb\nc\n")
        ours = self.write("ours.md", b"h\na\nX\nb\nc\n")
        theirs = self.write("theirs.md", b"h\na\nb\nY\nc\nZ\n")
        out = str(self.dir / "out.md")
        code, _, _ = self.run_main(["--base", base, "--ours", ours, "--theirs", theirs, "--out", out])
        self.assertEqual(code, 0)
        lines = self.read_bytes("out.md").decode("utf-8").split("\n")
        for line in ("h", "a", "X", "b", "Y", "c", "Z"):
            self.assertEqual(lines.count(line), 1, line)

    def test_one_sided_deletion_does_not_drop_the_line(self):
        base = self.write("base.md", b"h\na\nb\nc\n")
        ours = self.write("ours.md", b"h\na\nc\n")
        theirs = self.write("theirs.md", b"h\na\nb\nc\n")
        out = str(self.dir / "out.md")
        code, _, _ = self.run_main(["--base", base, "--ours", ours, "--theirs", theirs, "--out", out])
        self.assertEqual(code, 0)
        lines = self.read_bytes("out.md").decode("utf-8").split("\n")
        self.assertIn("b", lines)
        self.assertEqual(lines.count("b"), 1)

    def test_crlf_and_lf_copies_of_same_content_do_not_duplicate(self):
        ours = self.write("ours.md", b"a\r\nb\r\n")
        theirs = self.write("theirs.md", b"a\nb\n")
        out = str(self.dir / "out.md")
        code, _, _ = self.run_main(["--ours", ours, "--theirs", theirs, "--out", out])
        self.assertEqual(code, 0)
        self.assertEqual(self.read_bytes("out.md"), b"a\nb\n")

    def test_existing_out_is_refused_and_left_untouched(self):
        ours = self.write("ours.md", b"a\n")
        theirs = self.write("theirs.md", b"b\n")
        out = self.write("out.md", b"keep me\n")
        code, _, err = self.run_main(["--ours", ours, "--theirs", theirs, "--out", out])
        self.assertEqual(code, 2)
        self.assertIn("refusing to overwrite", err)
        self.assertEqual(self.read_bytes("out.md"), b"keep me\n")

    def test_out_equal_to_an_input_is_refused_and_input_untouched(self):
        ours = self.write("ours.md", b"a\nb\n")
        theirs = self.write("theirs.md", b"a\nc\n")
        code, _, err = self.run_main(["--ours", ours, "--theirs", theirs, "--out", ours])
        self.assertEqual(code, 2)
        self.assertIn("must not be an input", err)
        self.assertEqual(self.read_bytes("ours.md"), b"a\nb\n")

    def test_invariant_violation_exits_2_and_writes_nothing(self):
        ours = self.write("ours.md", b"a\nb\n")
        theirs = self.write("theirs.md", b"a\nb\nc\n")
        out = str(self.dir / "out.md")

        def drops_theirs_tail(_ours, _theirs):
            return list(_ours)

        with mock.patch.object(reconcile_log, "merge_lines", drops_theirs_tail):
            code, _, err = self.run_main(["--ours", ours, "--theirs", theirs, "--out", out])
        self.assertEqual(code, 2)
        self.assertIn("invariant violated", err)
        self.assertFalse(os.path.exists(out))

    def test_summary_line_reports_counts_and_kept_all(self):
        ours = self.write("ours.md", b"a\nb\n")
        theirs = self.write("theirs.md", b"a\nb\nc\n")
        out = str(self.dir / "out.md")
        code, stdout, _ = self.run_main(["--ours", ours, "--theirs", theirs, "--out", out])
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "RECONCILE: ours=2 theirs=3 out=3 kept-all=yes\n")

    def test_inputs_are_never_written(self):
        base = self.write("base.md", b"h\na\n")
        ours = self.write("ours.md", b"h\na\nX\n")
        theirs = self.write("theirs.md", b"h\nY\na\n")
        before = [self.read_bytes(n) for n in ("base.md", "ours.md", "theirs.md")]
        out = str(self.dir / "out.md")
        code, _, _ = self.run_main(["--base", base, "--ours", ours, "--theirs", theirs, "--out", out])
        self.assertEqual(code, 0)
        after = [self.read_bytes(n) for n in ("base.md", "ours.md", "theirs.md")]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
