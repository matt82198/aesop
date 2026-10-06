#!/usr/bin/env python3
"""
Behavioral tests for tools/emit_receipt.py + tools/receipt_common.py (receipt gate, increment 1).

Red-first: canonicalization is stable, a signed receipt verifies, tampering ANY field
breaks the signature, and `--dry-run` posts nothing (the gh runner is a tripwire that
fails the test if invoked).
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
    spec = importlib.util.spec_from_file_location(name + "_under_test", TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = _load("receipt_common")
emit = _load("emit_receipt")

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: F401
    HAVE_CRYPTO = True
except Exception:  # pragma: no cover - environment dependent
    HAVE_CRYPTO = False

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


def _mk_repo(tmp):
    repo = Path(tmp) / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "feat")
    return repo


def _hmac_secret():
    # Runtime-assembled dummy secret (never a literal credential shape).
    return "unit" + "-" + "test" + "-" + "receipt" + "-" + "value" + "-" + "0" * 16


class TestCanonicalJson(unittest.TestCase):
    def test_sorted_keys_no_whitespace_and_stable(self):
        a = {"b": 1, "a": {"z": [1, 2], "y": "s"}}
        b = {"a": {"y": "s", "z": [1, 2]}, "b": 1}
        ca, cb = rc.canonical_json(a), rc.canonical_json(b)
        self.assertEqual(ca, cb)
        self.assertEqual(ca, b'{"a":{"y":"s","z":[1,2]},"b":1}')
        self.assertNotIn(b" ", ca)
        self.assertNotIn(b"\n", ca)


class TestHmacSignature(unittest.TestCase):
    def setUp(self):
        self.receipt = {"schema": 1, "head_sha": "a" * 40, "parts": [{"name": "py-shard-0", "exit_code": 0}]}
        self.sig = rc.sign(rc.canonical_json(self.receipt), scheme="hmac-sha256", hmac_secret=_hmac_secret())

    def test_valid_signature_verifies(self):
        self.assertEqual(self.sig["scheme"], "hmac-sha256")
        self.assertTrue(rc.verify_signature(rc.canonical_json(self.receipt), self.sig, hmac_secret=_hmac_secret()))

    def test_tamper_each_top_level_field_fails(self):
        for key in list(self.receipt):
            tampered = json.loads(json.dumps(self.receipt))
            if isinstance(tampered[key], int):
                tampered[key] += 1
            elif isinstance(tampered[key], str):
                tampered[key] = "b" + tampered[key][1:]
            else:
                tampered[key][0]["exit_code"] = 1
            self.assertFalse(
                rc.verify_signature(rc.canonical_json(tampered), self.sig, hmac_secret=_hmac_secret()),
                "tampering field %r must break the signature" % key,
            )

    def test_wrong_secret_fails(self):
        self.assertFalse(rc.verify_signature(rc.canonical_json(self.receipt), self.sig, hmac_secret=_hmac_secret() + "x"))

    def test_missing_key_material_fails_not_passes(self):
        self.assertFalse(rc.verify_signature(rc.canonical_json(self.receipt), self.sig))


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed: Ed25519 path optional")
class TestEd25519Signature(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.priv = Path(self.tmp.name) / "key.pem"
        self.pub = Path(self.tmp.name) / "key.pub"
        rc.generate_ed25519_keypair(self.priv, self.pub)
        self.receipt = {"schema": 1, "tree_hash": "c" * 40, "parts": []}
        self.sig = rc.sign(rc.canonical_json(self.receipt), scheme="ed25519", private_key_path=self.priv)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pub_file_carries_only_public_material(self):
        text = self.pub.read_text(encoding="utf-8")
        self.assertIn("PUBLIC KEY", text)
        self.assertNotIn("PRIVATE", text)

    def test_valid_verifies_and_tamper_fails(self):
        self.assertEqual(self.sig["scheme"], "ed25519")
        canon = rc.canonical_json(self.receipt)
        self.assertTrue(rc.verify_signature(canon, self.sig, pubkey_path=self.pub))
        tampered = dict(self.receipt, tree_hash="d" * 40)
        self.assertFalse(rc.verify_signature(rc.canonical_json(tampered), self.sig, pubkey_path=self.pub))

    def test_other_pubkey_fails(self):
        other_priv = Path(self.tmp.name) / "o.pem"
        other_pub = Path(self.tmp.name) / "o.pub"
        rc.generate_ed25519_keypair(other_priv, other_pub)
        self.assertFalse(rc.verify_signature(rc.canonical_json(self.receipt), self.sig, pubkey_path=other_pub))


class TestBuildReceipt(unittest.TestCase):
    def test_receipt_binds_head_tree_and_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            parts = [{"name": "py-shard-0", "exit_code": 0, "test_count": 3, "duration_s": 0.1}]
            r = emit.build_receipt(repo, parts, skipped=[{"name": "browser-proofs", "reason": "x"}], slug="o/r")
            self.assertEqual(r["schema"], 1)
            self.assertEqual(r["repo"], "o/r")
            self.assertEqual(r["head_sha"], _git(repo, "rev-parse", "HEAD"))
            self.assertEqual(r["tree_hash"], _git(repo, "rev-parse", "HEAD^{tree}"))
            self.assertEqual(r["base_sha"], _git(repo, "rev-parse", "origin/main"))
            self.assertNotEqual(r["base_sha"], r["head_sha"])
            self.assertEqual(r["parts"], parts)
            self.assertEqual(r["skipped"][0]["name"], "browser-proofs")
            for k in ("os", "python", "hostname_hash"):
                self.assertIn(k, r["host"])
            self.assertEqual(len(r["host"]["hostname_hash"]), 16)
            self.assertTrue(r["timestamp"].endswith("Z"))


class TestRunMatrix(unittest.TestCase):
    def test_runs_runnable_parts_and_records_skips(self):
        ok = [sys.executable, "-c", "import sys; print('Ran 7 tests in 0.01s'); sys.exit(0)"]
        bad = [sys.executable, "-c", "import sys; sys.exit(3)"]
        registry = {
            "good": emit.PartSpec(lambda repo: ok),
            "bad": emit.PartSpec(lambda repo: bad),
            "browser-proofs": emit.PartSpec(None, "not runnable here: hosted browsers only"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            parts, skipped = emit.run_matrix(Path(tmp), ["good", "bad", "browser-proofs"], registry=registry, jobs=2)
        by = {p["name"]: p for p in parts}
        self.assertEqual(by["good"]["exit_code"], 0)
        self.assertEqual(by["good"]["test_count"], 7)
        self.assertEqual(by["bad"]["exit_code"], 3)
        self.assertIsNone(by["bad"]["test_count"])
        self.assertGreaterEqual(by["good"]["duration_s"], 0)
        self.assertEqual(skipped, [{"name": "browser-proofs", "reason": "not runnable here: hosted browsers only"}])

    def test_unknown_part_name_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(emit.ReceiptError):
                emit.run_matrix(Path(tmp), ["nope"], registry={}, jobs=1)

    def test_default_registry_declares_shards_and_prepush_gates(self):
        names = set(emit.DEFAULT_REGISTRY)
        for i in range(4):
            self.assertIn("py-shard-%d" % i, names)
        for g in ("secret-scan", "claudemd-sync-gate", "gen-tool-index", "verify-test-suite-count",
                  "encoding-lint", "import-resolution-check", "sibling-import-check", "browser-proofs"):
            self.assertIn(g, names)
        self.assertIsNone(emit.DEFAULT_REGISTRY["browser-proofs"].cmd)
        self.assertEqual(emit.conclusion_for([{"exit_code": 0}, {"exit_code": 0}]), "success")
        self.assertEqual(emit.conclusion_for([{"exit_code": 0}, {"exit_code": 1}]), "failure")


class TestCliDryRunAndPost(unittest.TestCase):
    def _registry(self):
        ok = [sys.executable, "-c", "print('Ran 2 tests in 0.00s')"]
        return {"py-shard-0": emit.PartSpec(lambda repo: ok), "browser-proofs": emit.PartSpec(None, "hosted only")}

    def test_dry_run_prints_envelope_and_never_calls_gh(self):
        calls = []

        def tripwire(args):
            calls.append(args)
            raise AssertionError("gh must not be invoked under --dry-run")

        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            out = Path(tmp) / "r.json"
            env = {"AESOP_RECEIPT_HMAC_SECRET": _hmac_secret()}
            code = emit.main(["--repo", str(repo), "--matrix", "py-shard-0,browser-proofs", "--scheme", "hmac-sha256",
                              "--slug", "o/r", "--dry-run", "--post", "--out", str(out)],
                             registry=self._registry(), gh_runner=tripwire, environ=env)
            self.assertEqual(code, 0)
            self.assertEqual(calls, [])
            env_json = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(env_json["sig"]["scheme"], "hmac-sha256")
            self.assertTrue(rc.verify_signature(rc.canonical_json(env_json["receipt"]), env_json["sig"],
                                                hmac_secret=_hmac_secret()))
            self.assertEqual(env_json["receipt"]["parts"][0]["test_count"], 2)

    def test_post_uses_check_run_then_falls_back_to_commit_comment(self):
        calls = []

        def gh_403_then_ok(args):
            calls.append(args)
            if "check-runs" in args[1]:
                return (1, "", "HTTP 403: Resource not accessible by personal access token")
            return (0, json.dumps({"id": 7, "html_url": "https://example.invalid/c/7"}), "")

        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            env = {"AESOP_RECEIPT_HMAC_SECRET": _hmac_secret()}
            code = emit.main(["--repo", str(repo), "--matrix", "py-shard-0", "--scheme", "hmac-sha256",
                              "--slug", "o/r", "--post"], registry=self._registry(), gh_runner=gh_403_then_ok, environ=env)
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 2)
            self.assertIn("repos/o/r/check-runs", calls[0][1])
            head = _git(repo, "rev-parse", "HEAD")
            self.assertIn("repos/o/r/commits/%s/comments" % head, calls[1][1])

    def test_post_fails_closed_when_both_channels_fail(self):
        def gh_fail(args):
            return (1, "", "HTTP 500")

        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            env = {"AESOP_RECEIPT_HMAC_SECRET": _hmac_secret()}
            code = emit.main(["--repo", str(repo), "--matrix", "py-shard-0", "--scheme", "hmac-sha256",
                              "--slug", "o/r", "--post"], registry=self._registry(), gh_runner=gh_fail, environ=env)
            self.assertEqual(code, 2)

    def test_no_key_material_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            code = emit.main(["--repo", str(repo), "--matrix", "py-shard-0", "--scheme", "hmac-sha256",
                              "--slug", "o/r", "--dry-run"], registry=self._registry(), gh_runner=None, environ={})
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
