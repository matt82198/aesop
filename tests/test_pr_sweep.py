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
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "pr_sweep.py"


def load_module():
    spec = importlib.util.spec_from_file_location("pr_sweep_under_test", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def iso(minutes_ago, now=None):
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_pr(number, *, draft=False, auto_merge=True, merge_state="CLEAN",
            updated_min_ago=60, created_min_ago=120, head="a" * 40, now=None):
    return {
        "number": number,
        "title": "pr #%d" % number,
        "isDraft": draft,
        "autoMergeRequest": {"enabledAt": iso(5, now)} if auto_merge else None,
        "mergeStateStatus": merge_state,
        "updatedAt": iso(updated_min_ago, now),
        "createdAt": iso(created_min_ago, now),
        "headRefOid": head,
        "headRefName": "feature-%d" % number,
        "url": "https://github.com/acme/repo/pull/%d" % number,
    }


class FakeGh:
    """Dispatches on (args[0], args[1]) exactly like the real gh CLI's
    subcommand shape, recording every call for assertions."""

    def __init__(self, prs, checks_by_pr=None, repo="acme/repo", auth_ok=True):
        self.prs = prs
        self.checks_by_pr = checks_by_pr or {}
        self.repo = repo
        self.auth_ok = auth_ok
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("auth", "status"):
            if self.auth_ok:
                return ""
            return {"error": "not logged in", "rc": 1}
        if args[:2] == ("repo", "view"):
            return {"nameWithOwner": self.repo}
        if args[:2] == ("pr", "list"):
            return self.prs
        if args[:2] == ("pr", "checks"):
            pr_num = int(args[2])
            return self.checks_by_pr.get(pr_num, [])
        if args[:2] == ("pr", "merge"):
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
        self.run_sweep(fake, behind_cap=2)
        api_calls = fake.calls_matching("api")
        self.assertEqual(len(api_calls), 2, api_calls)
        touched = {c[1] for c in api_calls}
        self.assertEqual(touched, {"repos/acme/repo/pulls/201/update-branch",
                                    "repos/acme/repo/pulls/202/update-branch"})

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


if __name__ == "__main__":
    unittest.main()
