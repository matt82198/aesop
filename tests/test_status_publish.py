"""Publish-path tests rebased onto the current secret-gist API.

status_publish was retargeted from PUBLIC GitHub issues to SECRET GISTS mid-development
(the aesop repo is public, so an "issue" target would have made every fleet-status
snapshot world-readable). The publish entry point became
publish_to_gist(payload, gist_id, dry_run=False), dropping the old issue_num/as_comment
keywords.

TestDryRun, TestIdempotence, and TestGhFailure below exercise that gist path: a
run_command() stand-in is injected via unittest.mock.patch so no test ever shells out
to a real `gh` (the harness blocks it anyway) -- each fake runner records the argv it
was called with and answers only `gh gist view` / `gh gist edit`, raising on anything
else. These classes previously carried an unconditional @unittest.skip and had no real
assertions against this API; they are now live.
"""
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

import hashlib
import io
import sys
import unittest
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


class TestRedaction(unittest.TestCase):
    """Test redaction of secrets and paths."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_redact_token_pattern(self):
        """Test that token-like patterns are redacted."""
        # Build GitHub token without literal pattern in source
        # Requires 36+ chars after "ghp-" to match the pattern
        # Assembled at runtime: a literal 36-char suffix here trips the repo's own
        # secret scanner (generic_secret_assignment). Concat-assembly is the documented
        # convention for dummy secrets in tests.
        _prefix = "".join([chr(103), chr(104), chr(112), chr(45)])
        _suffix = "xyzabcdefghijklm" + "nopqrstuvwxyz" + "ABCDEFG"  # 36 chars total
        token = _prefix + _suffix
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


class TestDryRun(unittest.TestCase):
    """Test --dry-run output against the current gist publish path."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

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
        """Test that build_payload() assembles the expected markdown payload."""
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

    @patch('status_publish.run_command')
    def test_dry_run_prints_payload_and_makes_no_gh_call(self, mock_run):
        """--dry-run prints the payload and never shells out to gh (no network)."""
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            result = status_publish.publish_to_gist(
                "# Fleet Status\nhello", gist_id="abc123", dry_run=True
            )
        finally:
            sys.stdout = old_stdout

        assert result is True
        assert "Fleet Status" in buf.getvalue()
        # dry-run must skip BOTH the visibility check and the gist edit
        mock_run.assert_not_called()

    @patch('status_publish.run_command')
    def test_dry_run_output_is_redacted(self, mock_run):
        """Dry-run must print the redacted payload, not the raw one."""
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            status_publish.publish_to_gist(
                "Home dir is /home/matt8/aesop", gist_id="abc123", dry_run=True
            )
        finally:
            sys.stdout = old_stdout

        assert "/home/matt8" not in buf.getvalue()
        assert "[REDACTED_HOME]" in buf.getvalue()
        mock_run.assert_not_called()


class TestIdempotence(unittest.TestCase):
    """Publishing unchanged content must not re-edit the gist; publishing
    changed content must edit the SAME gist again -- never create a new one."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)
        self.state_dir = self.tmp_path / 'state'
        self.state_dir.mkdir()
        self.last_publish_file = self.state_dir / '.status-publish-last'

    def tearDown(self):
        self._td.cleanup()

    @staticmethod
    def _fake_runner(calls):
        """Injected stand-in for status_publish.run_command.

        Records every argv it is called with (for assertions) and answers
        the two gh subcommands the publish path actually issues. Hits no
        network/process -- this is the "inject a runner" seam the real gh
        invocation goes through.
        """
        def _run(cmd, timeout=10):
            calls.append(list(cmd))
            if cmd[:3] == ['gh', 'gist', 'view']:
                return (json.dumps({"isPublic": False}), 0)
            if cmd[:3] == ['gh', 'gist', 'edit']:
                return ("", 0)
            raise AssertionError(f"unexpected command in idempotence test: {cmd}")
        return _run

    def test_unchanged_payload_skips_update(self):
        """Second publish of IDENTICAL content must not call `gh gist edit` again."""
        payload = "# Fleet Status\nNo changes"
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()[:8]
        self.last_publish_file.write_text(payload_hash, encoding='utf-8')

        calls = []
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            with patch('status_publish.AESOP_STATE_ROOT', self.state_dir), \
                 patch('status_publish.LAST_PUBLISH_FILE', self.last_publish_file), \
                 patch('status_publish.redact_payload', return_value=payload), \
                 patch('status_publish.run_command', side_effect=self._fake_runner(calls)):
                result = status_publish.publish_to_gist(
                    payload, gist_id="abc123", dry_run=False
                )
        finally:
            sys.stdout = old_stdout

        assert result is True
        assert "No changes" in buf.getvalue()
        edit_calls = [c for c in calls if c[:3] == ['gh', 'gist', 'edit']]
        assert edit_calls == [], f"unchanged content must skip the edit, got: {calls}"

    def test_changed_payload_edits_same_gist_not_a_new_one(self):
        """Two publishes of DIFFERENT content must both land as `gh gist edit`
        against the same gist id -- the code must never fall back to
        `gh gist create` on a repeat publish."""
        calls = []
        with patch('status_publish.AESOP_STATE_ROOT', self.state_dir), \
             patch('status_publish.LAST_PUBLISH_FILE', self.last_publish_file), \
             patch('status_publish.run_command', side_effect=self._fake_runner(calls)):
            status_publish.publish_to_gist("first payload", gist_id="abc123", dry_run=False)
            status_publish.publish_to_gist("second payload", gist_id="abc123", dry_run=False)

        edit_calls = [c for c in calls if c[:3] == ['gh', 'gist', 'edit']]
        create_calls = [c for c in calls if 'create' in c]
        assert create_calls == [], f"must never create a new gist, got argv: {calls}"
        assert len(edit_calls) == 2, f"expected 2 edits (one per changed publish), got: {calls}"
        assert all(c[3] == "abc123" for c in edit_calls), (
            f"both edits must target the same gist id, got: {edit_calls}"
        )


class TestGhFailure(unittest.TestCase):
    """gh failures must surface as a real exception the caller sees --
    never a silently-returned None/True (the vacuous-pass class of bug)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)
        self.state_dir = self.tmp_path / 'state'
        self.state_dir.mkdir()
        # Non-existent -- forces the idempotence check to miss and proceed to publish.
        self.last_publish_file = self.state_dir / 'nonexistent'

    def tearDown(self):
        self._td.cleanup()

    def test_gh_edit_raising_propagates(self):
        """run_command raising on the edit call must raise out of publish_to_gist,
        not swallow the error and return a falsy/None success value."""
        def _run(cmd, timeout=10):
            if cmd[:3] == ['gh', 'gist', 'view']:
                return (json.dumps({"isPublic": False}), 0)
            raise RuntimeError("gh not found")

        with patch('status_publish.LAST_PUBLISH_FILE', self.last_publish_file), \
             patch('status_publish.run_command', side_effect=_run):
            with pytest.raises(RuntimeError, match="Failed to update gist"):
                status_publish.publish_to_gist(
                    "payload", gist_id="abc123", dry_run=False
                )

    def test_gh_nonzero_exit_code_raises(self):
        """A non-zero `gh gist edit` exit code must raise, not return silently."""
        def _run(cmd, timeout=10):
            if cmd[:3] == ['gh', 'gist', 'view']:
                return (json.dumps({"isPublic": False}), 0)
            if cmd[:3] == ['gh', 'gist', 'edit']:
                return ("error", 1)
            raise AssertionError(f"unexpected command: {cmd}")

        with patch('status_publish.LAST_PUBLISH_FILE', self.last_publish_file), \
             patch('status_publish.run_command', side_effect=_run):
            with pytest.raises(RuntimeError, match="gh gist edit failed"):
                status_publish.publish_to_gist(
                    "payload", gist_id="abc123", dry_run=False
                )

    def test_public_gist_refused_before_any_edit(self):
        """Fail-closed: a PUBLIC gist must be refused before any edit is attempted."""
        calls = []

        def _run(cmd, timeout=10):
            calls.append(list(cmd))
            if cmd[:3] == ['gh', 'gist', 'view']:
                return (json.dumps({"isPublic": True}), 0)
            raise AssertionError("must not reach gh gist edit on a public gist")

        with patch('status_publish.LAST_PUBLISH_FILE', self.last_publish_file), \
             patch('status_publish.run_command', side_effect=_run):
            with pytest.raises(RuntimeError, match="PUBLIC"):
                status_publish.publish_to_gist(
                    "payload", gist_id="abc123", dry_run=False
                )

        assert all(c[:3] != ['gh', 'gist', 'edit'] for c in calls)


class TestCommandTimeout(unittest.TestCase):
    """Test subprocess timeout handling."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

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


@unittest.skip("publish path retargeted to secret gists; test references the removed issue_num API")
class TestPublishWithComment(unittest.TestCase):
    """Test --comment flag behavior."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

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
            status_publish.publish_to_gist(
                "payload", issue_num=42, as_comment=True, dry_run=False
            )

        # Verify gh issue comment was called (not edit)
        calls = mock_run.call_args_list
        # Should have called gh with 'comment' in the args
        assert len(calls) > 0
        call_str = str(calls[0])
        assert 'comment' in call_str or '42' in call_str  # issue number should be there


class TestConfigLoading(unittest.TestCase):
    """Test aesop.config.json loading."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_load_config_missing_file(self):
        tmp_path = self.tmp_path
        """Test that missing config returns empty dict."""
        import os
        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config = status_publish.load_config()
            assert config == {}
        finally:
            os.chdir(cwd)

    def test_load_config_valid_json(self):
        tmp_path = self.tmp_path
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

    def test_load_config_invalid_json(self):
        tmp_path = self.tmp_path
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
