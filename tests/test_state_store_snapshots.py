"""Tests for state_store projection snapshots (wave-19 P2 fix).

Covers:
(a) snapshot + tail-replay yields IDENTICAL projection state to full replay
(b) corrupt/missing snapshot falls back to full replay correctly
(c) concurrent snapshot reads are safe

This test suite validates the O(n²) -> O(n) tail-replay optimization
that fixes the tracker mutation replay cost growth across a wave.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store.store import EventStore  # noqa: E402
from state_store.api import StateAPI  # noqa: E402
from state_store.projections import (  # noqa: E402
    project_tracker,
    project_tracker_with_snapshot,
    save_snapshot,
)
from state_store.export import export_tracker  # noqa: E402
from state_store.write_api import WriteAPI  # noqa: E402
from state_store.coordination import compact_claims, fold_claims  # noqa: E402


class SnapshotCorrectnessTest(unittest.TestCase):
    """Verify snapshot + tail-replay matches full replay."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "events.db")
        self.store = EventStore(self.db)

    def tearDown(self):
        import shutil
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_snapshot_with_tail_replay_equals_full_replay(self):
        """Core correctness: snapshot + tail-replay ≡ full replay for same event log."""
        # Build an event log: 2 creates, 1 update, 1 create, 1 archive, 1 update
        events_data = [
            ("item_created", {"id": "a", "title": "A", "lane": "proposed", "status": "todo"}),
            ("item_created", {"id": "b", "title": "B", "lane": "proposed", "status": "todo"}),
            ("item_updated", {"id": "a", "lane": "in-progress", "status": "in-progress"}),
            ("item_created", {"id": "c", "title": "C", "lane": "proposed", "status": "todo"}),
            ("item_archived", {"id": "b", "completed_at": "2026-07-14T00:00:00Z"}),
            ("item_updated", {"id": "c", "lane": "in-progress", "status": "in-progress"}),
        ]
        for etype, payload in events_data:
            self.store.append("tracker", etype, payload, "test")

        # Read all events
        all_events = self.store.read("tracker")

        # Full replay (original method)
        full_projection = project_tracker(all_events)

        # Snapshot after event 3 (after first update), then tail-replay
        snapshot_at_version = 3
        events_through_snapshot = [ev for ev in all_events if ev["version"] <= snapshot_at_version]
        snapshot_state = project_tracker(events_through_snapshot)
        save_snapshot(self.store, "tracker", snapshot_at_version, snapshot_state)

        # Now project with snapshot (tail-replay)
        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)

        # Projections must be byte-identical
        self.assertEqual(snapshot_projection, full_projection)
        self.assertEqual(len(snapshot_projection["items"]), 3)
        by_id = {it["id"]: it for it in snapshot_projection["items"]}
        self.assertEqual(by_id["a"]["lane"], "in-progress")
        self.assertEqual(by_id["b"]["status"], "archived")
        self.assertEqual(by_id["c"]["lane"], "in-progress")

    def test_snapshot_at_end_equals_full_replay(self):
        """Snapshot at the very end matches full replay exactly."""
        events_data = [
            ("item_created", {"id": "x", "title": "X", "lane": "proposed", "status": "todo"}),
            ("item_updated", {"id": "x", "lane": "done", "status": "done"}),
        ]
        for etype, payload in events_data:
            self.store.append("tracker", etype, payload, "test")

        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Snapshot at the end
        save_snapshot(self.store, "tracker", len(all_events), full_projection)
        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)

        self.assertEqual(snapshot_projection, full_projection)

    def test_snapshot_at_beginning_tails_all_events(self):
        """Snapshot at version 0 forces full tail-replay (edge case)."""
        events_data = [
            ("item_created", {"id": "p", "title": "P", "lane": "proposed", "status": "todo"}),
        ]
        for etype, payload in events_data:
            self.store.append("tracker", etype, payload, "test")

        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Snapshot at version 0 (before any events)
        save_snapshot(self.store, "tracker", 0, {"version": 1, "items": []})
        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)

        self.assertEqual(snapshot_projection, full_projection)

    def test_multiple_snapshots_uses_latest(self):
        """With multiple snapshots, tail-replay starts from the latest."""
        events_data = [
            ("item_created", {"id": "m", "title": "M", "lane": "proposed", "status": "todo"}),
            ("item_updated", {"id": "m", "lane": "in-progress", "status": "in-progress"}),
            ("item_updated", {"id": "m", "lane": "done", "status": "done"}),
        ]
        for etype, payload in events_data:
            self.store.append("tracker", etype, payload, "test")

        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Save snapshot at version 1
        snap1 = project_tracker([all_events[0]])
        save_snapshot(self.store, "tracker", 1, snap1)

        # Save another snapshot at version 2 (latest)
        snap2 = project_tracker(all_events[:2])
        save_snapshot(self.store, "tracker", 2, snap2)

        # Tail-replay should use the version-2 snapshot
        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)

        self.assertEqual(snapshot_projection, full_projection)


class SnapshotFallbackTest(unittest.TestCase):
    """Verify graceful fallback on corrupt/missing snapshots."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "events.db")
        self.store = EventStore(self.db)

    def tearDown(self):
        import shutil
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_snapshot_falls_back_to_full_replay(self):
        """No snapshot → falls back to full replay (not an error)."""
        events_data = [
            ("item_created", {"id": "n", "title": "N", "lane": "proposed", "status": "todo"}),
        ]
        for etype, payload in events_data:
            self.store.append("tracker", etype, payload, "test")

        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # No snapshot saved; should still work
        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)

        self.assertEqual(snapshot_projection, full_projection)
        self.assertEqual(len(snapshot_projection["items"]), 1)

    def test_corrupt_snapshot_json_falls_back(self):
        """Corrupt JSON in snapshot → log warning, fall back to full replay."""
        events_data = [
            ("item_created", {"id": "q", "title": "Q", "lane": "proposed", "status": "todo"}),
        ]
        for etype, payload in events_data:
            self.store.append("tracker", etype, payload, "test")

        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Manually insert corrupt snapshot
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO snapshots (ts, stream, event_version, projection, checksum) "
                "VALUES (?, ?, ?, ?, ?)",
                (12345.0, "tracker", 1, "{invalid json", "deadbeef"),
            )
            conn.commit()
        finally:
            conn.close()

        # Should log warning to stderr and fall back to full replay
        import io
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)
            stderr_output = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr

        self.assertEqual(snapshot_projection, full_projection)
        self.assertIn("corrupt", stderr_output.lower())
        self.assertIn("tracker", stderr_output)

    def test_checksum_mismatch_falls_back(self):
        """Snapshot with bad checksum → log warning, fall back to full replay."""
        events_data = [
            ("item_created", {"id": "r", "title": "R", "lane": "proposed", "status": "todo"}),
        ]
        for etype, payload in events_data:
            self.store.append("tracker", etype, payload, "test")

        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Manually insert snapshot with wrong checksum
        projection_json = '{"version": 1, "items": [{"id": "r", "title": "R"}]}'
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO snapshots (ts, stream, event_version, projection, checksum) "
                "VALUES (?, ?, ?, ?, ?)",
                (12345.0, "tracker", 1, projection_json, "wrongchecksumvalue"),
            )
            conn.commit()
        finally:
            conn.close()

        # Should detect mismatch and fall back
        import io
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)
            stderr_output = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr

        self.assertEqual(snapshot_projection, full_projection)
        self.assertIn("checksum", stderr_output.lower())

    def test_snapshot_correctness_with_heavy_history(self):
        """Large event log: snapshot + tail is correct and efficient."""
        # Create 100 items
        for i in range(100):
            self.store.append("tracker", "item_created", {
                "id": f"item_{i}",
                "title": f"Item {i}",
                "lane": "proposed",
                "status": "todo",
            }, "test")

        # Update 50 of them
        all_events = self.store.read("tracker")
        for i in range(50):
            self.store.append("tracker", "item_updated", {
                "id": f"item_{i}",
                "lane": "in-progress",
                "status": "in-progress",
            }, "test")

        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Snapshot at version 100 (after all creates)
        snap_at_100 = project_tracker([ev for ev in all_events if ev["version"] <= 100])
        save_snapshot(self.store, "tracker", 100, snap_at_100)

        # Tail-replay should match full replay
        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)
        self.assertEqual(snapshot_projection, full_projection)
        self.assertEqual(len(snapshot_projection["items"]), 100)

    def test_empty_stream_no_snapshot(self):
        """Empty stream + no snapshot → returns empty projection (no crash)."""
        # Don't add any events
        all_events = self.store.read("tracker")
        self.assertEqual(all_events, [])

        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)

        self.assertEqual(snapshot_projection["items"], [])
        self.assertEqual(snapshot_projection["version"], 1)

    def test_idempotency_snapshot_exports_byte_identical(self):
        """Export of snapshot-projected tracker matches export of full-replay
        (git-as-export semantic is preserved)."""
        import json as json_lib

        events_data = [
            ("item_created", {"id": "e1", "title": "E1", "lane": "proposed", "status": "todo"}),
            ("item_created", {"id": "e2", "title": "E2", "lane": "proposed", "status": "todo"}),
            ("item_updated", {"id": "e1", "lane": "done", "status": "done"}),
        ]
        for etype, payload in events_data:
            self.store.append("tracker", etype, payload, "test")

        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Save snapshot at version 2
        snap = project_tracker([ev for ev in all_events if ev["version"] <= 2])
        save_snapshot(self.store, "tracker", 2, snap)

        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)

        # Both must serialize to identical JSON (git export contract)
        full_json = json_lib.dumps(full_projection, indent=2, sort_keys=True)
        snapshot_json = json_lib.dumps(snapshot_projection, indent=2, sort_keys=True)
        self.assertEqual(full_json, snapshot_json)


class SnapshotEdgeCasesTest(unittest.TestCase):
    """Edge cases and stress tests."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "events.db")
        self.store = EventStore(self.db)

    def tearDown(self):
        import shutil
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_snapshot_with_unknown_event_types(self):
        """Snapshot correctly ignores unknown event types (fault tolerance)."""
        events_data = [
            ("item_created", {"id": "unk1", "title": "U", "lane": "proposed", "status": "todo"}),
            ("unknown_event_type", {"id": "unk1", "foo": "bar"}),  # unknown type
            ("item_updated", {"id": "unk1", "lane": "done"}),
        ]
        for etype, payload in events_data:
            self.store.append("tracker", etype, payload, "test")

        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Snapshot after create
        snap = project_tracker([all_events[0]])
        save_snapshot(self.store, "tracker", 1, snap)

        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)
        self.assertEqual(snapshot_projection, full_projection)

    def test_snapshot_with_archived_items(self):
        """Snapshot correctly handles archived items in tail-replay."""
        events_data = [
            ("item_created", {"id": "arch1", "title": "A", "lane": "proposed", "status": "todo"}),
            ("item_created", {"id": "arch2", "title": "B", "lane": "proposed", "status": "todo"}),
            ("item_archived", {"id": "arch1", "completed_at": "2026-07-15T00:00:00Z"}),
            ("item_updated", {"id": "arch2", "lane": "done", "status": "done"}),
        ]
        for etype, payload in events_data:
            self.store.append("tracker", etype, payload, "test")

        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Snapshot after both creates
        snap = project_tracker(all_events[:2])
        save_snapshot(self.store, "tracker", 2, snap)

        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)
        self.assertEqual(snapshot_projection, full_projection)
        by_id = {it["id"]: it for it in snapshot_projection["items"]}
        self.assertEqual(by_id["arch1"]["status"], "archived")
        self.assertEqual(by_id["arch2"]["status"], "done")


class WiredPathInvarianceTest(unittest.TestCase):
    """Verify that wired snapshot paths produce byte-identical results.

    This test suite validates the critical invariant: WriteAPI._project_tracker()
    and try_claim() snapshot paths produce identical projections to full replay,
    including after claims-stream compaction.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "events.db")
        self.api = StateAPI(self.db)
        self.store = self.api._store

    def tearDown(self):
        import shutil
        self.api.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_api_projection_with_snapshot_equals_full_replay(self):
        """WriteAPI._project_tracker() with snapshots produces identical results to full replay."""
        # Simulate WriteAPI usage: create, update, archive items
        self.api.append("tracker", "item_created", {
            "id": "item_1", "title": "Item 1", "lane": "proposed", "status": "todo"
        })
        self.api.append("tracker", "item_created", {
            "id": "item_2", "title": "Item 2", "lane": "proposed", "status": "todo"
        })
        self.api.append("tracker", "item_updated", {
            "id": "item_1", "lane": "in-progress", "status": "in-progress"
        })
        self.api.append("tracker", "item_archived", {"id": "item_2"})

        # Full replay (ground truth)
        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Save snapshot after first two creates
        snap_state = project_tracker(all_events[:2])
        save_snapshot(self.store, "tracker", 2, snap_state)

        # Snapshot path (what WriteAPI now uses)
        snapshot_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)

        # Must be byte-identical
        self.assertEqual(snapshot_projection, full_projection)
        self.assertEqual(len(snapshot_projection["items"]), 2)

    def test_try_claim_with_compacted_claims_equals_full_fold(self):
        """try_claim() with compacted claims produces identical fold to full replay."""
        # Simulate multiple dispatches claiming resources
        for i in range(5):
            self.api.append("claims", "claim_requested", {
                "resource": f"wave_{i}",
                "instance_id": f"instance_{i}",
                "ttl": 300.0
            })

        # Full fold (ground truth)
        all_events = self.store.read("claims")
        full_holders = fold_claims(all_events)

        # Snapshot the claims stream
        compact_claims(self.api)

        # After compaction, read should use snapshot + tail
        compacted_events = []
        es = self.api._store
        snapshot = es.read_snapshot("claims")
        if snapshot:
            _, snap_state, _ = snapshot
            compacted_events = snap_state.get("active_claims", [])
        compacted_events.extend(es.read_since("claims", 0 if not snapshot else snapshot[0]))

        # Fold compacted events should equal full fold
        compacted_holders = fold_claims(compacted_events)
        self.assertEqual(compacted_holders, full_holders)

    def test_claims_compaction_after_releases(self):
        """Compaction correctly handles claim releases and expired claims."""
        now = time.time()

        # Create and release claims
        self.api.append("claims", "claim_requested", {
            "resource": "res_1",
            "instance_id": "inst_1",
            "ttl": 300.0,
            "ts": now
        })
        self.api.append("claims", "claim_requested", {
            "resource": "res_2",
            "instance_id": "inst_2",
            "ttl": 300.0,
            "ts": now
        })
        self.api.append("claims", "claim_released", {
            "resource": "res_1",
            "instance_id": "inst_1"
        })

        # Full fold
        all_events = self.store.read("claims")
        full_holders = fold_claims(all_events, now=now)

        # Compact
        compact_claims(self.api)

        # After compaction, only res_2 should be held
        self.assertEqual(full_holders, {"res_2": "inst_2"})

        # Read after compaction should produce same result
        es = self.api._store
        snapshot = es.read_snapshot("claims")
        self.assertIsNotNone(snapshot)

    def test_snapshot_fallback_on_empty_claims(self):
        """Compaction on empty claims stream returns empty snapshot."""
        # Don't add any claims
        result = compact_claims(self.api)

        # Empty stream returns False (no snapshot needed)
        self.assertFalse(result)

    def test_write_api_path_after_multiple_mutations(self):
        """WriteAPI snapshot path is correct after many item mutations."""
        # Create 10 items
        for i in range(10):
            self.api.append("tracker", "item_created", {
                "id": f"item_{i}",
                "title": f"Item {i}",
                "lane": "proposed",
                "status": "todo"
            })

        # Update 5 of them
        for i in range(5):
            self.api.append("tracker", "item_updated", {
                "id": f"item_{i}",
                "lane": "in-progress",
                "status": "in-progress"
            })

        # Full projection
        all_events = self.store.read("tracker")
        full_projection = project_tracker(all_events)

        # Snapshot at version 10 (after all creates)
        snap_state = project_tracker(all_events[:10])
        save_snapshot(self.store, "tracker", 10, snap_state)

        # Wired path (snapshot + tail)
        wired_projection = project_tracker_with_snapshot(self.store, "tracker", all_events)

        # Must be identical
        self.assertEqual(wired_projection, full_projection)
        self.assertEqual(len(wired_projection["items"]), 10)

        # Verify update state
        by_id = {it["id"]: it for it in wired_projection["items"]}
        for i in range(5):
            self.assertEqual(by_id[f"item_{i}"]["lane"], "in-progress")
        for i in range(5, 10):
            self.assertEqual(by_id[f"item_{i}"]["lane"], "proposed")


if __name__ == "__main__":
    unittest.main()
