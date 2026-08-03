#!/usr/bin/env python3
"""
Cost ceiling guard — trips tools/halt.py's kill switch when fleet spend
(tokens) meets or exceeds a configured ceiling.

Why: wave-26 critique — autonomy expanded to self-merging portfolio PRs on
green with no ceiling/cap/limit/abort anywhere in the harness. This is the cap.

Configuration (aesop.config.json):
  "limits": {
    "max_wave_tokens": null,   # null = disabled (opt-in)
    "max_daily_tokens": null
  }

Spend source: an explicit --spent/spent= figure always wins. Otherwise spend
is read from the cost ledger (tools/fleet_ledger.py's OUTCOMES-LEDGER.md under
the resolved state dir): sum of tokens_in + tokens_out across all ledger rows
within the specified window (or all rows if no window specified).
A ledger that does not exist yet -> spend of 0 (never trips on a fresh install).
This is DISTINCT from a ledger that cannot be READ (permission denied, disk full,
lock contention): that raises, and the error path returns exceeded=True to abort
the current wave while leaving tripped=False, so a transient I/O fault stops work
without permanently wedging the fleet. Absent means zero; unreadable means stop.

WINDOW CONTRACT (shared with cost_projection.py):
  Both tools filter ledger rows by the SAME window parameters to ensure
  consistent spend calculations. The helper calculate_window_bounds() is
  imported by both modules to ensure identical window calculations.

  When both cost_projection.project(window_minutes=W) and cost_ceiling.check(window_minutes=W)
  are called with the same W value, they will produce identical spend figures.

API:
  check(spent=None, period="wave", config=None, state_dir=None, trip=True,
        window_minutes=None) -> dict
    Returns {"period", "ceiling", "spent", "exceeded", "tripped"}.
    When exceeded and trip=True, calls tools/halt.py's halt() with a reason
    describing the breach, and "tripped" is True. When ceiling is None
    (unconfigured), exceeded is always False and nothing is ever tripped.

    window_minutes: Optional time window in minutes for ledger filtering.
    If None, uses all ledger rows (backward compat). If specified, only rows
    within the window contribute to spend calculation.

CLI:
  python tools/cost_ceiling.py --check --spent N [--period wave|daily] [--window MINUTES]
    Exit 0 if not exceeded (or ceiling unconfigured), exit 1 if exceeded
    (and thus tripped, unless already halted).

READ-ONLY CHECK CONTRACT:
  --check never mutates the state tree while reading spend. Resolving spend from
  the ledger must not create <state>/ledger/OUTCOMES-LEDGER.md; header creation
  lives on fleet_ledger's APPEND path (ensure_ledger_header). The only write a
  check is ever allowed to make is the .HALT sentinel on a genuine ceiling breach
  with trip=True.
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    import halt
    import fleet_ledger
except ImportError:
    from tools import halt
    from tools import fleet_ledger


def calculate_window_bounds(window_minutes):
    """Calculate UTC window start and end times for ledger filtering.

    Shared contract between cost_projection.py and cost_ceiling.py to ensure
    consistent ledger windowing. Both tools import and use this helper to
    guarantee agreement on spend figures when using the same window.

    Args:
        window_minutes: Time window in minutes (e.g., 30 = last 30 minutes)

    Returns:
        Tuple of (window_start_utc, window_end_utc) as datetime objects with UTC timezone.
        window_end_utc is always datetime.now(timezone.utc).
        window_start_utc is window_end_utc minus window_minutes.
    """
    now_utc = datetime.now(timezone.utc)
    window_start = now_utc - timedelta(minutes=window_minutes)
    return window_start, now_utc


def load_config():
    """Load aesop.config.json from current directory, return dict (or {} if absent/bad)."""
    config_file = Path("aesop.config.json")
    if not config_file.exists():
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[cost_ceiling] Failed to load config: {e}", file=sys.stderr)
        return {}


def get_ceiling(config, period):
    """Extract the configured ceiling for `period` ('wave' or 'daily'), or None."""
    limits = config.get("limits", {}) if isinstance(config, dict) else {}
    if not isinstance(limits, dict):
        return None
    key = f"max_{period}_tokens"
    value = limits.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read_ledger_total_tokens(state_dir, period="wave", window_minutes=None):
    """Sum tokens_in + tokens_out from OUTCOMES-LEDGER.md with optional windowing.

    Args:
        state_dir: path to state directory
        period: "wave" (all rows) or "daily" (today's rows only, filtered by UTC date)
        window_minutes: Optional time window in minutes. If specified, only rows
                       within the last window_minutes are included. Default None
                       means include all rows (backward compat).

    Returns 0 if the ledger doesn't exist or is unreadable/empty.
    Uses fleet_ledger.py's shared parser (single source of truth).

    Window contract: When both cost_projection and cost_ceiling use the same
    window_minutes value, they produce identical spend figures (single source
    of truth is the ledger parse result, and the same filtering is applied).
    """
    # Set the state root temporarily so fleet_ledger can find the ledger
    import os
    old_state_root = os.environ.get("AESOP_STATE_ROOT")
    try:
        os.environ["AESOP_STATE_ROOT"] = str(state_dir)
        rows = fleet_ledger.parse_ledger_rows()
    finally:
        if old_state_root is not None:
            os.environ["AESOP_STATE_ROOT"] = old_state_root
        else:
            os.environ.pop("AESOP_STATE_ROOT", None)

    if not rows:
        return 0

    # Calculate window bounds if windowing is requested
    if window_minutes is not None:
        window_start, window_end = calculate_window_bounds(window_minutes)
    else:
        window_start = None  # No window filtering

    total = 0

    if period == "daily":
        # Filter to today's UTC date only
        today_utc = datetime.now(timezone.utc).date()
        for row in rows:
            # Extract date from ISO timestamp (format: YYYY-MM-DDTHH:MM:SSZ or similar)
            try:
                iso_ts = row['iso_ts']
                # Parse ISO timestamp
                ts_clean = iso_ts.replace('Z', '+00:00') if 'Z' in iso_ts else iso_ts
                row_dt = datetime.fromisoformat(ts_clean)
                if row_dt.tzinfo is None:
                    row_dt = row_dt.replace(tzinfo=timezone.utc)

                # Check date filter
                row_date = row_dt.date()
                if row_date != today_utc:
                    continue

                # Check window filter (if specified)
                if window_start is not None and row_dt < window_start:
                    continue

                total += row['tokens_in'] + row['tokens_out']
            except (ValueError, IndexError, KeyError, AttributeError):
                # Skip malformed timestamps
                continue
    else:
        # period == "wave": sum rows within window (or all if no window)
        for row in rows:
            # Check window filter (if specified)
            if window_start is not None:
                try:
                    iso_ts = row['iso_ts']
                    ts_clean = iso_ts.replace('Z', '+00:00') if 'Z' in iso_ts else iso_ts
                    row_dt = datetime.fromisoformat(ts_clean)
                    if row_dt.tzinfo is None:
                        row_dt = row_dt.replace(tzinfo=timezone.utc)

                    if row_dt < window_start:
                        continue
                except (ValueError, AttributeError):
                    # Skip malformed timestamps
                    continue

            total += row['tokens_in'] + row['tokens_out']

    return total


def check(spent=None, period="wave", config=None, state_dir=None, trip=True, window_minutes=None):
    """Check spend against the configured ceiling for `period`.

    Returns a dict: {"period", "ceiling", "spent", "exceeded", "tripped", "reason"}.

    Window contract: when cost_ceiling and cost_projection both pass the same
    window_minutes value, they will compute identical spend figures from the
    ledger, ensuring agreement on cost tracking.

    Distinctions:
    - Genuine ceiling breach (ceiling is configured, spent >= ceiling): when trip=True,
      writes persistent .HALT sentinel via halt.halt() and sets tripped=True.
    - Exception during spend computation (ledger read/create failure, etc.): signals
      abort of current wave (exceeded=True) but does NOT write persistent sentinel
      (tripped=False). This preserves fleet availability across transient I/O errors
      (e.g., momentary file lock, disk full) while still aborting the current wave.

    Args:
        spent: Optional explicit spend figure in tokens. If provided, overrides ledger.
        period: "wave" (all rows) or "daily" (today's rows only)
        config: aesop.config.json dict, or None to load from disk
        state_dir: path to state directory, or None to resolve from config
        trip: If True and exceeded, trip the .HALT sentinel
        window_minutes: Optional time window in minutes for ledger filtering.
                       If None, all ledger rows are included (backward compat).
                       When specified, only rows within the last window_minutes
                       are included. MUST match the window_minutes used in
                       cost_projection.project() to ensure agreement.
    """
    try:
        if config is None:
            config = load_config()
        if state_dir is None:
            state_dir = halt.resolve_state_dir(config=config)
        state_dir = Path(state_dir)

        ceiling = get_ceiling(config, period)

        if spent is None:
            spent = read_ledger_total_tokens(state_dir, period=period, window_minutes=window_minutes)
        spent = int(spent)

        exceeded = ceiling is not None and spent >= ceiling
        tripped = False

        if exceeded and trip:
            reason = (
                f"cost ceiling exceeded: {period} spend {spent} tokens >= "
                f"ceiling {ceiling} tokens"
            )
            halt.halt(reason, state_dir=state_dir)
            tripped = True

        return {
            "period": period,
            "ceiling": ceiling,
            "spent": spent,
            "exceeded": exceeded,
            "tripped": tripped,
            "reason": None,
        }
    except Exception as e:
        # Exception during spend computation (e.g., ledger read/create failure,
        # file lock, transient I/O error). This is fail-SAFE: abort the current
        # wave (exceeded=True) to prevent runaway work, but do NOT write the
        # persistent .HALT sentinel (tripped=False). The distinction ensures a
        # transient I/O hiccup does not permanently wedge the fleet.
        reason_text = f"cost_check_error: {type(e).__name__}: {str(e)[:100]}"
        return {
            "period": period,
            "ceiling": None,
            "spent": None,
            "exceeded": True,
            "tripped": False,
            "error": str(e),
            "reason": reason_text,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Cost ceiling guard for the fleet kill switch.")
    parser.add_argument("--check", action="store_true", help="Run the ceiling check (required).")
    parser.add_argument("--spent", type=int, default=None, help="Explicit spend figure in tokens; defaults to ledger total.")
    parser.add_argument("--period", choices=("wave", "daily"), default="wave", help="Which ceiling to check against (default: wave).")
    parser.add_argument("--window", type=int, default=None, help="Optional time window in minutes for ledger filtering; None means all rows (default, backward compat).")
    args = parser.parse_args(argv)

    if not args.check:
        parser.print_usage(sys.stderr)
        return 2

    result = check(spent=args.spent, period=args.period, window_minutes=args.window)

    # Check for errors FIRST (fail-closed): exception during check() means abort the wave
    if "error" in result:
        reason = result.get("reason", result["error"])
        print(f"[cost_ceiling] FATAL: {reason}", file=sys.stderr)
        return 1

    if result["ceiling"] is None:
        print(f"[cost_ceiling] no {args.period} ceiling configured — skipping (spent={result['spent']})")
        return 0

    if result["exceeded"]:
        print(
            f"[cost_ceiling] EXCEEDED: {args.period} spend {result['spent']} >= "
            f"ceiling {result['ceiling']} — HALT tripped"
        )
        return 1

    print(f"[cost_ceiling] ok: {args.period} spend {result['spent']} < ceiling {result['ceiling']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
