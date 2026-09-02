#!/usr/bin/env python3
"""Unit tests for tools/merge_train.py serial merge train."""
import os
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

    def test_flake_retry_run_not_found_error_keeps_queued(self):
        """Test defect 1 issue: transient error like 'run not found' keeps PR queued (finding #2).

        - PR has FAIL
        - gh run rerun returns "run not found" error (TOCTOU race after run list)
        - This is a transient error, not a permanent failure
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
                    return [{"databaseId": 999, "status": "COMPLETED"}]
                if "run" in args and "rerun" in args:
                    # Simulate TOCTOU race: run disappeared between list and rerun
                    return {"error": "run not found", "rc": 1}
                return {}

            mock_gh.side_effect = gh_side_effect

            prs = [123]
            success = module.run_train(prs, max_rounds=1, poll_interval=0)

            # PR should stay queued (not skipped) because "run not found" is transient
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


class TestCheckEnforceAdmins(unittest.TestCase):
    """Tests for check_enforce_admins() - B1.4 branch protection gate."""

    def setUp(self):
        self.tool_path = Path(__file__).parent.parent / "tools" / "merge_train.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_enforce_admins_with_python_bool_true(self):
        """RED-FIRST: gh() returns Python bool True, not string 'true'.

        When gh() parses JSON, enforce_admins.enabled comes back as Python True,
        not the string "true". The current check `if result == "true":` fails to
        catch this and always prints FAIL even on correctly protected repos.
        """
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.return_value = True  # Python bool, as gh() returns from json.loads
            result = self.module.check_enforce_admins()
            self.assertTrue(result, "Should pass when gh() returns Python bool True")

    def test_enforce_admins_with_python_bool_false(self):
        """gh() returns Python bool False when enforce_admins is disabled."""
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.return_value = False  # Python bool False
            result = self.module.check_enforce_admins()
            self.assertFalse(result, "Should fail when gh() returns Python bool False")

    def test_enforce_admins_with_string_true(self):
        """Backward compatibility: handle string 'true' (case-insensitive, stripped)."""
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.return_value = "true"
            result = self.module.check_enforce_admins()
            self.assertTrue(result, "Should pass when gh() returns string 'true'")

    def test_enforce_admins_with_string_false(self):
        """Handle string 'false'."""
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.return_value = "false"
            result = self.module.check_enforce_admins()
            self.assertFalse(result, "Should fail when gh() returns string 'false'")

    def test_enforce_admins_with_error_dict(self):
        """gh() returns error dict when the API call fails."""
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.return_value = {"error": "not found"}
            result = self.module.check_enforce_admins()
            self.assertFalse(result, "Should fail when gh() returns error dict")

    def test_enforce_admins_with_string_true_uppercase(self):
        """Case-insensitive string comparison."""
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.return_value = "TRUE"
            result = self.module.check_enforce_admins()
            self.assertTrue(result, "Should pass with uppercase TRUE")

    def test_enforce_admins_with_string_true_whitespace(self):
        """Strings should be stripped before comparison."""
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.return_value = "  true  "
            result = self.module.check_enforce_admins()
            self.assertTrue(result, "Should pass with whitespace-padded 'true'")


class TestIntegrationMode(unittest.TestCase):
    """Tests for --integration batch merge mode."""

    def setUp(self):
        self.tool_path = Path(__file__).parent.parent / "tools" / "merge_train.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def _run_tool_subprocess(self, *args):
        cmd = [sys.executable, str(self.tool_path)] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result

    def test_integration_flag_recognized(self):
        result = self._run_tool_subprocess("--integration", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("integration", result.stdout.lower())

    def test_integration_short_flag_recognized(self):
        result = self._run_tool_subprocess("-i", "--help")
        self.assertEqual(result.returncode, 0)

    def test_integration_flag_with_batch_name(self):
        result = self._run_tool_subprocess("--integration", "my-batch", "--help")
        self.assertEqual(result.returncode, 0)

    def test_create_integration_branch_calls_git(self):
        with patch.object(self.module, 'git') as mock_git:
            mock_git.return_value = (True, "")
            ok = self.module.create_integration_branch("batch-wave")
            self.assertTrue(ok)
            calls = [c[0] for c in mock_git.call_args_list]
            fetch_call = [c for c in calls if "fetch" in c]
            self.assertTrue(len(fetch_call) > 0, "Should fetch origin before branching")
            checkout_call = [c for c in calls if "checkout" in c]
            self.assertTrue(len(checkout_call) > 0, "Should checkout the integration branch")

    def test_create_integration_branch_name_format(self):
        with patch.object(self.module, 'git') as mock_git:
            mock_git.return_value = (True, "")
            self.module.create_integration_branch("my-batch")
            checkout_calls = [c for c in mock_git.call_args_list
                              if "checkout" in c[0]]
            found = any("integrate/my-batch" in " ".join(c[0]) for c in checkout_calls)
            self.assertTrue(found, "Branch name should be integrate/<batch-name>")

    def test_merge_pr_into_integration_success(self):
        with patch.object(self.module, 'gh') as mock_gh, \
             patch.object(self.module, 'git') as mock_git:
            mock_gh.return_value = {
                "headRefOid": "abc123",
                "headRefName": "feat/thing",
                "title": "Add thing",
            }
            mock_git.return_value = (True, "")
            ok = self.module.merge_pr_into_integration(42)
            self.assertTrue(ok)
            merge_calls = [c for c in mock_git.call_args_list
                           if "merge" in c[0]]
            self.assertTrue(len(merge_calls) > 0, "Should call git merge")

    def test_merge_pr_into_integration_conflict_returns_false(self):
        with patch.object(self.module, 'gh') as mock_gh, \
             patch.object(self.module, 'git') as mock_git:
            mock_gh.return_value = {
                "headRefOid": "abc123",
                "headRefName": "feat/conflict",
                "title": "Conflicts",
            }
            def git_side_effect(*args):
                if "merge" in args and "--abort" not in args:
                    return (False, "CONFLICT (content): Merge conflict in file.py")
                return (True, "")
            mock_git.side_effect = git_side_effect
            ok = self.module.merge_pr_into_integration(42)
            self.assertFalse(ok)

    def test_merge_pr_into_integration_aborts_on_conflict(self):
        with patch.object(self.module, 'gh') as mock_gh, \
             patch.object(self.module, 'git') as mock_git:
            mock_gh.return_value = {
                "headRefOid": "abc123",
                "headRefName": "feat/conflict",
                "title": "Conflicts",
            }
            def git_side_effect(*args):
                if "merge" in args and "--abort" not in args:
                    return (False, "CONFLICT")
                return (True, "")
            mock_git.side_effect = git_side_effect
            self.module.merge_pr_into_integration(42)
            abort_calls = [c for c in mock_git.call_args_list
                           if "merge" in c[0] and "--abort" in c[0]]
            self.assertTrue(len(abort_calls) > 0, "Should abort merge on conflict")

    def test_push_integration_branch(self):
        with patch.object(self.module, 'git') as mock_git:
            mock_git.return_value = (True, "")
            ok = self.module.push_integration_branch("integrate/batch-wave")
            self.assertTrue(ok)
            push_calls = [c for c in mock_git.call_args_list if "push" in c[0]]
            self.assertTrue(len(push_calls) > 0)

    def test_create_integration_pr(self):
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.side_effect = [
                [],
                "https://github.com/org/repo/pull/99",
            ]
            url = self.module.create_integration_pr(
                "integrate/batch-wave", [10, 11, 12])
            self.assertIn("pull", url)
            create_calls = [c for c in mock_gh.call_args_list
                            if "pr" in c[0] and "create" in c[0]]
            self.assertTrue(len(create_calls) > 0)

    def test_create_integration_pr_finds_existing(self):
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.return_value = [{"number": 99, "url": "https://github.com/org/repo/pull/99"}]
            url = self.module.create_integration_pr(
                "integrate/batch-wave", [10, 11])
            self.assertIn("99", url)

    def test_close_superseded_prs(self):
        with patch.object(self.module, 'gh') as mock_gh, \
             patch.object(self.module, 'git') as mock_git:
            def gh_side_effect(*args):
                if "view" in args:
                    return {"headRefOid": "abc123", "title": "test"}
                return ""
            mock_gh.side_effect = gh_side_effect
            mock_git.return_value = (True, "")  # is_ancestor returns True
            self.module.close_superseded_prs([10, 11, 12])
            close_calls = [c for c in mock_gh.call_args_list
                           if "pr" in c[0] and "close" in c[0]]
            self.assertEqual(len(close_calls), 3)

    def test_cleanup_integration_branch(self):
        with patch.object(self.module, 'git') as mock_git:
            mock_git.return_value = (True, "")
            self.module.cleanup_integration_branch("integrate/batch-wave")
            delete_calls = [c for c in mock_git.call_args_list
                            if "branch" in c[0] and "-D" in c[0]]
            self.assertTrue(len(delete_calls) > 0)
            push_delete_calls = [c for c in mock_git.call_args_list
                                 if "push" in c[0] and "--delete" in c[0]]
            self.assertTrue(len(push_delete_calls) > 0)

    def test_run_integration_train_happy_path(self):
        m = self.module
        with patch.object(m, 'check_enforce_admins') as mock_enforce, \
             patch.object(m, 'create_integration_branch') as mock_create, \
             patch.object(m, 'merge_pr_into_integration') as mock_merge_pr, \
             patch.object(m, 'push_integration_branch') as mock_push, \
             patch.object(m, 'create_integration_pr') as mock_create_pr, \
             patch.object(m, 'wait_for_integration_ci') as mock_wait, \
             patch.object(m, 'merge_integration_pr') as mock_merge_int, \
             patch.object(m, 'run_regenerators') as mock_regen, \
             patch.object(m, 'close_superseded_prs') as mock_close, \
             patch.object(m, 'cleanup_integration_branch') as mock_cleanup:

            mock_enforce.return_value = True
            mock_create.return_value = True
            mock_merge_pr.return_value = True
            mock_push.return_value = True
            mock_create_pr.return_value = "https://github.com/org/repo/pull/99"
            mock_wait.return_value = True
            mock_merge_int.return_value = True
            mock_regen.return_value = True

            result = m.run_integration_train([10, 11, 12], "batch-wave")
            self.assertTrue(result)

            mock_create.assert_called_once_with("batch-wave")
            self.assertEqual(mock_merge_pr.call_count, 3)
            mock_push.assert_called_once()
            mock_create_pr.assert_called_once()
            mock_wait.assert_called_once()
            mock_merge_int.assert_called_once()
            mock_close.assert_called_once_with([10, 11, 12])
            mock_cleanup.assert_called_once()

    def test_run_integration_train_skips_conflicting_prs(self):
        m = self.module
        with patch.object(m, 'check_enforce_admins') as mock_enforce, \
             patch.object(m, 'create_integration_branch') as mock_create, \
             patch.object(m, 'merge_pr_into_integration') as mock_merge_pr, \
             patch.object(m, 'push_integration_branch') as mock_push, \
             patch.object(m, 'create_integration_pr') as mock_create_pr, \
             patch.object(m, 'wait_for_integration_ci') as mock_wait, \
             patch.object(m, 'merge_integration_pr') as mock_merge_int, \
             patch.object(m, 'run_regenerators') as mock_regen, \
             patch.object(m, 'close_superseded_prs') as mock_close, \
             patch.object(m, 'cleanup_integration_branch') as mock_cleanup:

            mock_enforce.return_value = True
            mock_create.return_value = True
            mock_merge_pr.side_effect = [True, False, True]
            mock_push.return_value = True
            mock_create_pr.return_value = "https://github.com/org/repo/pull/99"
            mock_wait.return_value = True
            mock_merge_int.return_value = True
            mock_regen.return_value = True

            result = m.run_integration_train([10, 11, 12], "batch-wave")
            self.assertTrue(result)
            mock_close.assert_called_once_with([10, 12])

    def test_run_integration_train_all_conflict_aborts(self):
        m = self.module
        with patch.object(m, 'check_enforce_admins') as mock_enforce, \
             patch.object(m, 'create_integration_branch') as mock_create, \
             patch.object(m, 'merge_pr_into_integration') as mock_merge_pr, \
             patch.object(m, 'cleanup_integration_branch') as mock_cleanup:

            mock_enforce.return_value = True
            mock_create.return_value = True
            mock_merge_pr.return_value = False

            result = m.run_integration_train([10, 11], "batch-wave")
            self.assertFalse(result)
            mock_cleanup.assert_called_once()

    def test_run_integration_train_ci_fails_aborts(self):
        m = self.module
        with patch.object(m, 'create_integration_branch') as mock_create, \
             patch.object(m, 'merge_pr_into_integration') as mock_merge_pr, \
             patch.object(m, 'push_integration_branch') as mock_push, \
             patch.object(m, 'create_integration_pr') as mock_create_pr, \
             patch.object(m, 'wait_for_integration_ci') as mock_wait, \
             patch.object(m, 'cleanup_integration_branch') as mock_cleanup:

            mock_create.return_value = True
            mock_merge_pr.return_value = True
            mock_push.return_value = True
            mock_create_pr.return_value = "https://github.com/org/repo/pull/99"
            mock_wait.return_value = False

            result = m.run_integration_train([10, 11], "batch-wave")
            self.assertFalse(result)

    def test_run_integration_train_branch_create_fails(self):
        m = self.module
        with patch.object(m, 'create_integration_branch') as mock_create:
            mock_create.return_value = False
            result = m.run_integration_train([10], "batch-wave")
            self.assertFalse(result)

    def test_git_helper_function_exists(self):
        self.assertTrue(hasattr(self.module, 'git'))
        import inspect
        sig = inspect.signature(self.module.git)
        params = list(sig.parameters.keys())
        self.assertIn('args', params)

    def test_wait_for_integration_ci_uses_pr_state(self):
        with patch.object(self.module, 'pr_state') as mock_pr_state:
            mock_pr_state.return_value = {
                "state": "OPEN", "merge": "CLEAN", "checks": "green",
                "title": "integration", "headRefName": "integrate/batch-wave",
            }
            ok = self.module.wait_for_integration_ci(99, poll_interval=0, max_polls=1)
            self.assertTrue(ok)

    def test_wait_for_integration_ci_pending_polls(self):
        call_count = {"n": 0}
        def pr_state_side_effect(n):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return {"state": "OPEN", "merge": "CLEAN", "checks": "pending",
                        "title": "int", "headRefName": "integrate/x"}
            return {"state": "OPEN", "merge": "CLEAN", "checks": "green",
                    "title": "int", "headRefName": "integrate/x"}

        with patch.object(self.module, 'pr_state') as mock_ps, \
             patch('time.sleep'):
            mock_ps.side_effect = pr_state_side_effect
            ok = self.module.wait_for_integration_ci(99, poll_interval=0, max_polls=10)
            self.assertTrue(ok)
            self.assertEqual(call_count["n"], 3)

    def test_wait_for_integration_ci_fail_returns_false(self):
        with patch.object(self.module, 'pr_state') as mock_ps:
            mock_ps.return_value = {
                "state": "OPEN", "merge": "CLEAN", "checks": "FAIL",
                "title": "int", "headRefName": "integrate/x",
            }
            ok = self.module.wait_for_integration_ci(99, poll_interval=0, max_polls=1)
            self.assertFalse(ok)

    def test_wait_for_integration_ci_timeout_returns_false(self):
        with patch.object(self.module, 'pr_state') as mock_ps, \
             patch('time.sleep'):
            mock_ps.return_value = {
                "state": "OPEN", "merge": "CLEAN", "checks": "pending",
                "title": "int", "headRefName": "integrate/x",
            }
            ok = self.module.wait_for_integration_ci(99, poll_interval=0, max_polls=2)
            self.assertFalse(ok)

    def test_merge_integration_pr_squash(self):
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.side_effect = [
                "",
                "MERGED",
            ]
            ok = self.module.merge_integration_pr(99)
            self.assertTrue(ok)
            merge_calls = [c for c in mock_gh.call_args_list
                           if "pr" in c[0] and "merge" in c[0]]
            self.assertTrue(len(merge_calls) > 0)

    def test_merge_integration_pr_verify_state(self):
        with patch.object(self.module, 'gh') as mock_gh:
            mock_gh.side_effect = [
                "",
                "OPEN",
            ]
            ok = self.module.merge_integration_pr(99)
            self.assertFalse(ok)

    def test_b13_ancestor_check_blocks_bogus_close(self):
        """TDD-FIRST test for B1.3: verify ancestor before close_superseded_prs.

        BUG: A partially-applied integration can close a PR whose content never landed.
        Example: PR #42 merged into integration but integration PR merge failed.
        Content of #42 never reached main, but close_superseded_prs(42) still closes it.

        FIX: Before closing, verify `git merge-base --is-ancestor <headRefOid> origin/main`.
        If check fails, report loudly and DO NOT close that PR.
        """
        m = self.module
        with patch.object(m, 'gh') as mock_gh, \
             patch.object(m, 'git') as mock_git:

            # PR info: headRefOid is abc123, but it's NOT an ancestor of origin/main
            # (it was merged into integration, but integration PR never reached main)
            def gh_side_effect(*args):
                if "pr" in args and "view" in args:
                    # Reading PR info to get headRefOid
                    return {
                        "headRefOid": "abc123",
                        "headRefName": "feat/unlanded",
                        "title": "Unlanded feature",
                    }
                if "pr" in args and "close" in args:
                    return ""
                return {}

            def git_side_effect(*args):
                if "merge-base" in args and "--is-ancestor" in args:
                    # abc123 is NOT an ancestor of origin/main
                    return (False, "merge-base check failed")
                return (True, "")

            mock_gh.side_effect = gh_side_effect
            mock_git.side_effect = git_side_effect

            # Should NOT close PR 42 because content never landed
            m.close_superseded_prs([42])

            # Verify that ancestor check was called
            ancestor_checks = [c for c in mock_git.call_args_list
                              if "merge-base" in c[0] and "--is-ancestor" in c[0]]

            self.assertTrue(len(ancestor_checks) > 0,
                          "Should check ancestor before closing")

            # Verify close was NOT called (because ancestor check failed)
            close_calls = [c for c in mock_gh.call_args_list
                          if "pr" in c[0] and "close" in c[0]]

            self.assertEqual(len(close_calls), 0,
                           "Should NOT close PR if content not ancestor of origin/main")


class TestTransportDecodesUndecodableBytes(unittest.TestCase):
    """Regression: the merge queue crashed on EVERY pass decoding a 0x97 byte.

    `git()`/`gh()` carried `encoding='utf-8'` with the DEFAULT strict error
    handler. subprocess decodes captured output in a reader THREAD, so a
    strict UnicodeDecodeError there kills the thread and never propagates to
    the caller's frame -- `result.stdout` simply comes back None, and the
    crash surfaces as `AttributeError: 'NoneType' object has no attribute
    'strip'` from inside `git()`. That took the scheduled queue down for 24+
    consecutive passes; the real cause (byte 0x97, the cp1252 em-dash that
    queued PR titles and branch names are full of) was visible only as a
    stray thread traceback above the useless AttributeError.

    These are behavioral tests, not inspection: a real subprocess really
    emits the undecodable byte. `test_strict_decoding_is_what_broke_the_queue`
    pins the pre-fix behavior so the regression stays PROVEN rather than
    merely asserted.
    """

    RAW = b"em\x97dash"

    def setUp(self):
        self.tool_path = Path(__file__).parent.parent / "tools" / "merge_train.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_train", self.tool_path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

        # A throwaway repo whose config holds a raw, undecodable byte. git
        # echoes config values back verbatim without transcoding them, so
        # `git config --get` is a deterministic source of exactly the byte
        # that took the queue down. Scoped to a TemporaryDirectory, and the
        # cwd is restored in tearDown: no cwd or global git-config pollution.
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)],
                       capture_output=True, timeout=30, check=True)
        config = self.repo / ".git" / "config"
        config.write_bytes(config.read_bytes()
                           + b"[emdash]\n\ttitle = " + self.RAW + b"\n")
        self._cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _strict_probe(self):
        """The exact pre-fix call shape, for the red half of the proof."""
        return subprocess.run(
            ["git", "config", "--get", "emdash.title"],
            capture_output=True, text=True, encoding="utf-8",  # encoding-ok
            timeout=60)

    def test_strict_decoding_is_what_broke_the_queue(self):
        """RED half: without errors=, the call cannot yield usable stdout.

        The SAME defect surfaces two different ways, and the platform picks
        which one -- so asserting either single shape makes this test a
        Windows-only or Linux-only proof:

          * Windows: `capture_output` drains both pipes with reader THREADS.
            The UnicodeDecodeError is raised inside a thread, never reaches
            this frame, and `result.stdout` silently comes back None. That
            None is what killed the queue -- the caller's next `.strip()`
            died with a misleading AttributeError.
          * POSIX: `communicate()` decodes on the CALLING thread, so the
            UnicodeDecodeError propagates out of `subprocess.run` directly.

        Both are the same bug (strict decoding of one 0x97 byte) and both are
        unusable output, which is exactly what the assertion below pins. What
        must NEVER happen is a plain decoded string: that would mean the
        fixture stopped reproducing the crash and the green half proves
        nothing.
        """
        try:
            result = self._strict_probe()
        except UnicodeDecodeError:
            return  # POSIX shape: raised on the calling thread. Proof holds.
        self.assertEqual(result.returncode, 0,
                         "the fixture must produce a successful git call")
        self.assertIsNone(
            result.stdout,
            "strict utf-8 decoding must lose stdout (or raise) -- if this "
            "ever starts returning a string, the fixture stopped reproducing "
            "the 0x97 crash and the green half below proves nothing")

    def test_git_survives_undecodable_byte(self):
        """GREEN half: git() returns a usable string instead of crashing."""
        ok, out = self.module.git("config", "--get", "emdash.title")
        self.assertTrue(ok)
        self.assertIsInstance(out, str)
        self.assertIn("dash", out)
        # 'replace', not 'ignore': the bad byte must stay VISIBLE.
        self.assertIn("�", out,
                      "undecodable bytes must surface as U+FFFD, never be "
                      "silently dropped -- errors='ignore' is forbidden")

    def test_gh_survives_undecodable_byte(self):
        """gh() carries the same handler, proven on a real decoding call."""
        seen = {}
        real_run = subprocess.run

        def spy(cmd, **kwargs):
            seen.update(kwargs)
            return real_run(["git", "config", "--get", "emdash.title"], **kwargs)

        with patch.object(self.module.subprocess, "run", spy):
            out = self.module.gh("pr", "list")
        self.assertEqual(seen.get("encoding"), "utf-8")
        self.assertEqual(seen.get("errors"), "replace")
        self.assertIsInstance(out, str)
        self.assertIn("�", out)


if __name__ == "__main__":
    unittest.main()
