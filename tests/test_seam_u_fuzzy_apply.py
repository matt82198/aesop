#!/usr/bin/env python3
"""
Tests for fuzzy content-based diff application.

Tests that _fuzzy_apply handles:
- Wrong @@ line numbers (haiku vs opus vs sonnet)
- Multiple hunks
- CRLF line endings
- Whitespace differences
- Context-only matching

Real diff shapes from st01 (all produce same change, different line numbers):
- haiku:  @@ -18,7 +18,7 @@ (correct)
- opus:   @@ -28,7 +28,7 @@ (off by ~10)
- sonnet: @@ -24,7 +24,7 @@ (off by ~6)

All change: `  if now - req_time <= self.window_duration` to `  if now - req_time < self.window_duration`
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from bench.run_seam_u import (
    _fuzzy_apply,
    _parse_hunks,
    apply_diff_to_sandbox,
)


class FuzzyHunkParsingTest(unittest.TestCase):
    """Test hunk parsing from unified diffs."""

    def test_parse_simple_hunk(self):
        """Test parsing a simple one-hunk diff."""
        diff = (
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line 1\n"
            "-old line 2\n"
            "+new line 2\n"
            " line 3"  # No trailing newline
        )
        hunks = _parse_hunks(diff)
        self.assertEqual(len(hunks), 1)
        hunk = hunks[0]
        self.assertEqual(hunk['old_start'], 1)
        self.assertEqual(hunk['old_count'], 3)
        self.assertEqual(hunk['new_start'], 1)
        self.assertEqual(hunk['new_count'], 3)
        # Check lines: context, removed, added, context
        self.assertEqual(len(hunk['lines']), 4)

    def test_parse_multi_hunk_diff(self):
        """Test parsing a diff with multiple hunks."""
        diff = (
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line 1\n"
            "-old\n"
            "+new\n"
            " line 3\n"
            "@@ -10,3 +10,3 @@\n"
            " line 10\n"
            "-old\n"
            "+new\n"
            " line 12"  # No trailing newline
        )
        hunks = _parse_hunks(diff)
        self.assertEqual(len(hunks), 2)
        self.assertEqual(hunks[0]['old_start'], 1)
        self.assertEqual(hunks[1]['old_start'], 10)


class FuzzyApplyTest(unittest.TestCase):
    """Test fuzzy diff application with content matching."""

    def test_fuzzy_apply_correct_line_number(self):
        """Test applying diff with correct @@ line number."""
        original = "line 1\nold line 2\nline 3\n"
        diff = (
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line 1\n"
            "-old line 2\n"
            "+new line 2\n"
            " line 3\n"
        )
        result = _fuzzy_apply(original, diff)
        self.assertIsNotNone(result)
        self.assertIn("new line 2", result)
        self.assertNotIn("old line 2", result)

    def test_fuzzy_apply_wrong_line_number(self):
        """Test applying diff with WRONG @@ line number (core fix)."""
        # Simulate what opus/sonnet do: they say the change is at line 28,
        # but it's actually at line 18
        original = (
            "line 0\n" "line 1\n" "line 2\n" "line 3\n" "line 4\n" "line 5\n"
            "line 6\n" "line 7\n" "line 8\n" "line 9\n" "line 10\n" "line 11\n"
            "line 12\n" "line 13\n" "line 14\n" "line 15\n" "line 16\n" "line 17\n"
            "old line\n" "line 18\n" "line 19\n"
        )
        # Diff claims the change is at line 28, but it's really at line 18 (0-indexed = 17)
        # The fuzzy applier should find it by content
        diff = (
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -28,3 +28,3 @@\n"
            " line 17\n"
            "-old line\n"
            "+new line\n"
            " line 18\n"
        )
        result = _fuzzy_apply(original, diff)
        self.assertIsNotNone(result)
        self.assertIn("new line", result)
        self.assertNotIn("old line", result)

    def test_fuzzy_apply_nonexistent_content(self):
        """Test that genuinely wrong diffs are rejected."""
        original = "line 1\nline 2\nline 3\n"
        diff = (
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line 1\n"
            "-nonexistent content\n"
            "+new line\n"
            " line 3\n"
        )
        result = _fuzzy_apply(original, diff)
        # Should fail because the context doesn't exist
        self.assertIsNone(result)

    def test_fuzzy_apply_crlf(self):
        """Test applying diff to CRLF file."""
        original = "line 1\r\nold line\r\nline 3\r\n"
        diff = (
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line 1\n"
            "-old line\n"
            "+new line\n"
            " line 3\n"
        )
        result = _fuzzy_apply(original, diff)
        self.assertIsNotNone(result)
        self.assertIn("new line", result)

    def test_fuzzy_apply_whitespace_variation(self):
        """Test fuzzy apply with trailing whitespace differences."""
        original = "line 1\nold line   \nline 3\n"
        diff = (
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line 1\n"
            "-old line\n"
            "+new line\n"
            " line 3\n"
        )
        result = _fuzzy_apply(original, diff)
        # Should match with whitespace normalization
        self.assertIsNotNone(result)
        self.assertIn("new line", result)


class RealDiffShapesTest(unittest.TestCase):
    """Test with the REAL diff shapes from st01 (haiku, opus, sonnet)."""

    def setUp(self):
        """Load st01 repo."""
        self.st01_dir = (
            Path(__file__).parent.parent / "bench" / "seam_tasks" / "st01" / "repo"
        )

    def test_st01_haiku_shape(self):
        """Test with haiku's diff (correct @@ line)."""
        if not self.st01_dir.exists():
            self.skipTest("st01 task not found")

        rate_limiter = self.st01_dir / "rate_limiter.py"
        if not rate_limiter.exists():
            self.skipTest("rate_limiter.py not found")

        original = rate_limiter.read_text(encoding="utf-8")

        # Haiku's diff (correct line number)
        diff = (
            "--- a/rate_limiter.py\n"
            "+++ b/rate_limiter.py\n"
            "@@ -18,7 +18,7 @@\n"
            "         now = time.time()\n"
            "\n"
            "         # Remove requests outside the window\n"
            "         self.request_times = [\n"
            "             req_time\n"
            "             for req_time in self.request_times\n"
            "-            if now - req_time <= self.window_duration\n"
            "+            if now - req_time < self.window_duration\n"
            "         ]\n"
        )
        result = _fuzzy_apply(original, diff)
        self.assertIsNotNone(result, "Haiku diff should apply")
        self.assertIn("< self.window_duration", result)
        self.assertNotIn("<= self.window_duration", result)

    def test_st01_opus_shape(self):
        """Test with opus's diff (wrong @@ line, off by ~10)."""
        if not self.st01_dir.exists():
            self.skipTest("st01 task not found")

        rate_limiter = self.st01_dir / "rate_limiter.py"
        if not rate_limiter.exists():
            self.skipTest("rate_limiter.py not found")

        original = rate_limiter.read_text(encoding="utf-8")

        # Opus's diff (wrong line number: says -28 instead of -18)
        diff = (
            "--- a/rate_limiter.py\n"
            "+++ b/rate_limiter.py\n"
            "@@ -28,7 +28,7 @@\n"
            "         now = time.time()\n"
            "\n"
            "         # Remove requests outside the window\n"
            "         self.request_times = [\n"
            "             req_time\n"
            "             for req_time in self.request_times\n"
            "-            if now - req_time <= self.window_duration\n"
            "+            if now - req_time < self.window_duration\n"
            "         ]\n"
        )
        result = _fuzzy_apply(original, diff)
        self.assertIsNotNone(result, "Opus diff should apply via fuzzy matching")
        self.assertIn("< self.window_duration", result)
        self.assertNotIn("<= self.window_duration", result)

    def test_st01_sonnet_shape(self):
        """Test with sonnet's diff (wrong @@ line, off by ~6)."""
        if not self.st01_dir.exists():
            self.skipTest("st01 task not found")

        rate_limiter = self.st01_dir / "rate_limiter.py"
        if not rate_limiter.exists():
            self.skipTest("rate_limiter.py not found")

        original = rate_limiter.read_text(encoding="utf-8")

        # Sonnet's diff (wrong line number: says -24 instead of -18)
        diff = (
            "--- a/rate_limiter.py\n"
            "+++ b/rate_limiter.py\n"
            "@@ -24,7 +24,7 @@\n"
            "         now = time.time()\n"
            "\n"
            "         # Remove requests outside the window\n"
            "         self.request_times = [\n"
            "             req_time\n"
            "             for req_time in self.request_times\n"
            "-            if now - req_time <= self.window_duration\n"
            "+            if now - req_time < self.window_duration\n"
            "         ]\n"
        )
        result = _fuzzy_apply(original, diff)
        self.assertIsNotNone(result, "Sonnet diff should apply via fuzzy matching")
        self.assertIn("< self.window_duration", result)
        self.assertNotIn("<= self.window_duration", result)


class SandboxFuzzyApplyTest(unittest.TestCase):
    """Test apply_diff_to_sandbox with fuzzy applier."""

    def setUp(self):
        """Set up test fixtures."""
        self.tmpdir = tempfile.mkdtemp()
        self.st01_dir = (
            Path(__file__).parent.parent / "bench" / "seam_tasks" / "st01"
        )

    def tearDown(self):
        """Clean up."""
        if Path(self.tmpdir).exists():
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_apply_diff_with_wrong_line_number(self):
        """Test that apply_diff_to_sandbox handles wrong @@ line numbers."""
        if not self.st01_dir.exists():
            self.skipTest("st01 task not found")

        # Opus's diff (wrong line number)
        diff = (
            "--- a/rate_limiter.py\n"
            "+++ b/rate_limiter.py\n"
            "@@ -28,7 +28,7 @@\n"
            "         now = time.time()\n"
            "\n"
            "         # Remove requests outside the window\n"
            "         self.request_times = [\n"
            "             req_time\n"
            "             for req_time in self.request_times\n"
            "-            if now - req_time <= self.window_duration\n"
            "+            if now - req_time < self.window_duration\n"
            "         ]\n"
        )

        sandbox = Path(self.tmpdir) / "sandbox"
        status = apply_diff_to_sandbox(self.st01_dir / "repo", diff, sandbox)

        self.assertEqual(status, "applied")

        # Verify the file was actually changed correctly
        patched = (sandbox / "repo" / "rate_limiter.py").read_text()
        self.assertIn("< self.window_duration", patched)
        self.assertNotIn("<= self.window_duration", patched)

    def test_fuzzy_applied_file_is_correct(self):
        """Test that file content is correct after fuzzy-applied patch."""
        if not self.st01_dir.exists():
            self.skipTest("st01 task not found")

        # Opus's diff (wrong line number, uses fuzzy apply)
        diff = (
            "--- a/rate_limiter.py\n"
            "+++ b/rate_limiter.py\n"
            "@@ -28,7 +28,7 @@\n"
            "         now = time.time()\n"
            " \n"
            "         # Remove requests outside the window\n"
            "         self.request_times = [\n"
            "             req_time\n"
            "             for req_time in self.request_times\n"
            "-            if now - req_time <= self.window_duration\n"
            "+            if now - req_time < self.window_duration\n"
            "         ]\n"
        )

        sandbox = Path(self.tmpdir) / "sandbox"
        apply_status = apply_diff_to_sandbox(self.st01_dir / "repo", diff, sandbox)
        self.assertEqual(apply_status, "applied")

        # Verify file content is correct
        patched = (sandbox / "repo" / "rate_limiter.py").read_text()
        self.assertIn("< self.window_duration", patched)
        self.assertNotIn("<= self.window_duration", patched)


class LiveOpusTest(unittest.TestCase):
    """LIVE TEST: Run opus on st01, verify fuzzy apply works."""

    def setUp(self):
        """Set up test fixtures."""
        self.tmpdir = tempfile.mkdtemp()
        self.api_key = os.environ.get("BENCH_API_KEY")
        self.st01_dir = (
            Path(__file__).parent.parent / "bench" / "seam_tasks" / "st01"
        )

    def tearDown(self):
        """Clean up."""
        if Path(self.tmpdir).exists():
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_opus_st01_apply_and_oracle(self):
        """LIVE TEST: Run opus on st01, apply patch, run oracle."""
        if not self.api_key:
            self.skipTest(
                "BENCH_API_KEY environment variable not set; cannot test live API calls"
            )

        if not self.st01_dir.exists():
            self.skipTest("st01 task not found")

        from bench.run_seam_u import (
            build_u_arm_prompt,
            create_anthropic_http_runner,
            apply_diff_to_sandbox,
            run_oracle,
        )

        task_json = json.loads((self.st01_dir / "task.json").read_text())
        prompt = build_u_arm_prompt(task_json, self.st01_dir)

        # Call opus
        runner = create_anthropic_http_runner(
            self.api_key, "claude-opus-5", probe=False
        )
        try:
            response, usage = runner(prompt)
        except RuntimeError as e:
            error_str = str(e).lower()
            if "401" in error_str or "403" in error_str or "unauthorized" in error_str or "forbidden" in error_str:
                self.skipTest(
                    "API authentication failed (401/403); BENCH_API_KEY may be invalid or expired"
                )
            elif "refused" in error_str:
                self.skipTest(f"opus refused: {e}")
            else:
                self.fail(f"opus call failed: {e}")

        self.assertIsNotNone(response, "Opus should return a patch")
        self.assertGreater(len(response), 10)

        # Apply patch (should work even if line number is wrong)
        sandbox = Path(self.tmpdir) / "sandbox"
        apply_status = apply_diff_to_sandbox(self.st01_dir / "repo", response, sandbox)

        print(f"\n=== Opus Patch ===\n{response}\n=== Apply Status: {apply_status} ===\n")

        # Patch should apply (via fuzzy if needed)
        self.assertEqual(
            apply_status,
            "applied",
            f"Opus patch should apply (fuzzy fallback handles line number issues); got {apply_status}",
        )

        # Oracle should pass
        oracle_result = run_oracle(task_json, self.st01_dir, sandbox, timeout=30)
        self.assertTrue(oracle_result, "Oracle should pass after opus patch")


if __name__ == "__main__":
    unittest.main()


class FuzzyApplyTrailingContextRegression(unittest.TestCase):
    """Regression for the opus/sonnet bug: a hunk with context lines AFTER the
    change must replace the old block in place, NOT duplicate the trailing
    context (which produced 'SyntaxError: unmatched ]' and a false 'applied')."""

    def test_trailing_context_not_duplicated(self):
        from bench.run_seam_u import _fuzzy_apply
        original = (
            "        self.request_times = [\n"
            "            req_time\n"
            "            for req_time in self.request_times\n"
            "            if now - req_time <= self.window_duration\n"
            "        ]\n"
            "\n"
            "        # Check if we can allow this request\n"
            "        return True\n"
        )
        # Wrong @@ start line + trailing context after the changed line.
        # Context/added/removed lines carry the real unified-diff prefix
        # (leading space for context, - / + for changes).
        diff = (
            "--- a/rate_limiter.py\n"
            "+++ b/rate_limiter.py\n"
            "@@ -99,7 +99,7 @@\n"
            "         self.request_times = [\n"
            "             req_time\n"
            "             for req_time in self.request_times\n"
            "-            if now - req_time <= self.window_duration\n"
            "+            if now - req_time < self.window_duration\n"
            "         ]\n"
            " \n"
            "         # Check if we can allow this request\n"
        )
        result = _fuzzy_apply(original, diff)
        self.assertIsNotNone(result)
        # The fix landed...
        self.assertIn("req_time < self.window_duration", result)
        self.assertNotIn("<= self.window_duration", result)
        # ...and the trailing context was NOT duplicated.
        self.assertEqual(result.count("        ]"), 1)
        self.assertEqual(result.count("# Check if we can allow this request"), 1)


if __name__ == "__main__":
    unittest.main()


class FuzzyApplyMultiFileRegression(unittest.TestCase):
    """Regression: a multi-file diff with wrong @@ line numbers must apply to
    each file (previously the fuzzy fallback handled only the first file, and
    git apply rejects the whole diff when any file's line numbers are off)."""

    def test_multifile_split(self):
        from bench.run_seam_u import _split_diff_by_file
        diff = (
            "--- a/one.py\n+++ b/one.py\n@@ -1,1 +1,1 @@\n-a\n+A\n"
            "--- a/two.py\n+++ b/two.py\n@@ -1,1 +1,1 @@\n-b\n+B\n"
        )
        segs = _split_diff_by_file(diff)
        self.assertEqual([p for p, _ in segs], ["one.py", "two.py"])
        # a removed line beginning with dashes must NOT be treated as a header
        self.assertEqual(len(segs), 2)

    def test_removed_line_starting_with_dashes_not_a_header(self):
        from bench.run_seam_u import _split_diff_by_file
        diff = ("--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n"
                "---- decorative comment\n+# real comment\n z\n")
        segs = _split_diff_by_file(diff)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0][0], "f.py")


if __name__ == "__main__":
    unittest.main()
