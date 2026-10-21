"""
Test suite for tools/worktree_gc.py -- worktree GC report and cap check.

Covers: a SAFE, a DIRTY and an UNPUSHED worktree in a real temp git fixture
(origin is a local bare repo; gh is never called), the cap OVER/OK status,
and the removal gate (no token removes nothing; the matching token removes
SAFE only).
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import worktree_gc

NO_PRS = lambda branch: ([], [])  # noqa: E731  (fixture never reaches gh)
GIT_ID = ["-c", "user.name=gc-test", "-c", "user.email=gc-test@example.invalid"]


def _git(args, cwd):
    result = subprocess.run(["git", *GIT_ID, *args], cwd=str(cwd), capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=120)
    if result.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), result.stderr))
    return result.stdout


def _commit(repo, name, body):
    (repo / name).write_text(body, encoding="utf-8")
    _git(["add", name], repo)
    _git(["commit", "-q", "-m", "change " + name], repo)


def _fixture(root):
    """Return (primary, origin). Primary has main pushed to a local bare origin."""
    origin = root / "origin.git"
    primary = root / "primary"
    _git(["init", "-q", "--bare", str(origin)], root)
    _git(["init", "-q", "-b", "main", str(primary)], root)
    _commit(primary, "README.md", "base\n")
    _git(["remote", "add", "origin", str(origin)], primary)
    _git(["push", "-q", "origin", "main"], primary)
    return primary, origin


def _lane_for(path, branch):
    return {"name": path.name, "path": str(path), "branch": branch}


class TestClassifyFixture(unittest.TestCase):
    """SAFE, DIRTY and UNPUSHED worktrees in one real temp repo."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.primary, _ = _fixture(root)
        cls.wt_safe = root / "wt-safe"
        cls.wt_dirty = root / "wt-dirty"
        cls.wt_unpushed = root / "wt-unpushed"
        _git(["worktree", "add", "-q", "-b", "safe-b", str(cls.wt_safe), "origin/main"], cls.primary)
        _git(["worktree", "add", "-q", "-b", "dirty-b", str(cls.wt_dirty), "origin/main"], cls.primary)
        _git(["worktree", "add", "-q", "-b", "unpushed-b", str(cls.wt_unpushed), "origin/main"], cls.primary)
        _commit(cls.wt_unpushed, "feature.txt", "local only\n")
        (cls.wt_dirty / "README.md").write_text("edited, not committed\n", encoding="utf-8")
        cls.now = time.time()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _classify(self, path, branch):
        return worktree_gc.classify_worktree(
            _lane_for(path, branch), "fixture", self.primary,
            live_minutes=0, now=self.now, pr_view=NO_PRS, keep_norms=frozenset())

    def test_safe_clean_and_merged_into_default(self):
        rec = self._classify(self.wt_safe, "safe-b")
        self.assertEqual(rec["class"], "SAFE", rec)
        self.assertIn("ancestor", rec["reason"])

    def test_dirty_reports_changed_files(self):
        rec = self._classify(self.wt_dirty, "dirty-b")
        self.assertEqual(rec["class"], "DIRTY", rec)
        self.assertIn("README.md", rec["dirty_files"])

    def test_unpushed_commit_is_not_safe(self):
        rec = self._classify(self.wt_unpushed, "unpushed-b")
        self.assertEqual(rec["class"], "UNPUSHED", rec)
        self.assertEqual(rec["unpushed"], 1)

    def test_missing_path_is_unknown_not_safe(self):
        gone = self.wt_safe.parent / "does-not-exist"
        rec = self._classify(gone, "ghost-b")
        self.assertEqual(rec["class"], "UNKNOWN", rec)

    def test_open_pr_makes_lane_live_even_when_safe(self):
        rec = worktree_gc.classify_worktree(
            _lane_for(self.wt_safe, "safe-b"), "fixture", self.primary,
            live_minutes=0, now=self.now, pr_view=lambda b: ([], [77]),
            keep_norms=frozenset())
        self.assertEqual(rec["class"], "LIVE", rec)
        self.assertIn("#77", rec["reason"])

    def test_pr_lookup_failure_fails_closed(self):
        def broken(branch):
            raise worktree_gc.PrLookupError("gh unavailable")
        rec = worktree_gc.classify_worktree(
            _lane_for(self.wt_safe, "safe-b"), "fixture", self.primary,
            live_minutes=0, now=self.now, pr_view=broken, keep_norms=frozenset())
        self.assertEqual(rec["class"], "UNKNOWN", rec)



class TestRemovalGate(unittest.TestCase):
    """Removal mutates the repo, so each test gets its own fresh fixture."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.primary, _ = _fixture(root)
        self.wt_safe = root / "wt-safe"
        self.wt_dirty = root / "wt-dirty"
        self.wt_unpushed = root / "wt-unpushed"
        _git(["worktree", "add", "-q", "-b", "safe-b", str(self.wt_safe), "origin/main"], self.primary)
        _git(["worktree", "add", "-q", "-b", "dirty-b", str(self.wt_dirty), "origin/main"], self.primary)
        _git(["worktree", "add", "-q", "-b", "unpushed-b", str(self.wt_unpushed), "origin/main"], self.primary)
        _commit(self.wt_unpushed, "feature.txt", "local only\n")
        (self.wt_dirty / "README.md").write_text("edited, not committed\n", encoding="utf-8")
        self.now = time.time()
        self.old_token = os.environ.pop(worktree_gc.TOKEN_ENV, None)

    def tearDown(self):
        os.environ.pop(worktree_gc.TOKEN_ENV, None)
        if self.old_token is not None:
            os.environ[worktree_gc.TOKEN_ENV] = self.old_token
        self._tmp.cleanup()

    def _classify(self, path, branch):
        return worktree_gc.classify_worktree(
            _lane_for(path, branch), "fixture", self.primary,
            live_minutes=0, now=self.now, pr_view=NO_PRS, keep_norms=frozenset())

    def test_removal_without_token_removes_nothing(self):
        records = [self._classify(self.wt_safe, "safe-b")]
        code, _ = worktree_gc.apply_removals(records, "", lambda r: r)
        self.assertEqual(code, 2)
        self.assertTrue(self.wt_safe.is_dir())

    def test_removal_with_token_removes_safe_only(self):
        records = [self._classify(self.wt_safe, "safe-b"),
                   self._classify(self.wt_dirty, "dirty-b"),
                   self._classify(self.wt_unpushed, "unpushed-b")]
        os.environ[worktree_gc.TOKEN_ENV] = "tok-test-value"
        code, messages = worktree_gc.apply_removals(
            records, "tok-test-value",
            lambda r: self._classify(Path(r["path"]), r["branch"]))
        self.assertEqual(code, 0, messages)
        self.assertFalse(self.wt_safe.exists())
        self.assertTrue(self.wt_dirty.is_dir())
        self.assertTrue(self.wt_unpushed.is_dir())


class TestInventory(unittest.TestCase):
    def test_counts_non_primary_worktrees_and_names_unreadable_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary, _ = _fixture(root)
            _git(["worktree", "add", "-q", "-b", "inv-a", str(root / "inv-a"), "origin/main"], primary)
            _git(["worktree", "add", "-q", "-b", "inv-b", str(root / "inv-b"), "origin/main"], primary)
            entries, unreadable = worktree_gc.inventory(
                [("fixture", primary), ("missing", root / "no-such-repo")])
            self.assertEqual(sorted(e[2]["name"] for e in entries), ["inv-a", "inv-b"])
            self.assertEqual([name for name, _ in unreadable], ["missing"])


class TestCapSummary(unittest.TestCase):
    def _records(self, count):
        return [{"class": "SAFE", "primary": "p", "name": "n%d" % i} for i in range(count)]

    def test_over_cap_status(self):
        counts = worktree_gc.summarize(self._records(16), 15)
        self.assertEqual(worktree_gc.summary_line(counts),
                         "WORKTREES: total=16 safe=16 dirty=0 unpushed=0 live=0 cap=15 OVER")

    def test_at_cap_is_ok(self):
        counts = worktree_gc.summarize(self._records(15), 15)
        self.assertEqual(counts["status"], "OK")


if __name__ == "__main__":
    unittest.main()
