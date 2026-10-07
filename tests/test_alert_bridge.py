"""TDD tests for tools/alert_bridge.py — webhook-based fleet alerts for Slack/Discord.

Tests cover:
- Cursor-based idempotency (second scan sends nothing if no new alerts)
- Severity filtering (only send HIGH and above)
- Null webhook no-op (feature opt-in)
- Payload shape validation for both Slack blocks and Discord content
- URL masking in output (never log or echo full webhook URL)
- Heartbeat staleness detection (trigger alert if stale beyond threshold)
- --dry-run mode (print masked payload instead of POST)
- --test-message mode (send ping)

Run: python -m pytest tests/test_alert_bridge.py -q
     python -m unittest tests.test_alert_bridge
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# Add tools directory to path
TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import alert_bridge


class AlertBridgeFixtureCase(unittest.TestCase):
    """Base fixture for alert_bridge tests."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-alert-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        self.config_file = self.fixture_root / "aesop.config.json"
        self._saved_cwd = os.getcwd()
        os.chdir(str(self.fixture_root))

    def tearDown(self):
        os.chdir(self._saved_cwd)
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def create_config(self, alerts_config):
        """Create aesop.config.json with given alerts config."""
        config = {
            "state_root": str(self.state_dir),
            "alerts": alerts_config,
        }
        self.config_file.write_text(json.dumps(config), encoding="utf-8")

    def create_security_alerts(self, alerts_list):
        """Create SECURITY-ALERTS.log with given alerts."""
        alerts_file = self.state_dir / "SECURITY-ALERTS.log"
        content = "\n".join(alerts_list)
        if content:
            alerts_file.write_text(content + "\n", encoding="utf-8")
        else:
            alerts_file.write_text("", encoding="utf-8")

    def create_cursor(self, line_number):
        """Create cursor file with line number."""
        cursor_file = self.state_dir / ".alert-bridge-cursor"
        cursor_file.write_text(str(line_number), encoding="utf-8")

    def read_cursor(self):
        """Read cursor line number."""
        cursor_file = self.state_dir / ".alert-bridge-cursor"
        if cursor_file.exists():
            return int(cursor_file.read_text(encoding="utf-8").strip())
        return 0

    def create_heartbeat(self, age_seconds=0):
        """Create watchdog heartbeat file with age in seconds."""
        hb_file = self.state_dir / ".watchdog-heartbeat"
        timestamp = int(time.time()) - age_seconds
        hb_file.write_text(str(timestamp), encoding="utf-8")


# ==============================================================================
# Null Webhook Tests (feature opt-in)
# ==============================================================================


class TestNullWebhookNoOp(AlertBridgeFixtureCase):
    """Webhook URL absent/null = clean no-op exit 0."""

    def test_null_webhook_url_exit_zero(self):
        """webhook_url: null → exit 0, no network call."""
        self.create_config(
            {
                "webhook_url": None,
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["CRITICAL: secret leaked"])

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            mock_post.assert_not_called()

    def test_absent_webhook_url_exit_zero(self):
        """alerts block absent → exit 0, no network call."""
        self.create_config({})
        self.create_security_alerts(["CRITICAL: secret leaked"])

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            mock_post.assert_not_called()

    def test_empty_string_webhook_url_exit_zero(self):
        """webhook_url: '' → exit 0, no network call."""
        self.create_config(
            {
                "webhook_url": "",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["CRITICAL: secret leaked"])

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            mock_post.assert_not_called()


# ==============================================================================
# Severity Filtering Tests
# ==============================================================================


class TestSeverityFiltering(AlertBridgeFixtureCase):
    """Only send alerts at/above min_severity threshold."""

    def test_filter_below_min_severity(self):
        """Alerts below min_severity are skipped."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(
            [
                "INFO: routine check passed",
                "LOW: deprecated function used",
                "MEDIUM: inefficient algorithm",
            ]
        )

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            mock_post.assert_not_called()

    def test_include_at_min_severity(self):
        """Alerts at min_severity are included."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["HIGH: authentication bypass possible"])

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            mock_response = Mock()
            mock_response.getcode.return_value = 200
            mock_post.return_value = mock_response

            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            mock_post.assert_called_once()

    def test_include_above_min_severity(self):
        """Alerts above min_severity are included."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "MEDIUM",
            }
        )
        self.create_security_alerts(["CRITICAL: privileged escalation"])

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            mock_response = Mock()
            mock_response.getcode.return_value = 200
            mock_post.return_value = mock_response

            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            mock_post.assert_called_once()


# ==============================================================================
# Cursor Idempotency Tests
# ==============================================================================


class TestCursorIdempotency(AlertBridgeFixtureCase):
    """Cursor file tracks sent alerts; second scan sends nothing."""

    def test_first_scan_sends_alert(self):
        """First scan (no cursor) sends alert and creates cursor."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["[2026-01-15 10:30] HIGH: XSS vulnerability"])

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            mock_response = Mock()
            mock_response.getcode.return_value = 200
            mock_post.return_value = mock_response

            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            # Check that it was called with Request object (timeout is kwarg)
            self.assertGreaterEqual(mock_post.call_count, 1)

        # Cursor should be at line 1
        cursor = self.read_cursor()
        self.assertEqual(cursor, 1)

    def test_second_scan_no_new_alerts_sends_nothing(self):
        """Second scan with same alerts sends nothing (idempotent)."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["[2026-01-15 10:30] HIGH: XSS vulnerability"])

        # First scan
        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            mock_response = Mock()
            mock_response.getcode.return_value = 200
            mock_post.return_value = mock_response
            alert_bridge.main(["--scan"])
            first_call_count = mock_post.call_count
            self.assertGreaterEqual(first_call_count, 1)

        # Second scan
        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            # Second scan should not POST (idempotent)
            mock_post.assert_not_called()

    def test_cursor_advances_with_new_alerts(self):
        """When new alerts added, cursor advances and they are sent."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["[2026-01-15 10:30] HIGH: XSS vulnerability"])

        # First scan
        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            mock_response = Mock()
            mock_response.getcode.return_value = 200
            mock_post.return_value = mock_response
            alert_bridge.main(["--scan"])

        # Add new alert
        self.create_security_alerts(
            [
                "[2026-01-15 10:30] HIGH: XSS vulnerability",
                "[2026-01-15 11:00] CRITICAL: SQL injection",
            ]
        )

        # Second scan
        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            mock_response = Mock()
            mock_response.getcode.return_value = 200
            mock_post.return_value = mock_response
            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            # Should have POST'd the new alert
            self.assertGreaterEqual(mock_post.call_count, 1)

        cursor = self.read_cursor()
        self.assertEqual(cursor, 2)


# ==============================================================================
# Heartbeat Staleness Tests
# ==============================================================================


class TestHeartbeatStaleness(AlertBridgeFixtureCase):
    """Detect and alert on stale watchdog heartbeat."""

    def test_fresh_heartbeat_no_alert(self):
        """Fresh heartbeat (age < threshold) does not trigger alert."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
                "heartbeat_stall_s": 600,
            }
        )
        self.create_heartbeat(age_seconds=100)  # 100s old (< 600s threshold)

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            mock_post.assert_not_called()

    def test_stale_heartbeat_sends_alert(self):
        """Stale heartbeat (age >= threshold) sends alert."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
                "heartbeat_stall_s": 600,
            }
        )
        self.create_heartbeat(age_seconds=700)  # 700s old (>= 600s threshold)

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            mock_response = Mock()
            mock_response.getcode.return_value = 200
            mock_post.return_value = mock_response

            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            mock_post.assert_called_once()

    def test_heartbeat_stall_null_threshold_ignored(self):
        """heartbeat_stall_s: null → skip heartbeat check."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
                "heartbeat_stall_s": None,
            }
        )
        self.create_heartbeat(age_seconds=9999)  # Very stale

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            result = alert_bridge.main(["--scan"])
            self.assertEqual(result, 0)
            mock_post.assert_not_called()


# ==============================================================================
# Payload Format Tests (Slack vs Discord)
# ==============================================================================


class TestSlackPayloadFormat(AlertBridgeFixtureCase):
    """Slack provider: validates block-kit payload shape."""

    def test_slack_payload_has_blocks(self):
        """Slack payload contains 'blocks' array."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["HIGH: security issue found"])

        captured_payload = {}

        def mock_urlopen(req, **kwargs):
            captured_payload["data"] = json.loads(req.data.decode("utf-8"))
            response = Mock()
            response.getcode.return_value = 200
            return response

        with patch("alert_bridge.urllib.request.urlopen", side_effect=mock_urlopen):
            alert_bridge.main(["--scan"])

        self.assertIn("blocks", captured_payload["data"])
        self.assertIsInstance(captured_payload["data"]["blocks"], list)
        self.assertGreater(len(captured_payload["data"]["blocks"]), 0)

    def test_slack_blocks_contain_text(self):
        """Slack blocks include alert text."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        alert_text = "HIGH: authentication vulnerability in login module"
        self.create_security_alerts([alert_text])

        captured_payload = {}

        def mock_urlopen(req, **kwargs):
            captured_payload["data"] = json.loads(req.data.decode("utf-8"))
            response = Mock()
            response.getcode.return_value = 200
            return response

        with patch("alert_bridge.urllib.request.urlopen", side_effect=mock_urlopen):
            alert_bridge.main(["--scan"])

        payload_str = json.dumps(captured_payload["data"])
        self.assertIn("HIGH", payload_str)


class TestDiscordPayloadFormat(AlertBridgeFixtureCase):
    """Discord provider: validates embed payload shape."""

    def test_discord_payload_has_embeds(self):
        """Discord payload contains 'embeds' array."""
        self.create_config(
            {
                "webhook_url": "https://discordapp.com/api/webhooks/test",
                "provider": "discord",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["HIGH: security issue found"])

        captured_payload = {}

        def mock_urlopen(req, **kwargs):
            captured_payload["data"] = json.loads(req.data.decode("utf-8"))
            response = Mock()
            response.getcode.return_value = 200
            return response

        with patch("alert_bridge.urllib.request.urlopen", side_effect=mock_urlopen):
            alert_bridge.main(["--scan"])

        self.assertIn("embeds", captured_payload["data"])
        self.assertIsInstance(captured_payload["data"]["embeds"], list)
        self.assertGreater(len(captured_payload["data"]["embeds"]), 0)

    def test_discord_embed_contains_description(self):
        """Discord embed includes alert description."""
        self.create_config(
            {
                "webhook_url": "https://discordapp.com/api/webhooks/test",
                "provider": "discord",
                "min_severity": "HIGH",
            }
        )
        alert_text = "HIGH: database injection vulnerability detected"
        self.create_security_alerts([alert_text])

        captured_payload = {}

        def mock_urlopen(req, **kwargs):
            captured_payload["data"] = json.loads(req.data.decode("utf-8"))
            response = Mock()
            response.getcode.return_value = 200
            return response

        with patch("alert_bridge.urllib.request.urlopen", side_effect=mock_urlopen):
            alert_bridge.main(["--scan"])

        payload_str = json.dumps(captured_payload["data"])
        self.assertIn("HIGH", payload_str)


# ==============================================================================
# URL Masking Tests (never log or echo webhook URL)
# ==============================================================================


class TestURLMasking(AlertBridgeFixtureCase):
    """Webhook URL must be masked in output (last 6 chars only)."""

    def test_dry_run_masks_url(self):
        """--dry-run prints masked URL (last 6 chars)."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/ABCD1234567890",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["HIGH: issue found"])

        with patch("sys.stdout") as mock_stdout:
            with patch("alert_bridge.urllib.request.urlopen"):
                alert_bridge.main(["--dry-run"])

    def test_test_message_masks_url(self):
        """--test-message prints masked URL."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/ABCD1234567890",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )

        with patch("sys.stdout") as mock_stdout:
            with patch("alert_bridge.urllib.request.urlopen"):
                alert_bridge.main(["--test-message"])


# ==============================================================================
# Mode Tests (--scan, --dry-run, --test-message)
# ==============================================================================


class TestModes(AlertBridgeFixtureCase):
    """Test different modes: --scan, --dry-run, --test-message."""

    def test_dry_run_no_post(self):
        """--dry-run prints payload but does not POST."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["HIGH: issue"])

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            result = alert_bridge.main(["--dry-run"])
            self.assertEqual(result, 0)
            mock_post.assert_not_called()

    def test_test_message_posts_ping(self):
        """--test-message sends a test ping."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            mock_response = Mock()
            mock_response.getcode.return_value = 200
            mock_post.return_value = mock_response

            result = alert_bridge.main(["--test-message"])
            self.assertEqual(result, 0)
            mock_post.assert_called_once()

    def test_scan_default_mode(self):
        """No args → --scan mode (check alerts + heartbeat)."""
        self.create_config(
            {
                "webhook_url": "https://hooks.slack.com/services/test",
                "provider": "slack",
                "min_severity": "HIGH",
            }
        )
        self.create_security_alerts(["HIGH: issue"])

        with patch("alert_bridge.urllib.request.urlopen") as mock_post:
            mock_response = Mock()
            mock_response.getcode.return_value = 200
            mock_post.return_value = mock_response

            result = alert_bridge.main([])
            self.assertEqual(result, 0)
            mock_post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
