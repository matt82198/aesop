#!/usr/bin/env python3
"""Behavioral tests for tools/verify_handback.py (deterministic hand-back verifier)."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import emit_receipt as emit  # noqa: E402
from tools import receipt_common as rc  # noqa: E402
from tools import verify_handback as vh  # noqa: E402

_GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "Test Worker",
    "GIT_AUTHOR_EMAIL": "worker@example.invalid",
    "GIT_COMMITTER_NAME": "Test Worker",
    "GIT_COMMITTER_EMAIL": "worker@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}

REQUIRED_PARTS = ["py-shard-0", "py-shard-1"]
_UNSET = object()


def _dummy_hmac_secret() -> str:
    """Assemble a test-only HMAC key at runtime so no literal token sits in source."""
    return "hb" + "-" + "unit" + "-" + "key" + "-" + ("7" * 20)


def _git(repo: Path, *args: str) -> str:
    """Run git against a temporary fixture repository with isolated identity."""
    env = dict(os.environ)
    env.update(_GIT_IDENTITY_ENV)
    env["HOME"] = str(repo.parent)
    res = subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        cwd=str(repo),
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    return (res.stdout or "").strip()


def _commit_files(repo: Path, files: dict, message: str) -> str:
    """Write relative file paths into `repo`, stage, commit, and return HEAD sha."""
    for rel_path, content in files.items():
        full_path = repo / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        _git(repo, "add", rel_path)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _good_parts(names=None) -> list:
    """Build passing receipt part entries."""
    part_names = names if names is not None else REQUIRED_PARTS
    return [
        {"name": name, "exit_code": 0, "test_count": 4, "duration_s": 0.5}
        for name in part_names
    ]


class HandbackFixtureBase(unittest.TestCase):
    """Temporary git repo fixture with origin/main, feature commit, and HMAC receipts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.repo = self.tmp_path / "wt"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _commit_files(
            self.repo,
            {
                "README.md": "root doc\n",
                "outside/locked.txt": "do not touch\n",
                "tools/existing.py": "VALUE = 1\n",
            },
            "initial main",
        )
        self.base_sha = _commit_files(
            self.repo,
            {"docs/guide.md": "guide v1\n"},
            "second main",
        )
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "checkout", "-q", "-b", "feat/lane-work")
        self.head_sha = _commit_files(
            self.repo,
            {
                "tools/new_gate.py": "OK = True\n",
                "tests/test_new_gate.py": "TEST = True\n",
            },
            "lane changes inside scope",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def make_envelope(self, parts=None, secret=None, main_ref="origin/main", profile="full", **overrides) -> dict:
        """Create an HMAC-signed receipt envelope for the current fixture checkout."""
        receipt = emit.build_receipt(
            self.repo,
            parts if parts is not None else _good_parts(),
            skipped=[],
            slug="owner/repo",
            main_ref=main_ref,
            profile=profile,
        )
        receipt.update(overrides)
        sig = rc.sign(
            rc.canonical_json(receipt),
            scheme="hmac-sha256",
            hmac_secret=_dummy_hmac_secret() if secret is None else secret,
        )
        return {"receipt": receipt, "sig": sig}

    def make_handback(self, **overrides) -> dict:
        """Create a valid hand-back dictionary for the fixture branch."""
        payload = {
            "lane": "handback-verifier",
            "scope": ["tools/*.py", "tests/test_*.py"],
            "test_run": {
                "command": "python -m unittest tests.test_new_gate -v",
                "exit_code": 0,
            },
        }
        payload.update(overrides)
        return payload

    def verify(self, handback=_UNSET, **kwargs) -> dict:
        """Invoke verify_handback with fixture defaults unless overridden."""
        hb = self.make_handback() if handback is _UNSET else handback
        params = {
            "repo": self.repo,
            "receipt_envelope": self.make_envelope(),
            "base_ref": "origin/main",
            "main_ref": "origin/main",
            "max_behind": 50,
            "required": REQUIRED_PARTS,
            "hmac_secret": _dummy_hmac_secret(),
        }
        params.update(kwargs)
        return vh.verify_handback(hb, **params)


class TestVerifyHandbackAccept(HandbackFixtureBase):
    """Happy-path acceptance and schema contract tests."""

    def test_accepts_when_all_four_checks_pass(self):
        verdict = self.verify()
        self.assertEqual(
            set(verdict.keys()),
            {"v", "ok", "verdict", "lane", "head_sha", "checks", "reasons"},
        )
        self.assertEqual(verdict["v"], 1)
        self.assertIs(verdict["ok"], True)
        self.assertEqual(verdict["verdict"], "ACCEPT")
        self.assertEqual(verdict["lane"], "handback-verifier")
        self.assertEqual(verdict["head_sha"], self.head_sha)
        self.assertEqual(verdict["reasons"], [])
        self.assertEqual(
            set(verdict["checks"].keys()),
            {"receipt", "worktree_clean", "scope", "tests"},
        )
        for name, check in verdict["checks"].items():
            self.assertIs(check["ok"], True, f"check {name} must be ok=True")
        self.assertEqual(vh.exit_code_for_verdict(verdict), 0)

    def test_accepts_receipt_embedded_in_handback_payload(self):
        hb = self.make_handback(receipt=self.make_envelope())
        verdict = vh.verify_handback(
            hb,
            repo=self.repo,
            required=REQUIRED_PARTS,
            hmac_secret=_dummy_hmac_secret(),
        )
        self.assertIs(verdict["ok"], True)
        self.assertEqual(verdict["verdict"], "ACCEPT")

    def test_accepts_none_lane_when_omitted(self):
        hb = self.make_handback()
        hb.pop("lane")
        verdict = self.verify(handback=hb)
        self.assertIs(verdict["ok"], True)
        self.assertIsNone(verdict["lane"])


class TestCheck1Receipt(HandbackFixtureBase):
    """Check 1: signed receipt verification via verify_receipt.verify()."""

    def test_rejects_missing_receipt(self):
        verdict = self.verify(receipt_envelope=None)
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["receipt"]["ok"], False)
        self.assertTrue(any("receipt" in r.lower() for r in verdict["reasons"]))

    def test_rejects_bad_hmac_signature(self):
        forged = self.make_envelope(secret=_dummy_hmac_secret() + "-wrong")
        verdict = self.verify(receipt_envelope=forged)
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["receipt"]["ok"], False)
        self.assertTrue(any("signature" in r for r in verdict["reasons"]))

    def test_rejects_tree_hash_mismatch(self):
        bad_tree = self.make_envelope(tree_hash="0" * 40)
        verdict = self.verify(receipt_envelope=bad_tree)
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["receipt"]["ok"], False)
        self.assertTrue(any("tree_hash" in r for r in verdict["reasons"]))

    def test_rejects_missing_or_failing_required_part(self):
        failing_parts = _good_parts()
        failing_parts[1]["exit_code"] = 1
        verdict = self.verify(receipt_envelope=self.make_envelope(parts=failing_parts))
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["receipt"]["ok"], False)

    def test_rejects_stale_receipt_exceeding_max_behind(self):
        env = self.make_envelope()
        _git(self.repo, "checkout", "-q", "main")
        for idx in range(3):
            _commit_files(self.repo, {f"docs/m_{idx}.md": f"{idx}\n"}, f"advance {idx}")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.repo, "checkout", "-q", "feat/lane-work")

        stale = self.verify(receipt_envelope=env, max_behind=2)
        self.assertEqual(stale["verdict"], "REJECT")
        self.assertIs(stale["checks"]["receipt"]["ok"], False)

        fresh_enough = self.verify(receipt_envelope=env, max_behind=3)
        self.assertEqual(fresh_enough["verdict"], "ACCEPT")

    def test_rejects_when_handback_head_sha_disagrees_with_checkout(self):
        hb = self.make_handback(head_sha=self.base_sha)
        verdict = self.verify(handback=hb)
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")

    def test_allow_no_required_parts_flag_controls_empty_required(self):
        denied = self.verify(required=[], allow_no_required=False)
        self.assertEqual(denied["verdict"], "ERROR")
        self.assertIs(denied["ok"], False)

        allowed = self.verify(required=[], allow_no_required=True)
        self.assertEqual(allowed["verdict"], "ACCEPT")
        self.assertIs(allowed["ok"], True)


class TestCheck2WorktreeClean(HandbackFixtureBase):
    """Check 2: git status --porcelain --untracked-files=all cleanliness."""

    def test_rejects_nested_untracked_file(self):
        nested = self.repo / "tools" / "sub" / "untracked.tmp"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text("stray\n", encoding="utf-8")

        verdict = self.verify()
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["worktree_clean"]["ok"], False)
        self.assertIn("tools/sub/untracked.tmp", verdict["checks"]["worktree_clean"]["dirty_paths"])

    def test_rejects_unstaged_tracked_modification(self):
        (self.repo / "tools" / "new_gate.py").write_text("OK = False\n", encoding="utf-8")
        verdict = self.verify()
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["worktree_clean"]["ok"], False)

    def test_rejects_staged_uncommitted_change(self):
        (self.repo / "tools" / "staged_only.py").write_text("X = 1\n", encoding="utf-8")
        _git(self.repo, "add", "tools/staged_only.py")
        verdict = self.verify()
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["worktree_clean"]["ok"], False)


class TestCheck3Scope(HandbackFixtureBase):
    """Check 3: committed diff paths against non-empty declared scope globs."""

    def test_rejects_empty_scope_even_when_diff_is_empty(self):
        _git(self.repo, "checkout", "-q", "main")
        env_on_main = self.make_envelope()
        hb = self.make_handback(scope=[])
        verdict = self.verify(handback=hb, receipt_envelope=env_on_main)
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["scope"]["ok"], False)

    def test_rejects_missing_scope(self):
        hb = self.make_handback()
        hb.pop("scope")
        verdict = self.verify(handback=hb)
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["scope"]["ok"], False)

    def test_rejects_out_of_scope_diff_path(self):
        _commit_files(self.repo, {"outside/locked.txt": "tampered\n"}, "touch outside scope")
        verdict = self.verify(receipt_envelope=self.make_envelope())
        self.assertIs(verdict["ok"], False)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["scope"]["ok"], False)
        self.assertIn("outside/locked.txt", verdict["checks"]["scope"]["out_of_scope"])

    def test_single_star_does_not_cross_directory_boundary_while_double_star_does(self):
        _commit_files(self.repo, {"tools/nested/helper.py": "H = 1\n"}, "nested tool file")
        env = self.make_envelope()

        narrow = self.verify(
            handback=self.make_handback(scope=["tools/*.py", "tests/test_*.py"]),
            receipt_envelope=env,
        )
        self.assertEqual(narrow["verdict"], "REJECT")
        self.assertIn("tools/nested/helper.py", narrow["checks"]["scope"]["out_of_scope"])

        recursive = self.verify(
            handback=self.make_handback(scope=["tools/**/*.py", "tests/test_*.py"]),
            receipt_envelope=env,
        )
        self.assertEqual(recursive["verdict"], "ACCEPT")

        subtree = self.verify(
            handback=self.make_handback(scope=["tools/**", "tests/**"]),
            receipt_envelope=env,
        )
        self.assertEqual(subtree["verdict"], "ACCEPT")

    def test_normalizes_backslash_scope_globs(self):
        verdict = self.verify(
            handback=self.make_handback(scope=["tools\\*.py", "tests\\test_*.py"]),
        )
        self.assertEqual(verdict["verdict"], "ACCEPT")

    def test_scope_kwarg_overrides_handback_scope_and_empty_override_fails_closed(self):
        narrow_override = self.verify(scope=["tools/*.py"])
        self.assertEqual(narrow_override["verdict"], "REJECT")
        self.assertIn("tests/test_new_gate.py", narrow_override["checks"]["scope"]["out_of_scope"])

        empty_override = self.verify(scope=[])
        self.assertEqual(empty_override["verdict"], "REJECT")
        self.assertIs(empty_override["checks"]["scope"]["ok"], False)

    def test_rejects_rename_from_outside_scope_into_scope(self):
        _git(self.repo, "mv", "outside/locked.txt", "tools/smuggled.py")
        _git(self.repo, "commit", "-q", "-m", "rename outside file into tools")
        verdict = self.verify(receipt_envelope=self.make_envelope())
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIn("outside/locked.txt", verdict["checks"]["scope"]["out_of_scope"])


class TestCheck4RecordedTests(HandbackFixtureBase):
    """Check 4: recorded test_run command and strict int exit_code == 0."""

    def test_rejects_missing_test_run(self):
        hb = self.make_handback()
        hb.pop("test_run")
        verdict = self.verify(handback=hb)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertIs(verdict["checks"]["tests"]["ok"], False)

    def test_rejects_empty_or_whitespace_command(self):
        for bad_cmd in ("", "   ", None, 123):
            hb = self.make_handback(test_run={"command": bad_cmd, "exit_code": 0})
            verdict = self.verify(handback=hb)
            self.assertEqual(verdict["verdict"], "REJECT", f"command={bad_cmd!r} must reject")
            self.assertIs(verdict["checks"]["tests"]["ok"], False)

    def test_rejects_boolean_exit_codes_false_and_true(self):
        for bad_code in (False, True):
            hb = self.make_handback(
                test_run={"command": "python -m unittest", "exit_code": bad_code}
            )
            verdict = self.verify(handback=hb)
            self.assertEqual(verdict["verdict"], "REJECT", f"exit_code={bad_code!r} must reject")
            self.assertIs(verdict["checks"]["tests"]["ok"], False)

    def test_rejects_nonzero_or_non_int_exit_codes(self):
        for bad_code in (1, 2, -1, "0", None, 0.0):
            hb = self.make_handback(
                test_run={"command": "python -m unittest", "exit_code": bad_code}
            )
            verdict = self.verify(handback=hb)
            self.assertEqual(verdict["verdict"], "REJECT", f"exit_code={bad_code!r} must reject")
            self.assertIs(verdict["checks"]["tests"]["ok"], False)


class TestFailClosedErrors(HandbackFixtureBase):
    """Exit-2 ERROR conditions: malformed inputs, unreadable repos, and git errors."""

    def test_non_dict_handback_returns_error(self):
        for bad_input in (None, [], "string", 42):
            verdict = self.verify(handback=bad_input)
            self.assertEqual(verdict["verdict"], "ERROR")
            self.assertIs(verdict["ok"], False)
            self.assertEqual(vh.exit_code_for_verdict(verdict), 2)

    def test_missing_or_non_git_repo_returns_error(self):
        absent = self.tmp_path / "does_not_exist"
        verdict_absent = self.verify(repo=absent)
        self.assertEqual(verdict_absent["verdict"], "ERROR")
        self.assertIs(verdict_absent["ok"], False)

        plain_dir = self.tmp_path / "plain_dir"
        plain_dir.mkdir()
        verdict_plain = self.verify(repo=plain_dir)
        self.assertEqual(verdict_plain["verdict"], "ERROR")
        self.assertIs(verdict_plain["ok"], False)

    def test_unresolvable_base_ref_or_main_ref_returns_error(self):
        bad_base = self.verify(base_ref="origin/no-such-branch")
        self.assertEqual(bad_base["verdict"], "ERROR")
        self.assertIs(bad_base["ok"], False)

        bad_main = self.verify(main_ref="origin/no-such-branch")
        self.assertEqual(bad_main["verdict"], "ERROR")
        self.assertIs(bad_main["ok"], False)

    def test_missing_key_material_and_malformed_envelope_return_error(self):
        no_key = self.verify(hmac_secret=None)
        self.assertEqual(no_key["verdict"], "ERROR")
        self.assertIs(no_key["ok"], False)

        bad_env = self.verify(receipt_envelope={"not_a_receipt": 1})
        self.assertEqual(bad_env["verdict"], "ERROR")
        self.assertIs(bad_env["ok"], False)


class TestCli(HandbackFixtureBase):
    """CLI end-to-end tests via both main() and subprocess invocation."""

    def _write_json(self, name: str, data: dict) -> Path:
        path = self.tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_cli_accept_reject_and_error_via_subprocess(self):
        hb_path = self._write_json("handback.json", self.make_handback())
        rc_path = self._write_json("receipt.json", self.make_envelope())
        script = TOOLS_DIR / "verify_handback.py"
        env = dict(os.environ)
        env[rc.HMAC_ENV] = _dummy_hmac_secret()

        accept_proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--handback",
                str(hb_path),
                "--receipt",
                str(rc_path),
                "--repo",
                str(self.repo),
                "--required",
                ",".join(REQUIRED_PARTS),
                "--json",
            ],
            cwd=str(self.tmp_path),
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(accept_proc.returncode, 0, accept_proc.stderr)
        accept_payload = json.loads(accept_proc.stdout)
        self.assertEqual(accept_payload["verdict"], "ACCEPT")
        self.assertIs(accept_payload["ok"], True)

        reject_proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--handback",
                str(hb_path),
                "--receipt",
                str(rc_path),
                "--repo",
                str(self.repo),
                "--scope",
                "tools/*.py",
                "--required",
                ",".join(REQUIRED_PARTS),
                "--json",
            ],
            cwd=str(self.tmp_path),
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(reject_proc.returncode, 1, reject_proc.stderr)
        reject_payload = json.loads(reject_proc.stdout)
        self.assertEqual(reject_payload["verdict"], "REJECT")
        self.assertIs(reject_payload["ok"], False)

        bad_hb_path = self.tmp_path / "broken.json"
        bad_hb_path.write_text("{not valid json", encoding="utf-8")
        error_proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--handback",
                str(bad_hb_path),
                "--repo",
                str(self.repo),
                "--json",
            ],
            cwd=str(self.tmp_path),
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(error_proc.returncode, 2)
        error_payload = json.loads(error_proc.stdout)
        self.assertEqual(error_payload["verdict"], "ERROR")
        self.assertIs(error_payload["ok"], False)

    def test_cli_reads_handback_from_stdin(self):
        hb_with_receipt = self.make_handback(receipt=self.make_envelope())
        script = TOOLS_DIR / "verify_handback.py"
        env = dict(os.environ)
        env[rc.HMAC_ENV] = _dummy_hmac_secret()

        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--handback",
                "-",
                "--repo",
                str(self.repo),
                "--required",
                ",".join(REQUIRED_PARTS),
                "--json",
            ],
            input=json.dumps(hb_with_receipt),
            cwd=str(self.tmp_path),
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["verdict"], "ACCEPT")

    def test_main_missing_handback_argument_exits_2(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vh.main(["--repo", str(self.repo), "--json"], environ={})
        self.assertEqual(code, 2)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["verdict"], "ERROR")


if __name__ == "__main__":
    unittest.main()
