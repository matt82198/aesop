#!/usr/bin/env python3
"""Tests for tools/branch_freshness_check.py -- pre-merge branch freshness gate.

Root cause reproduced: aesop main disabled 'strict / up-to-date with base
branch' (2026-07-30), so GitHub's mergeStateStatus stops reporting BEHIND
for stale branches and tools/merge_train.py's existing BEHIND-handling
never fires. Branches forked between c1000e9 (introduced the
`if args.message:` bug in tools/commit_lint.py) and 0dc79da (fixed it to
`if args.message is not None:`) that never merged/rebased main since kept
failing tests/test_commit_lint.py::test_cli_empty_message_explicit with
stale CI. This gate re-derives staleness directly from local git history
(merge-base + rev-list), independent of GitHub's mergeStateStatus.

Uses a local bare "origin" repo (file:// path, no network) so tests are
hermetic. Never touches global/user git config (--local only, isolated
temp HOME) per test/process isolation hygiene.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from branch_freshness_check import (
    check_branch_freshness,
    run_check,
)

REPO_ROOT = str(Path(__file__).parent.parent)


def _git(args, cwd, env=None, check=True):
    result = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", env=env, timeout=30,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result


class BranchFreshnessFixture(unittest.TestCase):
    """Builds a hermetic origin (bare) + working-copy repo pair per test."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.bare = base / "origin.git"
        self.work = base / "work"
        self.bare.mkdir()
        self.work.mkdir()

        # Isolated HOME so global git config never leaks in/out (test-hygiene).
        self.env = dict(os.environ)
        self.env["HOME"] = str(base)
        self.env["USERPROFILE"] = str(base)
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"

        _git(["init", "--bare", "--initial-branch=main", str(self.bare)], cwd=str(base), env=self.env)
        _git(["init", "--initial-branch=main", str(self.work)], cwd=str(base), env=self.env)
        _git(["config", "--local", "user.name", "Test User"], cwd=str(self.work), env=self.env)
        _git(["config", "--local", "user.email", "test@example.com"], cwd=str(self.work), env=self.env)
        _git(["remote", "add", "origin", str(self.bare)], cwd=str(self.work), env=self.env)

        (self.work / "README.md").write_text("v0\n", encoding="utf-8")
        _git(["add", "README.md"], cwd=str(self.work), env=self.env)
        _git(["commit", "-m", "chore: initial commit"], cwd=str(self.work), env=self.env)
        _git(["push", "-u", "origin", "main"], cwd=str(self.work), env=self.env)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _commit(self, filename, content, message):
        (self.work / filename).write_text(content, encoding="utf-8")
        _git(["add", filename], cwd=str(self.work), env=self.env)
        _git(["commit", "-m", message], cwd=str(self.work), env=self.env)

    def _push(self, branch):
        _git(["push", "origin", branch], cwd=str(self.work), env=self.env)

    def _make_stale_branch(self, branch="feature/stale"):
        """Fork a branch from main, push it, then advance main further
        (mirrors the incident: branch forked before a main-only fix)."""
        _git(["checkout", "-b", branch], cwd=str(self.work), env=self.env)
        self._commit("feature.txt", "wip\n", "feat: wip on branch")
        self._push(branch)

        _git(["checkout", "main"], cwd=str(self.work), env=self.env)
        self._commit("commit_lint_fix.txt", "fixed\n",
                     "fix: 4 verified findings from adversarial audit")
        self._push("main")
        return branch


class TestCheckBranchFreshness(BranchFreshnessFixture):
    """Unit-level: check_branch_freshness() against one branch."""

    def test_stale_branch_is_caught(self):
        """Defect caught: a branch missing a later main-only fix is flagged."""
        branch = self._make_stale_branch()
        result = check_branch_freshness(branch, root=str(self.work), max_behind=0)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["stale"])
        self.assertEqual(result["behind"], 1)

    def test_fresh_branch_passes(self):
        """Clean input passes: a branch at the same commit as main is fresh."""
        _git(["checkout", "-b", "feature/fresh"], cwd=str(self.work), env=self.env)
        self._push("feature/fresh")
        result = check_branch_freshness("feature/fresh", root=str(self.work), max_behind=0)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(result["stale"])
        self.assertEqual(result["behind"], 0)

    def test_rebased_branch_passes(self):
        """A branch that incorporated the later main fix is no longer stale."""
        branch = self._make_stale_branch()
        _git(["checkout", branch], cwd=str(self.work), env=self.env)
        _git(["merge", "origin/main", "--no-edit"], cwd=str(self.work), env=self.env)
        self._push(branch)
        result = check_branch_freshness(branch, root=str(self.work), max_behind=0)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(result["stale"])

    def test_unknown_branch_reports_error_not_stale(self):
        """A branch that doesn't exist is a could-not-evaluate, not a false 'fresh'."""
        result = check_branch_freshness("does/not/exist", root=str(self.work), max_behind=0)
        self.assertFalse(result["ok"])
        self.assertFalse(result["stale"])
        self.assertIsNotNone(result["error"])


class TestRunCheckCLIContract(BranchFreshnessFixture):
    """Integration: run_check() exit-code contract (0/1/2)."""

    def test_defect_caught_via_run_check(self):
        branch = self._make_stale_branch()
        exit_code, report = run_check(branch_names=[branch], root=str(self.work))
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(report["findings"]), 1)
        self.assertIn(branch, report["findings"][0])

    def test_clean_input_exits_zero(self):
        _git(["checkout", "-b", "feature/fresh2"], cwd=str(self.work), env=self.env)
        self._push("feature/fresh2")
        exit_code, report = run_check(branch_names=["feature/fresh2"], root=str(self.work))
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["findings"], [])

    def test_zero_input_exits_two(self):
        """No --pr/--branch/--all-open at all: could-not-evaluate, never a
        silent 0 for having scanned nothing."""
        exit_code, report = run_check(root=str(self.work))
        self.assertEqual(exit_code, 2)
        self.assertTrue(len(report["errors"]) > 0)

    def test_unresolvable_branch_exits_two(self):
        """An explicit branch that can't be resolved is could-not-evaluate,
        not a clean pass."""
        exit_code, report = run_check(
            branch_names=["totally/nonexistent/branch"], root=str(self.work)
        )
        self.assertEqual(exit_code, 2)

    def test_all_open_empty_result_is_legitimately_clean(self):
        """--all-open succeeding with zero open PRs is exit 0 (it DID scan;
        there was nothing to find), distinct from a failed gh call."""
        # Simulate by monkeypatching resolve_all_open_prs indirectly via a
        # gh-less PATH: gh unavailable must be exit 2 (could not evaluate),
        # never silently 0.
        import branch_freshness_check as bfc
        original = bfc.resolve_all_open_prs
        try:
            bfc.resolve_all_open_prs = lambda root=None: ([], None)
            exit_code, report = run_check(all_open=True, root=str(self.work))
            self.assertEqual(exit_code, 0)
        finally:
            bfc.resolve_all_open_prs = original

    def test_gh_unavailable_for_all_open_exits_two(self):
        """gh missing/erroring for --all-open must be could-not-evaluate,
        never a silent clean pass."""
        import branch_freshness_check as bfc
        original = bfc.resolve_all_open_prs
        try:
            bfc.resolve_all_open_prs = lambda root=None: (None, "gh: command not found")
            exit_code, report = run_check(all_open=True, root=str(self.work))
            self.assertEqual(exit_code, 2)
        finally:
            bfc.resolve_all_open_prs = original

    def test_max_behind_threshold_respected(self):
        """A branch within the configured tolerance is not flagged."""
        branch = self._make_stale_branch()
        exit_code, report = run_check(
            branch_names=[branch], root=str(self.work), max_behind=5
        )
        self.assertEqual(exit_code, 0)


class TestCLISubprocess(BranchFreshnessFixture):
    """Exercises the real CLI entry point via subprocess (not just imports)."""

    def test_cli_json_output_on_stale_branch(self):
        branch = self._make_stale_branch()
        result = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "tools", "branch_freshness_check.py"),
             "--branch", branch, "--root", str(self.work), "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(len(data["findings"]), 1)

    def test_cli_zero_args_exits_two(self):
        result = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "tools", "branch_freshness_check.py"),
             "--root", str(self.work)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_cli_clean_branch_exits_zero(self):
        _git(["checkout", "-b", "feature/cli-fresh"], cwd=str(self.work), env=self.env)
        self._push("feature/cli-fresh")
        result = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "tools", "branch_freshness_check.py"),
             "--branch", "feature/cli-fresh", "--root", str(self.work)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
