"""Tests for state_query — CLI time-travel state query tool (unittest).

Covers:
- ASCII table output with proper columns and sorting
- JSON output mode
- Stream filtering
- Temporal filtering (--after, --before)
- Version range filtering
- Aggregate mode (per-stream stats)
- Limit parameter
- Empty database handling
- Deterministic output (sorted by timestamp)
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StateQueryTest(unittest.TestCase):
    """Tests for state_query CLI tool."""

    def setUp(self):
        """Create a temporary state directory with an initialized state store."""
        self.tmp = tempfile.mkdtemp()
        self.state_dir = Path(self.tmp) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._stores = []  # track stores for tearDown cleanup

        # Set AESOP_STATE_ROOT for the query tool
        self.original_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

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

    def _init_db(self):
        """Initialize the event store database."""
        from state_store import EventStore
        db_path = self.state_dir / "tracker_events.db"
        store = EventStore(str(db_path))
        self._stores.append(store)
        return store

    def _run_query(self, *args):
        """Run state_query.py with the given arguments, return (stdout, stderr, returncode)."""
        cmd = [sys.executable, str(ROOT / "tools" / "state_query.py")] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env={**os.environ, "AESOP_STATE_ROOT": str(self.state_dir)}
        )
        return result.stdout, result.stderr, result.returncode

    def test_empty_db_returns_graceful_output(self):
        """Test that an empty database returns graceful output."""
        self._init_db()

        stdout, stderr, rc = self._run_query()
        self.assertEqual(rc, 0)
        # Should have header or indication of empty results
        self.assertIn("timestamp", stdout.lower())

    def test_ascii_output_format(self):
        """Test ASCII table output with proper column headers and formatting."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add a test event
        api.append("wave", "dispatch_started", {
            "wave_id": "wave-1",
            "timestamp": "2026-07-30T12:00:00Z"
        })

        stdout, stderr, rc = self._run_query()
        self.assertEqual(rc, 0)

        # Check for expected columns in ASCII output
        lines = stdout.strip().split("\n")
        self.assertGreater(len(lines), 1, "Should have header and at least one row")

        # Header should contain these column names (case-insensitive)
        header = lines[0].lower()
        self.assertIn("timestamp", header)
        self.assertIn("stream", header)
        self.assertIn("version", header)
        self.assertIn("type", header)

    def test_json_output_mode(self):
        """Test JSON output mode produces valid JSON array."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add a test event
        api.append("wave", "dispatch_started", {
            "wave_id": "wave-1",
        })

        stdout, stderr, rc = self._run_query("--json")
        self.assertEqual(rc, 0)

        # Parse JSON output
        data = json.loads(stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)

        # Check event structure
        event = data[0]
        self.assertIn("timestamp", event)
        self.assertIn("stream", event)
        self.assertIn("version", event)
        self.assertIn("type", event)
        self.assertEqual(event["stream"], "wave")
        self.assertEqual(event["type"], "dispatch_started")

    def test_stream_filter(self):
        """Test --stream filter returns only matching streams."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add events to different streams
        api.append("wave", "dispatch_started", {"wave_id": "wave-1"})
        api.append("agent", "agent_dispatched", {"agent_id": "agent-1"})
        api.append("wave", "phase_changed", {"phase": "audit"})

        # Filter by wave stream
        stdout, stderr, rc = self._run_query("--stream", "wave", "--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        self.assertEqual(len(data), 2)
        for event in data:
            self.assertEqual(event["stream"], "wave")

    def test_temporal_after_filter(self):
        """Test --after filter with ISO timestamp."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add events (ts is unix timestamp)
        import time
        early_ts = time.time()
        api.append("wave", "event1", {"data": "early"})

        time.sleep(0.1)  # Small delay to create distinct timestamp
        mid_ts = time.time()
        api.append("wave", "event2", {"data": "mid"})

        time.sleep(0.1)
        api.append("wave", "event3", {"data": "late"})

        # Convert mid timestamp to ISO format
        mid_iso = datetime.fromtimestamp(mid_ts, tz=timezone.utc).isoformat()

        # Query events after mid_ts
        stdout, stderr, rc = self._run_query("--after", mid_iso, "--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        # Should get event2 (boundary inclusive) and event3
        self.assertGreaterEqual(len(data), 1)
        # First event should be after our cutoff (parse ISO timestamp)
        first_event_iso = data[0]["timestamp"]
        first_event_ts = datetime.fromisoformat(first_event_iso.replace('Z', '+00:00')).timestamp()
        self.assertGreaterEqual(first_event_ts, mid_ts - 1)  # 1s tolerance

    def test_temporal_before_filter(self):
        """Test --before filter with ISO timestamp."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add events
        import time
        api.append("wave", "event1", {"data": "early"})
        time.sleep(0.1)
        mid_ts = time.time()
        api.append("wave", "event2", {"data": "mid"})
        time.sleep(0.1)
        api.append("wave", "event3", {"data": "late"})

        # Convert mid timestamp to ISO format
        mid_iso = datetime.fromtimestamp(mid_ts, tz=timezone.utc).isoformat()

        # Query events before mid_ts
        stdout, stderr, rc = self._run_query("--before", mid_iso, "--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        # Should get event1 (and maybe event2 depending on precision)
        self.assertGreater(len(data), 0)
        # Last event should be before our cutoff (parse ISO timestamp)
        last_event_iso = data[-1]["timestamp"]
        last_event_ts = datetime.fromisoformat(last_event_iso.replace('Z', '+00:00')).timestamp()
        self.assertLessEqual(last_event_ts, mid_ts + 1)  # 1s tolerance

    def test_version_range_filter(self):
        """Test --version-range N:M filter."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add 5 events to the same stream
        for i in range(1, 6):
            api.append("wave", f"event{i}", {"num": i})

        # Filter for versions 2-4
        stdout, stderr, rc = self._run_query("--stream", "wave", "--version-range", "2:4", "--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        self.assertEqual(len(data), 3)  # versions 2, 3, 4
        self.assertEqual(data[0]["version"], 2)
        self.assertEqual(data[1]["version"], 3)
        self.assertEqual(data[2]["version"], 4)

    def test_event_type_filter(self):
        """Test --type EVENT_TYPE filter."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add events of different types
        api.append("wave", "dispatch_started", {"wave": 1})
        api.append("wave", "phase_changed", {"phase": "audit"})
        api.append("wave", "dispatch_started", {"wave": 2})

        # Filter by type
        stdout, stderr, rc = self._run_query("--type", "dispatch_started", "--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        self.assertEqual(len(data), 2)
        for event in data:
            self.assertEqual(event["type"], "dispatch_started")

    def test_aggregate_mode(self):
        """Test --aggregate mode shows per-stream stats."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add events to multiple streams
        api.append("wave", "event1", {})
        api.append("wave", "event2", {})
        api.append("agent", "event3", {})

        stdout, stderr, rc = self._run_query("--aggregate")
        self.assertEqual(rc, 0)

        # Output should contain stream names and counts
        self.assertIn("wave", stdout)
        self.assertIn("agent", stdout)
        # Should show count = 2 for wave
        self.assertIn("2", stdout)
        # Should show count = 1 for agent
        self.assertIn("1", stdout)

    def test_limit_parameter(self):
        """Test --limit N caps output to N events."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add 10 events
        for i in range(10):
            api.append("wave", f"event{i}", {"num": i})

        # Limit to 3
        stdout, stderr, rc = self._run_query("--limit", "3", "--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        self.assertEqual(len(data), 3)

    def test_deterministic_ordering_by_timestamp(self):
        """Test that output is deterministically sorted by timestamp ascending."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add events (will have different timestamps)
        api.append("wave", "event1", {})
        api.append("wave", "event2", {})
        api.append("wave", "event3", {})

        stdout, stderr, rc = self._run_query("--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        # Verify they're sorted by timestamp ascending (parse ISO timestamps)
        timestamps = [datetime.fromisoformat(e["timestamp"].replace('Z', '+00:00')).timestamp() for e in data]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_missing_db_fails_closed(self):
        """Test that missing database exits with error."""
        # Don't initialize the database
        stdout, stderr, rc = self._run_query()
        # Should fail (exit code 1)
        self.assertNotEqual(rc, 0)

    def test_combined_filters(self):
        """Test combining multiple filters."""
        store = self._init_db()
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        import time
        # Add events to multiple streams with sufficient time gaps
        # (Windows timer resolution is ~15ms; use 200ms gaps)
        api.append("wave", "dispatch_started", {"wave": 1})
        time.sleep(0.2)
        mid_ts = time.time()
        time.sleep(0.2)
        api.append("wave", "dispatch_started", {"wave": 2})
        time.sleep(0.2)
        api.append("agent", "dispatch_started", {"agent": 1})

        mid_iso = datetime.fromtimestamp(mid_ts, tz=timezone.utc).isoformat()

        # Filter: wave stream, after mid_ts, type=dispatch_started
        stdout, stderr, rc = self._run_query(
            "--stream", "wave",
            "--after", mid_iso,
            "--type", "dispatch_started",
            "--json"
        )
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        # Should get at least the wave-2 event (after mid_ts)
        self.assertGreaterEqual(len(data), 1)
        self.assertEqual(data[0]["stream"], "wave")
        self.assertEqual(data[0]["type"], "dispatch_started")

    def test_help_flag(self):
        """Test that --help exits successfully."""
        stdout, stderr, rc = self._run_query("--help")
        self.assertEqual(rc, 0)
        self.assertIn("usage", stdout.lower())

    def test_unknown_flag_fails(self):
        """Test that unknown flags exit with error."""
        stdout, stderr, rc = self._run_query("--unknown-flag")
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
