#!/usr/bin/env python3
"""Tests for tracker_autoclose.py — automatic tracker zombie prevention.

Guardrail G1: Auto-close tracked items when linked PRs merge or files ship.
Tests evidence-based classification: SHIPPED, OPEN, AMBIGUOUS.
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

    def _read_journal(self):
        """Read journal entries."""
        journal_path = self.state_dir / "tracker-journal.jsonl"
        if not journal_path.exists():
            return []
        entries = []
        for line in journal_path.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                entries.append(json.loads(line))
        return entries

    # ========== ESCAPE REPRO: In-progress item with merged PR evidence -> SHIPPED ==========

    def test_escape_repro_in_progress_merged_pr(self):
        """ESCAPE REPRO: in-progress item with merged PR -> classified SHIPPED."""
        # This reproduces the zombie detection that should have caught the 3 real escapes
        items = [
            {
                "id": "358af636cdf0",  # Real zombie ID from tracker
                "title": "Escaped item",
                "status": "in_progress",
                "lane": "proposed",
                "priority": "P1",
                "notes": "Fix: PR #123",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)

        # Mock gh pr view to return MERGED
        with patch("tools.tracker_autoclose.subprocess.run") as mock_run:
            mock_proc = Mock()
            mock_proc.returncode = 0
            mock_proc.stdout = "MERGED"
            mock_run.return_value = mock_proc

            # Act: check mode should detect closable item
            rc = tracker_autoclose.main(["--check"], state_root=str(self.state_dir))

            # Assert: check mode returns 1 (closable items found)
            self.assertEqual(rc, 1, "Should detect closable item in --check mode")

    def test_escape_repro_apply_closes_zombie(self):
        """ESCAPE REPRO: --apply closes the zombie and records evidence."""
        items = [
            {
                "id": "358af636cdf0",
                "title": "Escaped item",
                "status": "in_progress",
                "lane": "proposed",
                "priority": "P1",
                "notes": "Fix: PR #123",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)

        # Mock gh pr view to return MERGED
        with patch("tools.tracker_autoclose.subprocess.run") as mock_run:
            mock_proc = Mock()
            mock_proc.returncode = 0
            mock_proc.stdout = "MERGED"
            mock_run.return_value = mock_proc

            # Act: apply mode should close the item
            rc = tracker_autoclose.main(["--apply"], state_root=str(self.state_dir))

            # Assert: apply succeeds
            self.assertEqual(rc, 0)
            # Item should be marked done
            tracker_path = self.state_dir / "tracker.json"
            updated = json.loads(tracker_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["items"][0]["status"], "done")
            # Evidence should be recorded
            self.assertIn("RECONCILED", updated["items"][0]["notes"])
            self.assertIn("#123", updated["items"][0]["notes"])
            # Journal should have entry
            journal = self._read_journal()
            self.assertGreater(len(journal), 0)
            self.assertEqual(journal[0]["id"], "358af636cdf0")
            self.assertIn("merged", journal[0]["evidence"])

    # ========== Genuinely open item (no evidence) ==========

    def test_open_item_no_evidence(self):
        """Item with no PR or file evidence -> OPEN."""
        items = [
            {
                "id": "item2",
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

        # Assert: check returns 0 (no closable items found)
        self.assertEqual(rc, 0, "Should not close item with no evidence")

    # ========== AMBIGUOUS case (partial evidence) ==========

    def test_ambiguous_partial_evidence_pr_unavailable(self):
        """Item with PR reference but gh unavailable -> AMBIGUOUS."""
        items = [
            {
                "id": "item3",
                "title": "Partial evidence item",
                "status": "in_progress",
                "lane": "proposed",
                "priority": "P2",
                "notes": "See PR #789",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)

        # Mock gh to be unavailable
        with patch("tools.tracker_autoclose.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("gh not found")

            # Act
            rc = tracker_autoclose.main(["--check"], state_root=str(self.state_dir))

            # Assert: still returns 0 (ambiguous, not closable)
            self.assertEqual(rc, 0)

    # ========== --apply records evidence in journal ==========

    def test_apply_records_evidence_in_journal(self):
        """--apply flag records evidence in tracker-journal.jsonl."""
        items = [
            {
                "id": "item4",
                "title": "Item with evidence",
                "status": "proposed",
                "lane": "proposed",
                "priority": "P1",
                "notes": "PR #456",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)

        with patch("tools.tracker_autoclose.subprocess.run") as mock_run:
            mock_proc = Mock()
            mock_proc.returncode = 0
            mock_proc.stdout = "MERGED"
            mock_run.return_value = mock_proc

            # Act
            rc = tracker_autoclose.main(["--apply"], state_root=str(self.state_dir))

            # Assert
            self.assertEqual(rc, 0)
            journal = self._read_journal()
            self.assertEqual(len(journal), 1)
            self.assertEqual(journal[0]["id"], "item4")
            self.assertEqual(journal[0]["status"], "proposed")
            self.assertIn("456", journal[0]["evidence"])

    # ========== --check never mutates ==========

    def test_check_never_modifies_tracker(self):
        """--check mode never modifies tracker.json."""
        items = [
            {
                "id": "item5",
                "title": "Should not change",
                "status": "in_progress",
                "lane": "proposed",
                "priority": "P1",
                "notes": "PR #789",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)
        original = json.loads(
            (self.state_dir / "tracker.json").read_text(encoding="utf-8")
        )

        with patch("tools.tracker_autoclose.subprocess.run") as mock_run:
            mock_proc = Mock()
            mock_proc.returncode = 0
            mock_proc.stdout = "MERGED"
            mock_run.return_value = mock_proc

            # Act
            tracker_autoclose.main(["--check"], state_root=str(self.state_dir))

            # Assert: file unchanged
            current = json.loads(
                (self.state_dir / "tracker.json").read_text(encoding="utf-8")
            )
            self.assertEqual(original, current, "--check should never modify tracker")

    # ========== gh-absent handling ==========

    def test_gh_absent_skips_pr_check(self):
        """When gh is not available, PR checks are skipped without error."""
        items = [
            {
                "id": "item6",
                "title": "Item with PR",
                "status": "in_progress",
                "lane": "proposed",
                "priority": "P2",
                "notes": "See PR #999",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            }
        ]
        self._write_tracker(items)

        # Act: skip-gh flag simulates gh unavailable
        rc = tracker_autoclose.main(["--check", "--skip-gh"], state_root=str(self.state_dir))

        # Assert: returns 0 (no error, just skipped)
        self.assertEqual(rc, 0)

    # ========== --json output ==========

    def test_json_output_format(self):
        """--json flag produces valid JSON with structured output."""
        items = [
            {
                "id": "item7",
                "title": "Item A",
                "status": "proposed",
                "lane": "proposed",
                "priority": "P1",
                "notes": "PR #111",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            },
            {
                "id": "item8",
                "title": "Item B",
                "status": "in_progress",
                "lane": "in-progress",
                "priority": "P2",
                "notes": "No PR",
                "pr_link": None,
                "created_at": "2026-07-29T00:00:00Z",
                "completed_at": None,
            },
        ]
        self._write_tracker(items)

        with patch("tools.tracker_autoclose.subprocess.run") as mock_run:
            mock_proc = Mock()
            mock_proc.returncode = 0
            mock_proc.stdout = "MERGED"
            mock_run.return_value = mock_proc

            # Capture output
            import io
            import contextlib
            f = io.StringIO()
            with contextlib.redirect_stdout(f):
                rc = tracker_autoclose.main(["--check", "--json"], state_root=str(self.state_dir))

            output = f.getvalue()
            result = json.loads(output)

            # Assert: result has expected structure
            self.assertIn("shipped", result)
            self.assertIn("open", result)
            self.assertIn("ambiguous", result)
            self.assertEqual(result["shipped"], 1)  # item7 with PR
            self.assertEqual(result["open"], 1)      # item8 without PR

    # ========== Already done items are skipped ==========

    def test_skip_already_done_items(self):
        """Items already in 'done' status are skipped."""
        items = [
            {
                "id": "item9",
                "title": "Already done",
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
        tracker_path = self.state_dir / "tracker.json"
        updated = json.loads(tracker_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["items"][0]["notes"], "Already complete")

    # ========== Error handling ==========

    def test_unknown_flag_returns_error(self):
        """Unknown flags return exit code 2."""
        items = [{"id": "item10", "title": "Test", "status": "done", "lane": "done"}]
        self._write_tracker(items)

        # Act
        rc = tracker_autoclose.main(["--unknown-flag"], state_root=str(self.state_dir))

        # Assert
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
