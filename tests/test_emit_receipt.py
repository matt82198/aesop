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


class TestWslRequiredPart(unittest.TestCase):
    """A part marked requires_wsl is skipped (not failed) when no WSL distro exists,
    and fails closed when the WSL probe itself errors."""

    OK_CMD = [sys.executable, "-c", "import sys; sys.exit(0)"]
    BAD_CMD = [sys.executable, "-c", "import sys; sys.exit(1)"]

    def _registry(self):
        return {
            "good": emit.PartSpec(lambda repo: self.OK_CMD),
            "linux-shape": emit.PartSpec(lambda repo: self.BAD_CMD, requires_wsl=True),
        }

    def _run(self, status):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            return emit.run_matrix(repo, ["good", "linux-shape"], registry=self._registry(), jobs=1,
                                   wsl_status_fn=lambda: status)

    def test_no_distro_skips_wsl_part_and_conclusion_stays_success(self):
        parts, skipped = self._run("no-distro")
        self.assertEqual([p["name"] for p in parts], ["good"])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["name"], "linux-shape")
        self.assertIn("WSL", skipped[0]["reason"])
        self.assertEqual(emit.conclusion_for(parts), "success")

    def test_absent_wsl_also_skips_wsl_part(self):
        parts, skipped = self._run("absent")
        self.assertEqual([p["name"] for p in parts], ["good"])
        self.assertEqual(skipped[0]["name"], "linux-shape")
        self.assertEqual(emit.conclusion_for(parts), "success")

    def test_probe_error_runs_wsl_part_and_fails_closed(self):
        parts, skipped = self._run("error")
        self.assertEqual(skipped, [])
        by = {p["name"]: p for p in parts}
        self.assertEqual(by["linux-shape"]["exit_code"], 1)
        self.assertEqual(emit.conclusion_for(parts), "failure")

    def test_ok_wsl_runs_part_and_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            registry = {"linux-shape": emit.PartSpec(lambda r: self.OK_CMD, requires_wsl=True)}
            parts, skipped = emit.run_matrix(repo, ["linux-shape"], registry=registry, jobs=1,
                                             wsl_status_fn=lambda: "ok")
        self.assertEqual(skipped, [])
        self.assertEqual(parts[0]["exit_code"], 0)
        self.assertEqual(emit.conclusion_for(parts), "success")

    def test_default_registry_declares_linux_shape_part_requiring_wsl(self):
        spec = emit.DEFAULT_REGISTRY["linux-shape"]
        self.assertTrue(spec.requires_wsl)
        self.assertIsNotNone(spec.cmd)
        self.assertIn("--require-wsl", spec.cmd(TOOLS.parent))


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


class TestRedGateExitCodes(unittest.TestCase):
    """--fail-on-red / --pushed-red: the exit code the pre-push hook reads (docs/archive/RECEIPT-GATE.md).

    0 = green (or red without --fail-on-red: legacy contract), 3 = red and blocked,
    4 = red, override accepted, receipt stamped pushed_red: true. Red is any part exit != 0;
    skipped parts (reason printed, never in `parts`) never make a receipt red.
    """

    OK = [sys.executable, "-c", "print('Ran 2 tests in 0.00s')"]
    BAD = [sys.executable, "-c", "import sys; print('FAILED one'); sys.exit(1)"]

    def _registry(self, red):
        return {"py-shard-0": emit.PartSpec(lambda repo: self.OK),
                "py-shard-1": emit.PartSpec(lambda repo: self.BAD if red else self.OK),
                "browser-proofs": emit.PartSpec(None, "not runnable here: hosted browsers only")}

    def _run(self, tmp, red, extra):
        posted = []

        def gh(args):
            # The check-run payload file is deleted after the call: capture its JSON while it exists.
            if "--input" in args:
                posted.append(json.loads(Path(args[-1]).read_text(encoding="utf-8")))
            return (0, "{}", "")

        repo = _mk_repo(tmp)
        out = Path(tmp) / "env.json"
        argv = ["--repo", str(repo), "--matrix", "py-shard-0,py-shard-1,browser-proofs", "--scheme", "hmac-sha256",
                "--slug", "o/r", "--post", "--out", str(out)] + extra
        code = emit.main(argv, registry=self._registry(red), gh_runner=gh,
                         environ={"AESOP_RECEIPT_HMAC_SECRET": _hmac_secret()})
        return code, json.loads(out.read_text(encoding="utf-8")), posted

    def test_red_required_part_blocks_with_exit_3_and_still_posts_failure_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, env_json, posted = self._run(tmp, red=True, extra=["--fail-on-red"])
        self.assertEqual(code, 3)
        # The red record is still published: the trace of the red push attempt survives.
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["conclusion"], "failure")
        self.assertNotIn("pushed_red", env_json["receipt"])

    def test_all_green_is_allowed_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, env_json, posted = self._run(tmp, red=False, extra=["--fail-on-red"])
        self.assertEqual(code, 0)
        self.assertEqual(posted[0]["conclusion"], "success")
        self.assertNotIn("pushed_red", env_json["receipt"])

    def test_skipped_with_reason_is_not_red_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, env_json, _ = self._run(tmp, red=False, extra=["--fail-on-red"])
        self.assertEqual(code, 0)
        self.assertEqual([s["name"] for s in env_json["receipt"]["skipped"]], ["browser-proofs"])

    def test_red_without_fail_on_red_keeps_legacy_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = self._run(tmp, red=True, extra=[])
        self.assertEqual(code, 0)

    def test_pushed_red_override_exit_4_and_stamps_signed_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, env_json, _ = self._run(tmp, red=True, extra=["--fail-on-red", "--pushed-red"])
        self.assertEqual(code, 4)
        self.assertIs(env_json["receipt"]["pushed_red"], True)
        # The stamp is inside the signed bytes: the signature verifies over the stamped receipt...
        self.assertTrue(rc.verify_signature(rc.canonical_json(env_json["receipt"]), env_json["sig"],
                                            hmac_secret=_hmac_secret()))
        # ...and flipping it breaks the signature (it is not a free-floating label).
        tampered = dict(env_json["receipt"], pushed_red=False)
        self.assertFalse(rc.verify_signature(rc.canonical_json(tampered), env_json["sig"],
                                             hmac_secret=_hmac_secret()))

    def test_pushed_red_on_green_is_exit_0_without_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, env_json, _ = self._run(tmp, red=False, extra=["--fail-on-red", "--pushed-red"])
        self.assertEqual(code, 0)
        self.assertNotIn("pushed_red", env_json["receipt"])


class TestShardEnvScrubsTripwireArmed(unittest.TestCase):
    """The shard env emit_receipt builds must not inherit AESOP_CI_SHARD_TRIPWIRE_ARMED.

    ci_shard_runner._arm_isolation_tripwire() returns early (no tripwire) when that variable
    is set, so an inherited value would run the receipt matrix with the isolation tripwire
    disabled. Driven through run_part() (the real env builder) with a part that inspects its
    own environment.
    """

    VAR = "AESOP_CI_SHARD_TRIPWIRE_ARMED"
    CHECK = [sys.executable, "-c",
             "import os, sys; sys.exit(1 if 'AESOP_CI_SHARD_TRIPWIRE_ARMED' in os.environ else 0)"]

    def test_part_env_does_not_inherit_tripwire_armed(self):
        spec = emit.PartSpec(lambda repo: self.CHECK)
        with tempfile.TemporaryDirectory() as tmp:
            caller_env = dict(os.environ)
            caller_env[self.VAR] = "1"
            result = emit.run_part(Path(tmp), "py-shard-0", spec, environ=caller_env)
        self.assertEqual(result["exit_code"], 0, "shard part saw %s from the caller env" % self.VAR)

    def test_scrub_child_env_removes_tripwire_armed(self):
        child, removed = emit.scrub_child_env({self.VAR: "1", "PATH": os.environ.get("PATH", "")})
        self.assertNotIn(self.VAR, child)
        self.assertIn(self.VAR, removed)


def _mk_trunk_repo(tmp, remote_refs):
    """A repo whose origin/* refs are exactly remote_refs (names, all at the base commit).

    An empty remote_refs list is a fresh remote with no trunk yet (BOOTSTRAP). The feature
    commit is HEAD, one commit past the base.
    """
    repo = Path(tmp) / "trunk-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "feat")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "base")
    for name in remote_refs:
        _git(repo, "update-ref", "refs/remotes/origin/" + name, "HEAD")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "feat")
    return repo


class TestTrunkResolution(unittest.TestCase):
    """The emitter resolves the trunk the way hooks/pre-push-policy.sh gate_trunk_ref does."""

    def _emit(self, repo, tmp, *extra):
        out = Path(tmp) / "r.json"
        env = {"AESOP_RECEIPT_HMAC_SECRET": _hmac_secret()}
        registry = {"py-shard-0": emit.PartSpec(lambda r: [sys.executable, "-c", "print('Ran 1 tests in 0.00s')"])}
        code = emit.main(["--repo", str(repo), "--matrix", "py-shard-0", "--scheme", "hmac-sha256",
                          "--dry-run", "--out", str(out)] + list(extra),
                         registry=registry, gh_runner=lambda args: None, environ=env)
        receipt = json.loads(out.read_text(encoding="utf-8"))["receipt"] if out.exists() else None
        return code, receipt

    def test_trunk_master_on_origin_base_is_merge_base_with_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_trunk_repo(tmp, ["master"])
            code, r = self._emit(repo, tmp)
            self.assertEqual(code, 0)
            self.assertEqual(r["base_sha"], _git(repo, "merge-base", "HEAD", "origin/master"))
            self.assertNotEqual(r["base_sha"], r["head_sha"])
            self.assertEqual(r["trunk_ref"], "origin/master")

    def test_empty_remote_is_bootstrap_base_is_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_trunk_repo(tmp, [])
            code, r = self._emit(repo, tmp)
            self.assertEqual(code, 0)
            self.assertEqual(r["base_sha"], _git(repo, "rev-parse", "HEAD"))
            self.assertEqual(r["trunk_ref"], "HEAD")

    def test_origin_head_symbolic_target_wins_over_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_trunk_repo(tmp, ["main", "master"])
            _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
            code, r = self._emit(repo, tmp)
            self.assertEqual(code, 0)
            self.assertEqual(r["trunk_ref"], "origin/master")
            self.assertEqual(r["base_sha"], _git(repo, "merge-base", "HEAD", "origin/master"))

    def test_unresolvable_explicit_main_ref_falls_back_to_trunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_trunk_repo(tmp, ["master"])
            code, r = self._emit(repo, tmp, "--main-ref", "origin/nope")
            self.assertEqual(code, 0)
            self.assertEqual(r["trunk_ref"], "origin/master")
            self.assertEqual(r["base_sha"], _git(repo, "merge-base", "HEAD", "origin/master"))


class TestNonGithubRemotePost(unittest.TestCase):
    """A check-run only exists on github.com. Non-GitHub origins skip the POST; GitHub errors still block."""

    def _registry(self):
        ok = [sys.executable, "-c", "print('Ran 1 tests in 0.00s')"]
        return {"py-shard-0": emit.PartSpec(lambda repo: ok)}

    def _bare_origin(self, tmp):
        bare = Path(tmp) / "remote.git"
        _git(Path(tmp), "init", "-q", "--bare", str(bare))
        return bare

    def test_non_github_origin_skips_post_and_records_field(self):
        calls = []

        def tripwire(args):
            calls.append(args)
            return (1, "", "HTTP 404: Not Found")

        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            _git(repo, "remote", "add", "origin", str(self._bare_origin(tmp)))
            out = Path(tmp) / "r.json"
            env = {"AESOP_RECEIPT_HMAC_SECRET": _hmac_secret()}
            code = emit.main(["--repo", str(repo), "--matrix", "py-shard-0", "--scheme", "hmac-sha256",
                              "--post", "--out", str(out)],
                             registry=self._registry(), gh_runner=tripwire, environ=env)
            self.assertEqual(code, 0)
            self.assertEqual(calls, [], "no GitHub API call for a non-GitHub origin")
            env_json = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(env_json["receipt"]["posted"], {"skipped": "non-github-remote", "remote": "local-path"})
            self.assertTrue(rc.verify_signature(rc.canonical_json(env_json["receipt"]), env_json["sig"],
                                                hmac_secret=_hmac_secret()))

    def test_github_origin_404_still_blocks_with_exit_2(self):
        calls = []

        def gh_404(args):
            calls.append(args)
            return (1, "", "HTTP 404: Not Found")

        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            _git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
            env = {"AESOP_RECEIPT_HMAC_SECRET": _hmac_secret()}
            code = emit.main(["--repo", str(repo), "--matrix", "py-shard-0", "--scheme", "hmac-sha256",
                              "--post"], registry=self._registry(), gh_runner=gh_404, environ=env)
            self.assertEqual(code, 2)
            self.assertEqual(len(calls), 1)
            self.assertIn("repos/o/r/check-runs", calls[0][1])

    def test_remote_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_repo(tmp)
            self.assertIsNone(emit.non_github_remote(repo), "no origin keeps today's behaviour")
            local = {"skipped": "non-github-remote", "remote": "local-path"}
            cases = [
                ("https://github.com/o/r.git", None),
                ("git@github.com:o/r.git", None),
                ("ssh://git@github.com/o/r", None),
                ("C:/Users/x/remote.git", local),
                ("file:///tmp/remote.git", local),
                ("git@gitlab.example.com:o/r.git", {"skipped": "non-github-remote", "remote": "other-host"}),
            ]
            _git(repo, "remote", "add", "origin", cases[0][0])
            for url, expected in cases:
                _git(repo, "remote", "set-url", "origin", url)
                self.assertEqual(emit.non_github_remote(repo), expected, url)


_HANG_SRC = """import sys, time
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write(str(__import__("os").getpid()))
time.sleep(600)
"""

_HANG_WITH_GRANDCHILD_SRC = """import subprocess, sys, time
child = subprocess.Popen([sys.executable, sys.argv[2]])
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write(str(__import__("os").getpid()) + " " + str(child.pid))
time.sleep(600)
"""

_OK_SRC = "import sys\nsys.exit(0)\n"


def _alive(pid):
    """True when `pid` is still running. Windows uses tasklist (os.kill(pid, 0) is CTRL_C there)."""
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=30).stdout
        return str(pid) in out.split()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _bounded(fn, budget_s):
    """Run fn() in a daemon thread; fail the test (not the runner) if it has not returned in budget_s."""
    import threading
    box = {}

    def target():
        box["value"] = fn()

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(budget_s)
    if worker.is_alive():
        raise AssertionError("run_part still blocked after %ss: a hung part is not bounded" % budget_s)
    return box["value"]


class TestPartTimeout(unittest.TestCase):
    """A hung receipt-matrix part must fail RED within its bound and take its whole tree with it.
    Driven through run_part() (the real Popen path); no timed-out part may ever be reported green."""

    def _helper(self, tmp, name, src):
        path = Path(tmp) / name
        path.write_text(src, encoding="utf-8")
        return path

    def test_hung_part_fails_red_within_bound_and_leaves_no_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = self._helper(tmp, "hang.py", _HANG_SRC)
            pidfile = Path(tmp) / "pid.txt"
            spec = emit.PartSpec(lambda repo: [sys.executable, str(helper), str(pidfile)])
            logfile = Path(tmp) / "part.log"
            env = dict(os.environ, AESOP_RECEIPT_PART_TIMEOUT="5")
            res = _bounded(lambda: emit.run_part(Path(tmp), "hung", spec, log_path=logfile, environ=env), 30)
            self.assertNotEqual(res["exit_code"], 0, "a timed-out part must be RED")
            self.assertLess(res["duration_s"], 30)
            self.assertIn("timed out after 5s", logfile.read_text(encoding="utf-8"))
            self.assertFalse(_alive(int(pidfile.read_text(encoding="utf-8"))), "hung part survived the kill")

    def test_sleeping_grandchild_is_killed_with_its_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            grandchild = self._helper(tmp, "grandchild.py", _HANG_SRC)
            parent = self._helper(tmp, "parent.py", _HANG_WITH_GRANDCHILD_SRC)
            pidfile = Path(tmp) / "pids.txt"
            spec = emit.PartSpec(lambda repo: [sys.executable, str(parent), str(pidfile), str(grandchild)])
            env = dict(os.environ, AESOP_RECEIPT_PART_TIMEOUT="5")
            # The grandchild shares the pipe, so a tree kill is the only thing that lets run_part return.
            res = _bounded(lambda: emit.run_part(Path(tmp), "tree", spec, environ=env), 30)
            self.assertNotEqual(res["exit_code"], 0)
            parent_pid, child_pid = (int(x) for x in pidfile.read_text(encoding="utf-8").split())
            self.assertFalse(_alive(parent_pid), "parent survived the kill")
            self.assertFalse(_alive(child_pid), "grandchild survived the kill")

    def test_fast_part_is_unaffected_by_the_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = self._helper(tmp, "ok.py", _OK_SRC)
            spec = emit.PartSpec(lambda repo: [sys.executable, str(ok)])
            env = dict(os.environ, AESOP_RECEIPT_PART_TIMEOUT="5")
            res = _bounded(lambda: emit.run_part(Path(tmp), "fast", spec, environ=env), 30)
            self.assertEqual(res["exit_code"], 0)

    def test_invalid_timeout_uses_default_and_says_so(self):
        import io
        for raw in ("abc", "0", "-5", "nan", "inf"):
            err = io.StringIO()
            real_stderr, sys.stderr = sys.stderr, err
            try:
                value = emit.part_timeout_seconds({"AESOP_RECEIPT_PART_TIMEOUT": raw})
            finally:
                sys.stderr = real_stderr
            self.assertEqual(value, emit.PART_TIMEOUT_DEFAULT_S, raw)
            self.assertIn("AESOP_RECEIPT_PART_TIMEOUT", err.getvalue(), raw)
        self.assertEqual(emit.part_timeout_seconds({"AESOP_RECEIPT_PART_TIMEOUT": "7"}), 7.0)
        self.assertEqual(emit.part_timeout_seconds({}), emit.PART_TIMEOUT_DEFAULT_S)


if __name__ == "__main__":
    unittest.main()
