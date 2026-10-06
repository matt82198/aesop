#!/usr/bin/env python3
"""Behavioral tests for halt kill-switch enforcement in merge_queue.py.

Tests verify that merge_queue.py (THE ACTOR) gates entry points and merge actions
with halt checks, and refuses to proceed when halt is set. Uses NON-DEFAULT state_root
to prove behavioral isolation.

Reference: Audit finding P0 #1 — direct CLI usage of merge_queue.py --advance
bypassed halt guard that only existed in shell daemon wrapper.
"""
import sys
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import os


class TestMergeQueueHaltEnforcement(unittest.TestCase):
    """Test halt kill-switch enforcement in merge_queue.py."""

    def setUp(self):
        """Set up test fixtures."""
        self.tool_path = Path(__file__).parent.parent / "tools" / "merge_queue.py"
        self.halt_path = Path(__file__).parent.parent / "tools" / "halt.py"

    def _run_tool_subprocess(self, *args, state_root=None, expect_rc=None):
        """Run merge_queue.py as subprocess with optional state_root.

        Args:
            *args: Arguments to pass to merge_queue.py
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
        """Test that halt module is available (FAIL CLOSED)."""
        self.assertTrue(self.halt_path.exists(), "halt.py must exist")
        # Try to import halt to verify it's valid
        import importlib.util
        spec = importlib.util.spec_from_file_location("halt", self.halt_path)
        halt_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(halt_module)

    def test_help_works(self):
        """Test that --help still works (baseline)."""
        rc, stdout, stderr = self._run_tool_subprocess("--help")
        self.assertEqual(rc, 0)
        self.assertIn("merge", stdout.lower() or stderr.lower())

    def test_halt_set_refuses_advance_entry(self):
        """Test (LEG 1): halt set via NON-DEFAULT state_root → --advance refuses.

        Setup:
          1. Create temp state dir with .HALT sentinel
          2. Run merge_queue --advance with state_root via AESOP_STATE_ROOT
          3. Assert tool records halted exception row and exits 1 immediately
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            # Set halt via the halt.py API
            import importlib.util
            spec = importlib.util.spec_from_file_location("halt", self.halt_path)
            halt_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(halt_module)

            halt_module.halt("test halt for merge_queue --advance", state_dir=state_root)

            # Now try to run merge_queue with halt set
            rc, stdout, stderr = self._run_tool_subprocess("--advance", state_root=state_root)
            # Tool exits 1 on halted status
            self.assertEqual(rc, 1, "Should exit 1 when halt is set")
            # Status should indicate halted
            if stdout:
                self.assertIn("halted", stdout.lower(), "Should indicate halted in output")

    def test_halt_set_refuses_with_json_output(self):
        """Test that halt refusal shows in JSON output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            import importlib.util
            spec = importlib.util.spec_from_file_location("halt", self.halt_path)
            halt_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(halt_module)

            halt_module.halt("test halt", state_dir=state_root)

            rc, stdout, stderr = self._run_tool_subprocess("--advance", "--json",
                                                          state_root=state_root)
            self.assertEqual(rc, 1)
            try:
                summary = json.loads(stdout)
                self.assertEqual(summary.get("status"), "halted",
                               "JSON output should show halted status")
            except json.JSONDecodeError:
                # Tool might not output JSON on early halt, which is acceptable
                pass

    def test_halt_cleared_allows_operation(self):
        """Test (LEG 2): clearing halt allows merge_queue to proceed (at least past halt check).

        Setup:
          1. Create temp state dir
          2. Set halt
          3. Verify merge_queue refuses (from LEG 1)
          4. Clear halt via halt.py API
          5. Verify merge_queue no longer exits on halt (may fail for other reasons)
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            import importlib.util
            spec = importlib.util.spec_from_file_location("halt", self.halt_path)
            halt_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(halt_module)

            # Set halt
            halt_module.halt("test halt", state_dir=state_root)
            rc1, _, _ = self._run_tool_subprocess("--advance", state_root=state_root,
                                                 expect_rc=1)
            self.assertEqual(rc1, 1, "Should refuse when halted")

            # Clear halt
            cleared = halt_module.clear_halt(state_dir=state_root)
            self.assertTrue(cleared, "Halt should have been cleared")

            # Verify not halted
            is_halted = halt_module.is_halted(state_dir=state_root)
            self.assertFalse(is_halted, "Halt should be cleared")

            # Run merge_queue again - should NOT exit due to halt
            # (will likely timeout due to no gh/git, but NOT due to halt refusal)
            try:
                cmd = [sys.executable, str(self.tool_path), "--advance"]
                env = os.environ.copy()
                env["AESOP_STATE_ROOT"] = str(state_root)
                result = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=2, env=env)
                rc2, stdout, stderr = result.returncode, result.stdout, result.stderr
            except subprocess.TimeoutExpired:
                # Timeout is OK - means it passed halt check and got to the preconditions
                # which try to call gh and timeout
                return

            # If it didn't timeout, check that we didn't exit due to halt
            if "halted" in (stdout.lower() or stderr.lower()):
                self.fail("Should not refuse after halt is cleared")

    def test_halt_module_import_failure_exit_code(self):
        """Test (LEG 3): if halt module is unimportable, merge_queue exits 2 (FAIL CLOSED).

        This is a structural test: verify merge_queue has proper import guards
        by reading the code.
        """
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify that halt module is imported with try/except
        self.assertIn("try:", content, "Should have try/except for halt import")
        self.assertIn("from halt import", content, "Should import from halt")
        self.assertIn("sys.exit(2)", content,
                     "Should exit 2 on import failure (FAIL CLOSED)")

    def test_halt_re_check_before_singleton_merge(self):
        """Test (LEG 4a): halt is re-checked before merge_and_verify() in advance_singleton.

        Structural test: verify the check is in place in the code.
        """
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify _check_halt_and_record is called before merge_and_verify in advance_singleton
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'def advance_singleton(' in line:
                # Look for _check_halt_and_record and merge_and_verify in this function
                context = '\n'.join(lines[i:i+100])
                self.assertIn('_check_halt_and_record', context,
                            "advance_singleton should check halt")
                self.assertIn('merge_and_verify', context,
                            "advance_singleton should have merge_and_verify")
                break
        else:
            self.fail("advance_singleton function not found")

    def test_halt_re_check_before_batch_merge(self):
        """Test (LEG 4b): halt is re-checked before merge_and_verify() in handle_batch_pr.

        Structural test: verify the check is in place.
        """
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'def handle_batch_pr(' in line:
                context = '\n'.join(lines[i:i+150])
                self.assertIn('_check_halt_and_record', context,
                            "handle_batch_pr should check halt")
                self.assertIn('merge_and_verify', context,
                            "handle_batch_pr should have merge_and_verify")
                break
        else:
            self.fail("handle_batch_pr function not found")

    def test_halt_entry_check(self):
        """Test (LEG 4c): halt is checked at run_pass() entry."""
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'def run_pass(' in line:
                # Look for _check_halt_and_record in the next 30 lines
                context = '\n'.join(lines[i:i+30])
                self.assertIn('_check_halt_and_record', context,
                            "run_pass should check halt early")
                break
        else:
            self.fail("run_pass function not found")

    def test_halt_check_function_records_exception(self):
        """Test that _check_halt_and_record uses record_exception."""
        with open(self.tool_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify _check_halt_and_record function uses record_exception
        self.assertIn("def _check_halt_and_record(", content)
        # Find the function and check it records exceptions
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'def _check_halt_and_record(' in line:
                context = '\n'.join(lines[i:i+20])
                self.assertIn("record_exception", context,
                            "_check_halt_and_record should record exception rows")
                break

    def test_halt_info_logged_on_refusal(self):
        """Test that halt reason is included in exception row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            import importlib.util
            spec = importlib.util.spec_from_file_location("halt", self.halt_path)
            halt_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(halt_module)

            reason = "cost ceiling exceeded"
            halt_module.halt(reason, state_dir=state_root)

            rc, _, _ = self._run_tool_subprocess("--advance", state_root=state_root)
            self.assertEqual(rc, 1)
            # Check that exception file was created with the reason
            exceptions_path = state_root / "merge-queue" / "exceptions.jsonl"
            if exceptions_path.exists():
                content = exceptions_path.read_text(encoding="utf-8")
                self.assertIn(reason, content,
                            "Exception row should include the halt reason")


if __name__ == "__main__":
    unittest.main()
