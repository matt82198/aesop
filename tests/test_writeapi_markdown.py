#!/usr/bin/env python3
"""Tests for WriteAPI markdown write-path unification (WS4 increment 1).

Verifies that WriteAPI provides a unified write path for markdown control files
(STATE.md, BUILDLOG.md) through the event store, preventing drift between
markdown and SQLite state.

Tests:
- write_state_md() atomically appends event AND writes STATE.md
- append_buildlog() atomically appends event AND appends line to BUILDLOG.md
- OCC conflict detection prevents concurrent modification loss
- Round-trip: write → read → verify both representations agree
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store import EventStore  # noqa: E402
from state_store.write_api import WriteAPI, WriteConflict  # noqa: E402


class WriteAPIMarkdownTest(unittest.TestCase):
    """Tests for markdown write unification (STATE.md, BUILDLOG.md)."""

    def setUp(self):
        """Create temp state directory for each test."""
        self.tmp = tempfile.mkdtemp()
        self.state_dir = Path(self.tmp) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.api = WriteAPI(str(self.state_dir))

    def tearDown(self):
        """Clean up temp directory."""
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_state_md_appends_event_and_file(self):
        """write_state_md() should append event AND update STATE.md file."""
        content = "## Phase: `wave-1`\nIntent: test\nNEXT STEPS: none"

        self.api.write_state_md(content, actor="test")

        # Check STATE.md file exists
        state_file = self.state_dir / "STATE.md"
        self.assertTrue(state_file.exists(), "STATE.md should exist after write")

        # Check content matches
        written_content = state_file.read_text(encoding="utf-8")
        self.assertEqual(written_content, content)

        # Check event was appended to state_store
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        events = store.read("state_markdown")
        self.assertEqual(len(events), 1, "One event should be appended to state_markdown stream")
        self.assertEqual(events[0]["type"], "state_md_written")
        self.assertEqual(events[0]["payload"]["content"], content)

    def test_append_buildlog_appends_event_and_line(self):
        """append_buildlog() should append event AND append line to BUILDLOG.md."""
        # First, initialize BUILDLOG.md via WriteAPI
        self.api.ensure_buildlog_exists()

        # Now append entries
        line1 = "[2026-07-29] dispatched agent-1"
        self.api.append_buildlog(line1, actor="test")

        # Check BUILDLOG.md file exists and contains the line
        buildlog_file = self.state_dir / "BUILDLOG.md"
        self.assertTrue(buildlog_file.exists(), "BUILDLOG.md should exist after append")

        content = buildlog_file.read_text(encoding="utf-8")
        self.assertIn(line1, content)

        # Check event was appended to state_store
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        events = store.read("buildlog")
        self.assertEqual(len(events), 1, "One event should be appended to buildlog stream")
        self.assertEqual(events[0]["type"], "buildlog_entry")
        self.assertEqual(events[0]["payload"]["line"], line1)

    def test_multiple_buildlog_appends(self):
        """Multiple append_buildlog() calls should preserve all entries."""
        self.api.ensure_buildlog_exists()

        entries = [
            "[2026-07-29 10:00] start",
            "[2026-07-29 10:05] working",
            "[2026-07-29 10:10] done",
        ]

        for entry in entries:
            self.api.append_buildlog(entry, actor="test")

        # Check all entries in BUILDLOG.md
        buildlog_file = self.state_dir / "BUILDLOG.md"
        content = buildlog_file.read_text(encoding="utf-8")

        for entry in entries:
            self.assertIn(entry, content)

        # Check all events in state_store
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        events = store.read("buildlog")
        self.assertEqual(len(events), len(entries))

    def test_state_md_occ_detects_concurrent_modification(self):
        """write_state_md() with concurrent modification should raise WriteConflict.

        Tests OCC by directly calling _write_markdown_atomic with a pre-existing
        start_disk_hash, simulating a file that changed between capture and write.
        """
        state_file = self.state_dir / "STATE.md"

        # Step 1: Write initial content
        content1 = "## Phase: `wave-1`"
        self.api.write_state_md(content1, actor="test")

        # Step 2: Capture the hash (simulating what an API writer would do)
        hash_after_write1 = self.api._compute_content_hash({"content": content1})

        # Step 3: Simulate external modification (or concurrent API write)
        state_file.write_text("## Phase: `wave-1-alt` (concurrent modification)")
        hash_concurrent = self.api._compute_content_hash(
            {"content": "## Phase: `wave-1-alt` (concurrent modification)"}
        )

        # Step 4: Try to write new content, passing the old hash as start_disk_hash
        # This simulates: API captured hash1, then someone else modified the file,
        # then the original API tries to write
        content2 = "## Phase: `wave-2` (retry)"

        # Manually call _write_markdown_atomic with the captured start hash
        # This should detect conflict since:
        # - start_disk_hash = hash1 (what we captured)
        # - disk_hash = hash_concurrent (current file)
        # - new_hash = hash(content2) (what we're trying to write)
        with self.assertRaises(WriteConflict) as ctx:
            self.api._write_markdown_atomic(state_file, content2, start_disk_hash=hash_after_write1)

        self.assertEqual(
            ctx.exception.expected_hash,
            hash_after_write1,
            "Expected hash should be what we captured before the conflict",
        )
        self.assertEqual(
            ctx.exception.actual_hash,
            hash_concurrent,
            "Actual hash should be the concurrent modification",
        )

    def test_write_state_md_round_trip(self):
        """Write STATE.md, read back, verify consistency."""
        content = "## Phase: `wave-5`\nPriority: features\nNEXT STEPS: implement"
        self.api.write_state_md(content, actor="test")

        # Read it back from file
        state_file = self.state_dir / "STATE.md"
        read_back = state_file.read_text(encoding="utf-8")
        self.assertEqual(read_back, content)

        # Read it back from event store
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        events = store.read("state_markdown")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["content"], content)

    def test_ensure_buildlog_creates_empty_file(self):
        """ensure_buildlog_exists() should create BUILDLOG.md if missing."""
        buildlog_file = self.state_dir / "BUILDLOG.md"
        self.assertFalse(buildlog_file.exists())

        self.api.ensure_buildlog_exists()

        self.assertTrue(buildlog_file.exists())
        content = buildlog_file.read_text(encoding="utf-8")
        # Should have a header
        self.assertIn("BUILDLOG", content)

    def test_buildlog_append_to_existing_file(self):
        """append_buildlog() should append to existing BUILDLOG.md, not overwrite."""
        # Pre-create with some content
        buildlog_file = self.state_dir / "BUILDLOG.md"
        buildlog_file.write_text("# BUILDLOG\ninitial entry\n")

        # Append via API
        self.api.append_buildlog("new entry", actor="test")

        # Verify both content exists
        content = buildlog_file.read_text(encoding="utf-8")
        self.assertIn("initial entry", content)
        self.assertIn("new entry", content)

    def test_state_md_force_rebuild_bypasses_occ(self):
        """rebuild_state_md(force=True) should bypass OCC check."""
        content1 = "## Phase: `wave-1`"
        self.api.write_state_md(content1, actor="test")

        # Simulate concurrent modification
        state_file = self.state_dir / "STATE.md"
        state_file.write_text("## Phase: `wave-2` (external)")

        # Force rebuild should succeed by bypassing OCC
        content2 = "## Phase: `wave-3` (rebuild)"
        self.api.rebuild_state_md(content2, force=True)

        # File should have the new content
        read_back = state_file.read_text(encoding="utf-8")
        self.assertEqual(read_back, content2)

    # ====== F1: rebuild_state_md() must append event to event store ======
    def test_rebuild_state_md_appends_event(self):
        """F1: rebuild_state_md() should append event to state_markdown stream.

        Previously, rebuild_state_md() only wrote the file without appending
        to the event store. This leaves the event stream unaware of the rebuild,
        causing drift between markdown and store.
        """
        content = "## Rebuilt STATE.md content"

        # Call rebuild_state_md
        self.api.rebuild_state_md(content, force=True)

        # Verify the event was appended to the event store
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        events = store.read("state_markdown")

        # Should have at least one event (the rebuild event)
        self.assertGreater(len(events), 0, "rebuild_state_md() should append an event")

        # Find the rebuild event (should be of type "state_md_rebuilt")
        rebuild_events = [e for e in events if e["type"] == "state_md_rebuilt"]
        self.assertEqual(len(rebuild_events), 1, "Should have exactly one state_md_rebuilt event")
        self.assertEqual(rebuild_events[0]["payload"]["content"], content)

    # ====== F2: Event must be appended AFTER file write, not before ======
    def test_write_state_md_event_only_on_success(self):
        """F2: write_state_md() should only append event if file write succeeds.

        Previously, event was appended BEFORE file write. If the file write
        failed (WriteConflict or crash), the event was orphaned in the store,
        claiming content that never reached disk. Now events should be appended
        only AFTER successful file write.
        """
        content = "## Phase: wave-42"

        # First write should succeed
        self.api.write_state_md(content, actor="test")

        # Verify event was appended
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        events_before = len(store.read("state_markdown"))
        self.assertEqual(events_before, 1, "First write should create one event")

        # Now simulate a failure: manually corrupt the event store to force
        # a failure, OR patch the file write to fail after event append
        # We'll use a patch approach: make os.replace fail on the second write

        state_file = self.state_dir / "STATE.md"
        content2 = "## Phase: wave-43 (should fail)"

        # Patch os.replace to raise an exception (simulating atomic write failure)
        with patch("os.replace", side_effect=OSError("Simulated atomic write failure")):
            with self.assertRaises(WriteConflict):
                self.api.write_state_md(content2, actor="test")

        # Verify NO new event was appended (because the file write failed)
        events_after = len(store.read("state_markdown"))
        self.assertEqual(events_after, 1, "Failed write should NOT append event")

        # Verify file still has original content
        disk_content = state_file.read_text(encoding="utf-8")
        self.assertEqual(disk_content, content, "File should still have original content after failed write")

    # ====== F3: TOCTOU race detection via file lock ======
    def test_concurrent_write_race_prevention(self):
        """F3: Concurrent writers should not silently overwrite each other.

        Tests that _write_markdown_atomic uses a file lock to prevent TOCTOU
        race between OCC check and os.replace. Two threads racing to write
        should result in one succeeding and one detecting the conflict.
        """
        state_file = self.state_dir / "STATE.md"

        # Initial state
        initial_content = "## Initial"
        self.api.write_state_md(initial_content, actor="writer1")

        # Track results from both threads
        results = {"writer1": None, "writer2": None}
        exceptions = []

        def writer_thread(thread_id, content):
            """Simulate a concurrent writer."""
            try:
                api = WriteAPI(str(self.state_dir))
                # Capture the hash from the current state
                if state_file.exists():
                    current = state_file.read_text(encoding="utf-8")
                    start_hash = api._compute_content_hash({"content": current})
                else:
                    start_hash = None

                # Small delay to increase chance of race
                time.sleep(0.01)

                # Call the atomic write directly with the captured hash
                api._write_markdown_atomic(state_file, content, start_disk_hash=start_hash)
                results[thread_id] = "success"
            except WriteConflict as e:
                results[thread_id] = "conflict"
                exceptions.append((thread_id, e))
            except Exception as e:
                results[thread_id] = f"error: {e}"
                exceptions.append((thread_id, e))

        # Start two concurrent writers
        t1 = threading.Thread(
            target=writer_thread,
            args=("writer1", "## Content from writer 1")
        )
        t2 = threading.Thread(
            target=writer_thread,
            args=("writer2", "## Content from writer 2")
        )

        t1.start()
        t2.start()

        t1.join()
        t2.join()

        # Verify: exactly one should succeed, one should conflict
        # (or both could succeed if they both write the same content, but that's unlikely)
        # The key is that neither should silently overwrite the other without detection

        # At minimum, we should not have two successful writes with different content
        success_count = sum(1 for r in results.values() if r == "success")
        conflict_count = sum(1 for r in results.values() if r == "conflict")

        # If lock is working, conflicts should occur
        # If lock is broken, both might succeed (bad) or one might overwrite the other silently (worse)
        self.assertGreaterEqual(conflict_count, 1,
            f"At least one writer should detect a conflict with proper locking. Results: {results}")


if __name__ == "__main__":
    unittest.main()
