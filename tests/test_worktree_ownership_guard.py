#!/usr/bin/env python3
"""Behavioural tests: worktree ownership marker + merge_train integration-branch guard.

Sandbox only: every repo here is a throwaway temp repo with a local bare origin.
Nothing touches the live aesop checkout or the network.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import worktree_marker  # noqa: E402

GIT_ID = ["-c", "user.name=sandbox", "-c", "user.email=sandbox@example.invalid"]


def _git(cwd, *args):
    result = subprocess.run(["git"] + GIT_ID + list(args), cwd=str(cwd),
                            capture_output=True, encoding="utf-8",
                            errors="replace", timeout=60)
    return result.returncode, (result.stdout + result.stderr).strip()


def _load_merge_train():
    spec = importlib.util.spec_from_file_location("merge_train", TOOLS / "merge_train.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorktreeOwnershipFixture(unittest.TestCase):
    """Sandbox repo: bare origin, primary clone, three sibling worktrees."""

    def setUp(self):
        self._old_cwd = os.getcwd()
        self.root = Path(tempfile.mkdtemp(prefix="wtguard-"))
        origin = self.root / "origin.git"
        seed = self.root / "seed"
        primary = self.root / "primary"
        seed.mkdir()
        _git(self.root, "init", "-q", "--bare", "-b", "main", str(origin))
        _git(seed, "init", "-q", "-b", "main")
        (seed / "README").write_text("seed\n", encoding="utf-8")
        _git(seed, "add", "README")
        _git(seed, "commit", "-q", "-m", "seed")
        _git(seed, "push", "-q", str(origin), "main")
        _git(self.root, "clone", "-q", str(origin), str(primary))
        self.primary = primary
        self.ro = self.root / "wt-ro"
        self.rw = self.root / "wt-rw"
        self.unmarked = self.root / "wt-unmarked"
        for path, branch in ((self.ro, "lane-ro"), (self.rw, "lane-rw"),
                             (self.unmarked, "lane-unmarked")):
            rc, out = _git(primary, "worktree", "add", "-q", str(path), "-b", branch, "origin/main")
            self.assertEqual(rc, 0, out)
        worktree_marker.write_marker(str(self.ro), "lane-ro", "ro")
        worktree_marker.write_marker(str(self.rw), "lane-rw", "rw")

    def tearDown(self):
        os.chdir(self._old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def _branch_exists(self, name):
        rc, _ = _git(self.primary, "rev-parse", "--verify", "--quiet", "refs/heads/" + name)
        return rc == 0


class TestWorktreeMarker(WorktreeOwnershipFixture):

    def test_write_then_read_roundtrip(self):
        worktree_marker.write_marker(str(self.unmarked), "lane-x", "rw")
        self.assertEqual(worktree_marker.read_marker(str(self.unmarked)),
                         {"owner": "lane-x", "mode": "rw"})

    def test_read_absent_returns_none(self):
        self.assertIsNone(worktree_marker.read_marker(str(self.unmarked)))

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            worktree_marker.write_marker(str(self.unmarked), "lane-x", "rwx")

    def test_unreadable_marker_fails_closed(self):
        path = worktree_marker.marker_path(str(self.unmarked))
        Path(path).write_text("{not json", encoding="utf-8")
        with self.assertRaises(worktree_marker.MarkerError):
            worktree_marker.read_marker(str(self.unmarked))

    def test_malformed_marker_fails_closed(self):
        path = worktree_marker.marker_path(str(self.unmarked))
        Path(path).write_text('{"owner": "x", "mode": "admin"}', encoding="utf-8")
        with self.assertRaises(worktree_marker.MarkerError):
            worktree_marker.read_marker(str(self.unmarked))


class TestCreateIntegrationBranchGuard(WorktreeOwnershipFixture):

    def setUp(self):
        super().setUp()
        self.mt = _load_merge_train()
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AESOP_QUEUE_ROOT", None)
        os.environ.pop("AESOP_WORKTREE_OWNER", None)

    def test_ro_worktree_is_refused(self):
        # RED before the fix: the call moved the ro tree onto integrate/* (no cwd guard).
        with patch.object(self.mt, "print"):
            ok = self.mt.create_integration_branch("b-ro", cwd=str(self.ro), owner="lane-ro")
        self.assertFalse(ok)
        self.assertFalse(self._branch_exists("integrate/b-ro"))

    def test_refusal_names_the_path(self):
        printed = []
        with patch.object(self.mt, "print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
            ok = self.mt.create_integration_branch("b-ro", cwd=str(self.ro), owner="lane-ro")
        self.assertFalse(ok)
        # Windows: TEMP may be an 8.3 or subst/junction alias. The refusal must name the
        # resolved path in the location slot ("... in <path>"), not a repr'd alias.
        real_ro = str(Path(self.ro).resolve())
        self.assertTrue(any(f"in {real_ro}" in line for line in printed), printed)

    def test_unmarked_worktree_is_refused(self):
        with patch.object(self.mt, "print"):
            ok = self.mt.create_integration_branch("b-unm", cwd=str(self.unmarked), owner="lane-unmarked")
        self.assertFalse(ok)
        self.assertFalse(self._branch_exists("integrate/b-unm"))

    def test_rw_marker_owned_by_other_is_refused(self):
        with patch.object(self.mt, "print"):
            ok = self.mt.create_integration_branch("b-other", cwd=str(self.rw), owner="someone-else")
        self.assertFalse(ok)
        self.assertFalse(self._branch_exists("integrate/b-other"))

    def test_missing_cwd_is_refused(self):
        # Process cwd is deliberately the ro worktree: an implicit cwd must NOT be used.
        os.chdir(str(self.ro))
        with patch.object(self.mt, "print"):
            ok = self.mt.create_integration_branch("b-nocwd", owner="lane-ro")
        self.assertFalse(ok)
        self.assertFalse(self._branch_exists("integrate/b-nocwd"))

    def test_malformed_marker_is_refused(self):
        Path(worktree_marker.marker_path(str(self.rw))).write_text("garbage", encoding="utf-8")
        with patch.object(self.mt, "print"):
            ok = self.mt.create_integration_branch("b-bad", cwd=str(self.rw), owner="lane-rw")
        self.assertFalse(ok)
        self.assertFalse(self._branch_exists("integrate/b-bad"))

    def test_rw_marker_owned_by_caller_proceeds(self):
        with patch.object(self.mt, "print"):
            ok = self.mt.create_integration_branch("b-rw", cwd=str(self.rw), owner="lane-rw")
        self.assertTrue(ok)
        self.assertTrue(self._branch_exists("integrate/b-rw"))
        rc, head = _git(self.rw, "rev-parse", "--abbrev-ref", "HEAD")
        self.assertEqual(head, "integrate/b-rw")

    def test_owner_defaults_from_env(self):
        with patch.dict(os.environ, {"AESOP_WORKTREE_OWNER": "lane-rw"}):
            with patch.object(self.mt, "print"):
                ok = self.mt.create_integration_branch("b-env", cwd=str(self.rw))
        self.assertTrue(ok)

    def test_queue_root_env_is_accepted(self):
        with patch.dict(os.environ, {"AESOP_QUEUE_ROOT": str(self.unmarked)}):
            with patch.object(self.mt, "print"):
                ok = self.mt.create_integration_branch("b-queue", cwd=str(self.unmarked))
        self.assertTrue(ok)
        self.assertTrue(self._branch_exists("integrate/b-queue"))

    def test_run_integration_train_without_authorised_cwd_mutates_nothing(self):
        # Process cwd is the sandbox ro worktree, never the real repo the test runs from.
        os.chdir(str(self.ro))
        with patch.object(self.mt, "check_enforce_admins", return_value=True), \
             patch.object(self.mt, "_check_halt", return_value=None), \
             patch.object(self.mt, "print"):
            result = self.mt.run_integration_train([1], "b-train", dry_run=True)
        self.assertFalse(result)
        self.assertFalse(self._branch_exists("integrate/b-train"))


if __name__ == "__main__":
    unittest.main()
