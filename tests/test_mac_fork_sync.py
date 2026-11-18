"""Tests for tools/mac_fork_sync.py -- fail-closed macOS fork sync controller.

Uses isolated tempfile.TemporaryDirectory() bare + clone git repos with
GIT_DIR / GIT_WORK_TREE scrubbed so tests never touch the parent repo.

Run: python -m unittest tests.test_mac_fork_sync -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = REPO_ROOT / "tools" / "mac_fork_sync.py"
TOOLS_DIR = str(REPO_ROOT / "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import mac_fork_sync  # noqa: E402
from test_network_isolation import apply_test_isolation_env  # noqa: E402

apply_test_isolation_env()

_SCRUB_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
)


def _clean_git_env():
    """Return os.environ copy with GIT_DIR/GIT_WORK_TREE scrubbed."""
    env = dict(os.environ)
    for key in _SCRUB_GIT_VARS:
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_AUTHOR_NAME"] = "Test Runner"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test Runner"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return env


def _git(cwd, *args):
    """Run git inside cwd with scrubbed env and explicit encoding/timeout."""
    res = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=_clean_git_env(),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    if res.returncode != 0:
        raise AssertionError(
            f"git {args} in {cwd} failed (rc={res.returncode}):\n"
            f"stdout={res.stdout}\nstderr={res.stderr}"
        )
    return res.stdout


def _run_tool(repo, *args):
    """Run tools/mac_fork_sync.py against repo and return (rc, json_or_dict, raw_out, raw_err)."""
    res = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--repo", str(repo), *args],
        cwd=str(repo),
        env=_clean_git_env(),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    parsed = {}
    if res.stdout.strip():
        try:
            parsed = json.loads(res.stdout)
        except ValueError:
            parsed = {}
    return res.returncode, parsed, res.stdout, res.stderr


def _write_file(base, rel_path, content):
    target = Path(base) / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class MacForkSyncTests(unittest.TestCase):
    """Behavioral tests for tools/mac_fork_sync.py."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.env = _clean_git_env()

        self.origin_bare = self.root / "origin.git"
        self.fork_bare = self.root / "mac-fork.git"
        self.upstream_wt = self.root / "upstream-wt"
        self.mac_wt = self.root / "mac-wt"

        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(self.origin_bare)],
            cwd=str(self.root),
            env=self.env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        )
        subprocess.run(
            ["git", "init", "--bare", "-b", "port/macos-sweep", str(self.fork_bare)],
            cwd=str(self.root),
            env=self.env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        )

        # Seed upstream origin/main
        subprocess.run(
            ["git", "clone", str(self.origin_bare), str(self.upstream_wt)],
            cwd=str(self.root),
            env=self.env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        )
        _git(self.upstream_wt, "checkout", "-B", "main")
        _write_file(
            self.upstream_wt,
            ".github/workflows/ci.yml",
            "name: CI\njobs:\n  ci:\n    runs-on: ubuntu-latest\n",
        )
        _write_file(self.upstream_wt, "hooks/pre-push-policy.sh", "#!/bin/bash\necho base\n")
        _write_file(self.upstream_wt, "daemons/run-watchdog.sh", "#!/bin/bash\necho base\n")
        _write_file(self.upstream_wt, "tools/shared.py", "VALUE = 1\n")
        _git(self.upstream_wt, "add", "-A")
        _git(self.upstream_wt, "commit", "-m", "initial upstream commit")
        _git(self.upstream_wt, "push", "origin", "main")

        # Clone into mac-wt, add mac-fork remote, and create Mac port commit
        subprocess.run(
            ["git", "clone", str(self.origin_bare), str(self.mac_wt)],
            cwd=str(self.root),
            env=self.env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        )
        _git(self.mac_wt, "remote", "add", "mac-fork", str(self.fork_bare))
        _git(self.mac_wt, "checkout", "-b", "port/macos-sweep")
        _write_file(
            self.mac_wt,
            ".github/workflows/ci.yml",
            "name: CI\njobs:\n  ci:\n    runs-on: macos-latest\n",
        )
        _write_file(self.mac_wt, "hooks/pre-push-policy.sh", "#!/bin/bash\necho mac-hook\n")
        _write_file(self.mac_wt, "daemons/run-watchdog.sh", "#!/bin/bash\necho mac-daemon\n")
        _git(self.mac_wt, "add", "-A")
        _git(self.mac_wt, "commit", "-m", "mac port base")
        _git(self.mac_wt, "push", "mac-fork", "HEAD:refs/heads/port/macos-sweep")

    def _push_upstream(self, files_dict, message):
        for rel, content in files_dict.items():
            _write_file(self.upstream_wt, rel, content)
        _git(self.upstream_wt, "add", "-A")
        _git(self.upstream_wt, "commit", "-m", message)
        _git(self.upstream_wt, "push", "origin", "main")
        return _git(self.upstream_wt, "rev-parse", "HEAD").strip()

    def test_status_json_reports_ahead_behind_dirty_branch_and_commits(self):
        sha1 = self._push_upstream({"tools/upstream_a.py": "A = 1\n"}, "feat: upstream alpha")
        sha2 = self._push_upstream({"tools/upstream_b.py": "B = 2\n"}, "feat: upstream beta")

        rc, data, stdout, stderr = _run_tool(self.mac_wt, "--status", "--json")
        self.assertEqual(rc, 0, msg=f"stderr={stderr}")
        self.assertEqual(data.get("branch"), "port/macos-sweep")
        self.assertEqual(data.get("ahead"), 1)
        self.assertEqual(data.get("behind"), 2)
        self.assertIs(data.get("dirty"), False)

        commits = data.get("upstream_commits")
        self.assertIsInstance(commits, list)
        self.assertEqual(len(commits), 2)
        shas = [c["sha"] for c in commits]
        subjects = [c["subject"] for c in commits]
        self.assertIn(sha1, shas)
        self.assertIn(sha2, shas)
        self.assertIn("feat: upstream alpha", subjects)
        self.assertIn("feat: upstream beta", subjects)

        # Also verify dirty detection
        _write_file(self.mac_wt, "untracked.txt", "dirty\n")
        rc_d, data_d, _, _ = _run_tool(self.mac_wt, "--status", "--json")
        self.assertEqual(rc_d, 0)
        self.assertIs(data_d.get("dirty"), True)

    def test_plan_json_clean_and_mac_owned_conflicts_without_modifying_tree(self):
        # Clean merge preview
        self._push_upstream({"tools/clean_feature.py": "X = 42\n"}, "feat: clean upstream change")
        head_before = _git(self.mac_wt, "rev-parse", "HEAD").strip()
        status_before = _git(self.mac_wt, "status", "--porcelain")

        rc, data, _, stderr = _run_tool(self.mac_wt, "--plan", "--json")
        self.assertEqual(rc, 0, msg=f"stderr={stderr}")
        self.assertIs(data.get("clean"), True)
        self.assertEqual(data.get("conflicts"), [])
        self.assertEqual(data.get("mac_owned_conflicts"), [])
        self.assertTrue(data.get("tree_sha"))
        self.assertEqual(_git(self.mac_wt, "rev-parse", "HEAD").strip(), head_before)
        self.assertEqual(_git(self.mac_wt, "status", "--porcelain"), status_before)

        # Conflicting upstream edits on Mac-port-owned paths
        self._push_upstream(
            {
                ".github/workflows/ci.yml": "name: CI\njobs:\n  ci:\n    runs-on: ubuntu-24.04\n",
                "hooks/pre-push-policy.sh": "#!/bin/bash\necho upstream-conflict\n",
                "daemons/run-watchdog.sh": "#!/bin/bash\necho upstream-daemon-conflict\n",
            },
            "chore: conflicting edits on owned paths",
        )
        rc_c, data_c, _, _ = _run_tool(self.mac_wt, "--plan", "--json")
        self.assertEqual(rc_c, 1)
        self.assertIs(data_c.get("clean"), False)
        self.assertIn(".github/workflows/ci.yml", data_c.get("conflicts", []))
        self.assertIn("hooks/pre-push-policy.sh", data_c.get("conflicts", []))
        self.assertIn("daemons/run-watchdog.sh", data_c.get("conflicts", []))
        self.assertEqual(
            sorted(data_c.get("mac_owned_conflicts", [])),
            [
                ".github/workflows/ci.yml",
                "daemons/run-watchdog.sh",
                "hooks/pre-push-policy.sh",
            ],
        )
        # Working tree must remain completely untouched
        self.assertEqual(_git(self.mac_wt, "rev-parse", "HEAD").strip(), head_before)
        self.assertEqual(_git(self.mac_wt, "status", "--porcelain"), status_before)

    def test_sync_refuses_dirty_tree_and_detached_head_with_exit_2(self):
        self._push_upstream({"tools/clean_feature.py": "X = 1\n"}, "feat: clean")

        # Uncommitted modification
        _write_file(self.mac_wt, "tools/shared.py", "VALUE = 999\n")
        head_before = _git(self.mac_wt, "rev-parse", "HEAD").strip()
        rc, _, _, _ = _run_tool(self.mac_wt, "--sync", "--json")
        self.assertEqual(rc, 2)
        self.assertEqual(_git(self.mac_wt, "rev-parse", "HEAD").strip(), head_before)

        # Restore clean tree, then detach HEAD
        _git(self.mac_wt, "checkout", "--", "tools/shared.py")
        _git(self.mac_wt, "checkout", "--detach", "HEAD")
        rc_det, _, _, _ = _run_tool(self.mac_wt, "--sync", "--json")
        self.assertEqual(rc_det, 2)

    def test_audit_and_sync_rollback_when_ci_reintroduces_ubuntu_latest(self):
        # Upstream reintroduces ubuntu-latest in a way that merges cleanly or via non-conflicting edit:
        # First align base on ci.yml by having mac_wt only touch hooks/, or having upstream add a sibling line
        # Actually set both sides to share macos-latest first, then upstream switches jobs.ci back to ubuntu-latest.
        _write_file(
            self.upstream_wt,
            ".github/workflows/ci.yml",
            "name: CI\njobs:\n  ci:\n    runs-on: macos-latest\n",
        )
        _git(self.upstream_wt, "add", ".github/workflows/ci.yml")
        _git(self.upstream_wt, "commit", "-m", "sync ci.yml base")
        _git(self.upstream_wt, "push", "origin", "main")
        _git(self.mac_wt, "fetch", "origin")
        _git(self.mac_wt, "merge", "--no-edit", "origin/main")

        pre_merge_sha = _git(self.mac_wt, "rev-parse", "HEAD").strip()
        # Upstream commit reintroduces runs-on: ubuntu-latest in jobs.ci
        self._push_upstream(
            {
                ".github/workflows/ci.yml": "name: CI\njobs:\n  ci:\n    runs-on: ubuntu-latest\n",
            },
            "ci: revert runner to ubuntu-latest",
        )

        rc, data, _, stderr = _run_tool(self.mac_wt, "--sync", "--json")
        self.assertEqual(rc, 1, msg=f"stdout={data} stderr={stderr}")
        self.assertIs(data.get("rolled_back"), True)
        self.assertEqual(_git(self.mac_wt, "rev-parse", "HEAD").strip(), pre_merge_sha)
        self.assertEqual(_git(self.mac_wt, "status", "--porcelain"), "")
        self.assertIn(
            "runs-on: macos-latest",
            (self.mac_wt / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
        )

        # Also verify secondary workflow (main-full.yml matrix) reintroducing ubuntu-latest rolls back,
        # while Linux-exempt codeql.yml is allowed.
        self._push_upstream(
            {
                ".github/workflows/ci.yml": "name: CI\njobs:\n  ci:\n    runs-on: macos-latest\n",
                ".github/workflows/codeql.yml": "name: CodeQL\njobs:\n  analyze:\n    runs-on: ubuntu-latest\n",
                ".github/workflows/main-full.yml": "name: Main\njobs:\n  verify:\n    strategy:\n      matrix:\n        os: [ubuntu-latest, windows-latest]\n",
            },
            "ci: add codeql and ubuntu main-full",
        )
        rc2, data2, _, _ = _run_tool(self.mac_wt, "--sync", "--json")
        self.assertEqual(rc2, 1)
        self.assertIs(data2.get("rolled_back"), True)
        self.assertEqual(_git(self.mac_wt, "rev-parse", "HEAD").strip(), pre_merge_sha)

    def test_audit_and_sync_rollback_when_unbaselined_windows_user_path_introduced(self):
        pre_merge_sha = _git(self.mac_wt, "rev-parse", "HEAD").strip()
        bad_win_path = "C:" + "/Users/alice/secret-workspace"
        self._push_upstream(
            {"tools/leaked_path.py": f'WORKSPACE_ROOT = "{bad_win_path}"\n'},
            "oops: hardcode windows user path",
        )

        # Verify direct audit_mac_invariants fails when present
        _git(self.mac_wt, "fetch", "origin")
        _git(self.mac_wt, "merge", "--no-edit", "origin/main")
        direct = mac_fork_sync.audit_mac_invariants(self.mac_wt)
        self.assertFalse(direct["ok"])
        _git(self.mac_wt, "reset", "--hard", pre_merge_sha)

        # Verify --sync fails closed (exit 1) and rolls back to pre_merge_sha
        rc, data, _, stderr = _run_tool(self.mac_wt, "--sync", "--json")
        self.assertEqual(rc, 1, msg=f"stdout={data} stderr={stderr}")
        self.assertIs(data.get("rolled_back"), True)
        self.assertEqual(_git(self.mac_wt, "rev-parse", "HEAD").strip(), pre_merge_sha)
        self.assertFalse((self.mac_wt / "tools/leaked_path.py").exists())

    def test_clean_sync_push_to_mac_fork_and_hard_refuse_origin_main_master(self):
        # Hard-refuse origin/main and origin/master
        head_before = _git(self.mac_wt, "rev-parse", "HEAD").strip()
        for protected in ("main", "master"):
            rc_bad, _, _, _ = _run_tool(
                self.mac_wt,
                "--sync",
                "--push",
                "--fork-remote",
                "origin",
                "--fork-branch",
                protected,
                "--json",
            )
            self.assertEqual(rc_bad, 2)
            rc_bad_fork, _, _, _ = _run_tool(
                self.mac_wt,
                "--sync",
                "--push",
                "--fork-remote",
                "mac-fork",
                "--fork-branch",
                protected,
                "--json",
            )
            self.assertEqual(rc_bad_fork, 2)
            self.assertEqual(_git(self.mac_wt, "rev-parse", "HEAD").strip(), head_before)

        # Also refuse pushing even a feature branch to origin without --allow-origin-branch
        rc_origin_feat, _, _, _ = _run_tool(
            self.mac_wt,
            "--sync",
            "--push",
            "--fork-remote",
            "origin",
            "--fork-branch",
            "port/macos-sweep",
            "--json",
        )
        self.assertEqual(rc_origin_feat, 2)
        self.assertEqual(_git(self.mac_wt, "rev-parse", "HEAD").strip(), head_before)

        # Clean upstream commit -> merge + audit pass + push to mac-fork
        upstream_sha = self._push_upstream(
            {"tools/new_module.py": "def ok():\n    return True\n"},
            "feat: clean portable tool",
        )

        rc, data, _, stderr = _run_tool(
            self.mac_wt,
            "--sync",
            "--push",
            "--fork-remote",
            "mac-fork",
            "--fork-branch",
            "port/macos-sweep",
            "--json",
        )
        self.assertEqual(rc, 0, msg=f"stdout={data} stderr={stderr}")
        self.assertIs(data.get("pushed"), True)
        self.assertTrue((self.mac_wt / "tools/new_module.py").is_file())

        # Verify mac-fork bare remote has the synced commit and ancestor includes upstream_sha
        local_head = _git(self.mac_wt, "rev-parse", "HEAD").strip()
        remote_head = _git(self.fork_bare, "rev-parse", "refs/heads/port/macos-sweep").strip()
        self.assertEqual(remote_head, local_head)
        _git(self.mac_wt, "merge-base", "--is-ancestor", upstream_sha, "HEAD")


if __name__ == "__main__":
    unittest.main()
