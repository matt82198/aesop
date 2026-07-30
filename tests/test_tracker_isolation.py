"""Isolation tests for tracker item writes — wave-12 P1 fix.

Ensures that tracker operations only write to the isolated AESOP_STATE_ROOT,
never to the real repo's state/tracker.json (root cause: wave-8 junk items
leaked when tests/proof ran with implicit default state dir).

Test strategy:
  1. Create an isolated temp AESOP_STATE_ROOT
  2. Call collectors.create_tracker_item() with that env
  3. Assert the item was written ONLY to temp tracker.json
  4. Assert the real repo's tracker.json was never opened/modified

Run: python -m pytest tests/test_tracker_isolation.py -q
     python -m unittest tests.test_tracker_isolation
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import config
import collectors

ENV_KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


class TrackerIsolationCase(unittest.TestCase):
    """Base class for tracker isolation tests."""

    def setUp(self):
        """Set up isolated temp directories for testing."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-isolation-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        (self.fixture_root / "transcripts").mkdir()

        # Save original env
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}

        # Set isolated environment
        os.environ["AESOP_ROOT"] = str(self.fixture_root)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        # Reload config to pick up new env vars
        config.reload()

    def tearDown(self):
        """Restore original env and clean up temp files."""
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.reload()
        shutil.rmtree(self.fixture_root, ignore_errors=True)


class TestTrackerIsolation(TrackerIsolationCase):
    """Tests that tracker writes are isolated to AESOP_STATE_ROOT."""

    def test_create_tracker_item_writes_only_to_temp_state(self):
        """Creating a tracker item writes ONLY to isolated state dir, never real repo."""
        # Create an item in the isolated state
        item = collectors.create_tracker_item({
            "title": "Isolation Test Item",
            "priority": "P1",
            "notes": "This should only exist in temp state"
        })

        # Assert the item was created
        self.assertIsNotNone(item)
        self.assertEqual(item["title"], "Isolation Test Item")

        # Assert the item was written to the isolated tracker.json
        isolated_tracker = self.state_dir / "tracker.json"
        self.assertTrue(isolated_tracker.exists(), "Item should be written to isolated tracker.json")
        with open(isolated_tracker, encoding='utf-8') as f:
            tracked = json.load(f)
        self.assertEqual(len(tracked["items"]), 1, "Exactly one item should exist in isolated tracker")
        self.assertEqual(tracked["items"][0]["title"], "Isolation Test Item")

    def test_real_repo_tracker_never_modified(self):
        """Real repo's state/tracker.json is never touched or opened during isolated operations."""
        real_repo_state = Path.home() / "aesop" / "state"
        real_tracker = real_repo_state / "tracker.json"

        # Record initial state of real tracker (if it exists)
        real_tracker_existed = real_tracker.exists()
        real_tracker_mtime_before = real_tracker.stat().st_mtime if real_tracker_existed else None

        # Create an item in isolated state
        collectors.create_tracker_item({
            "title": "Should Not Leak",
            "priority": "P0"
        })

        # Assert real repo's tracker.json was not modified
        if real_tracker_existed:
            real_tracker_mtime_after = real_tracker.stat().st_mtime
            self.assertEqual(
                real_tracker_mtime_before,
                real_tracker_mtime_after,
                "Real repo's tracker.json should not be modified by isolated operations"
            )
        # If it didn't exist before, it definitely shouldn't exist now
        self.assertEqual(
            real_tracker.exists(),
            real_tracker_existed,
            "Real repo's tracker.json creation/deletion status should not change"
        )

    def test_config_state_dir_points_to_isolation_temp(self):
        """config.STATE_DIR is correctly pointing to the isolated temp directory."""
        self.assertTrue(
            str(config.STATE_DIR).startswith(str(self.fixture_root)),
            f"STATE_DIR {config.STATE_DIR} should point to isolated temp {self.fixture_root}"
        )
        self.assertNotEqual(
            config.STATE_DIR,
            Path.home() / "aesop" / "state",
            "STATE_DIR should not be the real repo's state dir"
        )

    def test_tracker_file_path_is_isolated(self):
        """config.TRACKER_FILE points to the isolated state directory."""
        self.assertTrue(
            str(config.TRACKER_FILE).startswith(str(self.fixture_root)),
            f"TRACKER_FILE {config.TRACKER_FILE} should point to isolated temp"
        )
        self.assertEqual(
            config.TRACKER_FILE.parent,
            self.state_dir,
            "TRACKER_FILE should be in the isolated state dir"
        )

    def test_multiple_items_isolated(self):
        """Multiple tracker items all write to isolated state, not real repo."""
        items = []
        for i in range(3):
            item = collectors.create_tracker_item({
                "title": f"Isolation Test Item {i}",
                "priority": "P1"
            })
            items.append(item)

        # Verify all were written to isolated tracker
        isolated_tracker = self.state_dir / "tracker.json"
        self.assertTrue(isolated_tracker.exists())
        with open(isolated_tracker, encoding='utf-8') as f:
            tracked = json.load(f)
        self.assertEqual(len(tracked["items"]), 3, "All three items should be in isolated tracker")

        # Verify they don't exist in real repo
        real_tracker = Path.home() / "aesop" / "state" / "tracker.json"
        if real_tracker.exists():
            with open(real_tracker, encoding='utf-8') as f:
                real_tracked = json.load(f)
            titles_in_real = [t.get("title") for t in real_tracked.get("items", [])]
            for item in items:
                self.assertNotIn(
                    item["title"],
                    titles_in_real,
                    f"Item '{item['title']}' should not exist in real repo tracker"
                )

    def test_verify_dash_would_not_pollute_state(self):
        """Simulate verify_dash.py behavior: ensure temp state is isolated."""
        # This simulates what verify_dash.py does
        temp_root = Path(tempfile.mkdtemp(prefix="aesop-verify-dash-sim-"))
        temp_state = temp_root / "state"
        temp_state.mkdir()

        # Set env to temp state
        saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_ROOT"] = str(temp_root)
        os.environ["AESOP_STATE_ROOT"] = str(temp_state)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(temp_root / "transcripts")

        try:
            # Reload config with new env
            config.reload()

            # Create an item (simulating POST /api/tracker in the test)
            item = collectors.create_tracker_item({
                "title": "verify_dash simulated item",
                "priority": "P1"
            })

            # Verify it went to temp_state only
            temp_tracker = temp_state / "tracker.json"
            self.assertTrue(temp_tracker.exists())
            with open(temp_tracker, encoding='utf-8') as f:
                tracked = json.load(f)
            self.assertEqual(len(tracked["items"]), 1)
            self.assertEqual(tracked["items"][0]["title"], "verify_dash simulated item")

            # Verify it didn't touch the real repo
            real_tracker = Path.home() / "aesop" / "state" / "tracker.json"
            if real_tracker.exists():
                with open(real_tracker, encoding='utf-8') as f:
                    real_tracked = json.load(f)
                real_titles = [t.get("title") for t in real_tracked.get("items", [])]
                self.assertNotIn("verify_dash simulated item", real_titles)

        finally:
            # Restore env
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            config.reload()
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
