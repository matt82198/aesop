#!/usr/bin/env python3
"""
Behavioral tests for tools/emit_receipt.py post_receipt and tools/receipt_flush.py
(receipt gate, spool-and-flush design).

Red-first: post_receipt spools on 422 (commit not found); flush posts spooled
receipts whose sha exists and deletes their spool file; sha-missing receipts are
kept (never deleted); stale receipts are dropped on --max-age-days.
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
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
emit = _load("emit_receipt")
flush = _load("receipt_flush")


def _hmac_secret():
    return "unit" + "-" + "test" + "-" + "receipt" + "-" + "value" + "-" + "0" * 16


def _dummy_envelope(head_sha="a" * 40):
    """Build a minimal valid envelope for testing."""
    receipt = {
        "schema": 1,
        "schema_version": 1,
        "repo": "owner/repo",
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


class TestPostReceiptSpoolsOn422(unittest.TestCase):
    """post_receipt should spool when the commit doesn't exist (422 on comment fallback)."""

    def test_post_receipt_spools_on_check_run_403_and_comment_422(self):
        """When check-run gets 403 and comment gets 422, should write spool file."""
        envelope = _dummy_envelope()
        slug = "owner/repo"

        # Mock gh_runner that returns 403 on check-run, 422 on comment
        def mock_gh_runner(args):
            args_str = " ".join(args) if args else ""
            if "check-runs" in args_str:
                # 403 Forbidden
                return 403, "", "403 Forbidden"
            elif "comments" in args_str and "commits" in args_str:
                # 422 Unprocessable Entity (commit not found)
                return 422, "", "422 No commit found for SHA"
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp_root:
            spool_dir = Path(tmp_root) / "receipts" / "spool"

            # post_receipt should spool the receipt when 422 occurs
            channel = emit.post_receipt(envelope, slug, mock_gh_runner, spool_dir=spool_dir)

            # Verify spooling occurred
            self.assertEqual(channel, "spooled")

            # Verify spool file was created
            head_sha = envelope["receipt"]["head_sha"]
            spool_file = Path(spool_dir) / (head_sha + ".json")
            self.assertTrue(spool_file.exists(), "spool file should exist at %s" % spool_file)

            # Verify spool file contains the envelope
            spooled_envelope = json.loads(spool_file.read_text(encoding="utf-8"))
            self.assertEqual(spooled_envelope["receipt"]["head_sha"], head_sha)
            self.assertEqual(spooled_envelope["sig"]["scheme"], envelope["sig"]["scheme"])


# Every subprocess a test starts on Windows must not open a console window.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _make_git_repo(root):
    """Local temp git repo with an origin remote (repo_slug reads it). Never the live repo."""
    repo = Path(root) / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["remote", "add", "origin", "https://github.com/owner/repo.git"]):
        res = subprocess.run(["git", "-C", str(repo)] + args, capture_output=True,
                             encoding="utf-8", errors="replace", timeout=60, creationflags=_NO_WINDOW)
        if res.returncode != 0:
            raise RuntimeError("git %s failed: %s" % (args[0], res.stderr))
    return repo


class _FakeGh(object):
    """Stands in for `gh`. Answers the commit-exists lookup, records every call,
    and captures the JSON payload of each check-run POST (the file is deleted
    by post_receipt right after the call, so it is read here, during the call)."""

    def __init__(self, commit_code, check_run_code=0):
        self.commit_code = commit_code
        self.check_run_code = check_run_code
        self.calls = []
        self.posted_payloads = []

    def __call__(self, args):
        self.calls.append(list(args))
        if any(a.endswith("/check-runs") for a in args):
            payload_path = args[args.index("--input") + 1]
            with open(payload_path, encoding="utf-8") as fh:
                self.posted_payloads.append(json.load(fh))
            if self.check_run_code == 0:
                return 0, json.dumps({"id": 123}), ""
            return self.check_run_code, "", "403 Forbidden"
        if len(args) == 2 and args[0] == "api" and "/commits/" in args[1]:
            if self.commit_code == 0:
                return 0, json.dumps({"sha": args[1].rsplit("/", 1)[1]}), ""
            return self.commit_code, "", "404 Not Found"
        return 0, "", ""


def _write_spool(spool_dir, envelope, age_days=0):
    spool_dir.mkdir(parents=True, exist_ok=True)
    path = spool_dir / (envelope["receipt"]["head_sha"] + ".json")
    path.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
    if age_days:
        old = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
        os.utime(path, (old, old))
    return path


def _run_flush(repo, spool_dir, gh, extra_args=()):
    """Run receipt_flush.main() in-process against the fake gh. Returns (exit code, stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = flush.main(["--repo", str(repo)] + list(extra_args),
                          gh_runner=gh, environ={rc.SPOOL_ENV: str(spool_dir)})
    return code, out.getvalue()


class TestReceiptFlushIntegration(unittest.TestCase):
    """receipt_flush should post spooled receipts and manage spool files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = _make_git_repo(self.root)
        self.spool_dir = self.root / "receipts" / "spool"

    def test_flush_posted_receipt_removes_spool_file(self):
        """When sha exists on GitHub, flush should post and remove spool file."""
        envelope = _dummy_envelope("def456" + "0" * 34)
        head_sha = envelope["receipt"]["head_sha"]
        spool_file = _write_spool(self.spool_dir, envelope)
        gh = _FakeGh(commit_code=0, check_run_code=0)

        code, out = _run_flush(self.repo, self.spool_dir, gh)

        self.assertEqual(code, 0)
        self.assertFalse(spool_file.exists(), "a posted spool file must be deleted")
        self.assertEqual(len(gh.posted_payloads), 1, "exactly one check-run POST expected")
        self.assertEqual(gh.posted_payloads[0]["head_sha"], head_sha)
        self.assertIn("posted=1", out)

    def test_flush_missing_sha_keeps_spool_file(self):
        """When sha does not exist, flush should keep the spool file."""
        envelope = _dummy_envelope("fedcba" + "0" * 34)
        spool_file = _write_spool(self.spool_dir, envelope)
        before = spool_file.read_bytes()
        gh = _FakeGh(commit_code=404)

        code, out = _run_flush(self.repo, self.spool_dir, gh)

        self.assertEqual(code, 0)
        self.assertTrue(spool_file.exists(), "a spool whose sha is absent must survive the flush")
        self.assertEqual(spool_file.read_bytes(), before, "kept spool must be byte-identical")
        self.assertEqual(gh.posted_payloads, [], "no receipt may be posted for an absent sha")
        self.assertEqual(len(gh.calls), 1, "only the commit-exists lookup should reach gh")
        self.assertIn("posted=0", out)
        self.assertIn("kept=1", out)

    def test_flush_stale_spool_file_dropped(self):
        """When spool file exceeds --max-age-days, flush should delete it."""
        stale = _dummy_envelope("111111" + "0" * 34)
        fresh = _dummy_envelope("222222" + "0" * 34)
        stale_file = _write_spool(self.spool_dir, stale, age_days=10)
        fresh_file = _write_spool(self.spool_dir, fresh)
        gh = _FakeGh(commit_code=404)

        code, out = _run_flush(self.repo, self.spool_dir, gh, ["--max-age-days", "7"])

        self.assertEqual(code, 0)
        self.assertFalse(stale_file.exists(), "a spool older than --max-age-days must be dropped")
        self.assertTrue(fresh_file.exists(), "a fresh spool must not be dropped by the age sweep")
        stale_sha = stale["receipt"]["head_sha"]
        self.assertFalse(any(stale_sha in a for call in gh.calls for a in call),
                         "a dropped stale spool must never reach gh")
        self.assertIn("dropped=1", out)


def _can_symlink():
    """True when this host lets the current user create symlinks (Windows needs developer mode or privilege)."""
    probe = Path(tempfile.gettempdir()) / ("aesop-symlink-probe-%d" % os.getpid())
    try:
        os.symlink(os.devnull, str(probe))
    except OSError:
        return False
    finally:
        if os.path.islink(str(probe)):
            os.unlink(str(probe))
    return True


def _write_text(path, text, age_days=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_days:
        old = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
        os.utime(path, (old, old))
    return path


class TestFlushLogPrune(unittest.TestCase):
    """receipt_flush prunes *.flush.log files by the same --max-age-days rule as spooled receipts.

    Candidates are top-level *.flush.log files in the spool directory. Symlinks are never followed
    or removed, and a file that cannot be stat'd or removed is reported and skipped, not fatal.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = _make_git_repo(self.root)
        self.spool_dir = self.root / "receipts" / "spool"
        self.spool_dir.mkdir(parents=True)

    def test_stale_flush_log_is_pruned_and_fresh_one_kept(self):
        stale = _write_text(self.spool_dir / ("a" * 40 + ".flush.log"), "old log\n", age_days=10)
        fresh = _write_text(self.spool_dir / ("b" * 40 + ".flush.log"), "new log\n", age_days=1)

        code, out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404))

        self.assertEqual(code, 0)
        self.assertFalse(stale.exists(), "a flush log older than --max-age-days must be pruned")
        self.assertTrue(fresh.exists(), "a flush log inside --max-age-days must be kept")

    def test_json_expiry_unchanged_alongside_flush_logs(self):
        stale = _write_spool(self.spool_dir, _dummy_envelope("1" * 40), age_days=10)
        fresh = _write_spool(self.spool_dir, _dummy_envelope("2" * 40))
        _write_text(self.spool_dir / ("c" * 40 + ".flush.log"), "log\n", age_days=10)

        code, out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404))

        self.assertEqual(code, 0)
        self.assertFalse(stale.exists(), "a json spool older than --max-age-days must still be dropped")
        self.assertTrue(fresh.exists(), "a fresh json spool must still be kept")
        self.assertIn("dropped=1", out)

    def test_unstatable_flush_log_is_reported_and_skipped_not_fatal(self):
        bad = _write_text(self.spool_dir / ("d" * 40 + ".flush.log"), "x\n", age_days=10)
        good = _write_text(self.spool_dir / ("e" * 40 + ".flush.log"), "y\n", age_days=10)
        real_stat = Path.stat

        def flaky_stat(path, *args, **kwargs):
            if path.name == bad.name:
                raise PermissionError(13, "Permission denied")
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(Path, "stat", flaky_stat):
            code, out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404))

        self.assertEqual(code, 0, "an unstatable flush log must not be fatal without --strict")
        self.assertTrue(bad.exists(), "a flush log that could not be stat'd must be skipped, not removed")
        self.assertFalse(good.exists(), "the other stale flush log must still be pruned")
        self.assertIn("could not stat flush log", out)
        self.assertIn(bad.name, out)

    def test_unremovable_flush_log_is_reported_and_skipped_not_fatal(self):
        locked = _write_text(self.spool_dir / ("f" * 40 + ".flush.log"), "z\n", age_days=10)
        good = _write_text(self.spool_dir / ("g" * 40 + ".flush.log"), "w\n", age_days=10)
        real_unlink = Path.unlink

        def flaky_unlink(path, *args, **kwargs):
            if path.name == locked.name:
                raise PermissionError(13, "Permission denied")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", flaky_unlink):
            code, out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404))

        self.assertEqual(code, 0, "an unremovable flush log must not be fatal without --strict")
        self.assertTrue(locked.exists())
        self.assertFalse(good.exists(), "the other stale flush log must still be pruned")
        self.assertIn("could not delete flush log", out)
        self.assertIn(locked.name, out)

    def test_pruning_is_top_level_and_exact_pattern_only(self):
        nested = _write_text(self.spool_dir / "nested" / ("i" * 40 + ".flush.log"), "n\n", age_days=10)
        other = _write_text(self.spool_dir / "notes.txt", "n\n", age_days=10)
        near_miss = _write_text(self.spool_dir / ("j" * 40 + ".flush.log.bak"), "b\n", age_days=10)

        code, _out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404))

        self.assertEqual(code, 0)
        self.assertTrue(nested.exists(), "pruning is top-level only, never recursive")
        self.assertTrue(other.exists(), "non-matching files must never be pruned")
        self.assertTrue(near_miss.exists(), "only exact *.flush.log names are candidates")

    @unittest.skipUnless(_can_symlink(), "this host cannot create symlinks (needs privilege); runs on CI Linux")
    def test_symlink_named_like_flush_log_is_never_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        precious = _write_text(outside / "precious.txt", "keep me\n", age_days=10)
        link = self.spool_dir / ("h" * 40 + ".flush.log")
        os.symlink(str(precious), str(link))

        code, _out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404))

        self.assertEqual(code, 0)
        self.assertTrue(precious.exists(), "a flush-log symlink must never cause its target outside the spool dir to be removed")
        self.assertTrue(os.path.islink(str(link)), "a symlink named like a flush log must be left alone")


class TestMaxAgeDaysArgument(unittest.TestCase):
    """--max-age-days is a whole number of days, 0 or more. Negative or non-finite values are refused at
    argument parsing (exit 2) before anything is pruned. 0 expires everything older than now, for json
    spools and flush logs alike."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = _make_git_repo(self.root)
        self.spool_dir = self.root / "receipts" / "spool"
        self.spool_dir.mkdir(parents=True)

    def test_negative_max_age_is_rejected_at_parse_time_and_prunes_nothing(self):
        fresh = _write_text(self.spool_dir / ("a" * 40 + ".flush.log"), "fresh\n", age_days=0)
        stale = _write_text(self.spool_dir / ("b" * 40 + ".flush.log"), "stale\n", age_days=10)
        err = io.StringIO()

        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404), ["--max-age-days=-1"])

        self.assertEqual(cm.exception.code, 2, "a negative --max-age-days must exit 2")
        self.assertIn("--max-age-days", err.getvalue(), "the refusal must name the offending option")
        self.assertTrue(fresh.exists(), "a negative window must never prune a fresh flush log")
        self.assertTrue(stale.exists(), "a refused run must prune nothing at all")

    def test_non_finite_or_fractional_max_age_is_rejected(self):
        for bad in ("nan", "inf", "-inf", "1.5"):
            with self.subTest(value=bad):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as cm:
                        _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404),
                                   ["--max-age-days=%s" % bad])
                self.assertEqual(cm.exception.code, 2)
                self.assertIn("--max-age-days", err.getvalue())

    def test_zero_max_age_is_accepted_and_expires_json_and_flush_logs_alike(self):
        stale_json = _write_spool(self.spool_dir, _dummy_envelope("4" * 40), age_days=1)
        stale_log = _write_text(self.spool_dir / ("c" * 40 + ".flush.log"), "x\n", age_days=1)

        code, out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404), ["--max-age-days", "0"])

        self.assertEqual(code, 0, "0 is a valid window")
        self.assertFalse(stale_json.exists(), "with 0, a json spool older than now is dropped")
        self.assertFalse(stale_log.exists(), "with 0, a flush log older than now is pruned by the same rule")

    def test_huge_max_age_is_refused_at_parse_time_not_an_overflow_traceback(self):
        fresh = _write_text(self.spool_dir / ("a" * 40 + ".flush.log"), "fresh\n", age_days=0)

        for bad in ("1000000000", "99999999999999999999", "3651"):
            with self.subTest(value=bad):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as cm:
                        _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404), ["--max-age-days", bad])
                self.assertEqual(cm.exception.code, 2, "a window above 3650 days must exit 2, not overflow")
                self.assertIn("--max-age-days", err.getvalue())
        self.assertTrue(fresh.exists(), "a refused window must prune nothing")

    def test_ceiling_of_3650_days_is_accepted(self):
        code, _out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404), ["--max-age-days", "3650"])

        self.assertEqual(code, 0, "3650 days is the inclusive upper bound")


class TestRefusedLogPrune(unittest.TestCase):
    """The refused-receipts log beside the spool directory is pruned by the same --max-age-days rule
    as the flush logs (file mtime, which for an append-only log is the newest record's time). Never followed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = _make_git_repo(self.root)
        self.spool_dir = self.root / "receipts" / "spool"
        self.spool_dir.mkdir(parents=True)
        self.refused = self.root / "receipts" / "refused-receipts.jsonl"

    def test_stale_refused_log_is_pruned(self):
        _write_text(self.refused, '{"head_sha": "%s"}\n' % ("5" * 40), age_days=10)

        code, out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404))

        self.assertEqual(code, 0)
        self.assertFalse(self.refused.exists(), "a refused-receipts log older than --max-age-days must be pruned")

    def test_fresh_refused_log_is_kept(self):
        _write_text(self.refused, '{"head_sha": "%s"}\n' % ("6" * 40), age_days=1)

        code, out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404), ["--max-age-days", "7"])

        self.assertEqual(code, 0)
        self.assertTrue(self.refused.exists(), "a refused-receipts log inside --max-age-days must be kept")

    def test_zero_window_prunes_refused_log_like_flush_logs(self):
        _write_text(self.refused, '{"head_sha": "%s"}\n' % ("7" * 40), age_days=10)

        code, out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404), ["--max-age-days", "0"])

        self.assertEqual(code, 0)
        self.assertFalse(self.refused.exists(), "with 0, the refused log is expired exactly as a flush log is")

    def test_refused_log_under_seven_days_survives_a_zero_window(self):
        _write_text(self.refused, '{"head_sha": "%s"}\n' % ("8" * 40), age_days=3)

        code, out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404), ["--max-age-days", "0"])

        self.assertEqual(code, 0)
        self.assertTrue(self.refused.exists(),
                        "the refused log is the only durable trace of a refused push: never pruned younger than 7 days")
        self.assertNotIn("pruned refused-receipts log", out)

    def test_refused_log_under_seven_days_survives_a_thirty_day_window(self):
        _write_text(self.refused, '{"head_sha": "%s"}\n' % ("9" * 40), age_days=3)

        code, out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404), ["--max-age-days", "30"])

        self.assertEqual(code, 0)
        self.assertTrue(self.refused.exists(), "a 30-day window keeps a 3-day-old refused log, as before")

    def test_refused_log_past_seven_days_is_pruned_at_zero_window_floor_only(self):
        _write_text(self.refused, '{"head_sha": "%s"}\n' % ("e" * 40), age_days=10)

        code, _out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404), ["--max-age-days", "0"])

        self.assertEqual(code, 0)
        self.assertFalse(self.refused.exists(), "the floor is 7 days, not infinity: 10 days old is pruned at 0")

    def test_refused_log_older_than_thirty_days_is_pruned_at_thirty_window(self):
        _write_text(self.refused, '{"head_sha": "%s"}\n' % ("f" * 40), age_days=40)

        code, _out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404), ["--max-age-days", "30"])

        self.assertEqual(code, 0)
        self.assertFalse(self.refused.exists(), "a 40-day-old refused log is pruned once the 30-day window is larger than 7")

    @unittest.skipUnless(_can_symlink(), "this host cannot create symlinks (needs privilege); runs on CI Linux")
    def test_symlinked_refused_log_is_never_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        precious = _write_text(outside / "precious.txt", "keep me\n", age_days=10)
        self.refused.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(str(precious), str(self.refused))

        code, _out = _run_flush(self.repo, self.spool_dir, _FakeGh(commit_code=404))

        self.assertEqual(code, 0)
        self.assertTrue(precious.exists(), "a symlinked refused log must never cause its target to be removed")
        self.assertTrue(os.path.islink(str(self.refused)), "a symlinked refused log must be left alone")


def _try_junction(link, target):
    """Create a Windows directory junction with mklink /J (no privilege needed). True when it exists afterwards."""
    if os.name != "nt":
        return False
    res = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                         capture_output=True, encoding="utf-8", errors="replace", timeout=60,
                         creationflags=_NO_WINDOW)
    return res.returncode == 0 and os.path.isdir(str(link))


def _make_dir_link(link, target):
    """A directory link at link pointing at target: a junction on Windows, a symlink elsewhere. True when made."""
    if os.name == "nt":
        return _try_junction(link, target)
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
    except OSError:
        return False
    return True


class TestSpoolDirLinkRefusal(unittest.TestCase):
    """A spool directory that is itself a symlink or a junction is never pruned or flushed through.
    The run reports it, touches nothing reached through it, and exits 2 (non-zero, with or without --strict)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = _make_git_repo(self.root)
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.stale_log = _write_text(self.outside / ("k" * 40 + ".flush.log"), "old\n", age_days=10)
        self.stale_json = _write_spool(self.outside, _dummy_envelope("3" * 40), age_days=10)
        self.link = self.root / "receipts" / "spool"
        self.link.parent.mkdir(parents=True)

    def _assert_refused(self, code, out, gh):
        self.assertEqual(code, 2, "a linked spool directory must make the run exit non-zero")
        self.assertTrue(self.stale_log.exists(), "a flush log reached through a linked spool dir must not be pruned")
        self.assertTrue(self.stale_json.exists(), "a spool reached through a linked spool dir must not be dropped")
        self.assertEqual(gh.posted_payloads, [], "nothing may be posted from a linked spool dir")
        self.assertIn("symlink or junction", out)

    @unittest.skipUnless(_can_symlink(), "this host cannot create symlinks (needs privilege); runs on CI Linux")
    def test_symlinked_spool_dir_is_refused_not_followed(self):
        os.symlink(str(self.outside), str(self.link), target_is_directory=True)
        gh = _FakeGh(commit_code=0)

        code, out = _run_flush(self.repo, self.link, gh)

        self._assert_refused(code, out, gh)

    def test_junctioned_spool_dir_is_refused_not_followed(self):
        if os.name != "nt":
            self.skipTest("directory junctions exist only on Windows; the symlink case covers POSIX hosts")
        if not _try_junction(self.link, self.outside):
            self.skipTest("this host could not create a directory junction (mklink /J refused)")
        gh = _FakeGh(commit_code=0)

        code, out = _run_flush(self.repo, self.link, gh)

        self._assert_refused(code, out, gh)

    def test_linked_spool_dir_beside_a_posted_spool_exits_nonzero(self):
        """The final return (not the early returns) must carry the refusal: the linked primary dir is
        refused, the legacy repo/state/receipts/spool still posts one receipt, and the run must exit 2."""
        if not _make_dir_link(self.link, self.outside):
            self.skipTest("this host cannot create a directory link (symlink needs privilege; junction refused)")
        legacy = self.repo / "state" / "receipts" / "spool"
        posted_file = _write_spool(legacy, _dummy_envelope("5" * 40))
        gh = _FakeGh(commit_code=0, check_run_code=0)

        code, out = _run_flush(self.repo, self.link, gh)

        self.assertEqual(code, 2, "a refused linked spool dir must make the run exit non-zero even when a spool posts")
        self.assertIn("posted=1", out, "the legacy spool must still have been posted")
        self.assertFalse(posted_file.exists(), "the posted legacy spool is deleted, as on any successful post")
        self.assertTrue(self.stale_log.exists(), "the linked dir's flush log must not be pruned")
        self.assertTrue(self.stale_json.exists(), "the linked dir's spool must not be dropped")
        self.assertIn("symlink or junction", out)


def suite():
    """Return test suite for this module."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestPostReceiptSpoolsOn422))
    suite.addTests(loader.loadTestsFromTestCase(TestReceiptFlushIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestFlushLogPrune))
    suite.addTests(loader.loadTestsFromTestCase(TestMaxAgeDaysArgument))
    suite.addTests(loader.loadTestsFromTestCase(TestRefusedLogPrune))
    suite.addTests(loader.loadTestsFromTestCase(TestSpoolDirLinkRefusal))
    return suite


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite())
    sys.exit(0 if result.wasSuccessful() else 1)
