#!/usr/bin/env python3
"""Test suite for driver/cli_worker_driver.py -- offline, no vendor CLI needed.

A fake Python CLI (written into a tmp dir) stands in for a headless vendor
coding CLI. The driver runs it as a bounded subprocess, prompt on stdin, in a
git repo created under tmp_path.

Tests:
  1. probe_capabilities() reports a non-agentic, non-harness backend
  2. build_driver({"backend": "cli", ...}) returns a CliWorkerDriver
  3. seats.worker promotion accepts backend 'cli'
  4. dispatch_worker success writes an owned file and reports it
  5. dispatch_worker rejects a write outside owned_files (ownership)
  6. dispatch_worker reports a non-zero CLI exit as ok=False
  7. dispatch_worker is bounded: a hanging CLI is killed at timeout_s
  8. dispatch_item green only from run_command exit 0 (never from the CLI)

Constraints:
  - stdlib unittest only (no pytest)
  - ASCII-only
  - Windows + Linux parity
"""

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

DRIVER_DIR = Path(__file__).resolve().parent.parent / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from agent_driver import WorkerRequest  # noqa: E402
from backend_config import build_driver  # noqa: E402
from cli_worker_driver import CliWorkerDriver  # noqa: E402
from wave_bridge import dispatch_item  # noqa: E402

FAKE_CLI_SOURCE = """\
import pathlib
import sys
import time

mode = sys.argv[1]
target = sys.argv[2] if len(sys.argv) > 2 else ""
sys.stdin.read()  # the prompt arrives on stdin
if mode == "write":
    pathlib.Path(target).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(target).write_text("written by cli\\n", encoding="utf-8")
elif mode == "fail":
    sys.stderr.write("boom\\n")
    sys.exit(3)
elif mode == "hang":
    time.sleep(60)
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


class _CliRepoFixture(unittest.TestCase):
    """Shared setup: a git repo in tmp_path plus a fake CLI script."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "test@example.invalid")
        _git(self.repo, "config", "user.name", "test")
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git(self.repo, "add", "seed.txt")
        _git(self.repo, "commit", "-q", "-m", "seed")
        self.fake = self.root / "fake_cli.py"
        self.fake.write_text(FAKE_CLI_SOURCE, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_driver(self, mode: str, target: str = "", timeout_s: float = 30.0):
        argv = [sys.executable, str(self.fake), mode]
        if target:
            argv.append(target)
        return CliWorkerDriver(argv=argv, timeout_s=timeout_s)


class TestCliWorkerCapabilities(_CliRepoFixture):
    """Capability probe and config wiring."""

    def test_probe_reports_non_agentic_backend(self):
        caps = self.make_driver("write").probe_capabilities()
        self.assertFalse(caps.worker_filesystem_access)
        self.assertEqual(caps.name, "cli")

    def test_build_driver_returns_cli_worker_driver(self):
        driver = build_driver(
            {"backend": "cli", "argv": [sys.executable, str(self.fake), "write"]}
        )
        self.assertIsInstance(driver, CliWorkerDriver)

    def test_seats_worker_promotion_accepts_cli(self):
        driver = build_driver(
            {
                "seats": {
                    "worker": {
                        "backend": "cli",
                        "argv": [sys.executable, str(self.fake), "write"],
                    }
                }
            }
        )
        self.assertIsInstance(driver, CliWorkerDriver)


class TestCliWorkerDispatch(_CliRepoFixture):
    """dispatch_worker: ownership, exit codes, bounded runtime."""

    def test_success_writes_owned_file_and_reports_it(self):
        driver = self.make_driver("write", "out/owned.txt")
        result = driver.dispatch_worker(
            WorkerRequest(
                prompt="write the file",
                owned_files=("out/owned.txt",),
                workdir=str(self.repo),
            )
        )
        self.assertTrue(result.ok, result.error)
        self.assertIn("out/owned.txt", result.files_written)
        self.assertTrue((self.repo / "out" / "owned.txt").exists())

    def test_write_outside_owned_files_is_rejected(self):
        driver = self.make_driver("write", "other/stray.txt")
        result = driver.dispatch_worker(
            WorkerRequest(
                prompt="write the file",
                owned_files=("out/owned.txt",),
                workdir=str(self.repo),
            )
        )
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertIn("other/stray.txt", result.error)

    def test_nonzero_cli_exit_is_not_ok(self):
        driver = self.make_driver("fail")
        result = driver.dispatch_worker(
            WorkerRequest(
                prompt="fail",
                owned_files=("out/owned.txt",),
                workdir=str(self.repo),
            )
        )
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)

    def test_hanging_cli_is_killed_at_timeout(self):
        driver = self.make_driver("hang", timeout_s=1.0)
        start = time.monotonic()
        result = driver.dispatch_worker(
            WorkerRequest(
                prompt="hang",
                owned_files=("out/owned.txt",),
                workdir=str(self.repo),
            )
        )
        elapsed = time.monotonic() - start
        self.assertFalse(result.ok)
        self.assertLess(elapsed, 30.0)


class TestCliWorkerBridge(_CliRepoFixture):
    """dispatch_item must decide green only from the test command exit code."""

    def test_green_only_from_run_command_exit_zero(self):
        driver = self.make_driver("write", "out/owned.txt")
        item = {
            "slug": "cli-green",
            "ownsFiles": ["out/owned.txt"],
            "prompt": "write the file",
            "testCmd": f'"{sys.executable}" -c "import sys; sys.exit(0)"',
            "workDir": str(self.repo),
        }
        out = dispatch_item(driver, item)
        self.assertEqual(out["route"], "driver")
        self.assertTrue(out["ok"])
        self.assertEqual(out["testExit"], 0)
        self.assertTrue(out["verified"])

    def test_red_when_test_command_fails(self):
        driver = self.make_driver("write", "out/owned.txt")
        item = {
            "slug": "cli-red",
            "ownsFiles": ["out/owned.txt"],
            "prompt": "write the file",
            "testCmd": f'"{sys.executable}" -c "import sys; sys.exit(4)"',
            "workDir": str(self.repo),
        }
        out = dispatch_item(driver, item)
        self.assertFalse(out["ok"])
        self.assertEqual(out["testExit"], 4)
        self.assertFalse(out["verified"])


if __name__ == "__main__":
    unittest.main()
