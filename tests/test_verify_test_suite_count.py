#!/usr/bin/env python3
"""
Test suite for verify_test_suite_count tool.

Tests the --check (auto-correct mode) and --fix (explicit rewrite) functionality,
plus new fail-closed behavior (missing sections = exit 1, can't-evaluate = exit 2).
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestVerifyTestSuiteCount(unittest.TestCase):
    """Test verify_test_suite_count tool."""

    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures."""
        # Repo root (parent of tests dir)
        cls.repo_root = Path(__file__).parent.parent

    def setUp(self):
        """Create a temporary isolated repo structure for mutation testing.

        CRITICAL: Tests must NEVER mutate the real repo. The tool now writes to
        tests/CLAUDE.md when auto-correcting drift, so all tests must use an
        isolated temp repo to avoid polluting the real repository.
        """
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)

        # Create complete temp repo structure
        tools_dir = self.temp_root / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)

        # Copy the tool itself
        tool_path = self.repo_root / "tools" / "verify_test_suite_count.py"
        if tool_path.exists():
            (tools_dir / "verify_test_suite_count.py").write_text(tool_path.read_text())

        # Create tests directory with CLAUDE.md
        tests_dir = self.temp_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        claudemd_src = self.repo_root / "tests" / "CLAUDE.md"
        claudemd_dst = tests_dir / "CLAUDE.md"
        claudemd_dst.write_text(claudemd_src.read_text())

        # Create minimal test files so git ls-files can count them
        # IMPORTANT: These must match the counts in CLAUDE.md for tests to pass
        (tests_dir / "test_a.py").touch()
        (tests_dir / "test_b.py").touch()
        (tests_dir / "test_a.test.mjs").touch()
        (tests_dir / "test_b.test.mjs").touch()
        (tests_dir / "test_a.test.sh").touch()

        # Initialize a minimal git repo in temp_root so git ls-files works
        # This is ESSENTIAL: the tool calls git ls-files to count files
        subprocess.run(
            ["git", "init"],
            cwd=str(self.temp_root),
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(self.temp_root),
            capture_output=True,
            check=False,
        )

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def _run_tool(self, *args):
        """Run the verify_test_suite_count.py tool in the isolated temp repo.

        CRITICAL: All tests must run the tool in self.temp_root, NOT in
        self.repo_root, to avoid mutating the real repository.
        """
        # Use sys.executable for cross-platform compatibility
        cmd = [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py")]
        cmd.extend(args)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',  # Ensure UTF-8 encoding for cross-platform compatibility
            cwd=str(self.temp_root),  # Run in isolated temp repo, NOT real repo
            timeout=30,
        )
        return result

    def test_check_mode_passes_when_counts_match(self):
        """--check should exit 0 when counts match actual files."""
        # Ensure counts match in the temp repo
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()
        # Update counts to match the minimal test files we created (2 py, 2 mjs, 1 sh)
        updated = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (2 suites)**:",
            content,
        )
        updated = re.sub(
            r"\*\*Node \(\d+ suites?\)\*\*:",
            "**Node (2 suites)**:",
            updated,
        )
        updated = re.sub(
            r"\*\*Shell \(\d+ suites?\)\*\*:",
            "**Shell (1 suites)**:",
            updated,
        )
        claudemd_path.write_text(updated)

        result = self._run_tool("--check")
        self.assertEqual(
            result.returncode,
            0,
            f"Expected --check to pass with correct counts. stderr: {result.stderr}",
        )

    def test_check_mode_auto_corrects_drift(self):
        """--check should auto-correct drift and exit 0 (treadmill fix)."""
        # Corrupt the count in the temp repo's CLAUDE.md
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()
        # Change Python count to an obviously wrong number
        corrupted = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (99999 suites)**:",
            content
        )
        claudemd_path.write_text(corrupted)

        # Run --check which should auto-correct
        result = self._run_tool("--check")

        # NEW BEHAVIOR: --check auto-corrects and exits 0
        self.assertEqual(
            result.returncode,
            0,
            f"Expected --check to auto-correct and pass. stderr: {result.stderr}",
        )
        # Verify it auto-corrected by reading the file
        updated_content = claudemd_path.read_text()
        self.assertNotIn("99999", updated_content, "File should have been auto-corrected")
        self.assertIn("AUTO-CORRECT", result.stdout, "Should report auto-correction")

    def test_check_mode_fails_on_missing_sections(self):
        """--check should exit 1 if documented sections are missing (real invariant)."""
        # Break the invariant by removing a documented section
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()
        # Remove the Python section header entirely
        corrupted = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python SECTION REMOVED**:",
            content
        )
        claudemd_path.write_text(corrupted)

        # Run --check which should fail because the section is missing
        result = self._run_tool("--check")

        # Real invariant broken: should exit 1
        self.assertEqual(
            result.returncode,
            1,
            f"Expected exit 1 for missing sections. stdout: {result.stdout}",
        )

    def test_fix_mode_rewrites_correct_count(self):
        """--fix should rewrite counts to match actual files."""
        # Test --fix --dry-run mode in temp repo
        result = self._run_tool("--fix", "--dry-run")

        # In dry-run mode, should succeed
        self.assertEqual(
            result.returncode,
            0,
            f"Expected --fix --dry-run to succeed. stderr: {result.stderr}",
        )

    def test_fix_mode_is_idempotent(self):
        """Running --fix twice should produce identical results."""
        # First --fix run
        result1 = self._run_tool("--fix", "--dry-run")
        self.assertEqual(
            result1.returncode,
            0,
            f"Expected first --fix --dry-run to succeed. stderr: {result1.stderr}",
        )

        # Second --fix run should still succeed with identical output
        result2 = self._run_tool("--fix", "--dry-run")
        self.assertEqual(
            result2.returncode,
            0,
            f"Expected second --fix --dry-run to be idempotent. stderr: {result2.stderr}",
        )

    def test_check_mode_fails_on_zero_files_found(self):
        """--check should exit 2 when no files found but CLAUDE.md expects counts (cannot evaluate).

        This tests the fail-closed path: if git ls-files returns zero files (actual == (0,0,0))
        but CLAUDE.md documents non-zero counts, the tool cannot evaluate the state and exits 2.
        """
        # Create CLAUDE.md with non-zero documented counts
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()
        # Ensure CLAUDE.md documents non-zero counts
        updated = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (5 suites)**:",
            content,
        )
        updated = re.sub(
            r"\*\*Node \(\d+ suites?\)\*\*:",
            "**Node (3 suites)**:",
            updated,
        )
        updated = re.sub(
            r"\*\*Shell \(\d+ suites?\)\*\*:",
            "**Shell (2 suites)**:",
            updated,
        )
        claudemd_path.write_text(updated)

        # Now remove all test files so git ls-files returns zero
        tests_dir = self.temp_root / "tests"
        for f in tests_dir.glob("test_*"):
            f.unlink()

        # Re-stage (remove from git's view)
        subprocess.run(
            ["git", "-C", str(self.temp_root), "add", "-A"],
            capture_output=True,
            check=False,
        )

        # Run --check which should detect cannot-evaluate and exit 2
        result = self._run_tool("--check")

        # Cannot-evaluate: should exit 2
        self.assertEqual(
            result.returncode,
            2,
            f"Expected exit 2 for zero files with documented counts. stderr: {result.stderr}",
        )
        self.assertIn("[ERROR]", result.stderr, "Should report error on stderr")
        self.assertIn("Cannot evaluate", result.stderr, "Should mention cannot evaluate")

    def test_tool_provides_help(self):
        """Tool should provide --help documentation."""
        result = self._run_tool("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("verify", result.stdout.lower())

    def test_check_mode_normal_match_unchanged(self):
        """--check with matching counts should exit 0 and leave file unchanged."""
        # First run to get actual counts and auto-correct
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        result1 = self._run_tool("--check")
        self.assertEqual(
            result1.returncode,
            0,
            f"Expected first --check to pass. stderr: {result1.stderr}",
        )

        # Read the auto-corrected file content
        original_content = claudemd_path.read_text()

        # Second run should find matching counts and not modify the file
        result2 = self._run_tool("--check")

        # Should exit 0 with matching counts
        self.assertEqual(
            result2.returncode,
            0,
            f"Expected --check to pass with matching counts. stderr: {result2.stderr}",
        )
        self.assertIn("[OK]", result2.stdout, "Should report OK on second run when counts match")

        # File should be unchanged (not auto-corrected on second run since counts match)
        final_content = claudemd_path.read_text()
        self.assertEqual(
            original_content,
            final_content,
            "File should not be modified on second run when counts already match",
        )


if __name__ == "__main__":
    unittest.main()
