#!/usr/bin/env python3
"""Behavioral tests for halt kill-switch enforcement in merge_train.py.

Tests verify that merge_train.py gates entry points and merge actions with halt checks,
and refuses to proceed when halt is set. Uses NON-DEFAULT state_root to prove behavioral
isolation (not just mocking).

Reference: Audit finding P0 #1 — direct CLI usage of merge_train.py bypassed halt guard
that only existed in shell daemon wrapper (daemons/run-merge-queue.sh).
"""
import sys
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import os


class TestMergeTrainHaltEnforcement(unittest.TestCase):
    """Test halt kill-switch enforcement in merge_train.py."""

    def setUp(self):
        """Set up test fixtures."""
        self.tool_path = Path(__file__).parent.parent / "tools" / "merge_train.py"
        self.halt_path = Path(__file__).parent.parent / "tools" / "halt.py"

    def _run_tool_subprocess(self, *args, state_root=None, expect_rc=None):
        """Run merge_train.py as subprocess with optional state_root.

        Args:
            *args: Arguments to pass to merge_train.py
            state_root: Optional state root directory (sets AESOP_STATE_ROOT env var)
            expect_rc: If set, assert returncode equals this value

        Returns:
            (returncode, stdout, stderr) tuple
        """
        cmd = [sys.executable, str(self.tool_path)] + list(args)
        env = os.environ.copy()
        if state_root:
            env["AESOP_STATE_ROOT"] = str(state_root)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=env)
        if expect_rc is not None:
            self.assertEqual(result.returncode, expect_rc,
                           f"Expected rc={expect_rc}, got {result.returncode}\n"
                           f"stdout: {result.stdout}\nstderr: {result.stderr}")
        return (result.returncode, result.stdout, result.stderr)

    def test_halt_import_available(self):
        """Test that halt module is available (FAIL CLOSED).

        If halt.py exists and imports correctly, merge_train should not fail at startup.
        """
        self.assertTrue(self.halt_path.exists(), "halt.py must exist")
        # Try to import halt to verify it's valid
        import importlib.util
        spec = importlib.util.spec_from_file_location("halt", self.halt_path)
        halt_module = importlib.util.module_from_spec(spec)
        # This should not raise
        spec.loader.exec_module(halt_module)

    def test_help_works(self):
        """Test that --help still works (baseline)."""
        rc, stdout, stderr = self._run_tool_subprocess("--help")
        self.assertEqual(rc, 0)
        self.assertIn("merge_train", stdout.lower())

    def test_no_prs_error_baseline(self):
        """Test that tool errors without PR numbers (baseline, no halt)."""
        rc, stdout, stderr = self._run_tool_subprocess()
        self.assertNotEqual(rc, 0, "Should error without PR numbers")

    def test_halt_set_refuses_entry_serial_mode(self):
        """Test (LEG 1a): halt set via NON-DEFAULT state_root → serial merge refuses.

        Setup:
          1. Create temp state dir with .HALT sentinel
          2. Run merge_train with dummy PR, passing state_root via AESOP_STATE_ROOT
          3. Assert tool exits 1 immediately (before attempting any merge)
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            # Set halt via the halt.py API
            import importlib.util
            spec = importlib.util.spec_from_file_location("halt", self.halt_path)
            halt_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(halt_module)

            # Write halt sentinel to our test state root
            halt_module.halt("test halt for serial mode", state_dir=state_root)

            # Now try to run merge_train with halt set
            rc, stdout, stderr = self._run_tool_subprocess("123", state_root=state_root)
            self.assertEqual(rc, 1, "Should exit 1 when halt is set")
            self.assertIn("HALTED", stderr, "Should print halt reason to stderr")
            self.assertIn("test halt for serial mode", stderr,
                         "Should include the halt reason")

    def test_halt_set_refuses_entry_integration_mode(self):
        """Test (LEG 1b): halt set via NON-DEFAULT state_root → integration merge refuses.

        Setup:
          1. Create temp state dir with .HALT sentinel
          2. Run merge_train -i with dummy PRs, passing state_root via AESOP_STATE_ROOT
          3. Assert tool exits 1 immediately
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            import importlib.util
            spec = importlib.util.spec_from_file_location("halt", self.halt_path)
            halt_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(halt_module)

            halt_module.halt("test halt for integration mode", state_dir=state_root)

            rc, stdout, stderr = self._run_tool_subprocess("-i", "batch", "123", "124",
                                                          state_root=state_root)
            self.assertEqual(rc, 1, "Should exit 1 when halt is set")
            self.assertIn("HALTED", stderr)
            self.assertIn("test halt for integration mode", stderr)

    def test_halt_cleared_allows_continued_operation(self):
        """Test (LEG 2): clearing halt restores merge operation readiness.

        Setup:
          1. Create temp state dir
          2. Set halt
          3. Verify merge_train refuses (from leg 1)
          4. Clear halt via halt.py API
          5. Verify merge_train no longer exits on halt check (may fail for other reasons,
             but not due to halt)
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            import importlib.util
            spec = importlib.util.spec_from_file_location("halt", self.halt_path)
            halt_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(halt_module)

            # Set halt
            halt_module.halt("test halt", state_dir=state_root)
            rc1, _, stderr1 = self._run_tool_subprocess("123", state_root=state_root)
            self.assertEqual(rc1, 1, "Should refuse when halted")
            self.assertIn("HALTED", stderr1)

            # Clear halt
            cleared = halt_module.clear_halt(state_dir=state_root)
            self.assertTrue(cleared, "Halt should have been cleared")

            # Verify not halted
            is_halted = halt_module.is_halted(state_dir=state_root)
            self.assertFalse(is_halted, "Halt should be cleared")

            # Run merge_train again - should NOT exit due to halt
            # (will likely fail due to no gh, but NOT due to halt)
            rc2, _, stderr2 = self._run_tool_subprocess("123", state_root=state_root)
            # Exit code may vary (depends on gh availability), but should NOT have
            # "HALTED" in stderr
            self.assertNotIn("HALTED", stderr2,
                           "Should not refuse after halt is cleared")

    def test_halt_info_logged_includes_reason_and_timestamp(self):
        """Test that halt info (reason + timestamp) is logged on halt refusal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            import importlib.util
            spec = importlib.util.spec_from_file_location("halt", self.halt_path)
            halt_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(halt_module)

            reason = "cost ceiling exceeded"
            halt_module.halt(reason, state_dir=state_root)

            rc, _, stderr = self._run_tool_subprocess("123", state_root=state_root)
            self.assertEqual(rc, 1)
            self.assertIn(reason, stderr, "Reason should be in stderr")
            # Timestamp should be present (ISO 8601 format with Z or offset)
            self.assertIn("202", stderr,  # Year 2020+
                         "Timestamp should be in stderr")

    def test_halt_module_import_failure_exit_code(self):
        """Test (LEG 3): if halt module is unimportable, merge_train exits 2 (not 1).

        This tests the FAIL CLOSED behavior: import failure should be fatal (exit 2),
        not continue or silently ignore.

        This is a structural test: we verify merge_train has proper import guards
        by reading the code, not by actually breaking the import at runtime (which
        would require modifying sys.modules or the import path).
        """
        # Read merge_train.py to verify it has proper import guards
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify that halt module is imported with try/except
        self.assertIn("try:", content, "Should have try/except for halt import")
        self.assertIn("from halt import", content, "Should import from halt")
        self.assertIn("sys.exit(2)", content,
                     "Should exit 2 on import failure (FAIL CLOSED)")

    def test_halt_re_check_before_serial_merge(self):
        """Test (LEG 4a): halt is re-checked before merge_pr() in serial mode.

        This is a code-structure test that verifies the check is in place.
        A full end-to-end test would require mocking gh to get a PR to CLEAN+green state,
        which is complex. Instead, we verify the code includes the check.
        """
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify _check_halt is called before merge_pr
        # Look for the pattern: _check_halt before merge_pr(n)
        lines = content.split('\n')
        merge_pr_line = None
        for i, line in enumerate(lines):
            if 'if merge_pr(n):' in line or 'merge_pr(n)' in line:
                # Check that _check_halt appears in previous lines
                context = '\n'.join(lines[max(0, i-5):i+1])
                if '_check_halt' in context:
                    merge_pr_line = i
                    break

        self.assertIsNotNone(merge_pr_line,
                           "Should have _check_halt before merge_pr call")

    def test_halt_re_check_before_integration_merge(self):
        """Test (LEG 4b): halt is re-checked before merge_integration_pr().

        Structural test: verify the check is in place in the code.
        """
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify _check_halt is called before merge_integration_pr
        lines = content.split('\n')
        merge_integration_line = None
        for i, line in enumerate(lines):
            if 'merge_integration_pr(pr_number)' in line:
                context = '\n'.join(lines[max(0, i-5):i+1])
                if '_check_halt' in context:
                    merge_integration_line = i
                    break

        self.assertIsNotNone(merge_integration_line,
                           "Should have _check_halt before merge_integration_pr call")

    def test_halt_entry_check_serial_mode(self):
        """Test (LEG 4c): halt is checked at run_train() entry."""
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify _check_halt is called inside run_train
        lines = content.split('\n')
        run_train_found = False
        for i, line in enumerate(lines):
            if 'def run_train(' in line:
                # Look for _check_halt in the next 10 lines
                context = '\n'.join(lines[i:i+10])
                if '_check_halt' in context:
                    run_train_found = True
                    break

        self.assertTrue(run_train_found,
                       "Should have _check_halt early in run_train()")

    def test_halt_entry_check_integration_mode(self):
        """Test (LEG 4d): halt is checked at run_integration_train() entry."""
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        lines = content.split('\n')
        run_integration_found = False
        for i, line in enumerate(lines):
            if 'def run_integration_train(' in line:
                context = '\n'.join(lines[i:i+10])
                if '_check_halt' in context:
                    run_integration_found = True
                    break

        self.assertTrue(run_integration_found,
                       "Should have _check_halt early in run_integration_train()")

    def test_halt_check_function_uses_get_halt_info(self):
        """Test that _check_halt uses get_halt_info for reason and timestamp."""
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify _check_halt function uses get_halt_info
        self.assertIn("def _check_halt(", content)
        self.assertIn("get_halt_info()", content)
        self.assertIn('"reason"', content)
        self.assertIn('"timestamp"', content)

    def test_halt_exit_code_is_1(self):
        """Test that halt refusal exits with code 1 (not 0 or 2)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            import importlib.util
            spec = importlib.util.spec_from_file_location("halt", self.halt_path)
            halt_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(halt_module)

            halt_module.halt("test", state_dir=state_root)
            rc, _, _ = self._run_tool_subprocess("123", state_root=state_root)
            self.assertEqual(rc, 1, "Halt refusal must exit 1 (not 0 or 2)")


if __name__ == "__main__":
    unittest.main()
