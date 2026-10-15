#!/usr/bin/env python3
"""verify-receipt names the cause when the missing receipt is for a server-authored merge.

A GitHub-authored update-branch merge head has two parents and a committer of
`GitHub <noreply@github.com>`. No box ran the gates on it, so a MISSING receipt for it
is not a lane bug to chase: the failure message must say so instead of a bare MISSING.
Each test drives the real verifier over a real temp git repo (or a fake gh api payload).
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

CAUSE = "server-authored update-branch merge; no box ran the gates; merge main locally and push"


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test_sm", TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vr = _load("verify_receipt")

_BASE_ENV = {
    "GIT_AUTHOR_NAME": "lane", "GIT_AUTHOR_EMAIL": "lane@x",
    "GIT_COMMITTER_NAME": "lane", "GIT_COMMITTER_EMAIL": "lane@x",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(cwd, *args, env_extra=None):
    env = dict(os.environ)
    env.update(_BASE_ENV)
    env["HOME"] = str(cwd)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["git", "-C", str(cwd)] + list(args), cwd=str(cwd), env=env,
                          capture_output=True, encoding="utf-8", errors="replace",
                          check=True).stdout.strip()


def _build_two_parent_commit(repo, committer_name, committer_email):
    """Two real parent commits, then a merge commit whose committer is the given identity."""
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "a")
    parent_a = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "b")
    parent_b = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-")
    tree = _git(repo, "rev-parse", parent_a + "^{tree}")
    return _git(repo, "commit-tree", tree, "-p", parent_a, "-p", parent_b, "-m", "merge",
                env_extra={"GIT_COMMITTER_NAME": committer_name,
                           "GIT_COMMITTER_EMAIL": committer_email})


class TestServerMergeCauseFromGit(unittest.TestCase):
    """CLI path (--receipt FILE absent, local --head): reads parents and committer from git."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")

    def tearDown(self):
        self._tmp.cleanup()

    def _run_missing_receipt(self, head):
        out = io.StringIO()
        absent = Path(self._tmp.name) / "no-such-receipt.json"
        with contextlib.redirect_stdout(out):
            code = vr.main(["--receipt", str(absent), "--repo", str(self.repo), "--head", head])
        return code, out.getvalue()

    def test_server_authored_two_parent_merge_names_the_cause(self):
        head = _build_two_parent_commit(self.repo, "GitHub", "noreply@github.com")
        code, text = self._run_missing_receipt(head)
        self.assertEqual(code, 1, text)
        self.assertIn("MISSING receipt", text)
        self.assertIn(CAUSE, text)

    def test_human_two_parent_merge_is_a_bare_missing_without_the_cause(self):
        head = _build_two_parent_commit(self.repo, "Matt Culliton", "matt82198@gmail.com")
        code, text = self._run_missing_receipt(head)
        self.assertEqual(code, 1, text)
        self.assertIn("MISSING receipt", text)
        self.assertNotIn("server-authored", text)


class TestServerMergeCauseFetchCli(unittest.TestCase):
    """--fetch-for-head (the workflow's lookup step): no receipt anywhere, so the MISSING
    line must name the cause for a server-authored head. The commit is read from the local
    fixture repo; the GitHub lookup is stubbed so no network is touched."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        self.out_found = Path(self._tmp.name) / "found"
        self.out_env = Path(self._tmp.name) / "env.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _fetch(self, sha, api_result=None, raises=False):
        def fake_api(path):
            if raises:
                raise RuntimeError("simulated gh outage")
            return api_result

        args = ["--fetch-for-head", sha, "--repo-slug", "matt82198/aesop", "--repo", str(self.repo),
                "--fetch-window-s", "0", "--out-found", str(self.out_found),
                "--out-envelope", str(self.out_env)]
        orig = vr._gh_api
        vr._gh_api = fake_api
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = vr.main(args, environ={})
        finally:
            vr._gh_api = orig
        return code, out.getvalue()

    def test_server_authored_head_names_the_cause_and_exits_1(self):
        head = _build_two_parent_commit(self.repo, "GitHub", "noreply@github.com")
        code, text = self._fetch(head, api_result={"check_runs": []})
        self.assertEqual(code, 1, text)
        self.assertIn("MISSING receipt", text)
        self.assertIn(CAUSE, text)
        self.assertEqual(self.out_found.read_text(encoding="utf-8"), "false")

    def test_server_authored_head_still_named_when_github_lookup_raises(self):
        head = _build_two_parent_commit(self.repo, "GitHub", "noreply@github.com")
        code, text = self._fetch(head, raises=True)
        self.assertEqual(code, 1, text)
        self.assertIn(CAUSE, text)

    def test_human_merge_head_stays_a_bare_missing(self):
        head = _build_two_parent_commit(self.repo, "Matt Culliton", "matt82198@gmail.com")
        code, text = self._fetch(head, api_result={"check_runs": []})
        self.assertEqual(code, 1, text)
        self.assertIn("MISSING receipt", text)
        self.assertNotIn("server-authored", text)

    def test_unknown_sha_names_no_cause_and_does_not_crash(self):
        _build_two_parent_commit(self.repo, "GitHub", "noreply@github.com")
        code, text = self._fetch("3" * 40, api_result={"check_runs": []})
        self.assertEqual(code, 1, text)
        self.assertIn("MISSING receipt", text)
        self.assertNotIn("server-authored", text)


if __name__ == "__main__":
    unittest.main()
