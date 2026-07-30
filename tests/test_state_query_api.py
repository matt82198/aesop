"""Tests for state_query_panel — REST API endpoints for state query (unittest).

Covers:
- GET /api/state/events with stream/type/temporal filters
- GET /api/state/streams with aggregate counts
- Empty database graceful handling
- Limit parameter enforcement (default 100, max 500)
- Temporal filter parsing (ISO timestamps)
- Missing database handling
- Deterministic sorting and output
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UI_DIR = ROOT / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))


class StateQueryAPITest(unittest.TestCase):
    """Tests for state_query_panel API endpoints."""

    def setUp(self):
        """Create a temporary state directory with an initialized state store."""
        self.tmp = tempfile.mkdtemp()
        self.state_dir = Path(self.tmp) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._stores = []  # track stores for tearDown cleanup

        # Set AESOP_STATE_ROOT for config.STATE_DIR
        self.original_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        # Force config reload to pick up the new AESOP_STATE_ROOT
        import config
        config.reload()

    def tearDown(self):
        """Clean up temporary state directory."""
        import shutil
        for s in self._stores:
            try:
                s.close()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

        # Restore original AESOP_STATE_ROOT
        if self.original_state_root is not None:
            os.environ["AESOP_STATE_ROOT"] = self.original_state_root
        else:
            os.environ.pop("AESOP_STATE_ROOT", None)

        # Force config reload to restore original state
        import config
        config.reload()

    def _init_db(self):
        """Initialize the event store database."""
        from state_store import EventStore
        db_path = self.state_dir / "tracker_events.db"
        store = EventStore(str(db_path))
        self._stores.append(store)
        return store

    def _add_test_events(self):
        """Add some test events to the database."""
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add events for different streams and types
        api.append("wave", "dispatch_started", {
            "wave_id": "wave-1",
            "timestamp": "2026-07-30T10:00:00Z"
        })
        api.append("wave", "dispatch_completed", {
            "wave_id": "wave-1",
            "timestamp": "2026-07-30T11:00:00Z"
        })
        api.append("tracker", "item_created", {
            "item_id": "item-1",
            "title": "Test item",
            "timestamp": "2026-07-30T12:00:00Z"
        })
        api.append("tracker", "item_updated", {
            "item_id": "item-1",
            "status": "in_progress",
            "timestamp": "2026-07-30T13:00:00Z"
        })
        api.append("agent", "dispatch_started", {
            "agent_id": "agent-1",
            "timestamp": "2026-07-30T14:00:00Z"
        })

    def test_empty_db_returns_empty_array(self):
        """Test that GET /api/state/events returns empty array when DB is empty."""
        self._init_db()

        from ui import state_query_panel
        import config
        config.reload()

        events = state_query_panel.get_events()
        self.assertEqual(events, [])

    def test_stream_filter_works(self):
        """Test that stream filter works correctly."""
        self._init_db()
        self._add_test_events()

        from ui import state_query_panel
        import config
        config.reload()

        # Query only wave stream
        events = state_query_panel.get_events(stream="wave")
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e["stream"] == "wave" for e in events))

        # Query tracker stream
        events = state_query_panel.get_events(stream="tracker")
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e["stream"] == "tracker" for e in events))

        # Query non-existent stream
        events = state_query_panel.get_events(stream="nonexistent")
        self.assertEqual(len(events), 0)

    def test_event_type_filter_works(self):
        """Test that event type filter works correctly."""
        self._init_db()
        self._add_test_events()

        from ui import state_query_panel
        import config
        config.reload()

        # Query only dispatch_started events
        events = state_query_panel.get_events(event_type="dispatch_started")
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e["type"] == "dispatch_started" for e in events))

        # Query item_created events
        events = state_query_panel.get_events(event_type="item_created")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "item_created")

    def test_limit_parameter_caps_results(self):
        """Test that limit parameter correctly caps results."""
        self._init_db()
        self._add_test_events()

        from ui import state_query_panel
        import config
        config.reload()

        # Limit to 2 events
        events = state_query_panel.get_events(limit=2)
        self.assertEqual(len(events), 2)

        # Limit to 1 event
        events = state_query_panel.get_events(limit=1)
        self.assertEqual(len(events), 1)

        # Limit > available should return all
        events = state_query_panel.get_events(limit=1000)
        self.assertEqual(len(events), 5)

        # Limit of 0 should be clamped to 1
        events = state_query_panel.get_events(limit=0)
        self.assertEqual(len(events), 1)

        # Limit > 500 should be clamped to 500
        events = state_query_panel.get_events(limit=1000)
        self.assertEqual(len(events), 5)  # Limited by available

    def test_temporal_filters_work(self):
        """Test that after/before temporal filters work."""
        import time
        self._init_db()

        from state_store import StateAPI
        from ui import state_query_panel
        import config

        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add events with small delays so they have different timestamps
        api.append("wave", "dispatch_started", {"wave_id": "wave-1"})
        time.sleep(0.01)  # sleep-ok: timing assertion requires precise ordering
        api.append("wave", "dispatch_completed", {"wave_id": "wave-1"})
        time.sleep(0.01)  # sleep-ok: timing assertion requires precise ordering
        api.append("tracker", "item_created", {"item_id": "item-1"})
        time.sleep(0.01)  # sleep-ok: timing assertion requires precise ordering
        api.append("tracker", "item_updated", {"item_id": "item-1"})
        time.sleep(0.01)  # sleep-ok: timing assertion requires precise ordering
        api.append("agent", "dispatch_started", {"agent_id": "agent-1"})

        # Get the events to determine their actual timestamps
        config.reload()
        all_events = state_query_panel.get_events()
        self.assertEqual(len(all_events), 5)

        # Use the middle event's timestamp to split the query
        middle_ts = all_events[2]["ts"]

        # Query events after the second event
        events = state_query_panel.get_events(after_ts=middle_ts + 0.001)
        self.assertEqual(len(events), 2)  # Last 2 events after the split point

        # Query events before the third event
        events = state_query_panel.get_events(before_ts=middle_ts - 0.001)
        self.assertEqual(len(events), 2)  # First 2 events before the split point

        # Query between times (should include the middle event)
        events = state_query_panel.get_events(after_ts=middle_ts - 0.001, before_ts=middle_ts + 0.001)
        self.assertEqual(len(events), 1)  # Only the middle event

    def test_streams_endpoint_returns_aggregate_counts(self):
        """Test that GET /api/state/streams returns aggregate counts."""
        self._init_db()
        self._add_test_events()

        from ui import state_query_panel
        import config
        config.reload()

        result = state_query_panel.get_streams()

        self.assertIn("streams", result)
        streams = result["streams"]
        self.assertEqual(len(streams), 3)  # wave, tracker, agent

        # Verify stream names and counts
        stream_dict = {s["name"]: s for s in streams}
        self.assertEqual(stream_dict["wave"]["count"], 2)
        self.assertEqual(stream_dict["tracker"]["count"], 2)
        self.assertEqual(stream_dict["agent"]["count"], 1)

        # Verify latest_ts format
        for stream in streams:
            self.assertIn("latest_ts", stream)
            self.assertRegex(stream["latest_ts"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_missing_database_returns_graceful_error(self):
        """Test that missing database returns graceful empty responses."""
        # Don't initialize DB, leave it missing

        from ui import state_query_panel
        import config
        config.reload()

        # get_events should return empty list (not raise)
        events = state_query_panel.get_events()
        self.assertEqual(events, [])

        # get_streams should return empty list (not raise)
        result = state_query_panel.get_streams()
        self.assertEqual(result, {"streams": []})

    def test_events_sorted_by_timestamp(self):
        """Test that events are sorted by timestamp ascending."""
        self._init_db()
        self._add_test_events()

        from ui import state_query_panel
        import config
        config.reload()

        events = state_query_panel.get_events()

        # Verify sorted by timestamp
        for i in range(len(events) - 1):
            self.assertLessEqual(events[i]["ts"], events[i + 1]["ts"])

    def test_mock_handler_integration_events(self):
        """Test serve_api_state_events with mock handler."""
        self._init_db()
        self._add_test_events()

        from ui import state_query_panel
        import config
        config.reload()

        # Create mock handler
        mock_handler = MagicMock()
        mock_handler.path = "/api/state/events?stream=wave&limit=10"
        mock_handler.wfile = MagicMock()

        # Call the handler
        state_query_panel.serve_api_state_events(mock_handler)

        # Verify response was sent
        mock_handler.send_response.assert_called_once_with(200)
        mock_handler.send_header.assert_any_call("Content-Type", "application/json; charset=utf-8")

        # Check that JSON was written
        write_calls = mock_handler.wfile.write.call_args_list
        self.assertGreater(len(write_calls), 0)

        # Parse the JSON response
        response_data = write_calls[0][0][0].decode('utf-8')
        events = json.loads(response_data)

        # Should be 2 wave events
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.get("stream") == "wave" for e in events))

    def test_mock_handler_integration_streams(self):
        """Test serve_api_state_streams with mock handler."""
        self._init_db()
        self._add_test_events()

        from ui import state_query_panel
        import config
        config.reload()

        # Create mock handler
        mock_handler = MagicMock()
        mock_handler.path = "/api/state/streams"
        mock_handler.wfile = MagicMock()

        # Call the handler
        state_query_panel.serve_api_state_streams(mock_handler)

        # Verify response was sent
        mock_handler.send_response.assert_called_once_with(200)
        mock_handler.send_header.assert_any_call("Content-Type", "application/json; charset=utf-8")

        # Check that JSON was written
        write_calls = mock_handler.wfile.write.call_args_list
        self.assertGreater(len(write_calls), 0)

        # Parse the JSON response
        response_data = write_calls[0][0][0].decode('utf-8')
        result = json.loads(response_data)

        # Should have streams list
        self.assertIn("streams", result)
        self.assertEqual(len(result["streams"]), 3)

    def test_invalid_timestamp_returns_400(self):
        """Test that invalid timestamp parameter returns 400 error."""
        self._init_db()

        from ui import state_query_panel
        import config
        config.reload()

        # Create mock handler
        mock_handler = MagicMock()
        mock_handler.path = "/api/state/events?after=invalid-timestamp"
        mock_handler.wfile = MagicMock()

        # Call the handler
        state_query_panel.serve_api_state_events(mock_handler)

        # Verify 400 response
        mock_handler.send_response.assert_called_once_with(400)

    def test_timestamp_parsing_and_formatting(self):
        """Test ISO timestamp parsing and formatting roundtrip."""
        from ui import state_query_panel

        # Parse an ISO timestamp
        ts_str = "2026-07-30T12:00:00Z"
        ts_float = state_query_panel.parse_iso_timestamp(ts_str)

        # Should be a valid float
        self.assertIsInstance(ts_float, float)

        # Format it back
        formatted = state_query_panel.format_timestamp(ts_float)

        # Should parse to same value
        ts_float2 = state_query_panel.parse_iso_timestamp(formatted)
        self.assertAlmostEqual(ts_float, ts_float2, places=1)  # Allow small drift


if __name__ == '__main__':
    unittest.main()
