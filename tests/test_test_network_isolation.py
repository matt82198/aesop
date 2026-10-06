#!/usr/bin/env python3
"""Behavioral proof for tools/test_network_isolation.py.

Incident (2026-10-05): tests/test_merge_train_halt_enforcement.py and
tests/test_merge_queue_halt_enforcement.py ran tools/merge_train.py /
tools/merge_queue.py as REAL subprocesses with `env = os.environ.copy()` --
no isolation at all -- on the assumption that once halt was cleared, the
real `gh`/`git` calls would harmlessly time out with no auth/network. On a
box with live `gh` auth, they did not: six `integrate/batch-20261005-15xx`
branches were pushed to the real origin and a worktree was repurposed.

Per-test mocking cannot be the only guard -- a test author can always forget
it, exactly as happened here. tools/test_network_isolation.py makes the
whole Python test PROCESS structurally incapable of reaching the real origin
or GitHub API, regardless of what any individual test does or forgets.

Every test here proves the mechanism BEHAVIORALLY (a real `git push`/`git
fetch`/`gh` invocation under the harness env), never by reading the module's
source and asserting on strings.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import test_network_isolation as isolation  # noqa: E402


def _git(args, cwd, env=None, check=True):
    result = subprocess.run(
        ["git"] + args, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=30,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stdout}\n{result.stderr}")
    return result


def _make_fixture_repo(root: Path, origin_url: str) -> Path:
    """A real local repo with one commit and a remote named 'origin' pointed at
    `origin_url` (never a real GitHub URL -- that is the whole point of these
    tests, same as every other fixture in tests/CLAUDE.md)."""
    repo = root / "fixture-repo"
    repo.mkdir()
    _git(["init", "-q"], cwd=repo)
    _git(["config", "user.email", "t@example.invalid"], cwd=repo)
    _git(["config", "user.name", "T"], cwd=repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-q", "-m", "init"], cwd=repo)
    _git(["remote", "add", "origin", origin_url], cwd=repo)
    return repo


class TestBuildIsolationEnv(unittest.TestCase):
    """Pure-function proof: the env dict the harness builds has the exact shape
    the task mandates, with no process-level side effects."""

    def test_git_config_entries_rewrite_both_github_url_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink_root = Path(tmp) / "sink"
            env = isolation.build_isolation_env(sink_root)

            count = int(env["GIT_CONFIG_COUNT"])
            self.assertGreaterEqual(count, 2)
            # GIT_CONFIG_KEY_n is a repeated git config KEY (url.<sink>.insteadOf) with
            # a DIFFERENT VALUE_n per prefix -- both entries legitimately share the same
            # key text, so this must be checked as pairs, never collapsed into a dict.
            pairs = [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"]) for i in range(count)]
            values = [v for _, v in pairs]
            self.assertIn("https://github.com/", values)
            self.assertIn("git@github.com:", values)
            for key, _ in pairs:
                self.assertTrue(key.endswith(".insteadOf"), key)

    def test_sets_terminal_prompt_and_no_network_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = isolation.build_isolation_env(Path(tmp) / "sink")
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(env["AESOP_TEST_NO_NETWORK"], "1")

    def test_sets_gh_blocking_vars(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = isolation.build_isolation_env(Path(tmp) / "sink")
            self.assertEqual(env["GH_TOKEN"], "")
            self.assertEqual(env["GH_HOST"], "localhost.invalid")
            self.assertIn("GH_CONFIG_DIR", env)
            # Must never point at the developer's real gh config.
            real_gh_config = Path(os.path.expanduser("~")) / ".config" / "gh"
            self.assertNotEqual(Path(env["GH_CONFIG_DIR"]).resolve(), real_gh_config.resolve())


class TestRealPushIsRedirected(unittest.TestCase):
    """The literal incident, reproduced and proven blocked: a real `git push
    origin ...` must never reach a real remote -- it must land in the
    throwaway local bare sink instead."""

    def test_push_to_github_style_origin_lands_in_bare_sink_not_real_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fixture = _make_fixture_repo(tmp, "https://github.com/fixture-owner/fixture-repo.git")

            sink_root = tmp / "sink-root"
            suffix = "fixture-owner/fixture-repo.git"
            sink_path = isolation.make_bare_sink(sink_root, suffix)
            self.assertTrue(sink_path.exists())

            env = os.environ.copy()
            env.update(isolation.build_isolation_env(sink_root))

            # The exact dangerous call from the incident: push a branch to "origin".
            push = subprocess.run(
                ["git", "push", "origin", "HEAD:refs/heads/probe-branch"],
                cwd=str(fixture), env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(push.returncode, 0, f"stdout={push.stdout}\nstderr={push.stderr}")

            # Proof positive: the branch landed in the SINK.
            ls_sink = subprocess.run(
                ["git", "ls-remote", "--heads", str(sink_path)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertIn("refs/heads/probe-branch", ls_sink.stdout)

    def test_fetch_from_github_style_origin_is_redirected_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fixture = _make_fixture_repo(tmp, "git@github.com:fixture-owner/fixture-repo.git")

            sink_root = tmp / "sink-root"
            suffix = "fixture-owner/fixture-repo.git"
            sink_path = isolation.make_bare_sink(sink_root, suffix)

            env = os.environ.copy()
            env.update(isolation.build_isolation_env(sink_root))

            # Push a ref into the sink directly so fetch has something to find,
            # proving the SSH-style URL form is rewritten too.
            subprocess.run(
                ["git", "push", "origin", "HEAD:refs/heads/seed"],
                cwd=str(fixture), env=env, capture_output=True, text=True, timeout=30, check=True,
            )
            # `git push` auto-updates the local remote-tracking ref for a branch it just
            # pushed, so fetch would report "nothing new" even if it were silently
            # talking to a different remote. Drop the tracking ref first so a
            # subsequent fetch can only repopulate it by actually reaching the sink.
            subprocess.run(
                ["git", "update-ref", "-d", "refs/remotes/origin/seed"],
                cwd=str(fixture), env=env, capture_output=True, text=True, timeout=30, check=True,
            )
            fetch = subprocess.run(
                ["git", "fetch", "origin"],
                cwd=str(fixture), env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(fetch.returncode, 0, f"stdout={fetch.stdout}\nstderr={fetch.stderr}")
            rev = subprocess.run(
                ["git", "rev-parse", "--verify", "refs/remotes/origin/seed"],
                cwd=str(fixture), env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(rev.returncode, 0, "fetch must have repopulated origin/seed from the sink")


class TestGhIsBlocked(unittest.TestCase):
    @unittest.skipUnless(shutil.which("gh"), "gh CLI not installed on this runner")
    def test_real_gh_cli_cannot_reach_github_under_isolation_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink_root = Path(tmp) / "sink"
            env = os.environ.copy()
            env.update(isolation.build_isolation_env(sink_root))

            result = subprocess.run(
                ["gh", "pr", "view", "1", "--repo", "matt82198/aesop"],
                env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            # Must never have produced a real PR body.
            self.assertNotIn("matt82198", result.stdout)


class TestApplyIsIdempotentPerProcess(unittest.TestCase):
    def setUp(self):
        self._snapshot = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._snapshot)

    def test_second_call_is_a_noop_without_force(self):
        first = isolation.apply_test_isolation_env(force=True)
        self.assertTrue(first)
        second = isolation.apply_test_isolation_env()
        self.assertEqual(second, {})
        # The sink established by the first call must still be the active one.
        self.assertEqual(os.environ.get("GIT_CONFIG_COUNT"), first["GIT_CONFIG_COUNT"])

    def test_applies_to_current_process_env(self):
        isolation.apply_test_isolation_env(force=True)
        self.assertEqual(os.environ.get("GIT_TERMINAL_PROMPT"), "0")
        self.assertEqual(os.environ.get("GH_HOST"), "localhost.invalid")
        self.assertIn("GIT_CONFIG_COUNT", os.environ)


if __name__ == "__main__":
    unittest.main()
