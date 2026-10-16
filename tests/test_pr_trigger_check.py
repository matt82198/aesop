#!/usr/bin/env python3
"""Unit tests for tools/pr_trigger_check.py -- the read-only pull_request-run detector.

Hermetic: no network, no real `gh`. `pr_trigger_check.gh` is a module global
patched with a FakeGh dispatcher per test (same injection pattern as
tests/test_pr_sweep.py patches pr_sweep.gh).

Coverage mirrors the tool's contract (see tools/pr_trigger_check.py docstring):
  * a PR whose exact head sha has a pull_request run -> exit 0
  * a PR whose head sha has NO pull_request run (runs exist only for an older
    sha) -> exit 1 and the line says run=no
  * drafts and closed PRs never make the exit code 1
  * one `gh run list` per PR, filtered on the PR head branch
  * explicit PR numbers use `gh pr view`, the default uses one `gh pr list`
  * any gh failure -> exit 2 (fail closed)
  * read-only: no remediation gh subcommand is ever issued
"""
import importlib.util
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "pr_trigger_check.py"

HEAD_NEW = "8d925be6" + "0" * 32
HEAD_OLD = "1111aaaa" + "0" * 32


def load_module():
    spec = importlib.util.spec_from_file_location("pr_trigger_check_under_test", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_pr(number, *, head=HEAD_NEW, draft=False, state="OPEN"):
    return {
        "number": number,
        "state": state,
        "isDraft": draft,
        "headRefName": "feature-%d" % number,
        "headRefOid": head,
    }


def make_run(head, status="completed", conclusion="success"):
    return {"headSha": head, "status": status, "conclusion": conclusion}


class FakeGh:
    """Dispatches on the gh subcommand shape, recording every call.

    `runs_by_branch` maps a head branch name to the list of pull_request runs
    `gh run list --branch <branch>` returns (newest first, like gh does).
    `fail_on` maps a subcommand prefix tuple to an error message.
    """

    def __init__(self, prs=None, view_by_number=None, runs_by_branch=None, fail_on=None):
        self.prs = prs or []
        self.view_by_number = view_by_number or {}
        self.runs_by_branch = runs_by_branch or {}
        self.fail_on = fail_on or {}
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        for prefix, message in self.fail_on.items():
            if tuple(args[:len(prefix)]) == prefix:
                return {"error": message, "rc": 1}
        if args[:2] == ("pr", "list"):
            return list(self.prs)
        if args[:2] == ("pr", "view"):
            number = int(args[2])
            if number not in self.view_by_number:
                return {"error": "no pull requests found", "rc": 1}
            return self.view_by_number[number]
        if args[:2] == ("run", "list"):
            branch = args[args.index("--branch") + 1]
            return list(self.runs_by_branch.get(branch, []))
        raise AssertionError("unexpected gh call: %r" % (args,))

    def calls_with(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == prefix]


class PrTriggerCheckTestCase(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def run_check(self, fake, pr_numbers=None):
        argv = ["--repo", "acme/repo"] + [str(n) for n in (pr_numbers or [])]
        buf = io.StringIO()
        err = io.StringIO()
        with patch.object(self.module, "gh", fake), redirect_stdout(buf), redirect_stderr(err):
            rc = self.module.main(argv)
        return rc, buf.getvalue().splitlines()


class TestRunExistsForHead(PrTriggerCheckTestCase):
    def test_pr_with_pull_request_run_on_exact_head_exits_zero(self):
        fake = FakeGh(
            prs=[make_pr(10)],
            runs_by_branch={"feature-10": [make_run(HEAD_NEW, status="completed")]},
        )
        rc, lines = self.run_check(fake)
        self.assertEqual(rc, 0)
        self.assertEqual(len(lines), 1)
        self.assertIn("#10", lines[0])
        self.assertIn("head=" + HEAD_NEW, lines[0])
        self.assertIn("pull_request_run=yes", lines[0])
        self.assertIn("status=completed", lines[0])

    def test_run_only_on_older_head_does_not_count_and_exits_one(self):
        fake = FakeGh(
            prs=[make_pr(1072, head=HEAD_NEW)],
            runs_by_branch={"feature-1072": [make_run(HEAD_OLD, status="completed")]},
        )
        rc, lines = self.run_check(fake)
        self.assertEqual(rc, 1, "an open non-draft PR without a run on its head must fail")
        self.assertIn("#1072", lines[0])
        self.assertIn("head=" + HEAD_NEW, lines[0])
        self.assertIn("pull_request_run=no", lines[0])
        self.assertIn("status=none", lines[0])

    def test_no_runs_at_all_exits_one(self):
        fake = FakeGh(prs=[make_pr(7)], runs_by_branch={})
        rc, lines = self.run_check(fake)
        self.assertEqual(rc, 1)
        self.assertIn("pull_request_run=no", lines[0])

    def test_matching_run_found_among_older_runs_is_reported_yes(self):
        fake = FakeGh(
            prs=[make_pr(8)],
            runs_by_branch={"feature-8": [
                make_run(HEAD_OLD, status="completed"),
                make_run(HEAD_NEW, status="in_progress"),
            ]},
        )
        rc, lines = self.run_check(fake)
        self.assertEqual(rc, 0)
        self.assertIn("pull_request_run=yes", lines[0])
        self.assertIn("status=in_progress", lines[0])

    def test_mixed_prs_exit_one_when_any_is_missing_a_run(self):
        fake = FakeGh(
            prs=[make_pr(21), make_pr(22)],
            runs_by_branch={
                "feature-21": [make_run(HEAD_NEW)],
                "feature-22": [],
            },
        )
        rc, lines = self.run_check(fake)
        self.assertEqual(rc, 1)
        self.assertEqual(len(lines), 2)
        self.assertIn("pull_request_run=yes", lines[0])
        self.assertIn("pull_request_run=no", lines[1])


class TestNeverCountsDraftsOrClosed(PrTriggerCheckTestCase):
    def test_draft_pr_without_run_does_not_fail_the_check(self):
        fake = FakeGh(prs=[make_pr(30, draft=True)], runs_by_branch={})
        rc, lines = self.run_check(fake)
        self.assertEqual(rc, 0)
        self.assertIn("pull_request_run=no", lines[0])
        self.assertIn("draft=yes", lines[0])

    def test_closed_pr_named_explicitly_does_not_fail_the_check(self):
        fake = FakeGh(
            view_by_number={40: make_pr(40, state="CLOSED")},
            runs_by_branch={},
        )
        rc, lines = self.run_check(fake, pr_numbers=[40])
        self.assertEqual(rc, 0)
        self.assertIn("state=CLOSED", lines[0])


class TestCallShape(PrTriggerCheckTestCase):
    def test_one_run_list_per_pr_filtered_on_head_branch(self):
        fake = FakeGh(
            prs=[make_pr(51), make_pr(52)],
            runs_by_branch={"feature-51": [make_run(HEAD_NEW)],
                            "feature-52": [make_run(HEAD_NEW)]},
        )
        rc, _ = self.run_check(fake)
        self.assertEqual(rc, 0)
        run_calls = fake.calls_with("run", "list")
        self.assertEqual(len(run_calls), 2, "exactly one gh run list per PR")
        for call in run_calls:
            self.assertIn("--event", call)
            self.assertEqual(call[call.index("--event") + 1], "pull_request")
            self.assertIn("headSha", call[call.index("--json") + 1])
        branches = sorted(c[c.index("--branch") + 1] for c in run_calls)
        self.assertEqual(branches, ["feature-51", "feature-52"])

    def test_default_uses_single_pr_list_and_no_pr_view(self):
        fake = FakeGh(prs=[make_pr(60)], runs_by_branch={"feature-60": [make_run(HEAD_NEW)]})
        self.run_check(fake)
        self.assertEqual(len(fake.calls_with("pr", "list")), 1)
        self.assertEqual(fake.calls_with("pr", "view"), [])

    def test_explicit_numbers_use_pr_view_not_pr_list(self):
        fake = FakeGh(
            view_by_number={61: make_pr(61), 62: make_pr(62)},
            runs_by_branch={"feature-61": [make_run(HEAD_NEW)],
                            "feature-62": [make_run(HEAD_NEW)]},
        )
        rc, lines = self.run_check(fake, pr_numbers=[61, 62])
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_with("pr", "list"), [])
        self.assertEqual(len(fake.calls_with("pr", "view")), 2)
        self.assertEqual(len(lines), 2)

    def test_read_only_no_remediation_subcommand_is_issued(self):
        fake = FakeGh(
            prs=[make_pr(70), make_pr(71, head=HEAD_OLD)],
            runs_by_branch={"feature-70": [make_run(HEAD_NEW)], "feature-71": []},
        )
        rc, _ = self.run_check(fake)
        self.assertEqual(rc, 1)
        allowed = {("pr", "list"), ("pr", "view"), ("run", "list")}
        self.assertTrue(fake.calls, "the tool must issue gh reads")
        for call in fake.calls:
            self.assertIn(call[:2], allowed,
                          "read-only tool issued a non-read gh call: %r" % (call,))
            self.assertNotIn("update-branch", " ".join(call))


class TestFailClosed(PrTriggerCheckTestCase):
    def test_pr_list_gh_error_exits_two(self):
        fake = FakeGh(fail_on={("pr", "list"): "HTTP 502"})
        rc, _ = self.run_check(fake)
        self.assertEqual(rc, 2)

    def test_run_list_gh_error_exits_two(self):
        fake = FakeGh(prs=[make_pr(80)], fail_on={("run", "list"): "rate limited"})
        rc, _ = self.run_check(fake)
        self.assertEqual(rc, 2)

    def test_pr_view_gh_error_exits_two(self):
        fake = FakeGh(view_by_number={}, fail_on={("pr", "view"): "boom"})
        rc, _ = self.run_check(fake, pr_numbers=[81])
        self.assertEqual(rc, 2)

    def test_gh_error_is_not_silently_reported_as_missing_run(self):
        fake = FakeGh(prs=[make_pr(82)], fail_on={("run", "list"): "rate limited"})
        rc, lines = self.run_check(fake)
        self.assertEqual(rc, 2)
        self.assertFalse(any("pull_request_run=no" in line for line in lines))


class TestPureEvaluation(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_evaluate_pr_yes_when_exact_head_matches(self):
        row = self.module.evaluate_pr(make_pr(90), [make_run(HEAD_NEW, status="queued")])
        self.assertTrue(row["has_run"])
        self.assertEqual(row["run_status"], "queued")
        self.assertTrue(row["counts"])

    def test_evaluate_pr_no_when_only_other_heads(self):
        row = self.module.evaluate_pr(make_pr(91), [make_run(HEAD_OLD)])
        self.assertFalse(row["has_run"])
        self.assertIsNone(row["run_status"])
        self.assertTrue(row["counts"])

    def test_evaluate_draft_never_counts(self):
        row = self.module.evaluate_pr(make_pr(92, draft=True), [])
        self.assertFalse(row["counts"])


if __name__ == "__main__":
    unittest.main()
