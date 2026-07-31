"""Regression test for tracker creation from empty state (Linux CI issue).

Tests that creating a tracker item works correctly when starting with an empty
state directory, which is the scenario in browser-proofs CI test.

Run: python -m pytest tests/test_tracker_create_empty_state.py -xvs
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"


def load_serve(fixture_root):
    """Import a fresh serve module bound to a fixture AESOP_ROOT."""
    import importlib.util
    os.environ["AESOP_ROOT"] = str(fixture_root)
    os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(fixture_root / "transcripts")
    os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

    spec = importlib.util.spec_from_file_location(
        f"serve_empty_tracker_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


class TrackerCreateEmptyStateTest(unittest.TestCase):
    """Test tracker creation from completely empty state (no DB, no JSON)."""

    def setUp(self):
        """Create a completely empty fixture (no state files at all)."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="tracker-empty-state-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()

        self.serve = load_serve(self.fixture_root)

        # Import the API module after serve initializes config
        import api.tracker
        import config as ui_config
        self.tracker_api = api.tracker
        self.config = ui_config
        self.token = self.serve.SESSION_TOKEN

    def tearDown(self):
        """Clean up fixture."""
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def test_create_item_from_empty_state(self):
        """Test creating a tracker item when no tracker.json or tracker_events.db exist."""
        # Verify state is truly empty
        self.assertFalse((self.fixture_root / "state" / "tracker.json").exists())
        self.assertFalse((self.fixture_root / "state" / "tracker_events.db").exists())

        # Create an item via the API
        body = json.dumps({"title": "Test item from empty state"}).encode("utf-8")
        headers = {
            "X-Aesop-Token": self.token,
            "Content-Length": str(len(body)),
        }

        status, result = self.tracker_api.create(headers, body)

        # Should succeed and create the item
        self.assertEqual(status, 201, f"Expected 201, got {status}: {result}")
        self.assertIn("id", result)
        self.assertEqual(result["title"], "Test item from empty state")

        # Verify tracker.json was created
        self.assertTrue((self.fixture_root / "state" / "tracker.json").exists())

        # Verify the item is readable via GET
        status, items = self.tracker_api.list_items()
        self.assertEqual(status, 200)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Test item from empty state")

    def test_create_multiple_items_from_empty_state(self):
        """Test creating multiple items sequentially from empty state."""
        for i in range(3):
            body = json.dumps({"title": f"Item {i}"}).encode("utf-8")
            headers = {
                "X-Aesop-Token": self.token,
                "Content-Length": str(len(body)),
            }

            status, result = self.tracker_api.create(headers, body)
            self.assertEqual(status, 201, f"Failed to create item {i}: {result}")

        # Verify all items are present
        status, items = self.tracker_api.list_items()
        self.assertEqual(status, 200)
        self.assertEqual(len(items), 3)
        titles = {item["title"] for item in items}
        self.assertEqual(titles, {"Item 0", "Item 1", "Item 2"})

    def test_create_item_with_existing_tracker_json(self):
        """Test creating an item when tracker.json already exists (migration scenario)."""
        # Create an existing tracker.json with one item
        existing_item = {
            "id": "existing-001",
            "title": "Existing item",
            "status": "todo",
            "priority": "P1",
            "source": "fixture",
        }
        (self.fixture_root / "state" / "tracker.json").write_text(
            json.dumps({"version": 1, "items": [existing_item]}),
            encoding="utf-8"
        )

        # Create a new item
        body = json.dumps({"title": "New item after migration"}).encode("utf-8")
        headers = {
            "X-Aesop-Token": self.token,
            "Content-Length": str(len(body)),
        }

        status, result = self.tracker_api.create(headers, body)
        self.assertEqual(status, 201, f"Failed to create new item: {result}")

        # Verify both items are present
        status, items = self.tracker_api.list_items()
        self.assertEqual(status, 200)
        self.assertEqual(len(items), 2)
        titles = {item["title"] for item in items}
        self.assertIn("Existing item", titles)
        self.assertIn("New item after migration", titles)


if __name__ == "__main__":
    unittest.main()
