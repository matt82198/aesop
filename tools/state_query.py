#!/usr/bin/env python3
"""state_query — CLI time-travel state query tool for event-sourced state store.

Query the event-sourced state store (SQLite WAL) with temporal and stream filters.

Usage:
  python tools/state_query.py [options]

Options:
  --stream STREAM        Filter by stream name (e.g., "wave", "agent", "tracker")
  --after ISO_TS         Only events after this ISO timestamp
  --before ISO_TS        Only events before this ISO timestamp
  --version-range N:M    Event version range per stream (e.g., "1:10")
  --type EVENT_TYPE      Filter by event type
  --json                 Output as JSON array instead of ASCII table
  --aggregate            Show aggregate stats (count, first/last timestamp per stream)
  --limit N              Max events to return (default: 100)
  --help                 Show this help message

Examples:
  # All events
  python tools/state_query.py

  # Events in wave stream
  python tools/state_query.py --stream wave

  # Events after a specific time
  python tools/state_query.py --after 2026-07-30T12:00:00Z

  # Combine filters
  python tools/state_query.py --stream wave --type dispatch_started --limit 50

  # JSON output
  python tools/state_query.py --json --limit 10

  # Aggregate statistics
  python tools/state_query.py --aggregate
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure tools module is importable
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


def parse_iso_timestamp(ts_str):
    """Parse ISO 8601 timestamp string to Unix timestamp (float).

    Args:
        ts_str: ISO 8601 timestamp string (e.g., "2026-07-30T12:00:00Z")

    Returns:
        float: Unix timestamp
    """
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.timestamp()
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid ISO timestamp '{ts_str}': {e}")


def format_timestamp(unix_ts):
    """Format Unix timestamp to ISO 8601 string.

    Args:
        unix_ts: Unix timestamp (float)

    Returns:
        str: ISO 8601 formatted timestamp
    """
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
    return dt.isoformat()


def query_events(api, stream=None, after_ts=None, before_ts=None, event_type=None):
    """Query events from the state store with filters.

    Args:
        api: StateAPI instance
        stream: Filter by stream name (optional)
        after_ts: Unix timestamp lower bound (optional)
        before_ts: Unix timestamp upper bound (optional)
        event_type: Filter by event type (optional)

    Returns:
        list: Filtered event dicts, sorted by timestamp ascending
    """
    from state_store import StateAPI

    # If stream filter is specified, query just that stream
    if stream:
        all_events = api.get(stream)
    else:
        # Otherwise read all events
        db_path = api._store.db_path
        from state_store import EventStore
        store = EventStore(db_path)
        all_events = store.read_all()
        store.close()

    # Apply filters
    filtered = []
    for event in all_events:
        # Stream filter
        if stream and event["stream"] != stream:
            continue

        # Temporal filters
        ts = float(event["ts"])
        if after_ts is not None and ts < after_ts:
            continue
        if before_ts is not None and ts > before_ts:
            continue

        # Event type filter
        if event_type and event["type"] != event_type:
            continue

        filtered.append(event)

    # Sort by timestamp ascending
    filtered.sort(key=lambda e: float(e["ts"]))

    return filtered


def apply_version_range_filter(events, version_range):
    """Apply version range filter to events (per stream).

    Args:
        events: List of event dicts
        version_range: String like "1:10" or "5:" or ":10"

    Returns:
        list: Filtered events
    """
    if not version_range:
        return events

    # Parse the range
    parts = version_range.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid version range '{version_range}'; use format N:M")

    start_str, end_str = parts
    start = int(start_str) if start_str else 0
    end = int(end_str) if end_str else float("inf")

    # Filter per-stream
    return [e for e in events if start <= e["version"] <= end]


def format_ascii_table(events, max_rows=None):
    """Format events as ASCII table.

    Args:
        events: List of event dicts
        max_rows: Max rows to display (optional)

    Returns:
        str: ASCII table as string
    """
    if max_rows:
        events = events[:max_rows]

    if not events:
        return "timestamp                   | stream    | version | type"

    # Calculate column widths
    widths = {
        "timestamp": max(19, max(len(format_timestamp(e["ts"])) for e in events)) if events else 19,
        "stream": max(6, max(len(e["stream"]) for e in events)) if events else 6,
        "version": max(7, len(str(max(e["version"] for e in events)))) if events else 7,
        "type": max(4, max(len(e["type"]) for e in events)) if events else 4,
    }

    # Header
    lines = []
    header = "timestamp".ljust(widths["timestamp"]) + " | "
    header += "stream".ljust(widths["stream"]) + " | "
    header += "version".ljust(widths["version"]) + " | "
    header += "type".ljust(widths["type"])
    lines.append(header)

    # Separator
    sep = "-" * widths["timestamp"] + "-+-"
    sep += "-" * widths["stream"] + "-+-"
    sep += "-" * widths["version"] + "-+-"
    sep += "-" * widths["type"]
    lines.append(sep)

    # Rows
    for event in events:
        ts_str = format_timestamp(event["ts"])
        row = ts_str.ljust(widths["timestamp"]) + " | "
        row += event["stream"].ljust(widths["stream"]) + " | "
        row += str(event["version"]).ljust(widths["version"]) + " | "
        row += event["type"].ljust(widths["type"])
        lines.append(row)

    return "\n".join(lines)


def format_json(events, max_rows=None):
    """Format events as JSON array.

    Args:
        events: List of event dicts
        max_rows: Max rows to include (optional)

    Returns:
        str: JSON array as string
    """
    if max_rows:
        events = events[:max_rows]

    # Convert timestamps to ISO strings in output
    output_events = []
    for event in events:
        output_event = dict(event)
        output_event["timestamp"] = format_timestamp(event["ts"])
        # Remove the 'ts' key to avoid duplication
        output_event.pop("ts", None)
        output_events.append(output_event)

    return json.dumps(output_events, indent=2)


def format_aggregate(events):
    """Format aggregate statistics per stream.

    Args:
        events: List of event dicts

    Returns:
        str: Aggregate statistics as ASCII table
    """
    # Group by stream
    by_stream = {}
    for event in events:
        stream = event["stream"]
        if stream not in by_stream:
            by_stream[stream] = []
        by_stream[stream].append(event)

    # Calculate stats per stream
    lines = []
    header = "stream    | count | first_timestamp         | last_timestamp"
    lines.append(header)
    lines.append("-" * len(header))

    for stream in sorted(by_stream.keys()):
        stream_events = by_stream[stream]
        count = len(stream_events)
        first_ts = format_timestamp(stream_events[0]["ts"])
        last_ts = format_timestamp(stream_events[-1]["ts"])

        row = stream.ljust(9) + " | "
        row += str(count).ljust(5) + " | "
        row += first_ts + " | "
        row += last_ts
        lines.append(row)

    return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Query the event-sourced state store with temporal and stream filters.",
        add_help=True
    )

    parser.add_argument(
        "--stream",
        help="Filter by stream name (e.g., 'wave', 'agent', 'tracker')",
        default=None
    )
    parser.add_argument(
        "--after",
        help="Only events after this ISO timestamp",
        default=None
    )
    parser.add_argument(
        "--before",
        help="Only events before this ISO timestamp",
        default=None
    )
    parser.add_argument(
        "--version-range",
        help="Event version range per stream (e.g., '1:10')",
        default=None
    )
    parser.add_argument(
        "--type",
        help="Filter by event type",
        default=None
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON array instead of ASCII table"
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Show aggregate stats (count, first/last timestamp per stream)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max events to return (default: 100)"
    )

    args = parser.parse_args()

    # Resolve state directory and database path
    from tools.common import get_state_db_path
    try:
        db_path = get_state_db_path()
    except Exception as e:
        print(f"Error resolving state directory: {e}", file=sys.stderr)
        return 1

    # Check that database exists
    if not db_path.exists():
        print(f"Error: State database not found at {db_path}", file=sys.stderr)
        return 1

    # Open state store
    from state_store import StateAPI
    try:
        api = StateAPI(str(db_path))
    except Exception as e:
        print(f"Error opening state store at {db_path}: {e}", file=sys.stderr)
        return 1

    try:
        # Parse temporal filters
        after_ts = None
        before_ts = None
        if args.after:
            try:
                after_ts = parse_iso_timestamp(args.after)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1

        if args.before:
            try:
                before_ts = parse_iso_timestamp(args.before)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1

        # Query events
        events = query_events(
            api,
            stream=args.stream,
            after_ts=after_ts,
            before_ts=before_ts,
            event_type=args.type
        )

        # Apply version range filter
        if args.version_range:
            try:
                events = apply_version_range_filter(events, args.version_range)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1

        # Format and output
        if args.aggregate:
            output = format_aggregate(events)
        elif args.json:
            output = format_json(events, args.limit)
        else:
            output = format_ascii_table(events, args.limit)

        print(output)
        return 0

    except Exception as e:
        print(f"Error querying state store: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        try:
            api.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
