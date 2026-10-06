#!/usr/bin/env python3
"""
Red-first behavioral tests for tools/verify_receipt.py (receipt gate, increment 2).

The verifier NEVER trusts a number inside the receipt: it recomputes the tree hash from
its own checkout, re-derives freshness from git ancestry, and re-checks the signature.
Each test drives the real verifier over a real temp git repo and asserts the exit code.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test_v", TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = _load("receipt_common")
emit = _load("emit_receipt")
vr = _load("verify_receipt")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(cwd, *args):
    env = dict(os.environ)
    env.update(_GIT_ENV)
    env["HOME"] = str(cwd)
    return subprocess.run(["git", "-C", str(cwd)] + list(args), cwd=str(cwd), env=env,
                          capture_output=True, encoding="utf-8", errors="replace", check=True).stdout.strip()


def _commit(repo, name):
    (repo / name).write_text(name + "\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _secret():
    return "verify" + "-" + "unit" + "-" + "dummy" + "-" + "1" * 20


REQUIRED = ["py-shard-0", "py-shard-1"]


def _good_parts():
    return [{"name": n, "exit_code": 0, "test_count": 5, "duration_s": 1.0} for n in REQUIRED]


class VerifyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _commit(self.repo, "m1")
        self.base = _commit(self.repo, "m2")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "checkout", "-q", "-b", "feat")
        self.head = _commit(self.repo, "f1")

    def tearDown(self):
        self.tmp.cleanup()

    def envelope(self, parts=None, **overrides):
        receipt = emit.build_receipt(self.repo, parts if parts is not None else _good_parts(), skipped=[], slug="o/r")
        receipt.update(overrides)
        sig = rc.sign(rc.canonical_json(receipt), scheme="hmac-sha256", hmac_secret=_secret())
        return {"receipt": receipt, "sig": sig}

    def run_verify(self, env, head=None, max_behind=50, required=None, secret=None):
        code, reasons = vr.verify(
            env, repo=self.repo, head=head or self.head, main_ref="origin/main", max_behind=max_behind,
            required=required if required is not None else REQUIRED, pubkey_path=None,
            hmac_secret=_secret() if secret is None else secret,
        )
        return code, reasons


class TestVerify(VerifyBase):
    def test_valid_receipt_passes(self):
        code, reasons = self.run_verify(self.envelope())
        self.assertEqual((code, reasons), (0, []))

    def test_wrong_tree_fails(self):
        env = self.envelope(tree_hash="0" * 40)
        code, reasons = self.run_verify(env)
        self.assertEqual(code, 1)
        self.assertTrue(any("tree" in r for r in reasons), reasons)

    def test_head_sha_mismatch_fails(self):
        env = self.envelope()
        code, reasons = self.run_verify(env, head=self.base)
        self.assertEqual(code, 1)
        self.assertTrue(any("head_sha" in r for r in reasons), reasons)

    def test_expired_base_fails(self):
        env = self.envelope()
        _git(self.repo, "checkout", "-q", "main")
        for i in range(3):
            _commit(self.repo, "later%d" % i)
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "checkout", "-q", "feat")
        self.assertEqual(self.run_verify(env, max_behind=2)[0], 1)
        self.assertEqual(self.run_verify(env, max_behind=3)[0], 0)

    def test_base_not_ancestor_of_main_fails(self):
        env = self.envelope(base_sha=self.head)  # the feature head is not on main
        code, reasons = self.run_verify(env)
        self.assertEqual(code, 1)
        self.assertTrue(any("ancestor" in r for r in reasons), reasons)

    def test_missing_required_part_fails(self):
        env = self.envelope(parts=_good_parts()[:1])
        code, reasons = self.run_verify(env)
        self.assertEqual(code, 1)
        self.assertTrue(any("py-shard-1" in r for r in reasons), reasons)

    def test_required_part_with_nonzero_exit_fails(self):
        parts = _good_parts()
        parts[1]["exit_code"] = 1
        code, reasons = self.run_verify(self.envelope(parts=parts))
        self.assertEqual(code, 1)

    def test_tampered_part_exit_code_fails_signature(self):
        parts = _good_parts()
        parts[0]["exit_code"] = 1
        env = self.envelope(parts=parts)
        env["receipt"]["parts"][0]["exit_code"] = 0  # forge green after signing
        code, reasons = self.run_verify(env)
        self.assertEqual(code, 1)
        self.assertTrue(any("signature" in r for r in reasons), reasons)

    def test_wrong_secret_fails(self):
        self.assertEqual(self.run_verify(self.envelope(), secret=_secret() + "x")[0], 1)

    def test_no_key_material_cannot_evaluate(self):
        code, reasons = vr.verify(self.envelope(), repo=self.repo, head=self.head, main_ref="origin/main",
                                  max_behind=50, required=REQUIRED, pubkey_path=None, hmac_secret=None)
        self.assertEqual(code, 2)

    def test_unknown_scheme_is_rejected(self):
        env = self.envelope()
        env["sig"]["scheme"] = "md5"
        self.assertEqual(self.run_verify(env)[0], 1)

    def test_malformed_envelope_cannot_evaluate(self):
        self.assertEqual(self.run_verify({"nope": 1})[0], 2)
        self.assertEqual(self.run_verify({"receipt": {"schema": 2}, "sig": {}})[0], 2)


class TestCli(VerifyBase):
    def test_cli_exit_codes_and_file_input(self):
        path = Path(self.tmp.name) / "r.json"
        path.write_text(json.dumps(self.envelope()), encoding="utf-8")
        environ = {"AESOP_RECEIPT_HMAC_SECRET": _secret()}
        args = ["--receipt", str(path), "--repo", str(self.repo), "--head", self.head,
                "--required", ",".join(REQUIRED), "--pubkey", str(Path(self.tmp.name) / "absent.pub")]
        self.assertEqual(vr.main(args, environ=environ), 0)
        self.assertEqual(vr.main(args + ["--head", self.base], environ=environ), 1)
        self.assertEqual(vr.main(args, environ={}), 2)
        self.assertEqual(vr.main(["--receipt", str(Path(self.tmp.name) / "missing.json"),
                                  "--repo", str(self.repo)], environ=environ), 2)


class TestFetchReceiptForHead(VerifyBase):
    """Red-first coverage for the Action's fetch step (tools/verify_receipt.py
    --fetch-for-head), which must land on exactly one of three outcomes:
    absent (no receipt) -> neutral, found-and-valid -> pass, found-and-invalid -> fail.
    The fetch lookup itself must NEVER raise or report "found" on a lookup failure --
    only an envelope it actually extracted counts as found.
    """

    def test_absent_when_api_returns_nothing(self):
        # Simulates: no check-run, no commit comment carries a receipt for this sha.
        env, note = vr.fetch_receipt_for_head(self.head, "o/r", api=lambda path: {"check_runs": []} if "check-runs" in path else [])
        self.assertIsNone(env)
        self.assertIn("no", note)
        self.assertIn(rc.CHECK_NAME, note)

    def test_absent_when_api_call_itself_fails(self):
        # gh api returning None (nonzero exit / bad JSON) must still resolve to absent,
        # not blow up the lookup.
        env, note = vr.fetch_receipt_for_head(self.head, "o/r", api=lambda path: None)
        self.assertIsNone(env)

    def test_absent_never_raises_on_unexpected_lookup_exception(self):
        # Red-first: this is the exact production failure (ModuleNotFoundError on a
        # stale PR tree, surfaced as an uncaught exception during the lookup) -- any
        # exception during the lookup phase must degrade to absent, never propagate.
        def boom(path):
            raise RuntimeError("network blip")
        env, note = vr.fetch_receipt_for_head(self.head, "o/r", api=boom)
        self.assertIsNone(env)
        self.assertIn("unexpectedly", note)

    def test_found_valid_receipt_via_check_run(self):
        good = self.envelope()

        def api(path):
            if "check-runs" in path:
                return {"check_runs": [{"name": rc.CHECK_NAME, "completed_at": "2026-10-06T10:00:00Z",
                                        "output": {"text": emit.wrap_receipt_text(good)}}]}
            return []

        env, note = vr.fetch_receipt_for_head(self.head, "o/r", api=api)
        self.assertIsNotNone(env)
        self.assertIn("found", note)
        code, reasons = self.run_verify(env)
        self.assertEqual((code, reasons), (0, []), "a receipt the fetch step finds must still verify VALID")

    def test_found_invalid_receipt_via_commit_comment(self):
        bad = self.envelope(tree_hash="0" * 40)  # forged/stale tree -> INVALID, not absent

        def api(path):
            if "check-runs" in path:
                return {"check_runs": []}
            return [{"body": emit.wrap_receipt_text(bad), "created_at": "2026-10-06T10:00:00Z"}]

        env, note = vr.fetch_receipt_for_head(self.head, "o/r", api=api)
        self.assertIsNotNone(env, "a present-but-invalid receipt must be FOUND, not absent")
        code, reasons = self.run_verify(env)
        self.assertEqual(code, 1)
        self.assertTrue(any("tree" in r for r in reasons), reasons)


class TestFetchForHeadCli(VerifyBase):
    """The --fetch-for-head CLI mode the workflow actually invokes: it must always
    exit 0 and communicate the outcome purely through --out-found / --out-envelope,
    regardless of which of the three outcomes occurred."""

    def _run(self, api_patch):
        found_path = Path(self.tmp.name) / "receipt_found"
        env_path = Path(self.tmp.name) / "receipt.json"
        args = ["--fetch-for-head", self.head, "--repo-slug", "o/r",
                "--out-found", str(found_path), "--out-envelope", str(env_path)]
        orig = vr._gh_api
        vr._gh_api = api_patch
        try:
            code = vr.main(args, environ={})
        finally:
            vr._gh_api = orig
        return code, found_path, env_path

    def test_cli_absent_exits_0_and_writes_false(self):
        code, found_path, env_path = self._run(lambda path: None)
        self.assertEqual(code, 0)
        self.assertEqual(found_path.read_text(encoding="utf-8"), "false")
        self.assertFalse(env_path.exists())

    def test_cli_found_exits_0_and_writes_envelope(self):
        good = self.envelope()

        def api(path):
            if "check-runs" in path:
                return {"check_runs": [{"name": rc.CHECK_NAME, "completed_at": "2026-10-06T10:00:00Z",
                                        "output": {"text": emit.wrap_receipt_text(good)}}]}
            return []

        code, found_path, env_path = self._run(api)
        self.assertEqual(code, 0)
        self.assertEqual(found_path.read_text(encoding="utf-8"), "true")
        self.assertEqual(json.loads(env_path.read_text(encoding="utf-8"))["receipt"]["head_sha"], good["receipt"]["head_sha"])

    def test_cli_missing_repo_slug_is_absent_not_a_crash(self):
        code, found_path, _ = self._run(lambda path: None)  # api unused; repo-slug omission short-circuits
        found_path2 = Path(self.tmp.name) / "receipt_found2"
        args = ["--fetch-for-head", self.head, "--out-found", str(found_path2),
                "--out-envelope", str(Path(self.tmp.name) / "r2.json")]
        self.assertEqual(vr.main(args, environ={}), 0)
        self.assertEqual(found_path2.read_text(encoding="utf-8"), "false")


class TestExtractFromCheckRuns(unittest.TestCase):
    """The Action extracts the newest receipt from check-runs or commit comments."""

    def test_picks_latest_matching_check_run_text(self):
        env_a = {"receipt": {"schema": 1, "n": "a"}, "sig": {}}
        env_b = {"receipt": {"schema": 1, "n": "b"}, "sig": {}}
        payload = {"check_runs": [
            {"name": "other", "completed_at": "2026-10-06T12:00:00Z", "output": {"text": json.dumps(env_a)}},
            {"name": rc.CHECK_NAME, "completed_at": "2026-10-06T10:00:00Z", "output": {"text": emit.wrap_receipt_text(env_a)}},
            {"name": rc.CHECK_NAME, "completed_at": "2026-10-06T11:00:00Z", "output": {"text": emit.wrap_receipt_text(env_b)}},
        ]}
        found = vr.extract_receipt_from_check_runs(payload)
        self.assertEqual(found["receipt"]["n"], "b")

    def test_commit_comment_fallback_and_none(self):
        env_a = {"receipt": {"schema": 1, "n": "a"}, "sig": {}}
        comments = [{"body": "unrelated", "created_at": "2026-10-06T10:00:00Z"},
                    {"body": emit.wrap_receipt_text(env_a), "created_at": "2026-10-06T11:00:00Z"}]
        self.assertEqual(vr.extract_receipt_from_comments(comments)["receipt"]["n"], "a")
        self.assertIsNone(vr.extract_receipt_from_comments([{"body": "x"}]))
        self.assertIsNone(vr.extract_receipt_from_check_runs({"check_runs": []}))


if __name__ == "__main__":
    unittest.main()
