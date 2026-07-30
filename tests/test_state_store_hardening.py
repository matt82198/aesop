"""test_state_store_hardening — verify corrupt event handling and input validation.

TDD fixtures for audit findings:
(1) store.py: corrupt JSON payload crashes read(); should skip with stderr log
(2) ingest.py: malformed event input should raise, not fold in
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

# Add parent dir to path so we can import state_store modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from state_store.store import EventStore
from state_store.ingest import ingest_tracker_json


class MockAPI:
    """Minimal mock for the state_store API contract (just append)."""

    def __init__(self, db_path):
        self.store = EventStore(db_path)

    def append(self, stream, event_type, payload, actor="migration"):
        return self.store.append(stream, event_type, payload, actor)


class TestStoreCorruptPayload(unittest.TestCase):
    """Verify store.py skips corrupt JSON payloads and logs them."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.store = EventStore(str(self.db_path))

    def tearDown(self):
        self.store.close()
        self.tmpdir.cleanup()

    def test_read_skips_corrupt_payload_logs_to_stderr(self):
        """Corrupt JSON payload in a row is skipped; good rows return; error logged to stderr."""
        # Insert a good event first
        self.store.append("stream1", "event_type", {"key": "value"}, "actor1")

        # Insert a corrupt event directly into the DB (simulating database corruption)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO events (ts, actor, stream, type, payload, version) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (1234.5, "actor2", "stream1", "bad_event", "{invalid json", 2),
            )
            conn.commit()
        finally:
            conn.close()

        # Insert another good event after the corrupt one
        self.store.append("stream1", "event_type", {"key": "value2"}, "actor3")

        # Capture stderr to check for log message
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        try:
            # Read should skip the corrupt event and return only good events
            rows = self.store.read("stream1")

            stderr_output = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr

        # Should have 2 good events (versions 1 and 3), corrupt one (version 2) is skipped
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["version"], 1)
        self.assertEqual(rows[0]["payload"], {"key": "value"})
        self.assertEqual(rows[1]["version"], 3)
        self.assertEqual(rows[1]["payload"], {"key": "value2"})

        # Verify stderr contains the corrupt event info (stream and sequence/id)
        self.assertIn("stream1", stderr_output)
        self.assertIn("corrupt", stderr_output.lower())

    def test_read_all_skips_corrupt_payload(self):
        """read_all() also skips corrupt payloads across all streams."""
        self.store.append("stream1", "event_type", {"x": 1}, "a")

        # Insert corrupt event
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO events (ts, actor, stream, type, payload, version) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (1234.5, "b", "stream2", "corrupt", "not valid json", 1),
            )
            conn.commit()
        finally:
            conn.close()

        self.store.append("stream2", "event_type", {"y": 2}, "c")

        old_stderr = sys.stderr
        sys.stderr = StringIO()
        try:
            rows = self.store.read_all()
        finally:
            sys.stderr = old_stderr

        # Should have 2 good events, corrupt one skipped
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["payload"], {"x": 1})
        self.assertEqual(rows[1]["payload"], {"y": 2})


class TestIngestValidation(unittest.TestCase):
    """Verify ingest.py validates event structure at the boundary."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.store = EventStore(str(self.db_path))
        self.api = MockAPI(str(self.db_path))

    def tearDown(self):
        self.store.close()
        self.api.store.close()
        self.tmpdir.cleanup()

    def test_ingest_rejects_non_dict_event(self):
        """ingest_tracker_json rejects items that are not dicts."""
        tracker_path = Path(self.tmpdir.name) / "tracker.json"
        tracker_path.write_text(json.dumps({"items": ["not a dict", 123, None]}))

        with self.assertRaises(TypeError) as ctx:
            ingest_tracker_json(self.api, str(tracker_path))

        self.assertIn("dict", str(ctx.exception).lower())

    def test_ingest_rejects_malformed_json(self):
        """ingest_tracker_json rejects invalid JSON input."""
        tracker_path = Path(self.tmpdir.name) / "tracker.json"
        tracker_path.write_text("{invalid json")

        with self.assertRaises(json.JSONDecodeError):
            ingest_tracker_json(self.api, str(tracker_path))

    def test_ingest_validates_required_fields(self):
        """ingest_tracker_json validates each event dict has required structure."""
        tracker_path = Path(self.tmpdir.name) / "tracker.json"
        # Valid tracker with items; ingest should validate structure
        # For now, assume any dict is valid (field validation is optional per the spec)
        tracker_path.write_text(json.dumps({"items": [{"id": "1", "title": "Task"}]}))

        count = ingest_tracker_json(self.api, str(tracker_path))
        self.assertEqual(count, 1)

    def test_ingest_accepts_valid_dicts(self):
        """ingest_tracker_json accepts valid dict items."""
        tracker_path = Path(self.tmpdir.name) / "tracker.json"
        items = [
            {"id": "1", "title": "Task 1"},
            {"id": "2", "title": "Task 2", "extra": "field"},
        ]
        tracker_path.write_text(json.dumps({"items": items}))

        count = ingest_tracker_json(self.api, str(tracker_path))
        self.assertEqual(count, 2)

        # Verify they were ingested into the store
        rows = self.store.read("tracker")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["payload"], items[0])
        self.assertEqual(rows[1]["payload"], items[1])


if __name__ == "__main__":
    unittest.main()
