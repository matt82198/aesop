"""Regression test for tracker creation from empty state (Linux CI issue).

Tests that creating a tracker item works correctly when starting with an empty
state directory, which is the scenario in browser-proofs CI test.

Run: python -m pytest tests/test_tracker_create_empty_state.py -xvs
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"
UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

_FIXTURE_ENV_KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_UI_COLLECT_INTERVAL")


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

        # api.tracker reads config.TRACKER_FILE live, so the fixture must rebind config to
        # its own state root (env first, then reload) -- otherwise writes land in whatever
        # root an earlier test in this process left bound (see the isolation test below).
        import config as ui_config
        self._saved_env = {k: os.environ.get(k) for k in _FIXTURE_ENV_KEYS}
        os.environ["AESOP_STATE_ROOT"] = str(self.fixture_root / "state")
        self.serve = load_serve(self.fixture_root)
        ui_config.reload()

        # Import the API module after serve initializes config
        import api.tracker
        self.tracker_api = api.tracker
        self.config = ui_config
        self.token = self.serve.SESSION_TOKEN

    def tearDown(self):
        """Restore env, rebind config away from the removed fixture, clean up."""
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.config.reload()
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

        status, result = self.tracker_api.create(headers, body, expected_port=8770)

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

            status, result = self.tracker_api.create(headers, body, expected_port=8770)
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

        status, result = self.tracker_api.create(headers, body, expected_port=8770)
        self.assertEqual(status, 201, f"Failed to create new item: {result}")

        # Verify both items are present
        status, items = self.tracker_api.list_items()
        self.assertEqual(status, 200)
        self.assertEqual(len(items), 2)
        titles = {item["title"] for item in items}
        self.assertIn("Existing item", titles)
        self.assertIn("New item after migration", titles)


class TrackerStateIsolationFromForeignBindingTest(unittest.TestCase):
    """Regression: api.tracker reads config.TRACKER_FILE live, so a fixture that does not
    config.reload() writes into whichever state root an earlier test left bound in the
    shard process. Items then pile up across tests (5 != 3, 7 != 2, 10 != 3)."""

    KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_UI_COLLECT_INTERVAL")

    def test_create_lands_in_own_fixture_after_foreign_config_binding(self):
        import config as ui_config
        saved = {k: os.environ.get(k) for k in self.KEYS}
        decoy = Path(tempfile.mkdtemp(prefix="tracker-decoy-"))
        (decoy / "state").mkdir()
        (decoy / "transcripts").mkdir()
        try:
            # Simulate an earlier test leaving config bound to its own state root.
            os.environ["AESOP_ROOT"] = str(decoy)
            os.environ["AESOP_STATE_ROOT"] = str(decoy / "state")
            os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(decoy / "transcripts")
            ui_config.reload()
            case = TrackerCreateEmptyStateTest("test_create_multiple_items_from_empty_state")
            case.setUp()
            try:
                body = json.dumps({"title": "Isolation probe"}).encode("utf-8")
                headers = {"X-Aesop-Token": case.token, "Content-Length": str(len(body))}
                status, result = case.tracker_api.create(headers, body, expected_port=8770)
                self.assertEqual(status, 201, f"Expected 201, got {status}: {result}")
                self.assertTrue((case.fixture_root / "state" / "tracker.json").exists())
                self.assertFalse((decoy / "state" / "tracker.json").exists(),
                                 "item leaked into the foreign bound state root")
            finally:
                case.tearDown()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            ui_config.reload()
            shutil.rmtree(decoy, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
