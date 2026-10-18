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
        # profile="full": these fixtures test signature/tree/freshness/parts, not shard scoping
        # (the scoped recomputation has its own class, TestScopedRequiredShards).
        receipt = emit.build_receipt(self.repo, parts if parts is not None else _good_parts(), skipped=[],
                                     slug="o/r", profile="full")
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

    def test_receipt_with_schema_version_field(self):
        """A receipt emitted now carries schema_version=1 inside the signed canonical payload."""
        env = self.envelope()
        # After implementation, emit_receipt.build_receipt should include schema_version=1
        receipt = env["receipt"]
        self.assertIn("schema_version", receipt, "receipt must include schema_version field")
        self.assertEqual(receipt["schema_version"], 1, "schema_version must be 1")
        # Verify the signature is over the entire receipt including schema_version
        code, reasons = self.run_verify(env)
        self.assertEqual((code, reasons), (0, []), f"receipt with schema_version=1 must verify (reasons: {reasons})")

    def test_verify_rejects_invalid_schema_version_type(self):
        """A receipt with an unknown higher schema_version fails verification with a clear message."""
        env = self.envelope()
        # Manually set an unknown schema_version in the signed receipt
        receipt = env["receipt"]
        receipt["schema_version"] = 99
        sig = rc.sign(rc.canonical_json(receipt), scheme="hmac-sha256", hmac_secret=_secret())
        env["sig"] = sig
        code, reasons = self.run_verify(env)
        self.assertEqual(code, 1, "receipt with unknown schema_version must fail verification")
        self.assertTrue(any("schema_version" in r for r in reasons),
                       f"failure reason must mention schema_version, got: {reasons}")

    def test_verify_accepts_absent_schema_version(self):
        """A legacy receipt WITHOUT the schema_version field still verifies (backward compatible)."""
        env = self.envelope()
        receipt = env["receipt"]
        # Remove schema_version if present, to simulate legacy receipt
        receipt.pop("schema_version", None)
        sig = rc.sign(rc.canonical_json(receipt), scheme="hmac-sha256", hmac_secret=_secret())
        env["sig"] = sig
        code, reasons = self.run_verify(env)
        self.assertEqual((code, reasons), (0, []),
                        f"legacy receipt without schema_version must still verify (reasons: {reasons})")


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
        # A receipt FILE that does not exist is a MISSING receipt (fail-closed, exit 1).
        self.assertEqual(vr.main(["--receipt", str(Path(self.tmp.name) / "missing.json"),
                                  "--repo", str(self.repo)], environ=environ), 1)


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
        args = ["--fetch-for-head", self.head, "--repo-slug", "o/r", "--fetch-window-s", "0",
                "--out-found", str(found_path), "--out-envelope", str(env_path)]
        orig = vr._gh_api
        vr._gh_api = api_patch
        try:
            code = vr.main(args, environ={})
        finally:
            vr._gh_api = orig
        return code, found_path, env_path

    def test_cli_absent_exits_1_and_writes_false(self):
        # Missing receipt is fail-closed: the job must go RED, not neutral.
        code, found_path, env_path = self._run(lambda path: None)
        self.assertEqual(code, 1)
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
        self.assertEqual(vr.main(args, environ={}), 1)
        self.assertEqual(found_path2.read_text(encoding="utf-8"), "false")


class TestMissingReceiptFailsClosed(VerifyBase):
    """Missing receipt => exit 1 with a one-line reason naming the sha and where it was
    looked for. Present-but-invalid => 1. Present-and-valid => 0. (verify-receipt was
    neutral on a missing receipt, which hid the gap.)"""

    def _main(self, args, environ=None):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vr.main(args, environ=environ or {"AESOP_RECEIPT_HMAC_SECRET": _secret()})
        return code, buf.getvalue()

    def test_missing_receipt_file_exits_1_and_names_sha(self):
        code, out = self._main(["--receipt", str(Path(self.tmp.name) / "nope.json"),
                                "--repo", str(self.repo), "--head", self.head])
        self.assertEqual(code, 1)
        self.assertIn("MISSING", out)
        self.assertIn(self.head[:12], out)

    def test_extract_payload_without_receipt_exits_1(self):
        path = Path(self.tmp.name) / "payload.json"
        path.write_text(json.dumps({"check_runs": []}), encoding="utf-8")
        code, out = self._main(["--receipt", str(path), "--extract", "check-runs",
                                "--repo", str(self.repo), "--head", self.head])
        self.assertEqual(code, 1)
        self.assertIn("MISSING", out)

    def test_fetch_no_receipt_names_sha_and_lookup_location(self):
        args = ["--fetch-for-head", self.head, "--repo-slug", "o/r", "--fetch-window-s", "0",
                "--out-found", str(Path(self.tmp.name) / "found"),
                "--out-envelope", str(Path(self.tmp.name) / "e.json")]
        orig = vr._gh_api
        vr._gh_api = lambda path: None
        try:
            code, out = self._main(args)
        finally:
            vr._gh_api = orig
        self.assertEqual(code, 1)
        self.assertIn(self.head, out)
        self.assertIn(rc.CHECK_NAME, out)

    def test_valid_receipt_file_exits_0(self):
        path = Path(self.tmp.name) / "good.json"
        path.write_text(json.dumps(self.envelope()), encoding="utf-8")
        code, _ = self._main(["--receipt", str(path), "--repo", str(self.repo), "--head", self.head,
                              "--required", ",".join(REQUIRED), "--pubkey", str(Path(self.tmp.name) / "absent.pub")])
        self.assertEqual(code, 0)

    def test_invalid_receipt_file_exits_1(self):
        path = Path(self.tmp.name) / "bad.json"
        path.write_text(json.dumps(self.envelope()), encoding="utf-8")
        code, _ = self._main(["--receipt", str(path), "--repo", str(self.repo), "--head", self.base,
                              "--required", ",".join(REQUIRED), "--pubkey", str(Path(self.tmp.name) / "absent.pub")])
        self.assertEqual(code, 1)


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


class _FakeClock:
    """Deterministic time: sleep() advances the clock; nothing real waits."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class TestBoundedPollWindow(VerifyBase):
    """The Action's fetch step must keep polling for a receipt for a bounded window
    (default >= 5 min, ~20s interval) before reporting MISSING. The autoflush posts the
    receipt only after the push lands, so one immediate lookup plus one +60s lookup loses
    the race on fast CI starts (PR #1035, hosted run 38753687250)."""

    def _api_receipt_at(self, fake, appear_at, env):
        def api(path):
            if "check-runs" in path:
                if fake.t >= appear_at:
                    return {"check_runs": [{"name": rc.CHECK_NAME, "completed_at": "2026-10-06T10:00:00Z",
                                            "output": {"text": emit.wrap_receipt_text(env)}}]}
                return {"check_runs": []}
            return []
        return api

    def test_default_window_is_at_least_five_minutes_at_twenty_second_interval(self):
        self.assertGreaterEqual(vr.DEFAULT_FETCH_WINDOW_S, 300)
        self.assertEqual(vr.DEFAULT_FETCH_INTERVAL_S, 20)

    def test_absent_receipt_reports_missing_only_after_the_full_window(self):
        fake = _FakeClock()
        calls = []

        def api(path):
            calls.append(path)
            return {"check_runs": []} if "check-runs" in path else []

        env, note = vr.poll_receipt_for_head(self.head, "o/r", api=api, window_s=300, interval_s=20,
                                             sleep=fake.sleep, clock=fake.clock)
        self.assertIsNone(env)
        self.assertIn("MISSING receipt", note)
        self.assertEqual(fake.t, 300, "must wait the whole window before giving up, and no longer")
        self.assertLessEqual(len(calls), 2 * 20, "poll count must be bounded")

    def test_receipt_appearing_mid_window_is_found_and_verified(self):
        good = self.envelope()
        fake = _FakeClock()
        env, note = vr.poll_receipt_for_head(self.head, "o/r", api=self._api_receipt_at(fake, 100, good),
                                             window_s=300, interval_s=20, sleep=fake.sleep, clock=fake.clock)
        self.assertIsNotNone(env, "a receipt that lands mid-window must be found")
        self.assertLess(fake.t, 300, "polling must stop as soon as the receipt is found")
        code, reasons = self.run_verify(env)
        self.assertEqual((code, reasons), (0, []), "a receipt found mid-window must still verify VALID")

    def test_transient_lookup_failure_does_not_end_the_window(self):
        good = self.envelope()
        fake = _FakeClock()
        appear = self._api_receipt_at(fake, 40, good)

        def flaky(path):
            if fake.t == 0:
                raise RuntimeError("simulated gh outage")
            return appear(path)

        env, _ = vr.poll_receipt_for_head(self.head, "o/r", api=flaky, window_s=300, interval_s=20,
                                          sleep=fake.sleep, clock=fake.clock)
        self.assertIsNotNone(env, "one failed lookup must not stop the poll; later lookups still count")

    def test_zero_window_is_a_single_lookup_with_no_sleep(self):
        fake = _FakeClock()
        calls = []

        def api(path):
            calls.append(path)
            return {"check_runs": []} if "check-runs" in path else []

        env, _ = vr.poll_receipt_for_head(self.head, "o/r", api=api, window_s=0, interval_s=20,
                                          sleep=fake.sleep, clock=fake.clock)
        self.assertIsNone(env)
        self.assertEqual(fake.sleeps, [])
        self.assertEqual(len(calls), 2, "one lookup = check-runs + commit comments")

    def test_nonpositive_interval_is_rejected_so_the_loop_cannot_spin(self):
        with self.assertRaises(ValueError):
            vr.poll_receipt_for_head(self.head, "o/r", api=lambda p: None, window_s=300, interval_s=0)

    def test_cli_window_flags_are_honoured_and_validated(self):
        calls = []
        orig = vr._gh_api
        vr._gh_api = lambda path: calls.append(path) or None
        try:
            base_args = ["--fetch-for-head", self.head, "--repo-slug", "o/r",
                         "--out-found", str(Path(self.tmp.name) / "f"),
                         "--out-envelope", str(Path(self.tmp.name) / "e.json")]
            self.assertEqual(vr.main(base_args + ["--fetch-window-s", "0"], environ={}), 1)
            self.assertEqual(len(calls), 2, "--fetch-window-s 0 must mean exactly one lookup")
            with self.assertRaises(SystemExit):
                vr.main(base_args + ["--fetch-window-s", "100000"], environ={})
            with self.assertRaises(SystemExit):
                vr.main(base_args + ["--fetch-interval-s", "0"], environ={})
        finally:
            vr._gh_api = orig

    def test_cli_default_fetch_polls_past_a_first_miss(self):
        """main() must route --fetch-for-head through the poll with the default window: a receipt
        that lands on the second lookup is found and written, not reported MISSING."""
        real = vr.fetch_receipt_for_head
        seen = []

        def late(sha, slug, api=None):
            seen.append(sha)
            if len(seen) == 1:
                return None, "MISSING receipt: not yet"
            return {"marker": "late"}, "found on lookup 2"

        vr.fetch_receipt_for_head = late
        try:
            out_found = Path(self.tmp.name) / "late_found"
            out_env = Path(self.tmp.name) / "late.json"
            code = vr.main(["--fetch-for-head", self.head, "--repo-slug", "o/r", "--fetch-interval-s", "1",
                            "--out-found", str(out_found), "--out-envelope", str(out_env)], environ={})
        finally:
            vr.fetch_receipt_for_head = real
        self.assertEqual(code, 0, "the default CLI path must poll past the first miss")
        self.assertEqual(len(seen), 2, "exactly one retry after the miss, then stop")
        self.assertEqual(out_found.read_text(encoding="utf-8"), "true")
        self.assertEqual(json.loads(out_env.read_text(encoding="utf-8")), {"marker": "late"})


class TestDefaultMainRefResolvesLikeEmitter(unittest.TestCase):
    """verify_receipt's default --main-ref comes from the trunk resolver emit_receipt uses
    (emit_receipt.resolve_trunk), not a hardcoded origin/main. This repo has NO origin/main,
    only origin/master, so a hardcoded default cannot verify the receipt."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "feat")
        _commit(self.repo, "base")
        _git(self.repo, "update-ref", "refs/remotes/origin/master", "HEAD")
        self.head = _commit(self.repo, "feat1")
        self.receipt_path = Path(self.tmp.name) / "receipt.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_receipt(self, main_ref):
        receipt = emit.build_receipt(self.repo, _good_parts(), skipped=[], slug="o/r", main_ref=main_ref,
                                     profile="full")
        sig = rc.sign(rc.canonical_json(receipt), scheme="hmac-sha256", hmac_secret=_secret())
        self.receipt_path.write_text(json.dumps({"receipt": receipt, "sig": sig}), encoding="utf-8")

    def _verify(self, *extra):
        args = ["--receipt", str(self.receipt_path), "--repo", str(self.repo), "--head", self.head,
                "--required", ",".join(REQUIRED)] + list(extra)
        return vr.main(args, environ={"AESOP_RECEIPT_HMAC_SECRET": _secret()})

    def test_default_main_ref_resolves_to_origin_master_when_origin_main_absent(self):
        self._write_receipt("origin/master")
        self.assertEqual(self._verify(), 0)

    def test_explicit_unresolvable_main_ref_stays_fail_closed(self):
        self._write_receipt("origin/master")
        self.assertEqual(self._verify("--main-ref", "origin/nope"), 2)


def _commit_files(repo, files, message):
    """Commit each `name -> text` (nested paths allowed) and return the new HEAD sha."""
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class TestScopedRequiredShards(unittest.TestCase):
    """The scoped profile recomputes its required shards from the receipt's own changed files
    (emit_receipt._find_owning_shards over base_sha..head_sha) and fails closed: an owning set
    that is None (unowned tools/ change) or empty (docs-only) requires all four shards.
    The tests are on real repos: the shard map is derived by the verifier from the checkout's
    tracked tests/test_*.py, which round-robins sorted stems test_a..test_d onto py-shard-0..3.
    The caller's --required list (py-shard-0,py-shard-1 here) must no longer decide scope."""

    CALLER_REQUIRED = ["py-shard-0", "py-shard-1"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _commit_files(self.repo, {"tests/test_a.py": "a\n", "tests/test_b.py": "b\n",
                                  "tests/test_c.py": "c\n", "tests/test_d.py": "d\n"}, "m1")
        _commit_files(self.repo, {"m2.txt": "m2\n"}, "m2")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "checkout", "-q", "-b", "feat")

    def tearDown(self):
        self.tmp.cleanup()

    def _head_with(self, files):
        return _commit_files(self.repo, files, "feat change")

    @staticmethod
    def _passing(*names):
        return [{"name": n, "exit_code": 0, "test_count": 3, "duration_s": 1.0} for n in names]

    def _verify(self, parts, profile="scoped"):
        receipt = emit.build_receipt(self.repo, parts, skipped=[], slug="o/r", profile=profile)
        sig = rc.sign(rc.canonical_json(receipt), scheme="hmac-sha256", hmac_secret=_secret())
        return vr.verify({"receipt": receipt, "sig": sig}, repo=self.repo, head=receipt["head_sha"],
                         main_ref="origin/main", max_behind=50, required=self.CALLER_REQUIRED,
                         pubkey_path=None, hmac_secret=_secret())

    def test_scoped_receipt_omitting_owning_shard_is_invalid(self):
        self._head_with({"tests/test_c.py": "c2\n"})  # owned by py-shard-2
        code, reasons = self._verify(self._passing("py-shard-0", "py-shard-1"))
        self.assertEqual(code, 1, reasons)
        self.assertIn("required part missing: py-shard-2", reasons)

    def test_scoped_receipt_with_owning_shard_passing_is_valid(self):
        self._head_with({"tests/test_c.py": "c2\n"})
        self.assertEqual(self._verify(self._passing("py-shard-2")), (0, []))

    def test_docs_only_scoped_receipt_with_zero_py_parts_is_valid(self):
        # Deliberate rule change (PR fix/verify-receipt-docs-only-required): a docs-only diff has an
        # EMPTY owning set, which requires no py shards. The emitter's scoped receipt carries zero
        # py-shard parts there, so it must verify VALID. Only owning-is-None (unowned tools/) fails closed.
        self._head_with({"docs/guide.md": "guide\n"})
        non_py = [{"name": n, "exit_code": 0, "test_count": 0, "duration_s": 0.1}
                  for n in ("lint", "docs-gate")]
        self.assertEqual(self._verify(non_py), (0, []))

    def test_docs_only_scoped_receipt_with_all_four_passing_is_valid(self):
        # Rule changed deliberately (PR fix/verify-receipt-docs-only-required): an empty owning set
        # requires no py shards; four passing shards are still harmless extras, so this stays valid.
        self._head_with({"docs/guide.md": "guide\n"})
        self.assertEqual(self._verify(self._passing("py-shard-0", "py-shard-1", "py-shard-2", "py-shard-3")),
                         (0, []))

    def test_unowned_tools_change_fails_closed_to_all_four(self):
        self._head_with({"tools/brand_new.py": "x = 1\n"})  # _find_owning_shards -> None
        code, reasons = self._verify(self._passing("py-shard-0", "py-shard-1"))
        self.assertEqual(code, 1, reasons)
        self.assertIn("required part missing: py-shard-3", reasons)

    def test_unowned_tools_change_with_all_four_passing_is_valid(self):
        self._head_with({"tools/brand_new.py": "x = 1\n"})
        self.assertEqual(self._verify(self._passing("py-shard-0", "py-shard-1", "py-shard-2", "py-shard-3")),
                         (0, []))

    def test_full_profile_keeps_the_caller_required_list(self):
        self._head_with({"docs/guide.md": "guide\n"})
        self.assertEqual(self._verify(self._passing("py-shard-0", "py-shard-1"), profile="full"), (0, []))


if __name__ == "__main__":
    unittest.main()
