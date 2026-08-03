#!/usr/bin/env python3
"""
Wave History Summarizer — per-wave summaries from event store.
INDEX: Wave history CLI for per-wave event store analysis (guardrail G1): closes items whose linked PRs merged or whose ownsFiles shipped on main; CLI: `[--check | --dry-run]`; exit 0=all resolved, 1=items still open; timezone-aware UTC timestamps

Reads the event-sourced state store to produce per-wave summaries showing:
- Wave start/end timestamps
- Duration
- Number of items dispatched
- Number of events
- Event types distribution

Usage:
  python tools/wave_history.py [--json] [--latest N] [--state-root PATH]

Options:
  --json                 Output as JSON array instead of ASCII table
  --latest N             Show only last N waves (default: all)
  --state-root PATH      Path to state directory (default: AESOP_STATE_ROOT or ./state)
  --help                 Show this help message

Examples:
  # ASCII table of all waves
  python tools/wave_history.py

  # JSON output of last 5 waves
  python tools/wave_history.py --json --latest 5

  # Query a specific state directory
  python tools/wave_history.py --state-root /path/to/state

Exit codes:
  0 — Success
  1 — Missing database or other error
  2 — Usage error
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure tools and state_store are importable
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir


def format_timestamp(unix_ts):
    """Format Unix timestamp to ISO 8601 string.

    Args:
        unix_ts: Unix timestamp (float)

    Returns:
        str: ISO 8601 formatted timestamp
    """
    if unix_ts is None:
        return "N/A"
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
    return dt.isoformat()


def extract_wave_id(event):
    """Extract wave_id from event.

    Checks event payload for wave_id field. Also handles stream names
    that may contain wave identifiers.

    Args:
        event: Event dict with 'stream', 'payload', 'type' keys

    Returns:
        str: wave_id or None if not found
    """
    payload = event.get("payload") or {}
    wave_id = payload.get("wave_id")
    if wave_id:
        return wave_id

    # Check stream name for wave identifiers (e.g., "wave-123")
    stream = event.get("stream", "")
    if stream.startswith("wave"):
        return stream

    return None


def group_events_by_wave(events):
    """Group events by wave.

    Args:
        events: List of event dicts from the state store

    Returns:
        dict: Mapping of wave_id to list of events
    """
    waves = defaultdict(list)
    for event in events:
        wave_id = extract_wave_id(event)
        if wave_id:
            waves[wave_id].append(event)

    return dict(waves)


def compute_wave_metrics(wave_id, events):
    """Compute metrics for a single wave.

    Args:
        wave_id: Wave identifier
        events: List of events for this wave

    Returns:
        dict: Metrics including start, end, duration, event counts
    """
    if not events:
        return None

    # Sort events by timestamp
    sorted_events = sorted(events, key=lambda e: e.get("ts", 0))

    start_ts = sorted_events[0].get("ts")
    end_ts = sorted_events[-1].get("ts")

    duration_sec = (end_ts - start_ts) if start_ts and end_ts else 0

    # Count event types
    event_types = defaultdict(int)
    for event in events:
        etype = event.get("type", "unknown")
        event_types[etype] += 1

    # Count unique streams
    streams = set(e.get("stream", "") for e in events)

    return {
        "wave_id": wave_id,
        "start": start_ts,
        "end": end_ts,
        "duration_sec": duration_sec,
        "event_count": len(events),
        "stream_count": len(streams),
        "streams": sorted(list(streams)),
        "event_types": dict(event_types),
    }


def format_ascii_table(wave_metrics):
    """Format wave metrics as ASCII table.

    Args:
        wave_metrics: List of metrics dicts

    Returns:
        str: ASCII table
    """
    if not wave_metrics:
        return "No waves found in event store."

    # Header
    lines = [
        "Wave                         | Start                   | Duration (sec) | Events | Streams",
        "-" * 95,
    ]

    for metrics in wave_metrics:
        wave_id = metrics["wave_id"]
        start = format_timestamp(metrics["start"])
        duration = int(metrics["duration_sec"])
        event_count = metrics["event_count"]
        stream_count = metrics["stream_count"]

        line = f"{wave_id:<28} | {start:<23} | {duration:>14} | {event_count:>6} | {stream_count:>7}"
        lines.append(line)

    return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Per-wave history summarizer from event store",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--latest", type=int, default=None, help="Show only last N waves")
    parser.add_argument("--state-root", default=None, help="Path to state directory")

    try:
        args = parser.parse_args()
    except SystemExit as e:
        if e.code != 0:
            return 2
        return 0

    # Determine state directory
    state_dir = args.state_root or os.environ.get("AESOP_STATE_ROOT") or "./state"
    state_path = Path(state_dir)

    # Check if state directory exists
    if not state_path.exists():
        print(f"Error: state directory not found: {state_dir}", file=sys.stderr)
        return 1

    # Check if database exists
    db_path = state_path / "tracker_events.db"
    if not db_path.exists():
        if args.json:
            print(json.dumps([]))
        else:
            print("No waves found in event store.")
        return 0

    # Load events from state store
    try:
        from state_store import StateAPI

        api = StateAPI(str(db_path))
        try:
            all_events = api.get("wave")
        finally:
            api.close()
    except Exception as e:
        print(f"Error reading event store: {e}", file=sys.stderr)
        return 1

    if not all_events:
        if args.json:
            print(json.dumps([]))
        else:
            print("No waves found in event store.")
        return 0

    # Group events by wave and compute metrics
    waves_dict = group_events_by_wave(all_events)
    all_metrics = []

    for wave_id in sorted(waves_dict.keys()):
        metrics = compute_wave_metrics(wave_id, waves_dict[wave_id])
        if metrics:
            all_metrics.append(metrics)

    # Apply --latest filter
    if args.latest:
        all_metrics = all_metrics[-args.latest :]

    # Output
    if args.json:
        output = json.dumps(all_metrics, indent=2, default=str)
        print(output)
    else:
        output = format_ascii_table(all_metrics)
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
