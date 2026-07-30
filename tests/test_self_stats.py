"""TDD tests for tools/self_stats.py — self-building stats counter for README.

Tests cover:
- Git-derived metrics: merged PRs, total commits, project age, insertions+deletions, files tracked, co-authors (wave_count retained in JSON for backward compatibility but not displayed in README table)
- Session telemetry from docs/self-stats-data.json (omitted when missing/null)
- Output modes: default table, --markdown (with START/END markers), --json
- Markdown block must have verification markers for hard numbers
- Metrics gate validation (no unverified hard metrics)

Run: python -m unittest discover tests test_self_stats
     python -m pytest tests/test_self_stats.py -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Add tools directory to path
TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import self_stats


class SelfStatsFixtureCase(unittest.TestCase):
    """Base fixture: tiny git repo + optional JSON data."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-selfstats-test-"))
        self.repo_root = self.fixture_root / "testrepo"
        self.repo_root.mkdir(parents=True)
        self.data_file = self.repo_root / "docs" / "self-stats-data.json"
        self.data_file.parent.mkdir(parents=True)

        # Initialize tiny git repo
        subprocess.run(["git", "init"], cwd=str(self.repo_root), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(self.repo_root), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(self.repo_root), capture_output=True)

        self._saved_cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._saved_cwd)
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def make_commit(self, msg, coauthor=None):
        """Create a commit in test repo."""
        # Create a test file
        test_file = self.repo_root / "test.txt"
        test_file.write_text(f"content {msg}\n")
        subprocess.run(["git", "add", "test.txt"], cwd=str(self.repo_root), capture_output=True, check=True)

        commit_msg = msg
        if coauthor:
            commit_msg += f"\n\nCo-Authored-By: {coauthor}"

        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(self.repo_root),
            capture_output=True,
            check=True
        )

    def make_merge_commit(self, pr_num):
        """Create a merge commit (mimics github merge)."""
        # Create initial main branch if it doesn't exist
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", "main"],
                cwd=str(self.repo_root),
                capture_output=True,
                check=True
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                ["git", "checkout", "-b", "main"],
                cwd=str(self.repo_root),
                capture_output=True
            )

        # Create and checkout a feature branch
        subprocess.run(
            ["git", "checkout", "-b", f"feature-{pr_num}"],
            cwd=str(self.repo_root),
            capture_output=True,
            check=True
        )
        self.make_commit(f"feature {pr_num}")

        # Switch back to main
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=str(self.repo_root),
            capture_output=True,
            check=True
        )

        # Merge with --no-ff to create merge commit
        subprocess.run(
            ["git", "merge", "--no-ff", f"feature-{pr_num}", "-m", f"Merge pull request #{pr_num} from test/feature"],
            cwd=str(self.repo_root),
            capture_output=True,
            check=True
        )


class GitDerivedStatsTest(SelfStatsFixtureCase):
    """Test git-derived statistics."""

    def test_git_stats_empty_repo(self):
        """Empty repo has zero stats."""
        from unittest.mock import patch
        import subprocess as real_subprocess

        os.chdir(str(self.repo_root))
        stats = self_stats.GitStats(repo_root=str(self.repo_root))

        # Mock subprocess.run to prevent gh from querying the real aesop repo
        with patch.object(self_stats, 'subprocess') as mock_subprocess:
            def run_side_effect(*args, **kwargs):
                if args and 'gh' in args[0]:
                    raise FileNotFoundError("gh not found in test")
                # For git calls, use real subprocess
                return real_subprocess.run(*args, **kwargs)

            mock_subprocess.run.side_effect = run_side_effect
            mock_subprocess.TimeoutExpired = real_subprocess.TimeoutExpired
            stats._merged_prs = None  # Reset cache

            self.assertEqual(stats.merged_prs, 0, "empty repo should have 0 merged PRs")
            self.assertEqual(stats.total_commits, 0, "empty repo should have 0 commits")
            self.assertIsNone(stats.project_age_days, "empty repo should have None project age")

    def test_git_stats_basic(self):
        """Repo with commits and PR merge."""
        from unittest.mock import patch
        import subprocess as real_subprocess

        os.chdir(str(self.repo_root))

        # Create initial commit
        self.make_commit("initial commit")

        # Create a merge commit
        self.make_merge_commit(1)

        stats = self_stats.GitStats(repo_root=str(self.repo_root))

        # Mock subprocess.run to prevent gh from querying the real aesop repo
        with patch.object(self_stats, 'subprocess') as mock_subprocess:
            def run_side_effect(*args, **kwargs):
                if args and 'gh' in args[0]:
                    raise FileNotFoundError("gh not found in test")
                # For git calls, use real subprocess
                return real_subprocess.run(*args, **kwargs)

            mock_subprocess.run.side_effect = run_side_effect
            mock_subprocess.TimeoutExpired = real_subprocess.TimeoutExpired
            stats._merged_prs = None  # Reset cache

            self.assertGreaterEqual(stats.total_commits, 2, "should have at least 2 commits")
            self.assertEqual(stats.merged_prs, 1, "should have 1 merged PR")
            # Project age might be None or 0 depending on git date parsing, so just check it's not negative
            if stats.project_age_days is not None:
                self.assertGreaterEqual(stats.project_age_days, 0, "project age should be >= 0")

    def test_coauthors_detection(self):
        """Should detect Co-Authored-By lines."""
        os.chdir(str(self.repo_root))

        self.make_commit("commit 1")
        self.make_commit("commit 2", coauthor="Claude Haiku <noreply@anthropic.com>")
        self.make_commit("commit 3", coauthor="Claude Sonnet <noreply@anthropic.com>")

        stats = self_stats.GitStats(repo_root=str(self.repo_root))

        # Should include "Test User" + 2 coauthors
        self.assertGreaterEqual(stats.distinct_coauthors, 3, "should detect co-authors")


class SessionTelemetryTest(SelfStatsFixtureCase):
    """Test session telemetry from JSON."""

    def test_no_data_file(self):
        """Missing JSON should return None for telemetry fields."""
        telemetry = self_stats.SessionTelemetry(data_file=str(self.data_file))

        self.assertIsNone(telemetry.total_sessions)
        self.assertIsNone(telemetry.total_turns)
        self.assertIsNone(telemetry.cumulative_tokens)

    def test_data_file_missing_fields(self):
        """JSON with some null fields should omit them."""
        data = {
            "total_sessions": 42,
            "total_turns": None,
            "cumulative_tokens": 1000000
        }
        self.data_file.write_text(json.dumps(data))

        telemetry = self_stats.SessionTelemetry(data_file=str(self.data_file))

        self.assertEqual(telemetry.total_sessions, 42)
        self.assertIsNone(telemetry.total_turns)
        self.assertEqual(telemetry.cumulative_tokens, 1000000)

    def test_data_file_all_fields(self):
        """JSON with all fields should load them."""
        data = {
            "_source": "orchestrator/telemetry.py",
            "_updated": "2024-12-13T14:30:00Z",
            "total_sessions": 15,
            "total_turns": 450,
            "total_user_prompts": 120,
            "max_tokens_single_turn": 8000,
            "cumulative_agent_runs": 340,
            "cumulative_tokens": 45000000,
            "total_coding_hours": 128.5
        }
        self.data_file.write_text(json.dumps(data))

        telemetry = self_stats.SessionTelemetry(data_file=str(self.data_file))

        self.assertEqual(telemetry.total_sessions, 15)
        self.assertEqual(telemetry.total_turns, 450)
        self.assertEqual(telemetry.cumulative_tokens, 45000000)


class OutputModesTest(SelfStatsFixtureCase):
    """Test output modes: table, markdown, json."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        # Create a basic repo
        self.make_commit("initial")
        self.make_merge_commit(1)

        # Add some telemetry data
        data = {
            "total_sessions": 10,
            "total_turns": 200,
            "cumulative_tokens": 10000000
        }
        self.data_file.write_text(json.dumps(data))

    def test_default_table_mode(self):
        """Default mode prints human table."""
        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        output = stats.table()

        self.assertIn("Aesop Self-Building Stats", output, "table should have title")
        self.assertIn("Repository Metrics", output, "table should have metrics section")

    def test_markdown_mode_has_markers(self):
        """Markdown mode has START/END markers."""
        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        output = stats.markdown()

        self.assertIn("<!-- SELF-STATS:START -->", output)
        self.assertIn("<!-- SELF-STATS:END -->", output)

    def test_markdown_mode_contains_stats(self):
        """Markdown mode includes actual stats."""
        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        output = stats.markdown()

        # Should have section header
        self.assertIn("Aesop builds itself", output)
        # Should have table with real data
        self.assertIn("1", output, "should include merged PR count")

    def test_markdown_has_verification_markers(self):
        """Markdown output should include metrics-verified comments for hard numbers."""
        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        output = stats.markdown()

        # Any hard numbers should have verification markers
        # This is a soft check - actual gate will be metrics_gate.py
        if "%" in output or "x " in output or "$" in output:
            self.assertIn("metrics-verified", output, "hard metrics need verification comment")

    def test_json_mode(self):
        """JSON mode outputs machine-readable format."""
        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        output = stats.json()

        # Should be valid JSON
        data = json.loads(output)
        self.assertIsInstance(data, dict)
        self.assertIn("git", data)
        self.assertIn("telemetry", data)
        self.assertIn("merged_prs", data["git"])
        self.assertIn("total_sessions", data["telemetry"])


class CliIntegrationTest(SelfStatsFixtureCase):
    """Test CLI entry point."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        self.make_commit("initial")
        self.make_merge_commit(1)
        data = {
            "total_sessions": 5,
            "cumulative_tokens": 5000000
        }
        self.data_file.write_text(json.dumps(data))

    def test_cli_default_mode(self):
        """CLI default mode calls table()."""
        # Import and run via subprocess to test actual CLI
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py")],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )

        self.assertEqual(result.returncode, 0, f"CLI should exit 0, stderr: {result.stderr}")
        self.assertIn("Aesop Self-Building Stats", result.stdout)

    def test_cli_markdown_mode(self):
        """CLI --markdown mode calls markdown()."""
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--markdown"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env
        )

        self.assertEqual(result.returncode, 0, f"CLI should exit 0, stderr: {result.stderr}")
        if result.stdout:
            self.assertIn("<!-- SELF-STATS:START -->", result.stdout)
            self.assertIn("<!-- SELF-STATS:END -->", result.stdout)

    def test_cli_json_mode(self):
        """CLI --json mode calls json()."""
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--json"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}
        )

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        data = json.loads(result.stdout)
        self.assertIn("git", data)
        self.assertIn("merged_prs", data["git"])


class StatsFileRegenerationTest(SelfStatsFixtureCase):
    """Test --regenerate mode for stats.json."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        self.make_commit("initial")
        self.make_merge_commit(1)
        self.stats_file = self.repo_root / "stats.json"

    def test_regenerate_creates_stats_json(self):
        """--regenerate should create/update stats.json with fresh git data."""
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--regenerate", "--stats-file", str(self.stats_file)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )

        self.assertEqual(result.returncode, 0, f"CLI should exit 0, stderr: {result.stderr}")
        self.assertTrue(self.stats_file.exists(), "stats.json should be created")

        # Verify it's valid JSON
        with open(self.stats_file) as f:
            data = json.load(f)

        # Check structure
        self.assertIn("git", data)
        self.assertIn("telemetry", data)
        self.assertIn("generated_at", data)
        self.assertIn("loc", data)

        # Verify git stats are populated
        self.assertGreaterEqual(data["git"]["total_commits"], 1)
        # merged_prs could be 1 (from git) or higher (from gh API hitting real aesop repo)
        # Since we hardcode repo:matt82198/aesop, don't assert specific value here
        self.assertGreaterEqual(data["git"]["merged_prs"], 0)

    def test_regenerate_includes_metadata(self):
        """Regenerated stats.json should include generated_at and loc fields."""
        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        stats.save_stats(str(self.stats_file))

        with open(self.stats_file) as f:
            data = json.load(f)

        self.assertIn("generated_at", data)
        self.assertIn("loc", data)
        self.assertIsInstance(data["loc"], int)
        self.assertGreater(data["loc"], 0, "should have some lines of code")


class ReadmeUpdateTest(SelfStatsFixtureCase):
    """Test --update-readme mode for updating README.md."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        self.make_commit("initial")
        self.make_merge_commit(1)
        self.stats_file = self.repo_root / "stats.json"
        self.readme_file = self.repo_root / "README.md"

    def test_update_readme_with_stats_markers(self):
        """--update-readme should replace content between <!-- STATS:START/END --> markers."""
        # Create a README with STATS markers
        readme_content = """# Test Project

Some intro text.

<!-- STATS:START -->
This will be replaced.
<!-- STATS:END -->

Footer text.
"""
        self.readme_file.write_text(readme_content)

        # First regenerate stats.json
        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        stats.save_stats(str(self.stats_file))

        # Now update README
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--update-readme",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )

        self.assertEqual(result.returncode, 0, f"CLI should exit 0, stderr: {result.stderr}")
        self.assertIn("Updated", result.stdout)

        # Verify README was updated
        updated_content = self.readme_file.read_text()
        self.assertIn("<!-- STATS:START -->", updated_content)
        self.assertIn("<!-- STATS:END -->", updated_content)
        self.assertIn("Aesop builds itself", updated_content)
        self.assertIn("Metric | Value", updated_content, "should have table header")

    def test_update_readme_gracefully_noop_without_markers(self):
        """--update-readme should gracefully skip if markers don't exist."""
        # Create a README without STATS markers
        readme_content = "# Test Project\n\nNo stats markers here.\n"
        self.readme_file.write_text(readme_content)

        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        stats.save_stats(str(self.stats_file))

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--update-readme",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )

        self.assertEqual(result.returncode, 0, "should exit 0 even if no markers")
        self.assertIn("No markers found", result.stdout, "should report graceful no-op")

        # README should be unchanged
        unchanged_content = self.readme_file.read_text()
        self.assertEqual(unchanged_content, readme_content, "README should not be modified")

    def test_update_readme_preserves_surrounding_content(self):
        """--update-readme should preserve content before/after markers."""
        header = "# My Project\nIntroduction text.\n\n"
        footer = "\n\nFooter section.\nMore content here.\n"
        readme_content = header + "<!-- STATS:START -->OLD<!-- STATS:END -->" + footer

        self.readme_file.write_text(readme_content)

        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        stats.save_stats(str(self.stats_file))

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--update-readme",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )

        self.assertEqual(result.returncode, 0)

        updated = self.readme_file.read_text()
        self.assertTrue(updated.startswith(header), "header should be preserved")
        self.assertTrue(updated.endswith(footer), "footer should be preserved")


class StatsCheckModeTest(SelfStatsFixtureCase):
    """Test --check mode for drift detection."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        self.make_commit("initial")
        self.make_merge_commit(1)
        self.stats_file = self.repo_root / "stats.json"
        self.readme_file = self.repo_root / "README.md"

    def test_check_passes_when_readme_matches_stats(self):
        """--check should return 0 when README matches stats.json."""
        # Create a README with matching stats using --update-readme mode
        # This ensures the README is created exactly as the check expects
        readme_content = """# Project

<!-- STATS:START -->
placeholder
<!-- STATS:END -->

Footer.
"""
        self.readme_file.write_text(readme_content)

        # Generate stats
        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        stats.save_stats(str(self.stats_file))

        # Update README to have the correct content
        subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--update-readme",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=True
        )

        # Now check should pass
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--check",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )

        self.assertEqual(result.returncode, 0, f"should exit 0 when matched, stdout: {result.stdout}, stderr: {result.stderr}")
        self.assertIn("OK", result.stdout)

    def test_check_fails_when_readme_drifts(self):
        """--check should return 1 when README diverges from stats.json."""
        # Create stats.json
        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        stats.save_stats(str(self.stats_file))

        # Create a README with outdated/wrong stats
        outdated_markdown = """<!-- STATS:START -->

## Aesop builds itself

Outdated text here.

| Metric | Value |
| --- | --- |
| Merged PRs | 999 <!-- metrics-verified: self_stats.py (git log) --> |

<!-- STATS:END -->
"""
        readme_content = "# Project\n\n" + outdated_markdown + "\nFooter.\n"
        self.readme_file.write_text(readme_content)

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--check",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )

        self.assertNotEqual(result.returncode, 0, "should exit non-zero when drifted")
        self.assertIn("DRIFT", result.stdout)

    def test_check_passes_when_no_markers_exist(self):
        """--check should return 0 (no-op) when markers don't exist."""
        stats = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        stats.save_stats(str(self.stats_file))

        # Create README without markers
        self.readme_file.write_text("# Project\n\nNo stats markers.\n")

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--check",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )

        self.assertEqual(result.returncode, 0, "should exit 0 when no markers (graceful no-op)")


class StatsCheckFailClosedTest(SelfStatsFixtureCase):
    """Test --check mode fail-closed semantics (no tree mutation on missing/unreadable stats)."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        self.make_commit("initial")
        self.make_merge_commit(1)
        self.stats_file = self.repo_root / "stats.json"
        self.readme_file = self.repo_root / "README.md"

    def test_check_with_stats_json_missing_exits_1_no_mutation(self):
        """--check should NOT create stats.json when missing; exit 1 with MISSING error."""
        # Create a README with markers (so we're actually checking)
        readme_content = """# Project

<!-- STATS:START -->
placeholder
<!-- STATS:END -->

Footer.
"""
        self.readme_file.write_text(readme_content)

        # Verify stats.json doesn't exist
        self.assertFalse(self.stats_file.exists(), "test precondition: stats.json should not exist yet")

        # Run --check on missing stats.json
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--check",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )

        # Should exit 1
        self.assertNotEqual(result.returncode, 0, "should exit non-zero for missing stats.json")

        # Should have MISSING in stderr or stdout
        combined_output = result.stdout + result.stderr
        self.assertIn("MISSING", combined_output, "error message should include MISSING for missing file")

        # CRITICAL: stats.json must NOT be created (fail-closed, no tree mutation)
        self.assertFalse(self.stats_file.exists(), "stats.json must NOT be created by --check")

    def test_check_with_corrupted_stats_json_exits_1_no_mutation(self):
        """--check should NOT modify stats.json when corrupted; exit 1 with UNREADABLE error."""
        # Create corrupted stats.json
        corrupted_content = "{ invalid json }"
        self.stats_file.write_text(corrupted_content)

        # Create a README with markers
        readme_content = """# Project

<!-- STATS:START -->
placeholder
<!-- STATS:END -->

Footer.
"""
        self.readme_file.write_text(readme_content)

        # Record original file state
        original_bytes = self.stats_file.read_bytes()

        # Run --check on corrupted stats.json
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--check",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True
        )

        # Should exit 1
        self.assertNotEqual(result.returncode, 0, "should exit non-zero for unreadable stats.json")

        # Should have UNREADABLE in stderr or stdout
        combined_output = result.stdout + result.stderr
        self.assertIn("UNREADABLE", combined_output, "error message should include UNREADABLE for corrupted file")

        # CRITICAL: stats.json must NOT be modified (fail-closed, no tree mutation)
        self.assertEqual(
            self.stats_file.read_bytes(),
            original_bytes,
            "stats.json must NOT be modified by --check"
        )


class MergedPRsGhAndGitFallbackTest(SelfStatsFixtureCase):
    """Test merged_prs with gh API (preferred) and git fallback."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        self.make_commit("initial")
        # Create test commits with various PR formats
        self.make_merge_commit(1)  # Creates: Merge pull request #1
        # Also add squash-merge style commits
        test_file = self.repo_root / "test.txt"
        test_file.write_text("squash merge 2\n")
        subprocess.run(["git", "add", "test.txt"], cwd=str(self.repo_root), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "feature: some work (#2)"],
            cwd=str(self.repo_root),
            capture_output=True,
            check=True
        )
        # Another squash-merge commit
        test_file.write_text("squash merge 3\n")
        subprocess.run(["git", "add", "test.txt"], cwd=str(self.repo_root), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "refactor: cleanup (#3)"],
            cwd=str(self.repo_root),
            capture_output=True,
            check=True
        )

    def test_git_fallback_counts_both_merge_and_squash_styles(self):
        """Git fallback should count both merge-commit and squash-merge style PRs."""
        from unittest.mock import patch

        stats = self_stats.GitStats(repo_root=str(self.repo_root))

        # Mock subprocess.run to simulate gh not being available, forcing git fallback
        def run_side_effect(*args, **kwargs):
            # If gh is called, fail to force fallback
            if args and 'gh' in args[0]:
                raise FileNotFoundError("gh not found")
            # Git calls should work normally
            if args and 'git' in args[0]:
                if '--format=%s' in args[0]:
                    # Return our test commits
                    git_output = "Merge pull request #1 from test/branch\nfeature: some work (#2)\nrefactor: cleanup (#3)\n"
                    mock_result = type('Result', (), {'stdout': git_output, 'returncode': 0})()
                    return mock_result
            raise NotImplementedError(f"Unexpected subprocess call: {args}")

        with patch.object(self_stats.GitStats, '_origin_slug', return_value='example/fixture'):
            with patch('subprocess.run') as mock_run:
                mock_run.side_effect = run_side_effect
                stats._merged_prs = None

                count = stats.merged_prs

                # Should be 3 (PRs #1, #2, #3)
                self.assertEqual(count, 3, f"should count all 3 PRs (got {count})")
                self.assertEqual(stats.merged_prs_source, "git-log",
                                 "git fallback must record source 'git-log'")

    def test_git_fallback_counts_distinct_pr_numbers(self):
        """Git fallback should count distinct PR numbers (dedupe)."""
        from unittest.mock import patch

        stats = self_stats.GitStats(repo_root=str(self.repo_root))

        # Mock subprocess.run to force git fallback with dedup test data
        def run_side_effect(*args, **kwargs):
            # If gh is called, fail to force fallback
            if args and 'gh' in args[0]:
                raise FileNotFoundError("gh not found")
            # Git calls
            if args and 'git' in args[0]:
                if '--format=%s' in args[0]:
                    # Return output with PR #1 appearing in both formats
                    git_output = "Merge pull request #1 from test/branch\nfeature: some work (#1)\nrefactor: cleanup (#3)\n"
                    mock_result = type('Result', (), {'stdout': git_output, 'returncode': 0})()
                    return mock_result
            raise NotImplementedError(f"Unexpected subprocess call: {args}")

        with patch.object(self_stats.GitStats, '_origin_slug', return_value='example/fixture'):
            with patch('subprocess.run') as mock_run:
                mock_run.side_effect = run_side_effect
                stats._merged_prs = None

                count = stats.merged_prs

                # Should be 2 (PR #1 counted once, PR #3)
                self.assertEqual(count, 2, f"should dedupe PR numbers (got {count})")

    def test_merged_prs_with_gh_api_success(self):
        """When gh API succeeds with valid integer, should return the gh count."""
        from unittest.mock import patch

        stats = self_stats.GitStats(repo_root=str(self.repo_root))

        with patch.object(self_stats.GitStats, '_origin_slug', return_value='example/fixture'):
            with patch('subprocess.run') as mock_run:
                mock_result = type('Result', (), {'returncode': 0, 'stdout': '387\n'})()
                mock_run.return_value = mock_result

                stats._merged_prs = None
                count = stats.merged_prs

                # Should return 387 from gh
                self.assertEqual(count, 387, f"should return gh API count (got {count})")
                self.assertEqual(stats.merged_prs_source, "gh-api",
                                 "gh API path must record source 'gh-api'")

    def test_gh_not_attempted_without_github_origin(self):
        """Without a GitHub origin remote, gh must never be invoked (reproducible offline)."""
        from unittest.mock import patch
        import subprocess as real_subprocess

        # Fixture repo has no origin remote at all
        stats = self_stats.GitStats(repo_root=str(self.repo_root))

        gh_calls = []

        def run_side_effect(*args, **kwargs):
            if args and args[0] and args[0][0] == 'gh':
                gh_calls.append(args)
                raise AssertionError("gh must not be invoked when origin is not a GitHub repo")
            return real_subprocess.run(*args, **kwargs)

        with patch.object(self_stats, 'subprocess') as mock_subprocess:
            mock_subprocess.run.side_effect = run_side_effect
            mock_subprocess.TimeoutExpired = real_subprocess.TimeoutExpired
            stats._merged_prs = None

            count = stats.merged_prs

            self.assertEqual(gh_calls, [], "gh must be skipped without a GitHub origin")
            self.assertGreaterEqual(count, 3, "git fallback should still count fixture PRs")
            self.assertEqual(stats.merged_prs_source, "git-log")

    def test_merged_prs_falls_back_to_git_on_gh_failure(self):
        """When gh API fails (non-zero exit), should fall back to git count."""
        from unittest.mock import patch

        stats = self_stats.GitStats(repo_root=str(self.repo_root))

        call_count = [0]

        def run_side_effect(*args, **kwargs):
            call_count[0] += 1
            # First call is gh API (fail)
            if call_count[0] == 1:
                mock_result = type('Result', (), {'returncode': 1, 'stdout': ''})()
                return mock_result
            # Second call is git (succeed)
            if '--format=%s' in args[0]:
                mock_result = type('Result', (), {'stdout': 'Merge pull request #101\nfeature: work (#102)\n', 'returncode': 0})()
                return mock_result
            # Shouldn't reach here
            mock_result = type('Result', (), {'stdout': '', 'returncode': 0})()
            return mock_result

        with patch.object(self_stats.GitStats, '_origin_slug', return_value='example/fixture'):
            with patch('subprocess.run') as mock_run:
                mock_run.side_effect = run_side_effect
                stats._merged_prs = None

                count = stats.merged_prs

                # Should fall back and return git count (2 in this case)
                self.assertEqual(count, 2, f"should fall back to git on gh failure (got {count})")
                self.assertEqual(stats.merged_prs_source, "git-log",
                                 "fallback path must record source 'git-log'")

    def test_merged_prs_handles_gh_non_numeric_output(self):
        """When gh returns non-numeric output, should fall back to git."""
        from unittest.mock import patch

        stats = self_stats.GitStats(repo_root=str(self.repo_root))

        call_count = [0]

        def run_side_effect(*args, **kwargs):
            call_count[0] += 1
            # First call returns non-numeric output from gh
            if call_count[0] == 1:
                mock_result = type('Result', (), {'returncode': 0, 'stdout': 'invalid output\n'})()
                return mock_result
            # Fallback to git
            if '--format=%s' in args[0]:
                mock_result = type('Result', (), {'stdout': 'fix: bug (#50)\n', 'returncode': 0})()
                return mock_result
            mock_result = type('Result', (), {'stdout': '', 'returncode': 0})()
            return mock_result

        with patch.object(self_stats.GitStats, '_origin_slug', return_value='example/fixture'):
            with patch('subprocess.run') as mock_run:
                mock_run.side_effect = run_side_effect
                stats._merged_prs = None

                count = stats.merged_prs

                # Should fall back to git count
                self.assertEqual(count, 1, f"should fall back when gh output is invalid (got {count})")
                self.assertEqual(stats.merged_prs_source, "git-log")

    def test_merged_prs_handles_gh_timeout(self):
        """When gh times out, should fall back to git without raising."""
        from unittest.mock import patch

        stats = self_stats.GitStats(repo_root=str(self.repo_root))

        call_count = [0]

        def run_side_effect(*args, **kwargs):
            call_count[0] += 1
            # First call times out
            if call_count[0] == 1:
                raise subprocess.TimeoutExpired("gh", timeout=10)
            # Fallback to git
            if '--format=%s' in args[0]:
                mock_result = type('Result', (), {'stdout': 'fix: another bug (#75)\n', 'returncode': 0})()
                return mock_result
            mock_result = type('Result', (), {'stdout': '', 'returncode': 0})()
            return mock_result

        with patch.object(self_stats.GitStats, '_origin_slug', return_value='example/fixture'):
            with patch('subprocess.run') as mock_run:
                mock_run.side_effect = run_side_effect
                stats._merged_prs = None

                # Should not raise, should fall back to git
                count = stats.merged_prs
                self.assertEqual(count, 1, f"should handle gh timeout gracefully (got {count})")
                self.assertEqual(stats.merged_prs_source, "git-log")


class AuthorClassificationTest(unittest.TestCase):
    """Test author classification logic."""

    def test_classify_human_author(self):
        """Should classify human author by email."""
        classification, metadata = self_stats.classify_author("Matt Culliton", "matt82198@gmail.com")
        self.assertEqual(classification, "human")
        self.assertIsNone(metadata)

    def test_classify_multiple_human_identities_same_email(self):
        """Multiple names with same email should all be human."""
        for name in ["Matt Culliton", "AliceAdmin", "John \"Jack\" Doe"]:
            classification, metadata = self_stats.classify_author(name, "matt82198@gmail.com")
            self.assertEqual(classification, "human", f"{name} with matt82198@gmail.com should be human")

    def test_classify_model_anthropic_email(self):
        """Should classify model authors by noreply@anthropic.com email."""
        test_cases = [
            ("Claude Opus 4.8", "noreply@anthropic.com", "Opus 4.8"),
            ("Claude Haiku 4.5", "noreply@anthropic.com", "Haiku 4.5"),
            ("Claude Fable 5", "noreply@anthropic.com", "Fable 5"),
            ("Claude Opus 5.0", "noreply@anthropic.com", "Opus 5.0"),
        ]
        for name, email, expected_tier in test_cases:
            classification, metadata = self_stats.classify_author(name, email)
            self.assertEqual(classification, "model", f"{name} should be classified as model")
            self.assertEqual(metadata, expected_tier, f"{name} should extract tier as {expected_tier}, got {metadata}")

    def test_classify_model_aesop_email(self):
        """Should classify model authors by noreply@aesop email."""
        classification, metadata = self_stats.classify_author("Claude Fable 5", "noreply@aesop")
        self.assertEqual(classification, "model")
        self.assertEqual(metadata, "Fable 5")

    def test_normalize_model_tier_variants(self):
        """Should normalize model tier name variants."""
        test_cases = [
            ("Claude Opus 4.8 (1M context)", "noreply@anthropic.com", "Opus 4.8"),
            ("Claude Haiku 4.5 (preview)", "noreply@anthropic.com", "Haiku 4.5"),
            ("Claude Fable 5 (beta)", "noreply@anthropic.com", "Fable 5"),
        ]
        for name, email, expected_tier in test_cases:
            classification, metadata = self_stats.classify_author(name, email)
            self.assertEqual(classification, "model")
            self.assertEqual(metadata, expected_tier, f"Should normalize {name} to {expected_tier}")

    def test_classify_bot(self):
        """Should classify bot authors by [bot] in name."""
        classification, metadata = self_stats.classify_author("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
        self.assertEqual(classification, "bot")
        self.assertIsNone(metadata)

    def test_classify_junk_by_email(self):
        """Should classify junk by test email."""
        classification, metadata = self_stats.classify_author("Test User", "test@example.com")
        self.assertEqual(classification, "junk")
        self.assertIsNone(metadata)

    def test_classify_junk_by_aesop_open_source_email(self):
        """Should classify aesop@open-source as junk."""
        test_cases = [
            ("AliceAdmin", "aesop@open-source"),
            ("John \"Jack\" Doe", "aesop@open-source"),
            ("aesop", "aesop@open-source"),
        ]
        for name, email in test_cases:
            classification, metadata = self_stats.classify_author(name, email)
            self.assertEqual(classification, "junk", f"{name} with {email} should be junk")

    def test_classify_junk_by_generic_name(self):
        """Should classify generic bot/system names as junk."""
        test_cases = [
            ("aesop", "aesop@example.com"),
            ("Aesop Contributors", "aesop@example.com"),
        ]
        for name, email in test_cases:
            classification, metadata = self_stats.classify_author(name, email)
            self.assertEqual(classification, "junk", f"Generic name '{name}' should be junk")

    def test_extract_model_tier(self):
        """Should correctly normalize model tier names."""
        test_cases = [
            ("Claude Opus 4.8", "Opus 4.8"),
            ("Claude Opus 4.8 (1M context)", "Opus 4.8"),
            ("Claude Haiku 4.5", "Haiku 4.5"),
            ("Claude Fable 5", "Fable 5"),
            ("Claude Fable 5 (beta)", "Fable 5"),
            ("Opus 4.8", "Opus 4.8"),  # Already normalized
            ("Haiku 4.5 (preview)", "Haiku 4.5"),
        ]
        for input_name, expected_output in test_cases:
            result = self_stats.extract_model_tier(input_name)
            self.assertEqual(result, expected_output, f"Failed to normalize {input_name}")


class ClassifiedAuthorsGitStatsTest(SelfStatsFixtureCase):
    """Test classified author statistics (human, model, bot, junk)."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        # Configure repo with human email for tests
        subprocess.run(["git", "config", "user.email", "matt82198@gmail.com"], cwd=str(self.repo_root), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Matt Culliton"], cwd=str(self.repo_root), capture_output=True)

    def test_authors_human_single_email(self):
        """Should count distinct human emails (currently 1)."""
        # setUp already configured with matt82198@gmail.com
        self.make_commit("commit from Matt")

        # Simulate another identity with same email via coauthor trailer
        self.make_commit("second commit", coauthor="Alice Admin <matt82198@gmail.com>")

        stats = self_stats.GitStats(repo_root=str(self.repo_root))
        self.assertEqual(stats.authors_human, 1, "should count distinct human emails, not names")

    def test_model_tiers_multiple(self):
        """Should count distinct model tiers."""
        self.make_commit("commit 1")
        self.make_commit("commit 2", coauthor="Claude Haiku 4.5 <noreply@anthropic.com>")
        self.make_commit("commit 3", coauthor="Claude Opus 4.8 <noreply@anthropic.com>")
        self.make_commit("commit 4", coauthor="Claude Fable 5 <noreply@anthropic.com>")

        stats = self_stats.GitStats(repo_root=str(self.repo_root))
        self.assertEqual(stats.model_tiers, 3, "should count 3 distinct model tiers")
        self.assertEqual(set(stats.model_tier_names), {"Haiku 4.5", "Opus 4.8", "Fable 5"})

    def test_model_tiers_normalize_variants(self):
        """Should deduplicate model tier variants (e.g., Opus 4.8 with/without context hint)."""
        self.make_commit("commit 1")
        self.make_commit("commit 2", coauthor="Claude Opus 4.8 <noreply@anthropic.com>")
        self.make_commit("commit 3", coauthor="Claude Opus 4.8 (1M context) <noreply@anthropic.com>")

        stats = self_stats.GitStats(repo_root=str(self.repo_root))
        self.assertEqual(stats.model_tiers, 1, "should deduplicate Opus 4.8 variants")
        self.assertEqual(stats.model_tier_names, ["Opus 4.8"])

    def test_model_tier_names_sorted(self):
        """Should return model tier names in sorted order."""
        self.make_commit("commit 1")
        self.make_commit("commit 2", coauthor="Claude Fable 5 <noreply@anthropic.com>")
        self.make_commit("commit 3", coauthor="Claude Haiku 4.5 <noreply@anthropic.com>")
        self.make_commit("commit 4", coauthor="Claude Opus 4.8 <noreply@anthropic.com>")

        stats = self_stats.GitStats(repo_root=str(self.repo_root))
        self.assertEqual(stats.model_tier_names, ["Fable 5", "Haiku 4.5", "Opus 4.8"])

    def test_json_includes_classified_fields(self):
        """JSON output should include authors_human, model_tiers, model_tier_names."""
        self.make_commit("commit 1")
        self.make_commit("commit 2", coauthor="Claude Haiku 4.5 <noreply@anthropic.com>")

        counter = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        output = counter.json()
        data = json.loads(output)

        self.assertIn("authors_human", data["git"])
        self.assertIn("model_tiers", data["git"])
        self.assertIn("model_tier_names", data["git"])
        self.assertGreaterEqual(data["git"]["authors_human"], 1)
        self.assertEqual(data["git"]["model_tiers"], 1)
        self.assertEqual(data["git"]["model_tier_names"], ["Haiku 4.5"])

    def test_markdown_renders_classified_authors(self):
        """Markdown output should render authors as 'N human + M Claude model tiers'."""
        self.make_commit("commit 1")
        self.make_commit("commit 2", coauthor="Claude Haiku 4.5 <noreply@anthropic.com>")
        self.make_commit("commit 3", coauthor="Claude Opus 4.8 <noreply@anthropic.com>")

        counter = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        output = counter.markdown()

        # Should contain the new format
        self.assertIn("Authors", output, "should have 'Authors' row")
        self.assertIn("human", output, "should mention 'human'")
        self.assertIn("Claude model tier", output, "should mention 'Claude model tier'")
        # Verify the tiers count is correct
        self.assertIn("2 Claude model tier", output, "should list 2 model tiers")


class SingleSourceMergedPrTest(SelfStatsFixtureCase):
    """The merged-PR count must be single-sourced: one field, one definition,
    with the source ('gh-api' | 'git-log') recorded, and economics consuming
    the SAME count (never recomputing its own)."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        self.make_commit("initial")
        self.make_merge_commit(1)
        # Squash-style commit so union heuristic differs from merge-commit-only counting
        test_file = self.repo_root / "test.txt"
        test_file.write_text("squash work\n")
        subprocess.run(["git", "add", "test.txt"], cwd=str(self.repo_root), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: squash work (#2)"],
            cwd=str(self.repo_root), capture_output=True, check=True
        )
        self.stats_file = self.repo_root / "stats.json"

    def test_json_output_includes_source_field(self):
        """counter.json() must include git.merged_prs_source on the git block."""
        counter = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        data = json.loads(counter.json())
        self.assertIn("merged_prs_source", data["git"])
        self.assertIn(data["git"]["merged_prs_source"], ("gh-api", "git-log"))

    def test_regenerated_stats_source_is_git_log_without_gh(self):
        """Fixture repo has no GitHub origin, so regenerated stats must say source git-log."""
        counter = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        counter.save_stats(str(self.stats_file))
        with open(self.stats_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["git"]["merged_prs_source"], "git-log")
        # Union heuristic: PR #1 (merge commit) + PR #2 (squash) = 2
        self.assertEqual(data["git"]["merged_prs"], 2)

    def test_economics_consumes_same_merged_pr_count(self):
        """Economics block must carry the exact same merged-PR count as the git block."""
        counter = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        counter.save_stats(str(self.stats_file))
        with open(self.stats_file, encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("economics", data, "economics block should be present")
        econ = data["economics"]
        git_count = data["git"]["merged_prs"]

        econ_counts = []
        if "merged_prs" in econ:
            econ_counts.append(econ["merged_prs"])
        if isinstance(econ.get("cost_per_merged_pr"), dict):
            econ_counts.append(econ["cost_per_merged_pr"]["merged_prs"])
        self.assertTrue(econ_counts, "economics must expose the merged-PR count it used")
        for c in econ_counts:
            self.assertEqual(c, git_count,
                             "economics merged-PR count must equal git.merged_prs (single source)")
        # Source must be carried too
        self.assertEqual(econ.get("merged_prs_source"), data["git"]["merged_prs_source"])

    def test_regenerated_stats_include_head_sha(self):
        """Regenerated stats must record the HEAD sha for provenance."""
        counter = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        counter.save_stats(str(self.stats_file))
        with open(self.stats_file, encoding="utf-8") as f:
            data = json.load(f)

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(self.repo_root), capture_output=True, text=True, timeout=30
        ).stdout.strip()
        self.assertEqual(data.get("head_sha"), head)

    def test_no_zero_filler_economics_without_ledger(self):
        """With no token ledger, economics must NOT ship 0.0 token/cost fields."""
        counter = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        counter.save_stats(str(self.stats_file))
        with open(self.stats_file, encoding="utf-8") as f:
            data = json.load(f)

        econ = data.get("economics", {})
        self.assertIs(econ.get("token_ledger_available"), False,
                      "economics must explicitly mark the token ledger unavailable")
        flat = json.dumps(econ)
        for filler_key in ("tokens_per_loc", "tokens_per_pr", "tokens_per_wave",
                           "cost_per_backlog_item"):
            self.assertNotIn(filler_key, flat,
                             f"'{filler_key}' must be omitted when unmeasured, never 0.0 filler")


class StatsIntegrityValidationTest(SelfStatsFixtureCase):
    """validate_stats_integrity: contradiction, zero-filler, source, staleness."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        self.make_commit("initial")
        self.make_merge_commit(1)
        self.stats_file = self.repo_root / "stats.json"
        counter = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        counter.save_stats(str(self.stats_file))
        with open(self.stats_file, encoding="utf-8") as f:
            self.stats_dict = json.load(f)

    def _validate(self, d, **kwargs):
        kwargs.setdefault("repo_root", str(self.repo_root))
        return self_stats.validate_stats_integrity(d, **kwargs)

    def test_fresh_stats_pass_validation(self):
        errors = self._validate(self.stats_dict)
        self.assertEqual(errors, [], f"freshly regenerated stats must validate clean: {errors}")

    def test_pr_count_contradiction_detected(self):
        d = json.loads(json.dumps(self.stats_dict))
        d["economics"]["merged_prs"] = d["git"]["merged_prs"] + 100
        errors = self._validate(d)
        self.assertTrue(any("merged" in e.lower() and "contradiction" in e.lower() for e in errors),
                        f"contradicting PR counts must fail validation: {errors}")

    def test_full_economics_block_contradiction_detected(self):
        d = json.loads(json.dumps(self.stats_dict))
        d["economics"]["cost_per_merged_pr"] = {
            "merged_prs": d["git"]["merged_prs"] + 5,
            "total_tokens": 100,
            "tokens_per_pr": 1.0,
        }
        d["economics"]["token_ledger_available"] = True
        errors = self._validate(d)
        self.assertTrue(any("contradiction" in e.lower() for e in errors),
                        f"cost_per_merged_pr.merged_prs disagreeing must fail: {errors}")

    def test_zero_filler_economics_detected(self):
        """Old-style all-zero economics (total_tokens=0 with 0.0 ratios present) must fail."""
        d = json.loads(json.dumps(self.stats_dict))
        n = d["git"]["merged_prs"]
        d["economics"] = {
            "cost_per_loc": {"lines_of_code": 100, "total_tokens": 0, "tokens_per_loc": 0.0},
            "cost_per_merged_pr": {"merged_prs": n, "total_tokens": 0, "tokens_per_pr": 0.0},
            "cost_per_wave": {"wave_count": 3, "total_tokens": 0, "tokens_per_wave": 0.0},
            "unit_economics": {
                "cost_per_backlog_item": 0.0,
                "cost_per_wave_item": 0.0,
                "backlog_item_proxy": "merged_prs",
                "items_count": n,
            },
        }
        errors = self._validate(d)
        self.assertTrue(any("zero-filler" in e.lower() for e in errors),
                        f"0.0-but-present token metrics must fail validation: {errors}")

    def test_missing_source_field_detected(self):
        d = json.loads(json.dumps(self.stats_dict))
        d["git"].pop("merged_prs_source", None)
        errors = self._validate(d)
        self.assertTrue(any("merged_prs_source" in e for e in errors),
                        f"missing source field must fail validation: {errors}")

    def test_stale_generated_at_detected(self):
        d = json.loads(json.dumps(self.stats_dict))
        old = datetime.now(self_stats.timezone.utc) - timedelta(days=60)
        d["generated_at"] = old.isoformat()
        errors = self._validate(d)
        self.assertTrue(any("stale" in e.lower() and "regenerate" in e.lower() for e in errors),
                        f"generated_at 60 days old must fail with regenerate hint: {errors}")

    def test_generated_at_within_threshold_passes(self):
        d = json.loads(json.dumps(self.stats_dict))
        recent = datetime.now(self_stats.timezone.utc) - timedelta(days=2)
        d["generated_at"] = recent.isoformat()
        errors = self._validate(d, max_age_days=14)
        self.assertEqual(errors, [], f"2-day-old stats within 14-day window must pass: {errors}")

    def _current_commit_count(self):
        out = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(self.repo_root), capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return int(out)

    def test_commit_lag_beyond_threshold_detected(self):
        d = json.loads(json.dumps(self.stats_dict))
        d["git"]["total_commits"] = self._current_commit_count() - 10
        errors = self._validate(d, max_commits_behind=5)
        self.assertTrue(any("stale" in e.lower() and "commit" in e.lower() for e in errors),
                        f"stats 10 commits behind with threshold 5 must fail: {errors}")

    def test_commit_lag_within_threshold_passes(self):
        d = json.loads(json.dumps(self.stats_dict))
        d["git"]["total_commits"] = self._current_commit_count() - 2
        errors = self._validate(d, max_commits_behind=5)
        self.assertEqual(errors, [], f"2 commits behind with threshold 5 must pass: {errors}")

    def test_shallow_clone_negative_delta_not_flagged(self):
        """If the recorded count exceeds the visible history (shallow clone), skip the commit check."""
        d = json.loads(json.dumps(self.stats_dict))
        d["git"]["total_commits"] = d["git"]["total_commits"] + 1000
        errors = self._validate(d, max_commits_behind=5)
        self.assertEqual([e for e in errors if "commit" in e.lower()], [],
                         f"shallow-clone negative delta must not be flagged stale: {errors}")


class StatsCheckIntegrityCliTest(SelfStatsFixtureCase):
    """--check must fail (exit 1) on internal contradiction or stale receipts,
    even when README matches stats.json byte-for-byte."""

    def setUp(self):
        super().setUp()
        os.chdir(str(self.repo_root))
        self.make_commit("initial")
        self.make_merge_commit(1)
        self.stats_file = self.repo_root / "stats.json"
        self.readme_file = self.repo_root / "README.md"

        self.readme_file.write_text(
            "# Project\n\n<!-- STATS:START -->\nplaceholder\n<!-- STATS:END -->\n\nFooter.\n",
            encoding="utf-8",
        )
        counter = self_stats.StatsCounter(repo_root=str(self.repo_root), data_file=str(self.data_file))
        counter.save_stats(str(self.stats_file))
        subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--update-readme",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root), capture_output=True, text=True, timeout=120, check=True,
        )

    def _run_check(self):
        return subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--check",
             "--repo", str(self.repo_root),
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root), capture_output=True, text=True, timeout=120,
        )

    def _mutate_stats(self, mutator):
        with open(self.stats_file, encoding="utf-8") as f:
            data = json.load(f)
        mutator(data)
        with open(self.stats_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def test_check_passes_on_fresh_consistent_stats(self):
        result = self._run_check()
        self.assertEqual(result.returncode, 0,
                         f"fresh consistent stats must pass --check; stdout={result.stdout} stderr={result.stderr}")

    def test_check_fails_on_pr_count_contradiction(self):
        def mutate(data):
            data["economics"]["merged_prs"] = data["git"]["merged_prs"] + 42

        self._mutate_stats(mutate)
        # README still renders only the git block, so re-sync it to keep README matching
        subprocess.run(
            [sys.executable, str(TOOLS_DIR / "self_stats.py"), "--update-readme",
             "--stats-file", str(self.stats_file), "--readme", str(self.readme_file)],
            cwd=str(self.repo_root), capture_output=True, text=True, timeout=120, check=True,
        )
        result = self._run_check()
        self.assertNotEqual(result.returncode, 0, "contradicting PR counts must fail --check")
        combined = result.stdout + result.stderr
        self.assertIn("contradiction", combined.lower())

    def test_check_fails_on_stale_generated_at(self):
        def mutate(data):
            old = datetime.now(self_stats.timezone.utc) - timedelta(days=60)
            data["generated_at"] = old.isoformat()

        self._mutate_stats(mutate)
        result = self._run_check()
        self.assertNotEqual(result.returncode, 0, "stale stats.json must fail --check")
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("stale", combined)
        self.assertIn("regenerate", combined, "staleness failure must include a regenerate hint")


if __name__ == "__main__":
    unittest.main()
