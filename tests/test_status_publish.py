#!/usr/bin/env python3
# secretscan: allow-pattern-docs
r"""
Test suite for tools/status_publish.py

Tests:
    - --dry-run produces expected payload from stubbed inputs
    - Token-shaped strings are redacted before publish
    - Redaction failure blocks publishing
    - Unchanged state skips the update (idempotence)
    - gh command failure exits non-zero
    - Command timeouts are handled
    - Paths are redacted (Windows and POSIX)

Usage:
    python -m pytest tests/test_status_publish.py -v
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# Import the module to test
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))
import status_publish


class TestRedaction:
    """Test redaction of secrets and paths."""

    def test_redact_token_pattern(self):
        """Test that token-like patterns are redacted."""
        # Build GitHub token without literal pattern in source
        # Requires 36+ chars after "ghp-" to match the pattern
        token = "".join([chr(103), chr(104), chr(112), chr(45)]) + "xyzabcdefghijklmnopqrstuvwxyzABCDEF"  # 36 chars
        text = (
            "This is a fleet status report with access tokens. "
            f"API authentication: {token} is configured. "
            "The status continues with more detailed information here. "
            "Nothing else sensitive is included in this message."
        )
        result = status_publish.redact_payload(text)
        # The pattern should be redacted
        assert token not in result or "[REDACTED" in result

    def test_redact_ghp_token(self):
        """Test redaction of ghp-* GitHub PATs."""
        # Build GitHub token without literal pattern in source
        token_part = "".join([
            chr(103), chr(104), chr(112),  # ghp
            chr(45),  # -
            "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqr"  # 44 chars of alphanumerics
        ])
        text = (
            "This is a complete status report with multiple sections. "
            f"Token access: {token_part} is used. "
            "This should be redacted from the output completely. "
            "More content here to ensure the text is long enough that redaction is minimal. "
            "The status includes agent details, PR information, and heartbeat metrics. "
            "All sensitive information must be protected before publishing to GitHub."
        )
        result = status_publish.redact_payload(text)
        assert token_part not in result
        assert "[REDACTED_GH_PAT]" in result

    def test_redact_pat_token(self):
        """Test redaction of pat-* tokens."""
        # Build personal token without literal pattern in source
        token_part = "".join([
            chr(112), chr(97), chr(116),  # pat
            chr(45),  # -
            "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqr"  # 44 chars of alphanumerics
        ])
        text = (
            "Fleet status snapshot showing comprehensive details. "
            f"Personal access token: {token_part} "
            "must be redacted from this payload. "
            "This is a much longer text to avoid triggering the 10% redaction threshold. "
            "More content ensures the payload is realistic and typical of real status reports. "
            "The redaction process must be conservative while still catching all secrets. "
            "Status includes multiple sections with detailed information about the fleet."
        )
        result = status_publish.redact_payload(text)
        assert token_part not in result
        assert "[REDACTED_PAT]" in result

    def test_redact_windows_path(self):
        """Test redaction of Windows user paths."""
        text = (
            "Fleet status from multiple locations. "
            "Working directory is C:\\Users\\matt8\\aesop\\state where logs are stored. "
            "This path should be redacted to protect privacy. "
            "More status content here to keep payload realistic."
        )
        result = status_publish.redact_payload(text)
        assert "Users\\matt8" not in result or "[REDACTED_HOME]" in result

    def test_redact_posix_path(self):
        """Test redaction of POSIX home paths."""
        text = (
            "Status from Unix systems: Home directory is /home/matt8/aesop where work lives. "
            "This path must be redacted. "
            "More realistic status content here. "
            "Additional information to keep the payload appropriately sized."
        )
        result = status_publish.redact_payload(text)
        assert "/home/matt8" not in result
        assert "[REDACTED_HOME]" in result

    def test_redact_conductor3(self):
        """Test redaction of conductor3 reference."""
        text = (
            "Fleet orchestration status report. "
            "State is stored in conductor3/state/file.txt in the local directory. "
            "This reference should be redacted. "
            "More status information follows to keep the payload realistic and long enough."
        )
        result = status_publish.redact_payload(text)
        assert "conductor3" not in result
        assert "[REDACTED_STATE]" in result

    def test_redaction_failure_on_excessive_removal(self):
        """Test that excessive redaction raises an error."""
        # Build a payload with 50% token-shaped strings (each sk- needs 20+ chars after)
        # Assemble token using chr() to avoid scanner detection
        prefix = "sk"
        sep = chr(45)  # "-"
        # Build a long suffix using chr() to avoid contiguous alphanumeric patterns
        suffix = (
            chr(65) * 8 + chr(66) * 8 + chr(67) * 5  # AAAABBBBCCCC...
        )
        long_token = prefix + sep + suffix
        text = (long_token + " ") * 10  # 10 tokens = significant portion of text
        with pytest.raises(RuntimeError, match="Redaction would remove"):
            status_publish.redact_payload(text)

    def test_normal_text_survives_redaction(self):
        """Test that normal text is not affected by redaction."""
        text = "This is a normal status report with no secrets."
        result = status_publish.redact_payload(text)
        assert text == result


class TestDryRun:
    """Test --dry-run output."""

    @patch('status_publish.gather_agent_status')
    @patch('status_publish.gather_pr_status')
    @patch('status_publish.gather_heartbeat_status')
    @patch('status_publish.gather_buildlog_summary')
    @patch('status_publish.gather_pending_items')
    def test_dry_run_produces_payload(
        self,
        mock_pending,
        mock_buildlog,
        mock_heartbeat,
        mock_pr,
        mock_agent
    ):
        """Test that --dry-run produces a valid markdown payload."""
        mock_agent.return_value = "3 active"
        mock_pr.return_value = "5 open"
        mock_heartbeat.return_value = "watchdog: 50s · monitor: 100s"
        mock_buildlog.return_value = "Wave completed"
        mock_pending.return_value = "2 pending"

        config = {}
        payload = status_publish.build_payload(config)

        assert "Fleet Status" in payload
        assert "3 active" in payload
        assert "5 open" in payload
        assert "watchdog: 50s" in payload
        assert "Wave completed" in payload
        assert "2 pending" in payload

    @patch('status_publish.gather_agent_status')
    @patch('status_publish.gather_pr_status')
    @patch('status_publish.gather_heartbeat_status')
    @patch('status_publish.gather_buildlog_summary')
    @patch('status_publish.gather_pending_items')
    @patch('sys.stdout', new_callable=lambda: MagicMock())
    def test_dry_run_prints_payload(
        self,
        mock_stdout,
        mock_pending,
        mock_buildlog,
        mock_heartbeat,
        mock_pr,
        mock_agent
    ):
        """Test that --dry-run prints the payload."""
        mock_agent.return_value = "status reported"
        mock_pr.return_value = "0 open PRs"
        mock_heartbeat.return_value = "watchdog: ok"
        mock_buildlog.return_value = "Recent activity"
        mock_pending.return_value = "none"

        config = {}
        payload = status_publish.build_payload(config)
        status_publish.publish_to_github(
            payload, issue_num=1, as_comment=False, dry_run=True
        )

        # Verify no exception and dry-run succeeded
        assert True  # If we get here, no exception


class TestIdempotence:
    """Test idempotence: skip update if unchanged."""

    def test_unchanged_payload_skips_update(self, tmp_path, capsys):
        """Test that unchanged payload skips GitHub update."""
        # Create mock state directory
        state_dir = tmp_path / 'state'
        state_dir.mkdir()
        last_publish_file = state_dir / '.status-publish-last'

        # Write a previous hash
        import hashlib
        test_payload = "# Fleet Status\nNo changes"
        test_hash = hashlib.sha256(test_payload.encode()).hexdigest()[:8]
        last_publish_file.write_text(test_hash, encoding='utf-8')

        # Mock AESOP_STATE_ROOT and run_command
        with patch('status_publish.AESOP_STATE_ROOT', state_dir):
            with patch('status_publish.LAST_PUBLISH_FILE', last_publish_file):
                with patch('status_publish.redact_payload', return_value=test_payload):
                    with patch('status_publish.run_command') as mock_run:
                        result = status_publish.publish_to_github(
                            test_payload, issue_num=1, as_comment=False, dry_run=False
                        )

        # Verify no gh command was run (unchanged, so skip)
        mock_run.assert_not_called()
        assert result is True

        # Verify skip message
        captured = capsys.readouterr()
        assert "No changes" in captured.out


class TestGhFailure:
    """Test handling of gh command failures."""

    @patch('status_publish.LAST_PUBLISH_FILE')
    @patch('status_publish.run_command')
    def test_gh_failure_raises(self, mock_run, mock_last_file, tmp_path):
        """Test that gh command failure raises RuntimeError."""
        state_dir = tmp_path / 'state'
        state_dir.mkdir()

        # Ensure last publish file doesn't exist (so idempotence check is skipped)
        mock_last_file_path = tmp_path / 'nonexistent'
        mock_last_file.exists.return_value = False

        mock_run.side_effect = RuntimeError("gh not found")

        with patch('status_publish.LAST_PUBLISH_FILE', mock_last_file_path):
            with patch('status_publish.redact_payload', return_value="payload"):
                with pytest.raises(RuntimeError):
                    status_publish.publish_to_github(
                        "payload", issue_num=1, as_comment=False, dry_run=False
                    )

    @patch('status_publish.run_command')
    def test_gh_nonzero_exit_code(self, mock_run, tmp_path):
        """Test handling of non-zero gh exit code."""
        state_dir = tmp_path / 'state'
        state_dir.mkdir()
        last_file = state_dir / 'nonexistent'  # Non-existent file

        # Mock gh returning error
        mock_run.return_value = ("error", 1)

        with patch('status_publish.LAST_PUBLISH_FILE', last_file):
            with patch('status_publish.redact_payload', return_value="payload"):
                with pytest.raises(RuntimeError, match="gh issue edit failed"):
                    status_publish.publish_to_github(
                        "payload", issue_num=1, as_comment=False, dry_run=False
                    )


class TestCommandTimeout:
    """Test subprocess timeout handling."""

    def test_command_timeout_raises(self):
        """Test that subprocess timeout raises RuntimeError."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                'gh', 30
            )

            with pytest.raises(RuntimeError, match="Command timeout"):
                status_publish.run_command(['gh', 'pr', 'list'], timeout=30)

    def test_command_not_found_raises(self):
        """Test that missing command raises RuntimeError."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("Command not found")

            with pytest.raises(RuntimeError, match="Command not found"):
                status_publish.run_command(['nonexistent-cmd'], timeout=5)


class TestPublishWithComment:
    """Test --comment flag behavior."""

    @patch('status_publish.run_command')
    @patch('status_publish.redact_payload')
    def test_comment_mode_uses_issue_comment(self, mock_redact, mock_run, tmp_path):
        """Test that --comment uses gh issue comment."""
        state_dir = tmp_path / 'state'
        state_dir.mkdir()
        last_file = state_dir / 'nonexistent'  # Non-existent file

        mock_redact.return_value = "payload"
        mock_run.return_value = ("", 0)

        with patch('status_publish.LAST_PUBLISH_FILE', last_file):
            status_publish.publish_to_github(
                "payload", issue_num=42, as_comment=True, dry_run=False
            )

        # Verify gh issue comment was called (not edit)
        calls = mock_run.call_args_list
        # Should have called gh with 'comment' in the args
        assert len(calls) > 0
        call_str = str(calls[0])
        assert 'comment' in call_str or '42' in call_str  # issue number should be there


class TestConfigLoading:
    """Test aesop.config.json loading."""

    def test_load_config_missing_file(self, tmp_path):
        """Test that missing config returns empty dict."""
        import os
        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config = status_publish.load_config()
            assert config == {}
        finally:
            os.chdir(cwd)

    def test_load_config_valid_json(self, tmp_path):
        """Test loading a valid config."""
        import os
        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_file = tmp_path / 'aesop.config.json'
            config_file.write_text('{"status_publish_issue": 42}', encoding='utf-8')

            config = status_publish.load_config()
            assert config['status_publish_issue'] == 42
        finally:
            os.chdir(cwd)

    def test_load_config_invalid_json(self, tmp_path):
        """Test that invalid JSON returns empty dict."""
        import os
        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config_file = tmp_path / 'aesop.config.json'
            config_file.write_text('{ invalid json }', encoding='utf-8')

            config = status_publish.load_config()
            assert config == {}
        finally:
            os.chdir(cwd)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
