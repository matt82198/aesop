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


def _make_git_repo(root):
    """Local temp git repo with an origin remote (repo_slug reads it). Never the live repo."""
    repo = Path(root) / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["remote", "add", "origin", "https://github.com/owner/repo.git"]):
        res = subprocess.run(["git", "-C", str(repo)] + args, capture_output=True,
                             encoding="utf-8", errors="replace", timeout=60)
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


def suite():
    """Return test suite for this module."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestPostReceiptSpoolsOn422))
    suite.addTests(loader.loadTestsFromTestCase(TestReceiptFlushIntegration))
    return suite


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite())
    sys.exit(0 if result.wasSuccessful() else 1)
