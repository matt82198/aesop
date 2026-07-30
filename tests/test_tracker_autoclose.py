#!/usr/bin/env python3
"""Tests for tracker_autoclose.py — automatic tracker zombie prevention.

Guardrail G1: Auto-close tracked items when linked PRs merge or files ship.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import tracker_autoclose


class TestTrackerAutoclose(unittest.TestCase):
    """Test suite for tracker auto-close functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)

    def tearDown(self):
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    def _write_tracker(self, items):
        """Write tracker.json fixture."""
        tracker_path = self.state_dir / "tracker.json"
        tracker_data = {"version": 1, "items": items}
        tracker_path.write_text(json.dumps(tracker_data, indent=2), encoding="utf-8")
        return tracker_path

    def test_autoclose_merged_pr(self):
        """Test auto-close when PR is MERGED."""
        # Arrange
        items = [
            {
                "id": "item1",
                "title": "Feature X",
                "status": "in_progress",
                "lane": "proposed",
                "priority": "P1",
                "notes": "PR: #123",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)

        # Mock gh pr view to return MERGED
        with patch(
            "tools.tracker_autoclose.subprocess.run"
        ) as mock_run:
            mock_proc = Mock()
            mock_proc.returncode = 0
            mock_proc.stdout = "MERGED"  # gh jq returns just the state value
            mock_run.return_value = mock_proc

            # Act
            rc = tracker_autoclose.main(["--check"], state_root=str(self.state_dir))

            # Assert
            self.assertEqual(rc, 0)
            tracker_path = self.state_dir / "tracker.json"
            updated_data = json.loads(tracker_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_data["items"][0]["status"], "done")
            self.assertIn("RECONCILED", updated_data["items"][0]["notes"])
            self.assertIn("#123", updated_data["items"][0]["notes"])

    def test_dry_run_doesnt_modify(self):
        """Test --dry-run doesn't modify tracker."""
        # Arrange
        items = [
            {
                "id": "item1",
                "title": "Feature X",
                "status": "in_progress",
                "lane": "proposed",
                "priority": "P1",
                "notes": "PR: #123",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)
        original_data = json.loads(
            (self.state_dir / "tracker.json").read_text(encoding="utf-8")
        )

        # Mock gh pr view to return MERGED
        with patch(
            "tools.tracker_autoclose.subprocess.run"
        ) as mock_run:
            mock_proc = Mock()
            mock_proc.returncode = 0
            mock_proc.stdout = "MERGED"
            mock_run.return_value = mock_proc

            # Act
            rc = tracker_autoclose.main(["--dry-run"], state_root=str(self.state_dir))

            # Assert
            self.assertEqual(rc, 0)
            tracker_path = self.state_dir / "tracker.json"
            updated_data = json.loads(tracker_path.read_text(encoding="utf-8"))
            # File should NOT be modified (still has status in_progress)
            self.assertEqual(updated_data, original_data)

    def test_check_exits_1_on_unresolved(self):
        """Test --check exits 1 when items have unmerged PRs."""
        # Arrange
        items = [
            {
                "id": "item1",
                "title": "Feature X",
                "status": "in_progress",
                "lane": "proposed",
                "priority": "P1",
                "notes": "PR: #123",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)

        # Mock gh pr view to return OPEN
        with patch(
            "tracker_autoclose.subprocess.run"
        ) as mock_run:
            mock_proc = Mock()
            mock_proc.returncode = 0
            mock_proc.stdout = "OPEN"
            mock_run.return_value = mock_proc

            # Act
            rc = tracker_autoclose.main(["--check"], state_root=str(self.state_dir))

            # Assert
            self.assertEqual(rc, 1)

    def test_check_exits_0_when_clean(self):
        """Test --check exits 0 when all items are resolved."""
        # Arrange
        items = [
            {
                "id": "item1",
                "title": "Feature X",
                "status": "done",
                "lane": "done",
                "priority": "P1",
                "notes": "RECONCILED: PR #123 merged",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": "2026-07-29T01:00:00Z",
            }
        ]
        self._write_tracker(items)

        # Act
        rc = tracker_autoclose.main(["--check"], state_root=str(self.state_dir))

        # Assert
        self.assertEqual(rc, 0)

    def test_skip_already_done_items(self):
        """Test that already-done items are skipped."""
        # Arrange
        items = [
            {
                "id": "item1",
                "title": "Feature X",
                "status": "done",
                "lane": "done",
                "priority": "P1",
                "notes": "Already complete",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": "2026-07-29T01:00:00Z",
            }
        ]
        self._write_tracker(items)

        # Act
        rc = tracker_autoclose.main(["--check"], state_root=str(self.state_dir))

        # Assert
        self.assertEqual(rc, 0)
        # Verify item was not modified
        tracker_path = self.state_dir / "tracker.json"
        updated_data = json.loads(tracker_path.read_text(encoding="utf-8"))
        self.assertEqual(updated_data["items"][0]["status"], "done")
        self.assertEqual(updated_data["items"][0]["notes"], "Already complete")

    def test_extract_pr_from_notes(self):
        """Test PR number extraction from notes field."""
        # Arrange
        items = [
            {
                "id": "item1",
                "title": "Fix bug",
                "status": "ranked",
                "lane": "ranked",
                "priority": "P2",
                "notes": "See PR #456 for details",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)

        # Mock gh pr view to return MERGED
        with patch(
            "tools.tracker_autoclose.subprocess.run"
        ) as mock_run:
            mock_proc = Mock()
            mock_proc.returncode = 0
            mock_proc.stdout = "MERGED"
            mock_run.return_value = mock_proc

            # Act
            rc = tracker_autoclose.main(["--check"], state_root=str(self.state_dir))

            # Assert
            self.assertEqual(rc, 0)
            # Verify that gh was called with PR 456
            calls = [str(call) for call in mock_run.call_args_list]
            self.assertTrue(
                any("456" in str(call) for call in calls),
                f"Expected PR 456 to be queried. Calls: {calls}",
            )

    def test_skip_items_without_pr_reference(self):
        """Test that items without PR references are skipped."""
        # Arrange
        items = [
            {
                "id": "item1",
                "title": "Research task",
                "status": "in_progress",
                "lane": "proposed",
                "priority": "P3",
                "notes": "No PR yet",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)

        # Act
        rc = tracker_autoclose.main(["--check"], state_root=str(self.state_dir))

        # Assert
        # Should exit with 1 (unresolved item)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
