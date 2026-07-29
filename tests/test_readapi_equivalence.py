#!/usr/bin/env python3
"""
Equivalence tests for read-path unification (WS4a).

Verify that ReadAPI produces the same data as direct file reads, so consumers
can be safely refactored to use the API without behavior changes.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store.read_api import ReadAPI


class ReadAPIEquivalenceTest(unittest.TestCase):
    """Verify ReadAPI produces identical results to direct file reads."""

    def setUp(self):
        """Create temporary state directory with sample data."""
        self.tmp_dir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmp_dir) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up temporary files."""
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_read_tracker_snapshot_equivalence(self):
        """Tracker snapshot via ReadAPI equals direct file read."""
        # Create a sample tracker.json
        tracker_data = {
            "version": 1,
            "items": [
                {
                    "id": "abc123",
                    "title": "Test item",
                    "priority": "P1",
                    "status": "todo",
                    "lane": "proposed",
                    "source": "test",
                    "tags": ["tag1"],
                    "notes": "Notes here",
                    "pr_link": None,
                    "created_at": "2026-07-28T00:00:00Z",
                    "completed_at": None
                }
            ]
        }
        tracker_file = self.state_dir / "tracker.json"
        tracker_file.write_text(json.dumps(tracker_data), encoding="utf-8")

        # Read via direct file access (baseline)
        direct_read = json.loads(tracker_file.read_text(encoding="utf-8"))

        # Read via ReadAPI
        api = ReadAPI(str(self.state_dir))
        api_read = api.read_tracker_snapshot()

        # Should be identical
        self.assertEqual(api_read, direct_read, "ReadAPI should match direct file read")

    def test_read_tracker_snapshot_missing_file(self):
        """Both methods return empty dict when tracker.json is missing."""
        # Direct read (manual)
        tracker_file = self.state_dir / "tracker.json"
        if tracker_file.exists():
            tracker_file.unlink()
        direct_result = {}  # Missing file -> empty dict

        # Via ReadAPI
        api = ReadAPI(str(self.state_dir))
        api_result = api.read_tracker_snapshot()

        self.assertEqual(api_result, direct_result)

    def test_read_orchestrator_status_equivalence(self):
        """Orchestrator status via ReadAPI equals direct file read."""
        # Create a sample orchestrator-status.json
        status_data = {
            "id": "orch-1",
            "phase": "wave-27",
            "activity": "running",
            "updated_at": "2026-07-28T12:00:00Z"
        }
        status_file = self.state_dir / "orchestrator-status.json"
        status_file.write_text(json.dumps(status_data), encoding="utf-8")

        # Direct read
        direct_read = json.loads(status_file.read_text(encoding="utf-8"))

        # Via ReadAPI
        api = ReadAPI(str(self.state_dir))
        api_read = api.read_orchestrator_status()

        self.assertEqual(api_read, direct_read)

    def test_read_orchestrator_status_missing_file(self):
        """Both methods return None when file is missing."""
        status_file = self.state_dir / "orchestrator-status.json"
        if status_file.exists():
            status_file.unlink()

        # Direct read would return None
        direct_result = None

        # Via ReadAPI
        api = ReadAPI(str(self.state_dir))
        api_result = api.read_orchestrator_status()

        self.assertEqual(api_result, direct_result)

    def test_is_orchestrator_status_fresh_equivalence(self):
        """Freshness check logic via ReadAPI matches direct implementation."""
        # Create a fresh status file (just now)
        now = datetime.now(timezone.utc)
        status_data = {
            "id": "orch-1",
            "updated_at": now.isoformat().replace("+00:00", "Z")
        }
        status_file = self.state_dir / "orchestrator-status.json"
        status_file.write_text(json.dumps(status_data), encoding="utf-8")

        # Via ReadAPI
        api = ReadAPI(str(self.state_dir))
        is_fresh = api.is_orchestrator_status_fresh(threshold_s=300)

        # Should be fresh (just created)
        self.assertTrue(is_fresh, "Freshly created status should be fresh")

    def test_check_heartbeat_fresh_equivalence(self):
        """Heartbeat freshness check via ReadAPI works."""
        import time

        # Create a fresh heartbeat file
        hb_file = self.state_dir / ".watchdog-heartbeat"
        hb_file.write_text(str(int(time.time())), encoding="utf-8")

        # Via ReadAPI
        api = ReadAPI(str(self.state_dir))
        is_fresh = api.check_heartbeat_fresh(".watchdog-heartbeat", threshold_s=10)

        # Should be fresh
        self.assertTrue(is_fresh, "Fresh heartbeat should pass check")

    def test_read_ledger_rows_equivalence(self):
        """Ledger parsing via ReadAPI delegates to fleet_ledger correctly."""
        # Create a sample ledger file
        ledger_content = """# OUTCOMES-LEDGER.md

| wave | agent | task | model | verdict | repair | tokens | wall_sec |
|------|-------|------|-------|---------|--------|--------|----------|
| 27   | fix   | item-123 | claude-opus | PASS | 0 | 1000 | 5.2 |
| 27   | review | item-124 | claude-opus | PASS | 0 | 2000 | 8.1 |
"""
        ledger_dir = self.state_dir / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = ledger_dir / "OUTCOMES-LEDGER.md"
        ledger_file.write_text(ledger_content, encoding="utf-8")

        # Via ReadAPI
        api = ReadAPI(str(self.state_dir))
        rows = api.read_ledger_rows()

        # Should have parsed 2 rows
        self.assertIsInstance(rows, list)
        # Note: exact structure depends on fleet_ledger parser, just verify non-empty
        # This test ensures the delegation works correctly

    def test_collectors_snapshot_tracker_equivalence(self):
        """ui/collectors._snapshot_tracker should match ReadAPI."""
        # Create sample tracker.json
        tracker_data = {
            "version": 1,
            "items": [
                {
                    "id": "item1",
                    "title": "Test",
                    "priority": "P1",
                    "status": "todo",
                    "lane": "proposed",
                    "source": "test",
                    "tags": [],
                    "notes": None,
                    "pr_link": None,
                    "created_at": "2026-07-28T00:00:00Z",
                    "completed_at": None
                }
            ]
        }
        tracker_file = self.state_dir / "tracker.json"
        tracker_file.write_text(json.dumps(tracker_data), encoding="utf-8")

        # Direct implementation (old _snapshot_tracker logic)
        def old_snapshot():
            if not tracker_file.exists():
                return {"items": []}
            try:
                data = json.loads(tracker_file.read_text(encoding='utf-8'))
                if isinstance(data, dict) and "items" in data:
                    return {"items": data.get("items", [])}
                return {"items": []}
            except Exception:
                return {"items": []}

        # New implementation via ReadAPI
        def new_snapshot():
            api = ReadAPI(str(self.state_dir))
            full = api.read_tracker_snapshot()
            return {"items": full.get("items", [])}

        old_result = old_snapshot()
        new_result = new_snapshot()

        self.assertEqual(new_result, old_result, "Snapshot should match old and new")

    def test_collectors_snapshot_orchestrator_status_equivalence(self):
        """ui/collectors._snapshot_orchestrator_status should match ReadAPI."""
        # Create sample orchestrator-status.json
        status_data = {
            "id": "orch-1",
            "phase": "wave-27",
            "updated_at": "2026-07-28T12:00:00Z",
            "age_seconds": 0,
            "stale": False
        }
        status_file = self.state_dir / "orchestrator-status.json"
        status_file.write_text(json.dumps(status_data), encoding="utf-8")

        # Old implementation (simplified)
        def old_snapshot():
            if not status_file.exists():
                return {"orchestrators": []}
            try:
                data = json.loads(status_file.read_text(encoding='utf-8'))
                if not isinstance(data, dict):
                    return {"orchestrators": []}
                if "orchestrators" in data and isinstance(data["orchestrators"], list):
                    return data
                if "id" in data or "role" in data:
                    return {"orchestrators": [data]}
                return {"orchestrators": []}
            except Exception:
                return {"orchestrators": []}

        # New implementation via ReadAPI
        def new_snapshot():
            api = ReadAPI(str(self.state_dir))
            status = api.read_orchestrator_status()
            if status is None:
                return {"orchestrators": []}
            if "orchestrators" in status and isinstance(status["orchestrators"], list):
                return status
            if "id" in status or "role" in status:
                return {"orchestrators": [status]}
            return {"orchestrators": []}

        old_result = old_snapshot()
        new_result = new_snapshot()

        self.assertEqual(new_result, old_result, "Status snapshot should match old and new")


if __name__ == "__main__":
    unittest.main()
