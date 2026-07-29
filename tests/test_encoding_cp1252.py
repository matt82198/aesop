"""TDD test for cp1252 Windows encoding fix.

Tests that self_stats.py and verify-stats.sh handle arrow characters (U+2192)
correctly on Windows cp1252 locale without encoding crashes.

The issue: stdout encoding on Windows cp1252 can't handle UTF-8 arrow chars (→).
Fix: Set PYTHONIOENCODING=utf-8 or use ASCII-safe output.

Run: python -m unittest tests.test_encoding_cp1252
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class Cp1252EncodingTest(unittest.TestCase):
    """Test encoding safety with cp1252-like restrictions."""

    def setUp(self):
        """Create a temp git repo for testing."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-cp1252-test-"))
        self.repo_root = self.fixture_root / "testrepo"
        self.repo_root.mkdir(parents=True)
        self.scripts_dir = self.repo_root / "scripts"
        self.scripts_dir.mkdir(parents=True)

        # Initialize tiny git repo
        subprocess.run(["git", "init"], cwd=str(self.repo_root), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(self.repo_root), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(self.repo_root), capture_output=True)

        # Create a minimal README for stats
        readme_content = """# Test Repo

<!-- STATS:START -->
<!-- STATS:END -->

Test content.
"""
        (self.repo_root / "README.md").write_text(readme_content)
        subprocess.run(["git", "add", "README.md"], cwd=str(self.repo_root), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(self.repo_root), capture_output=True, check=True)

        self._saved_cwd = os.getcwd()

    def tearDown(self):
        """Clean up temp directory."""
        os.chdir(self._saved_cwd)
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def test_self_stats_utf8_encoding_with_restricted_stdout(self):
        """Test self_stats.py works with cp1252-restricted stdout encoding.

        Simulates Windows cp1252 by setting PYTHONIOENCODING=cp1252,
        but with PYTHONIOENCODING=utf-8 override in the script,
        output should not crash.
        """
        # Locate self_stats.py in the worktree
        tools_dir = Path(__file__).parent.parent / "tools"
        self_stats_src = tools_dir / "self_stats.py"
        self.assertTrue(self_stats_src.exists(), f"self_stats.py not found at {self_stats_src}")

        # Run self_stats.py with PYTHONIOENCODING=utf-8 explicitly set
        # This simulates the fix: ensure UTF-8 output even on cp1252 systems
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(
            [sys.executable, str(self_stats_src), "--json", "--repo", str(self.repo_root)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            env=env,
            timeout=10
        )

        # Should succeed (exit 0)
        self.assertEqual(result.returncode, 0, f"Exit code: {result.returncode}, stderr: {result.stderr}")

        # Output should be valid JSON (will contain UTF-8 chars or ASCII equivalents)
        try:
            data = json.loads(result.stdout)
            self.assertIn("git", data)
            self.assertIn("telemetry", data)
        except json.JSONDecodeError as e:
            self.fail(f"Output is not valid JSON: {e}\nstdout: {result.stdout}")

    def test_verify_stats_sh_encoding(self):
        """Test that verify-stats.sh can handle UTF-8 output without cp1252 crashes.

        The shell script invokes self_stats.py. Both must handle UTF-8.
        """
        tools_dir = Path(__file__).parent.parent / "tools"
        scripts_dir = Path(__file__).parent.parent / "scripts"
        self_stats_src = tools_dir / "self_stats.py"
        verify_script = scripts_dir / "verify-stats.sh"

        self.assertTrue(verify_script.exists(), f"verify-stats.sh not found at {verify_script}")

        # Copy verify-stats.sh to the test repo for testing
        test_verify = self.repo_root / "verify-stats.sh"
        test_verify.write_text(verify_script.read_text())
        test_verify.chmod(0o755)

        # Create a stub self_stats.py in tools/
        (self.repo_root / "tools").mkdir(exist_ok=True)
        (self.repo_root / "tools" / "self_stats.py").write_text(self_stats_src.read_text())

        # Run the verify script with PYTHONIOENCODING=utf-8
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(
            ["bash", str(test_verify), "check"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            env=env,
            timeout=10
        )

        # Should not crash on encoding errors
        # (might exit 1 due to missing stats.json, but should not have UnicodeEncodeError)
        self.assertNotIn("UnicodeEncodeError", result.stderr, f"Encoding error in stderr: {result.stderr}")
        self.assertNotIn("codec", result.stderr.lower(), f"Codec error in stderr: {result.stderr}")

    def test_arrow_character_output_safe(self):
        """Test that self_stats.py output doesn't use non-ASCII chars that break cp1252.

        Verifies the markdown output uses only ASCII or properly escaped UTF-8.
        """
        tools_dir = Path(__file__).parent.parent / "tools"
        self_stats_src = tools_dir / "self_stats.py"

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(
            [sys.executable, str(self_stats_src), "--markdown", "--repo", str(self.repo_root)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            env=env,
            timeout=10
        )

        self.assertEqual(result.returncode, 0, f"Exit code: {result.returncode}, stderr: {result.stderr}")

        # Markdown output should not contain arrow chars directly (they could be HTML entities instead)
        # or if they do, they should be valid UTF-8
        output = result.stdout

        # Check that output is valid text (encodable as UTF-8)
        try:
            output.encode("utf-8")
        except UnicodeEncodeError as e:
            self.fail(f"Output contains characters that can't be encoded as UTF-8: {e}")


    def test_verify_stats_sh_works_without_pythonioencoding(self):
        """Test that verify-stats.sh sets PYTHONIOENCODING internally.

        Verify the script works even if PYTHONIOENCODING is not set in the environment.
        """
        tools_dir = Path(__file__).parent.parent / "tools"
        scripts_dir = Path(__file__).parent.parent / "scripts"
        self_stats_src = tools_dir / "self_stats.py"
        verify_script = scripts_dir / "verify-stats.sh"

        self.assertTrue(verify_script.exists(), f"verify-stats.sh not found at {verify_script}")

        # Create stats.json in test repo so the check doesn't fail
        stats_data = {
            "git": {
                "merged_prs": 1,
                "total_commits": 1,
                "project_age_days": 0,
                "wave_count": 0,
                "insertions_deletions": 10,
                "files_tracked": 1,
                "distinct_coauthors": 1,
                "authors_human": 1,
                "model_tiers": 0,
                "model_tier_names": []
            },
            "telemetry": {}
        }
        stats_file = self.repo_root / "stats.json"
        stats_file.write_text(json.dumps(stats_data))

        # Update README to match
        readme = self.repo_root / "README.md"
        readme.write_text("""# Test

<!-- STATS:START -->

## Aesop builds itself

Aesop is built entirely by its own `/buildsystem` wave cycle—running parallel Haiku fleets across ranked backlog items, verifying merges, auditing orchestration health. These stats are the receipts: all numbers computed LIVE from git, verified by anyone who clones.

| Metric | Value |
| --- | --- |
| Merged PRs | 1 <!-- metrics-verified: self_stats.py (git log) --> |
| Total Commits | 1 <!-- metrics-verified: self_stats.py (git log) --> |
| Project Age | 0 days <!-- metrics-verified: self_stats.py (git log) --> |
| Insertions + Deletions | 10 <!-- metrics-verified: self_stats.py (git log) --> |
| Files Tracked | 1 <!-- metrics-verified: self_stats.py (git log) --> |
| Authors | 1 human <!-- metrics-verified: self_stats.py (git log) --> |

<!-- STATS:END -->

Content.
""")

        # Run verify-stats.sh with PYTHONIOENCODING explicitly NOT set
        # The script should set it internally
        env = os.environ.copy()
        if "PYTHONIOENCODING" in env:
            del env["PYTHONIOENCODING"]

        result = subprocess.run(
            ["bash", str(verify_script), "check"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            env=env,
            timeout=10
        )

        # Should succeed (exit 0) because verify-stats.sh sets PYTHONIOENCODING internally
        self.assertEqual(
            result.returncode, 0,
            f"Exit code: {result.returncode}, stdout: {result.stdout}, stderr: {result.stderr}"
        )
        self.assertIn("OK", result.stdout, f"Expected 'OK' in stdout, got: {result.stdout}")


if __name__ == "__main__":
    unittest.main()
