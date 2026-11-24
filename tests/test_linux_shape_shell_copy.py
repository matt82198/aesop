#!/usr/bin/env python3
"""
Fail-closed tests for the WSL copy step, the prelude's fail path, and copy cleanup.

INDEX: The WSL shell leg runs its suite on a copy of the worktree. A copy that fails
partway must fail the leg, never let the suite run on a partial tree. A git failure while
building that copy must be reported as a failure with a reason. Cleanup must never hand an
empty or foreign path to pkill or rm.

Test cases:
  - a source read that fails makes the copy step exit non-zero (bash run locally)
  - a good copy succeeds and produces the file (bash run locally)
  - a git failure in the prelude fails closed (rc != 0, reason named) and never runs wsl.exe
  - cleanup runs neither pkill nor rm for an empty or foreign path
  - cleanup runs both commands for this run's own copy dir
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import linux_shape_check as lsc


def _bash_script(script: str) -> subprocess.CompletedProcess:
    """Run a bash snippet locally. Paths in the snippet are POSIX-style, so pass them as as_posix()."""
    return subprocess.run(["bash", "-c", script], capture_output=True, timeout=60)


class TestCopyStepFailsClosed(unittest.TestCase):

    def test_failed_source_read_fails_the_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp, "dest")
            dest.mkdir()
            missing = Path(tmp, "no-such-source").as_posix()
            res = _bash_script(lsc.wsl_copy_snippet(missing, dest.as_posix()))
        self.assertNotEqual(res.returncode, 0, res.stderr)
        self.assertIn(b"WSL copy step failed", res.stderr)

    def test_good_copy_succeeds_and_copies_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp, "src")
            dest = Path(tmp, "dest")
            src.mkdir()
            dest.mkdir()
            (src / "marker.txt").write_text("ok", encoding="utf-8")
            res = _bash_script(lsc.wsl_copy_snippet(src.as_posix(), dest.as_posix()))
            copied = (dest / "marker.txt").exists()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(copied)


class TestPreludeFailureIsFailClosed(unittest.TestCase):

    def test_git_failure_in_prelude_fails_closed_without_running_wsl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A linked worktree: .git is a file, so the prelude path (and its git calls) runs.
            (root / ".git").write_text("gitdir: C:/fake/.git/worktrees/x\n", encoding="utf-8")
            calls = []

            def boom(argv, **kwargs):
                calls.append(argv)
                raise OSError("git unavailable for test")

            with mock.patch("linux_shape_check.subprocess.run", side_effect=boom):
                rc, output = lsc.run_shell_tests_wsl(root, "/mnt/c/fake/aesop")
        self.assertNotEqual(rc, 0)
        self.assertIn("git unavailable for test", output)
        self.assertFalse(any(argv[0] == "wsl.exe" for argv in calls), calls)


class TestCleanupGuard(unittest.TestCase):

    def _cleanup_calls(self, parent):
        with mock.patch("linux_shape_check.subprocess.run") as run:
            lsc._cleanup_wsl_copy(parent)
        return run.call_args_list

    def test_empty_parent_runs_no_command(self):
        self.assertEqual(self._cleanup_calls(""), [])

    def test_foreign_paths_run_no_command(self):
        for bad in ("/", "/tmp", "/tmp/", "/home/u", "/tmp/aesop-shell-leg-", "/tmp/aesop-shell-leg-XYZ",
                    "/tmp/aesop-shell-leg-0123456789ab/../../etc", "/tmp/aesop-shell-leg-0123456789ab; rm -rf /"):
            with self.subTest(parent=bad):
                self.assertEqual(self._cleanup_calls(bad), [])

    def test_own_copy_dir_gets_pkill_then_rm(self):
        parent = f"/tmp/aesop-shell-leg-{uuid.uuid4().hex[:12]}"
        calls = self._cleanup_calls(parent)
        cmds = [c.args[0][-1] for c in calls]
        self.assertEqual(len(cmds), 2, cmds)
        self.assertIn("pkill -f", cmds[0])
        self.assertEqual(cmds[1], f"rm -rf {parent}")


if __name__ == "__main__":
    unittest.main()
