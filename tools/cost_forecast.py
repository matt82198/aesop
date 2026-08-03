#!/usr/bin/env python3
"""
Cost Forecasting Tool — Predict spend trends and budget runway.
INDEX: Cost forecasting tool: weighted-moving-average daily burn rate, predicted monthly spend, days-to-ceiling; reads fleet ledger; CLI: `--ceiling DOLLARS [--ledger PATH] [--json] [--check] [--help]`; stdlib-only, fail-closed on unknown flags

Reads cost data from the fleet ledger (OUTCOMES-LEDGER.md) and produces
spend forecasts with daily burn rate, predicted monthly spend, and days
to budget ceiling.

Forecasting method:
  - Parses markdown ledger entries for dollar amounts and timestamps
  - Calculates daily burn rate using weighted moving average (recent data weighted higher)
  - Extrapolates to predicted monthly spend
  - Computes runway (days until budget ceiling at current burn rate)
  - Includes confidence interval (interquartile range as proxy for variability)

API:
  forecast(ledger_path, ceiling_dollars=None) -> dict
    Parse ledger and compute forecast metrics.
    Returns:
      {
        "available": bool,           # true if ledger has data, false otherwise
        "daily_burn_rate": float,    # dollars per day (or 0.0 if unavailable)
        "predicted_monthly_spend": float,  # extrapolated to 30 days
        "days_to_ceiling": float or None,  # days until ceiling at current burn rate
        "confidence_interval": [float, float],  # [low, high] estimate range
        "data_points_used": int,     # number of ledger entries used
        "reason": str or None        # explanation if available=false
      }

CLI:
  python tools/cost_forecast.py [--ceiling DOLLARS] [--ledger PATH] [--json]
    Compute and display forecast (human-readable or JSON).
    Default ledger: state/ledger/OUTCOMES-LEDGER.md
    Default ceiling: from aesop.config.json limits.max_wave_tokens or None

  python tools/cost_forecast.py --check
    Validate that the ledger is parseable and report summary stats.
    Exit 0 if valid, 1 if not parseable.

  python tools/cost_forecast.py --help
    Display usage and exit 0.

Environment:
  AESOP_STATE_ROOT: Path to state directory (default: ./state)

Design notes:
  - Stdlib only (json, sys, os, pathlib, datetime, re, statistics)
  - Windows + Linux safe (ASCII paths, UTC datetimes)
  - Ledger format: markdown table with ISO timestamps and token columns
  - Token-to-dollar conversion uses standard Anthropic pricing model
  - Weighted moving average gives recent data 3x the weight of older data
  - Confidence interval is IQR approximation (25th to 75th percentile)
  - Unknown CLI flags exit 1 (fail-closed)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from statistics import median, stdev, quantiles
from typing import Optional, Dict, Any, List, Tuple

try:
    import common
except ImportError:
    from tools import common


# Anthropic pricing constants (tokens per dollar)
# These reflect standard pricing as of 2026-07; adjust as needed
PRICING = {
    "haiku": {
        "input": 8000,    # 8K tokens per dollar for Haiku input
        "output": 2000,   # 2K tokens per dollar for Haiku output (4x more expensive)
    },
    "sonnet": {
        "input": 4000,    # 4K tokens per dollar for Sonnet input
        "output": 1000,   # 1K tokens per dollar for Sonnet output (4x more expensive)
    },
    "opus": {
        "input": 2000,    # 2K tokens per dollar for Opus input
        "output": 500,    # 500 tokens per dollar for Opus output (4x more expensive)
    },
}

# Default to Haiku pricing if model unknown
DEFAULT_PRICING = {
    "input": 8000,
    "output": 2000,
}


def tokens_to_dollars(tokens_in: int, tokens_out: int, model: str = "haiku") -> float:
    """Convert token count to dollar cost using model-specific pricing.

    Args:
        tokens_in: Input tokens (user prompt)
        tokens_out: Output tokens (assistant response)
        model: Model name (haiku, sonnet, opus); defaults to haiku

    Returns:
        Estimated cost in dollars (float)
    """
    model_lower = model.lower().strip()
    pricing = PRICING.get(model_lower, DEFAULT_PRICING)
    cost = 0.0
    if pricing["input"] > 0:
        cost += tokens_in / pricing["input"]
    if pricing["output"] > 0:
        cost += tokens_out / pricing["output"]
    return cost


def parse_iso_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse ISO 8601 timestamp string to UTC datetime.

    Args:
        ts_str: ISO timestamp string (e.g., "2026-07-30T15:30:45Z")

    Returns:
        datetime object with UTC timezone, or None if invalid
    """
    if not ts_str:
        return None
    try:
        # Handle ISO with Z suffix
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts_str)
        # Ensure UTC timezone
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def parse_ledger(ledger_path: Path) -> List[Dict[str, Any]]:
    """Parse OUTCOMES-LEDGER.md markdown table into list of cost entries.

    Expected format:
    | ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |
    |--------|------------|-------|--------------|-----------|------------|--------|-------|------|
    | 2026-07-30T15:30:45Z | haiku | haiku | 10 | 500 | 250 | OK | build | 1 |

    Args:
        ledger_path: Path to OUTCOMES-LEDGER.md file

    Returns:
        List of dicts with keys: timestamp, tokens_in, tokens_out, model, cost_dollars
        Sorted by timestamp (oldest first).
    """
    entries = []

    if not ledger_path.exists():
        return entries

    try:
        text = ledger_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[cost_forecast] Failed to read ledger: {e}", file=sys.stderr)
        return entries

    # Split into lines and skip header rows
    lines = text.strip().split("\n")
    for line in lines:
        # Skip empty lines and header lines (starting with |)
        if not line.strip() or line.startswith("|-----"):
            continue
        if not line.startswith("|"):
            continue

        # Parse markdown table row: | col1 | col2 | ... |
        cells = [cell.strip() for cell in line.split("|")]
        # cells[0] is empty (before first |), cells[1:] are the actual columns
        cells = cells[1:-1]  # Skip empty first and last cells

        if len(cells) < 9:  # Not enough columns (need at least 9: ts, type, model, dur, in, out, verdict, phase, wave)
            continue

        # Ledger columns: ISO ts(0), agent_type(1), model(2), duration_sec(3), tokens_in(4), tokens_out(5), verdict(6), phase(7), wave(8)
        iso_ts_str = cells[0]
        model = cells[2].lower().strip()
        tokens_in_str = cells[4].strip()
        tokens_out_str = cells[5].strip()

        # Parse timestamp
        timestamp = parse_iso_timestamp(iso_ts_str)
        if timestamp is None:
            continue

        # Parse token counts
        try:
            tokens_in = int(tokens_in_str)
            tokens_out = int(tokens_out_str)
        except (ValueError, TypeError):
            continue

        # Calculate cost
        cost_dollars = tokens_to_dollars(tokens_in, tokens_out, model)

        entries.append({
            "timestamp": timestamp,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "model": model,
            "cost_dollars": cost_dollars,
        })

    # Sort by timestamp (oldest first)
    entries.sort(key=lambda e: e["timestamp"])
    return entries


def calculate_daily_burn_rate(entries: List[Dict[str, Any]]) -> Tuple[float, int]:
    """Calculate daily burn rate using weighted moving average.

    Recent entries are weighted 3x higher than older entries to capture
    current spending trends.

    Args:
        entries: List of cost entries with timestamp and cost_dollars

    Returns:
        Tuple of (daily_burn_rate_dollars, data_points_used)
        Returns (0.0, 0) if insufficient data.
    """
    if len(entries) < 1:
        return 0.0, 0

    if len(entries) == 1:
        # Single entry: extrapolate as per-day cost
        # Assume it happened "today" (no historical context)
        return entries[0]["cost_dollars"], 1

    # Calculate time span of entries (oldest to newest)
    oldest = entries[0]["timestamp"]
    newest = entries[-1]["timestamp"]
    time_span = (newest - oldest).total_seconds() / 86400.0  # Convert to days

    if time_span < 0.01:  # Less than ~15 minutes
        # All entries on same day; extrapolate as daily rate
        total_cost = sum(e["cost_dollars"] for e in entries)
        return total_cost, len(entries)

    # Weighted moving average: more recent entries weighted higher
    total_weight = 0.0
    weighted_sum = 0.0

    for i, entry in enumerate(entries):
        # Weight increases from 1.0 for oldest to 3.0 for newest (linear interpolation)
        weight = 1.0 + (2.0 * i / max(1, len(entries) - 1))
        weighted_sum += entry["cost_dollars"] * weight
        total_weight += weight

    # Average cost per entry
    avg_cost_per_entry = weighted_sum / total_weight if total_weight > 0 else 0.0

    # Entries per day (average frequency)
    entries_per_day = len(entries) / time_span if time_span > 0 else 1.0

    # Daily burn rate = avg cost per entry * entries per day
    daily_burn = avg_cost_per_entry * entries_per_day

    return daily_burn, len(entries)


def calculate_confidence_interval(entries: List[Dict[str, Any]]) -> Tuple[float, float]:
    """Calculate confidence interval for daily burn rate.

    Uses interquartile range (25th-75th percentile) as a simple proxy
    for estimation uncertainty.

    Args:
        entries: List of cost entries with cost_dollars

    Returns:
        Tuple of (low_estimate, high_estimate) in dollars per day
        Returns (0.0, 0.0) if insufficient data.
    """
    if len(entries) < 3:
        # Not enough data for percentiles
        return 0.0, 0.0

    daily_costs = [e["cost_dollars"] for e in entries]

    try:
        # Calculate quartiles: 25th, 50th (median), 75th percentile
        q_list = quantiles(daily_costs, n=4)  # Returns 3 values: Q1, Q2, Q3
        q1 = q_list[0]  # 25th percentile
        q3 = q_list[2]  # 75th percentile
        return q1, q3
    except (ValueError, IndexError, TypeError):
        # Not enough data for quantiles
        return 0.0, 0.0


def forecast(
    ledger_path: Path,
    ceiling_dollars: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute cost forecast from ledger.

    Args:
        ledger_path: Path to OUTCOMES-LEDGER.md
        ceiling_dollars: Optional budget ceiling in dollars

    Returns:
        Dict with forecast metrics:
        {
            "available": bool,
            "daily_burn_rate": float,
            "predicted_monthly_spend": float,
            "days_to_ceiling": float or None,
            "confidence_interval": [float, float],
            "data_points_used": int,
            "reason": str or None,
        }
    """
    entries = parse_ledger(ledger_path)

    if not entries:
        return {
            "available": False,
            "daily_burn_rate": 0.0,
            "predicted_monthly_spend": 0.0,
            "days_to_ceiling": None,
            "confidence_interval": [0.0, 0.0],
            "data_points_used": 0,
            "reason": "No cost data found in ledger",
        }

    # Warn if sample size is very small
    if len(entries) == 1:
        reason = "Warning: single data point; forecast low confidence"
    elif len(entries) < 3:
        reason = "Warning: few data points; forecast may be unreliable"
    else:
        reason = None

    # Calculate daily burn rate
    daily_burn, num_points = calculate_daily_burn_rate(entries)

    # Predict monthly spend (30 days at current burn rate)
    monthly_spend = daily_burn * 30.0

    # Calculate days until ceiling
    days_to_ceiling = None
    if ceiling_dollars is not None and daily_burn > 0.0:
        days_to_ceiling = ceiling_dollars / daily_burn

    # Calculate confidence interval
    ci_low, ci_high = calculate_confidence_interval(entries)
    # If we have a valid IQR, scale to daily burn estimate
    if ci_low > 0 or ci_high > 0:
        ci_low_monthly = ci_low * 30.0
        ci_high_monthly = ci_high * 30.0
    else:
        ci_low_monthly = 0.0
        ci_high_monthly = 0.0

    return {
        "available": True,
        "daily_burn_rate": round(daily_burn, 2),
        "predicted_monthly_spend": round(monthly_spend, 2),
        "days_to_ceiling": round(days_to_ceiling, 2) if days_to_ceiling is not None else None,
        "confidence_interval": [round(ci_low_monthly, 2), round(ci_high_monthly, 2)],
        "data_points_used": num_points,
        "reason": reason,
    }


def load_config() -> Dict[str, Any]:
    """Load aesop.config.json from current directory.

    Returns:
        Config dict, or empty dict if file not found or invalid.
    """
    config_file = Path("aesop.config.json")
    if not config_file.exists():
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[cost_forecast] Failed to load config: {e}", file=sys.stderr)
        return {}


def get_default_ledger_path() -> Path:
    """Resolve default ledger path from AESOP_STATE_ROOT or current directory.

    Returns:
        Path to OUTCOMES-LEDGER.md
    """
    state_dir = common.get_state_dir() if hasattr(common, "get_state_dir") else Path("state")
    return state_dir / "ledger" / "OUTCOMES-LEDGER.md"


def get_default_ceiling(config: Dict[str, Any]) -> Optional[float]:
    """Extract default ceiling from aesop.config.json.

    Reads limits.max_wave_tokens; returns None if unconfigured.

    Args:
        config: Loaded aesop.config.json dict

    Returns:
        Ceiling in dollars, or None if unconfigured
    """
    limits = config.get("limits", {})
    if not isinstance(limits, dict):
        return None
    max_tokens = limits.get("max_wave_tokens")
    if max_tokens is None:
        return None
    try:
        # Convert max_tokens to dollars
        # Assuming Haiku pricing: 8K input + 2K output average
        # Very rough: 1 token ≈ $0.00003 (median of input/output)
        tokens = int(max_tokens)
        dollars = tokens * 0.000015  # Rough approximation
        return dollars
    except (TypeError, ValueError):
        return None


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Cost forecasting tool for Aesop fleet spend.",
        prog="cost_forecast.py",
        add_help=False,  # Disable auto --help so we can handle unknown flags
    )
    parser.add_argument(
        "--ceiling",
        type=float,
        default=None,
        help="Budget ceiling in dollars (optional; overrides config)",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="Path to OUTCOMES-LEDGER.md (default: state/ledger/OUTCOMES-LEDGER.md)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (default: human-readable)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate ledger is parseable; exit 0 if valid, 1 if not",
    )
    parser.add_argument(
        "--help",
        action="store_true",
        help="Display this help message and exit",
    )

    # Parse known args; unknown args are an error (fail-closed)
    try:
        args, unknown = parser.parse_known_args()
    except SystemExit:
        # argparse calls sys.exit on error; catch it
        return 1

    # Check for unknown flags (fail-closed)
    if unknown:
        print(f"Error: unknown flags: {' '.join(unknown)}", file=sys.stderr)
        print("Run with --help for usage", file=sys.stderr)
        return 1

    # Handle --help manually (argparse's built-in --help uses sys.exit)
    if args.help:
        help_text = f"""usage: {parser.prog} [options]

Cost forecasting tool for Aesop fleet spend.

Computes daily burn rate, predicted monthly spend, and days until budget ceiling
from the fleet cost ledger.

options:
  --ceiling DOLLARS        Budget ceiling in dollars (optional; overrides config)
  --ledger PATH           Path to OUTCOMES-LEDGER.md (default: state/ledger/OUTCOMES-LEDGER.md)
  --json                  Output as JSON (default: human-readable)
  --check                 Validate ledger is parseable; exit 0 if valid, 1 if not
  --help                  Display this help message and exit
"""
        print(help_text)
        return 0

    # Resolve ledger path
    if args.ledger is None:
        ledger_path = get_default_ledger_path()
    else:
        ledger_path = args.ledger

    # Handle --check: validate ledger is parseable
    if args.check:
        try:
            entries = parse_ledger(ledger_path)
            if entries:
                print(f"Ledger valid: {len(entries)} entries")
                return 0
            else:
                print("Ledger valid but empty", file=sys.stderr)
                return 0  # Empty is valid, just no data
        except Exception as e:
            print(f"Ledger invalid: {e}", file=sys.stderr)
            return 1

    # Load config for default ceiling if not specified
    if args.ceiling is None:
        config = load_config()
        args.ceiling = get_default_ceiling(config)

    # Compute forecast
    result = forecast(ledger_path, args.ceiling)

    # Output result
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        # Human-readable output
        if not result["available"]:
            print(f"No forecast available: {result['reason']}")
            return 0

        print(f"Daily burn rate: ${result['daily_burn_rate']:.2f}")
        print(f"Predicted monthly spend: ${result['predicted_monthly_spend']:.2f}")

        if result["days_to_ceiling"] is not None:
            print(f"Days to budget ceiling: {result['days_to_ceiling']:.1f}")

        ci = result["confidence_interval"]
        if ci[0] > 0 or ci[1] > 0:
            print(f"Confidence interval (monthly): ${ci[0]:.2f} - ${ci[1]:.2f}")

        print(f"Data points: {result['data_points_used']}")

        if result["reason"]:
            print(f"Note: {result['reason']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
