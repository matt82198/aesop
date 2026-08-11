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


def pending_rollup(module):
    """A rollup where every required context is still running."""
    return [check_run(name, status="IN_PROGRESS", conclusion=None)
            for name in module.EXPECTED_REQUIRED_CHECKS]


def load_generated_paths():
    """Load tools/generated_paths.py -- the single generated-file registry."""
    path = Path(__file__).resolve().parents[1] / "tools" / "generated_paths.py"
    spec = importlib.util.spec_from_file_location("generated_paths_under_test",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StateIsolatedTestCase(unittest.TestCase):
    """Base class: every test gets its own AESOP_STATE_ROOT and module instance."""

    def setUp(self):
        self.module = load_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.state_root = Path(self._tmp.name) / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._prev_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_root)
        # No unit test may shell out. `run_regenerator` is the module's only
        # subprocess call; it is stubbed green here so build_batch tests
        # exercise batch construction, not this repo's real generators. Tests
        # that care about regeneration re-patch it with their own behaviour.
        self._regen_patch = patch.object(self.module, "run_regenerator",
                                         return_value=(True, ""))
        self._regen_patch.start()
        self.addCleanup(self._regen_patch.stop)

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
            "baseRefName": "main",
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
        """gh fake that MODELS labelling: `pr edit --add-label` sticks, and a
        later `pr view` reports it (that read-back is what the silent-label-
        failure guard checks)."""
        applied = set()

        def side_effect(*args):
            calls.append(args)
            if args[:2] == ("pr", "edit") and "--add-label" in args:
                applied.add(args[args.index("--add-label") + 1])
                return ""
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                return {"headRefOid": "sha%d" % number,
                        "baseRefName": "main",
                        "headRefName": "feat/%d" % number,
                        "title": "pr %d" % number,
                        "labels": [{"name": n} for n in sorted(applied)]}
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
            if args[0] == "rev-parse":
                return (True, "main")
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
            if args[0] == "rev-parse":
                return (True, "main")
            return (True, "")

        with patch.object(self.module, "gh", side_effect=self._batch_gh(gh_calls)), \
             patch.object(self.module, "git", side_effect=dirty_git):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "")
        rows = self.exception_rows()
        self.assertEqual([r["kind"] for r in rows], ["unsafe_worktree"])
        self.assertIn("dirty", rows[0]["detail"])
        self.assertEqual([c for c in git_calls if c[0] == "checkout"], [])

    def test_wrong_branch_refuses_to_build(self):
        """The daemon must never yank a human's checked-out branch."""
        gh_calls, git_calls = [], []
        summary = {"actions": [], "merged": [], "status": "ok"}

        def other_branch_git(*args):
            git_calls.append(args)
            if args[0] == "status":
                return (True, "")
            if args[0] == "rev-parse":
                return (True, "feat/someones-work")
            return (True, "")

        with patch.object(self.module, "gh", side_effect=self._batch_gh(gh_calls)), \
             patch.object(self.module, "git", side_effect=other_branch_git):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "")
        rows = self.exception_rows()
        self.assertEqual([r["kind"] for r in rows], ["unsafe_worktree"])
        self.assertIn("feat/someones-work", rows[0]["detail"])
        self.assertEqual([c for c in git_calls if c[0] == "checkout"], [])

    def test_unreadable_git_state_refuses_to_build(self):
        """Fail-closed: if git state cannot be read, do not build."""
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._batch_gh([])), \
             patch.object(self.module, "git", return_value=(False, "fatal")):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "")
        self.assertEqual([r["kind"] for r in self.exception_rows()],
                         ["unsafe_worktree"])

    def test_singleton_path_never_touches_the_working_tree(self):
        """The common case is pure API -- no git, so no tree hazard at all."""
        git_calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}

        def gh_side_effect(*args):
            if args[:2] == ("pr", "view") and "--jq" in args:
                return "MERGED"
            if args[:2] == ("pr", "view"):
                return {"number": 77, "state": "OPEN", "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                        "statusCheckRollup": green_rollup(self.module),
                        "baseRefName": "main",
                        "headRefName": "feat/x", "headRefOid": "sha77",
                        "labels": [], "body": "", "url": "https://x/pull/77"}
            return ""

        def tracking_git(*args):
            git_calls.append(args)
            return (True, "")

        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
             patch.object(self.module, "git", side_effect=tracking_git):
            ok = self.module.advance_singleton(77, summary)
        self.assertTrue(ok)
        self.assertEqual(git_calls, [], "singleton merge must not invoke git")


class TestWorktreeAlwaysRestored(StateIsolatedTestCase):
    """A3: every exit path out of build_batch puts the shared tree back on main.

    build_batch does `git checkout -B integrate/q-<epoch>` in a working tree
    the scheduled task shares with a human. A failure that returned without
    restoring main left the tree on the integration branch forever, so every
    later pass died `unsafe_worktree` -- and record_exception's dedupe meant
    the row was written once and then silenced. The queue stops merging and
    says nothing.
    """

    def _git(self, calls, fail_on=None):
        """git fake on a clean tree; `fail_on` names one failing subcommand."""
        def side_effect(*args):
            calls.append(args)
            if fail_on and args[0] == fail_on:
                return (False, "simulated %s failure" % fail_on)
            if args[0] == "status":
                return (True, "")
            if args[0] == "rev-parse":
                return (True, "main")
            return (True, "")
        return side_effect

    def _gh(self, create_fails=False):
        def side_effect(*args):
            if args[:2] == ("pr", "edit"):
                return ""
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                return {"headRefOid": "sha%d" % number,
                        "baseRefName": "main",
                        "headRefName": "feat/%d" % number,
                        "title": "pr %d" % number,
                        "labels": [{"name": self.module.BATCH_LABEL}]}
            if args[:2] == ("pr", "create"):
                if create_fails:
                    return {"error": "GraphQL: was submitted too quickly"}
                return "https://github.com/o/r/pull/900"
            return {}
        return side_effect

    def assertRestoredToMain(self, git_calls):
        restores = [c for c in git_calls if c[0] == "checkout" and "-B" not in c]
        self.assertTrue(
            restores and restores[-1][:2] == ("checkout", "main"),
            "working tree was left on the integration branch; git calls: %s"
            % (git_calls,))

    def test_pr_create_failure_restores_main(self):
        """The observed strand: `gh pr create` errors and the tree never returns."""
        git_calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._gh(create_fails=True)), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "")
        self.assertEqual(summary["status"], "error")
        self.assertEqual([r["kind"] for r in self.exception_rows()],
                         ["batch_pr_create_failed"])
        self.assertRestoredToMain(git_calls)

    def test_push_failure_restores_main(self):
        git_calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._gh()), \
             patch.object(self.module, "git",
                          side_effect=self._git(git_calls, fail_on="push")):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "")
        self.assertRestoredToMain(git_calls)
        self.assertTrue([c for c in git_calls if c[0] == "branch" and "-D" in c],
                        "the unpushed integration branch must be deleted")

    def test_success_restores_main(self):
        git_calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._gh()), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "integrate/q-1700000000")
        self.assertRestoredToMain(git_calls)

    def test_unexpected_exception_still_restores_main(self):
        """Even a raise out of the middle of batch construction restores main."""
        git_calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}

        def exploding_gh(*args):
            if args[:2] == ("pr", "view"):
                raise subprocess.TimeoutExpired(cmd=["gh", "pr", "view"], timeout=60)
            return {}

        with patch.object(self.module, "gh", side_effect=exploding_gh), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertRestoredToMain(git_calls)


class TestTimeoutContainment(StateIsolatedTestCase):
    """A7: a hung transport call is contained, not a traceback.

    merge_train.gh/git run subprocess with timeout=60/120 and no handler
    anywhere in merge_queue, so a hung gh or git raised TimeoutExpired straight
    out of run_pass/main: no exception row (the ledger shows nothing at all),
    a possibly stranded working tree, and a scheduled task whose only record is
    a Python traceback nobody reads.
    """

    def _preconditions_ok(self, then):
        """gh fake: preconditions pass, then `then(*args)` handles the rest."""
        contexts = list(self.module.EXPECTED_REQUIRED_CHECKS)

        def side_effect(*args):
            if args[:2] == ("auth", "status"):
                return ""
            if "--jq" in args and ".default_branch" in args:
                return "main"
            if "--jq" in args and ".enforce_admins.enabled" in args:
                return True
            if "--jq" in args and ".required_status_checks.contexts" in args:
                return contexts
            return then(*args)
        return side_effect

    def test_timeout_writes_an_exception_row_and_exits_nonzero(self):
        def hang(*args):
            raise subprocess.TimeoutExpired(cmd=["gh"] + list(args), timeout=60)

        with patch.object(self.module, "gh",
                          side_effect=self._preconditions_ok(hang)), \
             patch.object(self.module, "git", return_value=(True, "")):
            code, summary = self.module.run_pass(repo="o/r")
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "error")
        rows = self.exception_rows()
        self.assertEqual([r["kind"] for r in rows], ["subprocess_timeout"])
        self.assertIn("timeout", rows[0]["detail"].lower())

    def test_timeout_restores_the_working_tree(self):
        git_calls = []

        def hang(*args):
            raise subprocess.TimeoutExpired(cmd=["gh"] + list(args), timeout=60)

        def tracking_git(*args):
            git_calls.append(args)
            return (True, "")

        with patch.object(self.module, "gh",
                          side_effect=self._preconditions_ok(hang)), \
             patch.object(self.module, "git", side_effect=tracking_git):
            self.module.run_pass(repo="o/r")
        self.assertIn(("checkout", "main"), git_calls,
                      "a timed-out pass must not leave the tree off main")

    def test_timeout_releases_the_lock(self):
        def hang(*args):
            raise subprocess.TimeoutExpired(cmd=["gh"] + list(args), timeout=60)

        with patch.object(self.module, "gh",
                          side_effect=self._preconditions_ok(hang)), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.run_pass(repo="o/r")
        self.assertFalse((self.state_root / self.module.LOCK_DIRNAME).exists())

    def test_a_timeout_during_restore_is_swallowed(self):
        """The containment path itself must never raise a second time."""
        def hang(*args):
            raise subprocess.TimeoutExpired(cmd=["gh"] + list(args), timeout=60)

        def hanging_git(*args):
            raise subprocess.TimeoutExpired(cmd=["git"] + list(args), timeout=120)

        with patch.object(self.module, "gh",
                          side_effect=self._preconditions_ok(hang)), \
             patch.object(self.module, "git", side_effect=hanging_git):
            code, summary = self.module.run_pass(repo="o/r")
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "error")

    def test_main_contains_a_timeout_from_preconditions(self):
        """A hang before the lock is taken exits nonzero, not by traceback."""
        def hang(*args):
            raise subprocess.TimeoutExpired(cmd=["gh"] + list(args), timeout=60)

        with patch.object(self.module, "gh", side_effect=hang), \
             patch.object(self.module, "git", return_value=(True, "")):
            code = self.module.main(["--advance", "--repo", "o/r"])
        self.assertEqual(code, 1)
        self.assertEqual([r["kind"] for r in self.exception_rows()],
                         ["subprocess_timeout"])


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

    def _batch_pr(self, rollup=None, members="#11, #12", created_at=None):
        return {
            "number": 900, "state": "OPEN", "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": rollup if rollup is not None
            else green_rollup(self.module),
            "baseRefName": "main",
            "headRefName": "integrate/q-1700000000", "headRefOid": "batchsha",
            "labels": [{"name": self.module.BATCH_LABEL}],
            "body": "Members: %s\n" % members,
            "url": "https://x/pull/900",
            # Old enough that the grace window never applies unless a test
            # deliberately asks for a young batch.
            "createdAt": created_at or "2020-01-01T00:00:00Z",
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

    def _absent_windows_rollup(self):
        """Every required context present EXCEPT the `windows` aggregator.

        This is the real shape seconds after `gh pr create`: GitHub creates
        check runs asynchronously and the aggregator only appears once its
        shards exist.
        """
        return [check_run(name) for name in self.module.EXPECTED_REQUIRED_CHECKS
                if name != "windows"]

    def _young(self, seconds_ago=60):
        from datetime import datetime, timedelta, timezone as tz
        return (datetime.now(tz.utc)
                - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _view(self, batch, gh_calls):
        def gh_side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                if number == 900:
                    return batch
                return {"number": number, "state": "OPEN",
                        "statusCheckRollup": green_rollup(self.module)}
            return {}
        return gh_side_effect

    def test_young_batch_missing_a_context_waits_instead_of_dissolving(self):
        """Regression: the 2026-08-03 rebatch loop that merged nothing.

        A batch evaluated seconds after creation is missing `windows` only
        because the check run does not exist yet. Dissolving on that absence
        evicts every member and the next pass rebuilds the same batch, forever.
        """
        gh_calls = []
        batch = self._batch_pr(rollup=self._absent_windows_rollup(),
                               created_at=self._young(60))
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._view(batch, gh_calls)), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.handle_batch_pr({"number": 900}, summary)

        self.assertEqual(self.exception_rows(), [])
        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "close")], [])
        rejected = [c for c in gh_calls
                    if c[:2] == ("pr", "edit") and self.module.REJECT_LABEL in c]
        self.assertEqual(rejected, [])
        self.assertTrue(any("not created yet" in a for a in summary["actions"]),
                        summary["actions"])

    def test_old_batch_missing_a_context_still_dissolves(self):
        """The grace window is a delay, not an amnesty: absence stays red."""
        gh_calls = []
        batch = self._batch_pr(rollup=self._absent_windows_rollup(),
                               created_at="2020-01-01T00:00:00Z")
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._view(batch, gh_calls)), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.handle_batch_pr({"number": 900}, summary)

        self.assertEqual({r["kind"] for r in self.exception_rows()},
                         {"batch_red_dissolved"})
        self.assertEqual([int(c[2]) for c in gh_calls
                          if c[:2] == ("pr", "close")], [900])

    def test_young_batch_with_a_real_failure_dissolves_immediately(self):
        """A concluded FAILURE is positive evidence; youth buys it nothing."""
        gh_calls = []
        rollup = self._absent_windows_rollup()
        rollup[0] = check_run(self.module.EXPECTED_REQUIRED_CHECKS[0],
                              conclusion="FAILURE", url="http://run/batch")
        batch = self._batch_pr(rollup=rollup, created_at=self._young(5))
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._view(batch, gh_calls)), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.handle_batch_pr({"number": 900}, summary)

        self.assertEqual({r["kind"] for r in self.exception_rows()},
                         {"batch_red_dissolved"})
        self.assertEqual([int(c[2]) for c in gh_calls
                          if c[:2] == ("pr", "close")], [900])

    def test_young_batch_with_unknown_creation_time_dissolves(self):
        """An unreadable timestamp must not buy an indefinite grace period."""
        gh_calls = []
        batch = self._batch_pr(rollup=self._absent_windows_rollup())
        batch.pop("createdAt")
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._view(batch, gh_calls)), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.handle_batch_pr({"number": 900}, summary)

        self.assertEqual({r["kind"] for r in self.exception_rows()},
                         {"batch_red_dissolved"})

    def test_grace_never_makes_an_incomplete_rollup_mergeable(self):
        """The waiting path must never merge: absent is still never green."""
        gh_calls = []
        batch = self._batch_pr(rollup=self._absent_windows_rollup(),
                               created_at=self._young(1))
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._view(batch, gh_calls)), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.handle_batch_pr({"number": 900}, summary)
        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "merge")], [])
        self.assertEqual(summary["merged"], [])
        # And the underlying verdict is unchanged -- still not green.
        verdict, _, _ = self.module.required_checks_green(
            self._absent_windows_rollup())
        self.assertEqual(verdict, "not_green")

    def test_pr_fields_requests_created_at(self):
        """The grace check is inert unless createdAt is actually fetched."""
        self.assertIn("createdAt", self.module.PR_FIELDS)

    def test_unparseable_members_is_exception_rowed_not_merged(self):
        """Branch is alive but neither the body nor its commits name members."""
        gh_calls = []
        batch = self._batch_pr()
        batch["body"] = "no members line"

        def gh_side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "view"):
                return batch
            return {}

        def git_side_effect(*args):
            if args[0] == "ls-remote":
                return (True, "batchsha\trefs/heads/integrate/q-1700000000")
            return (True, "")

        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
             patch.object(self.module, "git", side_effect=git_side_effect):
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

    def test_two_reclaimers_cannot_both_win(self):
        """A2: interleaved stale reclaim must produce exactly ONE lock holder.

        The old sequence was `shutil.rmtree(stale)` then `os.mkdir()`. Those are
        two steps, so pass B could reclaim and claim in the window between A's
        rmtree decision and A's rmtree call -- and then A's rmtree deleted B's
        brand-new FRESH lock and A claimed it too. Two advancers then ran
        concurrently against the same queue, which is the observed #727/#728
        double-batch shape.

        The interleave is forced deterministically rather than raced: B runs to
        completion from inside the exact instant A is about to take the stale
        lock away, which is the only window the bug lives in.
        """
        module = self.module
        lock = self.state_root / module.LOCK_DIRNAME
        lock.mkdir(parents=True)
        (lock / "timestamp").write_text("1", encoding="utf-8")  # epoch 1 = ancient
        (lock / "pid").write_text("999999", encoding="utf-8")

        results = []
        state = {"reentered": False}
        real_rmtree, real_rename = module.shutil.rmtree, module.os.rename

        def reenter_once():
            if state["reentered"]:
                return
            state["reentered"] = True
            results.append(module.acquire_lock(lock, stale_s=60))

        def rmtree_hook(path, *a, **kw):
            if str(path).startswith(str(lock)):
                reenter_once()
            return real_rmtree(path, *a, **kw)

        def rename_hook(src, dst, *a, **kw):
            if str(src) == str(lock):
                reenter_once()
            return real_rename(src, dst, *a, **kw)

        with patch.object(module.shutil, "rmtree", side_effect=rmtree_hook), \
             patch.object(module.os, "rename", side_effect=rename_hook):
            results.append(module.acquire_lock(lock, stale_s=60))

        self.assertTrue(state["reentered"], "the interleave never happened")
        self.assertEqual(
            results.count(True), 1,
            "exactly one pass may hold the advancer lock, got %s" % (results,))

    def test_reclaim_never_deletes_a_fresh_lock(self):
        """After an interleaved reclaim the surviving lock is a live one."""
        module = self.module
        lock = self.state_root / module.LOCK_DIRNAME
        lock.mkdir(parents=True)
        (lock / "timestamp").write_text("1", encoding="utf-8")
        (lock / "pid").write_text("999999", encoding="utf-8")

        state = {"reentered": False}
        real_rename = module.os.rename

        def rename_hook(src, dst, *a, **kw):
            if str(src) == str(lock) and not state["reentered"]:
                state["reentered"] = True
                module.acquire_lock(lock, stale_s=60)
            return real_rename(src, dst, *a, **kw)

        with patch.object(module.os, "rename", side_effect=rename_hook):
            module.acquire_lock(lock, stale_s=60)

        self.assertTrue(lock.exists(), "the winner's lock was deleted")
        self.assertTrue((lock / "pid").exists(), "the surviving lock has no owner")

    def test_no_stale_graveyard_directories_are_left_behind(self):
        """Reclaim must not litter the state dir with orphaned lock copies."""
        module = self.module
        lock = self.state_root / module.LOCK_DIRNAME
        lock.mkdir(parents=True)
        (lock / "timestamp").write_text("1", encoding="utf-8")
        self.assertTrue(module.acquire_lock(lock, stale_s=60))
        siblings = [p.name for p in self.state_root.iterdir()
                    if p.is_dir() and p.name != module.LOCK_DIRNAME]
        self.assertEqual(siblings, [], "reclaim left %s behind" % siblings)

    def test_lock_contention_exits_cleanly(self):
        lock = self.state_root / self.module.LOCK_DIRNAME
        self.assertTrue(self.module.acquire_lock(lock))
        contexts = list(self.module.EXPECTED_REQUIRED_CHECKS)

        def gh_side_effect(*args):
            if "--jq" in args and ".default_branch" in args:
                return "main"
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
            if "--jq" in args and ".default_branch" in args:
                return "main"
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
            if "--jq" in args and ".default_branch" in args:
                return "main"
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
            if "--jq" in args and ".default_branch" in args:
                return "main"
            if "--jq" in args and ".enforce_admins.enabled" in args:
                return True
            if "--jq" in args and ".required_status_checks.contexts" in args:
                return contexts
            if args[:2] == ("pr", "list"):
                if ("--label" in args
                        and args[args.index("--label") + 1]
                        == self.module.BATCH_LABEL):
                    return []
                return [{"number": 55, "title": "t", "labels": [],
                         "body": "", "baseRefName": "main",
                         "headRefName": "feat/x"}]
            if args[:2] == ("pr", "view") and "files" in args:
                return {"files": [{"path": "tools/x.py"}]}
            if args[:2] == ("pr", "view"):
                return {"number": 55, "state": "OPEN", "mergeable": "MERGEABLE",
                        "mergeStateStatus": "BLOCKED",
                        "statusCheckRollup": rollup,
                        "baseRefName": "main",
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
            if "--jq" in args and ".default_branch" in args:
                return "main"
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


# ---------------------------------------------------------------------------
# Regression: the first real-world defect (duplicate batching)
# ---------------------------------------------------------------------------

# Verbatim body of the real batch PR #727 (matt82198/aesop, 2026-08-02).
REAL_BATCH_BODY = (
    "Merge-queue batch built by tools/merge_queue.py.\n\n"
    "Members: #717, #689, #696, #702, #712, #716, #723\n\n"
    "Members are closed only after `git merge-base --is-ancestor` proves "
    "their content landed on main."
)
REAL_BATCH_BRANCH = "integrate/q-1785727927"
REAL_MEMBERS = [717, 689, 696, 702, 712, 716, 723]


class TestBatchDiscoveryAndDedupe(StateIsolatedTestCase):
    """A pass must recognise a batch that is ALREADY open and never rebatch it.

    Measured defect (2026-08-02): two consecutive `--advance` passes each opened
    a NEW integration batch (#727 q-1785727927, then #728 q-1785729091) over the
    SAME seven members. Root cause: the `merge-queue-batch` LABEL did not exist
    in the repository, so `gh pr edit --add-label` failed silently at batch
    creation and `gh pr list --label merge-queue-batch` returned `[]` with exit 0
    on every later pass -- batch discovery was blind, and the members (still
    carrying `merge-queue`) were admitted and batched all over again.

    Discovery therefore must NOT rest on a label alone. The integration BRANCH
    name is minted by this module and cannot silently fail, so it is the
    authoritative signal; the label is a convenience on top of it.
    """

    def _batch_pr_row(self, number=727, branch=REAL_BATCH_BRANCH,
                      body=REAL_BATCH_BODY, labels=None):
        """A batch PR as it appears in a `gh pr list` payload."""
        return {"number": number, "title": "merge-queue batch q-1785727927",
                "labels": labels if labels is not None else [],
                "body": body, "baseRefName": "main",
                "headRefName": branch}

    def _queue_rows(self):
        """The seven queued members, each file-disjoint, all `merge-queue`."""
        return [{"number": n, "title": "lane %d" % n,
                 "labels": [{"name": self.module.QUEUE_LABEL}],
                 "body": "", "baseRefName": "main",
                 "headRefName": "feat/%d" % n}
                for n in REAL_MEMBERS]

    def _gh(self, calls, batch_rows, queue_rows, batch_rollup=None):
        """gh fake reproducing the exact production responses of that pass.

        `gh pr list --label merge-queue-batch` answers `[]` (the label does not
        exist in the repo) while the label-less open-PR list still shows the
        batch -- which is precisely the state the two duplicate passes saw.
        """
        module = self.module
        rollup = batch_rollup

        def side_effect(*args):
            calls.append(args)
            if "--jq" in args and ".default_branch" in args:
                return "main"
            if "--jq" in args and ".enforce_admins.enabled" in args:
                return True
            if "--jq" in args and ".required_status_checks.contexts" in args:
                return list(module.EXPECTED_REQUIRED_CHECKS)
            if args[:2] == ("pr", "list"):
                if "--label" in args:
                    label = args[args.index("--label") + 1]
                    if label == module.BATCH_LABEL:
                        return []
                    return list(queue_rows)
                return list(queue_rows) + list(batch_rows)
            if args[:2] == ("pr", "view") and "files" in args:
                return {"files": [{"path": "lane/%s.py" % args[2]}]}
            if args[:2] == ("pr", "view") and "--jq" in args:
                return "MERGED"
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                if number == 727:
                    row = self._batch_pr_row()
                    row.update({
                        "state": "OPEN", "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN", "headRefOid": "batchsha",
                        "statusCheckRollup": (rollup if rollup is not None
                                              else pending_rollup(module)),
                        "url": "https://x/pull/727"})
                    return row
                return {"number": number, "state": "OPEN",
                        "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                        "statusCheckRollup": green_rollup(module),
                        "baseRefName": "main",
                        "headRefName": "feat/%d" % number,
                        "headRefOid": "sha%d" % number,
                        "labels": [{"name": module.QUEUE_LABEL}],
                        "body": "", "url": "https://x/pull/%d" % number}
            return ""
        return side_effect

    @staticmethod
    def _git(calls):
        def side_effect(*args):
            calls.append(args)
            if args[0] == "status":
                return (True, "")
            if args[0] == "rev-parse":
                return (True, "main")
            if args[0] == "ls-remote":
                return (True, "batchsha\trefs/heads/%s" % args[-1])
            return (True, "")
        return side_effect

    def test_second_pass_does_not_open_a_duplicate_batch(self):
        """THE REGRESSION: pass 2 over pass 1's state must create no PR."""
        gh_calls, git_calls = [], []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(gh_calls, [self._batch_pr_row()],
                                               self._queue_rows())), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            code, summary = self.module.run_pass(repo="o/r")

        self.assertEqual(code, 0)
        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "create")], [],
                         "an already-open batch must never be rebatched")
        self.assertEqual([c for c in git_calls if c[0] == "checkout"], [],
                         "no integration branch may be built while one is open")
        self.assertEqual(summary["admitted"], [])

    def test_open_batch_is_discovered_by_branch_when_the_label_is_absent(self):
        gh_calls = []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(gh_calls, [self._batch_pr_row()],
                                               self._queue_rows())):
            batches = self.module.list_open_batches()
        self.assertEqual([b["number"] for b in batches], [727])

    def test_labelled_and_branch_discovery_do_not_double_count(self):
        gh_calls = []
        labelled = self._batch_pr_row(
            labels=[{"name": self.module.BATCH_LABEL}])

        def side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "list"):
                if "--label" in args and \
                        args[args.index("--label") + 1] == self.module.BATCH_LABEL:
                    return [labelled]
                return [labelled]
            return ""

        with patch.object(self.module, "gh", side_effect=side_effect):
            batches = self.module.list_open_batches()
        self.assertEqual([b["number"] for b in batches], [727])

    def test_batched_members_are_excluded_from_this_pass(self):
        gh_calls, git_calls = [], []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(gh_calls, [self._batch_pr_row()],
                                               self._queue_rows())), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            _, summary = self.module.run_pass(repo="o/r")
        self.assertEqual(summary["batched_members"], sorted(REAL_MEMBERS))

    def test_a_closed_batch_lets_its_members_re_enter_the_queue(self):
        """#728 was closed by hand: its members must simply requeue."""
        gh_calls, git_calls = [], []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(gh_calls, [],
                                               self._queue_rows())), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            _, summary = self.module.run_pass(repo="o/r")
        self.assertEqual(summary["batched_members"], [])
        self.assertEqual(summary["admitted"], sorted(REAL_MEMBERS))
        self.assertTrue([c for c in gh_calls if c[:2] == ("pr", "create")],
                        "with no batch open the members must batch normally")

    def test_a_queued_pr_on_an_integration_branch_is_never_requeued(self):
        """Belt and braces: a batch branch can never enter the member queue."""
        gh_calls, git_calls = [], []
        queue = self._queue_rows() + [
            {"number": 727, "title": "batch", "labels":
                [{"name": self.module.QUEUE_LABEL}], "body": REAL_BATCH_BODY,
             "baseRefName": "main",
             "headRefName": REAL_BATCH_BRANCH}]
        with patch.object(self.module, "gh",
                          side_effect=self._gh(gh_calls, [], queue)), \
             patch.object(self.module, "git", side_effect=self._git(git_calls)):
            _, summary = self.module.run_pass(repo="o/r")
        self.assertNotIn(727, summary["admitted"])

    def test_is_batch_branch_matches_only_generated_names(self):
        self.assertTrue(self.module.is_batch_branch(REAL_BATCH_BRANCH))
        self.assertTrue(self.module.is_batch_branch("integrate/q-1"))
        for other in ("", "feat/x", "integrate/q-", "integrate/batch-1",
                      "xintegrate/q-1", "integrate/q-1x"):
            self.assertFalse(self.module.is_batch_branch(other), other)

    def test_members_fall_back_to_commit_messages_for_a_pre_fix_batch(self):
        """A batch opened before the body contract is still resolvable."""
        log = ("integrate #12 into integrate/q-1700000000\n"
               "integrate #11 into integrate/q-1700000000\n"
               "some unrelated subject\n")

        def git_side_effect(*args):
            if args[0] == "log":
                return (True, log)
            if args[0] == "ls-remote":
                return (True, "sha\trefs/heads/integrate/q-1700000000")
            return (True, "")

        batch = {"number": 900, "body": "no members line here",
                 "baseRefName": "main",
                 "headRefName": "integrate/q-1700000000"}
        with patch.object(self.module, "git", side_effect=git_side_effect):
            members = self.module.resolve_batch_members(batch)
        self.assertEqual(sorted(members), [11, 12])

    def test_body_members_win_over_the_commit_fallback(self):
        git_calls = []

        def git_side_effect(*args):
            git_calls.append(args)
            return (True, "integrate #99 into integrate/q-1\n")

        batch = {"number": 727, "body": REAL_BATCH_BODY,
                 "baseRefName": "main",
                 "headRefName": REAL_BATCH_BRANCH}
        with patch.object(self.module, "git", side_effect=git_side_effect):
            members = self.module.resolve_batch_members(batch)
        self.assertEqual(members, REAL_MEMBERS)
        self.assertEqual(git_calls, [], "a parseable body needs no git read")

    def test_deleted_batch_branch_is_an_exception_row_not_a_rebatch(self):
        """Stale batch: report it, never dissolve and never rebuild blindly."""
        gh_calls = []
        batch = {"number": 900, "state": "OPEN", "mergeable": "MERGEABLE",
                 "mergeStateStatus": "CLEAN",
                 "statusCheckRollup": green_rollup(self.module),
                 "baseRefName": "main",
                 "headRefName": "integrate/q-1700000000",
                 "headRefOid": "batchsha", "labels": [],
                 "body": "no members line", "url": "https://x/pull/900"}

        def gh_side_effect(*args):
            gh_calls.append(args)
            if args[:2] == ("pr", "view"):
                return batch
            return {}

        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
             patch.object(self.module, "git", return_value=(True, "")):
            self.module.handle_batch_pr({"number": 900}, summary)

        self.assertEqual([r["kind"] for r in self.exception_rows()],
                         ["batch_branch_missing"])
        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "merge")], [])
        self.assertEqual([c for c in gh_calls if c[:2] == ("pr", "close")], [])


class TestBatchLabelIsVerified(StateIsolatedTestCase):
    """The silent `--add-label` failure that started all of this."""

    def _gh(self, calls, label_exists=True, creatable=True):
        module = self.module
        state = {"exists": label_exists, "labels": set()}

        def side_effect(*args):
            calls.append(args)
            if args[:2] == ("pr", "edit") and "--add-label" in args:
                name = args[args.index("--add-label") + 1]
                if name != module.BATCH_LABEL or state["exists"]:
                    state["labels"].add(name)
                    return ""
                return {"error": "could not add label: '%s' not found" % name}
            if args[:2] == ("label", "create"):
                if not creatable:
                    return {"error": "HTTP 403"}
                state["exists"] = True
                return ""
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                return {"headRefOid": "sha%d" % number,
                        "baseRefName": "main",
                        "headRefName": "feat/%d" % number,
                        "title": "pr %d" % number,
                        "labels": [{"name": n} for n in sorted(state["labels"])]}
            if args[:2] == ("pr", "create"):
                return "https://github.com/o/r/pull/900"
            return {}
        return side_effect

    @staticmethod
    def _git(*_args, **_kwargs):
        def side_effect(*args):
            if args[0] == "status":
                return (True, "")
            if args[0] == "rev-parse":
                return (True, "main")
            return (True, "")
        return side_effect

    def test_missing_label_is_created_then_reapplied(self):
        calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh",
                          side_effect=self._gh(calls, label_exists=False)), \
             patch.object(self.module, "git", side_effect=self._git()):
            self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertTrue([c for c in calls if c[:2] == ("label", "create")],
                        "a missing batch label must be created, not ignored")
        self.assertEqual(self.exception_rows(), [])
        self.assertEqual(summary["status"], "ok")

    def test_unlabelable_batch_is_exception_rowed(self):
        calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh",
                          side_effect=self._gh(calls, label_exists=False,
                                               creatable=False)), \
             patch.object(self.module, "git", side_effect=self._git()):
            self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual([r["kind"] for r in self.exception_rows()],
                         ["batch_label_failed"])

    def test_label_creation_is_never_forced(self):
        calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh",
                          side_effect=self._gh(calls, label_exists=False)), \
             patch.object(self.module, "git", side_effect=self._git()):
            self.module.build_batch([11, 12], summary, epoch=1700000000)
        flat = " ".join(" ".join(str(a) for a in c) for c in calls)
        self.assertNotIn("--force", flat)


class TestGeneratedFileTolerance(StateIsolatedTestCase):
    """Dirty-tree bug #2: a gate that REGENERATES a tracked file blocked a pass.

    `tools/verify_test_suite_count.py --check` auto-corrects the count lines in
    `tests/CLAUDE.md` and writes the file. When that gate ran in the scheduled
    task's project root, the advancer's next pass saw a dirty tree and refused
    to build a batch -- a self-inflicted stall over a file the repo generates.

    The pass now restores THOSE EXACT paths and nothing else. Never a stash
    (`git stash` is shared across worktrees and would eat a human's WIP), never
    a blanket `git checkout .`.
    """

    def _gh(self):
        def side_effect(*args):
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                return {"headRefOid": "sha%d" % number,
                        "baseRefName": "main",
                        "headRefName": "feat/%d" % number,
                        "title": "pr %d" % number,
                        "labels": [{"name": self.module.BATCH_LABEL}]}
            if args[:2] == ("pr", "create"):
                return "https://github.com/o/r/pull/900"
            return {}
        return side_effect

    def _git(self, calls, dirty):
        """git fake whose `status` reports `dirty` until those paths restore."""
        remaining = {"paths": list(dirty)}

        def side_effect(*args):
            calls.append(args)
            if args[0] == "status":
                return (True, "\n".join(" M %s" % p
                                        for p in remaining["paths"]))
            if args[0] == "restore":
                target = args[-1]
                remaining["paths"] = [p for p in remaining["paths"]
                                      if p != target]
                return (True, "")
            if args[0] == "rev-parse":
                return (True, "main")
            return (True, "")
        return side_effect

    def test_generated_file_dirt_is_restored_and_the_build_proceeds(self):
        calls = []
        with patch.object(self.module, "git",
                          side_effect=self._git(calls, ["tests/CLAUDE.md"])):
            safe, why = self.module.worktree_is_safe()
        self.assertTrue(safe, why)
        restores = [c for c in calls if c[0] == "restore"]
        self.assertEqual([c[-1] for c in restores], ["tests/CLAUDE.md"])

    def test_restore_touches_only_the_generated_path(self):
        calls = []
        dirty = ["tests/CLAUDE.md", "tools/CLAUDE.md"]
        with patch.object(self.module, "git",
                          side_effect=self._git(calls, dirty)):
            safe, _ = self.module.worktree_is_safe()
        self.assertTrue(safe)
        self.assertEqual(sorted(c[-1] for c in calls if c[0] == "restore"),
                         sorted(dirty))

    def test_non_generated_dirt_still_refuses_and_restores_nothing(self):
        calls = []
        with patch.object(self.module, "git",
                          side_effect=self._git(calls, ["tools/merge_queue.py"])):
            safe, why = self.module.worktree_is_safe()
        self.assertFalse(safe)
        self.assertIn("dirty", why)
        self.assertEqual([c for c in calls if c[0] == "restore"], [])

    def test_mixed_dirt_refuses_wholesale(self):
        """One non-generated edit poisons the whole tree -- restore nothing."""
        calls = []
        dirty = ["tests/CLAUDE.md", "ui/app.tsx"]
        with patch.object(self.module, "git",
                          side_effect=self._git(calls, dirty)):
            safe, _ = self.module.worktree_is_safe()
        self.assertFalse(safe)
        self.assertEqual([c for c in calls if c[0] == "restore"], [])

    def test_restore_that_fails_to_clean_the_tree_still_refuses(self):
        def stubborn_git(*args):
            if args[0] == "status":
                return (True, " M tests/CLAUDE.md")
            if args[0] == "rev-parse":
                return (True, "main")
            return (True, "")

        with patch.object(self.module, "git", side_effect=stubborn_git):
            safe, why = self.module.worktree_is_safe()
        self.assertFalse(safe)
        self.assertIn("dirty", why)

    def test_wrong_branch_is_rejected_before_any_restore(self):
        """Never touch files in a tree a human has checked out elsewhere."""
        calls = []

        def other_branch_git(*args):
            calls.append(args)
            if args[0] == "status":
                return (True, " M tests/CLAUDE.md")
            if args[0] == "rev-parse":
                return (True, "feat/someones-work")
            return (True, "")

        with patch.object(self.module, "git", side_effect=other_branch_git):
            safe, why = self.module.worktree_is_safe()
        self.assertFalse(safe)
        self.assertIn("feat/someones-work", why)
        self.assertEqual([c for c in calls if c[0] == "restore"], [])

    def test_dirty_generated_file_no_longer_blocks_a_batch(self):
        """End to end: the gate's own output must not stall the advancer."""
        git_calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._gh()), \
             patch.object(self.module, "git",
                          side_effect=self._git(git_calls, ["tests/CLAUDE.md"])):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "integrate/q-1700000000")
        self.assertEqual(self.exception_rows(), [])

    def test_status_parsing_handles_rename_and_untracked_entries(self):
        parse = self.module.dirty_paths
        self.assertEqual(parse(" M tests/CLAUDE.md"), ["tests/CLAUDE.md"])
        self.assertEqual(parse("?? tools/CLAUDE.md"), ["tools/CLAUDE.md"])
        self.assertEqual(parse("R  old.py -> new.py"), ["new.py"])
        self.assertEqual(parse('?? "sp ace.py"'), ["sp ace.py"])
        self.assertEqual(parse(""), [])

    def test_status_parsing_survives_the_stripped_leading_column(self):
        """`git()` strips output, so ' M path' arrives as 'M path'.

        Regression: dirty_paths sliced line[3:], which on the stripped form ate
        the first character of the path ('ests/CLAUDE.md'). Every registered
        generated file then read as an unregistered edit, worktree_is_safe
        answered 'working tree is dirty' forever, and no batch could ever be
        built -- the 2026-08-03 board jam.
        """
        parse = self.module.dirty_paths
        for stripped, raw in (("M tests/CLAUDE.md", " M tests/CLAUDE.md"),
                              ("D gone.txt", " D gone.txt"),
                              ("A x.py", " A x.py")):
            self.assertEqual(parse(stripped), parse(raw))
        self.assertEqual(parse("M tests/CLAUDE.md"), ["tests/CLAUDE.md"])
        self.assertEqual(parse("MM tests/CLAUDE.md"), ["tests/CLAUDE.md"])
        self.assertEqual(parse("UU conflict.py"), ["conflict.py"])
        # A line that is not a porcelain row yields nothing, never a mis-slice.
        self.assertEqual(parse("not a porcelain line"), [])

    def test_worktree_is_safe_tolerates_a_stripped_generated_path(self):
        """The end-to-end effect of the slice bug: a batch is buildable again."""
        def fake_git(*args):
            if args[0] == "rev-parse":
                return (True, "main")
            if args[0] == "status":
                # Post-restore status is clean; pre-restore is the stripped form.
                return (True, "" if fake_git.restored else "M tests/CLAUDE.md")
            if args[0] == "restore":
                fake_git.restored = True
                return (True, "")
            return (True, "")
        fake_git.restored = False
        with patch.object(self.module, "git", side_effect=fake_git):
            safe, why = self.module.worktree_is_safe()
        self.assertTrue(safe, why)
        self.assertTrue(fake_git.restored)

    def test_generated_registry_is_shared_not_duplicated(self):
        """tools/generated_paths.py is the single registry; nothing re-lists."""
        registry = load_generated_paths()
        self.assertEqual(tuple(self.module.GENERATED_PATHS),
                         tuple(registry.GENERATED_PATHS))
        self.assertIn("tests/CLAUDE.md", registry.GENERATED_PATHS)

    def test_module_never_stashes(self):
        source = TOOL_PATH.read_text(encoding="utf-8")
        self.assertNotIn('"stash"', source)
        self.assertNotIn("'stash'", source)


class TestBatchRegeneration(StateIsolatedTestCase):
    """A batch's union must not fail a drift gate every member passed.

    Two members can each add a test file: the suite counts in tests/CLAUDE.md
    are right on both branches and wrong on their union, so the pre-push hook
    fail-closes with '[DRIFT] Test suite count mismatch' and the queue stalls
    holding a branch it can never publish. The generators run on the batch
    branch, before the push, and only registry paths are ever committed.
    """

    def _git(self, calls, status_after_regen=""):
        """The tree is clean at the safety check and drifts only once members
        have been merged -- which is exactly when a batch's union diverges."""
        state = {"merged": False}

        def side_effect(*args):
            calls.append(args)
            if args[0] == "merge" and "--abort" not in args:
                state["merged"] = True
            if args[0] == "status":
                return (True, status_after_regen if state["merged"] else "")
            if args[0] == "rev-parse":
                return (True, "main")
            if args[0] == "commit":
                state["merged"] = False  # committing cleans the tree
            return (True, "")
        return side_effect

    def _gh(self):
        def side_effect(*args, **kwargs):
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                return {"headRefOid": "sha%d" % number,
                        "baseRefName": "main",
                        "headRefName": "feat/%d" % number,
                        "title": "pr %d" % number,
                        "labels": [{"name": "merge-queue-batch"}]}
            if args[:2] == ("pr", "create"):
                return "https://github.com/o/r/pull/900"
            return {}
        return side_effect

    def test_drifted_counts_are_regenerated_and_committed_before_push(self):
        calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._gh()), \
             patch.object(self.module, "git",
                          side_effect=self._git(calls, "M tests/CLAUDE.md")), \
             patch.object(self.module, "run_regenerator", return_value=(True, "")):
            branch = self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertEqual(branch, "integrate/q-1700000000")
        verbs = [c[0] for c in calls]
        self.assertIn("commit", verbs)
        self.assertIn("push", verbs)
        # The regenerated file is committed BEFORE the branch is pushed.
        self.assertLess(verbs.index("commit"), verbs.index("push"))
        self.assertTrue(any(c[0] == "add" and "tests/CLAUDE.md" in c
                            for c in calls), calls)
        self.assertTrue(any("regenerated" in a for a in summary["actions"]),
                        summary["actions"])

    def test_clean_tree_after_regeneration_commits_nothing(self):
        calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._gh()), \
             patch.object(self.module, "git", side_effect=self._git(calls, "")), \
             patch.object(self.module, "run_regenerator", return_value=(True, "")):
            self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertNotIn("commit", [c[0] for c in calls])

    def test_regenerator_overreach_commits_nothing(self):
        """A generator writing an unregistered path must not be committed."""
        calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._gh()), \
             patch.object(self.module, "git",
                          side_effect=self._git(calls, "M tests/CLAUDE.md\nM src/app.py")), \
             patch.object(self.module, "run_regenerator", return_value=(True, "")):
            self.module.build_batch([11, 12], summary, epoch=1700000000)
        self.assertNotIn("commit", [c[0] for c in calls])
        kinds = [r["kind"] for r in self.exception_rows()]
        self.assertIn("regenerator_overreach", kinds)

    def test_regenerator_failure_is_rowed_and_does_not_commit(self):
        calls = []
        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=self._gh()), \
             patch.object(self.module, "git", side_effect=self._git(calls, "")), \
             patch.object(self.module, "run_regenerator",
                          return_value=(False, "boom")):
            self.module.build_batch([11, 12], summary, epoch=1700000000)
        kinds = [r["kind"] for r in self.exception_rows()]
        self.assertIn("regenerator_failed", kinds)
        self.assertNotIn("commit", [c[0] for c in calls])

    def test_every_regenerator_exists_and_accepts_its_flags(self):
        """Really invoke each generator: an unknown flag must not ship.

        The pre-push hook's failure text advises `--regenerate`, which the tool
        rejects with 'unrecognized arguments'. A registry naming a flag the
        script does not accept would fail silently on every batch, so the flag
        is proven against the real argument parser here, not assumed.
        """
        import subprocess
        for argv in self.module.REGENERATORS:
            script = TOOL_PATH.parent.parent / argv[0]
            self.assertTrue(script.exists(), "missing generator: %s" % argv[0])
            proc = subprocess.run(
                [sys.executable, str(script), "--help"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr[:300])
            for flag in argv[1:]:
                self.assertIn(flag, proc.stdout,
                              "%s does not accept %s" % (argv[0], flag))

    def test_run_regenerator_uses_sys_executable_with_encoding(self):
        source = TOOL_PATH.read_text(encoding="utf-8")
        marker = source.split("def run_regenerator", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("sys.executable", marker)
        self.assertIn('encoding="utf-8"', marker)
        self.assertIn("timeout=", marker)


class TestNonDefaultBaseIsRefused(StateIsolatedTestCase):
    """The stacked-PR guard: a PR whose base is not the trunk is never merged.

    Measured red (2026-08-03, against the pre-fix module): `list_queue`,
    `list_open_prs` and `PR_FIELDS` never requested `baseRefName` at all, so a
    PR based on `feat/parent` and labeled `merge-queue` reached
    `advance_singleton`, was found green + CLEAN, and was MERGED -- into its
    parent branch. `gh pr view --json state` answered MERGED, so the daemon's
    own merge verification, the load-bearing proof that a merge really happened,
    reported success for content that never reached the trunk.

      advance_singleton returned : True
      gh pr merge calls          : [('pr', 'merge', '101', '--merge')]
      summary['merged']          : [101]
      exception rows             : []

    A batch of >1 is worse: build_batch cuts `integrate/q-*` from the trunk and
    merges member HEADs onto it, so a stacked member grafts its parent's
    unmerged commits onto the trunk under a single green batch check.
    """

    TRUNK = "main"
    STACKED_BASE = "feat/parent"

    def _pr(self, number=101, base="main", state="OPEN", rollup=None,
            mergeable="MERGEABLE", merge_state="CLEAN"):
        return {
            "number": number, "title": "t", "state": state,
            "mergeable": mergeable, "mergeStateStatus": merge_state,
            "statusCheckRollup": rollup if rollup is not None
            else green_rollup(self.module),
            "headRefName": "feat/child", "baseRefName": base,
            "headRefOid": "abc123", "labels": [], "body": "",
            "url": "https://x/pull/%d" % number,
        }

    def _gh(self, view_payload, calls, list_payload=None):
        """gh fake: preconditions green, one PR in the queue, merge reports MERGED."""
        contexts = list(self.module.EXPECTED_REQUIRED_CHECKS)

        def side_effect(*args):
            calls.append(args)
            if "--jq" in args and ".default_branch" in args:
                return self.TRUNK
            if "--jq" in args and ".enforce_admins.enabled" in args:
                return True
            if "--jq" in args and ".required_status_checks.contexts" in args:
                return contexts
            if args[:2] == ("pr", "list"):
                if ("--label" in args
                        and args[args.index("--label") + 1]
                        == self.module.BATCH_LABEL):
                    return []
                return list_payload if list_payload is not None else []
            if args[:2] == ("pr", "view") and "state" in args and "--jq" in args:
                # GitHub genuinely reports MERGED for a PR merged into its
                # parent branch. This is the false success the guard prevents.
                return "MERGED"
            if args[:2] == ("pr", "view") and "files" in args:
                return {"files": [{"path": "tools/x.py"}]}
            if args[:2] == ("pr", "view"):
                return view_payload
            return ""
        return side_effect

    @staticmethod
    def merges(calls):
        return [c for c in calls if c[:2] == ("pr", "merge")]

    # -- the finding ------------------------------------------------------

    def test_stacked_pr_is_never_merged_by_the_singleton_path(self):
        """THE RED TEST. Pre-fix this merged #101 into feat/parent."""
        summary = {"actions": [], "merged": [], "status": "ok"}
        calls = []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(self._pr(base=self.STACKED_BASE),
                                               calls)):
            ok = self.module.advance_singleton(101, summary)

        self.assertFalse(ok)
        self.assertEqual(self.merges(calls), [],
                         "a PR based on a feature branch must never be merged")
        self.assertEqual(summary["merged"], [])
        rows = self.exception_rows()
        self.assertEqual([r["kind"] for r in rows], ["non_default_base"])
        self.assertIn(self.STACKED_BASE, rows[0]["detail"])

    def test_trunk_based_pr_still_merges(self):
        """The guard must not break the common case it wraps."""
        summary = {"actions": [], "merged": [], "status": "ok"}
        calls = []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(self._pr(base=self.TRUNK), calls)):
            ok = self.module.advance_singleton(101, summary)
        self.assertTrue(ok)
        self.assertEqual(len(self.merges(calls)), 1)
        self.assertEqual(self.exception_rows(), [])

    # -- root cause: the field was never fetched --------------------------

    def test_listings_and_views_request_baserefname(self):
        """The guard cannot work on a field the queries do not ask for."""
        self.assertIn("baseRefName", self.module.PR_FIELDS)
        self.assertIn("baseRefName", self.module.LIST_FIELDS)

        calls = []

        def side_effect(*args):
            calls.append(args)
            return []

        with patch.object(self.module, "gh", side_effect=side_effect):
            self.module.list_queue(self.module.QUEUE_LABEL)
            self.module.list_open_prs()
        for call in calls:
            self.assertIn("--json", call)
            self.assertIn("baseRefName", call[call.index("--json") + 1])

    # -- fail-closed on the unknown ---------------------------------------

    def test_absent_baserefname_fails_closed(self):
        payload = self._pr()
        payload.pop("baseRefName")
        summary = {"actions": [], "merged": [], "status": "ok"}
        calls = []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(payload, calls)):
            ok = self.module.advance_singleton(101, summary)
        self.assertFalse(ok)
        self.assertEqual(self.merges(calls), [])
        self.assertEqual([r["kind"] for r in self.exception_rows()],
                         ["non_default_base"])

    def test_empty_baserefname_fails_closed(self):
        summary = {"actions": [], "merged": [], "status": "ok"}
        calls = []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(self._pr(base=""), calls)):
            self.assertFalse(self.module.advance_singleton(101, summary))
        self.assertEqual(self.merges(calls), [])

    def test_predicate_is_fail_closed_on_junk(self):
        for payload in (None, {}, {"baseRefName": None}, {"baseRefName": "   "},
                        {"baseRefName": "mainline"}, {"baseRefName": "Main"}):
            ok, _ = self.module.base_is_integration_target(payload)
            self.assertFalse(ok, "must refuse %r" % (payload,))
        self.assertTrue(
            self.module.base_is_integration_target({"baseRefName": "main"})[0])

    # -- selection-time refusal -------------------------------------------

    def test_stacked_pr_is_filtered_out_of_the_queue(self):
        """A whole pass: the stacked PR is never admitted, never merged."""
        listing = [{"number": 101, "title": "t", "labels": [], "body": "",
                    "headRefName": "feat/child",
                    "baseRefName": self.STACKED_BASE}]
        calls = []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(self._pr(base=self.STACKED_BASE),
                                               calls, list_payload=listing)):
            code, summary = self.module.run_pass(repo="o/r")

        self.assertEqual(code, 0)
        self.assertEqual(summary["merged"], [])
        self.assertEqual(summary["admitted"], [])
        self.assertEqual(self.merges(calls), [])
        self.assertEqual([r["kind"] for r in self.exception_rows()],
                         ["non_default_base"])

    # -- the skip must NOT be sticky --------------------------------------

    def test_refusal_touches_no_label(self):
        """GitHub retargets stacked PRs on their own; a sticky skip would fight it.

        Removing `merge-queue` or applying `queue-rejected` would turn a
        temporary, self-healing condition into one needing a human re-queue.
        """
        listing = [{"number": 101, "title": "t", "labels": [], "body": "",
                    "headRefName": "feat/child",
                    "baseRefName": self.STACKED_BASE}]
        calls = []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(self._pr(base=self.STACKED_BASE),
                                               calls, list_payload=listing)):
            self.module.run_pass(repo="o/r")

        self.assertEqual([c for c in calls if c[:2] == ("pr", "edit")], [])
        flat = " ".join(" ".join(str(a) for a in c) for c in calls)
        self.assertNotIn(self.module.REJECT_LABEL, flat)
        self.assertNotIn("--remove-label", flat)

    def test_pr_retargeted_to_the_trunk_is_picked_up_on_a_later_pass(self):
        """Parent merged -> GitHub retargets to main -> the queue must take it."""
        stacked_listing = [{"number": 101, "title": "t", "labels": [], "body": "",
                            "headRefName": "feat/child",
                            "baseRefName": self.STACKED_BASE}]
        retargeted_listing = [dict(stacked_listing[0], baseRefName=self.TRUNK)]

        calls_a = []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(self._pr(base=self.STACKED_BASE),
                                               calls_a,
                                               list_payload=stacked_listing)):
            _, summary_a = self.module.run_pass(repo="o/r")
        self.assertEqual(summary_a["merged"], [])
        self.assertEqual(self.merges(calls_a), [])

        # GitHub has now retargeted #101 onto the trunk. Same state dir, same
        # labels, no human intervention.
        calls_b = []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(self._pr(base=self.TRUNK),
                                               calls_b,
                                               list_payload=retargeted_listing)):
            _, summary_b = self.module.run_pass(repo="o/r")

        self.assertEqual(summary_b["merged"], [101],
                         "a PR retargeted onto the trunk must merge without a "
                         "human re-queue -- the skip is not sticky")
        self.assertEqual(len(self.merges(calls_b)), 1)

    def test_repeated_refusal_appends_one_row(self):
        """Re-observing an unchanged stacked PR every 5 minutes is a no-op."""
        listing = [{"number": 101, "title": "t", "labels": [], "body": "",
                    "headRefName": "feat/child",
                    "baseRefName": self.STACKED_BASE}]
        for _ in range(3):
            with patch.object(self.module, "gh",
                              side_effect=self._gh(
                                  self._pr(base=self.STACKED_BASE), [],
                                  list_payload=listing)):
                self.module.run_pass(repo="o/r")
        self.assertEqual(len(self.exception_rows()), 1)

    # -- re-check at MERGE time, not only at selection ---------------------

    def test_base_changed_after_selection_does_not_slip_through(self):
        """Selected while based on the trunk, retargeted away before the merge.

        The listing is a separate, earlier read. Only the check on the payload
        `pr_view` returns immediately before `gh pr merge` can actually bind.
        """
        listing = [{"number": 101, "title": "t", "labels": [], "body": "",
                    "headRefName": "feat/child", "baseRefName": self.TRUNK}]
        calls = []
        with patch.object(self.module, "gh",
                          side_effect=self._gh(self._pr(base=self.STACKED_BASE),
                                               calls, list_payload=listing)):
            code, summary = self.module.run_pass(repo="o/r")

        self.assertEqual(summary["admitted"], [101], "selection saw the trunk")
        self.assertEqual(summary["merged"], [],
                         "the merge-time re-check must catch the change")
        self.assertEqual(self.merges(calls), [])
        rows = self.exception_rows()
        self.assertEqual([r["kind"] for r in rows], ["non_default_base"])
        self.assertIn("at merge time", rows[0]["detail"])

    def test_stacked_member_is_excluded_from_a_batch(self):
        """A stacked member would graft its parent's unmerged commits onto the trunk."""
        calls = []
        merged_shas = []

        def gh_side_effect(*args):
            calls.append(args)
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                base = self.STACKED_BASE if number == 102 else self.TRUNK
                return {"number": number, "headRefOid": "sha%d" % number,
                        "headRefName": "feat/%d" % number,
                        "baseRefName": base, "title": "t"}
            if args[:2] == ("pr", "create"):
                return "https://github.com/o/r/pull/999"
            return {}

        def git_side_effect(*args):
            if args[0] == "merge" and len(args) > 1 and args[1].startswith("sha"):
                merged_shas.append(args[1])
            if args[0] == "rev-parse":
                return True, "main"
            if args[0] == "status":
                return True, ""
            return True, ""

        summary = {"actions": [], "merged": [], "status": "ok", "batch": None}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
                patch.object(self.module, "git", side_effect=git_side_effect), \
                patch.object(self.module, "pr_has_label", return_value=True):
            self.module.build_batch([101, 102, 103], summary, epoch=1700000000)

        self.assertEqual(merged_shas, ["sha101", "sha103"],
                         "the stacked member's HEAD must never be grafted")
        self.assertEqual(summary["batch"]["members"], [101, 103])
        rows = self.exception_rows()
        self.assertEqual([r["kind"] for r in rows], ["non_default_base"])
        self.assertEqual(rows[0]["pr"], 102)

    def test_batch_pr_retargeted_away_from_the_trunk_is_not_merged(self):
        """A human can retarget the batch PR this module opened."""
        calls = []

        def gh_side_effect(*args):
            calls.append(args)
            if args[:2] == ("pr", "view") and "state" in args and "--jq" in args:
                return "MERGED"
            if args[:2] == ("pr", "view"):
                return {"number": 999, "state": "OPEN", "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                        "statusCheckRollup": green_rollup(self.module),
                        "headRefName": "integrate/q-1700000000",
                        "baseRefName": "release/0.8.0",
                        "headRefOid": "shabatch", "labels": [],
                        "body": "Members: #101, #102",
                        "url": "https://x/pull/999", "createdAt": None}
            return ""

        summary = {"actions": [], "merged": [], "status": "ok"}
        with patch.object(self.module, "gh", side_effect=gh_side_effect):
            self.module.handle_batch_pr({"number": 999}, summary)

        self.assertEqual(self.merges(calls), [])
        self.assertEqual(summary["merged"], [])
        self.assertEqual([r["kind"] for r in self.exception_rows()],
                         ["non_default_base"])
        # A retarget is a deliberate human act; refusing must not also destroy
        # the batch (which would discard seven members' worth of integration).
        self.assertEqual([c for c in calls if c[:2] == ("pr", "close")], [])


class TestDefaultBranchIsResolved(StateIsolatedTestCase):
    """Portability: the integration target is the repo's default branch.

    The daemon is a generic any-repo actor (`--repo OWNER/NAME`), and every ref
    it used was the literal `main`. Resolving the default branch is only sound
    if EVERY site reads the same value -- a guard that admits PRs targeting
    `master` while build_batch still cut `integrate/q-*` from `origin/main`
    would graft unrelated history onto the trunk, which is a worse bug than the
    one being fixed. So there is one resolver and one reader.
    """

    def test_default_branch_is_read_from_the_repository(self):
        with patch.object(self.module, "gh", return_value="develop"):
            ok, detail = self.module.resolve_base_branch("o/r")
        self.assertTrue(ok)
        self.assertIn("develop", detail)
        self.assertEqual(self.module.base_branch(), "develop")

    def test_unreadable_default_branch_fails_closed(self):
        with patch.object(self.module, "gh", return_value={"error": "404"}):
            ok, detail = self.module.resolve_base_branch("o/r")
        self.assertFalse(ok)
        self.assertIn("default branch", detail)

    def test_non_string_default_branch_fails_closed(self):
        for junk in (None, [], {}, 7, "", "   "):
            with patch.object(self.module, "gh", return_value=junk):
                self.assertFalse(self.module.resolve_base_branch("o/r")[0],
                                 "must refuse %r" % (junk,))

    def test_run_pass_exits_two_when_the_default_branch_is_unreadable(self):
        def side_effect(*args):
            if "--jq" in args and ".default_branch" in args:
                return {"error": "not found"}
            return True

        with patch.object(self.module, "gh", side_effect=side_effect):
            code, summary = self.module.run_pass(repo="o/r")
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "precondition_failed")

    def test_unresolved_default_branch_falls_back_to_main(self):
        """Direct unit calls that skip preconditions behave as they always did."""
        self.assertEqual(self.module.base_branch(),
                         self.module.DEFAULT_BASE_BRANCH)

    def test_every_ref_follows_the_resolved_branch(self):
        """One value, or none: guard, ancestor proof, cut and --base must agree."""
        git_calls, gh_calls = [], []

        def git_side_effect(*args):
            git_calls.append(args)
            if args[0] == "rev-parse":
                return True, "trunk"
            if args[0] == "status":
                return True, ""
            return True, ""

        def gh_side_effect(*args):
            gh_calls.append(args)
            if "--jq" in args and ".default_branch" in args:
                return "trunk"
            if args[:2] == ("pr", "view"):
                number = int(args[2])
                return {"number": number, "headRefOid": "sha%d" % number,
                        "headRefName": "feat/%d" % number,
                        "baseRefName": "trunk", "title": "t"}
            if args[:2] == ("pr", "create"):
                return "https://github.com/o/r/pull/999"
            return {}

        summary = {"actions": [], "merged": [], "status": "ok", "batch": None}
        with patch.object(self.module, "gh", side_effect=gh_side_effect), \
                patch.object(self.module, "git", side_effect=git_side_effect), \
                patch.object(self.module, "pr_has_label", return_value=True):
            self.module.resolve_base_branch("o/r")
            self.module.build_batch([101, 102], summary, epoch=1700000000)
            self.module.is_ancestor("deadbeef")

        self.assertIn(("checkout", "-B", "integrate/q-1700000000", "origin/trunk"),
                      git_calls)
        self.assertIn(("fetch", "origin", "trunk"), git_calls)
        self.assertIn(("merge-base", "--is-ancestor", "deadbeef", "origin/trunk"),
                      git_calls)
        create = [c for c in gh_calls if c[:2] == ("pr", "create")][0]
        self.assertEqual(create[create.index("--base") + 1], "trunk")
        # And the guard agrees with the branch that was actually cut.
        self.assertTrue(
            self.module.base_is_integration_target({"baseRefName": "trunk"})[0])
        self.assertFalse(
            self.module.base_is_integration_target({"baseRefName": "main"})[0])

    def test_no_hard_coded_trunk_ref_survives_in_executable_code(self):
        """Rules-as-code: a re-introduced literal would desync the guard.

        This is the guardrail for the class of bug fixed here -- a hard-coded
        `origin/main` alongside a resolved guard is how a base check silently
        stops describing the branch the module merges into.
        """
        code = TestForbiddenOperations.executable_source()
        for token in ('"origin/main"', "'origin/main'", 'branches/main/',
                      '"--base", "main"'):
            self.assertNotIn(token, code,
                             "%s must go through base_branch()" % token)
        main_literals = re.findall(r'(?<![\w/])"main"', code)
        self.assertEqual(
            len(main_literals), 1,
            'exactly one "main" literal is allowed in executable code '
            '(DEFAULT_BASE_BRANCH); found %d' % len(main_literals))


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
