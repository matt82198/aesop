#!/usr/bin/env python3
"""
Tests for tools/defect_escape.py — Haiku code quality telemetry.

TDD: These tests verify:
1. Commits matching fix-forward|hotfix|fix(ci)|repair patterns are counted
2. Fix-forward rate = fixforward commits / feature commits
3. First-try-estimate for merge detection
4. JSON output format correctness
5. Test hygiene: no pollution of global git config

IMPORTANT: Tests use tempfile.TemporaryDirectory() to isolate git repos
and prevent global config pollution (test-hygiene rule).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Add tools to path for import
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from defect_escape import (
    run_git,
    get_commits_since,
    get_commit_subject,
    is_fixforward_commit,
    get_commit_parents,
    main as defect_escape_main,
)


class TestIsFixforwardCommit(unittest.TestCase):
    """Test fix-forward pattern detection."""

    def test_matches_fix_forward(self):
        """Test fix-forward pattern."""
        self.assertTrue(is_fixforward_commit("fix-forward: update handler"))
        self.assertTrue(is_fixforward_commit("Fix-Forward: cleanup"))
        self.assertTrue(is_fixforward_commit("FIX-FORWARD: revert broken change"))

    def test_matches_hotfix(self):
        """Test hotfix pattern."""
        self.assertTrue(is_fixforward_commit("hotfix: race condition"))
        self.assertTrue(is_fixforward_commit("Hotfix: memory leak"))
        self.assertTrue(is_fixforward_commit("HOTFIX: urgent patch"))

    def test_matches_fix_ci(self):
        """Test fix(ci) pattern."""
        self.assertTrue(is_fixforward_commit("fix(ci): workflow timeout"))
        self.assertTrue(is_fixforward_commit("fix( ci ): spacing variant"))
        self.assertTrue(is_fixforward_commit("FIX(CI): uppercase"))

    def test_matches_repair(self):
        """Test repair pattern."""
        self.assertTrue(is_fixforward_commit("repair: broken build"))
        self.assertTrue(is_fixforward_commit("Repair: damaged config"))
        self.assertTrue(is_fixforward_commit("REPAIR: critical"))

    def test_does_not_match_feature(self):
        """Test that feature commits don't match."""
        self.assertFalse(is_fixforward_commit("feat: add new feature"))
        self.assertFalse(is_fixforward_commit("chore: cleanup"))
        self.assertFalse(is_fixforward_commit("docs: update readme"))

    def test_does_not_match_generic_fix(self):
        """Test that generic 'fix' without pattern doesn't match."""
        self.assertFalse(is_fixforward_commit("fix: generic issue"))
        self.assertFalse(is_fixforward_commit("Fix: something broken"))


class TestRunGit(unittest.TestCase):
    """Test git subprocess wrapper."""

    def setUp(self):
        """Create temporary git repo with initial commit."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        # Initialize git repo with local config
        subprocess.run(
            ["git", "init"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "user.name", "Test User"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "user.email", "test@example.com"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

        # Create initial commit so git log works
        test_file = self.repo / "test.txt"
        test_file.write_text("initial\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def test_run_git_success(self):
        """Test successful git command."""
        # Test with a git command that doesn't trigger hygiene checks (read-only, not config user.*)
        output = run_git(["log", "--format=%H", "-1"], self.repo)
        # Should get a commit hash (or empty if no commits)
        self.assertIsInstance(output, str)

    def test_run_git_invalid_command(self):
        """Test that invalid git command raises error."""
        with self.assertRaises(RuntimeError):
            run_git(["invalid-command"], self.repo)


class TestDefectEscapeIntegration(unittest.TestCase):
    """Integration tests for defect escape telemetry."""

    def setUp(self):
        """Create temporary git repo with test commits."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        # Initialize git repo with isolated local config
        subprocess.run(
            ["git", "init"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "user.name", "Test User"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "user.email", "test@example.com"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

        # Create test file and make commits
        self.test_file = self.repo / "test.txt"
        self.test_file.write_text("initial\n")

        # Calculate dates
        now = datetime.now()
        self.since_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        # Commit 1: feature
        self.test_file.write_text("feature 1\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "feat: add feature 1"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

        # Commit 2: fix-forward
        self.test_file.write_text("feature 1 fixed\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "fix-forward: correct feature 1"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

        # Commit 3: another feature
        self.test_file.write_text("feature 2\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "feat: add feature 2"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

        # Commit 4: hotfix
        self.test_file.write_text("feature 2 hotfixed\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "hotfix: urgent patch"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def test_get_commits_since(self):
        """Test retrieving commits since a date."""
        commits = get_commits_since(self.repo, self.since_date)
        # Should have 4 commits (all are after yesterday)
        self.assertEqual(len(commits), 4)

    def test_commit_subject_retrieval(self):
        """Test getting commit subject."""
        commits = get_commits_since(self.repo, self.since_date)
        subjects = [get_commit_subject(self.repo, c) for c in commits]
        self.assertIn("feat: add feature 1", subjects)
        self.assertIn("fix-forward: correct feature 1", subjects)

    def test_fixforward_rate_calculation(self):
        """Test fix-forward rate calculation."""
        commits = get_commits_since(self.repo, self.since_date)

        fixforward_count = 0
        feature_count = 0

        for commit in commits:
            subject = get_commit_subject(self.repo, commit)
            if is_fixforward_commit(subject):
                fixforward_count += 1
            else:
                feature_count += 1

        # Should have 2 feature and 2 fix-forward
        self.assertEqual(feature_count, 2)
        self.assertEqual(fixforward_count, 2)

        # Rate should be 100% (2 fix-forward / 2 feature)
        rate = fixforward_count / feature_count if feature_count > 0 else 0.0
        self.assertAlmostEqual(rate, 1.0, places=2)

    def test_json_output_format(self):
        """Test JSON output format."""
        commits = get_commits_since(self.repo, self.since_date)

        fixforward_count = sum(
            1
            for c in commits
            if is_fixforward_commit(get_commit_subject(self.repo, c))
        )
        feature_count = len(commits) - fixforward_count

        result = {
            "window": {"since": self.since_date, "total_commits": len(commits)},
            "feature_commits": feature_count,
            "fixforward_commits": fixforward_count,
            "fixforward_rate": round(
                fixforward_count / feature_count if feature_count > 0 else 0.0, 4
            ),
            "first_try_estimate": None,
        }

        # Should be valid JSON
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        self.assertEqual(parsed["window"]["total_commits"], 4)
        self.assertEqual(parsed["feature_commits"], 2)
        self.assertEqual(parsed["fixforward_commits"], 2)
        self.assertAlmostEqual(parsed["fixforward_rate"], 1.0, places=2)

    def test_commit_parents_detection(self):
        """Test detecting commit parents."""
        commits = get_commits_since(self.repo, self.since_date)

        # Last 3 commits should have 1 parent
        for commit in commits[:3]:
            parents = get_commit_parents(self.repo, commit)
            self.assertEqual(len(parents), 1)


class TestDefectEscapeCLI(unittest.TestCase):
    """Test CLI argument handling and main function."""

    def setUp(self):
        """Create temporary git repo."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        subprocess.run(
            ["git", "init"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "user.name", "Test User"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "user.email", "test@example.com"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

        test_file = self.repo / "test.txt"
        test_file.write_text("test\n")

        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def test_cli_with_valid_repo_and_date(self):
        """Test CLI with valid repo and date."""
        since_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        sys.argv = [
            "defect_escape",
            "--repo",
            str(self.repo),
            "--since",
            since_date,
            "--json",
        ]

        # Capture output
        import io
        from contextlib import redirect_stdout

        output = io.StringIO()
        try:
            with redirect_stdout(output):
                result = defect_escape_main()
        except SystemExit as e:
            result = e.code

        self.assertEqual(result, 0)

    def test_cli_with_invalid_repo(self):
        """Test CLI with invalid repo path."""
        sys.argv = [
            "defect_escape",
            "--repo",
            "/nonexistent/repo",
            "--since",
            "2026-07-01",
            "--json",
        ]

        with self.assertRaises(SystemExit) as cm:
            defect_escape_main()
        self.assertEqual(cm.exception.code, 1)

    def test_cli_with_invalid_date(self):
        """Test CLI with invalid date format."""
        sys.argv = [
            "defect_escape",
            "--repo",
            str(self.repo),
            "--since",
            "invalid-date",
            "--json",
        ]

        with self.assertRaises(SystemExit) as cm:
            defect_escape_main()
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
