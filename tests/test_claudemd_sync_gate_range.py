#!/usr/bin/env python3
"""
Range tests for claudemd_sync_gate.py --check (committed-but-unpushed work).

Incident (2026-10): a lane ran `--check --base-ref origin/main` on a branch whose
code change was committed ahead of origin/main and got "[OK]", while the pre-push
receipt part (no --base-ref) reported "[DRIFT]" for the same range.

Mechanism: get_git_changed_files() builds `origin/{base_ref}...HEAD`, so a base_ref
that already says `origin/main` becomes `origin/origin/main...HEAD`, which fails.
The loop then falls back to `HEAD~1...HEAD`, which sees only the last commit.

TDD: test_check_sees_committed_unpushed_code_change is RED on the current gate.
The three pins document current behaviour; a deliberate gate change flips them.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GATE = "tools/claudemd_sync_gate.py"
REPO_ROOT = Path(__file__).parent.parent


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "commit.gpgsign", "false")


def _commit(path, message, files):
    for rel, text in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        _git(path, "add", rel)
    _git(path, "commit", "-q", "-m", message)


def _base_repo(tmp):
    """Base commit on main, with origin/main pointing at it (no real remote)."""
    root = Path(tmp)
    _init_repo(root)
    _commit(root, "base", {
        "tools/CLAUDE.md": "# Tools domain\n",
        "tools/x.py": "print('a')\n",
        "README.md": "readme\n",
    })
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def _run_check(root, base_ref="origin/main"):
    # GITHUB_BASE_REF changes the refs the gate tries first; keep the result deterministic.
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_BASE_REF"}
    result = subprocess.run(
        [sys.executable, GATE, "--check", "--base-ref", base_ref, "--root", str(root)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


class TestCheckSeesCommittedRange(unittest.TestCase):

    def test_check_sees_committed_unpushed_code_change(self):
        """RED today: a tools/ change committed ahead of origin/main must be DRIFT.

        Two commits ahead on purpose: with one commit, HEAD~1...HEAD happens to be
        the same range and the fallback hides the bug.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _base_repo(tmp)
            _commit(root, "change x", {"tools/x.py": "print('b')\n"})
            _commit(root, "add test", {"tests/test_x.py": "def test_x(): pass\n"})

            code, out, err = _run_check(root, base_ref="origin/main")

            self.assertEqual(code, 1, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[DRIFT]", out, f"stdout: {out}\nstderr: {err}")

    def test_check_single_commit_ahead_is_drift_today(self):
        """GREEN pin (today): one commit ahead is caught only via the HEAD~1 fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _base_repo(tmp)
            _commit(root, "change x", {"tools/x.py": "print('b')\n"})

            code, out, err = _run_check(root, base_ref="origin/main")

            self.assertEqual(code, 1, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[DRIFT]", out, f"stdout: {out}\nstderr: {err}")

    def test_check_default_base_ref_sees_committed_range_today(self):
        """GREEN pin (today): the pre-push form (default base `main`) reads origin/main...HEAD."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _base_repo(tmp)
            _commit(root, "change x", {"tools/x.py": "print('b')\n"})
            _commit(root, "add test", {"tests/test_x.py": "def test_x(): pass\n"})

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 1, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[DRIFT]", out, f"stdout: {out}\nstderr: {err}")

    def test_check_working_tree_change_not_read_today(self):
        """GREEN pin (today's behaviour, not a requirement): an uncommitted tools/ edit is not read.

        The gate diffs committed refs only. If this flips to DRIFT, the gate changed
        on purpose and this pin must be updated in the same change.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _base_repo(tmp)
            _commit(root, "add test", {"tests/test_x.py": "def test_x(): pass\n"})
            (root / "tools" / "x.py").write_text("print('dirty')\n")

            code, out, err = _run_check(root, base_ref="origin/main")

            self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[OK]", out, f"stdout: {out}\nstderr: {err}")


def _set_head(path, branch):
    """Point HEAD at an unborn branch so the local default-branch config cannot leak in."""
    _git(path, "symbolic-ref", "HEAD", f"refs/heads/{branch}")


_DEFAULT_BASE_FILES = {
    "tools/CLAUDE.md": "# Tools domain\n",
    "tools/x.py": "print('a')\n",
    "README.md": "readme\n",
}


def _remote_repo(tmp, branch, push=True, base_files=None):
    """Work repo on `branch` with a bare origin. Base commit pushed when push=True.

    The base commit is on `branch` only. No other base ref exists, so the gate
    must resolve the trunk from the remote, not from a hard-coded name.
    base_files overrides the base commit's content (default includes tools/CLAUDE.md).
    """
    root = Path(tmp) / "work"
    origin = Path(tmp) / "origin.git"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True)
    _init_repo(root)
    _set_head(root, branch)
    _commit(root, "base", dict(_DEFAULT_BASE_FILES if base_files is None else base_files))
    _git(root, "remote", "add", "origin", str(origin))
    if push:
        _git(root, "push", "-q", "origin", branch)
        _git(root, "fetch", "-q", "origin")
    return root


class TestTrunkAndBootstrapBaseResolution(unittest.TestCase):

    def test_trunk_master_only_remote_is_the_base(self):
        """RED: remote has only master (no main). The gate must diff against origin/master.

        One commit touches tools/x.py and tools/CLAUDE.md together, so there is no drift.
        Today the candidates are origin/main and main only, so the gate exits 2.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _remote_repo(tmp, "master", push=True)
            _commit(root, "change x with clause", {
                "tools/x.py": "print('b')\n",
                "tools/CLAUDE.md": "# Tools domain\n- x.py: prints b\n",
            })

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
            self.assertIn("origin/master", out, f"stdout: {out}\nstderr: {err}")

    def test_bootstrap_empty_remote_consistent_change_passes(self):
        """RED: empty bare origin, no main/master base. Whole-tree check, consistent pair, exit 0.

        Required behaviour: diff against git's empty tree and print [BOOTSTRAP].
        Today the gate exits 2 (could not diff against base).
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _remote_repo(tmp, "feature/bootstrap", push=False)
            _commit(root, "fresh scaffold", {
                "tools/x.py": "print('b')\n",
                "tools/CLAUDE.md": "# Tools domain\n- x.py: prints b\n",
            })

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[BOOTSTRAP]", out, f"stdout: {out}\nstderr: {err}")

    def test_bootstrap_empty_remote_code_without_clause_still_drifts(self):
        """RED: empty bare origin, tools/x.py committed with no CLAUDE.md change.

        The whole-tree check must still bite in bootstrap mode: DRIFT, non-zero exit.
        Today the gate exits 2, not 1, so the exit-code assertion fails.
        """
        with tempfile.TemporaryDirectory() as tmp:
            # No tools/CLAUDE.md anywhere in the bootstrap history: nothing documents x.py.
            root = _remote_repo(tmp, "feature/bootstrap", push=False, base_files={
                "tools/x.py": "print('a')\n",
                "README.md": "readme\n",
            })
            _commit(root, "fresh scaffold without clause", {
                "tools/x.py": "print('b')\n",
            })

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 1, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[DRIFT]", out, f"stdout: {out}\nstderr: {err}")

    def test_no_base_with_nonempty_remote_fails_closed(self):
        """GREEN pin: origin has a pushed feature branch but no main/master anywhere.

        Remote is not empty, so bootstrap does not apply. The gate exits 2 with the
        existing fail-closed message and never falls back to HEAD~1.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _remote_repo(tmp, "feature/other", push=True)
            _commit(root, "change x", {"tools/x.py": "print('b')\n"})

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 2, f"stdout: {out}\nstderr: {err}")
            self.assertIn("could not diff against base", err + out, f"stdout: {out}\nstderr: {err}")


def _no_remote_repo(tmp, branch="main"):
    """Work repo on `branch` with NO remote and no origin/* refs. Base commit on `branch`."""
    root = Path(tmp)
    _init_repo(root)
    _set_head(root, branch)
    _commit(root, "base", dict(_DEFAULT_BASE_FILES))
    return root


class TestBaseResolutionAgainstHead(unittest.TestCase):
    """Remote-tracking bases may equal HEAD (zero commits ahead = OK); local branches may not.

    A remote-tracking candidate (origin/<trunk>) that resolves is always a valid base, even at
    HEAD: equal means no changes. A local-branch candidate is valid only when it is not the
    checked-out branch and does not resolve to HEAD (no-remote self-diff would read OK over drift).
    """

    def test_head_equal_to_origin_main_is_ok(self):
        """RED on 2b197ac0: HEAD == origin/main (CI on main after a merge, zero commits ahead).

        origin/main resolves and equals HEAD. It is a valid remote-tracking base, so the diff is
        empty and the gate prints OK exit 0. The pre-change gate skipped it and exited 2.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _remote_repo(tmp, "main", push=True)

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[OK]", out, f"stdout: {out}\nstderr: {err}")

    def test_detached_head_at_origin_main_is_ok(self):
        """Detached HEAD at origin/main: origin/main is the base and equals HEAD, so OK exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _remote_repo(tmp, "main", push=True)
            _git(root, "checkout", "-q", "--detach", "origin/main")

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[OK]", out, f"stdout: {out}\nstderr: {err}")

    def test_feature_branch_drift_against_origin_main_is_drift(self):
        """Feature branch with a tools/ change committed ahead of origin/main must be DRIFT exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _remote_repo(tmp, "main", push=True)
            _git(root, "checkout", "-q", "-b", "feature/x")
            _commit(root, "change x", {"tools/x.py": "print('b')\n"})

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 1, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[DRIFT]", out, f"stdout: {out}\nstderr: {err}")
            self.assertIn("origin/main", out, f"stdout: {out}\nstderr: {err}")

    def test_no_remote_head_on_main_code_drift_is_not_ok(self):
        """RED on the pre-fix gate: no remote, HEAD on main, code committed with no CLAUDE.md.

        origin/main is absent, so the bare name `main` resolves to HEAD itself. The gate
        diffed main...HEAD (empty) and printed [OK] exit 0 over committed drift. The fixed
        gate must fail closed (exit 2) and name the recipe; a HEAD~1 fallback would
        instead print DRIFT exit 1 and is excluded by this exit-code assertion.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _no_remote_repo(tmp)
            _commit(root, "change x", {"tools/x.py": "print('b')\n"})

            code, out, err = _run_check(root, base_ref="main")

            self.assertNotIn("[OK]", out, f"stdout: {out}\nstderr: {err}")
            self.assertEqual(code, 2, f"stdout: {out}\nstderr: {err}")
            self.assertIn("--base-ref", err + out, f"stdout: {out}\nstderr: {err}")

    def test_first_push_of_main_to_empty_remote_is_whole_tree_bootstrap(self):
        """RED on the pre-fix gate: empty origin, HEAD on main, whole-tree bootstrap must bite.

        This is the reachable case (pre-push of a first main push). `main` resolved to
        HEAD, so the gate diffed nothing and printed [OK] exit 0. With HEAD excluded the
        bootstrap whole-tree path applies: tools/x.py has no CLAUDE.md anywhere in
        history, so it must be DRIFT, exit 1, and the output must say [BOOTSTRAP].
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _remote_repo(tmp, "main", push=False, base_files={
                "tools/x.py": "print('a')\n",
                "README.md": "readme\n",
            })
            _commit(root, "fresh scaffold without clause", {"tools/x.py": "print('b')\n"})

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 1, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[BOOTSTRAP]", out, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[DRIFT]", out, f"stdout: {out}\nstderr: {err}")

    def test_origin_main_still_wins_when_local_main_is_head(self):
        """GREEN pin: origin/main exists and differs; local main == HEAD must not displace it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _set_head(root, "main")
            _commit(root, "base", dict(_DEFAULT_BASE_FILES))
            _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
            _commit(root, "change x", {"tools/x.py": "print('b')\n"})

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 1, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[BASE] origin/main", out, f"stdout: {out}\nstderr: {err}")

    def test_feature_branch_uses_local_main_when_no_remote(self):
        """GREEN pin: no remote, feature branch ahead of a real local main; main stays the base."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _no_remote_repo(tmp)
            _git(root, "checkout", "-q", "-b", "feature/x")
            _commit(root, "change x", {"tools/x.py": "print('b')\n"})

            code, out, err = _run_check(root, base_ref="main")

            self.assertEqual(code, 1, f"stdout: {out}\nstderr: {err}")
            self.assertIn("[BASE] main", out, f"stdout: {out}\nstderr: {err}")


if __name__ == "__main__":
    unittest.main()
