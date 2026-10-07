#!/usr/bin/env python3
"""
Tests for tools/git_identity_check.py — git user identity validator.

TDD: These tests verify:
1. CLI argument parsing (--expect-name, --expect-email, --repo, --mode)
2. Config file reading (aesop.config.json 'identity' block)
3. Identity resolution via git command (local repo scope only)
4. Physical .git/config file verification (grep-based, not cache)
5. --warn mode (exit 0, print drift)
6. --fail mode (exit 1 on drift)
7. Precedence: CLI args > config file > git current value
"""

import unittest
import sys
import tempfile
import json
import subprocess
from pathlib import Path

# Add tools to path for import
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from git_identity_check import (
    get_identity_from_args,
    get_identity_from_config_file,
    get_git_identity,
    get_physical_git_identity,
    validate_identity,
    main,
)


class TestGetIdentityFromArgs(unittest.TestCase):
    """Test CLI argument parsing."""

    def test_parse_name_and_email(self):
        """Test parsing --expect-name and --expect-email."""
        args = ["--expect-name", "John Doe", "--expect-email", "john@example.com"]
        result = get_identity_from_args(args)
        self.assertEqual(result, ("John Doe", "john@example.com"))

    def test_parse_name_only(self):
        """Test parsing only --expect-name."""
        args = ["--expect-name", "John Doe"]
        result = get_identity_from_args(args)
        self.assertEqual(result, ("John Doe", None))

    def test_parse_email_only(self):
        """Test parsing only --expect-email."""
        args = ["--expect-email", "john@example.com"]
        result = get_identity_from_args(args)
        self.assertEqual(result, (None, "john@example.com"))

    def test_parse_no_identity_args(self):
        """Test when no identity args provided."""
        args = []
        result = get_identity_from_args(args)
        self.assertEqual(result, (None, None))

    def test_parse_with_repo_and_mode(self):
        """Test parsing with other args doesn't interfere."""
        args = [
            "--repo", "/some/repo",
            "--expect-name", "John Doe",
            "--mode", "fail",
        ]
        result = get_identity_from_args(args)
        self.assertEqual(result, ("John Doe", None))


class TestGetIdentityFromConfigFile(unittest.TestCase):
    """Test reading identity from config file."""

    def setUp(self):
        """Create temp directory for config files."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.tmpdir.name)

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def test_read_identity_from_config(self):
        """Test reading identity block from aesop.config.json."""
        config_file = self.config_dir / "aesop.config.json"
        config = {
            "identity": {
                "user_name": "Jane Doe",
                "user_email": "jane@example.com"
            }
        }
        config_file.write_text(json.dumps(config))

        result = get_identity_from_config_file(str(config_file))
        self.assertEqual(result, ("Jane Doe", "jane@example.com"))

    def test_read_config_with_name_only(self):
        """Test reading config with only user_name."""
        config_file = self.config_dir / "aesop.config.json"
        config = {
            "identity": {
                "user_name": "Jane Doe"
            }
        }
        config_file.write_text(json.dumps(config))

        result = get_identity_from_config_file(str(config_file))
        self.assertEqual(result, ("Jane Doe", None))

    def test_missing_config_file(self):
        """Test when config file does not exist."""
        result = get_identity_from_config_file(
            str(self.config_dir / "nonexistent.json")
        )
        self.assertEqual(result, (None, None))

    def test_missing_identity_block(self):
        """Test when config file has no identity block."""
        config_file = self.config_dir / "aesop.config.json"
        config = {"other": "value"}
        config_file.write_text(json.dumps(config))

        result = get_identity_from_config_file(str(config_file))
        self.assertEqual(result, (None, None))

    def test_empty_identity_block(self):
        """Test when identity block is empty."""
        config_file = self.config_dir / "aesop.config.json"
        config = {"identity": {}}
        config_file.write_text(json.dumps(config))

        result = get_identity_from_config_file(str(config_file))
        self.assertEqual(result, (None, None))


class TestGetGitIdentity(unittest.TestCase):
    """Test reading git identity via git config command."""

    def setUp(self):
        """Create temp git repo."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

        # Set user identity in the repo (use --local to not inherit global config)
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

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def _unset_git_config_name(self):
        """Helper: unset user.name in temp repo."""
        subprocess.run(
            ["git", "config", "--local", "--unset", "user.name"],
            cwd=str(self.repo),
            capture_output=True,
        )

    def _unset_git_config_email(self):
        """Helper: unset user.email in temp repo."""
        subprocess.run(
            ["git", "config", "--local", "--unset", "user.email"],
            cwd=str(self.repo),
            capture_output=True,
        )

    def test_get_git_identity(self):
        """Test reading identity from git config."""
        name, email = get_git_identity(str(self.repo))
        self.assertEqual(name, "Test User")
        self.assertEqual(email, "test@example.com")

    def test_get_git_identity_missing_name(self):
        """Test when git user.name not set."""
        self._unset_git_config_name()
        name, email = get_git_identity(str(self.repo))
        self.assertIsNone(name)
        self.assertEqual(email, "test@example.com")

    def test_get_git_identity_missing_email(self):
        """Test when git user.email not set."""
        self._unset_git_config_email()
        name, email = get_git_identity(str(self.repo))
        self.assertEqual(name, "Test User")
        self.assertIsNone(email)

    def test_get_git_identity_both_missing(self):
        """Test when neither user.name nor user.email set."""
        self._unset_git_config_name()
        self._unset_git_config_email()
        name, email = get_git_identity(str(self.repo))
        self.assertIsNone(name)
        self.assertIsNone(email)


class TestGetPhysicalGitIdentity(unittest.TestCase):
    """Test reading git identity directly from .git/config file."""

    def setUp(self):
        """Create temp git repo."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

        # Set user identity in the repo (use --local to not inherit global config)
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

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def _set_git_config_name(self, name):
        """Helper: set user.name in temp repo."""
        subprocess.run(
            ["git", "config", "user.name", name],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

    def test_get_physical_git_identity(self):
        """Test reading identity from .git/config file directly."""
        name, email = get_physical_git_identity(str(self.repo))
        self.assertEqual(name, "Test User")
        self.assertEqual(email, "test@example.com")

    def test_physical_identity_missing_config(self):
        """Test when .git/config doesn't exist."""
        bad_repo = self.repo / "nonexistent"
        name, email = get_physical_git_identity(str(bad_repo))
        self.assertIsNone(name)
        self.assertIsNone(email)

    def test_physical_identity_detects_drift(self):
        """Test that physical read catches cache drift."""
        # Modify git config via helper method
        self._set_git_config_name("New User")

        # Physical read should see new value
        name, email = get_physical_git_identity(str(self.repo))
        self.assertEqual(name, "New User")
        self.assertEqual(email, "test@example.com")


class TestValidateIdentity(unittest.TestCase):
    """Test identity validation logic."""

    def setUp(self):
        """Create temp git repo."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

        # Set user identity in the repo (use --local to not inherit global config)
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

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def test_validate_matching_identity(self):
        """Test validation passes when identity matches."""
        errors = validate_identity(
            str(self.repo),
            expected_name="Test User",
            expected_email="test@example.com"
        )
        self.assertEqual(errors, [])

    def test_validate_name_mismatch(self):
        """Test validation fails on name mismatch."""
        errors = validate_identity(
            str(self.repo),
            expected_name="Different User",
            expected_email="test@example.com"
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("name", errors[0].lower())

    def test_validate_email_mismatch(self):
        """Test validation fails on email mismatch."""
        errors = validate_identity(
            str(self.repo),
            expected_name="Test User",
            expected_email="other@example.com"
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("email", errors[0].lower())

    def test_validate_both_mismatch(self):
        """Test validation fails on both mismatches."""
        errors = validate_identity(
            str(self.repo),
            expected_name="Different User",
            expected_email="other@example.com"
        )
        self.assertEqual(len(errors), 2)

    def test_validate_none_expected_matches_any(self):
        """Test that None expected value skips validation."""
        errors = validate_identity(
            str(self.repo),
            expected_name=None,
            expected_email="test@example.com"
        )
        self.assertEqual(errors, [])

    def test_validate_physical_drift_detected(self):
        """Test that physical read detects config drift."""
        # Manually modify .git/config to create drift
        config_path = self.repo / ".git" / "config"
        content = config_path.read_text()
        content = content.replace(
            'name = Test User',
            'name = Drifted User'
        )
        config_path.write_text(content)

        errors = validate_identity(
            str(self.repo),
            expected_name="Test User",
            expected_email="test@example.com"
        )
        self.assertGreater(len(errors), 0)
        # Should mention drift or mismatch
        error_text = " ".join(errors).lower()
        self.assertTrue(
            "drift" in error_text or "mismatch" in error_text or "differs" in error_text
        )


class TestMainIntegration(unittest.TestCase):
    """Integration tests for main() function."""

    def setUp(self):
        """Create temp git repo and config."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        self.config_dir = self.repo

        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

        # Set user identity via helper in temp repo
        self._set_git_repo_identity("Test User", "test@example.com")

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def _set_git_repo_identity(self, name, email):
        """Helper: set user.name and user.email in temp repo."""
        subprocess.run(
            ["git", "config", "--local", "user.name", name],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "--local", "user.email", email],
            cwd=str(self.repo),
            capture_output=True,
            check=True,
        )

    def test_main_matching_identity_warn_mode(self):
        """Test main() with matching identity in warn mode."""
        args = [
            "--repo", str(self.repo),
            "--mode", "warn",
            "--expect-name", "Test User",
            "--expect-email", "test@example.com",
        ]
        exit_code = main(args)
        self.assertEqual(exit_code, 0)

    def test_main_matching_identity_fail_mode(self):
        """Test main() with matching identity in fail mode."""
        args = [
            "--repo", str(self.repo),
            "--mode", "fail",
            "--expect-name", "Test User",
            "--expect-email", "test@example.com",
        ]
        exit_code = main(args)
        self.assertEqual(exit_code, 0)

    def test_main_mismatch_warn_mode(self):
        """Test main() with mismatched identity in warn mode."""
        args = [
            "--repo", str(self.repo),
            "--mode", "warn",
            "--expect-name", "Different User",
            "--expect-email", "test@example.com",
        ]
        exit_code = main(args)
        self.assertEqual(exit_code, 0)  # warn mode always exits 0

    def test_main_mismatch_fail_mode(self):
        """Test main() with mismatched identity in fail mode."""
        args = [
            "--repo", str(self.repo),
            "--mode", "fail",
            "--expect-name", "Different User",
            "--expect-email", "test@example.com",
        ]
        exit_code = main(args)
        self.assertEqual(exit_code, 1)  # fail mode exits 1 on mismatch

    def test_main_with_config_file(self):
        """Test main() reading from config file."""
        config_file = self.config_dir / "aesop.config.json"
        config = {
            "identity": {
                "user_name": "Test User",
                "user_email": "test@example.com"
            }
        }
        config_file.write_text(json.dumps(config))

        args = [
            "--repo", str(self.repo),
            "--config", str(config_file),
            "--mode", "fail",
        ]
        exit_code = main(args)
        self.assertEqual(exit_code, 0)

    def test_main_cli_args_precedence(self):
        """Test that CLI args take precedence over config file."""
        config_file = self.config_dir / "aesop.config.json"
        config = {
            "identity": {
                "user_name": "Config User",
                "user_email": "config@example.com"
            }
        }
        config_file.write_text(json.dumps(config))

        # CLI args should override config
        args = [
            "--repo", str(self.repo),
            "--config", str(config_file),
            "--expect-name", "Test User",
            "--expect-email", "test@example.com",
            "--mode", "fail",
        ]
        exit_code = main(args)
        self.assertEqual(exit_code, 0)  # Should match git identity, not config


if __name__ == "__main__":
    unittest.main()
