#!/usr/bin/env python3
"""
Aesop Alert Bridge — Slack/Discord webhook integration for fleet alerts.
INDEX: Slack/Discord webhook bridge for SECURITY-ALERTS

Bridges SECURITY-ALERTS.log and watchdog heartbeat stalls to Slack/Discord webhooks.
Modes:
  --scan           Check SECURITY-ALERTS.log and heartbeat, send alerts (default)
  --test-message   Send a test ping to webhook
  --dry-run        Print masked payload instead of posting

Configuration: reads from aesop.config.json at runtime (not imported at module load).
  alerts: {
    webhook_url: null,         # If null/absent: no-op exit 0 (feature opt-in)
    provider: "slack"|"discord",
    min_severity: "LOW"|"MEDIUM"|"HIGH"|"CRITICAL",
    heartbeat_stall_s: 600     # Check heartbeat staleness; null = skip
  }

Idempotency: cursor file (state/.alert-bridge-cursor) tracks last sent line.
Never logs or echoes webhook URL (masked to last 6 chars in output).

Stdlib-only (urllib, json, sys, os, pathlib, time).
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

try:
    from common import check_heartbeat_staleness as check_hb_staleness
except ImportError:
    from tools.common import check_heartbeat_staleness as check_hb_staleness


# ==============================================================================
# Configuration Loading
# ==============================================================================


def load_config():
    """Load aesop.config.json from current directory, return config dict."""
    config_file = Path("aesop.config.json")
    if not config_file.exists():
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[alert_bridge] Failed to load config: {e}", file=sys.stderr)
        return {}


def get_alerts_config(config):
    """Extract alerts config from aesop.config.json."""
    alerts = config.get("alerts", {})
    if not isinstance(alerts, dict):
        return {}
    return alerts


def get_state_root(config):
    """Get state directory from config or default to ./state."""
    return Path(config.get("state_root", "./state"))


# ==============================================================================
# Severity Levels
# ==============================================================================


SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def parse_severity(text):
    """Extract severity from alert text (e.g., 'HIGH: ...' or '[2026-01-15] HIGH: ...') or return None."""
    text = text.strip()
    # Handle timestamps like "[2026-01-15 10:30]" at start
    if text.startswith("["):
        end_bracket = text.find("]")
        if end_bracket >= 0:
            text = text[end_bracket + 1:].strip()

    # Now check for severity prefix
    for severity in SEVERITY_ORDER.keys():
        if text.startswith(severity + ":"):
            return severity
    return None


def should_send_alert(alert_text, min_severity):
    """Check if alert meets minimum severity threshold."""
    severity = parse_severity(alert_text)
    if severity is None:
        return False  # Unknown severity format
    min_level = SEVERITY_ORDER.get(min_severity, 0)
    return SEVERITY_ORDER.get(severity, 0) >= min_level


# ==============================================================================
# Cursor Management (Idempotency)
# ==============================================================================


def get_cursor_path(state_root):
    """Return cursor file path."""
    return state_root / ".alert-bridge-cursor"


def read_cursor(state_root):
    """Read last sent line number from cursor file (default 0)."""
    cursor_file = get_cursor_path(state_root)
    if not cursor_file.exists():
        return 0
    try:
        return int(cursor_file.read_text(encoding="utf-8").strip())
    except (ValueError, IOError):
        return 0


def write_cursor(state_root, line_number):
    """Write line number to cursor file."""
    cursor_file = get_cursor_path(state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    try:
        cursor_file.write_text(str(line_number), encoding="utf-8")
    except IOError as e:
        print(f"[alert_bridge] Failed to write cursor: {e}", file=sys.stderr)


# ==============================================================================
# Alert Collection
# ==============================================================================


def collect_new_alerts(state_root, min_severity, since_line):
    """
    Read SECURITY-ALERTS.log, filter by severity, return new alerts since cursor.

    Returns: (new_alerts: list[str], total_lines: int)
    """
    alerts_file = state_root / "SECURITY-ALERTS.log"
    if not alerts_file.exists():
        return [], 0

    try:
        content = alerts_file.read_text(encoding="utf-8")
    except IOError:
        return [], 0

    lines = content.strip().split("\n") if content.strip() else []
    new_alerts = []

    for i, line in enumerate(lines, start=1):
        # Skip if before cursor
        if i <= since_line:
            continue
        line = line.strip()
        if not line:
            continue
        # Skip marked entries
        if "NOTE:" in line or "RESOLVED-FP" in line:
            continue
        # Check severity
        if should_send_alert(line, min_severity):
            new_alerts.append(line)

    return new_alerts, len(lines)


# ==============================================================================
# Heartbeat Staleness Check
# ==============================================================================


def check_heartbeat_staleness(state_root, stall_threshold_s):
    """
    Check if watchdog heartbeat is stale.

    Returns: (is_stale: bool, heartbeat_info: str or None)
    """
    if stall_threshold_s is None:
        return False, None

    hb_file = state_root / ".watchdog-heartbeat"
    is_stale, age_seconds, info = check_hb_staleness(hb_file, stall_threshold_s)

    if not is_stale:
        return False, None

    # Map the common function's result to our return format
    if age_seconds == 0:
        # File missing/empty/unreadable
        return True, info
    else:
        # File exists but is stale
        return True, f"Watchdog heartbeat stale ({age_seconds}s >= {stall_threshold_s}s)"


# ==============================================================================
# Payload Formatting
# ==============================================================================


def format_slack_payload(alerts, heartbeat_info):
    """Format Slack block-kit payload."""
    blocks = []

    # Header block
    blocks.append(
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 Aesop Fleet Alert"},
        }
    )

    # Section for alerts
    if alerts:
        alert_text = "\n".join([f"• {alert}" for alert in alerts[:10]])
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": alert_text},
            }
        )

    # Section for heartbeat
    if heartbeat_info:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"⚠️  *Heartbeat*: {heartbeat_info}",
                },
            }
        )

    return {"blocks": blocks}


def format_discord_payload(alerts, heartbeat_info):
    """Format Discord embed payload."""
    embeds = []

    description_parts = []
    if alerts:
        for alert in alerts[:10]:
            description_parts.append(alert)
    if heartbeat_info:
        description_parts.append(f"⚠️  **Heartbeat**: {heartbeat_info}")

    embed = {
        "title": "🚨 Aesop Fleet Alert",
        "description": "\n".join(description_parts) or "Alert triggered",
        "color": 16711680,  # Red
    }

    embeds.append(embed)
    return {"embeds": embeds}


# ==============================================================================
# Webhook Sending
# ==============================================================================


def mask_webhook_url(webhook_url):
    """Mask webhook URL to last 6 chars for logging."""
    if not webhook_url or len(webhook_url) < 6:
        return "***"
    return "***" + webhook_url[-6:]


def send_webhook(webhook_url, payload):
    """POST payload to webhook URL. Returns (success: bool, status_code: int or None)."""
    if not webhook_url:
        return False, None

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        response = urllib.request.urlopen(req, timeout=10)
        status_code = response.getcode()
        return status_code in (200, 204), status_code
    except urllib.error.HTTPError as e:
        return False, e.code
    except Exception as e:
        print(
            f"[alert_bridge] Failed to send webhook: {e}",
            file=sys.stderr,
        )
        return False, None


# ==============================================================================
# Main Modes
# ==============================================================================


def mode_scan(config):
    """Scan SECURITY-ALERTS.log and heartbeat, send alerts if new."""
    alerts_config = get_alerts_config(config)
    state_root = get_state_root(config)

    webhook_url = alerts_config.get("webhook_url")
    provider = alerts_config.get("provider", "slack")
    min_severity = alerts_config.get("min_severity", "HIGH")
    heartbeat_stall_s = alerts_config.get("heartbeat_stall_s")

    # No-op if webhook disabled
    if not webhook_url:
        return 0

    # Read cursor
    cursor = read_cursor(state_root)

    # Collect new alerts
    new_alerts, total_lines = collect_new_alerts(state_root, min_severity, cursor)

    # Check heartbeat staleness
    is_stale, heartbeat_info = check_heartbeat_staleness(state_root, heartbeat_stall_s)

    # Nothing to send
    if not new_alerts and not is_stale:
        return 0

    # Format payload
    if provider == "discord":
        payload = format_discord_payload(new_alerts, heartbeat_info)
    else:  # slack or default
        payload = format_slack_payload(new_alerts, heartbeat_info)

    # Send webhook
    success, status_code = send_webhook(webhook_url, payload)
    if success:
        # Update cursor only on success
        write_cursor(state_root, total_lines)
        print(
            f"[alert_bridge] Sent {len(new_alerts)} alert(s) to {mask_webhook_url(webhook_url)} (status: {status_code})"
        )
        return 0
    else:
        print(
            f"[alert_bridge] Failed to send webhook (status: {status_code})",
            file=sys.stderr,
        )
        return 1


def mode_test_message(config):
    """Send a test ping to webhook."""
    alerts_config = get_alerts_config(config)
    webhook_url = alerts_config.get("webhook_url")
    provider = alerts_config.get("provider", "slack")

    if not webhook_url:
        print("[alert_bridge] Webhook URL not configured (no-op)", file=sys.stderr)
        return 0

    # Format test message
    if provider == "discord":
        payload = {"embeds": [{"title": "✅ Alert Bridge Test", "description": "Connection OK"}]}
    else:  # slack or default
        payload = {
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "✅ Alert Bridge Test"}},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "Connection OK"},
                },
            ]
        }

    success, status_code = send_webhook(webhook_url, payload)
    if success:
        print(f"[alert_bridge] Test message sent to {mask_webhook_url(webhook_url)} (status: {status_code})")
        return 0
    else:
        print(
            f"[alert_bridge] Test message failed (status: {status_code})",
            file=sys.stderr,
        )
        return 1


def mode_dry_run(config):
    """Print masked payload without sending."""
    alerts_config = get_alerts_config(config)
    state_root = get_state_root(config)

    webhook_url = alerts_config.get("webhook_url")
    provider = alerts_config.get("provider", "slack")
    min_severity = alerts_config.get("min_severity", "HIGH")
    heartbeat_stall_s = alerts_config.get("heartbeat_stall_s")

    if not webhook_url:
        print("[alert_bridge] Webhook URL not configured (no-op)", file=sys.stderr)
        return 0

    cursor = read_cursor(state_root)
    new_alerts, _ = collect_new_alerts(state_root, min_severity, cursor)
    is_stale, heartbeat_info = check_heartbeat_staleness(state_root, heartbeat_stall_s)

    if not new_alerts and not is_stale:
        print(f"[alert_bridge] No alerts to send (webhook: {mask_webhook_url(webhook_url)})")
        return 0

    # Format payload
    if provider == "discord":
        payload = format_discord_payload(new_alerts, heartbeat_info)
    else:  # slack or default
        payload = format_slack_payload(new_alerts, heartbeat_info)

    print(f"[alert_bridge] DRY-RUN payload (webhook: {mask_webhook_url(webhook_url)})")
    print(json.dumps(payload, indent=2))
    return 0


# ==============================================================================
# Entry Point
# ==============================================================================


USAGE = "Usage: alert_bridge.py [--scan | --test-message | --dry-run | --help]"


def main(args=None):
    """Main entry point."""
    if args is None:
        args = sys.argv[1:]

    mode = "--scan"  # default
    if args:
        mode = args[0]

    if mode in ("-h", "--help"):
        print(USAGE)
        return 0

    config = load_config()

    if mode == "--test-message":
        return mode_test_message(config)
    elif mode == "--dry-run":
        return mode_dry_run(config)
    elif mode == "--scan":
        return mode_scan(config)
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
