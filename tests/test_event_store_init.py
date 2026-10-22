"""Tests for tools/event_store/init.py and its pre-commit guard.

init.py creates a per-project event store: an orphan branch in a repo, a worktree
for it, store.json, .gitattributes, events/ and runs/ dirs, a README, and the
append-only pre-commit guard wired in through a per-worktree core.hooksPath.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INIT = REPO / "tools" / "event_store" / "init.py"
GUARD = REPO / "tools" / "event_store" / "guard" / "events_append_only.sh"


def git(cwd, *args, check=True):
    """Run git in cwd and return stdout. Raise on failure when check is True."""
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def run_init(repo, worktree, branch="events/keyrun", project="keyrun"):
    """Run init.py the way the CLI is documented: python -I, stdlib only."""
    return subprocess.run(
        [sys.executable, "-I", str(INIT),
         "--repo", str(repo), "--project", project,
         "--branch", branch, "--worktree", str(worktree)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


class InitRepo(unittest.TestCase):
    """A temp git repo on main with one commit, plus a worktree path for the store."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "README.md").write_text("fixture\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-q", "-m", "init")
        self.wt = self.tmp / "wt-keyrun"

    def tearDown(self):
        self._tmp.cleanup()


class TestInit(InitRepo):

    def test_init_creates_orphan_store(self):
        result = run_init(self.repo, self.wt)
        self.assertEqual(result.returncode, 0, result.stderr)

        # Orphan branch: its first commit has no parent (rev-list --parents shows one hash).
        parents = git(self.repo, "rev-list", "--parents", "events/keyrun").stdout.split()
        self.assertEqual(len(parents), 1, parents)

        # Worktree at the given path, on the store branch.
        self.assertTrue(self.wt.is_dir())
        self.assertEqual(git(self.wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
                         "events/keyrun")

        # Store contents.
        self.assertEqual(json.loads((self.wt / "store.json").read_text(encoding="utf-8")),
                         {"project": "keyrun", "schema": 2})
        self.assertIn("*.jsonl -text",
                      (self.wt / ".gitattributes").read_text(encoding="utf-8"))
        self.assertTrue((self.wt / "events" / ".gitkeep").is_file())
        self.assertTrue((self.wt / "runs" / ".gitkeep").is_file())
        self.assertTrue((self.wt / "README.md").is_file())

        # Everything init wrote is committed on the store branch.
        self.assertEqual(git(self.wt, "status", "--porcelain").stdout.strip(), "")

    def test_guard_installed_for_worktree_only(self):
        result = run_init(self.repo, self.wt)
        self.assertEqual(result.returncode, 0, result.stderr)

        hooks_path = git(self.wt, "config", "--get", "core.hooksPath").stdout.strip()
        self.assertTrue(hooks_path, "core.hooksPath must be set for the store worktree")
        hook = Path(hooks_path) / "pre-commit"
        self.assertTrue(hook.is_file())
        self.assertEqual(hook.read_bytes(), GUARD.read_bytes())

        # The primary repo must not inherit the store's hooks path.
        primary = git(self.repo, "config", "--get", "core.hooksPath", check=False)
        self.assertNotEqual(primary.stdout.strip(), hooks_path)

    def test_existing_branch_exits_1_and_changes_nothing(self):
        first = run_init(self.repo, self.wt)
        self.assertEqual(first.returncode, 0, first.stderr)
        head_before = git(self.repo, "rev-parse", "events/keyrun").stdout.strip()
        branches_before = git(self.repo, "branch", "--list").stdout

        second = run_init(self.repo, self.tmp / "wt-other")

        self.assertEqual(second.returncode, 1)
        self.assertEqual(git(self.repo, "rev-parse", "events/keyrun").stdout.strip(), head_before)
        self.assertEqual(git(self.repo, "branch", "--list").stdout, branches_before)
        self.assertFalse((self.tmp / "wt-other").exists())


class TestGuard(InitRepo):
    """The guard, as installed by init, enforces append-only events/*.jsonl."""

    def setUp(self):
        super().setUp()
        result = run_init(self.repo, self.wt)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.day = self.wt / "events" / "2026-10-22.jsonl"
        # Two events on the first day, committed through the installed hook.
        self.day.write_text('{"seq":1,"text":"alpha"}\n{"seq":2,"text":"bravo"}\n',
                            encoding="utf-8", newline="\n")
        git(self.wt, "add", "--", "events")
        self.assertEqual(git(self.wt, "commit", "-q", "-m", "seed", check=False).returncode, 0)

    def commit_count(self):
        return int(git(self.wt, "rev-list", "--count", "HEAD").stdout.strip())

    def test_guard_rejects_edit_and_allows_append(self):
        before = self.commit_count()
        # Rewrite an existing line and stage it: the guard must refuse the commit.
        data = self.day.read_bytes()
        self.day.write_bytes(data.replace(b"alpha", b"ALPHA"))
        git(self.wt, "add", "--", "events")
        edit = git(self.wt, "commit", "-q", "-m", "edit history", check=False)
        self.assertNotEqual(edit.returncode, 0, edit.stderr)
        self.assertIn("append-only", edit.stderr)
        self.assertEqual(self.commit_count(), before)

        # Unstage and restore, then a pure append must pass the same guard.
        git(self.wt, "reset", "-q", "--", "events")
        git(self.wt, "checkout", "--", "events")
        self.day.write_bytes(self.day.read_bytes() + b'{"seq":3,"text":"charlie"}\n')
        git(self.wt, "add", "--", "events")
        append = git(self.wt, "commit", "-q", "-m", "append", check=False)
        self.assertEqual(append.returncode, 0, append.stderr)
        self.assertEqual(self.commit_count(), before + 1)

    def test_guard_rejects_deleting_a_day_file(self):
        before = self.commit_count()
        git(self.wt, "rm", "-q", "--", "events/2026-10-22.jsonl")
        result = git(self.wt, "commit", "-q", "-m", "drop day", check=False)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("append-only", result.stderr)
        self.assertEqual(self.commit_count(), before)


if __name__ == "__main__":
    unittest.main()
