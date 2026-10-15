#!/usr/bin/env python3
"""
Red-first tests: the receipt verifier and its keyring must come from the BASE (origin/main),
never from the PR head checkout (adversarial finding on #970: a PR adding a rogue key under
tools/receipt_pubkeys/ and signing its own receipt got `receipt VALID`).

Covers:
  1. A receipt signed by a key present ONLY in the head keyring is INVALID when the verifier
     is pointed at the base keyring (--keyring / --pubkey from base). The head-keyring run is
     kept as a control that proves the attack is real.
  2. An empty required-parts list is refused (exit 2) unless --allow-no-required-parts is given.
  3. The verify-receipt workflow runs the BASE verifier and keyring, and fetches receipts with
     the base tool (structural check of the parsed workflow, fixture-style).
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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test_base_trust", TOOLS / (name + ".py"))
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


def _in_ci():
    return any(os.environ.get(k, "").strip().lower() in ("1", "true") for k in ("CI", "GITHUB_ACTIONS"))


def _crypto_guard(cls):
    """Locally: skip when cryptography is absent. Under CI: FAIL. A skipped base-keyring
    guard cannot catch the rogue-key attack, so a missing dependency must turn red there."""
    if HAVE_CRYPTO:
        return cls
    if _in_ci():
        def setUp(self):
            self.fail("cryptography not installed under CI: the base-keyring guard would be silently skipped")
        cls.setUp = setUp
        return cls
    return unittest.skip("cryptography not installed")(cls)

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


def _good_parts():
    return [{"name": n, "exit_code": 0, "test_count": 5, "duration_s": 1.0} for n in REQUIRED]


def _secret():
    return "verify" + "-" + "base" + "-" + "dummy" + "-" + "1" * 20


def _run_cli(argv):
    """Drive verify_receipt.main() in-process; return (exit_code, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = vr.main(argv, environ={})
    return code, buf.getvalue()


@_crypto_guard
class TestBaseKeyringRejectsHeadOnlyKey(unittest.TestCase):
    """Committed key A is on main. A PR adds rogue key B under tools/receipt_pubkeys/ and signs with B."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "tools" / "receipt_pubkeys").mkdir(parents=True)
        self.keys = root / "keys"
        self.keys.mkdir()
        self.key_a, self.pub_a = self.keys / "a.pem", self.keys / "a.pub"
        self.key_b, self.pub_b = self.keys / "b.pem", self.keys / "b.pub"
        rc.generate_ed25519_keypair(self.key_a, self.pub_a)
        rc.generate_ed25519_keypair(self.key_b, self.pub_b)
        (self.repo / "tools" / "receipt_pubkey.pub").write_bytes(self.pub_a.read_bytes())
        _commit(self.repo, "tools/receipt_pubkey.pub")
        self.base = _commit(self.repo, "m2")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        # The base keyring as it exists on origin/main: committed key only, no box keys.
        # Kept OUTSIDE the feat checkout so the rogue key on the PR head cannot leak into it.
        base_tools = root / "base" / "tools"
        (base_tools / "receipt_pubkeys").mkdir(parents=True)
        (base_tools / "receipt_pubkey.pub").write_bytes(self.pub_a.read_bytes())
        self.base_pubkey = base_tools / "receipt_pubkey.pub"
        self.base_keyring = base_tools / "receipt_pubkeys"
        _git(self.repo, "checkout", "-q", "-b", "feat")
        # The PR head: rogue key B registered in ITS OWN checkout.
        (self.repo / "tools" / "receipt_pubkeys" / "rogue.pub").write_bytes(self.pub_b.read_bytes())
        _git(self.repo, "add", "tools/receipt_pubkeys/rogue.pub")
        self.head = _commit(self.repo, "f1")
        self.env_file = root / "receipt.json"
        parts = _good_parts()
        receipt = emit.build_receipt(self.repo, parts, skipped=[], slug="o/r")
        sig = rc.sign(rc.canonical_json(receipt), scheme="ed25519", private_key_path=self.key_b)
        self.env_file.write_text(json.dumps({"receipt": receipt, "sig": sig}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _argv(self, pubkey, keyring):
        argv = ["--receipt", str(self.env_file), "--repo", str(self.repo), "--head", self.head,
                "--main-ref", "origin/main", "--max-behind", "50", "--required", ",".join(REQUIRED),
                "--pubkey", str(pubkey)]
        if keyring is not None:
            argv += ["--keyring", str(keyring)]
        return argv

    def test_control_head_keyring_accepts_rogue_key(self):
        """Control: pointed at the HEAD keyring the forged receipt IS valid. This is the attack."""
        head_pubkey = self.repo / "tools" / "receipt_pubkey.pub"
        head_keyring = self.repo / "tools" / "receipt_pubkeys"
        code, out = _run_cli(self._argv(head_pubkey, head_keyring))
        self.assertEqual(code, 0, out)
        self.assertIn("receipt VALID", out)

    def test_base_keyring_rejects_head_only_key(self):
        code, out = _run_cli(self._argv(self.base_pubkey, self.base_keyring))
        self.assertEqual(code, 1, out)
        self.assertIn("receipt INVALID", out)
        self.assertIn("signature: does not verify under ed25519", out)

    def test_keyring_flag_overrides_head_pubkey_dir(self):
        """Even when --pubkey is the PR head's own key, --keyring (base) decides what is trusted."""
        head_pubkey = self.repo / "tools" / "receipt_pubkey.pub"
        code, out = _run_cli(self._argv(head_pubkey, self.base_keyring))
        self.assertEqual(code, 1, out)
        self.assertIn("receipt INVALID", out)

    def test_keyring_flag_defaults_to_pubkey_dir_when_omitted(self):
        """Pointing only --pubkey at base still derives the base keyring (no head keyring leaks in)."""
        code, out = _run_cli(self._argv(self.base_pubkey, None))
        self.assertEqual(code, 1, out)


class TestEmptyRequiredRefused(unittest.TestCase):
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
        self.secret = _secret()
        self.env_file = Path(self.tmp.name) / "receipt.json"
        receipt = emit.build_receipt(self.repo, _good_parts(), skipped=[], slug="o/r")
        sig = rc.sign(rc.canonical_json(receipt), scheme="hmac-sha256", hmac_secret=self.secret)
        self.env_file.write_text(json.dumps({"receipt": receipt, "sig": sig}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _cli(self, required, extra=()):
        argv = ["--receipt", str(self.env_file), "--repo", str(self.repo), "--head", self.head,
                "--main-ref", "origin/main", "--required", required,
                "--pubkey", str(Path(self.tmp.name) / "absent.pub"), *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = vr.main(argv, environ={rc.HMAC_ENV: self.secret})
        return code, buf.getvalue()

    def test_api_empty_required_is_cannot_evaluate(self):
        code, reasons = vr.verify(
            json.loads(self.env_file.read_text(encoding="utf-8")), repo=self.repo, head=self.head,
            main_ref="origin/main", max_behind=50, required=[], pubkey_path=None, hmac_secret=self.secret)
        self.assertEqual(code, 2, reasons)
        self.assertTrue(any("required" in r for r in reasons), reasons)

    def test_cli_empty_required_string_is_refused(self):
        code, out = self._cli("")
        self.assertEqual(code, 2, out)

    def test_cli_empty_required_passes_only_with_explicit_flag(self):
        code, out = self._cli("", extra=("--allow-no-required-parts",))
        self.assertEqual(code, 0, out)

    def test_cli_nonempty_required_still_verifies(self):
        code, out = self._cli(",".join(REQUIRED))
        self.assertEqual(code, 0, out)


class TestWorkflowUsesBaseVerifier(unittest.TestCase):
    """Structural fixture over the parsed workflow: the trust-bearing steps run the BASE tree."""

    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest("PyYAML not installed")
        text = (REPO_ROOT / ".github" / "workflows" / "verify-receipt.yml").read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(text)
        cls.steps = cls.doc["jobs"]["verify-receipt"]["steps"]

    def _step_runs(self):
        return [s.get("run", "") for s in self.steps if isinstance(s, dict) and s.get("run")]

    def test_base_checkout_step_pins_base_sha(self):
        checkouts = [s for s in self.steps if isinstance(s, dict) and str(s.get("uses", "")).startswith("actions/checkout")]
        base = [s for s in checkouts if s.get("with", {}).get("path") == "base"]
        self.assertEqual(len(base), 1, "exactly one checkout must land the base tree in ./base")
        ref = base[0]["with"].get("ref", "")
        self.assertIn("github.event.pull_request.base.sha", ref)
        self.assertIn("'main'", ref, "workflow_dispatch must fall back to main")

    def test_no_run_step_invokes_the_head_verifier(self):
        for run in self._step_runs():
            for line in run.splitlines():
                if line.strip().startswith("#"):
                    continue
                if "verify_receipt.py" in line:
                    self.assertIn("base/tools/verify_receipt.py", line, line)

    def test_verify_step_uses_base_keyring_and_pubkey(self):
        verify = [s for s in self.steps if isinstance(s, dict) and "verify_receipt.py --receipt" in str(s.get("run", ""))]
        self.assertEqual(len(verify), 1)
        run = verify[0]["run"]
        self.assertIn("--pubkey base/tools/receipt_pubkey.pub", run)
        self.assertIn("--keyring base/tools/receipt_pubkeys", run)
        self.assertNotIn("--allow-no-required-parts", run)

    def test_fetch_step_uses_base_tool(self):
        fetch = [s for s in self.steps if isinstance(s, dict) and "--fetch-for-head" in str(s.get("run", ""))]
        self.assertEqual(len(fetch), 1)
        self.assertIn("base/tools/verify_receipt.py --fetch-for-head", fetch[0]["run"])


GATE_WORKFLOW = """name: fixture
on:
  pull_request:
    branches: [main]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          ref: ${{ github.event.pull_request.base.sha || 'main' }}
          path: base
      - name: run base verifier
        run: |
          python base/tools/thing.py --check
"""


class TestGateResolvesBaseCheckout(unittest.TestCase):
    """ci_gate_runability: a file under a checkout step's path: resolves like any other file."""

    def _findings(self, with_file):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "fixture.yml").write_text(GATE_WORKFLOW, encoding="utf-8")
        if with_file:
            (root / "tools").mkdir()
            (root / "tools" / "thing.py").write_text("print('ok')\n", encoding="utf-8")
        gate = _load("ci_gate_runability")
        jobs = gate.parse_yaml_line_by_line(GATE_WORKFLOW)
        return gate.check_jobs(str(root), ".github/workflows/fixture.yml", jobs)

    def test_base_checkout_file_present_is_not_flagged(self):
        self.assertEqual([f for f in self._findings(True) if "missing file" in f], [])

    def test_base_checkout_file_absent_is_still_flagged(self):
        flagged = [f for f in self._findings(False) if "missing file: base/tools/thing.py" in f]
        self.assertEqual(len(flagged), 1, flagged)

    def test_checkout_path_is_parsed_onto_the_step(self):
        gate = _load("ci_gate_runability")
        jobs = gate.parse_yaml_line_by_line(GATE_WORKFLOW)
        steps = jobs["verify"]["steps"]
        self.assertEqual(steps[0].get("checkout_path"), "base")


if __name__ == "__main__":
    unittest.main()
