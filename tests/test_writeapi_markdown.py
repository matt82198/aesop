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
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
