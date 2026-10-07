"""Tests for tools/new_gate_check.py -- the one-command new-gate checklist runner.

PR #872 (a new pre-push gate) took five red CI rounds, each a different
checklist item the author had no way to run locally in one shot. This suite
exercises the runner itself with every sub-check MOCKED (never shelling out
to the real, slow gate scripts), proving:

  1. all green -> exit 0, PASS table, no fix commands printed.
  2. one row failing -> exit 1, that row marked FAIL, and ITS OWN fix command
     (and only its own) printed in the "Fix commands for red rows" section.
  3. a row whose check function raises is still reported as a FAIL row (never
     crashes the whole run) with a recognizable internal-error detail.
  4. --json emits a machine-readable report with the same pass/fail shape.
  5. the real Windows-bash-resolution helper (_resolve_argv0) prefers a
     shutil.which() hit over a bare executable name, so a bare "bash" can
     never be intercepted by the WSL App Execution Alias stub.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import new_gate_check as ngc  # noqa: E402


def _ok(repo_root, env):
    return True, "all good", "no fix needed"


def _fail(fix_text):
    def _inner(repo_root, env):
        return False, "boom: something is wrong", fix_text
    return _inner


def _raises(repo_root, env):
    raise RuntimeError("sub-check exploded")


class PatchedChecksTestCase(unittest.TestCase):
    """Base class: replaces ngc.CHECKS with a small deterministic set."""

    def patch_checks(self, checks):
        patcher = mock.patch.object(ngc, "CHECKS", checks)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestAllGreen(PatchedChecksTestCase):
    def test_all_passing_checks_exit_zero_with_no_fix_commands(self):
        self.patch_checks([
            ("a", "check A", _ok),
            ("b", "check B", _ok),
        ])
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = ngc.main(["--root", REPO_ROOT])
        self.assertEqual(rc, 0)
        out = buf_out.getvalue()
        self.assertIn("[PASS] check A", out)
        self.assertIn("[PASS] check B", out)
        self.assertIn("RESULT: PASS (2/2 rows green)", out)
        self.assertNotIn("Fix commands", out)


class TestOneRedRow(PatchedChecksTestCase):
    def test_one_failing_check_exits_one_with_its_fix_command(self):
        self.patch_checks([
            ("a", "check A", _ok),
            ("b", "check B", _fail("run: fix-the-b-thing --now")),
            ("c", "check C", _ok),
        ])
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = ngc.main(["--root", REPO_ROOT])
        self.assertEqual(rc, 1)
        out = buf_out.getvalue()
        self.assertIn("[PASS] check A", out)
        self.assertIn("[FAIL] check B", out)
        self.assertIn("[PASS] check C", out)
        self.assertIn("RESULT: FAIL (2/3 rows green)", out)
        self.assertIn("Fix commands for red rows:", out)
        self.assertIn("fix-the-b-thing --now", out)
        # Only the failing row's fix command is printed.
        self.assertNotIn("check A:\n    ", out)
        self.assertNotIn("check C:\n    ", out)

    def test_captured_output_for_red_rows_goes_to_stderr(self):
        self.patch_checks([
            ("b", "check B", _fail("fix command here")),
        ])
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = ngc.main(["--root", REPO_ROOT])
        self.assertEqual(rc, 1)
        self.assertIn("boom: something is wrong", buf_err.getvalue())
        self.assertNotIn("boom: something is wrong", buf_out.getvalue())


class TestCrashingSubCheck(PatchedChecksTestCase):
    def test_a_raising_check_is_reported_as_fail_not_a_crash(self):
        self.patch_checks([
            ("a", "check A", _ok),
            ("boom", "check that explodes", _raises),
        ])
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = ngc.main(["--root", REPO_ROOT])
        self.assertEqual(rc, 1)
        out = buf_out.getvalue()
        self.assertIn("[FAIL] check that explodes", out)
        self.assertIn("RESULT: FAIL (1/2 rows green)", out)


class TestJsonMode(PatchedChecksTestCase):
    def test_json_report_shape(self):
        self.patch_checks([
            ("a", "check A", _ok),
            ("b", "check B", _fail("do the fix")),
        ])
        buf_out = io.StringIO()
        with redirect_stdout(buf_out):
            rc = ngc.main(["--root", REPO_ROOT, "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(buf_out.getvalue())
        self.assertFalse(data["ok"])
        by_key = {r["key"]: r for r in data["results"]}
        self.assertTrue(by_key["a"]["ok"])
        self.assertIsNone(by_key["a"]["fix"])
        self.assertFalse(by_key["b"]["ok"])
        self.assertEqual(by_key["b"]["fix"], "do the fix")


class TestUsageErrors(unittest.TestCase):
    def test_nonexistent_root_exits_two(self):
        buf_err = io.StringIO()
        with redirect_stderr(buf_err):
            rc = ngc.main(["--root", os.path.join(REPO_ROOT, "no-such-dir-at-all")])
        self.assertEqual(rc, 2)
        self.assertIn("not a directory", buf_err.getvalue())


class TestBashResolution(unittest.TestCase):
    """Guards the Windows WSL-alias-stub fix (tools/test_isolation_tripwire.py
    hit this first): a bare "bash" must be pre-resolved through shutil.which()
    before reaching subprocess.run, so the WSL App Execution Alias stub can
    never intercept it."""

    def test_bare_name_is_resolved_via_which(self):
        with mock.patch.object(ngc.shutil, "which", return_value=r"C:\Program Files\Git\bin\bash.exe"):
            resolved = ngc._resolve_argv0(["bash", "hooks/pre-push-policy.sh", "--test"])
        self.assertEqual(resolved[0], r"C:\Program Files\Git\bin\bash.exe")
        self.assertEqual(resolved[1:], ["hooks/pre-push-policy.sh", "--test"])

    def test_unresolvable_name_is_left_unchanged(self):
        with mock.patch.object(ngc.shutil, "which", return_value=None):
            resolved = ngc._resolve_argv0(["totally-made-up-binary", "--flag"])
        self.assertEqual(resolved, ["totally-made-up-binary", "--flag"])

    def test_empty_command_is_left_unchanged(self):
        self.assertEqual(ngc._resolve_argv0([]), [])


class TestRealCheckWiring(unittest.TestCase):
    """Sanity: CHECKS still references the ten documented rows, each a
    callable (not re-running the real slow gates -- just shape checks)."""

    def test_checks_list_has_the_documented_rows(self):
        self.assertEqual(len(ngc.CHECKS), 10)
        keys = [key for key, _label, _fn in ngc.CHECKS]
        self.assertEqual(len(keys), len(set(keys)), "duplicate check keys")
        for key, label, fn in ngc.CHECKS:
            self.assertTrue(callable(fn), key)
            self.assertIsInstance(label, str)
            self.assertTrue(label)


class TestDryPrepushHeadPreservation(unittest.TestCase):
    """Verify that check_dry_prepush does NOT switch the caller's HEAD or index.

    Regression test for the bug where new_gate_check.py's dry pre-push simulation
    would check out an unrelated branch (e.g., a merge-queue integration branch),
    leaving the worktree on that branch when it finished.
    """

    def test_dry_prepush_does_not_mutate_head_or_working_tree(self):
        """Run dry pre-push in a temp repo with multiple branches; verify HEAD unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            repo_root = tmppath / "test-repo"
            repo_root.mkdir()

            # Initialize the git repo with a main branch
            subprocess.run(
                ["git", "init", "-q"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )

            # Create initial commit on main
            (repo_root / "file.txt").write_text("initial content\n")
            subprocess.run(
                ["git", "add", "file.txt"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-q", "-m", "initial"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )

            # Create a feature branch
            subprocess.run(
                ["git", "checkout", "-q", "-b", "feature/test"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            (repo_root / "file.txt").write_text("feature content\n")
            subprocess.run(
                ["git", "add", "file.txt"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-q", "-m", "feature commit"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )

            # Create a second branch (simulating a merge-queue or other unrelated branch)
            subprocess.run(
                ["git", "checkout", "-q", "-b", "integrate/batch"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            (repo_root / "other.txt").write_text("other branch\n")
            subprocess.run(
                ["git", "add", "other.txt"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-q", "-m", "integrate commit"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )

            # Switch back to feature branch (the branch we'll be "pushing" from)
            subprocess.run(
                ["git", "checkout", "-q", "feature/test"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )

            # Create hooks directory and a minimal pre-push-policy.sh script
            hooks_dir = repo_root / "hooks"
            hooks_dir.mkdir(exist_ok=True)
            hook_script = hooks_dir / "pre-push-policy.sh"
            # Create a minimal hook that just exits 0 (pass)
            hook_script.write_text("#!/bin/bash\nexit 0\n")
            hook_script.chmod(0o755)

            # Verify we're on feature/test before the dry pre-push check
            result_before = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            branch_before = result_before.stdout.strip()
            self.assertEqual(branch_before, "feature/test",
                           "Should be on feature/test branch before check")

            # Get working tree status before
            status_before = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            porcelain_before = status_before.stdout

            # Run the dry pre-push check
            env = dict(os.environ)
            env["AESOP_ROOT"] = str(repo_root)
            ok, detail, fix = ngc.check_dry_prepush(repo_root, env)

            # Note: The check might fail for various reasons (missing pre-push hook,
            # no origin remote, etc.), but that's okay. We only care that HEAD didn't move.

            # Verify HEAD is STILL on feature/test after the check
            result_after = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            branch_after = result_after.stdout.strip()
            self.assertEqual(branch_after, "feature/test",
                           f"HEAD should still be on feature/test, but it's on {branch_after}")
            self.assertEqual(
                branch_before, branch_after,
                "check_dry_prepush must not change the checked-out branch"
            )

            # Verify working tree status is unchanged
            status_after = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            porcelain_after = status_after.stdout
            self.assertEqual(
                porcelain_before, porcelain_after,
                "check_dry_prepush must not modify the working tree"
            )


if __name__ == "__main__":
    unittest.main()
