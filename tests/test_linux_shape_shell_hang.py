#!/usr/bin/env python3
"""
Red-first tests for the WSL shell leg hang diagnostics in linux_shape_check.py.

INDEX: A hung shell test must fail the WSL shell leg with the NAME of the test that hung,
and the leg must never hand the WSL-side suite a stdin it can block on.

Why: the leg's only signal on a timeout was "Shell tests timed out after 300s", which
named nothing. The runner prints "[TEST] Running: <file>" before each test; that line is
the only evidence of which test was live when the leg was killed, and the timeout path
threw it away. A stdin inherited from the pre-push pipe is a second way for a test to wait
on input forever.

Test cases:
  - a timeout names the last "[TEST] Running:" test as the hung test
  - a timeout with no runner marker still fails closed with exit 124
  - the WSL shell leg runs with stdin closed (subprocess.DEVNULL)
  - the WSL shell leg writes output to a file, not a pipe that a stuck child can hold open
"""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import linux_shape_check as lsc

ANY_DIR = Path(__file__).parent  # the prelude is mocked out; only the .git check would read it


def _hanging_run(partial: bytes, seen: dict):
    """Stand-in for subprocess.run: writes partial output to the caller's stdout file, then hangs."""
    def fake_run(argv, **kwargs):
        seen["kwargs"] = kwargs
        kwargs["stdout"].write(partial)
        kwargs["stdout"].flush()
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))
    return fake_run


def _run_hanging(partial: bytes):
    seen = {}
    with mock.patch.object(lsc, "wsl_worktree_prelude", return_value=("", "")), \
            mock.patch("linux_shape_check.subprocess.run", side_effect=_hanging_run(partial, seen)):
        rc, output = lsc.run_shell_tests_wsl(ANY_DIR, "/mnt/c/fake/aesop")
    return rc, output, seen["kwargs"]


class TestHungTestIsNamed(unittest.TestCase):

    def test_timeout_names_the_test_that_was_running(self):
        partial = (
            b"[TEST] Running: tests/test_waveguard.sh\n"
            b"[PASS] tests/test_waveguard.sh\n"
            b"[TEST] Running: tests/test-run-watchdog.sh\n"
            b"partial output with no newline"
        )
        rc, output, _ = _run_hanging(partial)
        self.assertEqual(rc, 124)
        self.assertIn("hung test: tests/test-run-watchdog.sh", output)

    def test_timeout_without_runner_marker_still_fails_closed(self):
        rc, output, _ = _run_hanging(b"")
        self.assertEqual(rc, 124)
        self.assertIn("timed out", output)


class TestWslShellLegIsolation(unittest.TestCase):

    def _kwargs(self):
        seen = {}

        def ok_run(argv, **kwargs):
            seen["kwargs"] = kwargs
            return subprocess.CompletedProcess(args=argv, returncode=0)

        with mock.patch.object(lsc, "wsl_worktree_prelude", return_value=("", "")), \
                mock.patch("linux_shape_check.subprocess.run", side_effect=ok_run):
            lsc.run_shell_tests_wsl(ANY_DIR, "/mnt/c/fake/aesop")
        return seen["kwargs"]

    def test_shell_leg_stdin_is_closed(self):
        self.assertIs(self._kwargs().get("stdin"), subprocess.DEVNULL)

    def test_shell_leg_output_goes_to_a_file_not_a_pipe(self):
        kw = self._kwargs()
        self.assertNotIn("capture_output", kw)
        self.assertIsNot(kw.get("stdout"), subprocess.PIPE)
        self.assertIsNotNone(kw.get("stdout"))


if __name__ == "__main__":
    unittest.main()
