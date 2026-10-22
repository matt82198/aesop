#!/usr/bin/env python3
"""
Behavioural tests for tools/receipt_autoflush.py and the watchdog receipt-flush hook-in.

- The detached flusher polls GitHub for a spooled sha (404 then 200) and posts the
  envelope exactly once; it keeps the spool when the sha never appears and logs why.
- A real detached child (--spawn) writes its outcome to <sha>.flush.log and the spawn
  itself returns at once.
- One `run-watchdog.sh --once` cycle posts a spooled receipt, with gh stubbed on disk.

No real origin, no real gh: the GitHub side is a fake callable (unit) or a stub
executable selected by AESOP_GH_BIN (subprocess).
"""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test", TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = _load("receipt_common")
autoflush = _load("receipt_autoflush")
rf = _load("receipt_flush")


def _hmac_secret():
    return "unit" + "-" + "test" + "-" + "autoflush" + "-" + "value" + "-" + "0" * 16


SHA = "d" * 40
SLUG = "example-owner/fixture"


def _envelope(head_sha=SHA):
    receipt = {
        "schema": 1,
        "schema_version": 1,
        "repo": SLUG,
        "head_sha": head_sha,
        "base_sha": "b" * 40,
        "tree_hash": "c" * 40,
        "parts": [{"name": "py-shard-0", "exit_code": 0, "test_count": 5, "duration_s": 1.0}],
        "skipped": [],
        "host": {"os": "linux", "python": "3.12", "hostname_hash": "abc123"},
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    sig = rc.sign(rc.canonical_json(receipt), scheme="hmac-sha256", hmac_secret=_hmac_secret())
    return {"receipt": receipt, "sig": sig}


class FakeGitHub:
    """gh runner: sha-existence answers come from a scripted list (last entry repeats)."""

    def __init__(self, sha_codes):
        self.sha_codes = list(sha_codes)
        self.calls = []
        self.posts = 0

    def __call__(self, args):
        self.calls.append(list(args))
        if args and args[0] == "api" and "/commits/" in args[1]:
            code = self.sha_codes.pop(0) if len(self.sha_codes) > 1 else self.sha_codes[0]
            if code == 200:
                return 0, '{"sha": "x"}', ""
            return 1, "", "gh: Not Found (HTTP 404)"
        if args and args[0] == "api" and "check-runs" in args[1]:
            self.posts += 1
            return 0, "{}", ""
        return 1, "", "unexpected gh call: %s" % " ".join(args)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TestPollAndFlush(unittest.TestCase):
    """Unit-level: the poll loop posts once the sha appears and keeps the spool otherwise."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.log = self.spool / (SHA + ".flush.log")
        self._env = mock.patch.dict(os.environ, {"AESOP_RECEIPT_SPOOL": str(self.spool)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _write_spool(self):
        path = self.spool / (SHA + ".json")
        path.write_text(json.dumps(_envelope(), sort_keys=True), encoding="utf-8")
        return path

    def test_posts_once_sha_appears_after_404(self):
        spool_file = self._write_spool()
        gh = FakeGitHub([404, 200])
        clock = FakeClock()
        outcome = autoflush.poll_and_flush(self.repo, SHA, SLUG, gh, interval=15, timeout=600,
                                           log_path=self.log, sleep=clock.sleep, clock=clock)
        self.assertEqual(outcome, "posted")
        self.assertEqual(gh.posts, 1, "exactly one check-run POST for the spooled receipt")
        self.assertFalse(spool_file.exists(), "spool file must be deleted after a successful post")
        self.assertEqual(clock.now, 15.0, "one 15s sleep between the 404 poll and the 200 poll")
        self.assertIn("posted receipt for", self.log.read_text(encoding="utf-8"))

    def test_gives_up_at_timeout_keeps_spool_and_logs_reason(self):
        spool_file = self._write_spool()
        gh = FakeGitHub([404])
        clock = FakeClock()
        outcome = autoflush.poll_and_flush(self.repo, SHA, SLUG, gh, interval=15, timeout=60,
                                           log_path=self.log, sleep=clock.sleep, clock=clock)
        self.assertEqual(outcome, "timeout")
        self.assertEqual(gh.posts, 0, "nothing is posted while the sha is missing")
        self.assertTrue(spool_file.exists(), "spool kept for the watchdog flush")
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("gave up", text)
        self.assertIn("not on GitHub after 60s", text)

    def test_no_spool_file_is_a_clean_noop(self):
        gh = FakeGitHub([200])
        clock = FakeClock()
        outcome = autoflush.poll_and_flush(self.repo, SHA, SLUG, gh, interval=15, timeout=600,
                                           log_path=self.log, sleep=clock.sleep, clock=clock)
        self.assertEqual(outcome, "gone")
        self.assertEqual(gh.calls, [], "no GitHub call when there is nothing to post")

    def test_gh_missing_is_reported_as_404_style_wait_not_crash(self):
        self._write_spool()
        missing = self.root / "no-such-gh-binary"
        with mock.patch.dict(os.environ, {"AESOP_GH_BIN": str(missing)}):
            code, _out, err = rf._default_gh_runner(["api", "repos/x/y/commits/" + SHA])
        self.assertNotEqual(code, 0)
        self.assertIn("gh not runnable", err)


class TestSpawnDetached(unittest.TestCase):
    """Real detached child: spawn returns at once; the child logs its outcome to <sha>.flush.log."""

    def test_spawn_returns_fast_and_child_logs_outcome(self):
        # ignore_cleanup_errors: the detached flusher can still hold <sha>.flush.log
        # open for a moment after it logs "gave up" (CI: WinError 32 on Windows,
        # runs 39821111420 and 39433621472). The assertions below read that log before
        # the context exits, so the outcome is still verified; only the post-test
        # removal of the temp dir tolerates a file the child has not yet released.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            spool = root / "spool"
            spool.mkdir()
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                            "https://github.com/%s.git" % SLUG], check=True)
            (spool / (SHA + ".json")).write_text(json.dumps(_envelope(), sort_keys=True), encoding="utf-8")
            env = dict(os.environ, AESOP_RECEIPT_SPOOL=str(spool),
                       AESOP_GH_BIN=str(root / "no-such-gh-binary"))
            with mock.patch.dict(os.environ, env, clear=True):
                started = time.monotonic()
                rc_main = autoflush.main(["--repo", str(repo), "--sha", SHA, "--spawn",
                                          "--interval", "1", "--timeout", "0"])
                spawn_seconds = time.monotonic() - started
            self.assertEqual(rc_main, 0)
            self.assertLess(spawn_seconds, 10, "spawn must return without waiting for the poller")
            log = spool / (SHA + ".flush.log")
            deadline = time.monotonic() + 30
            text = ""
            while time.monotonic() < deadline:
                text = log.read_text(encoding="utf-8") if log.exists() else ""
                if "gave up" in text or "no GitHub" in text:
                    break
                time.sleep(0.2)
            self.assertIn("gave up", text, "detached child must log why it stopped")
            self.assertTrue((spool / (SHA + ".json")).exists(), "spool kept when sha never appears")


def _bash():
    found = shutil.which("bash")
    if not found or "system32" in found.lower():
        return None
    return found


class TestWatchdogCycleFlushes(unittest.TestCase):
    """One `run-watchdog.sh --once` cycle posts a spooled receipt (gh stubbed on disk)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.aesop = self.root / "aesop"
        (self.aesop / "tools").mkdir(parents=True)
        (self.aesop / "state").mkdir()
        for name in ("receipt_flush.py", "emit_receipt.py", "receipt_common.py", "halt.py"):
            shutil.copy(TOOLS / name, self.aesop / "tools" / name)
        subprocess.run(["git", "-C", str(self.aesop), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.aesop), "remote", "add", "origin",
                        "https://github.com/%s.git" % SLUG], check=True)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.gh_log = self.root / "gh-calls.log"
        self.gh = self._write_gh_stub()

    def tearDown(self):
        self._tmp.cleanup()

    def _write_gh_stub(self):
        if os.name == "nt":
            path = self.root / "gh-stub.bat"
            path.write_text("@echo off\r\necho %* >> \"%GH_STUB_LOG%\"\r\necho {}\r\nexit /b 0\r\n",
                            encoding="utf-8")
            return path
        path = self.root / "gh-stub"
        path.write_text("#!/bin/sh\necho \"$*\" >> \"$GH_STUB_LOG\"\necho '{}'\nexit 0\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def test_one_cycle_posts_spooled_receipt(self):
        bash = _bash()
        if bash is None:
            self.skipTest("no usable bash (Git Bash) on PATH")
        (self.spool / (SHA + ".json")).write_text(json.dumps(_envelope(), sort_keys=True), encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "AESOP_ROOT": str(self.aesop),
            "AESOP_STATE_ROOT": str(self.aesop / "state"),
            "CONDUCTOR_ROOT": str(self.root / "conductor3"),
            "AESOP_RECEIPT_SPOOL": str(self.spool),
            "AESOP_GH_BIN": str(self.gh),
            "GH_STUB_LOG": str(self.gh_log),
            "AESOP_WATCHDOG_CYCLE_CMD": "true",
        })
        proc = subprocess.run([bash, str(REPO_ROOT / "daemons" / "run-watchdog.sh"), "--once"],
                              cwd=str(self.root), env=env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse((self.spool / (SHA + ".json")).exists(),
                         "the watchdog cycle must post and delete the spooled receipt")
        self.assertTrue(self.gh_log.exists(), "gh stub was never called")
        self.assertIn("check-runs", self.gh_log.read_text(encoding="utf-8"))
        backup_log = self.aesop / "state" / "FLEET-BACKUP.log"
        self.assertIn("flushed receipt", backup_log.read_text(encoding="utf-8"))


def suite():
    loader = unittest.TestLoader()
    s = unittest.TestSuite()
    for case in (TestPollAndFlush, TestSpawnDetached, TestWatchdogCycleFlushes):
        s.addTests(loader.loadTestsFromTestCase(case))
    return s


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite())
    sys.exit(0 if result.wasSuccessful() else 1)
