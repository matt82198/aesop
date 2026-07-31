#!/usr/bin/env python3
"""Tests for tools/verify_premerge_count_drift.py.

Covers:
- the merge-race defect is caught: a sibling branch (simulated "base") advances
  past this branch's merge-base by adding a test file + bumping the documented
  count; the branch itself never touches tests/, so its OWN --check would pass
  locally, but this gate predicts the post-merge mismatch.
- clean input passes: a branch that adds its own test file and correctly
  updates its own documented count (no sibling drift) predicts no mismatch.
- zero/invalid input exits 2: non-git directory, and a repo missing
  tests/CLAUDE.md at the evaluated ref.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TOOL = REPO_ROOT / "tools" / "verify_premerge_count_drift.py"

CLAUDEMD_TEMPLATE = """# tests/ fixture

**Node ({node} suites)**: fixture.
**Shell ({shell} suites)**: fixture.
**Python ({python} suites)**: fixture.
"""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=check,
    )


def _run_tool(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def _write_claudemd(repo: Path, node: int, shell: int, python: int) -> None:
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "CLAUDE.md").write_text(
        CLAUDEMD_TEMPLATE.format(node=node, shell=shell, python=python),
        encoding="utf-8",
    )


def _init_repo(repo: Path) -> None:
    """Init a temp git repo scoped identity (never touches global git config)."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")


class TestVerifyPremergeCountDrift(unittest.TestCase):
    """Tests for the pre-merge count-drift prediction gate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        _init_repo(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defect_is_caught_sibling_merge_race(self):
        """A sibling advancing 'main' past this branch's merge-base predicts drift."""
        repo = self.repo
        # Base commit: 2 python test files, documented count 2.
        _write_claudemd(repo, node=0, shell=0, python=2)
        (repo / "tests" / "test_a.py").touch()
        (repo / "tests" / "test_b.py").touch()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base: 2 python suites")

        # Branch off base; never touches tests/ at all.
        _git(repo, "branch", "feature")

        # main advances: sibling PR adds a third test file + bumps the doc count.
        (repo / "tests" / "test_c.py").touch()
        _write_claudemd(repo, node=0, shell=0, python=3)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "sibling: add test_c, bump count to 3")

        result = _run_tool(repo, "--check", "--base", "main", "--head", "feature", "--json")
        self.assertEqual(
            result.returncode, 1,
            f"Expected drift to be predicted. stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        report = json.loads(result.stdout)
        categories = {f["category"] for f in report["findings"]}
        self.assertIn("Python", categories)
        python_finding = next(f for f in report["findings"] if f["category"] == "Python")
        self.assertEqual(python_finding["documented"], 2)
        self.assertEqual(python_finding["predicted_after_merge"], 3)

    def test_clean_when_branch_own_addition_matches_own_docs(self):
        """A branch that adds its own test file and updates its own docs predicts clean."""
        repo = self.repo
        _write_claudemd(repo, node=0, shell=0, python=2)
        (repo / "tests" / "test_a.py").touch()
        (repo / "tests" / "test_b.py").touch()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base: 2 python suites")

        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "tests" / "test_c.py").touch()
        _write_claudemd(repo, node=0, shell=0, python=3)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "feature: add test_c, bump own count to 3")
        _git(repo, "checkout", "-q", "main")

        result = _run_tool(repo, "--check", "--base", "main", "--head", "feature")
        self.assertEqual(
            result.returncode, 0,
            f"Expected no drift predicted. stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_clean_when_head_equals_base(self):
        """Identical head/base refs predict no drift (degenerate case)."""
        repo = self.repo
        _write_claudemd(repo, node=0, shell=0, python=1)
        (repo / "tests" / "test_a.py").touch()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")

        result = _run_tool(repo, "--check", "--base", "main", "--head", "main")
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r}")

    def test_zero_input_not_a_git_repo_exits_2(self):
        """Pointing --repo at a non-git directory must exit 2 (could not evaluate)."""
        not_a_repo = Path(self.tmp.name) / "plain-dir"
        not_a_repo.mkdir()
        result = _run_tool(not_a_repo, "--check")
        self.assertEqual(
            result.returncode, 2,
            f"Expected exit 2 for non-git dir. stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_zero_input_missing_claudemd_exits_2(self):
        """A ref with no tests/CLAUDE.md must exit 2, never silently pass as 0."""
        repo = self.repo
        (repo / "tests").mkdir(parents=True, exist_ok=True)
        (repo / "tests" / "test_a.py").touch()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "no CLAUDE.md at all")

        result = _run_tool(repo, "--check", "--base", "main", "--head", "main")
        self.assertEqual(
            result.returncode, 2,
            f"Expected exit 2 for missing tests/CLAUDE.md. stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_zero_input_bad_ref_exits_2(self):
        """An unresolvable --base ref must exit 2, never 0 or a crash."""
        repo = self.repo
        _write_claudemd(repo, node=0, shell=0, python=0)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")

        result = _run_tool(repo, "--check", "--base", "origin/does-not-exist", "--head", "main")
        self.assertEqual(result.returncode, 2, f"stdout={result.stdout!r} stderr={result.stderr!r}")

    def test_tool_provides_help(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("pre-merge", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
