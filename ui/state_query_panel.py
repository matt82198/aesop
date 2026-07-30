#!/usr/bin/env python3
"""State query API panel — REST endpoints exposing state_query functionality.

Provides time-travel query endpoints for the event-sourced state store
(SQLite WAL). Wraps state_store.api.StateAPI and tools/state_query.py logic.

Endpoints:
  GET /api/state/events — Query events with temporal/stream/type filters
  GET /api/state/streams — List all streams with event counts (aggregate view)

Both endpoints read config.STATE_DIR at call time (call-time config rule).
Fail gracefully if database missing (return empty results, not 500).
"""
import json
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import config


def parse_iso_timestamp(ts_str):
    """Parse ISO 8601 timestamp string to Unix timestamp (float).

    Args:
        ts_str: ISO 8601 timestamp string (e.g., "2026-07-30T12:00:00Z")

    Returns:
        float: Unix timestamp

    Raises:
        ValueError: If timestamp is invalid
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


def get_events(stream=None, after_ts=None, before_ts=None, event_type=None, limit=100):
    """Query events from the state store with filters.

    Args:
        stream: Filter by stream name (optional)
        after_ts: Unix timestamp lower bound (optional)
        before_ts: Unix timestamp upper bound (optional)
        event_type: Filter by event type (optional)
        limit: Max events to return (default: 100, max: 500)

    Returns:
        list: Filtered event dicts, sorted by timestamp ascending, capped at limit

    Raises:
        RuntimeError: If state database cannot be opened
    """
    # Clamp limit to 500 (reasonable API limit)
    limit = min(max(1, limit), 500)

    # Check if database exists before attempting to open
    state_dir = Path(config.STATE_DIR)
    db_path = state_dir / "tracker_events.db"
    if not db_path.exists():
        return []  # Graceful empty response if DB missing

    try:
        from state_store import StateAPI
        api = StateAPI(str(db_path))
    except Exception as e:
        # Graceful degradation: log and return empty
        print(f"[get_events] Error opening state store: {e}", file=sys.stderr)
        return []

    try:
        # If stream filter is specified, query just that stream
        if stream:
            all_events = api.get(stream)
        else:
            # Otherwise read all events from all streams
            from state_store import EventStore
            try:
                store = EventStore(str(db_path))
                all_events = store.read_all()
                store.close()
            except Exception as e:
                print(f"[get_events] Error reading all events: {e}", file=sys.stderr)
                return []

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

        # Apply limit
        return filtered[:limit]

    except Exception as e:
        print(f"[get_events] Error querying events: {e}", file=sys.stderr)
        return []
    finally:
        try:
            api.close()
        except Exception:
            pass


def get_streams():
    """Get aggregate stream statistics (event count, latest timestamp per stream).

    Returns:
        dict: {"streams": [{"name": stream, "count": int, "latest_ts": ISO_timestamp}]}
               Empty dict if no streams found or DB missing.
    """
    state_dir = Path(config.STATE_DIR)
    db_path = state_dir / "tracker_events.db"
    if not db_path.exists():
        return {"streams": []}

    try:
        from state_store import EventStore
        store = EventStore(str(db_path))
        all_events = store.read_all()
        store.close()
    except Exception as e:
        print(f"[get_streams] Error reading events: {e}", file=sys.stderr)
        return {"streams": []}

    # Group by stream and compute stats
    by_stream = {}
    for event in all_events:
        stream = event["stream"]
        if stream not in by_stream:
            by_stream[stream] = []
        by_stream[stream].append(event)

    # Build result
    streams = []
    for stream in sorted(by_stream.keys()):
        stream_events = by_stream[stream]
        count = len(stream_events)
        # Events are already sorted by append order; latest is last
        latest_ts = format_timestamp(stream_events[-1]["ts"])

        streams.append({
            "name": stream,
            "count": count,
            "latest_ts": latest_ts
        })

    return {"streams": streams}


def serve_api_state_events(handler):
    """GET /api/state/events — Query events with temporal/stream/type filters.

    Query parameters:
      stream=<name> — filter by stream
      type=<event_type> — filter by event type
      after=<iso_ts> — events after timestamp
      before=<iso_ts> — events before timestamp
      limit=<n> — max results (default 100, max 500)

    Returns JSON array of events.
    Fails gracefully (empty array) if database missing.
    """
    try:
        # Parse query string
        query = urllib.parse.urlparse(handler.path).query
        params = urllib.parse.parse_qs(query)

        # Extract parameters
        stream = params.get('stream', [None])[0]
        event_type = params.get('type', [None])[0]
        limit_str = params.get('limit', ['100'])[0]
        after_str = params.get('after', [None])[0]
        before_str = params.get('before', [None])[0]

        # Parse limit (with bounds)
        try:
            limit = int(limit_str)
        except (ValueError, TypeError):
            limit = 100

        # Parse temporal filters
        after_ts = None
        before_ts = None
        if after_str:
            try:
                after_ts = parse_iso_timestamp(after_str)
            except ValueError as e:
                handler.send_response(400)
                handler.send_header("Content-Type", "application/json; charset=utf-8")
                handler.end_headers()
                handler.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
                return

        if before_str:
            try:
                before_ts = parse_iso_timestamp(before_str)
            except ValueError as e:
                handler.send_response(400)
                handler.send_header("Content-Type", "application/json; charset=utf-8")
                handler.end_headers()
                handler.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
                return

        # Query events
        events = get_events(
            stream=stream,
            after_ts=after_ts,
            before_ts=before_ts,
            event_type=event_type,
            limit=limit
        )

        # Format output: convert ts to timestamp for JSON
        output_events = []
        for event in events:
            output_event = dict(event)
            output_event["timestamp"] = format_timestamp(event["ts"])
            # Keep ts for compatibility; remove if not needed
            output_events.append(output_event)

        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        handler.end_headers()
        handler.wfile.write(json.dumps(output_events, default=str).encode('utf-8'))

    except Exception as e:
        print(f"[serve_api_state_events] Uncaught exception: {e}", file=sys.stderr)
        try:
            handler.send_response(500)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
        except Exception:
            pass


def serve_api_state_streams(handler):
    """GET /api/state/streams — List all streams with event counts (aggregate view).

    Returns JSON object: {"streams": [{"name": stream, "count": count, "latest_ts": ISO_ts}]}
    Fails gracefully (empty streams) if database missing.
    """
    try:
        result = get_streams()

        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        handler.end_headers()
        handler.wfile.write(json.dumps(result, default=str).encode('utf-8'))

    except Exception as e:
        print(f"[serve_api_state_streams] Uncaught exception: {e}", file=sys.stderr)
        try:
            handler.send_response(500)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
        except Exception:
            pass
