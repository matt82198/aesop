#!/usr/bin/env python3
"""
INDEX: Stateless fleet status webhook (heartbeat/merge-queue/exceptions to Slack/Discord); reads orchestrator heartbeat freshness through the state_store.read_api ReadAPI facade (never opens the heartbeat file directly); CLI: `[--config PATH] [--dry-run]`; fail-open on missing config/network errors
Aesop Alerts Webhook — Stateless Slack/Discord webhook for fleet status.

One-shot tool: reads on-disk signals (heartbeats, merge-queue, exceptions),
composes a compact status payload, POSTs to configured webhook URL.
Fail-open design: missing config, network errors, and unavailable tools all exit 0.

Configuration: reads from aesop.config.json at runtime.
  alerts: {
    webhook_url: "https://...",     # Required to send; null/absent = exit 0
    style: "slack" | "discord",      # Payload format (default: "slack")
    heartbeat_stall_threshold_s: 300 # Staleness threshold (default: 300)
  }

Modes:
  python tools/alerts_webhook.py [--config PATH] [--dry-run]

Behavior:
- Reads on-disk signals only: heartbeats, merge-queue/state.json, exceptions.jsonl
- Orchestrator heartbeat freshness comes from the sanctioned StateAPI read facade
  (state_store.read_api.ReadAPI.check_heartbeat_fresh), never a direct file open.
  The facade is fail-closed by contract: a missing or unreadable heartbeat reports
  as stalled. If the facade cannot be imported, heartbeat status reports "unknown"
  and the tool still sends the rest of the payload (fail-open).
- Skips gh commands silently if unavailable (never credential-hunt)
- Composes Slack blocks or Discord embeds
- POSTs with 10s timeout; network errors warn and exit 0
- --dry-run prints payload JSON to stdout, never POSTs
- No config or webhook_url = exit 0 with skip line to stderr

Stdlib-only (json, urllib, pathlib, sys, subprocess, os) plus the state_store read facade.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta, timezone
import subprocess

# Repo root on sys.path so the state_store package resolves when this tool is
# loaded by file path (tools/ on sys.path) as well as via `python tools/...`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from state_store.read_api import ReadAPI
except Exception:  # pragma: no cover - facade unavailable in stripped installs
    ReadAPI = None

# Encoding: UTF-8 (all file I/O and network)
DEFAULT_ENCODING = "utf-8"
DEFAULT_TIMEOUT_S = 10
DEFAULT_HEARTBEAT_THRESHOLD_S = 300

# Heartbeat identifier passed TO the read facade (the facade owns the file access).
ORCHESTRATOR_HEARTBEAT = ".orchestrator-heartbeat"


# ==============================================================================
# Configuration Loading
# ==============================================================================


def load_config(config_path=None):
    """Load aesop.config.json from current directory or specified path."""
    if config_path:
        config_file = Path(config_path)
    else:
        config_file = Path("aesop.config.json")

    if not config_file.exists():
        return {}

    try:
        with open(config_file, "r", encoding=DEFAULT_ENCODING) as f:
            return json.load(f)
    except Exception as e:
        print(f"[alerts_webhook] Failed to load config: {e}", file=sys.stderr)
        return {}


def get_alerts_config(config):
    """Extract alerts config from aesop.config.json."""
    alerts = config.get("alerts", {})
    if not isinstance(alerts, dict):
        return {}
    return alerts


def get_state_root(config):
    """Get state directory from config or default to ./state."""
    state_root = config.get("state_root", "./state")
    return Path(state_root)


# ==============================================================================
# Signal Reading
# ==============================================================================


def read_heartbeat_stalled(state_dir, threshold_s=DEFAULT_HEARTBEAT_THRESHOLD_S):
    """Report orchestrator heartbeat staleness via the StateAPI read facade.

    The heartbeat file is never opened here; the read goes through
    state_store.read_api.ReadAPI.check_heartbeat_fresh, which owns the file
    access and the staleness contract (missing/unreadable/clock-skewed => stale).

    Args:
        state_dir: Path to the state directory.
        threshold_s: Age in seconds at or beyond which the heartbeat is stalled.

    Returns:
        True  if the orchestrator heartbeat is stalled (or missing),
        False if it is fresh,
        None  if the read facade is unavailable (status unknown; fail-open).
    """
    if ReadAPI is None:
        return None

    try:
        api = ReadAPI(state_dir)
        return not api.check_heartbeat_fresh(ORCHESTRATOR_HEARTBEAT, threshold_s)
    except Exception:
        # Facade error: report unknown rather than crashing the alert path.
        return None


def read_exceptions_tail(state_dir, tail_lines=10):
    """Read last N lines from exceptions.jsonl, parse as JSON objects."""
    exc_file = state_dir / "exceptions.jsonl"
    if not exc_file.exists():
        return []

    try:
        lines = exc_file.read_text(encoding=DEFAULT_ENCODING).strip().split("\n")
        # Take tail
        tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
        exceptions = []
        for line in tail:
            if line.strip():
                try:
                    exc = json.loads(line)
                    exceptions.append(exc)
                except json.JSONDecodeError:
                    pass
        return exceptions
    except Exception:
        return []


def read_merge_queue_state(state_dir):
    """Read merge-queue/state.json, return dict or empty dict on error."""
    queue_file = state_dir / "merge-queue" / "state.json"
    if not queue_file.exists():
        return {}

    try:
        with open(queue_file, "r", encoding=DEFAULT_ENCODING) as f:
            return json.load(f)
    except Exception:
        return {}


def count_open_prs_gh():
    """Count open PRs via gh command, return count or 0 if gh unavailable."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "number"],
            capture_output=True,
            text=False,  # Get bytes
            timeout=5,
            encoding=DEFAULT_ENCODING,
        )
        if result.returncode == 0:
            prs = json.loads(result.stdout)
            return len(prs)
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        # gh not found or timed out: silent skip
        pass
    return 0


# ==============================================================================
# Payload Composition
# ==============================================================================


def compose_payload(alerts_config, state_dir):
    """Compose status payload for Slack or Discord."""
    state_dir = Path(state_dir)

    # Staleness threshold drives the facade query
    threshold = alerts_config.get(
        "heartbeat_stall_threshold_s", DEFAULT_HEARTBEAT_THRESHOLD_S
    )

    # Read signals (heartbeat via the StateAPI facade; True/False/None)
    stalled = read_heartbeat_stalled(state_dir, threshold)
    exceptions = read_exceptions_tail(state_dir)
    queue_state = read_merge_queue_state(state_dir)
    open_prs = count_open_prs_gh()

    # Payload format
    style = alerts_config.get("style", "slack").lower()
    if style == "discord":
        return compose_discord_payload(stalled, exceptions, queue_state, open_prs)
    else:
        return compose_slack_payload(stalled, exceptions, queue_state, open_prs)


def compose_slack_payload(stalled, exceptions, queue_state, open_prs):
    """Compose Slack message with blocks.

    Args:
        stalled: True (heartbeat stalled/missing), False (fresh), or None (unknown).
    """
    blocks = []

    # Header
    blocks.append(
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Aesop Fleet Status",
                "emoji": True,
            },
        }
    )

    # Main status section
    status_text = "Fleet Status Report\n"
    if stalled is None:
        status_text += "? Orchestrator heartbeat unknown (state read facade unavailable)\n"
    elif stalled:
        status_text += "⚠️ Orchestrator stalled (heartbeat stale or missing)\n"
    else:
        status_text += "✓ Orchestrator active (heartbeat fresh)\n"

    if exceptions:
        status_text += f"⚠️ Recent exceptions: {len(exceptions)}\n"
    if open_prs:
        status_text += f"📊 Open PRs: {open_prs}\n"
    if queue_state:
        queue_len = queue_state.get("queue", [])
        status_text += f"🚂 Merge queue: {len(queue_len) if isinstance(queue_len, list) else 0} items\n"

    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": status_text},
        }
    )

    # Exception details if present
    if exceptions:
        details = "Recent Exceptions:\n"
        for exc in exceptions[-3:]:  # Show last 3
            msg = exc.get("message", "")[:100]
            details += f"• {msg}\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": details}})

    # Timestamp
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Generated: {datetime.now(timezone.utc).isoformat()}",
                }
            ],
        }
    )

    return {"blocks": blocks}


def compose_discord_payload(stalled, exceptions, queue_state, open_prs):
    """Compose Discord message with embeds.

    Args:
        stalled: True (heartbeat stalled/missing), False (fresh), or None (unknown).
    """
    embeds = []

    # Status embed: red if stalled, grey if unknown, green if fresh
    if stalled is None:
        color = 9807270
    elif stalled:
        color = 16711680
    else:
        color = 65280

    status_text = ""
    if stalled is None:
        status_text += "? Orchestrator heartbeat unknown (state read facade unavailable)\n"
    elif stalled:
        status_text += "⚠️ Orchestrator stalled (heartbeat stale or missing)\n"
    else:
        status_text += "✓ Orchestrator active (heartbeat fresh)\n"

    if exceptions:
        status_text += f"Exceptions: {len(exceptions)} recent\n"
    if open_prs:
        status_text += f"Open PRs: {open_prs}\n"
    if queue_state:
        queue_len = queue_state.get("queue", [])
        status_text += f"Merge queue: {len(queue_len) if isinstance(queue_len, list) else 0} items\n"

    embed = {
        "title": "Aesop Fleet Status",
        "description": status_text.strip(),
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Exception field if present
    if exceptions:
        exc_text = "\n".join([exc.get("message", "")[:80] for exc in exceptions[-3:]])
        embed["fields"] = [
            {
                "name": "Recent Exceptions",
                "value": exc_text,
                "inline": False,
            }
        ]

    embeds.append(embed)

    return {"embeds": embeds}


# ==============================================================================
# Webhook Posting
# ==============================================================================


def post_webhook(url, payload, timeout_s=DEFAULT_TIMEOUT_S):
    """POST payload to webhook URL with timeout."""
    try:
        data = json.dumps(payload).encode(DEFAULT_ENCODING)
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            status = response.status
            return status == 200
    except urllib.error.HTTPError as e:
        print(f"[alerts_webhook] HTTP error: {e.code}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"[alerts_webhook] URL error: {e.reason}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[alerts_webhook] Network error: {e}", file=sys.stderr)
        return False


# ==============================================================================
# Main
# ==============================================================================


def main(argv=None):
    """Main entry point."""
    if argv is None:
        argv = sys.argv[1:]

    # Parse arguments
    config_path = None
    dry_run = False
    for i, arg in enumerate(argv):
        if arg == "--config" and i + 1 < len(argv):
            config_path = argv[i + 1]
        elif arg == "--dry-run":
            dry_run = True

    # Load configuration
    config = load_config(config_path)
    alerts_config = get_alerts_config(config)

    # Check webhook URL (required to send)
    webhook_url = alerts_config.get("webhook_url")
    if not webhook_url:
        print("[alerts_webhook] No webhook_url configured; skipping", file=sys.stderr)
        return 0

    # Get state root
    state_dir = get_state_root(config)

    # Compose payload
    payload = compose_payload(alerts_config, state_dir)

    # Dry run: print and exit
    if dry_run:
        print(json.dumps(payload, indent=2), file=sys.stdout)
        return 0

    # Post to webhook
    success = post_webhook(webhook_url, payload)
    if success:
        print("[alerts_webhook] Posted to webhook", file=sys.stderr)
        return 0
    else:
        print("[alerts_webhook] Failed to post; skipping", file=sys.stderr)
        return 0  # Fail-open: never crash


if __name__ == "__main__":
    sys.exit(main())
