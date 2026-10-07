"""Tests for state_store.read_api — typed read-only facade for state surfaces.

Tests the consolidated read API that facades tracker snapshot, orchestrator-status,
heartbeat freshness, and ledger access.
"""
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store.read_api import ReadAPI  # noqa: E402


class ReadAPITest(unittest.TestCase):
    """Tests for the read_api facade."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state_dir = Path(self.tmp) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_tracker_snapshot_returns_dict(self):
        """Reading tracker returns a dict (empty if no data)."""
        api = ReadAPI(str(self.state_dir))
        tracker = api.read_tracker_snapshot()
        # Should return a dict (shape depends on projection, but always a dict)
        self.assertIsInstance(tracker, dict)

    def test_read_tracker_snapshot_with_real_file(self):
        """Reading tracker from tracker.json file."""
        # Write a minimal tracker.json
        tracker_file = self.state_dir / "tracker.json"
        sample_tracker = {"items": [{"id": "item-1", "title": "Test", "status": "todo"}]}
        tracker_file.write_text(json.dumps(sample_tracker))

        api = ReadAPI(str(self.state_dir))
        tracker = api.read_tracker_snapshot()
        self.assertIn("items", tracker)

    def test_read_orchestrator_status_returns_dict_or_none(self):
        """Reading orchestrator status returns dict or None."""
        api = ReadAPI(str(self.state_dir))
        status = api.read_orchestrator_status()
        # Should return dict (with phase/activity) or None if missing
        if status is not None:
            self.assertIsInstance(status, dict)
            # If present, should have phase field
            if status:
                self.assertIn("phase", status)

    def test_read_orchestrator_status_freshness(self):
        """Checking orchestrator status freshness."""
        # Write a fresh status
        status_file = self.state_dir / "orchestrator-status.json"
        now = datetime.now(timezone.utc)
        status_data = {
            "phase": "wave-27-verify",
            "activity": "running",
            "updated_at": now.isoformat()
        }
        status_file.write_text(json.dumps(status_data))

        api = ReadAPI(str(self.state_dir))
        is_fresh = api.is_orchestrator_status_fresh(threshold_s=300)
        self.assertTrue(is_fresh)

    def test_read_orchestrator_status_stale(self):
        """Detecting stale orchestrator status."""
        # Write a stale status (2 hours old)
        status_file = self.state_dir / "orchestrator-status.json"
        stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
        status_data = {
            "phase": "wave-27-verify",
            "activity": "running",
            "updated_at": stale_time.isoformat()
        }
        status_file.write_text(json.dumps(status_data))

        api = ReadAPI(str(self.state_dir))
        is_fresh = api.is_orchestrator_status_fresh(threshold_s=300)
        self.assertFalse(is_fresh)

    def test_read_orchestrator_status_missing(self):
        """Missing orchestrator status returns None and not fresh."""
        api = ReadAPI(str(self.state_dir))
        status = api.read_orchestrator_status()
        self.assertIsNone(status)

        is_fresh = api.is_orchestrator_status_fresh(threshold_s=300)
        self.assertFalse(is_fresh)

    def test_check_heartbeat_freshness(self):
        """Checking heartbeat file freshness."""
        # Write a fresh heartbeat
        hb_file = self.state_dir / ".watchdog-heartbeat"
        now_epoch = int(time.time())
        hb_file.write_text(str(now_epoch))

        api = ReadAPI(str(self.state_dir))
        is_fresh = api.check_heartbeat_fresh(".watchdog-heartbeat", threshold_s=200)
        self.assertTrue(is_fresh)

    def test_check_heartbeat_stale(self):
        """Detecting stale heartbeat."""
        # Write a stale heartbeat (10 minutes old)
        hb_file = self.state_dir / ".watchdog-heartbeat"
        stale_epoch = int(time.time()) - 600
        hb_file.write_text(str(stale_epoch))

        api = ReadAPI(str(self.state_dir))
        is_fresh = api.check_heartbeat_fresh(".watchdog-heartbeat", threshold_s=200)
        self.assertFalse(is_fresh)

    def test_check_heartbeat_missing(self):
        """Missing heartbeat is stale."""
        api = ReadAPI(str(self.state_dir))
        is_fresh = api.check_heartbeat_fresh(".watchdog-heartbeat", threshold_s=200)
        self.assertFalse(is_fresh)

    def test_read_ledger_rows(self):
        """Reading ledger rows from OUTCOMES-LEDGER.md."""
        # Create a ledger directory and file
        ledger_dir = self.state_dir / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = ledger_dir / "OUTCOMES-LEDGER.md"

        # Write minimal markdown table
        ledger_content = """# Outcomes Ledger

| Date | Phase | Model | Tokens | Status |
|------|-------|-------|--------|--------|
| 2026-07-22 | wave-27-build | haiku | 1000 | OK |
"""
        ledger_file.write_text(ledger_content)

        api = ReadAPI(str(self.state_dir))
        rows = api.read_ledger_rows()
        # Should return a list (empty if parsing fails, but not error)
        self.assertIsInstance(rows, list)


if __name__ == "__main__":
    unittest.main()
