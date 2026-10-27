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


if __name__ == "__main__":
    unittest.main()
