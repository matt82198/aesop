#!/usr/bin/env python3
"""
Cost Projection & Threshold Alerting — Live burn-rate observability for Aesop.
INDEX: Live burn-rate observability; projects end-of-wave spend and fires threshold alerts at the 70- and 90-percent ceiling thresholds; CLI: `--projection [--window N] [--json]` or `--check-alerts --wave N [--json]`; idempotent per wave via flag files under state/

Calculates burn rate (tokens/min) from recent ledger entries, projects end-of-wave
spend, and fires threshold alerts at 70% and 90% of the configured cost ceiling.

API:
  project(window_minutes, ceiling, config, horizon_minutes=60) -> dict
    Calculate current spend, burn rate, and projected end-of-wave total.
    Returns:
      {
        "current": int,                # tokens spent in window
        "burn_rate_per_min": float,    # avg tokens/min in window
        "projected": int,              # projected spend at horizon
        "ceiling": int or None,        # configured ceiling (or None if unconfigured)
        "pct_of_ceiling": float or None,  # % of ceiling (or None if no ceiling)
        "minutes_to_ceiling": float or None,  # minutes until ceiling hit at current burn rate (None-safe)
        "is_thin_window": bool,        # warn if window has < 3 entries
        "by_role": {role: {...}, ...}, # breakdown by model/role (haiku/sonnet/opus)
        "reason": str or None          # degradation reason if applicable
      }

  check_and_alert(window_minutes, ceiling, wave, config, horizon_minutes=60) -> dict
    Run projection + fire threshold alerts at 70% and 90% of ceiling.
    Writes to SECURITY-ALERTS.log and creates flag files for idempotency.
    Single-writer-per-wave assumption: only one process calls this per wave.
    Returns:
      {
        "alert_level": str or None,    # "70", "90", or None if below threshold
        "current": int,
        "ceiling": int,
        "pct_of_ceiling": float,
        "minutes_to_ceiling": float or None,  # minutes to ceiling breach at current burn rate
        "fired_alert": bool            # True if new alert was written (not already fired)
      }

CLI:
  python tools/cost_projection.py --projection [--window 30] [--horizon 60] [--ceiling N] [--json] [--config <path>]
    Print projection result (human or JSON).
  python tools/cost_projection.py --check-alerts --wave <id> [--horizon 60] [--ceiling N] [--json] [--config <path>]
    Check thresholds and fire alerts; print result. Requires --wave or AESOP_WAVE env var.

Configuration (aesop.config.json):
  "limits": {
    "max_wave_tokens": 50000,     # Ceiling for projection/alerts
    "max_daily_tokens": null,
    "wave_duration_minutes": 60   # Horizon for projection (optional; default 60)
  }

Ledger Source:
  Reads from state/ledger/OUTCOMES-LEDGER.md (markdown table with ISO timestamps).
  Filters entries within window_minutes of NOW (UTC).
  No naive datetimes; all UTC epoch-based.

WINDOW CONTRACT (shared with cost_ceiling.py):
  Both cost_projection.py and cost_ceiling.py import and use the shared
  calculate_window_bounds() helper to ensure identical window calculations.
  When both tools are called with the same window_minutes value, they will
  compute identical spend figures from the ledger.

  This prevents disagreement between projection and ceiling checks that use
  different window calculations.

Alert Idempotency:
  Flag files: state/.cost-alert-{70,90}-w{wave}
  Once fired for a (threshold, wave) pair, no duplicate log entries until next wave.
  Single-writer guarantee: only the monitor/orchestrator calls this at the same cadence.

Write Integrity:
  append_alert_log uses open('a')+write+flush+os.fsync for crash-safe appends.
  mark_alert_fired called only after successful append (checked via return value).
  Failures print CRITICAL to stderr; flag file not created on append failure.

Stdlib-only (json, sys, os, pathlib, datetime, collections).
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

try:
    import fleet_ledger
    import common
    import cost_ceiling
except ImportError:
    from tools import fleet_ledger
    from tools import common
    from tools import cost_ceiling


def get_state_dir(config=None):
    """Resolve state directory from config or environment."""
    if config and config.get("state_root"):
        return Path(config["state_root"])
    if os.environ.get("AESOP_STATE_ROOT"):
        return Path(os.environ["AESOP_STATE_ROOT"])
    return Path.cwd() / "state"


def get_ceiling(config):
    """Extract max_wave_tokens ceiling from config, or None if unconfigured."""
    if not config:
        return None
    limits = config.get("limits", {})
    if not isinstance(limits, dict):
        return None
    return limits.get("max_wave_tokens")


def get_horizon_minutes(config):
    """Extract wave_duration_minutes from config, or 60 if unconfigured."""
    if not config:
        return 60
    limits = config.get("limits", {})
    if not isinstance(limits, dict):
        return 60
    return limits.get("wave_duration_minutes", 60)


def filter_ledger_by_window(rows, window_minutes):
    """Filter ledger rows to those within the last window_minutes (UTC).

    Uses the shared calculate_window_bounds() helper from cost_ceiling.py
    to ensure consistent window calculations with cost_ceiling.check().

    Args:
        rows: List of dicts from fleet_ledger.parse_ledger_rows()
        window_minutes: Time window in minutes

    Returns:
        Filtered list of rows within the window

    Window contract:
      When cost_projection.project(window_minutes=W) and
      cost_ceiling.check(window_minutes=W) both use the same W value,
      they produce identical spend calculations.
    """
    if not rows:
        return []

    # Use shared window bounds helper from cost_ceiling
    window_start, window_end = cost_ceiling.calculate_window_bounds(window_minutes)

    filtered = []
    for row in rows:
        try:
            iso_ts = row['iso_ts']
            # Parse ISO timestamp (format: YYYY-MM-DDTHH:MM:SS[Z] or with offset)
            ts_clean = iso_ts.replace('Z', '+00:00') if 'Z' in iso_ts else iso_ts
            row_dt = datetime.fromisoformat(ts_clean)
            # Ensure UTC
            if row_dt.tzinfo is None:
                row_dt = row_dt.replace(tzinfo=timezone.utc)

            if row_dt >= window_start:
                filtered.append(row)
        except (ValueError, AttributeError):
            # Skip malformed timestamps
            continue

    return filtered


def project(window_minutes=30, ceiling=None, config=None, horizon_minutes=None):
    """Calculate current spend, burn rate, and end-of-wave projection.

    Args:
        window_minutes: Time window for burn rate calculation (default 30)
        ceiling: Optional cost ceiling (for percentage calculation)
        config: aesop.config.json dict or None (will use env var / defaults)
        horizon_minutes: Minutes to project spend to (default from config or 60)

    Returns:
        dict with keys: current, burn_rate_per_min, projected, ceiling,
        pct_of_ceiling, minutes_to_ceiling, is_thin_window, by_role, reason
    """
    if config is None:
        config = {}

    state_dir = get_state_dir(config)
    if ceiling is None:
        ceiling = get_ceiling(config)
    if horizon_minutes is None:
        horizon_minutes = get_horizon_minutes(config)

    # Parse ledger
    try:
        rows = fleet_ledger.parse_ledger_rows()
    except Exception as e:
        return {
            "current": 0,
            "burn_rate_per_min": 0.0,
            "projected": 0,
            "ceiling": ceiling,
            "pct_of_ceiling": None,
            "is_thin_window": True,
            "by_role": {},
            "reason": f"Failed to read ledger: {str(e)[:100]}"
        }

    # Filter to window
    windowed_rows = filter_ledger_by_window(rows, window_minutes)

    # Calculate current spend and breakdown by role
    current_total = 0
    by_role = defaultdict(lambda: {"tokens_in": 0, "tokens_out": 0, "total": 0, "entries": 0})

    for row in windowed_rows:
        ti = row.get('tokens_in', 0)
        to = row.get('tokens_out', 0)
        total = ti + to
        current_total += total

        role = row.get('model', 'unknown')
        by_role[role]['tokens_in'] += ti
        by_role[role]['tokens_out'] += to
        by_role[role]['total'] += total
        by_role[role]['entries'] += 1

    # Convert defaultdict to plain dict for JSON
    by_role_dict = {k: dict(v) for k, v in by_role.items()}

    # Calculate burn rate
    if len(windowed_rows) == 0:
        burn_rate_per_min = 0.0
        is_thin_window = True
    elif len(windowed_rows) < 3:
        # Thin window: estimate from available data (but flag it)
        # Use actual time span, not window size
        try:
            first_row = windowed_rows[0]
            last_row = windowed_rows[-1]
            first_ts = first_row['iso_ts'].replace('Z', '+00:00')
            last_ts = last_row['iso_ts'].replace('Z', '+00:00')
            first_dt = datetime.fromisoformat(first_ts)
            last_dt = datetime.fromisoformat(last_ts)
            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)

            time_span_min = (last_dt - first_dt).total_seconds() / 60.0
            if time_span_min <= 0:
                time_span_min = 1.0  # Avoid division by zero
            burn_rate_per_min = current_total / time_span_min
        except (ValueError, AttributeError):
            burn_rate_per_min = 0.0
        is_thin_window = True
    else:
        # Sufficient data: use window minutes
        burn_rate_per_min = current_total / window_minutes if window_minutes > 0 else 0.0
        is_thin_window = False

    # Project to end-of-horizon (using horizon_minutes parameter)
    projected = int(burn_rate_per_min * horizon_minutes)

    # Calculate percentage of ceiling
    pct_of_ceiling = None
    if ceiling is not None and ceiling > 0:
        pct_of_ceiling = (current_total / ceiling) * 100.0

    # Calculate minutes until ceiling is hit at current burn rate (None-safe)
    minutes_to_ceiling = None
    if ceiling is not None and ceiling > 0 and burn_rate_per_min > 0:
        remaining_tokens = ceiling - current_total
        if remaining_tokens > 0:
            minutes_to_ceiling = remaining_tokens / burn_rate_per_min
        else:
            minutes_to_ceiling = 0.0  # Already at or past ceiling

    return {
        "current": current_total,
        "burn_rate_per_min": round(burn_rate_per_min, 2),
        "projected": projected,
        "ceiling": ceiling,
        "pct_of_ceiling": pct_of_ceiling,
        "minutes_to_ceiling": round(minutes_to_ceiling, 2) if minutes_to_ceiling is not None else None,
        "is_thin_window": is_thin_window,
        "by_role": by_role_dict,
        "reason": None
    }


def get_alert_flag_path(state_dir, threshold, wave):
    """Get the flag file path for a (threshold, wave) pair."""
    return Path(state_dir) / f".cost-alert-{threshold}-w{wave}"


def check_alert_already_fired(state_dir, threshold, wave):
    """Check if alert has already been fired for this (threshold, wave) pair."""
    flag = get_alert_flag_path(state_dir, threshold, wave)
    return flag.exists()


def mark_alert_fired(state_dir, threshold, wave):
    """Mark an alert as fired by creating a flag file."""
    flag = get_alert_flag_path(state_dir, threshold, wave)
    state_dir_path = Path(state_dir)
    state_dir_path.mkdir(parents=True, exist_ok=True)
    try:
        flag.write_text(
            datetime.now(timezone.utc).isoformat(),
            encoding='utf-8'
        )
    except IOError as e:
        print(f"[cost_projection] Failed to write flag file: {e}", file=sys.stderr)


def append_alert_log(state_dir, alert_line):
    """Append one line to SECURITY-ALERTS.log (idempotent per flag).

    Uses open('a')+write+flush+os.fsync for crash-safe appends (Windows-portable).

    Returns:
        bool: True if append succeeded, False on IOError
    """
    alert_file = Path(state_dir) / "SECURITY-ALERTS.log"
    state_dir_path = Path(state_dir)
    state_dir_path.mkdir(parents=True, exist_ok=True)

    try:
        with open(alert_file, 'a', encoding='utf-8') as f:
            f.write(alert_line + '\n')
            f.flush()
            os.fsync(f.fileno())
        return True
    except IOError as e:
        print(f"[cost_projection] CRITICAL: Failed to write alert: {e}", file=sys.stderr)
        return False


def check_and_alert(window_minutes=30, ceiling=None, wave=None, config=None, horizon_minutes=None):
    """Run projection + fire threshold alerts at 70% and 90% of ceiling.

    Fires BOTH 70% and 90% alerts independently if a spike breaches 90%.
    Only calls mark_alert_fired AFTER append succeeds.

    Args:
        window_minutes: Time window for burn rate (default 30)
        ceiling: Cost ceiling (or None to use config)
        wave: Wave number for alert flag files (required or from AESOP_WAVE env)
        config: aesop.config.json dict
        horizon_minutes: Minutes to project spend to (default from config or 60)

    Returns:
        dict with alert info:
        {
          "alert_level": "70"|"90"|None,
          "current": int,
          "ceiling": int,
          "pct_of_ceiling": float,
          "minutes_to_ceiling": float or None,
          "fired_alert": bool
        }

    Raises:
        ValueError: If wave is not provided and AESOP_WAVE env var is not set
    """
    # Resolve wave from argument or environment
    if wave is None:
        wave = os.environ.get("AESOP_WAVE")
        if wave is None:
            raise ValueError(
                "wave parameter required: pass --wave <id> or set AESOP_WAVE env var"
            )
        try:
            wave = int(wave)
        except ValueError:
            raise ValueError(f"AESOP_WAVE must be an integer, got: {wave}")

    if config is None:
        config = {}

    state_dir = get_state_dir(config)
    if ceiling is None:
        ceiling = get_ceiling(config)
    if horizon_minutes is None:
        horizon_minutes = get_horizon_minutes(config)

    proj = project(window_minutes, ceiling, config, horizon_minutes)
    current = proj["current"]
    pct = proj["pct_of_ceiling"]
    minutes_to_ceiling = proj.get("minutes_to_ceiling")

    alert_level = None
    fired = False

    if pct is not None and ceiling is not None:
        # Check both thresholds independently (spike past 90% fires BOTH)
        # 90% alert (highest priority, always checked)
        if pct >= 90.0:
            alert_level = "90"
            if not check_alert_already_fired(state_dir, "90", wave):
                timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                alert_line = (
                    f"[{timestamp}] CRITICAL: Cost projection at 90% of ceiling "
                    f"({current}/{ceiling} tokens, {pct:.1f}%); wave {wave}"
                )
                if append_alert_log(state_dir, alert_line):
                    mark_alert_fired(state_dir, "90", wave)
                    fired = True
            # Also check 70% when at 90% (fire BOTH independently)
            if not check_alert_already_fired(state_dir, "70", wave):
                timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                alert_line_70 = (
                    f"[{timestamp}] HIGH: Cost projection at 70% of ceiling "
                    f"({current}/{ceiling} tokens, {pct:.1f}%); wave {wave}"
                )
                if append_alert_log(state_dir, alert_line_70):
                    mark_alert_fired(state_dir, "70", wave)
                    fired = True
        # 70% alert (if not at 90%)
        elif pct >= 70.0:
            alert_level = "70"
            if not check_alert_already_fired(state_dir, "70", wave):
                timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                alert_line = (
                    f"[{timestamp}] HIGH: Cost projection at 70% of ceiling "
                    f"({current}/{ceiling} tokens, {pct:.1f}%); wave {wave}"
                )
                if append_alert_log(state_dir, alert_line):
                    mark_alert_fired(state_dir, "70", wave)
                    fired = True

    return {
        "alert_level": alert_level,
        "current": current,
        "ceiling": ceiling,
        "pct_of_ceiling": pct,
        "minutes_to_ceiling": minutes_to_ceiling,
        "fired_alert": fired
    }


def load_config_file(config_path=None):
    """Load aesop.config.json from specified path or current directory."""
    if config_path:
        path = Path(config_path)
    else:
        path = Path.cwd() / "aesop.config.json"

    if not path.exists():
        return {}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[cost_projection] Failed to load config: {e}", file=sys.stderr)
        return {}


def main(argv=None):
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Cost projection and threshold alerting for Aesop."
    )
    parser.add_argument(
        "--projection",
        action="store_true",
        help="Calculate and print projection (default mode)"
    )
    parser.add_argument(
        "--check-alerts",
        action="store_true",
        help="Check thresholds and fire alerts"
    )
    parser.add_argument(
        "--window",
        type=int,
        default=30,
        help="Time window in minutes for burn rate (default 30)"
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=None,
        help="Minutes to project spend to (default from config or 60)"
    )
    parser.add_argument(
        "--ceiling",
        type=int,
        default=None,
        help="Cost ceiling in tokens (overrides config)"
    )
    parser.add_argument(
        "--wave",
        type=int,
        default=None,
        help="Wave number (for alert idempotency; required for --check-alerts or from AESOP_WAVE env)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to aesop.config.json (default: ./aesop.config.json)"
    )

    args = parser.parse_args(argv)

    config = load_config_file(args.config)

    if args.check_alerts:
        try:
            result = check_and_alert(
                window_minutes=args.window,
                ceiling=args.ceiling,
                wave=args.wave,
                config=config,
                horizon_minutes=args.horizon
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                pct_str = f"{result['pct_of_ceiling']:.1f}%" if result['pct_of_ceiling'] is not None else "N/A"
                mtc_str = f"{result['minutes_to_ceiling']:.1f}min" if result['minutes_to_ceiling'] is not None else "N/A"
                print(f"[cost_projection] alert_level={result['alert_level']}, "
                      f"current={result['current']}/{result['ceiling']}, "
                      f"pct={pct_str}, "
                      f"mtc={mtc_str}, "
                      f"fired={result['fired_alert']}")
            return 0
        except ValueError as e:
            print(f"[cost_projection] ERROR: {e}", file=sys.stderr)
            return 1
    else:
        # Default: --projection
        result = project(
            window_minutes=args.window,
            ceiling=args.ceiling,
            config=config,
            horizon_minutes=args.horizon
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            mtc_str = f"{result['minutes_to_ceiling']:.1f}min" if result['minutes_to_ceiling'] is not None else "N/A"
            print(f"[cost_projection] current={result['current']}, "
                  f"burn_rate={result['burn_rate_per_min']}/min, "
                  f"projected={result['projected']}, "
                  f"ceiling={result['ceiling']}, "
                  f"pct={result['pct_of_ceiling']}, "
                  f"mtc={mtc_str}, "
                  f"thin_window={result['is_thin_window']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
