#!/usr/bin/env python3
"""Tests for pre-push-policy.sh check_claudemd_sync integration.

Validates that the CLAUDE.md synchronization check in the pre-push hook:
1. Detects drift when domain code changes lack corresponding CLAUDE.md updates (via gate)
2. Passes when CLAUDE.md is properly updated (via gate)
3. Fails gracefully when tool is missing
"""

import subprocess
import tempfile
import os
import unittest
from pathlib import Path


class TestPrePushClaudemdSync(unittest.TestCase):
    """Test check_claudemd_sync() integration in pre-push-policy.sh"""

    def setUp(self):
        """Set up test fixtures."""
        self.hook_script = Path(__file__).parent.parent / "hooks" / "pre-push-policy.sh"

    def test_hook_fails_gracefully_when_tool_missing(self):
        """Test that hook fails gracefully (fail-open) when claudemd_sync_gate.py is missing.

        When the tool is absent, the hook should log skip event and allow push.
        This verifies the fail-open behavior for optional tooling.
        """
        # Verify the hook script contains the fail-open pattern
        hook_content = self.hook_script.read_text()

        # Check that tool-missing path logs skip event and returns 0
        self.assertIn("claudemd_sync_skipped_tool_missing", hook_content,
                     "Hook must skip gracefully when tool is missing")
        self.assertIn("check_claudemd_sync", hook_content,
                     "Hook must call check_claudemd_sync function")
        self.assertIn("log_event", hook_content,
                     "Hook must log skip events")

    def test_hook_calls_check_claudemd_sync_from_main(self):
        """Test that main() calls check_claudemd_sync before exit."""
        hook_content = self.hook_script.read_text()

        # Verify the check is called from main
        self.assertIn("if ! check_claudemd_sync", hook_content,
                     "main() must call check_claudemd_sync")
        self.assertIn("claudemd_sync_failure", hook_content,
                     "main() must log claudemd_sync_failure on failure")
        self.assertIn("log_block \"claudemd_sync_failure\"", hook_content,
                     "Hook must block push with claudemd_sync_failure reason")

    def test_hook_has_check_claudemd_sync_function_defined(self):
        """Test that check_claudemd_sync() function is properly defined."""
        hook_content = self.hook_script.read_text()

        # Verify function is defined
        self.assertIn("check_claudemd_sync()", hook_content,
                     "check_claudemd_sync() function must be defined")

        # Verify it resolves python binary
        self.assertIn("resolve_py_bin", hook_content,
                     "Function must resolve python binary")

        # Verify it runs the gate script
        self.assertIn("claudemd_sync_gate.py", hook_content,
                     "Function must run claudemd_sync_gate.py")

        # Verify it checks exit code
        self.assertIn("sync_exit_code", hook_content,
                     "Function must capture gate exit code")


class TestClaudemdSyncGateIntegration(unittest.TestCase):
    """Integration test: verify claudemd_sync_gate.py behavior matches expectations."""

    def setUp(self):
        """Set up test fixtures."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name)
        self.gate_script = Path(__file__).parent.parent / "tools" / "claudemd_sync_gate.py"

    def tearDown(self):
        """Clean up."""
        self.tmpdir.cleanup()

    def _init_repo_with_git(self):
        """Initialize a git repo with origin/main branch."""
        subprocess.run(
            ["git", "init"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )

    def test_gate_returns_drift_on_code_without_claudemd(self):
        """Verify gate exits 1 when code changes lack CLAUDE.md updates."""
        self._init_repo_with_git()

        # Create initial main branch
        (self.repo_root / "tools").mkdir(exist_ok=True)
        (self.repo_root / "tools/CLAUDE.md").write_text("# tools\n")
        subprocess.run(
            ["git", "add", "tools/CLAUDE.md"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "branch", "-M", "main"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )

        # Create feature branch with code change but no CLAUDE.md update
        subprocess.run(
            ["git", "checkout", "-b", "feature/drift"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        (self.repo_root / "tools/example.py").write_text("# code\n")
        subprocess.run(
            ["git", "add", "tools/example.py"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "add code"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )

        # Run gate check
        result = subprocess.run(
            [
                "python3" if os.name != "nt" else "python",
                str(self.gate_script),
                "--check"
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True
        )

        # Should exit 1 (drift found)
        self.assertEqual(result.returncode, 1, f"Expected exit 1, got {result.returncode}. stderr: {result.stderr}")
        self.assertIn("drift", result.stdout.lower(), f"Expected 'drift' in output: {result.stdout}")

    def test_gate_returns_clean_when_claudemd_updated(self):
        """Verify gate exits 0 when CLAUDE.md is properly updated."""
        self._init_repo_with_git()

        # Create initial main branch
        (self.repo_root / "tools").mkdir(exist_ok=True)
        (self.repo_root / "tools/CLAUDE.md").write_text("# tools\n")
        subprocess.run(
            ["git", "add", "tools/CLAUDE.md"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "branch", "-M", "main"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )

        # Create feature branch with both code AND CLAUDE.md update
        subprocess.run(
            ["git", "checkout", "-b", "feature/synced"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        (self.repo_root / "tools/example.py").write_text("# code\n")
        subprocess.run(
            ["git", "add", "tools/example.py"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "add code"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )

        # Now update CLAUDE.md
        (self.repo_root / "tools/CLAUDE.md").write_text("# tools\n- example.py\n")
        subprocess.run(
            ["git", "add", "tools/CLAUDE.md"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "update doc"],
            cwd=self.repo_root,
            capture_output=True,
            check=True
        )

        # Run gate check
        result = subprocess.run(
            [
                "python3" if os.name != "nt" else "python",
                str(self.gate_script),
                "--check"
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True
        )

        # Should exit 0 (clean)
        self.assertEqual(result.returncode, 0, f"Expected exit 0, got {result.returncode}. stdout: {result.stdout}, stderr: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
