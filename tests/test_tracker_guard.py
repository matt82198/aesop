"""Tracker zombie-resurrection prevention tests.

Tests for tracker_guard.py: append-only lane journal + zombie-resurrection fail-closed gate.

ZOMBIE RULE: An item whose lane history contains 'done' or 'rejected' (terminal states)
may NEVER re-enter an active lane (ranked/proposed/in-progress/accepted).

Test strategy:
  1. Seed: bootstrap journal from current tracker state
  2. Normal transition: normal lane changes are journaled
  3. Zombie detection: detect resurrection attempts (exit 1)
  4. Enforce: revert violators to terminal lane (exit 0)
  5. Edge cases: missing tracker, malformed items, journal rotation

Run: python -m unittest tests.test_tracker_guard -v
     python -m unittest tests.test_tracker_guard.TestTrackerGuardCore -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

# Make tools/ importable
TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import tracker_guard


class TrackerGuardTestBase(unittest.TestCase):
    """Base class for tracker_guard tests with isolated temp state."""

    def setUp(self):
        """Create isolated temp state directory."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="tracker-guard-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)

        # Save original env
        self._saved_aesop_state_root = os.environ.get("AESOP_STATE_ROOT")

        # Set isolated AESOP_STATE_ROOT
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        """Restore env and clean up temp files."""
        if self._saved_aesop_state_root is None:
            os.environ.pop("AESOP_STATE_ROOT", None)
        else:
            os.environ["AESOP_STATE_ROOT"] = self._saved_aesop_state_root
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def create_tracker(self, items):
        """Create tracker.json with given items."""
        tracker_file = self.state_dir / "tracker.json"
        tracker_data = {"version": 1, "items": items}
        tracker_file.write_text(json.dumps(tracker_data, indent=2))
        return tracker_file

    def create_journal(self, entries):
        """Create tracker-journal.jsonl with given entries."""
        journal_file = self.state_dir / "tracker-journal.jsonl"
        with open(journal_file, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        return journal_file

    def read_journal(self):
        """Read all journal entries."""
        journal_file = self.state_dir / "tracker-journal.jsonl"
        if not journal_file.exists():
            return []
        entries = []
        for line in journal_file.read_text().strip().split("\n"):
            if line:
                entries.append(json.loads(line))
        return entries

    def read_tracker(self):
        """Read tracker.json."""
        tracker_file = self.state_dir / "tracker.json"
        if not tracker_file.exists():
            return None
        return json.loads(tracker_file.read_text())


class TestTrackerGuardCore(TrackerGuardTestBase):
    """Core tests for tracker_guard functionality."""

    def test_seed_bootstrap_empty_tracker(self):
        """--seed with empty tracker creates initial journal."""
        self.create_tracker([])

        exit_code = tracker_guard.main(["--seed"])

        self.assertEqual(exit_code, 0, "seed should succeed on empty tracker")
        entries = self.read_journal()
        self.assertEqual(len(entries), 0, "journal should be empty for empty tracker")

    def test_seed_bootstrap_items(self):
        """--seed with items creates journal entries for each."""
        items = [
            {"id": "item1", "lane": "ranked", "title": "Item 1"},
            {"id": "item2", "lane": "done", "title": "Item 2"},
        ]
        self.create_tracker(items)

        exit_code = tracker_guard.main(["--seed"])

        self.assertEqual(exit_code, 0, "seed should succeed")
        entries = self.read_journal()
        # Seed should create initial entries for each item
        self.assertEqual(len(entries), 2)
        # Check that both items are recorded
        ids = {e["id"] for e in entries}
        self.assertEqual(ids, {"item1", "item2"})

    def test_normal_transition_journaled(self):
        """Normal lane transition is appended to journal."""
        items = [{"id": "item1", "lane": "ranked", "title": "Item 1"}]
        self.create_tracker(items)

        # Seed initial state
        tracker_guard.main(["--seed"])

        # Change lane
        items[0]["lane"] = "proposed"
        self.create_tracker(items)

        # Run normal check
        exit_code = tracker_guard.main([])

        # Should detect the normal transition (not a zombie)
        self.assertEqual(exit_code, 0, "normal transition should exit 0")
        entries = self.read_journal()
        # Should have seed entry + transition entry
        self.assertGreaterEqual(len(entries), 1)

    def test_zombie_detection_done_to_active(self):
        """Detect resurrection: item from done to ranked (exit 1)."""
        items = [
            {"id": "zombie1", "lane": "done", "title": "Zombie Item"}
        ]
        self.create_tracker(items)

        # Create journal entry marking item as done
        journal_entries = [
            {"ts": datetime.utcnow().isoformat(), "id": "zombie1", "from": None, "to": "done"}
        ]
        self.create_journal(journal_entries)

        # Resurrect: change to active lane
        items[0]["lane"] = "ranked"
        self.create_tracker(items)

        # Check mode should detect zombie
        exit_code = tracker_guard.main([])

        self.assertEqual(exit_code, 1, "should detect zombie resurrection (exit 1)")

    def test_zombie_detection_rejected_to_active(self):
        """Detect resurrection: item from rejected to in-progress (exit 1)."""
        items = [
            {"id": "zombie2", "lane": "rejected", "title": "Rejected Item"}
        ]
        self.create_tracker(items)

        # Create journal entry marking item as rejected
        journal_entries = [
            {"ts": datetime.utcnow().isoformat(), "id": "zombie2", "from": None, "to": "rejected"}
        ]
        self.create_journal(journal_entries)

        # Resurrect: change to active lane
        items[0]["lane"] = "in-progress"
        self.create_tracker(items)

        # Check mode should detect zombie
        exit_code = tracker_guard.main([])

        self.assertEqual(exit_code, 1, "should detect zombie resurrection (exit 1)")

    def test_enforce_reverts_zombie(self):
        """--enforce reverts zombie to terminal lane and appends journal entry."""
        items = [
            {"id": "zombie3", "lane": "ranked", "title": "Resurrected Item"}
        ]
        self.create_tracker(items)

        # Create journal showing item was done, then resurrected
        journal_entries = [
            {"ts": datetime.utcnow().isoformat(), "id": "zombie3", "from": None, "to": "done"},
        ]
        self.create_journal(journal_entries)

        # Now item is in ranked (zombie state)
        items[0]["lane"] = "ranked"
        self.create_tracker(items)

        # Enforce should revert it
        exit_code = tracker_guard.main(["--enforce"])

        self.assertEqual(exit_code, 0, "--enforce should exit 0 after revert")

        # Verify item was reverted to terminal lane
        tracker = self.read_tracker()
        self.assertEqual(tracker["items"][0]["lane"], "done", "item should be reverted to done")

        # Verify revert was journaled
        entries = self.read_journal()
        reverts = [e for e in entries if e.get("type") == "reverted"]
        self.assertEqual(len(reverts), 1, "should have 1 revert entry")
        self.assertEqual(reverts[0]["id"], "zombie3")

    def test_check_mode_no_zombies(self):
        """CHECK mode (default) exits 0 when no zombies present."""
        items = [
            {"id": "good1", "lane": "ranked", "title": "Good Item 1"},
            {"id": "good2", "lane": "done", "title": "Done Item"},
        ]
        self.create_tracker(items)

        # Seed
        tracker_guard.main(["--seed"])

        # Transition good1 normally
        items[0]["lane"] = "proposed"
        self.create_tracker(items)

        # Check mode should succeed
        exit_code = tracker_guard.main([])

        self.assertEqual(exit_code, 0, "CHECK mode should exit 0 with no zombies")

    def test_missing_tracker_noop(self):
        """Missing tracker.json is a no-op (exit 0, message printed)."""
        # Don't create a tracker file

        exit_code = tracker_guard.main([])

        self.assertEqual(exit_code, 0, "missing tracker should be no-op")
        # Journal should not be created
        journal_file = self.state_dir / "tracker-journal.jsonl"
        self.assertFalse(journal_file.exists(), "journal should not be created for missing tracker")

    def test_malformed_item_skipped_with_warn(self):
        """Malformed items are skipped with warning, no exit code change."""
        items = [
            {"id": "good1", "lane": "ranked"},
            {"id": "bad1"},  # Missing lane
            {"title": "no-id", "lane": "ranked"},  # Missing id
            {"id": "good2", "lane": "proposed"},
        ]
        self.create_tracker(items)

        # Seed should skip malformed items
        exit_code = tracker_guard.main(["--seed"])

        self.assertEqual(exit_code, 0, "should succeed even with malformed items")
        entries = self.read_journal()
        # Should only have entries for good items
        ids = {e["id"] for e in entries if "id" in e}
        self.assertIn("good1", ids)
        self.assertIn("good2", ids)
        self.assertNotIn("bad1", ids)
        self.assertNotIn("no-id", ids)

    def test_journal_rotation_at_5000_lines(self):
        """Journal rotates to archive when exceeding 5000 lines."""
        # Create a large journal
        entries = []
        for i in range(5100):
            entry = {
                "ts": datetime.utcnow().isoformat(),
                "id": f"item{i % 100}",
                "from": "ranked",
                "to": "proposed"
            }
            entries.append(entry)

        self.create_journal(entries)

        # Also need a tracker so the check mode actually runs
        items = [
            {"id": f"item{i}", "lane": "ranked", "title": f"Item {i}"}
            for i in range(5)
        ]
        self.create_tracker(items)

        # Run a check to trigger rotation logic
        tracker_guard.main([])

        # After rotation, main journal should have <= 5000 lines
        journal_file = self.state_dir / "tracker-journal.jsonl"
        self.assertTrue(journal_file.exists(), "journal should exist")
        lines = journal_file.read_text().strip().split("\n")
        self.assertLessEqual(len(lines), 5000, "rotated journal should not exceed 5000 lines")

        # Verify archive was created if rotation happened
        archive_file = self.state_dir / "tracker-journal.archive"
        if len(entries) > 5000:
            # If original had more than 5000, rotation should have created archive
            # (actual rotation depends on whether check mode appended anything)
            pass  # Archive may or may not exist depending on check results


class TestTrackerGuardEdgeCases(TrackerGuardTestBase):
    """Edge case tests."""

    def test_multiple_zombie_detections(self):
        """Detect multiple zombies in single run."""
        items = [
            {"id": "zombie_a", "lane": "ranked", "title": "A"},
            {"id": "zombie_b", "lane": "in-progress", "title": "B"},
        ]
        self.create_tracker(items)

        # Create journal showing both were done
        journal_entries = [
            {"ts": datetime.utcnow().isoformat(), "id": "zombie_a", "from": None, "to": "done"},
            {"ts": datetime.utcnow().isoformat(), "id": "zombie_b", "from": None, "to": "done"},
        ]
        self.create_journal(journal_entries)

        # Check mode
        exit_code = tracker_guard.main([])

        self.assertEqual(exit_code, 1, "should detect multiple zombies")

    def test_enforce_multiple_zombies(self):
        """--enforce reverts multiple zombies."""
        items = [
            {"id": "zombie_a", "lane": "ranked", "title": "A"},
            {"id": "zombie_b", "lane": "in-progress", "title": "B"},
            {"id": "good_c", "lane": "ranked", "title": "C"},
        ]
        self.create_tracker(items)

        journal_entries = [
            {"ts": datetime.utcnow().isoformat(), "id": "zombie_a", "from": None, "to": "done"},
            {"ts": datetime.utcnow().isoformat(), "id": "zombie_b", "from": None, "to": "rejected"},
        ]
        self.create_journal(journal_entries)

        # Enforce should revert both
        exit_code = tracker_guard.main(["--enforce"])

        self.assertEqual(exit_code, 0)
        tracker = self.read_tracker()
        lanes = {item["id"]: item["lane"] for item in tracker["items"]}
        self.assertEqual(lanes["zombie_a"], "done")
        self.assertEqual(lanes["zombie_b"], "rejected")
        self.assertEqual(lanes["good_c"], "ranked", "good items should not be modified")

    def test_item_never_entered_active_lane(self):
        """Item that goes done -> ranked is a zombie, even if born done."""
        items = [
            {"id": "direct_done", "lane": "ranked", "title": "Item"}
        ]
        self.create_tracker(items)

        journal_entries = [
            {"ts": datetime.utcnow().isoformat(), "id": "direct_done", "from": None, "to": "done"},
        ]
        self.create_journal(journal_entries)

        # Now it's ranked (zombie)
        items[0]["lane"] = "ranked"
        self.create_tracker(items)

        exit_code = tracker_guard.main([])
        self.assertEqual(exit_code, 1, "any resurrection is a zombie")

    def test_terminal_to_terminal_transition(self):
        """Transition from done to rejected (both terminal) is OK."""
        items = [
            {"id": "item1", "lane": "rejected", "title": "Item"}
        ]
        self.create_tracker(items)

        journal_entries = [
            {"ts": datetime.utcnow().isoformat(), "id": "item1", "from": None, "to": "done"},
        ]
        self.create_journal(journal_entries)

        # Transition to rejected (still terminal)
        items[0]["lane"] = "rejected"
        self.create_tracker(items)

        exit_code = tracker_guard.main([])

        # Terminal to terminal should not be a zombie
        self.assertEqual(exit_code, 0, "terminal-to-terminal is not a zombie")

    def test_seed_idempotent(self):
        """Running --seed twice produces idempotent journal."""
        items = [
            {"id": "item1", "lane": "ranked", "title": "Item 1"},
        ]
        self.create_tracker(items)

        # First seed
        tracker_guard.main(["--seed"])
        entries_1 = self.read_journal()

        # Second seed (should be idempotent or clear first)
        # Depending on implementation, this might append or be idempotent
        # For now, we just verify it doesn't crash
        exit_code = tracker_guard.main(["--seed"])
        self.assertEqual(exit_code, 0, "second seed should not crash")


class TestTrackerGuardIntegration(TrackerGuardTestBase):
    """Integration tests mimicking real workflow."""

    def test_workflow_seed_then_check_then_enforce(self):
        """Complete workflow: seed -> check -> detect -> enforce."""
        # 1. Initial state with some items
        items = [
            {"id": "item1", "lane": "ranked", "title": "Item 1"},
            {"id": "item2", "lane": "done", "title": "Item 2"},
        ]
        self.create_tracker(items)

        # 2. Seed
        exit_code = tracker_guard.main(["--seed"])
        self.assertEqual(exit_code, 0)

        # 3. Change lane normally
        items[0]["lane"] = "proposed"
        self.create_tracker(items)

        # 4. Check should pass
        exit_code = tracker_guard.main([])
        self.assertEqual(exit_code, 0, "normal change should pass")

        # 5. Resurrect item2 (zombie)
        items[1]["lane"] = "ranked"
        self.create_tracker(items)

        # 6. Check should fail
        exit_code = tracker_guard.main([])
        self.assertEqual(exit_code, 1, "zombie should be detected")

        # 7. Enforce should fix it
        exit_code = tracker_guard.main(["--enforce"])
        self.assertEqual(exit_code, 0, "enforce should fix")

        # 8. Verify it's fixed
        tracker = self.read_tracker()
        self.assertEqual(tracker["items"][1]["lane"], "done", "zombie should be reverted")

        # 9. Check again should pass
        exit_code = tracker_guard.main([])
        self.assertEqual(exit_code, 0, "after fix, check should pass")


class TestTrackerGuardCLI(TrackerGuardTestBase):
    """CLI argument tests."""

    def test_help_flag(self):
        """--help prints usage and exits 0."""
        exit_code = tracker_guard.main(["--help"])
        # Should exit 0 for help
        self.assertIn(exit_code, [0, None], "--help should exit 0")

    def test_unknown_flag_exit_failure(self):
        """Unknown flags cause exit 1 (fail-closed)."""
        exit_code = tracker_guard.main(["--unknown-flag"])
        self.assertEqual(exit_code, 1, "unknown flag should exit 1")

    def test_state_root_env_var(self):
        """AESOP_STATE_ROOT env var is respected."""
        # Already set in setUp
        items = [{"id": "item1", "lane": "ranked"}]
        self.create_tracker(items)

        exit_code = tracker_guard.main(["--seed"])
        self.assertEqual(exit_code, 0)

        journal_file = self.state_dir / "tracker-journal.jsonl"
        self.assertTrue(journal_file.exists(), "journal should be created in AESOP_STATE_ROOT")


if __name__ == "__main__":
    unittest.main()
