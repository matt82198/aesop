#!/usr/bin/env python3
"""
tests.test_wave_scheduler_tracker_writes — F-01 regression suite.

driver/wave_scheduler.py used to write tracker.json directly (os.replace with no
event). tracker.json is re-rendered from the event log on every WriteAPI call, so
the scheduler's in_progress/blocked marks reverted to todo on the next unrelated
write. These tests drive the real scheduler write path against a real WriteAPI
state directory and assert the observable outcome.

Run: python -m unittest tests.test_wave_scheduler_tracker_writes
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
if str(repo_root / "driver") not in sys.path:
    sys.path.insert(0, str(repo_root / "driver"))

from state_store import EventStore
from state_store.write_api import WriteAPI
import wave_scheduler as ws


def _item(item_id, slug):
    return {
        "id": item_id,
        "slug": slug,
        "title": f"Item {slug}",
        "status": "todo",
        "priority": "P1",
        "ownsFiles": [f"{slug}.py"],
        "prompt": "Fix",
        "testCmd": "test",
        "notes": "",
    }


def _statuses(tracker_path):
    data = json.loads(Path(tracker_path).read_text(encoding="utf-8"))
    return {it["id"]: it["status"] for it in data["items"]}


class WaveSchedulerTrackerWriteTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.tracker_path = str(self.state_dir / "tracker.json")
        self.api = WriteAPI(self.state_dir)
        self.api.tracker_append_item(_item("item-A", "feat/a"))
        self.api.tracker_append_item(_item("item-B", "feat/b"))

    def tearDown(self):
        self.api.close()
        self.temp_dir.cleanup()

    def test_marks_survive_unrelated_writeapi_write(self):
        """Scheduler marks must persist after an unrelated WriteAPI write (F-01 repro)."""
        ok, err = ws._write_tracker_status_atomic(
            self.tracker_path, ["item-A"], "in_progress", "wave-1",
            status_by_id={"item-B": "blocked"},
        )
        self.assertTrue(ok, err)
        self.api.tracker_append_item(_item("item-C", "feat/c"))

        statuses = _statuses(self.tracker_path)
        self.assertEqual(statuses["item-A"], "in_progress")
        self.assertEqual(statuses["item-B"], "blocked")
        self.assertEqual(statuses["item-C"], "todo")

    def test_marks_are_journaled_as_item_updated_events(self):
        """The mark is an event in the log, not a bare file edit."""
        ok, err = ws._write_tracker_status_atomic(
            self.tracker_path, ["item-A"], "in_progress", "wave-1",
        )
        self.assertTrue(ok, err)
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        try:
            events = store.read("tracker")
        finally:
            store.close() if hasattr(store, "close") else None
        updates = [
            e for e in events
            if e.get("type") == "item_updated"
            and (e.get("payload") or {}).get("id") == "item-A"
        ]
        self.assertTrue(updates, "no item_updated event journaled for item-A")
        self.assertEqual(updates[-1]["payload"].get("status"), "in_progress")

    def test_drift_between_log_and_file_fails_closed(self):
        """tracker.json items absent from the event log must not be clobbered."""
        data = json.loads(Path(self.tracker_path).read_text(encoding="utf-8"))
        data["items"].append(_item("item-Z", "feat/z"))
        Path(self.tracker_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        before = Path(self.tracker_path).read_bytes()

        ok, err = ws._write_tracker_status_atomic(
            self.tracker_path, ["item-A"], "in_progress", "wave-1",
        )
        self.assertFalse(ok)
        self.assertIn("tracker_event_log_drift", err or "")
        self.assertEqual(Path(self.tracker_path).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
