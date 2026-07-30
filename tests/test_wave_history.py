"""Tests for wave_history — per-wave history summarizer (unittest).

Covers:
- Empty database returns graceful output
- Multiple waves are grouped correctly
- JSON output is valid JSON
- --latest N caps output to last N waves
- Missing database exits with error
- Event count is accurate
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class WaveHistoryTest(unittest.TestCase):
    """Tests for wave_history CLI tool."""

    def setUp(self):
        """Create a temporary state directory with an initialized state store."""
        self.tmp = tempfile.mkdtemp()
        self.state_dir = Path(self.tmp) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._stores = []  # track stores for tearDown cleanup

        # Set AESOP_STATE_ROOT for the tool
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

    def _run_tool(self, *args):
        """Run wave_history.py with given arguments.

        Returns:
            tuple: (stdout, stderr, returncode)
        """
        cmd = [sys.executable, str(ROOT / "tools" / "wave_history.py")] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env={**os.environ, "AESOP_STATE_ROOT": str(self.state_dir)},
        )
        return result.stdout, result.stderr, result.returncode

    def _init_db(self):
        """Initialize the event store database."""
        from state_store import EventStore

        db_path = self.state_dir / "tracker_events.db"
        store = EventStore(str(db_path))
        self._stores.append(store)
        return store

    def test_empty_database_returns_graceful_output(self):
        """Test that an empty database returns graceful output."""
        self._init_db()

        stdout, stderr, rc = self._run_tool()
        self.assertEqual(rc, 0)
        self.assertIn("No waves found", stdout)

    def test_empty_database_json_returns_empty_array(self):
        """Test that --json on empty database returns empty array."""
        self._init_db()

        stdout, stderr, rc = self._run_tool("--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 0)

    def test_multiple_streams_produce_correct_grouping(self):
        """Test that events from multiple waves are grouped correctly."""
        from state_store import StateAPI

        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Create two waves with multiple events each
        base_time = time.time()

        # Wave 1: 3 events
        api.append("wave", "dispatch_started", {"wave_id": "wave-1"})
        time.sleep(0.01)
        api.append("wave", "phase_changed", {"wave_id": "wave-1", "phase": "setup"})
        time.sleep(0.01)
        api.append("wave", "phase_changed", {"wave_id": "wave-1", "phase": "dispatch"})

        # Wave 2: 2 events
        api.append("wave", "dispatch_started", {"wave_id": "wave-2"})
        time.sleep(0.01)
        api.append("wave", "phase_changed", {"wave_id": "wave-2", "phase": "audit"})

        stdout, stderr, rc = self._run_tool()
        self.assertEqual(rc, 0)

        # Check output contains both waves
        self.assertIn("wave-1", stdout)
        self.assertIn("wave-2", stdout)

        # Check event counts
        self.assertIn("3", stdout)  # wave-1 has 3 events
        self.assertIn("2", stdout)  # wave-2 has 2 events

    def test_json_output_is_valid(self):
        """Test JSON output is valid and contains expected structure."""
        from state_store import StateAPI

        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Add test events
        api.append("wave", "dispatch_started", {"wave_id": "wave-1"})
        api.append("wave", "phase_changed", {"wave_id": "wave-1"})

        stdout, stderr, rc = self._run_tool("--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)

        wave_data = data[0]
        self.assertIn("wave_id", wave_data)
        self.assertIn("start", wave_data)
        self.assertIn("end", wave_data)
        self.assertIn("duration_sec", wave_data)
        self.assertIn("event_count", wave_data)
        self.assertIn("stream_count", wave_data)

        self.assertEqual(wave_data["wave_id"], "wave-1")
        self.assertEqual(wave_data["event_count"], 2)

    def test_latest_n_caps_output(self):
        """Test --latest N returns only last N waves."""
        from state_store import StateAPI

        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Create 5 waves
        for i in range(1, 6):
            api.append("wave", "dispatch_started", {"wave_id": f"wave-{i}"})

        # Request only last 2 waves
        stdout, stderr, rc = self._run_tool("--latest", "2", "--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        self.assertEqual(len(data), 2)

        # Check that we got the last 2
        wave_ids = [w["wave_id"] for w in data]
        self.assertIn("wave-4", wave_ids)
        self.assertIn("wave-5", wave_ids)

    def test_missing_database_error(self):
        """Test that missing database exits with error code."""
        # Don't create the database
        non_existent_state = Path(self.tmp) / "nonexistent" / "state"

        stdout, stderr, rc = self._run_tool(
            "--state-root", str(non_existent_state)
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("Error", stderr)

    def test_event_count_is_accurate(self):
        """Test that event counts in output match actual events."""
        from state_store import StateAPI

        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        # Wave with 7 events
        for i in range(7):
            api.append(
                "wave",
                f"event_type_{i}",
                {"wave_id": "wave-test", "data": f"event_{i}"},
            )

        stdout, stderr, rc = self._run_tool("--json")
        self.assertEqual(rc, 0)

        data = json.loads(stdout)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["event_count"], 7)

    def test_ascii_output_has_correct_columns(self):
        """Test ASCII table output has all expected columns."""
        from state_store import StateAPI

        api = StateAPI(str(self.state_dir / "tracker_events.db"))
        self._stores.append(api)

        api.append("wave", "dispatch_started", {"wave_id": "wave-1"})

        stdout, stderr, rc = self._run_tool()
        self.assertEqual(rc, 0)

        lines = stdout.strip().split("\n")
        self.assertGreater(len(lines), 1)

        # Check header row contains expected columns
        header = lines[0].lower()
        self.assertIn("wave", header)
        self.assertIn("start", header)
        self.assertIn("duration", header)
        self.assertIn("events", header)
        self.assertIn("streams", header)

    def test_help_flag_works(self):
        """Test --help flag works and exits 0."""
        stdout, stderr, rc = self._run_tool("--help")
        self.assertEqual(rc, 0)
        self.assertIn("usage", stdout.lower())
        self.assertIn("--json", stdout)
        self.assertIn("--latest", stdout)


if __name__ == "__main__":
    unittest.main()
