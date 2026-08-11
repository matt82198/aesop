#!/usr/bin/env python3
"""
Healthcheck tool — Aggregates fleet health signals.
INDEX: Fleet health aggregator (heartbeat/alert/orchestrator status); health.js wraps Python

Checks:
- Heartbeat ages (watchdog, monitor) from config-driven state paths
- Tracker open-item counts by lane (state/tracker.json)
- Security-alert count and severity (state/SECURITY-ALERTS.log, if present)
- Orchestrator status age and phase (via ReadAPI, Inc 2+)

Output: One line `HEALTH: 🟢|🟡|🔴 <reason>` + compact bullet list of non-green contributors.

Green = all heartbeats fresh + no HIGH alerts
Yellow = stale heartbeat OR unreviewed MED alert
Red = HIGH alert OR watchdog dead while agents running

Config read at CALL time. Graceful on missing files (missing = reported, not crash).
Encoding: UTF-8 always. --json mode outputs machine-readable format.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import time

try:
    from common import check_heartbeat_staleness, get_state_dir
except ImportError:
    from tools.common import check_heartbeat_staleness, get_state_dir

# Import UI config for state path resolution
UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

# Ensure state_store module is importable
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

try:
    import config
except ImportError:
    print("ERROR: Unable to import config module from ui/", file=sys.stderr)
    sys.exit(2)


def check_health(json_mode=False):
    """
    Aggregate health signals and return health ball + reason.

    Returns:
        str: Either human-readable "HEALTH: 🟢|🟡|🔴 <reason>\n- bullet list"
             or JSON string (if json_mode=True)
    """
    # Reload config at call time to pick up env vars
    config.reload()

    issues = []  # List of (severity, message) tuples

    # 1. Check heartbeats
    watchdog_status = _check_heartbeat("watchdog", config.WATCHDOG_HEARTBEAT, threshold=300)
    if watchdog_status:
        issues.append(watchdog_status)

    monitor_status = _check_heartbeat("monitor", config.MONITOR_HEARTBEAT, threshold=3600)
    if monitor_status:
        issues.append(monitor_status)

    # 2. Check security alerts
    alert_status = _check_alerts(config.ALERTS_LOG)
    if alert_status:
        issues.append(alert_status)

    # 3. Check orchestrator status (via ReadAPI, Inc 2+)
    orch_status = _check_orchestrator_status_api(get_state_dir())
    if orch_status:
        issues.append(orch_status)

    # 4. Check tracker items (informational, not severity-driving)
    tracker_info = _check_tracker(config.TRACKER_FILE)

    # Determine ball color based on issue severities
    ball = "🟢"  # Default green
    for severity, msg in issues:
        if severity == "RED":
            ball = "🔴"
            break
        elif severity == "YELLOW" and ball != "🔴":
            ball = "🟡"

    # Build output
    if json_mode:
        return _format_json(ball, issues, tracker_info)
    else:
        return _format_human(ball, issues, tracker_info)


def _check_heartbeat(name, heartbeat_file, threshold=300):
    """
    Check heartbeat freshness.

    Returns:
        tuple (severity, message) or None if all OK
    """
    is_stale, age_seconds, info = check_heartbeat_staleness(heartbeat_file, threshold)

    if not is_stale:
        return None

    # Stale: determine severity and message
    if age_seconds == 0:
        # File missing/empty/unreadable - use generic message
        if "missing" in info.lower():
            return ("YELLOW", f"no {name} heartbeat file")
        elif "empty" in info.lower():
            return ("YELLOW", f"{name} heartbeat empty")
        else:
            return ("YELLOW", f"{name} heartbeat unparseable")

    # File exists but is stale
    if age_seconds >= threshold * 2:
        # Watchdog dead while orchestrator might be active
        if name == "watchdog" and age_seconds >= 600:
            return ("RED", f"watchdog dead ({age_seconds}s)")
        return ("YELLOW", f"{name} stale ({age_seconds}s > {threshold}s threshold)")
    else:
        return ("YELLOW", f"{name} stale ({age_seconds}s)")


def _check_alerts(alerts_file):
    """
    Check security alerts for HIGH severity.

    Returns:
        tuple (severity, message) or None if all OK
    """
    if not alerts_file.exists():
        return None

    try:
        content = alerts_file.read_text(encoding="utf-8").strip()
        if not content:
            return None

        lines = content.split("\n")
        unreviewed = [
            line.strip() for line in lines
            if line.strip()
            and "NOTE:" not in line
            and "RESOLVED-FP" not in line
        ]

        if not unreviewed:
            return None

        # Check for HIGH severity
        high_count = sum(1 for line in unreviewed if "[HIGH]" in line)
        if high_count > 0:
            return ("RED", f"{high_count} HIGH severity alerts")

        # Check for MED severity
        med_count = sum(1 for line in unreviewed if "[MED]" in line)
        if med_count > 0:
            return ("YELLOW", f"{med_count} unreviewed MED alerts")

        if unreviewed:
            return ("YELLOW", f"{len(unreviewed)} unreviewed alerts")

        return None
    except Exception as e:
        return ("YELLOW", f"alert check error: {e}")


def _check_orchestrator_status_api(state_dir):
    """
    Check orchestrator status age via ReadAPI (projection-first, Inc 2+).

    Returns:
        tuple (severity, message) or None if all OK
    """
    try:
        from state_store.read_api import ReadAPI

        api = ReadAPI(state_dir)
        status = api.read_orchestrator_status()

        if status is None:
            return None

        # Check updated_at age
        updated_at_str = status.get("updated_at")
        if not updated_at_str:
            return None

        # Handle both Z-suffix and +/-offset formats
        if updated_at_str.endswith("Z"):
            updated_at_str = updated_at_str[:-1]

        try:
            # fromisoformat handles both naive and tz-aware datetimes
            updated_at = datetime.fromisoformat(updated_at_str)

            # Convert to UTC if tz-aware; assume UTC if naive
            if updated_at.tzinfo is not None:
                updated_at_utc = updated_at.astimezone(timezone.utc)
                age_seconds = int((datetime.now(timezone.utc) - updated_at_utc).total_seconds())
            else:
                # Assume naive datetime is UTC
                age_seconds = int((datetime.now(timezone.utc).replace(tzinfo=None) - updated_at).total_seconds())

            # >1800s (30min) is stale for orchestrator
            if age_seconds > 1800:
                return ("YELLOW", f"orchestrator status stale ({age_seconds}s)")
        except ValueError as e:
            # Log the parse error instead of silently failing
            print(f"WARNING: failed to parse orchestrator updated_at '{updated_at_str}': {e}", file=sys.stderr)

        return None
    except Exception as e:
        return ("YELLOW", f"orchestrator status check error: {e}")


def _check_tracker(tracker_file):
    """
    Aggregate tracker item counts by lane (informational only).

    Returns:
        dict or None with lane counts
    """
    if not tracker_file.exists():
        return None

    try:
        content = tracker_file.read_text(encoding="utf-8").strip()
        if not content:
            return None

        data = json.loads(content)
        if not isinstance(data, dict):
            return None

        items = data.get("items", [])
        if not items:
            return None

        # Count by lane
        lanes = {}
        for item in items:
            lane = item.get("lane", "unknown")
            if lane not in lanes:
                lanes[lane] = 0
            lanes[lane] += 1

        return lanes
    except Exception:
        return None


def _format_human(ball, issues, tracker_info):
    """Format human-readable output."""
    reason = "OK"
    if issues:
        reasons = [msg for severity, msg in issues]
        reason = "; ".join(reasons)

    lines = [f"HEALTH: {ball} {reason}"]

    if tracker_info:
        lane_str = ", ".join(f"{lane}: {count}" for lane, count in sorted(tracker_info.items()))
        lines.append(f"  Tracker: {lane_str}")

    return "\n".join(lines)


def _format_json(ball, issues, tracker_info):
    """Format JSON output."""
    result = {
        "ball": ball,
        "health": "OK" if ball == "🟢" else ("DEGRADED" if ball == "🟡" else "CRITICAL"),
        "issues": [{"severity": severity, "message": msg} for severity, msg in issues],
        "tracker": tracker_info or {},
    }
    return json.dumps(result, indent=2)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Aesop healthcheck — aggregate fleet health signals"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()

    try:
        output = check_health(json_mode=args.json)
        print(output)
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
