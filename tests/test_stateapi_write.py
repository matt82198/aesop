#!/usr/bin/env python3
"""
tests.test_stateapi_write — Test suite for WriteAPI (state consolidation write facade).

Tests the write path for tracker mutations: status updates and item creation.
Validates fail-closed semantics, atomic projection rendering, and conflict detection.

TDD-organized: gap-centric, no hypothetical tests.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on path
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from state_store import EventStore, ConcurrencyConflict
from state_store.write_api import WriteAPI, WriteConflict


class WriteAPIBasicTest(unittest.TestCase):
    """Basic WriteAPI functionality: item creation and status updates."""

    def setUp(self):
        """Create a temp state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.api = WriteAPI(self.state_dir)

    def tearDown(self):
        """Clean up temp directory."""
        self.api.close()
        self.temp_dir.cleanup()

    def test_tracker_append_item_creates_item(self):
        """tracker_append_item creates a new item with correct fields."""
        item_dict = {
            "title": "Test task",
            "priority": "P0",
            "status": "in-progress",
            "lane": "active",
            "source": "test",
        }

        created = self.api.tracker_append_item(item_dict, actor="test-actor")

        # Verify fields
        self.assertEqual(created["title"], "Test task")
        self.assertEqual(created["priority"], "P0")
        self.assertEqual(created["status"], "in-progress")
        self.assertEqual(created["lane"], "active")
        self.assertEqual(created["source"], "test")
        self.assertIsNotNone(created["id"])
        self.assertIsNotNone(created["created_at"])
        self.assertIsNone(created["completed_at"])

    def test_tracker_append_item_default_values(self):
        """tracker_append_item uses sensible defaults for missing fields."""
        item_dict = {"title": "Minimal task"}

        created = self.api.tracker_append_item(item_dict)

        self.assertEqual(created["priority"], "P1")  # default
        self.assertEqual(created["status"], "todo")  # default
        self.assertEqual(created["lane"], "proposed")  # default
        self.assertEqual(created["source"], "api")  # default (actor)

    def test_tracker_append_item_validates_title(self):
        """tracker_append_item rejects items without a title."""
        # Empty title
        with self.assertRaises(ValueError) as ctx:
            self.api.tracker_append_item({"title": ""})
        self.assertIn("title", str(ctx.exception).lower())

        # Missing title
        with self.assertRaises(ValueError) as ctx:
            self.api.tracker_append_item({})
        self.assertIn("title", str(ctx.exception).lower())

        # Non-dict
        with self.assertRaises(ValueError) as ctx:
            self.api.tracker_append_item("not a dict")
        self.assertIn("dict", str(ctx.exception).lower())

    def test_tracker_append_item_appends_event(self):
        """tracker_append_item appends an event to the event store."""
        self.api.tracker_append_item({"title": "Event test"})

        # Read events directly from store
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        try:
            events = store.read("tracker")

            # Should have at least one event (the created event)
            self.assertGreater(len(events), 0)
            # Find the item_created event
            created_events = [e for e in events if e["type"] == "item_created"]
            self.assertEqual(len(created_events), 1)
            self.assertEqual(created_events[0]["payload"]["title"], "Event test")
        finally:
            store.close()

    def test_tracker_append_item_updates_projection(self):
        """tracker_append_item updates tracker.json with the new item."""
        created = self.api.tracker_append_item({"title": "Projection test"})

        # Read tracker.json directly
        tracker_file = self.state_dir / "tracker.json"
        self.assertTrue(tracker_file.exists())
        tracker_data = json.loads(tracker_file.read_text(encoding="utf-8"))

        # Item should be in the projection
        items_by_id = {item["id"]: item for item in tracker_data.get("items", [])}
        self.assertIn(created["id"], items_by_id)
        self.assertEqual(items_by_id[created["id"]]["title"], "Projection test")

    def test_tracker_update_status_updates_item(self):
        """tracker_update_status updates an item's status."""
        created = self.api.tracker_append_item({"title": "Status test"})
        item_id = created["id"]

        # Update status
        updated = self.api.tracker_update_status(item_id, "done")

        self.assertEqual(updated["status"], "done")
        self.assertEqual(updated["id"], item_id)
        self.assertEqual(updated["title"], "Status test")  # Other fields unchanged

    def test_tracker_update_status_appends_event(self):
        """tracker_update_status appends an event to the event store."""
        created = self.api.tracker_append_item({"title": "Event update test"})
        item_id = created["id"]

        # Update status
        self.api.tracker_update_status(item_id, "in-progress")

        # Read events from store
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        try:
            events = store.read("tracker")

            # Should have 2 events: created + updated
            self.assertGreater(len(events), 1)
            # Find the item_updated event
            updated_events = [
                e for e in events if e["type"] == "item_updated" and e["payload"].get("id") == item_id
            ]
            self.assertEqual(len(updated_events), 1)
            self.assertEqual(updated_events[0]["payload"]["status"], "in-progress")
        finally:
            store.close()

    def test_tracker_update_status_with_note(self):
        """tracker_update_status can add notes to an item."""
        created = self.api.tracker_append_item({"title": "Note test", "notes": "Original note"})
        item_id = created["id"]

        # Update with a note
        updated = self.api.tracker_update_status(item_id, "done", note="Work completed")

        # Notes should be appended
        self.assertIn("Original note", updated.get("notes", ""))
        self.assertIn("Work completed", updated.get("notes", ""))

    def test_tracker_update_status_item_not_found(self):
        """tracker_update_status raises ValueError if item doesn't exist."""
        with self.assertRaises(ValueError) as ctx:
            self.api.tracker_update_status("nonexistent-id", "done")
        self.assertIn("not found", str(ctx.exception).lower())

    def test_multiple_items_independent(self):
        """Multiple items created via WriteAPI are independent."""
        item1 = self.api.tracker_append_item({"title": "Item 1"})
        item2 = self.api.tracker_append_item({"title": "Item 2"})

        # Update only item1
        self.api.tracker_update_status(item1["id"], "done")

        # Read tracker and verify item2 is unchanged
        tracker_file = self.state_dir / "tracker.json"
        tracker_data = json.loads(tracker_file.read_text(encoding="utf-8"))
        items_by_id = {item["id"]: item for item in tracker_data["items"]}

        self.assertEqual(items_by_id[item1["id"]]["status"], "done")
        self.assertEqual(items_by_id[item2["id"]]["status"], "todo")


class WriteAPIFailClosedTest(unittest.TestCase):
    """Test fail-closed semantics: event append failure blocks projection write."""

    def setUp(self):
        """Create a temp state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.api = WriteAPI(self.state_dir)

    def tearDown(self):
        """Clean up temp directory."""
        self.api.close()
        self.temp_dir.cleanup()

    def test_invalid_item_raises_before_append(self):
        """Invalid item data raises ValueError before any event append."""
        # tracker_append_item validates title before appending event
        # This ensures fail-closed for invalid input

        # Empty title should raise immediately (before event append)
        with self.assertRaises(ValueError) as ctx:
            self.api.tracker_append_item({"title": ""})
        self.assertIn("title", str(ctx.exception).lower())

        # Verify no events were appended for the failed operation
        tracker_file = self.state_dir / "tracker.json"
        if tracker_file.exists():
            tracker_data = json.loads(tracker_file.read_text(encoding="utf-8"))
            # Should be empty or not exist
            items = tracker_data.get("items", [])
            self.assertEqual(len(items), 0)

    def test_projection_consistency_after_write(self):
        """After a successful write, tracker.json reflects the event store state."""
        # This validates the fail-closed property indirectly:
        # If projection write fails silently, items would exist in event store
        # but not in tracker.json. We verify they're always in sync.

        item1 = self.api.tracker_append_item({"title": "Item 1"})
        item2 = self.api.tracker_append_item({"title": "Item 2"})

        # Read projection
        tracker_file = self.state_dir / "tracker.json"
        tracker_data = json.loads(tracker_file.read_text(encoding="utf-8"))
        projection_items = {i["id"]: i for i in tracker_data["items"]}

        # Both items should be in projection
        self.assertIn(item1["id"], projection_items)
        self.assertIn(item2["id"], projection_items)

        # Now verify against event store
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        try:
            events = store.read("tracker")
            event_items = {e["payload"]["id"]: e["payload"]
                           for e in events if e["type"] == "item_created"}

            # Projection and event store should have same items
            self.assertEqual(set(projection_items.keys()), set(event_items.keys()))
        finally:
            store.close()


class WriteAPIProjectionTest(unittest.TestCase):
    """Test projection consistency: events are folded correctly into tracker.json."""

    def setUp(self):
        """Create a temp state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.api = WriteAPI(self.state_dir)

    def tearDown(self):
        """Clean up temp directory."""
        self.api.close()
        self.temp_dir.cleanup()

    def test_projection_matches_events(self):
        """The tracker.json projection matches the events in the event store."""
        # Create and update an item
        item = self.api.tracker_append_item(
            {"title": "Consistency test", "priority": "P0"}
        )
        self.api.tracker_update_status(item["id"], "in-progress")

        # Read tracker.json
        tracker_file = self.state_dir / "tracker.json"
        tracker_data = json.loads(tracker_file.read_text(encoding="utf-8"))

        # Find item in projection
        items_by_id = {i["id"]: i for i in tracker_data["items"]}
        projected_item = items_by_id[item["id"]]

        # Verify state matches latest update
        self.assertEqual(projected_item["status"], "in-progress")
        self.assertEqual(projected_item["title"], "Consistency test")
        self.assertEqual(projected_item["priority"], "P0")

    def test_empty_projection_on_first_write(self):
        """tracker.json has correct structure on first write."""
        tracker_file = self.state_dir / "tracker.json"

        # File should not exist yet
        self.assertFalse(tracker_file.exists())

        # Create an item
        self.api.tracker_append_item({"title": "First item"})

        # Now it should exist with proper structure
        self.assertTrue(tracker_file.exists())
        tracker_data = json.loads(tracker_file.read_text(encoding="utf-8"))

        self.assertIn("version", tracker_data)
        self.assertEqual(tracker_data["version"], 1)
        self.assertIn("items", tracker_data)
        self.assertIsInstance(tracker_data["items"], list)
        self.assertEqual(len(tracker_data["items"]), 1)


class WriteAPIAtomicityTest(unittest.TestCase):
    """Test atomic writes: tempfile + os.replace ensures atomicity."""

    def setUp(self):
        """Create a temp state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.api = WriteAPI(self.state_dir)

    def tearDown(self):
        """Clean up temp directory."""
        self.api.close()
        self.temp_dir.cleanup()

    def test_temp_file_cleanup(self):
        """Temp files are cleaned up after successful write."""
        self.api.tracker_append_item({"title": "Temp cleanup test"})

        # List files in state dir
        files = list(self.state_dir.glob("*"))
        # Should only have tracker.json and tracker_events.db* (WAL files)
        # No .tracker-*.json.tmp files should remain
        temp_files = [f for f in files if ".tracker-" in f.name and ".tmp" in f.name]
        self.assertEqual(len(temp_files), 0, f"Leftover temp files: {temp_files}")

    def test_multiple_rapid_writes(self):
        """Multiple rapid writes produce consistent state."""
        # Create several items quickly
        created_ids = []
        for i in range(5):
            item = self.api.tracker_append_item({"title": f"Item {i}"})
            created_ids.append(item["id"])

        # Verify all are in the projection
        tracker_file = self.state_dir / "tracker.json"
        tracker_data = json.loads(tracker_file.read_text(encoding="utf-8"))
        items_by_id = {i["id"]: i for i in tracker_data["items"]}

        for item_id in created_ids:
            self.assertIn(item_id, items_by_id)


class WriteAPIEdgeCasesTest(unittest.TestCase):
    """Test edge cases and error handling."""

    def setUp(self):
        """Create a temp state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.api = WriteAPI(self.state_dir)

    def tearDown(self):
        """Clean up temp directory."""
        self.api.close()
        self.temp_dir.cleanup()

    def test_append_item_with_custom_id(self):
        """tracker_append_item respects custom id if provided."""
        item_dict = {"title": "Custom ID", "id": "custom-123"}
        created = self.api.tracker_append_item(item_dict)
        self.assertEqual(created["id"], "custom-123")

    def test_append_item_sanitizes_title(self):
        """tracker_append_item strips whitespace from title."""
        item_dict = {"title": "  Padded title  "}
        created = self.api.tracker_append_item(item_dict)
        self.assertEqual(created["title"], "Padded title")

    def test_append_item_with_tags(self):
        """tracker_append_item preserves tags list."""
        item_dict = {"title": "Tagged", "tags": ["security", "urgent"]}
        created = self.api.tracker_append_item(item_dict)
        self.assertEqual(created["tags"], ["security", "urgent"])

    def test_append_item_with_invalid_tags(self):
        """tracker_append_item converts non-list tags to empty list."""
        item_dict = {"title": "Bad tags", "tags": "not-a-list"}
        created = self.api.tracker_append_item(item_dict)
        self.assertEqual(created["tags"], [])

    def test_update_status_preserves_other_fields(self):
        """tracker_update_status preserves unmodified fields."""
        created = self.api.tracker_append_item({
            "title": "Preservation test",
            "priority": "P0",
            "lane": "active",
            "tags": ["important"],
        })
        item_id = created["id"]

        # Update only status
        updated = self.api.tracker_update_status(item_id, "done")

        # Other fields should be unchanged
        self.assertEqual(updated["priority"], "P0")
        self.assertEqual(updated["lane"], "active")
        self.assertEqual(updated["tags"], ["important"])
        self.assertEqual(updated["title"], "Preservation test")

    def test_unicode_in_items(self):
        """WriteAPI handles unicode correctly in titles, notes, etc."""
        item_dict = {
            "title": "Unicode test: 🎯 中文 العربية",
            "notes": "Note with emoji: ✅ 🔥 📝",
        }
        created = self.api.tracker_append_item(item_dict)

        # Verify unicode is preserved
        self.assertEqual(created["title"], "Unicode test: 🎯 中文 العربية")
        self.assertEqual(created["notes"], "Note with emoji: ✅ 🔥 📝")


class WriteAPIIntegrationTest(unittest.TestCase):
    """Integration tests: WriteAPI interacting with existing tracker.json."""

    def setUp(self):
        """Create a temp state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.api = WriteAPI(self.state_dir)

    def tearDown(self):
        """Clean up temp directory."""
        self.api.close()
        self.temp_dir.cleanup()

    def test_append_to_existing_tracker_with_conflict(self):
        """WriteAPI detects conflict when tracker.json has unexplained items (not in event store)."""
        # Create a baseline tracker.json with an item not in the event store
        tracker_file = self.state_dir / "tracker.json"
        baseline_tracker = {
            "version": 1,
            "items": [
                {"id": "baseline-1", "title": "Existing item", "status": "todo", "priority": "P1", "lane": "proposed", "source": "external", "tags": [], "notes": None, "pr_link": None, "created_at": "2026-07-22T00:00:00Z", "completed_at": None},
            ],
        }
        tracker_file.write_text(json.dumps(baseline_tracker), encoding="utf-8")

        # Try to append via WriteAPI: should detect conflict
        # because baseline-1 is on disk but not in event store
        with self.assertRaises(WriteConflict) as ctx:
            self.api.tracker_append_item({"title": "New item"})

        self.assertIn("unexplained", str(ctx.exception).lower())

    def test_load_empty_state_dir(self):
        """WriteAPI handles empty/nonexistent state directory."""
        # State dir is empty
        self.assertFalse((self.state_dir / "tracker.json").exists())

        # Create an item (should work fine)
        item = self.api.tracker_append_item({"title": "First item"})
        self.assertIsNotNone(item["id"])

        # Now tracker.json should exist
        self.assertTrue((self.state_dir / "tracker.json").exists())


class WriteAPIConflictDetectionTest(unittest.TestCase):
    """Test OCC (Optimistic Concurrency Control) conflict detection.

    P1 DEFECT FIX: Verify that WriteAPI detects concurrent modification
    and raises WriteConflict instead of silently overwriting.
    """

    def setUp(self):
        """Create a temp state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.api = WriteAPI(self.state_dir)
        self._extra_apis = []  # track extra WriteAPI instances

    def tearDown(self):
        """Clean up temp directory."""
        for a in self._extra_apis:
            try:
                a.close()
            except Exception:
                pass
        self.api.close()
        self.temp_dir.cleanup()

    def test_concurrent_writer_detected_raises_writeconflict(self):
        """WriteAPI detects external write to tracker.json and raises WriteConflict."""
        # Use two WriteAPI instances to simulate concurrent modification
        api1 = WriteAPI(self.state_dir)
        api2 = WriteAPI(self.state_dir)
        self._extra_apis.extend([api1, api2])

        # Create initial item via api1
        item1 = api1.tracker_append_item({"title": "Item 1"})

        # Create item via api2 (will capture different start_disk_hash)
        item2 = api2.tracker_append_item({"title": "Item 2"})

        # Now tracker.json has both items
        tracker_file = self.state_dir / "tracker.json"
        tracker_data = json.loads(tracker_file.read_text(encoding="utf-8"))
        self.assertEqual(len(tracker_data["items"]), 2)

        # Manually modify tracker.json to simulate external concurrent write
        # Add an item that's NOT in the event store
        external_item = {
            "id": "external-123",
            "title": "External item",
            "status": "todo",
            "priority": "P1",
            "lane": "proposed",
            "source": "external",
            "tags": [],
            "notes": None,
            "pr_link": None,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "completed_at": None,
        }
        tracker_data["items"].append(external_item)
        tracker_file.write_text(json.dumps(tracker_data, indent=2), encoding="utf-8")

        # Now api1 tries to append item3. Since tracker.json was modified externally
        # AFTER item2 was appended, api1's projection will differ from disk
        # api1's start_disk_hash = hash of [item1, item2]
        # disk_hash after external mod = hash of [item1, item2, external]
        # new_hash from projection = hash of [item1, item2, item3]
        # Since disk_hash != start_disk_hash AND disk_hash != new_hash, conflict!
        with self.assertRaises(WriteConflict):
            api1.tracker_append_item({"title": "Item 3"})

    def test_writeconflict_preserves_disk_state(self):
        """When WriteConflict is raised, disk state is not overwritten."""
        # Create initial state via two instances
        api1 = WriteAPI(self.state_dir)
        api2 = WriteAPI(self.state_dir)
        self._extra_apis.extend([api1, api2])

        item1 = api1.tracker_append_item({"title": "Item 1"})
        item2 = api2.tracker_append_item({"title": "Item 2"})
        tracker_file = self.state_dir / "tracker.json"

        # Verify both are on disk
        disk_state_before = json.loads(tracker_file.read_text(encoding="utf-8"))
        self.assertEqual(len(disk_state_before["items"]), 2)

        # Now externally modify disk to add an item not in event store
        external_item = {
            "id": "external-123",
            "title": "External item",
            "status": "todo",
            "priority": "P1",
            "lane": "proposed",
            "source": "external",
            "tags": [],
            "notes": None,
            "pr_link": None,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "completed_at": None,
        }
        disk_state_before["items"].append(external_item)
        tracker_file.write_text(json.dumps(disk_state_before, indent=2), encoding="utf-8")

        # Try to append via api1 (should fail with conflict)
        # api1's start_disk_hash = hash of [item1, item2]
        # disk is modified to [item1, item2, external]
        # api1's new_hash = hash of [item1, item2, item3]
        # Conflict detected!
        with self.assertRaises(WriteConflict):
            api1.tracker_append_item({"title": "Item 3"})

        # Verify disk still has external item (not overwritten)
        disk_tracker = json.loads(tracker_file.read_text(encoding="utf-8"))
        disk_ids = {i["id"] for i in disk_tracker["items"]}

        # External item should still be there
        self.assertIn("external-123", disk_ids)
        # Should have 3 items: item1, item2, external
        self.assertEqual(len(disk_tracker["items"]), 3)

    def test_corrupt_disk_json_raises_writeconflict(self):
        """Corrupt JSON on disk raises WriteConflict (fail-closed, not fail-open)."""
        # Create initial item
        self.api.tracker_append_item({"title": "Item 1"})
        tracker_file = self.state_dir / "tracker.json"

        # Corrupt the JSON on disk
        tracker_file.write_text("{invalid json}", encoding="utf-8")

        # Try to append: should raise WriteConflict (fail-closed)
        # not silently pass and overwrite corrupt data
        with self.assertRaises(WriteConflict):
            self.api.tracker_append_item({"title": "Item 2"})


class WriteAPIIdCollisionTest(unittest.TestCase):
    """Test ID collision detection.

    P2 DEFECT FIX: tracker_append_item must reject duplicate explicit IDs.
    """

    def setUp(self):
        """Create a temp state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.api = WriteAPI(self.state_dir)

    def tearDown(self):
        """Clean up temp directory."""
        self.api.close()
        self.temp_dir.cleanup()

    def test_duplicate_explicit_id_raises_valueerror(self):
        """tracker_append_item rejects duplicate explicit ID."""
        # Create an item with explicit ID
        self.api.tracker_append_item({"title": "Item 1", "id": "dup-123"})

        # Try to create another item with same ID
        with self.assertRaises(ValueError) as ctx:
            self.api.tracker_append_item({"title": "Item 2", "id": "dup-123"})
        error_msg = str(ctx.exception).lower()
        self.assertTrue("already exists" in error_msg or "duplicate" in error_msg,
                       f"Expected 'already exists' or 'duplicate' in error: {error_msg}")

    def test_auto_generated_ids_never_collide(self):
        """Auto-generated IDs never collide (high probability with secrets.token_hex)."""
        # Create many items with auto-generated IDs
        created_ids = []
        for i in range(20):
            item = self.api.tracker_append_item({"title": f"Item {i}"})
            created_ids.append(item["id"])

        # All IDs should be unique
        self.assertEqual(len(created_ids), len(set(created_ids)))


class WriteAPIProjectionRecoveryTest(unittest.TestCase):
    """Test self-healing projection via rebuild_projection().

    P2 DEFECT FIX: orphaned events are recovered when rebuild_projection() is called.
    """

    def setUp(self):
        """Create a temp state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.api = WriteAPI(self.state_dir)

    def tearDown(self):
        """Clean up temp directory."""
        self.api.close()
        self.temp_dir.cleanup()

    def test_rebuild_projection_recovers_orphaned_event(self):
        """rebuild_projection() forces re-render from event store, recovering orphans."""
        # Create an item
        item1 = self.api.tracker_append_item({"title": "Item 1"})
        tracker_file = self.state_dir / "tracker.json"

        # Simulate orphaned event: manually delete the item from tracker.json
        # but leave it in the event store
        tracker_data = json.loads(tracker_file.read_text(encoding="utf-8"))
        tracker_data["items"] = []  # Remove all items
        tracker_file.write_text(json.dumps(tracker_data, indent=2), encoding="utf-8")

        # Verify item is gone from projection
        stale_tracker = json.loads(tracker_file.read_text(encoding="utf-8"))
        self.assertEqual(len(stale_tracker["items"]), 0)

        # Now rebuild projection (should recover the orphaned event)
        self.api.rebuild_projection()

        # Item should be back in projection
        recovered_tracker = json.loads(tracker_file.read_text(encoding="utf-8"))
        recovered_ids = {i["id"] for i in recovered_tracker["items"]}
        self.assertIn(item1["id"], recovered_ids)

    def test_rebuild_projection_bypasses_conflict_check(self):
        """rebuild_projection() bypasses OCC check for recovery."""
        # Create initial state
        item1 = self.api.tracker_append_item({"title": "Item 1"})
        tracker_file = self.state_dir / "tracker.json"

        # Corrupt projection: remove item but leave event
        tracker_data = json.loads(tracker_file.read_text(encoding="utf-8"))
        tracker_data["items"] = []
        tracker_file.write_text(json.dumps(tracker_data, indent=2), encoding="utf-8")

        # Simulate concurrent modification (so OCC would normally fail)
        external_item = {
            "id": "external-456",
            "title": "External",
            "status": "todo",
            "priority": "P1",
            "lane": "proposed",
            "source": "external",
            "tags": [],
            "notes": None,
            "pr_link": None,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "completed_at": None,
        }
        tracker_data["items"] = [external_item]
        tracker_file.write_text(json.dumps(tracker_data, indent=2), encoding="utf-8")

        # rebuild_projection() should work (always bypasses conflict check for recovery)
        self.api.rebuild_projection()

        # Both items should be in projection now
        recovered_tracker = json.loads(tracker_file.read_text(encoding="utf-8"))
        recovered_ids = {i["id"] for i in recovered_tracker["items"]}
        self.assertIn(item1["id"], recovered_ids)


if __name__ == "__main__":
    unittest.main()
