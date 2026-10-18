#!/usr/bin/env python3
"""Red-first tests: receipt verifier keyring (several registered Ed25519 public keys).

The committed key tools/receipt_pubkey.pub stays as-is; additional box keys live
in tools/receipt_pubkeys/*.pub. A receipt verifies when the public key whose
key_id equals sig.key_id is registered AND the signature checks under that key.
The verifier never trusts sig.key_id on its own: the signature must still verify.
"""

import importlib.util
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
vr = _load("verify_receipt")

def _have_crypto():
    try:
        return importlib.util.find_spec("cryptography.hazmat.primitives.asymmetric.ed25519") is not None
    except Exception:  # parent package missing raises ModuleNotFoundError
        return False


HAVE_CRYPTO = _have_crypto()

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1",
}
REQUIRED = ["py-shard-0", "py-shard-1"]


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


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed")
class TestKeyring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.key_a = self.dir / "a.pem"
        self.pub_a = self.dir / "a.pub"
        self.key_b = self.dir / "b.pem"
        self.pub_b = self.dir / "b.pub"
        rc.generate_ed25519_keypair(self.key_a, self.pub_a)
        rc.generate_ed25519_keypair(self.key_b, self.pub_b)

    def tearDown(self):
        self.tmp.cleanup()

    def _sig_by(self, key_path, canonical):
        return rc.sign(canonical, scheme="ed25519", private_key_path=key_path)

    def test_registered_second_key_verifies_by_key_id(self):
        canon = rc.canonical_json({"x": 1})
        sig = self._sig_by(self.key_b, canon)
        self.assertTrue(rc.verify_signature_keyring(canon, sig, [self.pub_a, self.pub_b]))

    def test_signer_not_in_keyring_is_rejected(self):
        canon = rc.canonical_json({"x": 1})
        sig = self._sig_by(self.key_b, canon)
        self.assertFalse(rc.verify_signature_keyring(canon, sig, [self.pub_a]))

    def test_forged_key_id_pointing_at_other_key_is_rejected(self):
        canon = rc.canonical_json({"x": 1})
        sig = self._sig_by(self.key_b, canon)
        forged = dict(sig, key_id=rc.key_id_for_pubkey(self.pub_a.read_bytes()))
        self.assertFalse(rc.verify_signature_keyring(canon, forged, [self.pub_a, self.pub_b]))

    def test_tampered_payload_is_rejected(self):
        canon = rc.canonical_json({"x": 1})
        sig = self._sig_by(self.key_b, canon)
        self.assertFalse(rc.verify_signature_keyring(rc.canonical_json({"x": 2}), sig, [self.pub_b]))

    def test_crlf_working_copy_has_same_key_id_as_lf_blob(self):
        """Windows autocrlf checkouts rewrite the PEM to CRLF; the key_id must not change."""
        lf = self.pub_b.read_bytes()
        crlf = self.dir / "b_crlf.pub"
        crlf.write_bytes(lf.replace(b"\n", b"\r\n"))
        self.assertEqual(rc.key_id_for_pubkey(crlf.read_bytes()), rc.key_id_for_pubkey(lf))
        canon = rc.canonical_json({"x": 1})
        sig = self._sig_by(self.key_b, canon)
        self.assertTrue(rc.verify_signature_keyring(canon, sig, [crlf]))

    def test_keyring_paths_includes_committed_key_and_registered_dir(self):
        tools = self.dir / "tools"
        (tools / "receipt_pubkeys").mkdir(parents=True)
        committed = tools / "receipt_pubkey.pub"
        committed.write_bytes(self.pub_a.read_bytes())
        (tools / "receipt_pubkeys" / "b.pub").write_bytes(self.pub_b.read_bytes())
        paths = rc.keyring_paths(committed)
        self.assertEqual(paths[0], committed)
        self.assertIn(tools / "receipt_pubkeys" / "b.pub", paths)


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed")
class TestVerifyWithKeyring(unittest.TestCase):
    """verify() end to end: committed key A, box key B registered under tools/receipt_pubkeys/."""

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
        keys = Path(self.tmp.name) / "keys"
        keys.mkdir()
        self.key_a, self.pub_a = keys / "a.pem", keys / "a.pub"
        self.key_b, self.pub_b = keys / "b.pem", keys / "b.pub"
        rc.generate_ed25519_keypair(self.key_a, self.pub_a)
        rc.generate_ed25519_keypair(self.key_b, self.pub_b)
        (self.repo / "tools" / "receipt_pubkeys").mkdir(parents=True)
        (self.repo / "tools" / "receipt_pubkey.pub").write_bytes(self.pub_a.read_bytes())

    def tearDown(self):
        self.tmp.cleanup()

    def _envelope(self, key_path):
        parts = [{"name": n, "exit_code": 0, "test_count": 5, "duration_s": 1.0} for n in REQUIRED]
        # profile="full": these fixtures test key trust, not shard scoping
        receipt = emit.build_receipt(self.repo, parts, skipped=[], slug="o/r", profile="full")
        sig = rc.sign(rc.canonical_json(receipt), scheme="ed25519", private_key_path=key_path)
        return {"receipt": receipt, "sig": sig}

    def _verify(self, env):
        return vr.verify(env, repo=self.repo, head=self.head, main_ref="origin/main", max_behind=50,
                         required=REQUIRED, pubkey_path=self.repo / "tools" / "receipt_pubkey.pub",
                         hmac_secret=None)

    def test_receipt_from_registered_box_key_verifies(self):
        (self.repo / "tools" / "receipt_pubkeys" / "b.pub").write_bytes(self.pub_b.read_bytes())
        self.assertEqual(self._verify(self._envelope(self.key_b))[0], 0)

    def test_receipt_from_unregistered_key_is_invalid(self):
        self.assertEqual(self._verify(self._envelope(self.key_b))[0], 1)

    def test_committed_key_still_verifies(self):
        self.assertEqual(self._verify(self._envelope(self.key_a))[0], 0)


if __name__ == "__main__":
    unittest.main()
