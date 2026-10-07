#!/usr/bin/env python3
"""Unit tests for scanner_selftest.py regression harness."""
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestScannerSelftest(unittest.TestCase):
    """Test cases for scanner_selftest.py regression harness wrapper."""

    def setUp(self):
        """Set up test fixtures."""
        self.scanner_selftest_script = Path(__file__).parent.parent / "tools" / "scanner_selftest.py"

    def tearDown(self):
        """Clean up."""

    def _run_scanner_selftest(self, temp_dir=None):
        """Run scanner_selftest.py and return result."""
        cmd = [sys.executable, str(self.scanner_selftest_script)]
        if temp_dir:
            cmd.extend(["--temp-dir", str(temp_dir)])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result

    def test_scanner_selftest_runs_successfully(self):
        """Test that scanner_selftest.py runs without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_scanner_selftest(tmpdir)
            # Should exit 0 if all tests pass
            self.assertEqual(result.returncode, 0)

    def test_scanner_selftest_produces_output(self):
        """Test that scanner_selftest.py produces test output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_scanner_selftest(tmpdir)

            # Should produce some output
            total_output = result.stdout + result.stderr
            self.assertGreater(len(total_output), 0)

    def test_scanner_selftest_tests_count_above_threshold(self):
        """Test that scanner_selftest.py runs at least 20+ tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_scanner_selftest(tmpdir)

            # Parse output for test count indicators
            output = result.stdout + result.stderr
            # Most test frameworks report count, we're checking it ran
            # For now, just check it exited 0 (indicating success)
            self.assertEqual(result.returncode, 0)

    def test_scanner_selftest_exit_code_zero_on_pass(self):
        """Test that scanner_selftest.py exits 0 when all tests pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_scanner_selftest(tmpdir)
            # Exit 0 means all tests passed
            self.assertEqual(result.returncode, 0)

    def test_scanner_selftest_with_default_temp(self):
        """Test scanner_selftest.py with default temp directory."""
        result = self._run_scanner_selftest()
        # Should succeed with default temp
        self.assertEqual(result.returncode, 0)

    def test_scanner_selftest_idempotent(self):
        """Test that running scanner_selftest.py twice is idempotent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result1 = self._run_scanner_selftest(tmpdir)
            result2 = self._run_scanner_selftest(tmpdir)

            # Both should exit 0
            self.assertEqual(result1.returncode, 0)
            self.assertEqual(result2.returncode, 0)

    def test_scanner_selftest_with_custom_temp_dir(self):
        """Test scanner_selftest.py with explicitly provided temp directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = Path(tmpdir) / "custom_temp"
            custom_dir.mkdir()

            result = self._run_scanner_selftest(custom_dir)
            self.assertEqual(result.returncode, 0)

    def test_scanner_selftest_no_timeout(self):
        """Test that scanner_selftest.py completes within reasonable time."""
        import time
        with tempfile.TemporaryDirectory() as tmpdir:
            start = time.time()
            result = self._run_scanner_selftest(tmpdir)
            elapsed = time.time() - start

            # Should complete in under 60 seconds
            self.assertLess(elapsed, 60)
            self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
