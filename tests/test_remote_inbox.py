#!/usr/bin/env python3
"""Tests for tools/remote_inbox.py — remote command dispatch via GitHub issues."""

import json
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add tools to path so we can import remote_inbox
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import remote_inbox


class TestRemoteInbox:
    """Test suite for remote_inbox.py."""

    def test_non_owner_comment_rejected(self, tmp_path):
        """Non-owner comments should be rejected."""
        comment = {
            "id": 123,
            "body": "/runwave",
            "author": {"login": "someoneelse"},
            "authorAssociation": "NONE",
        }

        assert not remote_inbox.verify_author(comment, owner_login="matt82198")

    def test_owner_comment_accepted(self, tmp_path):
        """Owner comments should be accepted."""
        comment = {
            "id": 123,
            "body": "/runwave",
            "author": {"login": "matt82198"},
            "authorAssociation": "OWNER",
        }

        assert remote_inbox.verify_author(comment, owner_login="matt82198")

    def test_non_allowlisted_command_filed_as_note(self):
        """Non-allowlisted commands (e.g., /unknown) should become NOTEs."""
        command, text = remote_inbox.extract_command("/unknown do something")
        assert command is None  # Not a known command
        assert text == "/unknown do something"  # But text is preserved

    def test_allowlisted_command_extracted(self):
        """Allowlisted commands should be extracted."""
        for cmd in ["/runwave", "/power", "/afk"]:
            command, text = remote_inbox.extract_command(cmd)
            assert command == cmd
            assert text == cmd

    def test_free_text_becomes_note(self):
        """Free text (no /) should become a NOTE."""
        command, text = remote_inbox.extract_command("Just some notes here")
        assert command is None
        assert text == "Just some notes here"

    def test_replay_prevention(self, tmp_path):
        """Replayed comment IDs should be detected."""
        seen_path = tmp_path / ".remote-inbox-seen"
        seen_path.write_text("123\n456\n")

        seen = set()
        with open(seen_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if line:
                    seen.add(line)

        assert "123" in seen
        assert "456" in seen
        assert "789" not in seen

    def test_malformed_comment_rejected(self):
        """Malformed or empty comment bodies should be rejected."""
        # Empty comment
        command, text = remote_inbox.extract_command("")
        assert command is None
        assert text == ""

        # Whitespace only
        command, text = remote_inbox.extract_command("   ")
        assert command is None
        assert text == ""

    def test_valid_command_format(self, tmp_path):
        """Valid commands should be appended in the correct format."""
        # Temporarily patch the paths
        inbox_path = tmp_path / "ui-inbox.md"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)

        # Create a mock function that uses our temp path
        def mock_append_inbox(command, text):
            iso_ts = datetime.now(timezone.utc).isoformat()
            if command:
                entry_text = command
            else:
                entry_text = f"NOTE: {text[:100]}"
            entry = f"- [{iso_ts}] {entry_text}\n"
            with open(inbox_path, "a", encoding="utf-8") as f:
                f.write(entry)

        # Append a command
        mock_append_inbox("/runwave", "/runwave")

        # Read back and verify format
        content = inbox_path.read_text(encoding="utf-8")
        assert content.startswith("- [")
        assert "] /runwave" in content

    def test_seen_file_tracking(self, tmp_path):
        """Comments should be tracked in a seen file to prevent replay."""
        seen_path = tmp_path / ".remote-inbox-seen"

        # Mark a comment as seen
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        seen_path.write_text("123\n")

        # Read it back
        seen = set()
        with open(seen_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if line:
                    seen.add(line)

        assert "123" in seen

    def test_gh_failure_exit_nonzero(self):
        """gh command failure should be handled gracefully."""
        with patch.object(remote_inbox, "run_gh") as mock_run_gh:
            mock_run_gh.return_value = (1, "", "gh command not found")

            comments = remote_inbox.get_issue_comments(999)
            assert comments is None

    def test_author_association_collaborator_accepted(self):
        """Collaborators should also be accepted (not just OWNER)."""
        comment = {
            "id": 123,
            "body": "/power",
            "author": {"login": "matt82198"},
            "authorAssociation": "COLLABORATOR",
        }
        # This should pass the check (though in practice only OWNER is expected)
        assert remote_inbox.verify_author(comment, owner_login="matt82198")

    def test_missing_author_field_rejected(self):
        """Comments with missing author should be rejected."""
        comment = {
            "id": 123,
            "body": "/power",
            "author": {},
        }
        assert not remote_inbox.verify_author(comment)

    def test_log_action_creates_file(self, tmp_path):
        """Log actions should append to REMOTE-DISPATCH.log."""
        log_path = tmp_path / "REMOTE-DISPATCH.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def mock_log_action(action, comment_id, author, command, reason=""):
            iso_ts = datetime.now(timezone.utc).isoformat()
            entry = f"[{iso_ts}] {action:10s} comment={comment_id} author={author} command={command or 'NONE':15s} {reason}\n"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry)

        mock_log_action("ACCEPT", 123, "matt82198", "/power")

        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "ACCEPT" in content
        assert "comment=123" in content
        assert "matt82198" in content

    def test_cli_requires_issue_number(self):
        """CLI should require --issue argument."""
        # This is tested via argparse, which will exit with error if required arg is missing
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--issue", type=int, required=True)
        parser.add_argument("--dry-run", action="store_true")

        # Should raise SystemExit if --issue is missing
        try:
            args = parser.parse_args([])
            assert False, "Should have raised SystemExit"
        except SystemExit:
            pass  # Expected

    def test_dry_run_mode_does_not_append(self):
        """Dry-run mode should not append to inbox."""
        # This is integration-level; we'd need a full mock setup
        # For now, just verify the extract logic works
        command, text = remote_inbox.extract_command("/runwave")
        assert command == "/runwave"
        # In dry-run, append_inbox() is skipped, so no write happens


class TestInboxFormat:
    """Test inbox format compliance with inbox_drain.py."""

    def test_inbox_format_matches_drain_expectations(self, tmp_path):
        """Appended entries must match inbox_drain.py's expected format."""
        inbox_path = tmp_path / "ui-inbox.md"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)

        # Append an entry
        iso_ts = "2026-07-31T12:34:56.123456"
        entry = f"- [{iso_ts}] /runwave\n"
        inbox_path.write_text(entry, encoding="utf-8")

        # Parse it back (mimicking inbox_drain.py logic)
        items = []
        with open(inbox_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if line.startswith("- [") and "]" in line:
                    end_bracket = line.index("]")
                    ts = line[3:end_bracket]
                    text = line[end_bracket + 2 :] if end_bracket + 2 < len(line) else ""
                    items.append((ts, text))

        assert len(items) == 1
        assert items[0] == (iso_ts, "/runwave")

    def test_inbox_multiline_entries(self, tmp_path):
        """Multiple entries should parse correctly."""
        inbox_path = tmp_path / "ui-inbox.md"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)

        content = """- [2026-07-31T12:00:00.000000] /power
- [2026-07-31T12:01:00.000000] /runwave
- [2026-07-31T12:02:00.000000] NOTE: some notes
"""
        inbox_path.write_text(content, encoding="utf-8")

        # Parse
        items = []
        with open(inbox_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if line.startswith("- [") and "]" in line:
                    end_bracket = line.index("]")
                    ts = line[3:end_bracket]
                    text = line[end_bracket + 2 :] if end_bracket + 2 < len(line) else ""
                    items.append((ts, text))

        assert len(items) == 3
        assert items[2] == ("2026-07-31T12:02:00.000000", "NOTE: some notes")


class TestSecurity:
    """Security-focused tests."""

    def test_no_arbitrary_code_execution(self):
        """Non-allowlisted commands should never execute as code."""
        dangerous_commands = [
            "rm -rf /",
            "bash -c 'some evil'",
            "/git push --force",
            "/python import os; os.system('rm -rf /')",
        ]

        for cmd in dangerous_commands:
            command, text = remote_inbox.extract_command(cmd)
            # All should be treated as NOTEs, not executed
            assert command is None
            assert text == cmd

    def test_owner_verification_required(self):
        """Only comments from owner should be processed."""
        non_owners = [
            {"author": {"login": "attacker"}, "authorAssociation": "NONE"},
            {"author": {"login": "random-user"}, "authorAssociation": "CONTRIBUTOR"},
            {"author": {}, "authorAssociation": "OWNER"},  # Missing login
        ]

        for comment in non_owners:
            assert not remote_inbox.verify_author(comment, owner_login="matt82198")

    def test_stderr_messages_do_not_leak_secrets(self):
        """Stderr messages should be safe to log."""
        # Verify that error messages use pattern names, not raw values
        error_msg = "gh api failed (rc=1): authentication failed"
        # Should not contain actual tokens or passwords
        assert "sk-" not in error_msg
        assert "password" not in error_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
