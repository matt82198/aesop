#!/usr/bin/env python3
"""Fail-closed check-state classification for tools/merge_train.py (Q0 defect 2).

Recorded lesson: "exit 0 != merged; gate must fail-closed on CANCELLED/unknown states".
The pre-fix pr_state() computed:

    pending = status != "COMPLETED"
    failing = status == "COMPLETED" and state == "FAILURE"

Two independent fail-open holes:

  1. A CANCELLED / TIMED_OUT / ACTION_REQUIRED check IS "COMPLETED" and is NOT
     "FAILURE", so it fell through to `green` and the PR was merged.
  2. `gh pr view --json statusCheckRollup` returns CheckRun entries carrying
     `conclusion` (SUCCESS/FAILURE/CANCELLED/...), NOT `state`. Only legacy
     StatusContext entries carry `state`. So `failing` was structurally always 0
     for every GitHub Actions check — even a hard FAILURE read as green.

The contract these tests pin: only SUCCESS / NEUTRAL / SKIPPED terminal outcomes
count as green. Everything else — including values this file does not enumerate —
is NOT green. tools/auto_merge.py already buckets this way; this mirrors it.
"""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


TOOL_PATH = Path(__file__).resolve().parent.parent / "tools" / "merge_train.py"


def _load_merge_train():
    """Load tools/merge_train.py as an isolated module object."""
    spec = importlib.util.spec_from_file_location("merge_train_failclosed", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_run(conclusion, status="COMPLETED", name="ci (0)"):
    """Build a CheckRun-shaped statusCheckRollup entry (what Actions returns)."""
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "workflowName": "CI",
    }


def _status_context(state, context="legacy/status"):
    """Build a StatusContext-shaped entry (legacy commit status)."""
    return {"__typename": "StatusContext", "context": context, "state": state}


class PRStateFailClosedTest(unittest.TestCase):
    """pr_state() must classify non-SUCCESS terminal outcomes as NOT green."""

    def setUp(self):
        self.module = _load_merge_train()

    def _checks(self, rollup):
        """Run pr_state(1) against a mocked gh returning `rollup`."""
        payload = {
            "state": "OPEN",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": rollup,
            "title": "fixture PR",
            "headRefName": "feat/fixture",
        }
        with patch.object(self.module, "gh", return_value=payload):
            return self.module.pr_state(1)["checks"]

    # --- the green set -----------------------------------------------------

    def test_success_is_green(self):
        self.assertEqual(self._checks([_check_run("SUCCESS")]), "green")

    def test_neutral_is_green(self):
        self.assertEqual(self._checks([_check_run("NEUTRAL")]), "green")

    def test_skipped_is_green(self):
        """Skipped non-required jobs (docs-only scoping, PR #694) stay green."""
        self.assertEqual(self._checks([_check_run("SKIPPED")]), "green")

    def test_all_green_conclusions_mixed(self):
        self.assertEqual(
            self._checks([
                _check_run("SUCCESS", name="ci (0)"),
                _check_run("SKIPPED", name="windows-shard"),
                _check_run("NEUTRAL", name="browser-proofs"),
            ]),
            "green",
        )

    # --- the fail-closed set ----------------------------------------------

    def test_cancelled_is_not_green(self):
        """The recorded lesson: CANCELLED must never fall through to green."""
        self.assertNotEqual(self._checks([_check_run("CANCELLED")]), "green")
        self.assertEqual(self._checks([_check_run("CANCELLED")]), "FAIL")

    def test_timed_out_is_not_green(self):
        self.assertEqual(self._checks([_check_run("TIMED_OUT")]), "FAIL")

    def test_action_required_is_not_green(self):
        self.assertEqual(self._checks([_check_run("ACTION_REQUIRED")]), "FAIL")

    def test_stale_is_not_green(self):
        self.assertEqual(self._checks([_check_run("STALE")]), "FAIL")

    def test_startup_failure_is_not_green(self):
        self.assertEqual(self._checks([_check_run("STARTUP_FAILURE")]), "FAIL")

    def test_failure_is_not_green(self):
        """Regression for hole 2: CheckRun FAILURE lives in `conclusion`, not `state`."""
        self.assertEqual(self._checks([_check_run("FAILURE")]), "FAIL")

    def test_unknown_conclusion_is_not_green(self):
        """Fail-closed on values GitHub may add later."""
        self.assertEqual(self._checks([_check_run("SOME_FUTURE_OUTCOME")]), "FAIL")

    def test_null_conclusion_completed_is_not_green(self):
        self.assertEqual(self._checks([_check_run(None)]), "FAIL")

    def test_missing_conclusion_key_is_not_green(self):
        entry = {"__typename": "CheckRun", "name": "ci (0)", "status": "COMPLETED"}
        self.assertNotEqual(self._checks([entry]), "green")

    def test_one_cancelled_among_successes_is_not_green(self):
        """A single CANCELLED check poisons the whole rollup."""
        rollup = [
            _check_run("SUCCESS", name="ci (0)"),
            _check_run("SUCCESS", name="ci (1)"),
            _check_run("CANCELLED", name="ci (2)"),
            _check_run("SUCCESS", name="ci (3)"),
        ]
        self.assertEqual(self._checks(rollup), "FAIL")

    # --- pending / empty ---------------------------------------------------

    def test_in_progress_is_pending(self):
        self.assertEqual(
            self._checks([_check_run(None, status="IN_PROGRESS")]), "pending"
        )

    def test_queued_is_pending(self):
        self.assertEqual(self._checks([_check_run(None, status="QUEUED")]), "pending")

    def test_pending_wins_over_cancelled(self):
        """Still-running checks mean 'wait', which is also not green."""
        rollup = [_check_run("CANCELLED"), _check_run(None, status="IN_PROGRESS")]
        self.assertNotEqual(self._checks(rollup), "green")

    def test_no_checks_is_none_not_green(self):
        self.assertEqual(self._checks([]), "none")

    def test_null_rollup_is_none_not_green(self):
        self.assertEqual(self._checks(None), "none")

    # --- legacy StatusContext entries -------------------------------------

    def test_status_context_success_is_green(self):
        self.assertEqual(self._checks([_status_context("SUCCESS")]), "green")

    def test_status_context_failure_is_not_green(self):
        self.assertEqual(self._checks([_status_context("FAILURE")]), "FAIL")

    def test_status_context_error_is_not_green(self):
        self.assertEqual(self._checks([_status_context("ERROR")]), "FAIL")

    def test_status_context_pending_is_pending(self):
        self.assertEqual(self._checks([_status_context("PENDING")]), "pending")

    # --- unknown shapes ----------------------------------------------------

    def test_unrecognised_entry_shape_is_not_green(self):
        """An entry we cannot classify must never be counted as green."""
        self.assertNotEqual(self._checks([{"name": "mystery"}]), "green")


class GreenConclusionSetTest(unittest.TestCase):
    """The allow-list must stay an allow-list (never a deny-list)."""

    def setUp(self):
        self.module = _load_merge_train()

    def test_green_conclusions_constant_exists(self):
        self.assertTrue(
            hasattr(self.module, "GREEN_CONCLUSIONS"),
            "merge_train must expose an explicit GREEN_CONCLUSIONS allow-list",
        )

    def test_green_conclusions_is_exactly_the_safe_set(self):
        self.assertEqual(
            set(self.module.GREEN_CONCLUSIONS),
            {"SUCCESS", "NEUTRAL", "SKIPPED"},
            "Widening the green set re-opens the fail-open merge path",
        )

    def test_hazard_conclusions_absent_from_green_set(self):
        for bad in ("CANCELLED", "TIMED_OUT", "ACTION_REQUIRED",
                    "FAILURE", "STALE", "STARTUP_FAILURE"):
            self.assertNotIn(bad, self.module.GREEN_CONCLUSIONS)


class SourceRegressionTest(unittest.TestCase):
    """Pin the source so the old fail-open predicate cannot be reintroduced."""

    def test_old_fail_open_predicate_is_gone(self):
        source = TOOL_PATH.read_text(encoding="utf-8")
        self.assertFalse(
            'c.get("state") == "FAILURE"' in source,
            "merge_train.py still contains the predicate `c.get(\"state\") == \"FAILURE\"`. "
            "CheckRun entries carry `conclusion`, not `state` — this predicate never "
            "fires for GitHub Actions checks (fail-open).",
        )


if __name__ == "__main__":
    unittest.main()
