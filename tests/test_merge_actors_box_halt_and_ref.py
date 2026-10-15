#!/usr/bin/env python3
"""Regression tests for the two merge actors' box-wide halt and PR head-ref handling.

F1 (fail-open kill switch): merge_train and merge_queue consulted only the
checkout-scoped .HALT (halt.is_halted() with no args). The box-wide sentinel
written by `halt.py set` (<CONDUCTOR_ROOT>/state/.HALT) was honoured by no merge
path. Both actors must halt on either sentinel, and treat any error reading
either as HALTED (fail closed).

F2 (argument injection): a PR's headRefName is fork-controlled. Passing it to
`git fetch origin <name>` without `--` let a name such as `--upload-pack=...`
become an option. Option-shaped names must be refused before any git call, and
every valid name must be fetched after an explicit `--`.

All state lives in temp dirs. The live conductor3 root is never touched: every
test pins CONDUCTOR_ROOT and AESOP_ROOT at a temp tree.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import merge_queue  # noqa: E402
import merge_train  # noqa: E402


def _write_sentinel(path: Path, reason: str = "drill") -> None:
    """Write a halt sentinel in the same JSON shape halt.py produces."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"reason": reason, "timestamp": "2026-10-15T00:00:00Z"}
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


class _IsolatedHaltEnv(unittest.TestCase):
    """Pins every halt root at a temp tree so the live conductor3 is never read or written."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="aesop-halt-ref-")
        root = Path(self._tmp)
        self.conductor = root / "conductor3"
        self.checkout = root / "checkout"
        self.checkout.mkdir()
        self.local_sentinel = self.checkout / "state" / ".HALT"
        self.box_sentinel = self.conductor / "state" / ".HALT"
        self._env = patch.dict(os.environ, {
            "CONDUCTOR_ROOT": str(self.conductor),
            "AESOP_ROOT": str(self.checkout),
        })
        self._env.start()
        # An explicit AESOP_STATE_ROOT is an isolated override that disables the
        # box-wide reader by design, so it must be absent for these tests.
        os.environ.pop("AESOP_STATE_ROOT", None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)


class BoxWideHaltTests(_IsolatedHaltEnv):
    """A box-wide halt must stop both merge actors even with no checkout-local sentinel."""

    def test_box_wide_sentinel_halts_merge_train(self):
        _write_sentinel(self.box_sentinel, "operator halt")
        self.assertFalse(self.local_sentinel.exists())
        with self.assertRaises(SystemExit) as cm:
            merge_train._check_halt(" [test]")
        self.assertEqual(cm.exception.code, 1)

    def test_box_wide_sentinel_halts_merge_queue(self):
        _write_sentinel(self.box_sentinel, "operator halt")
        self.assertFalse(self.local_sentinel.exists())
        summary = {}
        with patch.object(merge_queue, "record_exception") as rec:
            halted = merge_queue._check_halt_and_record(summary)
        self.assertTrue(halted)
        self.assertEqual(summary.get("status"), "halted")
        self.assertEqual(rec.call_count, 1)

    def test_checkout_sentinel_still_halts_merge_train(self):
        _write_sentinel(self.local_sentinel, "checkout halt")
        with self.assertRaises(SystemExit) as cm:
            merge_train._check_halt(" [test]")
        self.assertEqual(cm.exception.code, 1)

    def test_no_sentinel_anywhere_does_not_halt(self):
        merge_train._check_halt(" [test]")  # must return, not raise SystemExit
        with patch.object(merge_queue, "record_exception") as rec:
            self.assertFalse(merge_queue._check_halt_and_record({}))
        self.assertEqual(rec.call_count, 0)


class HaltReaderErrorFailsClosedTests(_IsolatedHaltEnv):
    """Any error reading either halt reader must read as HALTED, never as clear."""

    def test_merge_train_local_reader_error_halts(self):
        with patch.object(merge_train, "is_halted", side_effect=PermissionError("denied")):
            with self.assertRaises(SystemExit) as cm:
                merge_train._check_halt(" [test]")
        self.assertEqual(cm.exception.code, 1)

    def test_merge_train_box_wide_reader_error_halts(self):
        with patch.object(merge_train, "active_halt", side_effect=OSError("io"), create=True):
            with self.assertRaises(SystemExit) as cm:
                merge_train._check_halt(" [test]")
        self.assertEqual(cm.exception.code, 1)

    def test_merge_queue_local_reader_error_halts(self):
        with patch.object(merge_queue, "is_halted", side_effect=PermissionError("denied")), \
                patch.object(merge_queue, "record_exception"):
            self.assertTrue(merge_queue._check_halt_and_record({}))

    def test_merge_queue_box_wide_reader_error_halts(self):
        with patch.object(merge_queue, "active_halt", side_effect=OSError("io"), create=True), \
                patch.object(merge_queue, "record_exception"):
            self.assertTrue(merge_queue._check_halt_and_record({}))


class _RecordingGit:
    """Fake git runner: records every argv and answers check-ref-format with the name."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, cwd=None):
        self.calls.append(tuple(args))
        if args and args[0] == "check-ref-format":
            return (True, args[-1])
        return (True, "")

    def fetch_calls(self):
        return [c for c in self.calls if c and c[0] == "fetch"]


class HeadRefFetchTests(unittest.TestCase):
    """headRefName must never reach git as an option, and valid names fetch after `--`."""

    SHA = "a" * 40

    def _gh_view(self, head_ref_name):
        return {"headRefOid": self.SHA, "headRefName": head_ref_name, "title": "t"}

    def test_option_shaped_head_ref_rejected_before_any_git_call(self):
        fake = _RecordingGit()
        with patch.object(merge_train, "gh", return_value=self._gh_view("--upload-pack=evil")), \
                patch.object(merge_train, "git", side_effect=fake):
            result = merge_train.merge_pr_into_integration(7)
        self.assertFalse(result)
        self.assertEqual(fake.calls, [], "no git call may run for an option-shaped headRefName")

    def test_valid_head_ref_fetched_after_end_of_options(self):
        fake = _RecordingGit()
        with patch.object(merge_train, "gh", return_value=self._gh_view("feature/x")), \
                patch.object(merge_train, "git", side_effect=fake):
            result = merge_train.merge_pr_into_integration(8)
        self.assertTrue(result)
        self.assertIn(("fetch", "origin", "--", "feature/x"), fake.calls)

    def test_head_ref_problem_rejects_option_empty_and_invalid_names(self):
        self.assertTrue(merge_train.head_ref_problem("--upload-pack=x"))
        self.assertTrue(merge_train.head_ref_problem("-b"))
        self.assertTrue(merge_train.head_ref_problem(""))
        self.assertTrue(merge_train.head_ref_problem("bad..name"))
        self.assertTrue(merge_train.head_ref_problem("feat@{-1}"))

    def test_head_ref_problem_accepts_plain_branch_name(self):
        self.assertEqual(merge_train.head_ref_problem("feature/fix-thing"), "")


class RealGitOptionInjectionTests(unittest.TestCase):
    """End to end on real git: an option-shaped headRefName must not execute anything."""

    def test_option_shaped_head_ref_does_not_execute_upload_pack(self):
        tmp = Path(tempfile.mkdtemp(prefix="aesop-upload-pack-"))
        try:
            src = tmp / "src"
            clone = tmp / "clone"
            marker = tmp / "pwned.txt"
            src.mkdir()
            env_git = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
            subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
            subprocess.run(["git", *env_git, "-C", str(src), "commit", "-q", "--allow-empty",
                            "-m", "seed"], check=True)
            subprocess.run(["git", "clone", "-q", str(src), str(clone)], check=True)
            sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
            payload = "%s -c \"open('%s','w').write('x')\"" % (
                sys.executable.replace("\\", "/"), marker.as_posix())
            evil_ref = "--upload-pack=" + payload
            with patch.object(merge_train, "gh", return_value={
                    "headRefOid": sha, "headRefName": evil_ref, "title": "t"}):
                result = merge_train.merge_pr_into_integration(9, cwd=str(clone))
            self.assertFalse(marker.exists(), "option-shaped headRefName executed a command")
            self.assertFalse(result)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
