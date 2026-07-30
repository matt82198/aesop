#!/usr/bin/env python3
"""Orchestrator context-bloat meter — measures when the orchestrator should checkpoint or clear.

Signals measured:
  (a) checkpoint_age: time since STATE.md and BUILDLOG.md were last modified
  (b) activity_since_checkpoint: count of orchestrator status updates and activity logs newer than checkpoint
  (c) token_ledger: optional token count from state/ledger/fleet-usage.jsonl (reported as UNAVAILABLE if absent)

Verdicts: OK / ADVISE-CHECKPOINT / ADVISE-CLEAR based on thresholds.

Usage:
  python orchestrator_context_meter.py [--check] [--json] [--state-root DIR]
      [--checkpoint-age-hours H] [--activity-threshold N] [--clear-threshold N]

Exit codes:
  0: OK (thresholds not exceeded)
  1: Advisory triggered (checkpoint or clear advised)
  2: Usage/error

No external dependencies. Read-only: never mutates state.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir


# Default thresholds (heuristic-based, not measured)
DEFAULT_CHECKPOINT_AGE_HOURS = 4.0
DEFAULT_ACTIVITY_CHECKPOINT_THRESHOLD = 10
DEFAULT_ACTIVITY_CLEAR_THRESHOLD = 20


def get_checkpoint_files():
    """Return paths to checkpoint files."""
    state_dir = get_state_dir()
    return {
        "STATE.md": Path.cwd() / "STATE.md",
        "BUILDLOG.md": state_dir / "BUILDLOG.md",
    }


def compute_checkpoint_age(checkpoint_files):
    """Measure checkpoint age in seconds.

    Returns a tuple of (age_seconds, status):
      age_seconds: maximum age of the checkpoint files, or None if unavailable
      status: 'OK', 'UNAVAILABLE', or error message
    """
    max_age = None
    missing_files = []

    for name, path in checkpoint_files.items():
        try:
            if not path.exists():
                missing_files.append(name)
                continue
            mtime = path.stat().st_mtime
            age = time.time() - mtime
            if max_age is None or age > max_age:
                max_age = age
        except OSError as e:
            return None, f"Cannot read {name}: {e}"

    if missing_files:
        return None, f"Checkpoint files missing: {', '.join(missing_files)}"

    if max_age is None:
        return None, "No checkpoint files found"

    return max_age, "OK"


def count_activity_since_checkpoint(checkpoint_files, state_dir=None):
    """Count activity indicators newer than checkpoint.

    Activity signals:
      - orchestrator-status.json updates
      - BUILDLOG modifications (already in checkpoint age but count each line as activity)
      - Other activity logs in state/ newer than checkpoint

    Returns tuple of (activity_count, status):
      activity_count: count of post-checkpoint activities, or None if unavailable
      status: 'OK', 'UNAVAILABLE', or error message
    """
    if state_dir is None:
        state_dir = get_state_dir()

    # Find the oldest checkpoint time
    checkpoint_time = None
    for path in checkpoint_files.values():
        try:
            if path.exists():
                mtime = path.stat().st_mtime
                if checkpoint_time is None or mtime < checkpoint_time:
                    checkpoint_time = mtime
        except OSError:
            pass

    if checkpoint_time is None:
        return None, "Cannot determine checkpoint time"

    activity_count = 0

    # Count orchestrator-status.json updates
    status_file = state_dir / "orchestrator-status.json"
    try:
        if status_file.exists():
            mtime = status_file.stat().st_mtime
            if mtime > checkpoint_time:
                # Count each status file update as one activity
                activity_count += 1
    except OSError:
        pass

    # Count BUILDLOG.md lines added since checkpoint
    buildlog_file = state_dir / "BUILDLOG.md"
    try:
        if buildlog_file.exists():
            with open(buildlog_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # Simple heuristic: count non-empty lines that look like log entries
            # as potential post-checkpoint activities
            # (This is a weak signal; mtime is stronger but we use both)
            activity_count += len([l for l in lines if l.strip() and not l.startswith("#")])
    except (OSError, UnicodeDecodeError):
        pass

    # Count other activity logs in state/ that are newer than checkpoint
    try:
        for item in state_dir.iterdir():
            if item.is_file() and item.suffix in [".log", ".jsonl"]:
                try:
                    mtime = item.stat().st_mtime
                    if mtime > checkpoint_time:
                        activity_count += 1
                except OSError:
                    pass
    except OSError:
        pass

    return activity_count, "OK"


def read_token_ledger(state_dir=None):
    """Read optional token ledger if it exists.

    Returns tuple of (token_count, status):
      token_count: sum of tokens used, or None if unavailable
      status: 'OK', 'UNAVAILABLE', or error message
    """
    if state_dir is None:
        state_dir = get_state_dir()

    ledger_file = state_dir / "ledger" / "fleet-usage.jsonl"

    if not ledger_file.exists():
        return None, "UNAVAILABLE"

    try:
        total_tokens = 0
        with open(ledger_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if "tokens" in entry:
                        total_tokens += entry["tokens"]
                except (json.JSONDecodeError, TypeError):
                    pass
        return total_tokens, "OK"
    except (OSError, UnicodeDecodeError):
        return None, "UNAVAILABLE"


def evaluate_verdict(checkpoint_age_s, activity_count, checkpoint_hours_threshold, activity_checkpoint_threshold, activity_clear_threshold):
    """Determine the verdict based on thresholds.

    Returns: (verdict_string, reason_string)
      verdict_string: 'OK', 'ADVISE-CHECKPOINT', or 'ADVISE-CLEAR'
      reason_string: human-readable explanation
    """
    checkpoint_age_hours = checkpoint_age_s / 3600.0 if checkpoint_age_s is not None else None

    # ADVISE-CLEAR if checkpoint age exceeds clear threshold OR activity exceeds clear threshold
    if checkpoint_age_hours is not None and checkpoint_age_hours > checkpoint_hours_threshold * 3:
        return "ADVISE-CLEAR", f"Checkpoint age {checkpoint_age_hours:.1f}h exceeds {checkpoint_hours_threshold * 3:.1f}h threshold"

    if activity_count is not None and activity_count > activity_clear_threshold:
        return "ADVISE-CLEAR", f"Activity count {activity_count} exceeds clear threshold {activity_clear_threshold}"

    # ADVISE-CHECKPOINT if checkpoint age exceeds advisory threshold OR activity exceeds checkpoint threshold
    if checkpoint_age_hours is not None and checkpoint_age_hours > checkpoint_hours_threshold:
        return "ADVISE-CHECKPOINT", f"Checkpoint age {checkpoint_age_hours:.1f}h exceeds {checkpoint_hours_threshold:.1f}h threshold"

    if activity_count is not None and activity_count > activity_checkpoint_threshold:
        return "ADVISE-CHECKPOINT", f"Activity count {activity_count} exceeds checkpoint threshold {activity_checkpoint_threshold}"

    # Default: OK
    return "OK", "Checkpoint fresh and activity low"


def emit_result(verdict, checkpoint_age_s, activity_count, tokens, json_output=False):
    """Emit the result as text or JSON."""
    if json_output:
        result = {
            "verdict": verdict[0],
            "reason": verdict[1],
            "signals": {
                "checkpoint_age_seconds": checkpoint_age_s,
                "activity_count": activity_count,
                "tokens_used": tokens,
            }
        }
        print(json.dumps(result, indent=2))
    else:
        print(f"Verdict: {verdict[0]}")
        print(f"Reason: {verdict[1]}")
        print(f"Signals:")
        if checkpoint_age_s is not None:
            checkpoint_hours = checkpoint_age_s / 3600.0
            print(f"  Checkpoint age: {checkpoint_hours:.1f} hours ({int(checkpoint_age_s)} seconds)")
        else:
            print(f"  Checkpoint age: UNAVAILABLE")
        print(f"  Activity count: {activity_count if activity_count is not None else 'UNAVAILABLE'}")
        if tokens is not None:
            print(f"  Tokens used: {tokens}")
        else:
            print(f"  Tokens used: UNAVAILABLE")


def main():
    parser = argparse.ArgumentParser(
        description="Orchestrator context-bloat meter"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Check mode (default); exit 0 if OK, 1 if advisory, 2 if error"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of text"
    )
    parser.add_argument(
        "--state-root",
        help="Override AESOP_STATE_ROOT"
    )
    parser.add_argument(
        "--checkpoint-age-hours",
        type=float,
        default=DEFAULT_CHECKPOINT_AGE_HOURS,
        help=f"Checkpoint age threshold in hours (default: {DEFAULT_CHECKPOINT_AGE_HOURS})"
    )
    parser.add_argument(
        "--activity-checkpoint-threshold",
        type=int,
        default=DEFAULT_ACTIVITY_CHECKPOINT_THRESHOLD,
        help=f"Activity count threshold for ADVISE-CHECKPOINT (default: {DEFAULT_ACTIVITY_CHECKPOINT_THRESHOLD})"
    )
    parser.add_argument(
        "--activity-clear-threshold",
        type=int,
        default=DEFAULT_ACTIVITY_CLEAR_THRESHOLD,
        help=f"Activity count threshold for ADVISE-CLEAR (default: {DEFAULT_ACTIVITY_CLEAR_THRESHOLD})"
    )

    args = parser.parse_args()

    # Override state root if provided
    if args.state_root:
        os.environ["AESOP_STATE_ROOT"] = args.state_root

    state_dir = get_state_dir()

    # Measure signals
    checkpoint_files = get_checkpoint_files()
    checkpoint_age_s, checkpoint_age_status = compute_checkpoint_age(checkpoint_files)

    if checkpoint_age_status != "OK":
        if not args.json:
            print(f"[ERROR] {checkpoint_age_status}", file=sys.stderr)
        else:
            print(json.dumps({
                "verdict": "ERROR",
                "reason": checkpoint_age_status,
                "signals": {}
            }))
        sys.exit(2)

    activity_count, activity_status = count_activity_since_checkpoint(checkpoint_files, state_dir)
    if activity_status != "OK":
        activity_count = None

    tokens, tokens_status = read_token_ledger(state_dir)
    # tokens_status may be UNAVAILABLE; that's OK, we report it

    # Evaluate verdict
    verdict = evaluate_verdict(
        checkpoint_age_s,
        activity_count,
        args.checkpoint_age_hours,
        args.activity_checkpoint_threshold,
        args.activity_clear_threshold
    )

    # Emit result
    emit_result(verdict, checkpoint_age_s, activity_count, tokens, args.json)

    # Return exit code
    if verdict[0] == "OK":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
