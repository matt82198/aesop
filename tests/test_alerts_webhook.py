"""TDD tests for tools/alerts_webhook.py — stateless Slack/Discord webhook alerts.

Tests cover:
- Payload composition from fixture signals (heartbeats, merge-queue, exceptions, PRs)
- Slack vs Discord payload shapes
- Missing config skip (no URL = exit 0 with skip line)
- Network error tolerance (mock urlopen failures, warn + exit 0)
- --dry-run mode (print payload, no network)
- Timeout enforcement on every request
- UTF-8 encoding and ASCII source
- No credential hunting (silent skip if gh unavailable)

Run: python -m pytest tests/test_alerts_webhook.py -q
     python -m unittest tests.test_alerts_webhook
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, mock_open
import subprocess

# Add tools directory to path
TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# Import after adding to path
import alerts_webhook


class AlertsWebhookFixtureCase(unittest.TestCase):
    """Base fixture for alerts_webhook tests."""

    def setUp(self):
        """Set up temporary test fixture with state directory."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-webhook-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        self.config_file = self.fixture_root / "aesop.config.json"
        self._saved_cwd = os.getcwd()
        os.chdir(str(self.fixture_root))

    def tearDown(self):
        """Restore cwd and clean up fixture."""
        os.chdir(self._saved_cwd)
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def write_config(self, config_dict):
        """Helper to write aesop.config.json."""
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(config_dict, f)

    def write_heartbeat(self, age_seconds=0):
        """Helper to write heartbeat file with given age."""
        hb_file = self.state_dir / ".orchestrator-heartbeat"
        timestamp = time.time() - age_seconds
        hb_file.write_text(str(int(timestamp)), encoding="utf-8")

    def write_exceptions(self, exceptions_list):
        """Helper to write exceptions.jsonl file."""
        exc_file = self.state_dir / "exceptions.jsonl"
        for exc in exceptions_list:
            exc_file.write_text(json.dumps(exc) + "\n", encoding="utf-8")

    def write_merge_queue(self, queue_state):
        """Helper to write merge-queue/state.json."""
        queue_dir = self.state_dir / "merge-queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        queue_file = queue_dir / "state.json"
        with open(queue_file, "w", encoding="utf-8") as f:
            json.dump(queue_state, f)


class PayloadCompositionTests(AlertsWebhookFixtureCase):
    """Test payload composition from fixture signals."""

    def test_compose_payload_slack_format(self):
        """Test composing Slack-format status payload."""
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
            }
        }
        self.write_config(config)
        self.write_heartbeat(age_seconds=50)
        self.write_exceptions([{"timestamp": "2026-08-02T10:00:00Z", "message": "test error"}])

        payload = alerts_webhook.compose_payload(config["alerts"], self.state_dir)
        self.assertIsNotNone(payload)
        self.assertIn("blocks", payload)  # Slack uses blocks
        self.assertIsInstance(payload["blocks"], list)

    def test_compose_payload_discord_format(self):
        """Test composing Discord-format status payload."""
        config = {
            "alerts": {
                "webhook_url": "https://discord.com/api/webhooks/TEST",
                "style": "discord",
            }
        }
        self.write_config(config)
        self.write_heartbeat(age_seconds=30)

        payload = alerts_webhook.compose_payload(config["alerts"], self.state_dir)
        self.assertIsNotNone(payload)
        self.assertIn("embeds", payload)  # Discord uses embeds or content
        self.assertIsInstance(payload["embeds"], list)

    def test_compose_payload_includes_heartbeat_staleness(self):
        """Test that payload includes heartbeat staleness warnings."""
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
                "heartbeat_stall_threshold_s": 300,
            }
        }
        self.write_config(config)
        # Write stale heartbeat (age > threshold)
        self.write_heartbeat(age_seconds=400)

        payload = alerts_webhook.compose_payload(config["alerts"], self.state_dir)
        self.assertIsNotNone(payload)
        # Verify payload text or structure indicates staleness
        payload_str = json.dumps(payload)
        self.assertIn("stalled", payload_str.lower())

    def test_compose_payload_includes_exception_count(self):
        """Test that payload includes exception count."""
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
            }
        }
        self.write_config(config)
        exceptions = [
            {"timestamp": "2026-08-02T10:00:00Z", "message": "error 1"},
            {"timestamp": "2026-08-02T10:01:00Z", "message": "error 2"},
        ]
        self.write_exceptions(exceptions)

        payload = alerts_webhook.compose_payload(config["alerts"], self.state_dir)
        payload_str = json.dumps(payload)
        self.assertIn("2", payload_str)  # Should contain exception count


class MissingConfigTests(AlertsWebhookFixtureCase):
    """Test missing/invalid configuration handling."""

    def test_no_webhook_url_skips_gracefully(self):
        """Test that missing webhook_url causes clean exit 0 with skip message."""
        config = {"alerts": {}}
        self.write_config(config)

        with patch("sys.stderr", new_callable=MagicMock) as mock_stderr:
            with patch("sys.stdout", new_callable=MagicMock) as mock_stdout:
                exit_code = alerts_webhook.main(["--config", str(self.config_file)])
                self.assertEqual(exit_code, 0)  # Clean exit

    def test_no_config_file_skips_gracefully(self):
        """Test that missing config file causes clean exit 0."""
        # Don't write a config file
        exit_code = alerts_webhook.main(["--config", "nonexistent.json"])
        self.assertEqual(exit_code, 0)


class NetworkErrorToleranceTests(AlertsWebhookFixtureCase):
    """Test network error tolerance (warn + exit 0)."""

    @patch("urllib.request.urlopen")
    def test_network_error_warns_exits_zero(self, mock_urlopen):
        """Test that network errors warn and exit 0 (fail-open)."""
        mock_urlopen.side_effect = OSError("Connection refused")
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
            }
        }
        self.write_config(config)
        self.write_heartbeat(age_seconds=50)

        with patch("sys.stderr", new_callable=MagicMock) as mock_stderr:
            exit_code = alerts_webhook.main(["--config", str(self.config_file)])
            self.assertEqual(exit_code, 0)  # Fail-open: exit 0

    @patch("urllib.request.urlopen")
    def test_timeout_error_warns_exits_zero(self, mock_urlopen):
        """Test that timeout errors warn and exit 0."""
        import socket
        mock_urlopen.side_effect = socket.timeout("Request timeout")
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
            }
        }
        self.write_config(config)

        exit_code = alerts_webhook.main(["--config", str(self.config_file)])
        self.assertEqual(exit_code, 0)  # Fail-open: exit 0

    @patch("urllib.request.urlopen")
    def test_http_error_warns_exits_zero(self, mock_urlopen):
        """Test that HTTP errors warn and exit 0."""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 500, "Internal Server Error", {}, None
        )
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
            }
        }
        self.write_config(config)

        exit_code = alerts_webhook.main(["--config", str(self.config_file)])
        self.assertEqual(exit_code, 0)  # Fail-open: exit 0


class DryRunTests(AlertsWebhookFixtureCase):
    """Test --dry-run mode (print payload, no network)."""

    @patch("urllib.request.urlopen")
    def test_dry_run_prints_payload(self, mock_urlopen):
        """Test that --dry-run prints payload and doesn't call urlopen."""
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
            }
        }
        self.write_config(config)
        self.write_heartbeat(age_seconds=50)

        with patch("sys.stdout", new_callable=MagicMock) as mock_stdout:
            exit_code = alerts_webhook.main(
                ["--config", str(self.config_file), "--dry-run"]
            )
            self.assertEqual(exit_code, 0)
            mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_dry_run_no_network_call(self, mock_urlopen):
        """Test that --dry-run never calls urlopen."""
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
            }
        }
        self.write_config(config)
        mock_urlopen.side_effect = OSError("Should not be called!")

        exit_code = alerts_webhook.main(
            ["--config", str(self.config_file), "--dry-run"]
        )
        self.assertEqual(exit_code, 0)  # Succeeds because no network attempt


class TimeoutEnforcementTests(AlertsWebhookFixtureCase):
    """Test that timeout is enforced on every request."""

    @patch("urllib.request.urlopen")
    def test_post_request_includes_timeout(self, mock_urlopen):
        """Test that POST request includes timeout parameter."""
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
            }
        }
        self.write_config(config)
        self.write_heartbeat(age_seconds=50)
        mock_urlopen.return_value = MagicMock()
        mock_urlopen.return_value.status = 200

        alerts_webhook.main(["--config", str(self.config_file)])
        # Verify urlopen was called with timeout
        self.assertTrue(mock_urlopen.called)
        call_kwargs = mock_urlopen.call_args[1]
        self.assertIn("timeout", call_kwargs)
        self.assertIsInstance(call_kwargs["timeout"], (int, float))
        self.assertGreater(call_kwargs["timeout"], 0)


class GitCredentialSkipTests(AlertsWebhookFixtureCase):
    """Test gh command graceful skip if unavailable."""

    @patch("subprocess.run")
    def test_gh_command_skip_if_unavailable(self, mock_run):
        """Test that missing gh command is skipped gracefully."""
        mock_run.side_effect = FileNotFoundError("gh not found")
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
            }
        }
        self.write_config(config)

        # Should not crash
        exit_code = alerts_webhook.main(["--config", str(self.config_file)])
        self.assertEqual(exit_code, 0)


class EncodingTests(AlertsWebhookFixtureCase):
    """Test UTF-8 encoding and ASCII source."""

    def test_utf8_encoding_in_config_read(self):
        """Test that config is read with UTF-8 encoding."""
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
                "description": "Test with unicode: ✓",
            }
        }
        self.write_config(config)
        self.write_heartbeat(age_seconds=50)

        with patch("urllib.request.urlopen"):
            exit_code = alerts_webhook.main(["--config", str(self.config_file)])
            self.assertEqual(exit_code, 0)

    def test_utf8_encoding_in_request(self):
        """Test that POST request uses UTF-8 encoding."""
        config = {
            "alerts": {
                "webhook_url": "https://hooks.slack.com/services/TEST",
                "style": "slack",
            }
        }
        self.write_config(config)
        self.write_heartbeat(age_seconds=50)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = MagicMock()
            mock_urlopen.return_value.status = 200
            alerts_webhook.main(["--config", str(self.config_file)])

            # Verify data was encoded as UTF-8
            self.assertTrue(mock_urlopen.called)
            call_args = mock_urlopen.call_args
            if len(call_args[0]) > 1 or "data" in call_args[1]:
                data_arg = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("data")
                if data_arg is not None:
                    self.assertIsInstance(data_arg, bytes)


class SlackPayloadTests(AlertsWebhookFixtureCase):
    """Test Slack-specific payload structure."""

    def test_slack_payload_has_blocks(self):
        """Test that Slack payload has blocks structure."""
        config = {
            "alerts": {"style": "slack"},
        }
        payload = alerts_webhook.compose_payload(config["alerts"], self.state_dir)
        self.assertIn("blocks", payload)
        self.assertIsInstance(payload["blocks"], list)
        self.assertGreater(len(payload["blocks"]), 0)

    def test_slack_blocks_are_valid_structure(self):
        """Test that Slack blocks have required structure."""
        config = {
            "alerts": {"style": "slack"},
        }
        payload = alerts_webhook.compose_payload(config["alerts"], self.state_dir)
        for block in payload["blocks"]:
            self.assertIn("type", block)
            self.assertIsInstance(block["type"], str)


class DiscordPayloadTests(AlertsWebhookFixtureCase):
    """Test Discord-specific payload structure."""

    def test_discord_payload_has_embeds(self):
        """Test that Discord payload has embeds structure."""
        config = {
            "alerts": {"style": "discord"},
        }
        payload = alerts_webhook.compose_payload(config["alerts"], self.state_dir)
        self.assertIn("embeds", payload)
        self.assertIsInstance(payload["embeds"], list)

    def test_discord_embeds_have_title_and_description(self):
        """Test that Discord embeds have required fields."""
        config = {
            "alerts": {"style": "discord"},
        }
        payload = alerts_webhook.compose_payload(config["alerts"], self.state_dir)
        for embed in payload["embeds"]:
            # Embeds should have at least one of: title, description, fields
            has_content = (
                "title" in embed or "description" in embed or "fields" in embed
            )
            self.assertTrue(has_content)


class HeartbeatFacadeTests(AlertsWebhookFixtureCase):
    """The orchestrator heartbeat must be read through the StateAPI read facade.

    Regression guard for the stateapi_lint ratchet: this tool must never open
    .orchestrator-heartbeat directly, and must honour the facade's fail-closed
    contract (missing/unreadable heartbeat => stalled).
    """

    def test_heartbeat_read_goes_through_read_api_facade(self):
        """read_heartbeat_stalled delegates to ReadAPI.check_heartbeat_fresh."""
        fake_api = MagicMock()
        fake_api.check_heartbeat_fresh.return_value = True

        with patch.object(alerts_webhook, "ReadAPI", return_value=fake_api) as ctor:
            stalled = alerts_webhook.read_heartbeat_stalled(self.state_dir, 300)

        self.assertFalse(stalled)  # fresh => not stalled
        ctor.assert_called_once_with(self.state_dir)
        fake_api.check_heartbeat_fresh.assert_called_once_with(
            alerts_webhook.ORCHESTRATOR_HEARTBEAT, 300
        )

    def test_fresh_heartbeat_reports_active(self):
        """A recent heartbeat reports as active, not stalled."""
        self.write_heartbeat(age_seconds=10)
        self.assertFalse(alerts_webhook.read_heartbeat_stalled(self.state_dir, 300))

    def test_stale_heartbeat_reports_stalled(self):
        """A heartbeat past the threshold reports as stalled."""
        self.write_heartbeat(age_seconds=400)
        self.assertTrue(alerts_webhook.read_heartbeat_stalled(self.state_dir, 300))

    def test_missing_heartbeat_is_fail_closed_stalled(self):
        """Missing heartbeat is stalled (facade contract), not silently 'active'."""
        # No heartbeat written.
        self.assertTrue(alerts_webhook.read_heartbeat_stalled(self.state_dir, 300))

        payload = alerts_webhook.compose_payload({"style": "slack"}, self.state_dir)
        self.assertIn("stalled", json.dumps(payload).lower())

    def test_facade_unavailable_reports_unknown_and_still_composes(self):
        """Without the facade the status is unknown; the tool still fails open."""
        with patch.object(alerts_webhook, "ReadAPI", None):
            self.assertIsNone(alerts_webhook.read_heartbeat_stalled(self.state_dir, 300))

            slack = alerts_webhook.compose_payload({"style": "slack"}, self.state_dir)
            discord = alerts_webhook.compose_payload({"style": "discord"}, self.state_dir)

        self.assertIn("unknown", json.dumps(slack).lower())
        self.assertIn("unknown", json.dumps(discord).lower())

    def test_facade_error_reports_unknown_not_crash(self):
        """A facade exception degrades to unknown rather than crashing the alert."""
        with patch.object(alerts_webhook, "ReadAPI", side_effect=RuntimeError("boom")):
            self.assertIsNone(alerts_webhook.read_heartbeat_stalled(self.state_dir, 300))

    def test_no_direct_heartbeat_open_in_source(self):
        """The tool source must not open the heartbeat file itself."""
        source = Path(alerts_webhook.__file__).read_text(encoding="utf-8")
        for line in source.split("\n"):
            stripped = line.strip()
            if "orchestrator-heartbeat" not in stripped:
                continue
            # The only permitted occurrence is the identifier handed to the facade.
            self.assertTrue(
                stripped.startswith("ORCHESTRATOR_HEARTBEAT =")
                or stripped.startswith("#"),
                f"Unexpected heartbeat path usage: {stripped}",
            )
            self.assertNotIn("open(", stripped)
            self.assertNotIn("read_text", stripped)


if __name__ == "__main__":
    unittest.main()
