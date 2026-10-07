#!/usr/bin/env python3
"""
TDD test suite for receipt spool location resolution.
Tests that spool survives worktree removal via environment overrides and git common dir.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(_TOOLS_DIR))

import receipt_common as rc


class TestSpoolLocationResolver(unittest.TestCase):
    """Test spool location resolution with proper precedence."""

    def setUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="test_spool_"))

    def tearDown(self):
        if self.temp_root.exists():
            shutil.rmtree(self.temp_root, ignore_errors=True)

    def _init_repo(self, repo_path):
        """Helper to initialize a git repo with proper configuration (scoped to temp via GIT_CONFIG_GLOBAL)."""
        import os
        repo_path.mkdir(exist_ok=True)
        subprocess.run(["git", "init"], cwd=str(repo_path), capture_output=True, check=True)

        # Use GIT_CONFIG_GLOBAL to isolate the test's git config
        env = os.environ.copy()
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        env["GIT_CONFIG_SYSTEM"] = "/dev/null"

        subprocess.run(["git", "config", "user.email", "test@example.com"],
                      cwd=str(repo_path), capture_output=True, check=True, env=env)
        subprocess.run(["git", "config", "user.name", "Test"],
                      cwd=str(repo_path), capture_output=True, check=True, env=env)

    def test_spool_location_env_override_wins(self):
        """Test that $AESOP_RECEIPT_SPOOL env var takes precedence."""
        env_spool = self.temp_root / "custom_spool"
        environ = {rc.SPOOL_ENV: str(env_spool)}
        
        result = rc.resolve_spool_dir(environ=environ)
        self.assertEqual(Path(result), env_spool)

    def test_spool_location_git_common_dir_in_worktree(self):
        """Test that git-common-dir is used inside a worktree (survives removal)."""
        # Create a real temporary git repo
        repo_root = self.temp_root / "real_repo"
        self._init_repo(repo_root)

        # Create initial commit
        (repo_root / "dummy.txt").write_text("dummy")
        subprocess.run(["git", "add", "dummy.txt"], cwd=str(repo_root), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_root), capture_output=True, check=True)

        # Create a worktree
        wt_path = self.temp_root / "worktree"
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            check=True
        )

        # Now resolve spool from the worktree (no env override)
        environ = {}
        result = rc.resolve_spool_dir(repo=str(wt_path), environ=environ)

        # The spool should be at git_common_dir/aesop-receipt-spool
        git_common = (
            subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=str(wt_path),
                capture_output=True,
                text=True,
                check=True
            ).stdout.strip()
        )
        expected = Path(git_common).resolve() / "aesop-receipt-spool"
        self.assertEqual(Path(result), expected)

        # Create a spool file
        spool_dir = Path(result)
        spool_dir.mkdir(parents=True, exist_ok=True)
        spool_file = spool_dir / "test.json"
        spool_file.write_text('{"test": "data"}')
        self.assertTrue(spool_file.exists())

        # Remove the worktree
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=str(repo_root),
            capture_output=True,
            check=True
        )

        # The spool file should still exist (survived worktree removal)
        self.assertTrue(spool_file.exists(), "Spool file should survive worktree removal")

    def test_spool_location_home_fallback(self):
        """Test that ~/.aesop/receipt-spool is used as fallback when no other source exists."""
        home_dir = self.temp_root / "fake_home"
        home_dir.mkdir()
        environ = {"HOME": str(home_dir)}
        
        # Repo without git common dir available
        result = rc.resolve_spool_dir(repo="/nonexistent", environ=environ)
        expected = home_dir / ".aesop" / "receipt-spool"
        self.assertEqual(Path(result), expected)

    def test_legacy_spool_path_still_readable(self):
        """Test that legacy <repo>/state/receipts/spool is still checked as a fallback source."""
        repo_root = self.temp_root / "legacy_repo"
        repo_root.mkdir()
        
        # Create legacy spool file
        legacy_spool = repo_root / "state" / "receipts" / "spool"
        legacy_spool.mkdir(parents=True, exist_ok=True)
        legacy_file = legacy_spool / "old.json"
        legacy_file.write_text('{"legacy": true}')
        
        # Get list of spool sources (primary + legacy fallback)
        sources = rc.get_spool_search_paths(repo=str(repo_root), environ={})
        
        # Legacy path should be in the sources
        legacy_path = Path(repo_root) / "state" / "receipts" / "spool"
        self.assertIn(legacy_path, sources, "Legacy spool path should be in search paths")


class TestSpoolIntegration(unittest.TestCase):
    """Integration test: write a spool file and verify it can be read from flush."""

    def setUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="test_spool_int_"))

    def tearDown(self):
        if self.temp_root.exists():
            shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_emit_and_flush_spool_roundtrip(self):
        """Test that emit can write to resolved spool and flush can find it."""
        home_dir = self.temp_root / "home"
        home_dir.mkdir()
        
        environ = {"HOME": str(home_dir)}
        spool_dir = rc.resolve_spool_dir(environ=environ)
        spool_path = Path(spool_dir) / "test_receipt.json"
        
        # Simulate emit writing to the spool
        spool_path.parent.mkdir(parents=True, exist_ok=True)
        test_envelope = {
            "receipt": {
                "head_sha": "1234567890abcdef",
                "repo": "test/repo",
            },
            "sig": {"scheme": "hmac-sha256", "value": "test"}
        }
        spool_path.write_text(json.dumps(test_envelope), encoding="utf-8")
        
        # Verify flush can find and read it
        spool_files = list(Path(spool_dir).glob("*.json"))
        self.assertEqual(len(spool_files), 1)
        self.assertEqual(spool_files[0], spool_path)
        
        # Read it back
        loaded = json.loads(spool_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["receipt"]["head_sha"], "1234567890abcdef")


if __name__ == "__main__":
    unittest.main()
