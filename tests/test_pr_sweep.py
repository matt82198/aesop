#!/usr/bin/env python3
"""Unit tests for tools/pr_sweep.py -- the session-independent PR sweep actor.

Hermetic: no network, no real `gh`. `pr_sweep.gh` is a module global patched
with a FakeGh dispatcher per test (same injection pattern as
tests/test_merge_queue.py patches merge_queue.gh). The signal-hub queue is a
real temp file per test so dedupe-by-reading-the-file is exercised for real,
not mocked away.

Coverage mirrors the tool's contract (see tools/pr_sweep.py docstring):
  * arming missing auto-merge
  * BEHIND cap of 2, oldest-first, 20-min throttle
  * stuck-red (>=30 min) emits one pr.red event; a second run does not duplicate
  * DIRTY emits one pr.dirty event
  * draft PRs are skipped entirely
  * --dry-run performs no gh mutation and no queue write
  * gh auth missing -> exit 2
"""
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "pr_sweep.py"

# pr_sweep fails closed when CONDUCTOR_ROOT is unresolved (this test tree is not the canonical
# "aesop" checkout). Every test run pins its OWN mktemp conductor root, never the live fleet.
_TEST_CONDUCTOR_ROOT = tempfile.mkdtemp(prefix="aesop-prsweep-conductor-")
os.environ["CONDUCTOR_ROOT"] = _TEST_CONDUCTOR_ROOT


def load_module():
    spec = importlib.util.spec_from_file_location("pr_sweep_under_test", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def iso(minutes_ago, now=None):
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_pr(number, *, draft=False, auto_merge=True, merge_state="CLEAN",
            updated_min_ago=60, created_min_ago=120, head="a" * 40, now=None,
            base="main", head_ref=None):
    return {
        "number": number,
        "title": "pr #%d" % number,
        "isDraft": draft,
        "autoMergeRequest": {"enabledAt": iso(5, now)} if auto_merge else None,
        "mergeStateStatus": merge_state,
        "updatedAt": iso(updated_min_ago, now),
        "createdAt": iso(created_min_ago, now),
        "headRefOid": head,
        "headRefName": head_ref or "feature-%d" % number,
        "baseRefName": base,
        "url": "https://github.com/acme/repo/pull/%d" % number,
    }


class FakeGh:
    """Dispatches on (args[0], args[1]) exactly like the real gh CLI's
    subcommand shape, recording every call for assertions."""

    def __init__(self, prs, checks_by_pr=None, repo="acme/repo", auth_ok=True,
                 view_by_pr=None, fail_on=None, default_branch="main"):
        self.prs = prs
        # Repo default branch answered by `gh repo view`; None = unreadable.
        self.default_branch = default_branch
        self.checks_by_pr = checks_by_pr or {}
        self.repo = repo
        self.auth_ok = auth_ok
        # Per-PR `gh pr view --json statusCheckRollup,commits,comments` answers.
        # Default: a green-checked PR, so pre-existing tests never hit the
        # zero-check path (their PRs carry no commit timestamps).
        self.view_by_pr = view_by_pr or {}
        # Subcommand prefixes (e.g. ("workflow", "run")) that fail with an error.
        self.fail_on = fail_on or {}
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] in self.fail_on:
            return {"error": self.fail_on[args[:2]], "rc": 1}
        if args[:2] == ("auth", "status"):
            if self.auth_ok:
                return ""
            return {"error": "not logged in", "rc": 1}
        if args[:2] == ("repo", "view"):
            if self.default_branch is None:
                return {"nameWithOwner": self.repo}
            return {"nameWithOwner": self.repo,
                    "defaultBranchRef": {"name": self.default_branch}}
        if args[:2] == ("pr", "list"):
            return self.prs
        if args[:2] == ("pr", "view"):
            pr_num = int(args[2])
            return self.view_by_pr.get(pr_num, {
                "statusCheckRollup": [{"name": "ci (0)", "conclusion": "SUCCESS"}],
                "commits": [], "comments": [],
            })
        if args[:2] == ("pr", "checks"):
            pr_num = int(args[2])
            return self.checks_by_pr.get(pr_num, [])
        if args[:2] in (("pr", "merge"), ("pr", "comment"), ("pr", "edit"),
                        ("workflow", "run"), ("label", "create")):
            return ""
        if args[0] == "api":
            return ""
        return {"error": "FakeGh: unhandled call %r" % (args,), "rc": 1}

    def calls_matching(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == prefix]


class PrSweepTestCase(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.queue_path = Path(self._tmp.name) / "signal-hub-queue.jsonl"
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self._tmp.cleanup()

    def run_sweep(self, fake, *, dry_run=False, extra_argv=None, behind_cap=2):
        argv = ["--repo", fake.repo, "--queue-path", str(self.queue_path),
                "--behind-cap", str(behind_cap)]
        if dry_run:
            argv.append("--dry-run")
        if extra_argv:
            argv += extra_argv
        with patch.object(self.module, "gh", fake):
            rc = self.module.main(argv)
        return rc


class TestArmMissingAutoMerge(PrSweepTestCase):
    def test_arms_pr_with_no_auto_merge_request(self):
        pr = make_pr(101, auto_merge=False, now=self.now)
        fake = FakeGh([pr])
        rc = self.run_sweep(fake)
        self.assertEqual(rc, 0)
        merges = fake.calls_matching("pr", "merge")
        self.assertEqual(len(merges), 1)
        self.assertIn("101", merges[0])
        self.assertIn("--auto", merges[0])
        self.assertIn("--squash", merges[0])
        self.assertNotIn("--admin", merges[0])

    def test_does_not_rearm_already_armed_pr(self):
        pr = make_pr(102, auto_merge=True, now=self.now)
        fake = FakeGh([pr])
        self.run_sweep(fake)
        self.assertEqual(fake.calls_matching("pr", "merge"), [])


class TestBehindCap(PrSweepTestCase):
    def test_caps_at_two_oldest_first(self):
        # Three BEHIND PRs, all armed (no arm noise), all stale enough to be
        # eligible (updated 60 min ago, well past the 20-min throttle).
        # created_min_ago makes #201 the oldest, #203 the newest.
        prs = [
            make_pr(201, merge_state="BEHIND", created_min_ago=300, updated_min_ago=60, now=self.now),
            make_pr(202, merge_state="BEHIND", created_min_ago=200, updated_min_ago=60, now=self.now),
            make_pr(203, merge_state="BEHIND", created_min_ago=100, updated_min_ago=60, now=self.now),
        ]
        fake = FakeGh(prs)
        log_path = Path(self._tmp.name) / "pr-sweep.log"
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.run_sweep(fake, behind_cap=2,
                                extra_argv=["--json", "--log-path", str(log_path)])
        self.assertEqual(rc, 0)
        # New contract: a BEHIND PR is REFUSED, never updated server-side. A
        # GitHub-authored merge head has no box that ran the gates, so no
        # receipt can exist for it; the lane merges origin/main locally.
        self.assertEqual(fake.calls_matching("api"), [], fake.calls)
        self.assertEqual([c for c in fake.calls if any("update-branch" in str(a) for a in c)], [])
        self.assertEqual([c for c in fake.calls if "PUT" in c], [])
        out = buf.getvalue()
        summary = json.loads(out[out.index("{"):])
        self.assertEqual(summary["results"]["behind_refused"], 2, summary)
        behind_lines = [l for l in summary["plan"] if l.startswith("BEHIND #")]
        self.assertEqual([l.split()[1] for l in behind_lines], ["#201", "#202"], behind_lines)
        # The newest (#203) is beyond the cap: neither refused nor touched.
        self.assertNotIn("#203", out)
        reads = {("auth", "status"), ("repo", "view"), ("pr", "list"), ("pr", "view"), ("pr", "checks")}
        writes = [c for c in fake.calls if c[:2] not in reads]
        self.assertEqual(writes, [], writes)
        rows = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual([(r["pr"], r["action"]) for r in rows], [(201, "behind"), (202, "behind")], rows)
        for r in rows:
            self.assertTrue(r["outcome"].startswith("refused:"), r)

    def test_throttles_recently_updated_behind_pr(self):
        pr = make_pr(204, merge_state="BEHIND", updated_min_ago=5, now=self.now)
        fake = FakeGh([pr])
        self.run_sweep(fake)
        self.assertEqual(fake.calls_matching("api"), [])


class TestRedSignal(PrSweepTestCase):
    def _failing_checks(self, started_min_ago):
        return [{"name": "main-full", "bucket": "fail",
                 "startedAt": iso(started_min_ago, self.now), "completedAt": iso(started_min_ago - 1, self.now)}]

    def test_stuck_red_emits_one_event_then_dedupes_on_rerun(self):
        pr = make_pr(301, merge_state="CLEAN", head="deadbeef" * 5, now=self.now)
        fake1 = FakeGh([pr], checks_by_pr={301: self._failing_checks(45)})
        self.run_sweep(fake1)

        self.assertTrue(self.queue_path.exists())
        lines = self.queue_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["type"], "pr.red")
        self.assertEqual(event["pr"], 301)
        self.assertEqual(event["head"], pr["headRefOid"])
        self.assertEqual(event["checks"], ["main-full"])
        self.assertEqual(event["repo"], "acme/repo")

        # Second, independent run (fresh module + fake, same queue file on
        # disk) must not duplicate the event for the same (pr, head).
        module2 = load_module()
        fake2 = FakeGh([pr], checks_by_pr={301: self._failing_checks(50)})
        with patch.object(module2, "gh", fake2):
            module2.main(["--repo", "acme/repo", "--queue-path", str(self.queue_path)])
        lines_after = self.queue_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines_after), 1, "second run must not duplicate the pr.red event")

    def test_red_under_30_minutes_does_not_signal_yet(self):
        pr = make_pr(302, merge_state="CLEAN", now=self.now)
        fake = FakeGh([pr], checks_by_pr={302: self._failing_checks(10)})
        self.run_sweep(fake)
        self.assertFalse(self.queue_path.exists())

    def test_new_head_after_fix_push_signals_again(self):
        """A new push (new head sha) that is ALSO stuck red gets its own
        event -- dedupe is per (pr, head), not per pr alone."""
        old_head = "a" * 40
        new_head = "b" * 40
        pr_old = make_pr(303, merge_state="CLEAN", head=old_head, now=self.now)
        fake1 = FakeGh([pr_old], checks_by_pr={303: self._failing_checks(45)})
        self.run_sweep(fake1)

        pr_new = make_pr(303, merge_state="CLEAN", head=new_head, now=self.now)
        fake2 = FakeGh([pr_new], checks_by_pr={303: self._failing_checks(45)})
        self.run_sweep(fake2)

        lines = self.queue_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        heads = {json.loads(l)["head"] for l in lines}
        self.assertEqual(heads, {old_head, new_head})


class TestDirtySignal(PrSweepTestCase):
    def test_dirty_emits_event(self):
        pr = make_pr(401, merge_state="DIRTY", now=self.now)
        fake = FakeGh([pr])
        self.run_sweep(fake)
        self.assertTrue(self.queue_path.exists())
        lines = self.queue_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["type"], "pr.dirty")
        self.assertEqual(event["pr"], 401)

    def test_dirty_dedupes_on_rerun(self):
        pr = make_pr(402, merge_state="DIRTY", now=self.now)
        fake = FakeGh([pr])
        self.run_sweep(fake)
        self.run_sweep(FakeGh([pr]))
        lines = self.queue_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)


class TestStackedPrNotArmed(PrSweepTestCase):
    """A stacked PR (base is another PR's branch, not the repo default) must
    never be armed: GitHub would merge it into the parent branch, which has
    no protection and runs no CI, and report MERGED for content that never
    reached the trunk."""

    def stacked_pair(self):
        parent = make_pr(233, auto_merge=True, now=self.now,
                         head_ref="feat/rewards-content-facets")
        child = make_pr(332, auto_merge=False, base="feat/rewards-content-facets",
                        now=self.now)
        return parent, child

    def test_stacked_pr_is_not_armed(self):
        parent, child = self.stacked_pair()
        fake = FakeGh([parent, child])
        rc = self.run_sweep(fake)
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_matching("pr", "merge"), [])

    def test_stacked_pr_report_names_parent_and_retarget_step(self):
        parent, child = self.stacked_pair()
        fake = FakeGh([parent, child])
        with patch.object(self.module, "gh", fake):
            actions = self.module.build_plan([parent, child], "acme/repo",
                                             self.now, self.queue_path)
        stacked = [a for a in actions if a["kind"] == "stacked"]
        self.assertEqual(len(stacked), 1)
        self.assertEqual(stacked[0]["pr"], 332)
        self.assertIn("stacked on #233; retarget to main after #233 merges",
                      stacked[0]["reason"])
        self.assertEqual([a for a in actions if a["kind"] == "arm"], [])

    def test_stacked_pr_on_unknown_parent_is_still_refused(self):
        child = make_pr(340, auto_merge=False, base="feat/gone", now=self.now)
        fake = FakeGh([child])
        rc = self.run_sweep(fake)
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_matching("pr", "merge"), [])

    def test_default_branch_pr_still_armed(self):
        pr = make_pr(341, auto_merge=False, base="main", now=self.now)
        fake = FakeGh([pr])
        rc = self.run_sweep(fake)
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.calls_matching("pr", "merge")), 1)

    def test_unreadable_default_branch_fails_closed_before_any_write(self):
        pr = make_pr(342, auto_merge=False, base="main", now=self.now)
        fake = FakeGh([pr], default_branch=None)
        rc = self.run_sweep(fake)
        self.assertEqual(rc, 3)
        self.assertEqual(fake.calls_matching("pr", "merge"), [])


class TestDraftSkipped(PrSweepTestCase):
    def test_draft_pr_gets_no_action_at_all(self):
        pr = make_pr(501, draft=True, auto_merge=False, merge_state="BEHIND", updated_min_ago=60, now=self.now)
        fake = FakeGh([pr], checks_by_pr={501: [{"name": "main-full", "bucket": "fail",
                                                  "startedAt": iso(60, self.now)}]})
        self.run_sweep(fake)
        self.assertEqual(fake.calls_matching("pr", "merge"), [])
        self.assertEqual(fake.calls_matching("api"), [])
        self.assertFalse(self.queue_path.exists())
        # And required-checks is never even queried for a draft (no gh call
        # referencing PR 501 beyond the initial pr list).
        self.assertEqual(fake.calls_matching("pr", "checks", "501"), [])


class TestDryRun(PrSweepTestCase):
    def test_dry_run_mutates_nothing(self):
        prs = [
            make_pr(601, auto_merge=False, now=self.now),
            make_pr(602, merge_state="BEHIND", updated_min_ago=60, now=self.now),
            make_pr(603, merge_state="DIRTY", now=self.now),
        ]
        fake = FakeGh(prs, checks_by_pr={601: [], 602: [], 603: []})
        rc = self.run_sweep(fake, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_matching("pr", "merge"), [])
        self.assertEqual(fake.calls_matching("api"), [])
        self.assertFalse(self.queue_path.exists())


class TestGhAuthMissing(PrSweepTestCase):
    def test_exit_2_when_auth_missing(self):
        fake = FakeGh([], auth_ok=False)
        rc = self.run_sweep(fake)
        self.assertEqual(rc, 2)
        self.assertEqual(fake.calls_matching("pr", "list"), [])


RETRY_MARK = "<!-- pr-sweep:zero-check-retry sha=%s -->"
DISARM_MARK = "<!-- pr-sweep:zero-check-disarm sha=%s -->"
REBASE_MARK = "<!-- pr-sweep:needs-rebase sha=%s -->"
HEAD = "a" * 40


def zero_view(head_age_min, comments=None, rollup=None):
    """A `gh pr view` answer for an armed PR whose head has no check runs."""
    return {
        "statusCheckRollup": rollup or [],
        "commits": [{"oid": HEAD, "committedDate": iso(head_age_min)}],
        "comments": comments or [],
    }


class TestZeroCheckRetrigger(PrSweepTestCase):
    def test_zero_run_armed_pr_gets_ci_dispatched_and_commented(self):
        pr = make_pr(701, now=self.now)
        fake = FakeGh([pr], view_by_pr={701: zero_view(45)})
        rc = self.run_sweep(fake)
        self.assertEqual(rc, 0)
        dispatches = fake.calls_matching("workflow", "run")
        self.assertEqual(len(dispatches), 1)
        self.assertIn("ci.yml", dispatches[0])
        self.assertIn("feature-701", dispatches[0])
        comments = fake.calls_matching("pr", "comment")
        self.assertEqual(len(comments), 1)
        self.assertIn(RETRY_MARK % HEAD, " ".join(comments[0]))
        self.assertEqual(fake.calls_matching("pr", "merge", "701", "--disable-auto"), [])

    def test_pr_with_check_runs_is_untouched(self):
        pr = make_pr(702, now=self.now)
        view = zero_view(45, rollup=[{"name": "ci (0)", "conclusion": "SUCCESS"}])
        fake = FakeGh([pr], view_by_pr={702: view})
        self.assertEqual(self.run_sweep(fake), 0)
        self.assertEqual(fake.calls_matching("workflow", "run"), [])
        self.assertEqual(fake.calls_matching("pr", "comment"), [])

    def test_head_younger_than_threshold_is_untouched(self):
        pr = make_pr(703, now=self.now)
        fake = FakeGh([pr], view_by_pr={703: zero_view(5)})
        self.assertEqual(self.run_sweep(fake), 0)
        self.assertEqual(fake.calls_matching("workflow", "run"), [])

    def test_retrigger_is_idempotent_within_24h(self):
        pr = make_pr(704, now=self.now)
        comments = [{"body": RETRY_MARK % HEAD, "createdAt": iso(10)}]
        fake = FakeGh([pr], view_by_pr={704: zero_view(60, comments)})
        self.assertEqual(self.run_sweep(fake), 0)
        self.assertEqual(fake.calls_matching("workflow", "run"), [])
        self.assertEqual(fake.calls_matching("pr", "comment"), [])
        self.assertEqual(fake.calls_matching("pr", "merge", "704", "--disable-auto"), [])

    def test_disarms_and_labels_after_24h_still_zero(self):
        pr = make_pr(705, now=self.now)
        comments = [{"body": RETRY_MARK % HEAD, "createdAt": iso(25 * 60)}]
        fake = FakeGh([pr], view_by_pr={705: zero_view(26 * 60, comments)})
        self.assertEqual(self.run_sweep(fake), 0)
        self.assertEqual(len(fake.calls_matching("pr", "merge", "705", "--disable-auto")), 1)
        self.assertEqual(fake.calls_matching("workflow", "run"), [])
        edits = fake.calls_matching("pr", "edit", "705")
        self.assertEqual(len(edits), 1)
        self.assertIn("needs-ci-trigger", " ".join(edits[0]))
        self.assertEqual(len(fake.calls_matching("label", "create")), 1)
        disarm_comments = [c for c in fake.calls_matching("pr", "comment")
                           if DISARM_MARK % HEAD in " ".join(c)]
        self.assertEqual(len(disarm_comments), 1)

    def test_disarm_is_idempotent(self):
        pr = make_pr(706, now=self.now)
        comments = [{"body": RETRY_MARK % HEAD, "createdAt": iso(25 * 60)},
                    {"body": DISARM_MARK % HEAD, "createdAt": iso(60)}]
        fake = FakeGh([pr], view_by_pr={706: zero_view(26 * 60, comments)})
        self.assertEqual(self.run_sweep(fake), 0)
        self.assertEqual(fake.calls_matching("pr", "merge", "706", "--disable-auto"), [])
        self.assertEqual(fake.calls_matching("pr", "comment"), [])

    def test_dry_run_mutates_nothing(self):
        pr = make_pr(707, now=self.now)
        fake = FakeGh([pr], view_by_pr={707: zero_view(45)})
        self.assertEqual(self.run_sweep(fake, dry_run=True), 0)
        self.assertEqual(fake.calls_matching("workflow", "run"), [])
        self.assertEqual(fake.calls_matching("pr", "comment"), [])
        self.assertEqual(fake.calls_matching("pr", "merge"), [])

    def test_dirty_zero_run_pr_is_labelled_needs_rebase_not_dispatched(self):
        pr = make_pr(801, merge_state="DIRTY", now=self.now)
        fake = FakeGh([pr], view_by_pr={801: zero_view(45)})
        self.assertEqual(self.run_sweep(fake), 0)
        self.assertEqual(fake.calls_matching("workflow", "run"), [])
        edits = fake.calls_matching("pr", "edit", "801")
        self.assertEqual(len(edits), 1)
        self.assertIn("needs-rebase", " ".join(edits[0]))
        rebase_comments = [c for c in fake.calls_matching("pr", "comment")
                           if REBASE_MARK % HEAD in " ".join(c)]
        self.assertEqual(len(rebase_comments), 1)
        self.assertIn("conflict", " ".join(rebase_comments[0]).lower())
        self.assertEqual(fake.calls_matching("pr", "merge", "801", "--disable-auto"), [])

    def test_conflicting_mergeable_is_treated_as_dirty(self):
        pr = make_pr(802, merge_state="UNKNOWN", now=self.now)
        pr["mergeable"] = "CONFLICTING"
        fake = FakeGh([pr], view_by_pr={802: zero_view(45)})
        self.assertEqual(self.run_sweep(fake), 0)
        self.assertEqual(fake.calls_matching("workflow", "run"), [])
        self.assertEqual(len(fake.calls_matching("pr", "edit", "802")), 1)

    def test_dirty_rebase_is_idempotent_and_never_disarmed(self):
        pr = make_pr(803, merge_state="DIRTY", now=self.now)
        comments = [{"body": REBASE_MARK % HEAD, "createdAt": iso(25 * 60)}]
        fake = FakeGh([pr], view_by_pr={803: zero_view(26 * 60, comments)})
        self.assertEqual(self.run_sweep(fake), 0)
        self.assertEqual(fake.calls_matching("pr", "edit"), [])
        self.assertEqual(fake.calls_matching("pr", "comment"), [])
        self.assertEqual(fake.calls_matching("pr", "merge", "803", "--disable-auto"), [])

    def test_dry_run_does_not_flush_receipts(self):
        pr = make_pr(712, now=self.now)
        fake = FakeGh([pr], view_by_pr={712: zero_view(45)})
        with patch.object(self.module, "_flush_receipts_if_possible") as flush:
            self.assertEqual(self.run_sweep(fake, dry_run=True), 0)
        flush.assert_not_called()

    def test_read_failure_fails_closed_before_any_write(self):
        actionable = make_pr(708, now=self.now)
        unreadable = make_pr(709, now=self.now)
        fake = FakeGh([actionable, unreadable], view_by_pr={
            708: zero_view(45),
            709: {"error": "HTTP 502", "rc": 1},
        })
        rc = self.run_sweep(fake)
        self.assertNotEqual(rc, 0)
        writes = [c for c in fake.calls if c[:2] in
                  (("workflow", "run"), ("pr", "comment"), ("pr", "merge"), ("pr", "edit"))]
        self.assertEqual(writes, [])

    def test_dispatch_failure_stops_before_comment(self):
        pr = make_pr(710, now=self.now)
        fake = FakeGh([pr], view_by_pr={710: zero_view(45)},
                      fail_on={("workflow", "run"): "workflow dispatch refused"})
        rc = self.run_sweep(fake)
        self.assertNotEqual(rc, 0)
        self.assertEqual(fake.calls_matching("pr", "comment"), [])

    def test_action_is_logged_with_pr_and_reason(self):
        pr = make_pr(711, now=self.now)
        fake = FakeGh([pr], view_by_pr={711: zero_view(45)})
        log_path = Path(self._tmp.name) / "pr-sweep.log"
        self.assertEqual(self.run_sweep(fake, extra_argv=["--log-path", str(log_path)]), 0)
        entries = [json.loads(line) for line in
                   log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        zero = [e for e in entries if e.get("action") == "zero-retrigger"]
        self.assertEqual(len(zero), 1)
        self.assertEqual(zero[0]["pr"], 711)
        self.assertTrue(zero[0]["reason"])


class RefusingDispatchForBranchGh(FakeGh):
    """FakeGh where `gh workflow run` is refused for one branch only."""

    def __init__(self, prs, refused_branch, **kwargs):
        super().__init__(prs, **kwargs)
        self.refused_branch = refused_branch

    def __call__(self, *args):
        if args[:2] == ("workflow", "run") and self.refused_branch in args:
            self.calls.append(args)
            return {"error": "workflow dispatch refused", "rc": 1}
        return super().__call__(*args)


class TestPerPrIsolation(PrSweepTestCase):
    def test_refused_dispatch_for_one_pr_does_not_starve_later_zero_check_pr(self):
        first = make_pr(801, now=self.now)
        second = make_pr(802, now=self.now)
        fake = RefusingDispatchForBranchGh(
            [first, second], "feature-801",
            view_by_pr={801: zero_view(45), 802: zero_view(45)})
        log_path = Path(self._tmp.name) / "pr-sweep.log"
        rc = self.run_sweep(fake, extra_argv=["--log-path", str(log_path)])
        dispatches = fake.calls_matching("workflow", "run")
        self.assertEqual(len(dispatches), 2, "both zero-check PRs must be attempted")
        self.assertIn("feature-802", dispatches[1])
        comments = fake.calls_matching("pr", "comment")
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0][2], "802")
        self.assertNotEqual(rc, 0, "a failed PR action must fail the sweep at the end")
        entries = [json.loads(line) for line in
                   log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        by_pr = {e["pr"]: e for e in entries if e.get("action") == "zero-retrigger"}
        self.assertIn("failed", by_pr[801]["outcome"])
        self.assertIn("refused", by_pr[801]["outcome"])
        self.assertIn("dispatched", by_pr[802]["outcome"])


class LimitHonouringFakeGh(FakeGh):
    """Models real `gh pr list --limit N`: returns at most N PRs, so a
    listing that ignores the cap is visible to the test."""

    def __call__(self, *args):
        if args[:2] == ("pr", "list"):
            self.calls.append(args)
            limit = int(args[args.index("--limit") + 1])
            return self.prs[:limit]
        return super().__call__(*args)


class TestPrListPagination(PrSweepTestCase):
    def test_arms_pr_beyond_the_first_100_open_prs(self):
        prs = [make_pr(n, auto_merge=False, now=self.now) for n in range(1, 151)]
        fake = LimitHonouringFakeGh(prs)
        rc = self.run_sweep(fake)
        armed = {int(c[2]) for c in fake.calls_matching("pr", "merge")}
        self.assertEqual(rc, 0)
        self.assertEqual(len(armed), 150, "PRs past the 100 cap were never swept")
        self.assertIn(150, armed)

    def test_refuses_a_listing_that_hits_the_limit_and_acts_on_nothing(self):
        limit = self.module.PR_LIST_LIMIT
        prs = [make_pr(n, auto_merge=False, now=self.now) for n in range(1, limit + 2)]
        fake = LimitHonouringFakeGh(prs)
        rc = self.run_sweep(fake)
        self.assertEqual(rc, 2, "a truncated PR listing must fail closed, not sweep a partial set")
        self.assertEqual(fake.calls_matching("pr", "merge"), [])


class TestRequiredChecksReadFailsClosed(PrSweepTestCase):
    def test_required_checks_read_error_stops_before_any_write(self):
        pr = make_pr(301, auto_merge=False, now=self.now)
        fake = FakeGh([pr], fail_on={("pr", "checks"): "HTTP 502: bad gateway"})
        rc = self.run_sweep(fake)
        self.assertEqual(rc, 3, "an unreadable required-check state must not be read as 'no checks'")
        self.assertEqual(fake.calls_matching("pr", "merge"), [])
        self.assertEqual(fake.calls_matching("pr", "comment"), [])

    def test_repo_with_no_required_checks_is_not_an_error(self):
        pr = make_pr(302, auto_merge=True, now=self.now)
        fake = FakeGh([pr], fail_on={
            ("pr", "checks"): "no required checks reported on the 'feature-302' branch"})
        rc = self.run_sweep(fake)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
