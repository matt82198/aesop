#!/usr/bin/env python3
"""
Tests for agent_prompt_hygiene.py gate.

TDD: Write failing tests first.
"""

import sys
import tempfile
import subprocess
import unittest
from pathlib import Path


def run_gate(root_dir):
    """Run the hygiene gate and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "tools/agent_prompt_hygiene.py", root_dir],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    return result.returncode, result.stdout, result.stderr


class TestAgentPromptHygiene(unittest.TestCase):
    """Test suite for agent_prompt_hygiene.py gate."""

    def test_clean_prompt_passes(self):
        """Clean prompt file should pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a valid skills directory with a clean prompt
            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "test_skill.md").write_text(
                """# Test Skill

This is a valid skill prompt.

## Implementation

Do something useful here.
"""
            )

            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 0, f"Clean file should pass; got exit {exit_code}\nstderr: {stderr}")
            self.assertTrue("passed" in stdout.lower() or "OK" in stdout)

    def test_grep_for_api_key_fails(self):
        """Prompt suggesting grep for API keys should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "bad_skill.md").write_text(
                """# Bad Skill

Try to find the API key using grep:
grep -r api_key .env
"""
            )

            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 1, f"Should fail on grep for API key; got exit {exit_code}")
            self.assertTrue("FAIL" in stderr or "forbidden" in stderr.lower())

    def test_find_env_file_fails(self):
        """Prompt mentioning searching .env files should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "bad_skill.md").write_text(
                """# Bad Skill

Let's find the .env file and extract secrets.
find . -name .env
"""
            )

            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 1, f"Should fail on .env search; got exit {exit_code}")
            self.assertIn("FAIL", stderr)

    def test_token_hunting_pattern_fails(self):
        """Prompt containing token hunting pattern should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "bad_skill.md").write_text(
                """# Bad Skill

Let me hunt for secrets and tokens in the codebase.
Search for all token patterns.
"""
            )

            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 1, f"Should fail on token hunting; got exit {exit_code}")
            self.assertIn("FAIL", stderr)

    def test_no_prompt_files_fails(self):
        """Directory with no prompt files should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 1, f"No prompt files should fail; got exit {exit_code}")
            self.assertTrue("No prompt" in stderr or "not found" in stderr.lower())

    def test_help_flag(self):
        """--help flag should exit 0."""
        result = subprocess.run(
            [sys.executable, "tools/agent_prompt_hygiene.py", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        self.assertEqual(result.returncode, 0, f"--help should exit 0; got {result.returncode}")
        self.assertTrue("Usage" in result.stdout or "usage" in result.stdout.lower())

    def test_multiple_files_all_clean(self):
        """Multiple clean prompt files should all pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()

            for i in range(2):
                (skills_dir / f"skill_{i}.md").write_text(
                    f"""# Skill {i}

This is a clean skill prompt.

## Purpose

Do something useful.
"""
                )

            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 0, f"All clean files should pass; got exit {exit_code}\nstderr: {stderr}")
            self.assertIn("2", stdout)  # Should report 2 files

    def test_grep_for_secret_fails(self):
        """Prompt with grep for secrets should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "bad_skill.md").write_text(
                """# Bad Skill

To find secrets:
grep -r secret /path/to/code
"""
            )

            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 1, f"Should fail on grep for secrets; got exit {exit_code}")
            self.assertIn("FAIL", stderr)

    def test_split_line_api_key_bypass_fails(self):
        """Split-line credential pattern (api on one line, key on next) should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "bad_skill.md").write_text(
                """# Bad Skill

First, read the api
key .env file contents
"""
            )

            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 1, f"Split-line credential pattern should fail; got exit {exit_code}")
            self.assertIn("FAIL", stderr)

    def test_rephrased_credential_hunting_fails(self):
        """Rephrased credential hunting (cat the file) should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "bad_skill.md").write_text(
                """# Bad Skill

Let me cat the .env.local file and copy the value to clipboard.
"""
            )

            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 1, f"Rephrased credential hunting should fail; got exit {exit_code}")
            self.assertIn("FAIL", stderr)

    def test_policy_documentation_not_flagged(self):
        """Policy documentation (never grep -r secret) should NOT be flagged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "policy.md").write_text(
                """# Security Policy

You must NEVER grep -r secret in any file.
We do NOT hunt for API keys in .env files.
This is FORBIDDEN: search for credentials in the source tree.
"""
            )

            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 0, f"Policy doc should pass; got exit {exit_code}\nstderr: {stderr}")
            self.assertTrue("passed" in stdout.lower() or "OK" in stdout)

    def test_hygiene_ok_comment_suppresses_line(self):
        """# hygiene-ok inline comment should suppress line violation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "suppressed.md").write_text(
                """# Skill

This line is suppressed: grep -r api_key .env  # hygiene-ok
"""
            )

            exit_code, stdout, stderr = run_gate(tmpdir)

            self.assertEqual(exit_code, 0, f"hygiene-ok comment should suppress; got exit {exit_code}\nstderr: {stderr}")
            self.assertTrue("passed" in stdout.lower() or "OK" in stdout)

    def test_driver_wave_loop_scanned(self):
        """driver/wave_loop.py should be included in scan."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create driver directory with wave_loop.py
            driver_dir = tmpdir_path / "driver"
            driver_dir.mkdir()
            (driver_dir / "wave_loop.py").write_text(
                """# Valid driver code
def run():
    pass
"""
            )

            # Also create skills dir so gate doesn't fail for no files
            skills_dir = tmpdir_path / "skills"
            skills_dir.mkdir()
            (skills_dir / "dummy.md").write_text("# Dummy")

            exit_code, stdout, stderr = run_gate(tmpdir)

            # Should scan both files and pass
            self.assertEqual(exit_code, 0, f"Should scan driver files; got exit {exit_code}\nstderr: {stderr}")
            self.assertIn("2", stdout)  # Should report 2 files (dummy.md + wave_loop.py)


if __name__ == "__main__":
    unittest.main()
