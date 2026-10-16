#!/usr/bin/env python3
"""Receipt matrix hardening (tools/emit_receipt.py), two fail-open paths closed:

(F3) The receipt signing secrets (AESOP_RECEIPT_KEY path, AESOP_RECEIPT_HMAC_SECRET) must never
reach a matrix part's environment. A part runs the pushed head's test code; if the HMAC secret
is inherited, that code can forge an accepted receipt. The parent signing call keeps them.

(F4) In the scoped profile, when the changed-file set cannot be derived (merge-base or diff
fails), the emitter must run the FULL matrix, never an empty scope that passes with zero
Python tests executed. A derivable diff keeps the scoped selection.

Secrets are runtime-assembled dummy values; no real key material is read or printed.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import ci_shard_runner  # noqa: E402
import emit_receipt  # noqa: E402

_PY_SHARDS = ("py-shard-0", "py-shard-1", "py-shard-2", "py-shard-3")
_SCOPE_TESTS = ("test_alpha", "test_beta", "test_gamma", "test_delta", "test_epsilon")
_OK_CMD = [sys.executable, "-c", "print('Ran 2 tests in 0.00s')"]
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1",
}


def _dummy(tag):
    """Runtime-assembled dummy secret value: never a literal key or secret in source."""
    return "dummy-" + tag + "-" + "value-" + "0042"


def _write_add(repo, rel, text):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(repo, "add", rel)


def _git(cwd, *args):
    env = dict(os.environ)
    env.update(_GIT_ENV)
    res = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True,
                         encoding="utf-8", errors="replace", env=env)
    if res.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), res.stderr))
    return res.stdout.strip()


def _all_runnable_registry():
    """The real DEFAULT_REGISTRY with every runnable part replaced by a fast OK command.
    Keeps the real names and skip reasons, so DEFAULT_MATRIX resolves without running real gates."""
    reg = {}
    for name, spec in emit_receipt.DEFAULT_REGISTRY.items():
        if spec.cmd is None:
            reg[name] = spec
        else:
            reg[name] = emit_receipt.PartSpec(lambda repo: _OK_CMD)
    return reg


class _ProbeSpec:
    def __init__(self, script):
        self._script = script

    def cmd(self, repo):
        return [sys.executable, "-c", self._script]


class TestPartEnvExcludesSigningSecrets(unittest.TestCase):
    """F3: run_part gives every matrix part an environment without the signing material."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="scrub-secret-repo-"))
        self.addCleanup(shutil.rmtree, str(self.repo), True)

    @staticmethod
    def _parent_env(var, value):
        """The parent process environment (as the push would hand it over), with one dummy
        signing variable added. Passed explicitly to run_part; the real os.environ is untouched."""
        env = dict(os.environ)
        env[var] = value
        return env

    def _probe(self, var):
        # Exit 0 when the variable is absent; 9 when present. The value is never printed.
        return ("import os, sys\n"
                "sys.exit(0 if %r not in os.environ else 9)\n") % var

    def test_part_env_has_no_hmac_secret(self):
        var = "AESOP_RECEIPT_HMAC_SECRET"
        res = emit_receipt.run_part(self.repo, "probe", _ProbeSpec(self._probe(var)),
                                    environ=self._parent_env(var, _dummy("hmac")))
        self.assertEqual(res["exit_code"], 0, "HMAC signing secret reached the matrix part environment")

    def test_part_env_has_no_key_path(self):
        var = "AESOP_RECEIPT_KEY"
        res = emit_receipt.run_part(self.repo, "probe", _ProbeSpec(self._probe(var)),
                                    environ=self._parent_env(var, _dummy("keypath")))
        self.assertEqual(res["exit_code"], 0, "signing key path reached the matrix part environment")

    def test_scrub_child_env_removes_both_and_reports_names_only(self):
        secret = _dummy("hmac-scrub")
        env = {"AESOP_RECEIPT_HMAC_SECRET": secret, "AESOP_RECEIPT_KEY": _dummy("k"), "PATH": "/bin"}
        child_env, removed = emit_receipt.scrub_child_env(env)
        self.assertNotIn("AESOP_RECEIPT_HMAC_SECRET", child_env)
        self.assertNotIn("AESOP_RECEIPT_KEY", child_env)
        self.assertIn("AESOP_RECEIPT_HMAC_SECRET", removed)
        self.assertIn("AESOP_RECEIPT_KEY", removed)
        self.assertNotIn(secret, repr(removed))
        self.assertEqual(child_env.get("PATH"), "/bin")


class TestScopedFallbackFailsClosed(unittest.TestCase):
    """F4: an underivable changed-file set selects the full matrix, never an empty scope."""

    def _run_main(self, repo, extra):
        """repo=None runs main with no --repo flag (the cwd is the repo path under test)."""
        out_dir = Path(tempfile.mkdtemp(prefix="scope-out-"))
        self.addCleanup(shutil.rmtree, str(out_dir), True)
        out_path = out_dir / "env.json"
        repo_args = [] if repo is None else ["--repo", str(repo)]
        argv = repo_args + ["--scheme", "hmac-sha256", "--dry-run", "--out", str(out_path)] + extra
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = emit_receipt.main(argv, registry=_all_runnable_registry(), gh_runner=None,
                                     environ={"AESOP_RECEIPT_HMAC_SECRET": _dummy("scope")})
        env_json = json.loads(out_path.read_text(encoding="utf-8"))
        return code, env_json["receipt"], buf.getvalue()

    def _mk_repo(self):
        tmp = tempfile.mkdtemp(prefix="scope-fallback-")
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "base")
        _git(repo, "checkout", "-q", "-b", "feat")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_emit_receipt.py").write_text("# touched\n", encoding="utf-8")
        _git(repo, "add", "tests/test_emit_receipt.py")
        _git(repo, "commit", "-q", "-m", "feat")
        return repo

    def test_underivable_diff_runs_full_matrix(self):
        repo = self._mk_repo()
        real_git = emit_receipt._git

        def diff_fails(repo_arg, *args):
            # Fake only the scoped-profile diff failure; merge-base and every other git call stay real.
            if args and args[0] == "diff":
                raise emit_receipt.ReceiptError("git diff failed: simulated underivable change set")
            return real_git(repo_arg, *args)

        with unittest.mock.patch.object(emit_receipt, "_git", side_effect=diff_fails):
            code, receipt, stdout = self._run_main(repo, ["--main-ref", "refs/heads/main"])
        self.assertEqual(code, 0)
        ran = {p["name"] for p in receipt["parts"]}
        for shard in ("py-shard-0", "py-shard-1", "py-shard-2", "py-shard-3"):
            self.assertIn(shard, ran, "underivable diff must run %s (fail closed)" % shard)
        self.assertNotIn("py-shard-0", {s["name"] for s in receipt["skipped"]})
        self.assertIn("full matrix", stdout, "the fallback must log one line saying why")

    def _mk_scoped_repo(self, changed, timing=None, subdir=None):
        """Base commit: five test files, tools/receipt_common.py, optional .github/shard-timing.json.
        The feature branch changes only `changed`. `subdir` is created (untracked) for cwd tests."""
        tmp = tempfile.mkdtemp(prefix="scope-shard-")
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        for stem in _SCOPE_TESTS:
            _write_add(repo, "tests/%s.py" % stem, "# base\n")
        _write_add(repo, "tools/receipt_common.py", "# base\n")
        if timing is not None:
            _write_add(repo, ".github/shard-timing.json", json.dumps(timing))
        _git(repo, "commit", "-q", "-m", "base")
        _git(repo, "checkout", "-q", "-b", "feat")
        _write_add(repo, changed, "# touched\n")
        _git(repo, "commit", "-q", "-m", "feat")
        if subdir is not None:
            (repo / subdir).mkdir(parents=True, exist_ok=True)
        return repo

    def test_tools_change_with_no_owning_shard_runs_full_matrix(self):
        """Defect 1: a tools/ change that no shard owns must select the FULL set, never zero shards."""
        repo = self._mk_scoped_repo(changed="tools/receipt_common.py")
        code, receipt, _stdout = self._run_main(repo, ["--main-ref", "refs/heads/main"])
        self.assertEqual(code, 0)
        ran = {p["name"] for p in receipt["parts"]}
        for shard in _PY_SHARDS:
            self.assertIn(shard, ran, "tools/receipt_common.py has no owning shard: %s must run" % shard)

    def test_subdirectory_run_selects_the_same_scope_as_repo_root(self):
        """Defect 2: discovery is repo-root anchored. From a subdirectory the scope must match the root run."""
        repo = self._mk_scoped_repo(changed="tests/test_alpha.py", subdir="docs")
        sub = repo / "docs"
        self.assertEqual(emit_receipt._get_test_files(sub), emit_receipt._get_test_files(repo))
        self.assertTrue(emit_receipt._get_test_files(sub), "test discovery from a subdirectory must not be empty")
        _code, root_receipt, _ = self._run_main(repo, ["--main-ref", "refs/heads/main"])
        root_ran = {p["name"] for p in root_receipt["parts"] if p["name"] in _PY_SHARDS}
        old_cwd = os.getcwd()
        os.chdir(str(sub))
        self.addCleanup(os.chdir, old_cwd)
        code, receipt, _stdout = self._run_main(None, ["--main-ref", "refs/heads/main"])
        self.assertEqual(code, 0)
        sub_ran = {p["name"] for p in receipt["parts"] if p["name"] in _PY_SHARDS}
        self.assertGreaterEqual(len(sub_ran), 1, "a subdirectory run must still select the owning shard")
        self.assertEqual(sub_ran, root_ran)

    def test_registry_shard_argv_executes_the_map_assignment(self):
        """Defect 3: the argv the registry runs must load the same timing file the selection map uses."""
        timing = {"tests/test_alpha.py": 100.0}
        repo = self._mk_scoped_repo(changed="tests/test_beta.py", timing=timing)
        shard_map = emit_receipt._build_shard_map(sorted(_PY_SHARDS), timing=emit_receipt._load_ci_timing(repo),
                                                  repo=repo)
        self.assertIsNotNone(shard_map)
        argv = emit_receipt.DEFAULT_REGISTRY["py-shard-0"].cmd(repo)
        _shard_id, _total, timing_file, _emit = ci_shard_runner._parse_args(argv[2:])
        self.assertIsNotNone(timing_file, "registry shard argv must pass the CI timing file (round-robin otherwise)")
        runner_timing = ci_shard_runner.load_timing_data(str(repo / timing_file))
        self.assertTrue(runner_timing)
        runner_buckets = ci_shard_runner.distribute_shards_by_timing(sorted(_SCOPE_TESTS), len(_PY_SHARDS),
                                                                     runner_timing)
        for idx, bucket in enumerate(runner_buckets):
            for stem in bucket:
                self.assertEqual(shard_map[stem], _PY_SHARDS[idx],
                                 "%s: map says %s, runner executes it in %s" % (stem, shard_map[stem], _PY_SHARDS[idx]))

    def test_derivable_diff_keeps_scoped_selection(self):
        """Defect 4: out-of-scope shards are recorded as skipped (not in scope); the partition is exact."""
        repo = self._mk_scoped_repo(changed="tests/test_alpha.py")
        code, receipt, _stdout = self._run_main(repo, ["--main-ref", "refs/heads/main"])
        self.assertEqual(code, 0)
        ran_shards = {p["name"] for p in receipt["parts"] if p["name"] in _PY_SHARDS}
        skipped_shards = {s["name"]: s["reason"] for s in receipt["skipped"] if s["name"] in _PY_SHARDS}
        self.assertEqual(len(ran_shards), 1, "one changed test file owns exactly one shard")
        self.assertEqual(set(ran_shards) | set(skipped_shards), set(_PY_SHARDS))
        self.assertFalse(set(ran_shards) & set(skipped_shards))
        self.assertEqual(len(skipped_shards), 3)
        for name, reason in skipped_shards.items():
            self.assertIn("not in scope", reason, name)


if __name__ == "__main__":
    unittest.main()
