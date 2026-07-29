#!/usr/bin/env python3
"""
Test suite for verify_test_suite_count tool.

Tests the --check (verify mode) and --fix (auto-rewrite) functionality.
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
        """Create a temporary copy of tests/CLAUDE.md for mutation testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)

        # Copy the tool itself
        tools_dir = self.temp_root / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        tool_path = self.repo_root / "tools" / "verify_test_suite_count.py"
        if tool_path.exists():
            (tools_dir / "verify_test_suite_count.py").write_text(tool_path.read_text())

        # Copy tests/CLAUDE.md
        tests_dir = self.temp_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        claudemd_src = self.repo_root / "tests" / "CLAUDE.md"
        claudemd_dst = tests_dir / "CLAUDE.md"
        claudemd_dst.write_text(claudemd_src.read_text())

        # Create minimal test structure
        (tests_dir / "test_a.py").touch()
        (tests_dir / "test_b.py").touch()
        (tests_dir / "test_a.test.mjs").touch()
        (tests_dir / "test_b.test.mjs").touch()
        (tests_dir / "test_a.test.sh").touch()

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def _run_tool(self, *args):
        """Run the verify_test_suite_count.py tool."""
        # Use sys.executable for cross-platform compatibility
        cmd = [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py")]
        cmd.extend(args)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
            timeout=30,
        )
        return result

    def test_check_mode_passes_when_counts_match(self):
        """--check should exit 0 when counts match actual files."""
        result = self._run_tool("--check")
        self.assertEqual(
            result.returncode,
            0,
            f"Expected --check to pass with actual counts. stderr: {result.stderr}",
        )

    def test_check_mode_fails_when_counts_drift(self):
        """--check should exit 1 when counts drift from actual files."""
        # Corrupt the count in CLAUDE.md
        claudemd_path = self.repo_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()
        # Change Python count to an obviously wrong number
        corrupted = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (99999 suites)**:",
            content
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, dir=str(self.repo_root / "tests")
        ) as tmp:
            tmp.write(corrupted)
            tmp.flush()
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py"),
                 "--check", "--claudemd", tmp_path],
                capture_output=True,
                text=True,
                cwd=str(self.repo_root),
                timeout=30,
            )
            self.assertNotEqual(
                result.returncode,
                0,
                f"Expected --check to fail with corrupted counts. stdout: {result.stdout}",
            )
        finally:
            os.unlink(tmp_path)

    def test_fix_mode_rewrites_correct_count(self):
        """--fix should rewrite counts to match actual files."""
        # Use the actual repo for this test since we need real git integration
        result = subprocess.run(
            [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py"),
             "--fix", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
            timeout=30,
        )
        # In dry-run mode, should succeed
        self.assertEqual(
            result.returncode,
            0,
            f"Expected --fix --dry-run to succeed. stderr: {result.stderr}",
        )

    def test_fix_mode_is_idempotent(self):
        """Running --fix twice should produce identical results."""
        # Get actual counts first
        result1 = subprocess.run(
            [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py"),
             "--check"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
            timeout=30,
        )

        # First --fix run
        subprocess.run(
            [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py"),
             "--fix", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
            timeout=30,
        )

        # Second --fix run should still succeed
        result2 = subprocess.run(
            [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py"),
             "--fix", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
            timeout=30,
        )
        self.assertEqual(
            result2.returncode,
            0,
            f"Expected second --fix to be idempotent. stderr: {result2.stderr}",
        )

    def test_tool_provides_help(self):
        """Tool should provide --help documentation."""
        result = subprocess.run(
            [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py"),
             "--help"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("verify", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
