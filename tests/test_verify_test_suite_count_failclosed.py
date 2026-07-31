#!/usr/bin/env python3
"""
Regression test: verify_test_suite_count.py fails closed when git is unavailable.

Tests that the tool exits with non-zero (2) when git fails, never silently
returning 0 as a count.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestVerifySuiteCountFailClosed(unittest.TestCase):
    """Regression tests for fail-closed behavior in verify_test_suite_count.py."""

    def test_git_not_found_fails_closed(self):
        """Verify that missing git causes exit code 2 (error), not 0."""
        # Create a temporary CLAUDE.md
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()

            claudemd_path = tests_dir / "CLAUDE.md"
            claudemd_path.write_text(
                "**Shell (13 suites)**:\n"
                "**Node (25 suites)**:\n"
                "**Python (216 suites)**:\n",
                encoding="utf-8"
            )

            # Mock subprocess.run to simulate git not found
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError("git not found")

                # Import and run the verify script
                import sys
                import os
                sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

                from verify_test_suite_count import check_mode

                # Should fail with exit code 2, not 0
                exit_code = check_mode(claudemd_path)
                self.assertEqual(exit_code, 2, "Expected exit code 2 (error) when git is not found")

    def test_git_failure_fails_closed(self):
        """Verify that git command failure causes exit code 2 (error), not 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()

            claudemd_path = tests_dir / "CLAUDE.md"
            claudemd_path.write_text(
                "**Shell (13 suites)**:\n"
                "**Node (25 suites)**:\n"
                "**Python (216 suites)**:\n",
                encoding="utf-8"
            )

            # Mock subprocess.run to simulate git command failure
            with patch("subprocess.run") as mock_run:
                # Simulate a failed git ls-files call
                mock_run.return_value = MagicMock(
                    returncode=128,
                    stderr="fatal: not a git repository",
                    stdout=""
                )

                import sys
                sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

                from verify_test_suite_count import check_mode

                # Should fail with exit code 2, not 0
                exit_code = check_mode(claudemd_path)
                self.assertEqual(exit_code, 2, "Expected exit code 2 (error) when git fails")

    def test_real_git_returns_valid_count(self):
        """Verify that in a real git repo, counts are computed correctly."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

        # This test runs in the real aesop repo (current working directory)
        # It should find the actual test files
        from verify_test_suite_count import count_git_files

        # Count actual test files using git (should not raise)
        try:
            node_count = count_git_files("tests/*.test.mjs")
            shell_count = count_git_files("tests/*.test.sh", "tests/test_*.sh", "tests/test-*.sh")
            python_count = count_git_files("tests/test_*.py")

            # Verify counts are reasonable (not zero in this repo)
            self.assertGreater(node_count, 0, "Should find Node test files")
            self.assertGreater(shell_count, 0, "Should find Shell test files")
            self.assertGreater(python_count, 0, "Should find Python test files")

            # Verify the total matches documented counts
            total = node_count + shell_count + python_count
            self.assertEqual(
                total, 254,
                f"Total test count should be 254 (Node {node_count} + Shell {shell_count} + Python {python_count} = {total})"
            )
        except Exception as e:
            self.fail(f"count_git_files should not raise in a real repo: {e}")

    def test_no_zero_silently_returned(self):
        """Verify that count_git_files never silently returns 0 on failure."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

        from verify_test_suite_count import count_git_files

        # When git fails, it should raise ValueError, not return 0
        with patch("subprocess.run") as mock_run:
            # Simulate git failure
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr="error",
                stdout=""
            )

            # Should raise ValueError, not return 0
            with self.assertRaises(ValueError):
                count_git_files("tests/*.test.mjs")


if __name__ == "__main__":
    unittest.main()
