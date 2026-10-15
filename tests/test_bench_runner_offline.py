#!/usr/bin/env python3
"""Offline seam for the claude-CLI runners in tools/bench_runner.py.

AESOP_BENCH_OFFLINE=1 must block every paid path. The claude-CLI runners
(`--runner haiku|sonnet|opus`) shell `claude -p ...`, which is paid. With the
seam set they must refuse before building or spawning any command.

The subprocess layer and `shutil.which` are faked throughout, so no real
`claude` process is ever executed by this suite.
"""
import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import bench_runner  # noqa: E402

_SEAM_ENV = "AESOP_BENCH_OFFLINE"
_FAKE_STDOUT = '{"result": "x", "usage": {"output_tokens": 3}}'


def _fake_completed(args, **kwargs):
    return subprocess.CompletedProcess(args, 0, stdout=_FAKE_STDOUT, stderr="")


class _ClaudeCliFixture(unittest.TestCase):
    """Registers a claude-CLI runner, fakes `claude` on PATH and the spawn."""

    def setUp(self):
        env_patch = mock.patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop(_SEAM_ENV, None)

        reg_patch = mock.patch.dict(
            bench_runner.RUNNERS,
            {"haiku": bench_runner._make_claude_runner("haiku")},
        )
        reg_patch.start()
        self.addCleanup(reg_patch.stop)

        which_patch = mock.patch.object(bench_runner.shutil, "which", return_value="/fake/bin/claude")
        which_patch.start()
        self.addCleanup(which_patch.stop)

        self.spawn = mock.Mock(side_effect=_fake_completed)
        run_patch = mock.patch.object(bench_runner.subprocess, "run", self.spawn)
        run_patch.start()
        self.addCleanup(run_patch.stop)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = bench_runner.main(argv)
        return rc, out.getvalue(), err.getvalue()


class TestOfflineRefusesClaudeCliRunner(_ClaudeCliFixture):
    """With AESOP_BENCH_OFFLINE=1 the claude-CLI runner never reaches spawn."""

    def test_main_offline_refuses_cli_runner_before_any_spawn(self):
        os.environ[_SEAM_ENV] = "1"
        rc, _out, _err = self.run_main(["--runner", "haiku"])
        self.spawn.assert_not_called()
        self.assertNotEqual(rc, 0)

    def test_main_offline_refusal_is_one_clear_stderr_line(self):
        os.environ[_SEAM_ENV] = "1"
        rc, _out, err = self.run_main(["--runner", "haiku"])
        lines = [line for line in err.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, msg=f"stderr was: {err!r}")
        self.assertIn(_SEAM_ENV, lines[0])
        self.assertIn("claude", lines[0])
        self.assertNotEqual(rc, 0)

    def test_runner_direct_call_offline_raises_before_any_spawn(self):
        os.environ[_SEAM_ENV] = "1"
        runner = bench_runner._make_claude_runner("haiku")
        with self.assertRaises(RuntimeError) as ctx:
            runner("hello")
        self.assertIn(_SEAM_ENV, str(ctx.exception))
        self.spawn.assert_not_called()

    def test_mock_runner_still_runs_offline(self):
        os.environ[_SEAM_ENV] = "1"
        rc, out, _err = self.run_main(["--runner", "mock"])
        self.spawn.assert_not_called()
        self.assertEqual(rc, 0)
        self.assertIn("Accuracy:", out)


class TestOnlineClaudeCliRunnerUnchanged(_ClaudeCliFixture):
    """With the seam unset the claude-CLI runner builds the same argv as before."""

    def test_main_online_builds_claude_argv_and_spawns(self):
        rc, _out, _err = self.run_main(["--runner", "haiku"])
        self.assertEqual(rc, 0)
        self.assertGreater(self.spawn.call_count, 0)
        argv = self.spawn.call_args_list[0].args[0]
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertEqual(argv[3:], ["--model", "haiku", "--output-format", "json"])


if __name__ == "__main__":
    unittest.main()
