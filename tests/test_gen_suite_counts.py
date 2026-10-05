#!/usr/bin/env python3
"""
Test suite for gen_suite_counts.py (live suite-count library).

Contract under test (post PR #830 "guard: compute suite counts live"):
- There is no stored artifact any more: `--json` (default) is the ONLY mode
  and NEVER writes a file. No `--check`, no `--regenerate`, no `--dry-run`.
- Counts are derived fresh from `git ls-files` on every call -- running twice
  must produce byte-identical output (determinism, not idempotent-writing).
- Fail-closed preserved: non-git-repo = exit 2, vacuous zero derivation = exit 2.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestGenSuiteCounts(unittest.TestCase):
    """Test gen_suite_counts.py's live --json derivation."""

    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures."""
        cls.repo_root = Path(__file__).parent.parent

    def setUp(self):
        """Create temporary isolated repo for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)

        tools_dir = self.temp_root / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)

        tests_dir = self.temp_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        tool_path = self.repo_root / "tools" / "gen_suite_counts.py"
        if tool_path.exists():
            (tools_dir / "gen_suite_counts.py").write_text(tool_path.read_text())

        (tests_dir / "test_a.py").touch()
        (tests_dir / "test_b.py").touch()
        (tests_dir / "test_a.test.mjs").touch()
        (tests_dir / "test_a.sh").touch()

        subprocess.run(["git", "init"], cwd=str(self.temp_root),
                        capture_output=True, check=False)
        subprocess.run(["git", "add", "-A"], cwd=str(self.temp_root),
                        capture_output=True, check=False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_tool(self, *args):
        cmd = [sys.executable, str(self.repo_root / "tools" / "gen_suite_counts.py")]
        cmd.extend(args)
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8',
            cwd=str(self.temp_root),
        )

    def test_json_mode_outputs_live_counts(self):
        """--json outputs the live-derived counts to stdout (read-only)."""
        result = self._run_tool("--json", "--repo", str(self.temp_root))
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        data = json.loads(result.stdout)
        self.assertEqual(data["Node"], 1)
        self.assertEqual(data["Shell"], 1)
        self.assertEqual(data["Python"], 2)

    def test_default_mode_is_also_json(self):
        """No flags at all behaves exactly like --json (there is no other mode)."""
        result = self._run_tool("--repo", str(self.temp_root))
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        data = json.loads(result.stdout)
        self.assertEqual(data, {"Node": 1, "Shell": 1, "Python": 2})

    def test_nothing_is_ever_written(self):
        """A live library writes no files anywhere, under any flag combination."""
        before = sorted(p.relative_to(self.temp_root).as_posix()
                         for p in self.temp_root.rglob("*") if p.is_file())
        for args in (("--json", "--repo", str(self.temp_root)),
                     ("--repo", str(self.temp_root))):
            self._run_tool(*args)
        after = sorted(p.relative_to(self.temp_root).as_posix()
                       for p in self.temp_root.rglob("*") if p.is_file())
        self.assertEqual(before, after, "gen_suite_counts.py must never write a file")

    def test_check_and_regenerate_flags_no_longer_exist(self):
        """--check/--regenerate were removed with the artifact; argparse rejects them."""
        for flag in ("--check", "--regenerate", "--fix", "--dry-run"):
            result = self._run_tool(flag, "--repo", str(self.temp_root))
            self.assertNotEqual(
                result.returncode, 0,
                f"{flag} should no longer be a recognised argument",
            )

    def test_running_twice_is_byte_identical(self):
        """Determinism: calling --json twice in a row yields identical output."""
        first = self._run_tool("--json", "--repo", str(self.temp_root))
        second = self._run_tool("--json", "--repo", str(self.temp_root))
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)

    def test_fail_closed_on_non_git_repo(self):
        """Fail-closed (exit 2) when repo is not a git work tree."""
        temp_dir2 = tempfile.TemporaryDirectory()
        temp_root2 = Path(temp_dir2.name)

        tests_dir = temp_root2 / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_real.py").touch()

        result = subprocess.run(
            [sys.executable, str(self.repo_root / "tools" / "gen_suite_counts.py"),
             "--json", "--repo", str(temp_root2)],
            capture_output=True,
            text=True,
            cwd=str(temp_root2),
        )

        self.assertEqual(result.returncode, 2, f"stderr: {result.stderr}")
        self.assertIn("not a git repository", result.stderr)
        temp_dir2.cleanup()

    def test_vacuous_zero_guard_fails_closed(self):
        """A family that derives to zero while files clearly exist elsewhere
        must fail closed (exit 2), never silently report a 0 count."""
        empty_root = Path(tempfile.mkdtemp())
        try:
            tests_dir = empty_root / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "test_only.py").touch()
            subprocess.run(["git", "init", "-q"], cwd=str(empty_root), check=True,
                            capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=str(empty_root), check=True,
                            capture_output=True)

            result = self._run_tool("--json", "--repo", str(empty_root))
            self.assertEqual(
                result.returncode, 2,
                f"a wiped-out family must fail closed, not report 0. stderr: {result.stderr}",
            )
            self.assertIn("ZERO", result.stderr)
        finally:
            import shutil
            shutil.rmtree(empty_root, ignore_errors=True)


class TestUnmergedStageDeduplication(unittest.TestCase):
    """A path must count exactly once, whatever the index says about it.

    Ported from the pre-#776 tests/test_verify_test_suite_count.py (the fix
    landed in PR #759), reconciled here with the #830 artifact removal: this
    coverage targets gen_suite_counts.py's live `--json` derivation directly,
    since that is where the git ls-files derivation -- and therefore the
    dedup/merge-warning logic -- actually lives.

    Finding (bit the conflict sweep on PRs #710 and #711): `git ls-files <pattern>`
    lists an UNMERGED path once per stage (1=base, 2=ours, 3=theirs). During an
    in-progress merge a single conflicted `tests/test_*.py` was therefore counted
    two or three times.

    The same "count the file list, not the unique paths" shape also double-counts
    a file matched by two of the three shell globs (`tests/test_x.test.sh` matches
    both `tests/*.test.sh` and `tests/test_*.sh`).
    """

    @classmethod
    def setUpClass(cls):
        cls.aesop_root = Path(__file__).parent.parent
        cls.tool = cls.aesop_root / "tools" / "gen_suite_counts.py"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, *args):
        cmd = [sys.executable, str(self.tool)]
        cmd.extend(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    @staticmethod
    def _git(root, *args, check=True):
        """Run git inside root with hermetic, repo-LOCAL identity only."""
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} failed in {root}: {result.stdout}{result.stderr}"
            )
        return result

    def _init_repo(self, name):
        root = self.temp_root / name
        (root / "tests").mkdir(parents=True, exist_ok=True)
        self._git(root, "init", "-q")
        # Repo-local config only -- tests never touch global git config.
        self._git(root, "config", "user.email", "tester@example.invalid")
        self._git(root, "config", "user.name", "tester")
        self._git(root, "config", "commit.gpgsign", "false")
        return root

    def _make_conflicted_repo(self, name="conflicted"):
        """Build a repo left mid-merge with one genuinely conflicted test file.

        Truth on disk: 1 node suite, 1 shell suite, 2 python suites (one of which
        is the unmerged path).
        """
        root = self._init_repo(name)
        tests = root / "tests"
        (tests / "node_a.test.mjs").write_text("// node\n", encoding="utf-8")
        (tests / "test_shell_a.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        (tests / "test_stable.py").write_text("# stable\n", encoding="utf-8")
        (tests / "test_conflict.py").write_text("# base\n", encoding="utf-8")
        self._git(root, "add", "-A")
        self._git(root, "commit", "-q", "-m", "base")

        head = self._git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self._git(root, "branch", "side")

        (tests / "test_conflict.py").write_text("# ours\n", encoding="utf-8")
        self._git(root, "commit", "-q", "-a", "-m", "ours")

        self._git(root, "checkout", "-q", "side")
        (tests / "test_conflict.py").write_text("# theirs\n", encoding="utf-8")
        self._git(root, "commit", "-q", "-a", "-m", "theirs")

        self._git(root, "checkout", "-q", head)
        # Expected to fail: that is the point.
        self._git(root, "merge", "side", check=False)

        unmerged = self._git(root, "ls-files", "--unmerged").stdout
        self.assertIn(
            "tests/test_conflict.py",
            unmerged,
            "fixture must actually leave an unmerged path in the index",
        )
        merge_head = self._git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)
        self.assertEqual(
            merge_head.returncode, 0, "fixture must leave the merge IN PROGRESS"
        )
        return root

    def test_conflicted_path_is_listed_once_per_stage_by_raw_git(self):
        """Document the mechanism: raw `git ls-files` really does repeat the path."""
        root = self._make_conflicted_repo()

        listed = [
            line
            for line in self._git(root, "ls-files", "tests/test_*.py").stdout.splitlines()
            if line
        ]

        self.assertGreater(
            listed.count("tests/test_conflict.py"),
            1,
            "premise of this bug: an unmerged path is listed once per stage",
        )
        self.assertEqual(
            len(set(listed)), 2, "but there are only two unique python suites on disk"
        )

    def test_json_counts_unmerged_path_exactly_once(self):
        """RED before the fix: mid-merge, --json reported 4 for 2 Python files."""
        root = self._make_conflicted_repo()

        result = self._run("--json", "--repo", str(root))

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(
            data["Python"],
            2,
            "a conflicted test file must count ONCE, not once per index stage",
        )
        self.assertEqual(data["Node"], 1)
        self.assertEqual(data["Shell"], 1)

    def test_json_mid_merge_is_deterministic(self):
        """Calling --json twice mid-merge must agree (no write path to drift)."""
        root = self._make_conflicted_repo()

        first = self._run("--json", "--repo", str(root))
        second = self._run("--json", "--repo", str(root))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)

    def test_merge_in_progress_is_loudly_warned_not_silently_accepted(self):
        """Deduped counts are correct, but a half-merged tree must not be silent."""
        root = self._make_conflicted_repo()

        result = self._run("--json", "--repo", str(root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MERGE_HEAD", result.stderr)
        self.assertIn("[WARN]", result.stderr)

    def test_no_merge_warning_on_a_clean_tree(self):
        """The warning must not fire (and pollute gate output) outside a merge."""
        root = self._init_repo("clean")
        tests = root / "tests"
        (tests / "node_a.test.mjs").write_text("// node\n", encoding="utf-8")
        (tests / "test_shell_a.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        (tests / "test_stable.py").write_text("# stable\n", encoding="utf-8")
        self._git(root, "add", "-A")
        self._git(root, "commit", "-q", "-m", "base")

        result = self._run("--json", "--repo", str(root))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("MERGE_HEAD", result.stderr)
        self.assertNotIn("[WARN]", result.stderr)

    def test_file_matching_two_shell_globs_counts_once(self):
        """`tests/test_x.test.sh` matches two shell patterns; it is still one suite."""
        root = self._init_repo("overlap")
        tests = root / "tests"
        (tests / "node_a.test.mjs").write_text("// node\n", encoding="utf-8")
        # Matches BOTH tests/*.test.sh AND tests/test_*.sh.
        (tests / "test_overlap.test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        (tests / "test_stable.py").write_text("# stable\n", encoding="utf-8")
        self._git(root, "add", "-A")
        self._git(root, "commit", "-q", "-m", "base")

        result = self._run("--json", "--repo", str(root))

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(
            data["Shell"], 1, "a file matched by two globs is one suite, not two"
        )


if __name__ == "__main__":
    unittest.main()
