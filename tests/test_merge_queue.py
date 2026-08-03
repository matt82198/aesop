#!/usr/bin/env python3
"""Unit tests for tools/merge_queue.py -- the deterministic merge-queue advancer.

Every test is hermetic: no network, no gh, no git. `gh`, `git` and `is_ancestor`
are module globals in merge_queue precisely so they can be patched here, and all
state lands under a per-test AESOP_STATE_ROOT temp dir.

Coverage mirrors the advancer's contract:
  * zero-sleep proof (AST + source scan of the module's own text)
  * precondition failures exit 2
  * fail-closed check bucketing (CANCELLED/TIMED_OUT/ACTION_REQUIRED/unknown)
  * file-disjoint partition and queue ordering
  * singleton fast path merges AND verifies state == MERGED
  * conflicting batch member dropped, not the whole batch
  * ancestor guard blocks a close whose content never landed
  * exception-row schema, append-only-ness and dedupe
  * idempotent re-entry (second pass on unchanged state mutates nothing)
  * lock contention exits cleanly
"""
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "merge_queue.py"


def load_module():
    """Load tools/merge_queue.py as an isolated module object."""
    spec = importlib.util.spec_from_file_location("merge_queue_under_test", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_run(name, status="COMPLETED", conclusion="SUCCESS", url=""):
    """Build a statusCheckRollup CheckRun entry."""
    return {"name": name, "status": status, "conclusion": conclusion,
            "detailsUrl": url}


def green_rollup(module, url=""):
    """A rollup where every required context is a completed success."""
    return [check_run(name, url=url) for name in module.EXPECTED_REQUIRED_CHECKS]


class StateIsolatedTestCase(unittest.TestCase):
    """Base class: every test gets its own AESOP_STATE_ROOT and module instance."""

    def setUp(self):
        self.module = load_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.state_root = Path(self._tmp.name) / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._prev_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_root)

    def tearDown(self):
        if self._prev_state_root is None:
            os.environ.pop("AESOP_STATE_ROOT", None)
        else:
            os.environ["AESOP_STATE_ROOT"] = self._prev_state_root
        self._tmp.cleanup()

    def exception_rows(self):
        return self.module.read_exceptions()


class TestZeroSleep(unittest.TestCase):
    """The scheduler is the loop. There is no sleep in this module, ever."""

    def test_source_contains_no_sleep_call(self):
        source = TOOL_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "sleep":
                offenders.append(getattr(node, "lineno", "?"))
            if isinstance(func, ast.Name) and func.id == "sleep":
                offenders.append(getattr(node, "lineno", "?"))
        self.assertEqual(
            offenders, [],
            "merge_queue.py must contain zero sleep calls (lines: %s)" % offenders)

    def test_source_never_imports_sleep(self):
        source = TOOL_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "time":
                names = [alias.name for alias in node.names]
                self.assertNotIn("sleep", names,
                                 "merge_queue.py must not import time.sleep")

    def test_source_has_no_polling_loop(self):
        """No `while True` anywhere -- a pass is bounded by construction."""
        source = TOOL_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                is_true = (isinstance(node.test, ast.Constant)
                           and node.test.value is True)
                self.assertFalse(is_true,
                                 "merge_queue.py must contain no infinite loop")


class TestCli(unittest.TestCase):
    """CLI surface: help works, a bare invocation fails closed, output is ASCII."""

    def _run(self, *args):
        return subprocess.run([sys.executable, str(TOOL_PATH)] + list(args),
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=30, cwd=str(TOOL_PATH.parent.parent))

    def test_help_exits_zero(self):
        result = self._run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("advance", result.stdout.lower())

    def test_no_action_exits_two(self):
        result = self._run()
        self.assertEqual(result.returncode, 2)

    def test_unknown_flag_exits_nonzero(self):
        result = self._run("--not-a-real-flag")
        self.assertNotEqual(result.returncode, 0)

    def test_help_output_is_ascii(self):
        result = self._run("--help")
        result.stdout.encode("ascii")


class TestPreconditions(StateIsolatedTestCase):
    """Fail-closed preconditions: gh auth, enforce_admins, required-check set."""

    def test_gh_auth_failure_fails_closed(self):
        with patch.object(self.module, "gh", return_value={"error": "not logged in"}):
            ok, detail = self.module.check_gh_auth()
        self.assertFalse(ok)
        self.assertIn("gh auth status failed", detail)

    def test_enforce_admins_false_fails_closed(self):
        with patch.object(self.module, "gh", return_value=False):
            ok, detail = self.module.check_enforce_admins("o/r")
        self.assertFalse(ok)
        self.assertIn("enforce_admins", detail)

    def test_enforce_admins_true_accepted_as_bool_and_string(self):
        with patch.object(self.module, "gh", return_value=True):
            self.assertTrue(self.module.check_enforce_admins("o/r")[0])
        with patch.object(self.module, "gh", return_value="true"):
            self.assertTrue(self.module.check_enforce_admins("o/r")[0])

    def test_required_context_drift_fails_closed(self):
        with patch.object(self.module, "gh", return_value=["ci (0)", "windows"]):
            ok, detail = self.module.check_required_contexts("o/r")
        self.assertFalse(ok)
        self.assertIn("drift", detail)

    def test_required_context_exact_match_passes(self):
        contexts = list(self.module.EXPECTED_REQUIRED_CHECKS)
        with patch.object(self.module, "gh", return_value=contexts):
            self.assertTrue(self.module.check_required_contexts("o/r")[0])

    def test_required_context_api_error_fails_closed(self):
        with patch.object(self.module, "gh", return_value={"error": "404"}):
            self.assertFalse(self.module.check_required_contexts("o/r")[0])

    def test_run_pass_exits_two_on_precondition_failure(self):
        with patch.object(self.module, "gh", return_value={"error": "gh unavailable"}):
            code, summary = self.module.run_pass(repo="o/r")
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "precondition_failed")
        rows = self.exception_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "precondition_failed")

    def test_precondition_failure_never_acquires_lock(self):
        with patch.object(self.module, "gh", return_value={"error": "gh unavailable"}):
            self.module.run_pass(repo="o/r")
        self.assertFalse((self.state_root / self.module.LOCK_DIRNAME).exists())


class TestCheckBucketing(StateIsolatedTestCase):
    """Our own bucketing, independent of merge_train.pr_state's fail-open path."""

    def test_cancelled_is_not_green(self):
        _, verdict, _ = self.module.classify_check(
            check_run("ci (0)", conclusion="CANCELLED"))
        self.assertEqual(verdict, "not_green")

    def test_timed_out_and_action_required_are_not_green(self):
        for conclusion in ("TIMED_OUT", "ACTION_REQUIRED", "STALE",
                           "STARTUP_FAILURE", "NEUTRAL", "FAILURE"):
            _, verdict, _ = self.module.classify_check(
                check_run("ci (0)", conclusion=conclusion))
            self.assertEqual(verdict, "not_green",
                             "%s must bucket as not_green" % conclusion)

    def test_null_conclusion_on_completed_run_is_not_green(self):
        _, verdict, _ = self.module.classify_check(
            {"name": "ci (0)", "status": "COMPLETED", "conclusion": None})
        self.assertEqual(verdict, "not_green")

    def test_unknown_shape_is_not_green(self):
        _, verdict, _ = self.module.classify_check({"name": "mystery"})
        self.assertEqual(verdict, "not_green")
        _, verdict, _ = self.module.classify_check("not a dict")
        self.assertEqual(verdict, "not_green")

    def test_incomplete_run_is_pending(self):
        _, verdict, _ = self.module.classify_check(
            check_run("ci (0)", status="IN_PROGRESS", conclusion=None))
        self.assertEqual(verdict, "pending")

    def test_success_is_green(self):
        _, verdict, _ = self.module.classify_check(check_run("ci (0)"))
        self.assertEqual(verdict, "green")

    def test_status_context_shape_supported(self):
        _, verdict, url = self.module.classify_check(
            {"context": "ci (0)", "state": "SUCCESS", "targetUrl": "http://x"})
        self.assertEqual(verdict, "green")
        self.assertEqual(url, "http://x")
        _, verdict, _ = self.module.classify_check(
            {"context": "ci (0)", "state": "ERROR"})
        self.assertEqual(verdict, "not_green")

    def test_empty_rollup_is_not_green(self):
        verdict, detail, _ = self.module.required_checks_green([])
        self.assertEqual(verdict, "not_green")
        self.assertIn("absent", detail)

    def test_missing_required_context_is_not_green(self):
        rollup = green_rollup(self.module)[:-1]
        verdict, detail, _ = self.module.required_checks_green(rollup)
        self.assertEqual(verdict, "not_green")
        self.assertIn("windows", detail)

    def test_one_cancelled_required_check_poisons_the_rollup(self):
        rollup = green_rollup(self.module)
        rollup[2] = check_run(self.module.EXPECTED_REQUIRED_CHECKS[2],
                              conclusion="CANCELLED", url="http://run/2")
        verdict, detail, url = self.module.required_checks_green(rollup)
        self.assertEqual(verdict, "not_green")
        self.assertEqual(url, "http://run/2")

    def test_not_green_outranks_pending(self):
        rollup = green_rollup(self.module)
        rollup[0] = check_run(self.module.EXPECTED_REQUIRED_CHECKS[0],
                              status="IN_PROGRESS", conclusion=None)
        rollup[1] = check_run(self.module.EXPECTED_REQUIRED_CHECKS[1],
                              conclusion="CANCELLED")
        verdict, _, _ = self.module.required_checks_green(rollup)
        self.assertEqual(verdict, "not_green")

    def test_duplicate_name_collapses_to_worst_verdict(self):
        rollup = green_rollup(self.module)
        rollup.append(check_run(self.module.EXPECTED_REQUIRED_CHECKS[0],
                                conclusion="CANCELLED"))
        verdict, _, _ = self.module.required_checks_green(rollup)
        self.assertEqual(verdict, "not_green")

    def test_extra_non_required_red_check_does_not_block(self):
        rollup = green_rollup(self.module)
        rollup.append(check_run("browser-proofs", conclusion="FAILURE"))
        verdict, _, _ = self.module.required_checks_green(rollup)
        self.assertEqual(verdict, "green")

    def test_all_green_is_green(self):
        verdict, _, _ = self.module.required_checks_green(green_rollup(self.module))
        self.assertEqual(verdict, "green")


class TestQueueOrderingAndPartition(StateIsolatedTestCase):
    """PR-number ascending, merge-priority jumps the line, file-disjoint admission."""

    def test_queue_is_pr_number_ascending(self):
        prs = [{"number": 30, "labels": []},
               {"number": 10, "labels": []},
               {"number": 20, "labels": []}]
        ordered = [pr["number"] for pr in self.module.order_queue(prs)]
        self.assertEqual(ordered, [10, 20, 30])

    def test_merge_priority_jumps_the_line(self):
        prs = [{"number": 10, "labels": []},
               {"number": 99, "labels": [{"name": self.module.PRIORITY_LABEL}]},
               {"number": 20, "labels": []}]
        ordered = [pr["number"] for pr in self.module.order_queue(prs)]
        self.assertEqual(ordered, [99, 10, 20])

    def test_disjoint_partition_admits_non_intersecting(self):
        admitted = self.module.partition_disjoint([
            (1, ["a.py"]), (2, ["b.py"]), (3, ["c.py"])])
        self.assertEqual(admitted, [1, 2, 3])

    def test_disjoint_partition_defers_overlapping(self):
        admitted = self.module.partition_disjoint([
            (1, ["a.py", "shared.py"]),
            (2, ["shared.py"]),
            (3, ["c.py"])])
        self.assertEqual(admitted, [1, 3])

    def test_disjoint_partition_is_greedy_in_queue_order(self):
        admitted = self.module.partition_disjoint([
            (5, ["shared.py"]), (1, ["shared.py"])])
        self.assertEqual(admitted, [5])

    def test_unknown_file_set_is_never_admitted(self):
        admitted = self.module.partition_disjoint([(1, []), (2, ["b.py"])])
        self.assertEqual(admitted, [2])

    def test_parse_members_from_batch_body(self):
        body = "Batch.\n\nMembers: #12, #13, #14\n\nFooter."
        self.assertEqual(self.module.parse_members(body), [12, 13, 14])

    def test_parse_members_absent_returns_empty(self):
        self.assertEqual(self.module.parse_members("no members here"), [])
        self.assertEqual(self.module.parse_members(""), [])


class TestSingletonFastPath(StateIsolatedTestCase):
    """Batch of one, CLEAN, all required checks green -> merge and PROVE it."""

    def _pr(self, number=101, rollup=None, mergeable="MERGEABLE",
            merge_state="CLEAN", state="OPEN"):
        return {
            "number": number, "title": "t", "state": state,
            "mergeable": mergeable, "mergeStateStatus": merge_state,
            "statusCheckRollup": rollup if rollup is not None
            else green_rollup(self.module),
            "headRefName": "feat/x", "headRefOid": "abc123",
            "labels": [], "body": "", "url": "https://x/pull/%d" % number,
        }

    def _gh(self, pr_payload, merged_state="MERGED", calls=None):
        calls = calls if calls is not None else []

        def side_effect(*args):
            calls.append(args)
            if args[:2] == ("pr", "view") and "state" in args and "--jq" in args:
                return merged_state
            if args[:2] == ("pr", "view"):
                return pr_payload
            if args[:2] == ("pr", "merge"):
                return ""
            return {}
        return side_effect, calls

    def test_green_clean_singleton_merges(self):
        summary = {"actions": [], "merged": [], "status": "ok"}
        side_effect, calls = self._gh(self._pr())
        with patch.object(self.module, "gh", side_effect=side_effect):
            ok = self.module.advance_singleton(101, summary)
        self.assertTrue(ok)
        self.assertEqual(summary["merged"], [101])
        merge_calls = [c for c in calls if c[:2] == ("pr", "merge")]
        self.assertEqual(len(merge_calls), 1)
        self.assertIn("--merge", merge_calls[0])

    def test_merge_never_uses_admin_or_auto(self):
        summary = {"actions": [], "merged": [], "status": "ok"}
        side_effect, calls = self._gh(self._pr())
        with patch.object(self.module, "gh", side_effect=side_effect):
            self.module.advance_singleton(101, summary)
        flat = " ".join(" ".join(str(a) for a in c) for c in calls)
        self.assertNotIn("--admin", flat)
        self.assertNotIn("--auto", flat)

    def test_merge_verifies_merged_state(self):
        """gh pr merge exiting 0 is NOT proof; state must read MERGED."""
        summary = {"actions": [], "merged": [], "status": "ok"}
        side_effect, calls = self._gh(self._pr(), merged_state="OPEN")
        with patch.object(self.module, "gh", side_effect=side_effect):
            ok = self.module.advance_singleton(101, summary)
        self.assertFalse(ok)
        self.assertEqual(summary["merged"], [])
        rows = self.exception_rows()
        self.assertEqual([r["kind"] for r in rows], ["merge_verify_failed"])

    def test_cancelled_check_blocks_the_merge(self):
        rollup = green_rollup(self.module)
        rollup[0] = check_run(self.module.EXPECTED_REQUIRED_CHECKS[0],
                              conclusion="CANCELLED")
        summary = {"actions": [], "merged": [], "status": "ok"}
        side_effect, calls = self._gh(self._pr(rollup=rollup))
        with patch.object(self.module, "gh", side_effect=side_effect):
            ok = self.module.advance_singleton(101, summary)
        self.assertFalse(ok)
        self.assertEqual([c for c in calls if c[:2] == ("pr", "merge")], [])

    def test_pending_checks_are_a_noop(self):
        rollup = green_rollup(self.module)
        rollup[0] = check_run(self.module.EXPECTED_REQUIRED_CHECKS[0],
                              status="IN_PROGRESS", conclusion=None)
        summary = {"actions": [], "merged": [], "status": "ok"}
        side_effect, calls = self._gh(self._pr(rollup=rollup))
        with patch.object(self.module, "gh", side_effect=side_effect):
            self.module.advance_singleton(101, summary)
        self.assertEqual([c for c in calls if c[:2] == ("pr", "merge")], [])
        self.assertEqual(self.exception_rows(), [])

    def test_conflicting_pr_is_exception_rowed_not_merged(self):
        summary = {"actions": [], "merged": [], "status": "ok"}
        side_effect, calls = self._gh(self._pr(mergeable="CONFLICTING",
                                               merge_state="DIRTY"))
        with patch.object(self.module, "gh", side_effect=side_effect):
            self.module.advance_singleton(101, summary)
        self.assertEqual([c for c in calls if c[:2] == ("pr", "merge")], [])
        self.assertEqual([r["kind"] for r in self.exception_rows()], ["conflict"])

    def test_conversation_blocked_is_exception_rowed_never_resolved(self):
        summary = {"actions": [], "merged": [], "status": "ok"}
        side_effect, calls = self._gh(self._pr(merge_state="BLOCKED"))
        with patch.object(self.module, "gh", side_effect=side_effect):
            self.module.advance_singleton(101, summary)
        self.assertEqual([c for c in calls if c[:2] == ("pr", "merge")], [])
        rows = self.exception_rows()
        self.assertEqual([r["kind"] for r in rows], ["conversation_blocked"])
        flat = " ".join(" ".join(str(a) for a in c) for c in calls)
        self.assertNotIn("resolve", flat.lower())

    def test_closed_pr_is_skipped(self):
        summary = {"actions": [], "merged": [], "status": "ok"}
        side_effect, calls = self._gh(self._pr(state="CLOSED"))
        with patch.object(self.module, "gh", side_effect=side_effect):
            self.assertFalse(self.module.advance_singleton(101, summary))
        self.assertEqual([c for c in calls if c[:2] == ("pr", "merge")], [])


class TestBatchConstruction(StateIsolatedTestCase):
    """Batch >1: build, drop conflicting members individually, open PR, exit."""

    def _batch_gh(self, calls):
        def side_effect(*args):
            calls.append(args)
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                return {"headRefOid": "sha%d" % number,
                        "headRefName": "feat/%d" % number,
                        "title": "pr %d" % number}
            if args[:2] == ("pr", "create"):
                return "https://github.com/o/r/pull/900"
            return {}
        return side_effect

    def _git(self, calls, conflict_sha=None):
        def side_effect(*args):
            calls.append(args)
            if args[0] == "merge" and "--abort" not in args:
                if conflict_sha and conflict_sha in args:
                    return (False, "CONFLICT (content): Merge conflict in a.py")
            if args[0] == "status":
                return (True, "")
            return (True, "")
        return side_effect

    def test_batch_branch_named_from_epoch(self):
        gh_calls, git_calls = [], []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._batch_gh(gh_calls)), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "integrate/q-1700000000")
        checkout = [c for c in git_calls if c[0] == "checkout" and "-B" in c]
        self.assertTrue(checkout)
        self.assertIn("origin/main", checkout[0])

    def test_conflicting_member_dropped_batch_continues(self):
        gh_calls, git_calls = [], []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._batch_gh(gh_calls)), \
             patch.object(self.module, "git",
                          side_effect=self._git(git_calls, conflict_sha="sha12")):
            branch = self.module.build_batch([11, 12, 13], summary, epoch=1700000000)
        self.assertTrue(branch)
        rows = self.exception_rows()
        self.assertEqual([r["kind"] for r in rows], ["member_conflict"])
        self.assertEqual(rows[0]["pr"], 12)
        self.assertEqual(summary["batch"]["members"], [11, 13])
        aborts = [c for c in git_calls if c[0] == "merge" and "--abort" in c]
        self.assertEqual(len(aborts), 1)

    def test_batch_body_records_members_for_the_next_pass(self):
        gh_calls, git_calls = [], []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._batch_gh(gh_calls)), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            self.module.build_batch([11, 12], summary, epoch=1700000000)
        create = [c for c in gh_calls if c[:2] == ("pr", "create")][0]
        body = create[create.index("--body") + 1]
        self.assertEqual(self.module.parse_members(body), [11, 12])

    def test_batch_pr_gets_the_batch_label(self):
        gh_calls, git_calls = [], []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._batch_gh(gh_calls)), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            self.module.build_batch([11, 12], summary, epoch=1700000000)
        edits = [c for c in gh_calls if c[:2] == ("pr", "edit")]
        self.assertTrue(any(self.module.BATCH_LABEL in c for c in edits))

    def test_batch_push_is_never_forced(self):
        gh_calls, git_calls = [], []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._batch_gh(gh_calls)), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            self.module.build_batch([11, 12], summary, epoch=1700000000)
        flat = " ".join(" ".join(str(a) for a in c) for c in git_calls)
        self.assertNotIn("--force", flat)
        self.assertNotIn("--force-with-lease", flat)

    def test_single_survivor_abandons_the_batch(self):
        """One clean member is a singleton next pass, not a batch of one."""
        gh_calls, git_calls = [], []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._batch_gh(gh_calls)), \
             patch.object(self.module, "git",
                          side_effect=self._git(git_calls, conflict_sha="sha12")):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "")
        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "create")], [])

    def test_dirty_worktree_refuses_to_build(self):
        gh_calls, git_calls = [], []
        summary = {"actions": [], "merged": [], "status": "ok"}

        def dirty_git(*args):
            git_calls.append(args)
            if args[0] == "status":
                return (True, " M tools/x.py")
            return (True, "")

        with patch.object(self.module, "gh", side_effect=self._batch_gh(gh_calls)), \
             patch.object(self.module, "git", side_effect=dirty_git):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "")
        self.assertEqual([r["kind"] for r in self.exception_rows()], ["dirty_worktree"])


class TestAncestorGuard(StateIsolatedTestCase):
    """A member is closed only after its content provably landed on main."""

    def test_ancestor_failure_blocks_the_close(self):
        gh_calls = []

        def gh_side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "view"):
                return {"headRefOid": "abc123", "state": "OPEN"}
            return {}

        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
             patch.object(self.module, "git", return_value=(True, "")), \
             patch.object(self.module, "is_ancestor", return_value=False):
            self.module.close_landed_members([42], "#900 (integrate/q-1)", summary)

        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "close")], [])
        rows = self.exception_rows()
        self.assertEqual([r["kind"] for r in rows], ["ancestor_check_failed"])
        self.assertEqual(rows[0]["pr"], 42)
        self.assertEqual(summary["status"], "error")

    def test_ancestor_success_closes_with_merged_via_comment(self):
        gh_calls = []

        def gh_side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "view"):
                return {"headRefOid": "abc123", "state": "OPEN"}
            return {}

        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
             patch.object(self.module, "git", return_value=(True, "")), \
             patch.object(self.module, "is_ancestor", return_value=True):
            self.module.close_landed_members([42], "#900 (integrate/q-1)", summary)

        closes = [c for c in gh_calls if c[:2] == ("pr", "close")]
        self.assertEqual(len(closes), 1)
        self.assertTrue(any("merged via" in str(a) for a in closes[0]))
        self.assertEqual(self.exception_rows(), [])


class TestBatchEvaluation(StateIsolatedTestCase):
    """Green batch merges then closes; red batch evicts and dissolves."""

    def _batch_pr(self, rollup=None, members="#11, #12"):
        return {
            "number": 900, "state": "OPEN", "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": rollup if rollup is not None
            else green_rollup(self.module),
            "headRefName": "integrate/q-1700000000", "headRefOid": "batchsha",
            "labels": [{"name": self.module.BATCH_LABEL}],
            "body": "Members: %s\n" % members,
            "url": "https://x/pull/900",
        }

    def test_green_batch_merges_then_closes_landed_members(self):
        gh_calls = []
        batch = self._batch_pr()

        def gh_side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "view") and "--jq" in args:
                return "MERGED"
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                if number == 900:
                    return batch
                return {"headRefOid": "sha%d" % number, "state": "OPEN"}
            return {}

        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
             patch.object(self.module, "git", return_value=(True, "")), \
             patch.object(self.module, "is_ancestor", return_value=True):
            self.module.handle_batch_pr({"number": 900}, summary)

        self.assertIn(900, summary["merged"])
        closes = [c for c in gh_calls if c[:2] == ("pr", "close")]
        self.assertEqual(sorted(int(c[2]) for c in closes), [11, 12])

    def test_red_batch_evicts_individually_red_member(self):
        gh_calls = []
        red_rollup = green_rollup(self.module)
        red_rollup[0] = check_run(self.module.EXPECTED_REQUIRED_CHECKS[0],
                                  conclusion="FAILURE", url="http://run/11")
        batch = self._batch_pr(rollup=red_rollup)

        def gh_side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                if number == 900:
                    return batch
                if number == 11:
                    return {"number": 11, "state": "OPEN",
                            "statusCheckRollup": red_rollup}
                return {"number": number, "state": "OPEN",
                        "statusCheckRollup": green_rollup(self.module)}
            return {}

        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.handle_batch_pr({"number": 900}, summary)

        kinds = {r["kind"] for r in self.exception_rows()}
        self.assertIn("member_red", kinds)
        rejected = [c for c in gh_calls
                    if c[:2] == ("pr", "edit") and self.module.REJECT_LABEL in c]
        self.assertEqual([int(c[2]) for c in rejected], [11])
        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "merge")], [])

    def test_red_batch_with_all_members_green_dissolves_and_rows_everyone(self):
        gh_calls = []
        red_rollup = green_rollup(self.module)
        red_rollup[0] = check_run(self.module.EXPECTED_REQUIRED_CHECKS[0],
                                  conclusion="FAILURE", url="http://run/batch")
        batch = self._batch_pr(rollup=red_rollup)

        def gh_side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                if number == 900:
                    return batch
                return {"number": number, "state": "OPEN",
                        "statusCheckRollup": green_rollup(self.module)}
            return {}

        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.handle_batch_pr({"number": 900}, summary)

        rows = self.exception_rows()
        self.assertEqual(sorted(r["pr"] for r in rows), [11, 12])
        self.assertEqual({r["kind"] for r in rows}, {"batch_red_dissolved"})
        self.assertEqual(rows[0]["run_url"], "http://run/batch")
        closes = [c for c in gh_calls if c[:2] == ("pr", "close")]
        self.assertEqual([int(c[2]) for c in closes], [900])

    def test_unparseable_members_is_exception_rowed_not_merged(self):
        gh_calls = []
        batch = self._batch_pr()
        batch["body"] = "no members line"

        def gh_side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "view"):
                return batch
            return {}

        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.handle_batch_pr({"number": 900}, summary)
        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "merge")], [])
        self.assertEqual([r["kind"] for r in self.exception_rows()],
                         ["batch_members_unparseable"])

    def test_pending_batch_is_a_noop(self):
        pending = green_rollup(self.module)
        pending[0] = check_run(self.module.EXPECTED_REQUIRED_CHECKS[0],
                               status="QUEUED", conclusion=None)
        batch = self._batch_pr(rollup=pending)
        gh_calls = []

        def gh_side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "view"):
                return batch
            return {}

        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.handle_batch_pr({"number": 900}, summary)
        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "merge")], [])
        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "close")], [])
        self.assertEqual(self.exception_rows(), [])


class TestExceptionLedger(StateIsolatedTestCase):
    """Row schema, append-only-ness, dedupe."""

    def test_row_schema_is_exactly_five_keys(self):
        self.module.record_exception(42, "conflict", "detail text", "http://run")
        raw = self.module.exceptions_path().read_text(encoding="utf-8").strip()
        row = json.loads(raw)
        self.assertEqual(list(row.keys()), ["ts", "pr", "kind", "detail", "run_url"])
        self.assertEqual(row["pr"], 42)
        self.assertEqual(row["kind"], "conflict")
        self.assertEqual(row["detail"], "detail text")
        self.assertEqual(row["run_url"], "http://run")
        self.assertRegex(row["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_ledger_is_append_only_one_json_object_per_line(self):
        self.module.record_exception(1, "a", "one")
        self.module.record_exception(2, "b", "two")
        lines = self.module.exceptions_path().read_text(
            encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            json.loads(line)
        self.assertEqual([r["pr"] for r in self.exception_rows()], [1, 2])

    def test_identical_row_is_deduped(self):
        self.module.record_exception(1, "conversation_blocked", "same")
        self.module.record_exception(1, "conversation_blocked", "same")
        self.assertEqual(len(self.exception_rows()), 1)

    def test_different_detail_appends(self):
        self.module.record_exception(1, "conflict", "first")
        self.module.record_exception(1, "conflict", "second")
        self.assertEqual(len(self.exception_rows()), 2)

    def test_ledger_lives_under_state_merge_queue(self):
        path = self.module.exceptions_path()
        self.assertEqual(path.name, "exceptions.jsonl")
        self.assertEqual(path.parent.name, "merge-queue")


class TestLock(StateIsolatedTestCase):
    """Single-instance, fail-closed on contention, stale reclaim, heartbeat."""

    def test_second_acquire_fails_closed(self):
        lock = self.state_root / self.module.LOCK_DIRNAME
        self.assertTrue(self.module.acquire_lock(lock))
        self.assertFalse(self.module.acquire_lock(lock))

    def test_release_then_reacquire(self):
        lock = self.state_root / self.module.LOCK_DIRNAME
        self.assertTrue(self.module.acquire_lock(lock))
        self.module.release_lock(lock)
        self.assertTrue(self.module.acquire_lock(lock))

    def test_stale_lock_is_reclaimed(self):
        lock = self.state_root / self.module.LOCK_DIRNAME
        lock.mkdir(parents=True)
        (lock / "timestamp").write_text("1", encoding="utf-8")
        (lock / "pid").write_text("999999", encoding="utf-8")
        self.assertTrue(self.module.acquire_lock(lock, stale_s=60))

    def test_fresh_lock_is_never_stolen(self):
        import time as _time
        lock = self.state_root / self.module.LOCK_DIRNAME
        lock.mkdir(parents=True)
        (lock / "timestamp").write_text(str(int(_time.time())), encoding="utf-8")
        (lock / "pid").write_text("999999", encoding="utf-8")
        self.assertFalse(self.module.acquire_lock(lock, stale_s=600))

    def test_release_does_not_remove_someone_elses_lock(self):
        lock = self.state_root / self.module.LOCK_DIRNAME
        lock.mkdir(parents=True)
        (lock / "pid").write_text("999999", encoding="utf-8")
        self.module.release_lock(lock)
        self.assertTrue(lock.exists())

    def test_lock_contention_exits_cleanly(self):
        lock = self.state_root / self.module.LOCK_DIRNAME
        self.assertTrue(self.module.acquire_lock(lock))
        contexts = list(self.module.EXPECTED_REQUIRED_CHECKS)

        def gh_side_effect(*args):
            if "--jq" in args and ".enforce_admins.enabled" in args:
                return True
            if "--jq" in args and ".required_status_checks.contexts" in args:
                return contexts
            return ""

        with patch.object(self.module, "gh", side_effect=gh_side_effect):
            code, summary = self.module.run_pass(repo="o/r")
        self.assertEqual(code, 0)
        self.assertEqual(summary["status"], "lock_contention")

    def test_pass_beats_the_heartbeat(self):
        contexts = list(self.module.EXPECTED_REQUIRED_CHECKS)

        def gh_side_effect(*args):
            if "--jq" in args and ".enforce_admins.enabled" in args:
                return True
            if "--jq" in args and ".required_status_checks.contexts" in args:
                return contexts
            if args[:2] == ("pr", "list"):
                return []
            return ""

        with patch.object(self.module, "gh", side_effect=gh_side_effect):
            code, _ = self.module.run_pass(repo="o/r")
        self.assertEqual(code, 0)
        hb = self.state_root / self.module.HEARTBEAT_NAME
        self.assertTrue(hb.exists())
        self.assertTrue(hb.read_text(encoding="utf-8").strip().isdigit())

    def test_lock_released_after_a_pass(self):
        contexts = list(self.module.EXPECTED_REQUIRED_CHECKS)

        def gh_side_effect(*args):
            if "--jq" in args and ".enforce_admins.enabled" in args:
                return True
            if "--jq" in args and ".required_status_checks.contexts" in args:
                return contexts
            if args[:2] == ("pr", "list"):
                return []
            return ""

        with patch.object(self.module, "gh", side_effect=gh_side_effect):
            self.module.run_pass(repo="o/r")
        self.assertFalse((self.state_root / self.module.LOCK_DIRNAME).exists())


class TestIdempotentReEntry(StateIsolatedTestCase):
    """A second pass over unchanged state mutates nothing and appends nothing."""

    def _stable_gh(self, calls, rollup):
        contexts = list(self.module.EXPECTED_REQUIRED_CHECKS)

        def side_effect(*args):
            calls.append(args)
            if "--jq" in args and ".enforce_admins.enabled" in args:
                return True
            if "--jq" in args and ".required_status_checks.contexts" in args:
                return contexts
            if args[:2] == ("pr", "list"):
                if args[args.index("--label") + 1] == self.module.BATCH_LABEL:
                    return []
                return [{"number": 55, "title": "t", "labels": [],
                         "body": "", "headRefName": "feat/x"}]
            if args[:2] == ("pr", "view") and "files" in args:
                return {"files": [{"path": "tools/x.py"}]}
            if args[:2] == ("pr", "view"):
                return {"number": 55, "state": "OPEN", "mergeable": "MERGEABLE",
                        "mergeStateStatus": "BLOCKED",
                        "statusCheckRollup": rollup,
                        "headRefName": "feat/x", "headRefOid": "sha55",
                        "labels": [], "body": "", "url": "https://x/pull/55"}
            return ""
        return side_effect

    def test_second_pass_is_a_true_noop(self):
        rollup = green_rollup(self.module)
        calls_a, calls_b = [], []

        with patch.object(self.module, "gh",
                          side_effect=self._stable_gh(calls_a, rollup)):
            code_a, _ = self.module.run_pass(repo="o/r")
        rows_after_first = self.exception_rows()

        with patch.object(self.module, "gh",
                          side_effect=self._stable_gh(calls_b, rollup)):
            code_b, _ = self.module.run_pass(repo="o/r")
        rows_after_second = self.exception_rows()

        self.assertEqual(code_a, 0)
        self.assertEqual(code_b, 0)
        self.assertEqual([r["kind"] for r in rows_after_first],
                         ["conversation_blocked"])
        self.assertEqual(rows_after_second, rows_after_first,
                         "a re-entrant pass must append no new exception row")
        for calls in (calls_a, calls_b):
            self.assertEqual([c for c in calls if c[:2] == ("pr", "merge")], [])
            self.assertEqual([c for c in calls if c[:2] == ("pr", "edit")], [])
            self.assertEqual([c for c in calls if c[:2] == ("pr", "create")], [])

    def test_empty_queue_pass_is_a_noop(self):
        contexts = list(self.module.EXPECTED_REQUIRED_CHECKS)
        calls = []

        def gh_side_effect(*args):
            calls.append(args)
            if "--jq" in args and ".enforce_admins.enabled" in args:
                return True
            if "--jq" in args and ".required_status_checks.contexts" in args:
                return contexts
            if args[:2] == ("pr", "list"):
                return []
            return ""

        with patch.object(self.module, "gh", side_effect=gh_side_effect):
            code, summary = self.module.run_pass(repo="o/r")
        self.assertEqual(code, 0)
        self.assertEqual(summary["merged"], [])
        self.assertEqual(self.exception_rows(), [])


class TestForbiddenOperations(unittest.TestCase):
    """Static proof the module can never reach for a forbidden lever."""

    @staticmethod
    def executable_source():
        """Module source with comments and every docstring removed.

        Prose is allowed to NAME the forbidden levers (the contract has to say
        what is forbidden); executable code is not allowed to USE them.
        """
        source = TOOL_PATH.read_text(encoding="utf-8")
        lines = source.splitlines()
        tree = ast.parse(source)
        blanked = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            body = getattr(node, "body", None) or []
            if not body:
                continue
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                blanked.update(range(first.lineno, (first.end_lineno or
                                                    first.lineno) + 1))
        kept = []
        for index, line in enumerate(lines, 1):
            if index in blanked:
                continue
            if line.strip().startswith("#"):
                continue
            kept.append(line)
        return "\n".join(kept)

    def test_source_never_uses_admin_auto_or_force_push(self):
        code = self.executable_source()
        offenders = [token for token in
                     ("--admin", "--auto", "--force", "--force-with-lease")
                     if token in code]
        self.assertEqual(offenders, [],
                         "merge_queue.py must never use these flags: %s" % offenders)

    def test_source_never_resolves_review_threads(self):
        source = TOOL_PATH.read_text(encoding="utf-8").lower()
        self.assertNotIn("resolvereviewthread", source)

    def test_source_makes_no_model_call(self):
        source = TOOL_PATH.read_text(encoding="utf-8").lower()
        for token in ("anthropic", "openai", "claude -p", "agentdriver"):
            self.assertNotIn(token, source)

    def test_transport_primitives_are_imported_not_duplicated(self):
        """gh/git come from merge_train and are never redefined here."""
        tree = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "merge_train":
                imported.update(alias.name for alias in node.names)
        self.assertEqual(imported, {"gh", "git"})
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)}
        for name in ("gh", "git"):
            self.assertNotIn(name, defined,
                             "%s must be imported from merge_train, not redefined"
                             % name)

    def test_local_ancestor_guard_fails_closed(self):
        """is_ancestor is local (B1.3 has not landed) and treats git failure as
        'did not land' -- the direction that can never close an unlanded PR."""
        module = load_module()
        with patch.object(module, "git", return_value=(False, "fatal")):
            self.assertFalse(module.is_ancestor("abc123"))
        with patch.object(module, "git", return_value=(True, "")):
            self.assertTrue(module.is_ancestor("abc123"))
        self.assertFalse(module.is_ancestor(""))


class TestLaneContractLint(unittest.TestCase):
    """Q2: dispatch_lint forbids lane-side CI polling in DISPATCH PROMPTS.

    The lane's terminal action is: push -> open PR ->
    `gh pr edit <n> --add-label merge-queue` -> exit. Anything that makes the
    lane sit on CI re-couples merging to a live session.
    """

    @classmethod
    def setUpClass(cls):
        lint_path = (Path(__file__).resolve().parents[1] / "tools"
                     / "dispatch_lint.py")
        spec = importlib.util.spec_from_file_location("dispatch_lint_under_test",
                                                      lint_path)
        cls.lint = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.lint)

    def violations(self, prompt_body):
        """Run the linter over a synthetic dispatch file."""
        content = 'Agent(\n  prompt: """\n%s\n"""\n)\n' % prompt_body
        return self.lint.find_violations(Path("fake_dispatch.py"), content)

    def kinds(self, prompt_body):
        return {v["pattern"] for v in self.violations(prompt_body)}

    def test_ci_merge_wait_is_forbidden(self):
        self.assertIn("lane_ci_polling_merge_wait",
                      self.kinds("Then run python tools/ci_merge_wait.py 123"))

    def test_ci_merge_wait_bare_name_is_forbidden(self):
        self.assertIn("lane_ci_polling_merge_wait",
                      self.kinds("wait via ci_merge_wait until green"))

    def test_gh_run_watch_is_forbidden(self):
        self.assertIn("lane_ci_polling_run_watch",
                      self.kinds("Use gh run watch to follow the build"))

    def test_merge_train_invocation_is_forbidden(self):
        self.assertIn("lane_ci_polling_merge_train",
                      self.kinds("finish with python tools/merge_train.py 500 501"))

    def test_sleep_wrapped_gh_pr_checks_is_forbidden(self):
        self.assertIn("lane_ci_polling_sleep_checks",
                      self.kinds("loop: sleep 60 && gh pr checks 123"))
        self.assertIn("lane_ci_polling_sleep_checks",
                      self.kinds("run gh pr checks 123 then sleep 60"))

    def test_plain_gh_pr_checks_is_allowed(self):
        """A single status read is fine; it is the WAIT that is forbidden."""
        self.assertEqual(
            self.kinds("read gh pr checks 123 once and report"),
            set())

    def test_the_prescribed_terminal_action_is_clean(self):
        prompt = ("push the branch, open the PR, then run "
                  "gh pr edit 123 --add-label merge-queue and exit")
        self.assertEqual(self.kinds(prompt), set())

    def test_lane_polling_is_suppressible_for_the_advancer_itself(self):
        content = ('Agent(\n  prompt: """\n'
                   'run python tools/merge_train.py 1  # dispatch-ok\n'
                   '"""\n)\n')
        violations = self.lint.find_violations(Path("fake.py"), content)
        self.assertEqual(violations, [])

    def test_non_dispatch_file_is_not_scanned(self):
        """Only dispatch prompts are in scope; ordinary code is untouched."""
        content = "subprocess.run(['python', 'tools/merge_train.py', '1'])\n"
        self.assertEqual(self.lint.find_violations(Path("x.py"), content), [])


if __name__ == "__main__":
    unittest.main()
