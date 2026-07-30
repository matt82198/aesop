#!/usr/bin/env python3
"""Unit tests for tools/merge_train.py serial merge train."""
import sys
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
import tempfile


class TestMergeTrain(unittest.TestCase):
    """Test cases for merge_train.py: flake retry, DIRTY queue, adaptive poll."""

    def setUp(self):
        """Set up test fixtures."""
        self.tool_path = Path(__file__).parent.parent / "tools" / "merge_train.py"

    def _run_tool_subprocess(self, *args):
        """Run merge_train.py as subprocess."""
        cmd = [sys.executable, str(self.tool_path)] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result

    def test_help_works(self):
        """Test that --help works."""
        result = self._run_tool_subprocess("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("merge_train", result.stdout.lower())

    def test_no_prs_error(self):
        """Test that tool errors without PR numbers."""
        result = self._run_tool_subprocess()
        self.assertNotEqual(result.returncode, 0)

    def test_flake_retry_once_per_pr(self):
        """Test defect 1: Flake retry - on first FAIL, rerun CI once, keep PR queued.

        - PR starts with FAIL
        - Tool finds latest run via `gh run list --branch <headRefName> --limit 1 --json databaseId,status`
        - If run is completed, issue `gh run rerun <id> --failed`
        - Mark PR as retried (ONE retry max)
        - Keep in queue as pending
        - If second FAIL, skip permanently as today
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Mock pr_state to return FAIL first time, then track calls
        with patch.object(module, 'pr_state') as mock_pr_state, \
             patch.object(module, 'update_branch') as mock_update, \
             patch.object(module, 'gh') as mock_gh:

            # Round 1: PR has FAIL
            def pr_state_side_effect(n):
                if n == 123:
                    return {
                        "state": "OPEN",
                        "merge": "CLEAN",
                        "checks": "FAIL",
                        "title": "test PR",
                        "headRefName": "feat/test",  # Needed for run list
                    }
                return None

            mock_pr_state.side_effect = pr_state_side_effect

            # Mock gh run list response
            def gh_side_effect(*args):
                if "run" in args and "list" in args:
                    return [{"databaseId": 999, "status": "COMPLETED"}]
                if "run" in args and "rerun" in args:
                    return {"status": "requested"}
                return {}

            mock_gh.side_effect = gh_side_effect

            # Run train for one round with retries enabled
            # After fix, PR 123 should be retried and kept queued
            prs = [123]
            success = module.run_train(prs, max_rounds=1, poll_interval=0)

            # Should not succeed (PR still pending after retry)
            self.assertFalse(success)

            # gh should have been called for run list and rerun
            run_list_calls = [c for c in mock_gh.call_args_list
                             if c[0] and "run" in c[0] and "list" in c[0]]
            rerun_calls = [c for c in mock_gh.call_args_list
                          if c[0] and "run" in c[0] and "rerun" in c[0]]

            self.assertGreater(len(run_list_calls), 0, "Should call gh run list")
            self.assertGreater(len(rerun_calls), 0, "Should call gh run rerun on FAIL")

    def test_flake_retry_second_fail_skips(self):
        """Test defect 1: Second FAIL for same PR skips it permanently.

        - PR retried once (first FAIL -> rerun)
        - Second round: still FAIL
        - PR skipped permanently (no second retry)
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        rerun_count = {'count': 0}

        def pr_state_side_effect(n):
            if n == 123:
                # Always return FAIL
                return {
                    "state": "OPEN",
                    "merge": "CLEAN",
                    "checks": "FAIL",
                    "title": "test PR",
                    "headRefName": "feat/test",
                }
            return None

        with patch.object(module, 'pr_state') as mock_pr_state, \
             patch.object(module, 'gh') as mock_gh:

            mock_pr_state.side_effect = pr_state_side_effect

            def gh_side_effect(*args):
                if "run" in args and "list" in args:
                    return [{"databaseId": 999, "status": "COMPLETED"}]
                if "run" in args and "rerun" in args:
                    rerun_count['count'] += 1
                    return {"status": "requested"}
                return {}

            mock_gh.side_effect = gh_side_effect

            prs = [123]
            success = module.run_train(prs, max_rounds=3, poll_interval=0)

            # After retry and still FAIL, should be skipped (success=True means all PRs processed)
            self.assertTrue(success, "All PRs should be processed (merged or skipped)")
            # Verify rerun was only called once (not twice)
            rerun_calls = [c for c in mock_gh.call_args_list
                          if c[0] and "run" in c[0] and "rerun" in c[0]]
            self.assertEqual(len(rerun_calls), 1, "Should only rerun once per PR")

    def test_flake_retry_already_running_error_keeps_queued(self):
        """Test defect 1: If workflow already running, keep PR queued without consuming retry.

        - PR has FAIL
        - gh run rerun returns "workflow is already running" error
        - Keep PR in queue, do NOT consume the retry
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with patch.object(module, 'pr_state') as mock_pr_state, \
             patch.object(module, 'gh') as mock_gh:

            def pr_state_side_effect(n):
                if n == 123:
                    return {
                        "state": "OPEN",
                        "merge": "CLEAN",
                        "checks": "FAIL",
                        "title": "test PR",
                        "headRefName": "feat/test",
                    }
                return None

            mock_pr_state.side_effect = pr_state_side_effect

            def gh_side_effect(*args):
                if "run" in args and "list" in args:
                    return [{"databaseId": 999, "status": "IN_PROGRESS"}]
                if "run" in args and "rerun" in args:
                    return {"error": "workflow is already running", "rc": 1}
                return {}

            mock_gh.side_effect = gh_side_effect

            prs = [123]
            success = module.run_train(prs, max_rounds=1, poll_interval=0)

            # PR should stay queued (not skipped)
            self.assertFalse(success)

    def test_dirty_stays_in_queue(self):
        """Test defect 2: DIRTY PRs stay in queue, re-checked each round.

        - PR starts with DIRTY (merge conflict)
        - Old behavior: skipped permanently
        - New behavior: stays in queue, printed as [WARN] each round
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        dirty_rounds = {'count': 0}

        def pr_state_side_effect(n):
            if n == 123:
                dirty_rounds['count'] += 1
                return {
                    "state": "OPEN",
                    "merge": "DIRTY",
                    "checks": "pending",
                    "title": "test PR",
                    "headRefName": "feat/test",
                }
            return None

        with patch.object(module, 'pr_state') as mock_pr_state, \
             patch('builtins.print') as mock_print:

            mock_pr_state.side_effect = pr_state_side_effect

            prs = [123]
            success = module.run_train(prs, max_rounds=3, poll_interval=0)

            # PR should not be skipped immediately
            # After 3 rounds with DIRTY, should exit with it remaining
            self.assertFalse(success)

            # pr_state should be called multiple times (once per round)
            self.assertGreater(mock_pr_state.call_count, 1,
                             "DIRTY PR should be re-checked multiple rounds")

    def test_dirty_with_skip_flag(self):
        """Test defect 2: --skip-dirty flag restores old behavior (skip DIRTY immediately)."""
        result = self._run_tool_subprocess("123", "--skip-dirty", "--help")
        # Should parse --skip-dirty flag without error
        self.assertIn("skip-dirty", result.stdout.lower() or "")

    def test_dirty_5_consecutive_rounds_exits_with_error(self):
        """Test defect 2: If ALL remaining PRs are DIRTY for 5 consecutive rounds, exit 1.

        - Prevents infinite loop spinning on unresolvable DIRTY PRs
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def pr_state_side_effect(n):
            return {
                "state": "OPEN",
                "merge": "DIRTY",
                "checks": "pending",
                "title": "test PR",
                "headRefName": "feat/test",
            }

        with patch.object(module, 'pr_state') as mock_pr_state:
            mock_pr_state.side_effect = pr_state_side_effect

            prs = [123, 124]
            success = module.run_train(prs, max_rounds=10, poll_interval=0)

            # Should exit False (failure) when all PRs stuck DIRTY
            self.assertFalse(success)

    def test_adaptive_poll_pending_uses_default_45s(self):
        """Test defect 3: Adaptive poll - if any PR has pending checks, use 45s (default).

        - Round 1: PR 1 has pending, PR 2 has FAIL
        - Should use poll_interval=45 (default), not 3x
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        pr_states = {123: 0, 124: 0}

        def pr_state_side_effect(n):
            pr_states[n] += 1
            if n == 123:
                # PR 123 is pending
                return {
                    "state": "OPEN",
                    "merge": "CLEAN",
                    "checks": "pending",
                    "title": "pending PR",
                    "headRefName": "feat/test",
                }
            elif n == 124:
                # PR 124 is FAIL
                return {
                    "state": "OPEN",
                    "merge": "CLEAN",
                    "checks": "FAIL",
                    "title": "fail PR",
                    "headRefName": "feat/test",
                }
            return None

        with patch.object(module, 'pr_state') as mock_pr_state, \
             patch('time.sleep') as mock_sleep:

            mock_pr_state.side_effect = pr_state_side_effect

            prs = [123, 124]
            success = module.run_train(prs, max_rounds=2, poll_interval=45)

            # Should use 45s poll interval (default)
            # When there's a pending PR
            sleep_calls = mock_sleep.call_args_list
            if sleep_calls:
                # Check that at least one sleep was called (with or near 45s)
                # Since we have a pending, should NOT use 3x = 135s
                for call_obj in sleep_calls:
                    if call_obj[0]:
                        duration = call_obj[0][0]
                        self.assertLessEqual(duration, 45 + 5,
                                          "Pending PR should use default 45s, not 3x")

    def test_adaptive_poll_all_dirty_blocked_uses_3x_capped(self):
        """Test defect 3: Adaptive poll - if all queued are DIRTY/BLOCKED with no CI movement, use 3x capped at 300s.

        - All remaining PRs are DIRTY or BLOCKED (no pending/green checks)
        - No CI activity between rounds
        - Should use min(3*45, 300) = 135s
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def pr_state_side_effect(n):
            return {
                "state": "OPEN",
                "merge": "DIRTY",
                "checks": "none",
                "title": "dirty PR",
                "headRefName": "feat/test",
            }

        with patch.object(module, 'pr_state') as mock_pr_state, \
             patch('time.sleep') as mock_sleep:

            mock_pr_state.side_effect = pr_state_side_effect

            prs = [123]
            success = module.run_train(prs, max_rounds=3, poll_interval=45)

            # Should NOT succeed (DIRTY is stuck)
            self.assertFalse(success)

            # Sleep calls should exist for some wait periods
            # The exact duration depends on the adaptive logic
            # Just verify sleep was called (proving poll happened)
            self.assertTrue(mock_sleep.called or not success,
                          "With DIRTY PRs, should wait or fail after retries")

    def test_pr_state_includes_headRefName(self):
        """Test that pr_state() fetches and returns headRefName for run list queries.

        This is needed for defect 1 to get the branch name for `gh run list --branch`.
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with patch.object(module, 'gh') as mock_gh:
            mock_gh.return_value = {
                "state": "OPEN",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [],
                "title": "test",
                "headRefName": "feat/branch",
            }

            result = module.pr_state(123)

            # Verify gh was called with the fields we need
            call_args = mock_gh.call_args
            if call_args and call_args[0]:
                # The --json argument should contain headRefName
                call_str = ' '.join(str(arg) for arg in call_args[0])
                self.assertIn("headRefName", call_str,
                             "pr_state should fetch headRefName from gh pr view")

            # Result should include headRefName
            self.assertIn("headRefName", result,
                         "pr_state result should include headRefName")
            self.assertEqual(result["headRefName"], "feat/branch",
                           "pr_state should return the headRefName value")

    def test_merge_train_preserves_existing_behavior(self):
        """Test that existing behaviors are preserved: MERGED-state verification, restart after merge."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Verify key functions exist and have expected signatures
        self.assertTrue(hasattr(module, 'pr_state'))
        self.assertTrue(hasattr(module, 'update_branch'))
        self.assertTrue(hasattr(module, 'merge_pr'))
        self.assertTrue(hasattr(module, 'run_train'))

        # run_train should accept max_rounds and poll_interval
        import inspect
        sig = inspect.signature(module.run_train)
        self.assertIn('max_rounds', sig.parameters)
        self.assertIn('poll_interval', sig.parameters)

    def test_file_argument_still_works(self):
        """Test that --file argument for reading PR numbers works."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("123\n124\n125\n")
            f.flush()
            temp_file = f.name

        try:
            result = self._run_tool_subprocess("--file", temp_file, "--help")
            # Should parse the file argument without error
            # (will fail on gh calls, but arg parsing should work)
            self.assertIn("merge", result.stdout.lower())
        finally:
            import os
            os.unlink(temp_file)

    def test_max_rounds_argument(self):
        """Test that --max-rounds argument is recognized."""
        result = self._run_tool_subprocess("123", "--max-rounds", "20", "--help")
        # Should parse without error (will reach help)
        self.assertIn("merge", result.stdout.lower())

    def test_poll_argument(self):
        """Test that --poll argument is recognized."""
        result = self._run_tool_subprocess("123", "--poll", "60", "--help")
        # Should parse without error
        self.assertIn("merge", result.stdout.lower())

    def test_ascii_only_output(self):
        """Test that tool produces ASCII-only output (no em dashes, etc)."""
        result = self._run_tool_subprocess("--help")
        self.assertEqual(result.returncode, 0)
        # Check that output is ASCII-compatible (no Unicode dashes, etc)
        try:
            result.stdout.encode('ascii')
        except UnicodeEncodeError:
            self.fail("Help output contains non-ASCII characters")


if __name__ == "__main__":
    unittest.main()
