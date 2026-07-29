#!/usr/bin/env python3
"""
Tests for agent_prompt_hygiene.py gate.

TDD: Write failing tests first.
"""

import sys
import tempfile
import subprocess
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


def test_clean_prompt_passes():
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

        assert exit_code == 0, f"Clean file should pass; got exit {exit_code}\nstderr: {stderr}"
        assert "passed" in stdout.lower() or "OK" in stdout
        print("[PASS] test_clean_prompt_passes")


def test_grep_for_api_key_fails():
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

        assert exit_code == 1, f"Should fail on grep for API key; got exit {exit_code}"
        assert "FAIL" in stderr or "forbidden" in stderr.lower()
        print("[PASS] test_grep_for_api_key_fails")


def test_find_env_file_fails():
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

        assert exit_code == 1, f"Should fail on .env search; got exit {exit_code}"
        assert "FAIL" in stderr
        print("[PASS] test_find_env_file_fails")


def test_token_hunting_pattern_fails():
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

        assert exit_code == 1, f"Should fail on token hunting; got exit {exit_code}"
        assert "FAIL" in stderr
        print("[PASS] test_token_hunting_pattern_fails")


def test_no_prompt_files_fails():
    """Directory with no prompt files should fail."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exit_code, stdout, stderr = run_gate(tmpdir)

        assert exit_code == 1, f"No prompt files should fail; got exit {exit_code}"
        assert "No prompt" in stderr or "not found" in stderr.lower()
        print("[PASS] test_no_prompt_files_fails")


def test_help_flag():
    """--help flag should exit 0."""
    result = subprocess.run(
        [sys.executable, "tools/agent_prompt_hygiene.py", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )

    assert result.returncode == 0, f"--help should exit 0; got {result.returncode}"
    assert "Usage" in result.stdout or "usage" in result.stdout.lower()
    print("[PASS] test_help_flag")


def test_multiple_files_all_clean():
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

        assert exit_code == 0, f"All clean files should pass; got exit {exit_code}\nstderr: {stderr}"
        assert "2" in stdout  # Should report 2 files
        print("[PASS] test_multiple_files_all_clean")


def test_grep_for_secret_fails():
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

        assert exit_code == 1, f"Should fail on grep for secrets; got exit {exit_code}"
        assert "FAIL" in stderr
        print("[PASS] test_grep_for_secret_fails")


def test_split_line_api_key_bypass_fails():
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

        assert exit_code == 1, f"Split-line credential pattern should fail; got exit {exit_code}"
        assert "FAIL" in stderr
        print("[PASS] test_split_line_api_key_bypass_fails")


def test_rephrased_credential_hunting_fails():
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

        assert exit_code == 1, f"Rephrased credential hunting should fail; got exit {exit_code}"
        assert "FAIL" in stderr
        print("[PASS] test_rephrased_credential_hunting_fails")


def test_policy_documentation_not_flagged():
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

        assert exit_code == 0, f"Policy doc should pass; got exit {exit_code}\nstderr: {stderr}"
        assert "passed" in stdout.lower() or "OK" in stdout
        print("[PASS] test_policy_documentation_not_flagged")


def test_hygiene_ok_comment_suppresses_line():
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

        assert exit_code == 0, f"hygiene-ok comment should suppress; got exit {exit_code}\nstderr: {stderr}"
        assert "passed" in stdout.lower() or "OK" in stdout
        print("[PASS] test_hygiene_ok_comment_suppresses_line")


def test_driver_wave_loop_scanned():
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
        assert exit_code == 0, f"Should scan driver files; got exit {exit_code}\nstderr: {stderr}"
        assert "2" in stdout  # Should report 2 files (dummy.md + wave_loop.py)
        print("[PASS] test_driver_wave_loop_scanned")


def main():
    """Run all tests."""
    tests = [
        test_clean_prompt_passes,
        test_grep_for_api_key_fails,
        test_find_env_file_fails,
        test_token_hunting_pattern_fails,
        test_no_prompt_files_fails,
        test_help_flag,
        test_multiple_files_all_clean,
        test_grep_for_secret_fails,
        test_split_line_api_key_bypass_fails,
        test_rephrased_credential_hunting_fails,
        test_policy_documentation_not_flagged,
        test_hygiene_ok_comment_suppresses_line,
        test_driver_wave_loop_scanned,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {test.__name__} failed: {e}")
            failed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__} error: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
