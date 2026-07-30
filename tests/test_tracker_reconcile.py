#!/usr/bin/env python3
"""Tests for tracker_reconcile.py — zombie detection and reconciliation."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import tracker_reconcile


class TestTrackerReconcile(unittest.TestCase):
    """Test suite for tracker reconciliation tool."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_tracker(self, items):
        """Write tracker.json fixture."""
        data = {"version": 1, "items": items}
        (self.state_dir / "tracker.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    # --- PR extraction ---

    def test_extract_pr_numbers_hash(self):
        """Extract PR numbers from #NNN notation."""
        nums = tracker_reconcile._extract_pr_numbers("See #123 and #456")
        self.assertEqual(nums, ["123", "456"])

    def test_extract_pr_numbers_pr_prefix(self):
        """Extract PR numbers from 'PR NNN' notation."""
        nums = tracker_reconcile._extract_pr_numbers("Landed in PR 789")
        self.assertEqual(nums, ["789"])

    def test_extract_pr_numbers_empty(self):
        """Return empty list for text without PR refs."""
        self.assertEqual(tracker_reconcile._extract_pr_numbers("no refs here"), [])
        self.assertEqual(tracker_reconcile._extract_pr_numbers(None), [])
        self.assertEqual(tracker_reconcile._extract_pr_numbers(""), [])

    # --- No tracker data ---

    def test_no_tracker_exits_0(self):
        """Exit 0 when no tracker.json exists."""
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            rc = tracker_reconcile.main([])
        self.assertEqual(rc, 0)

    def test_empty_tracker_exits_0(self):
        """Exit 0 when tracker has no items."""
        self._write_tracker([])
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            rc = tracker_reconcile.main([])
        self.assertEqual(rc, 0)

    # --- Zombie detection via PR ---

    def test_zombie_detected_via_merged_pr(self):
        """Detect zombie when linked PR is MERGED."""
        self._write_tracker([
            {"id": "z1", "title": "Feature Z", "status": "in_progress",
             "notes": "PR #100", "pr_link": None},
        ])
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            with patch("tools.tracker_reconcile._check_pr_merged", return_value=True):
                with patch("tools.tracker_reconcile._check_git_evidence", return_value=None):
                    zombies, genuinely = tracker_reconcile.reconcile(
                        str(self.state_dir), str(Path(__file__).parent.parent)
                    )
        self.assertEqual(len(zombies), 1)
        self.assertEqual(zombies[0]["id"], "z1")
        self.assertIn("PR #100", zombies[0]["evidence"])

    def test_genuinely_open_no_evidence(self):
        """Items without evidence stay genuinely open."""
        self._write_tracker([
            {"id": "o1", "title": "Open item", "status": "ranked",
             "notes": "", "pr_link": None},
        ])
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            with patch("tools.tracker_reconcile._check_pr_merged", return_value=False):
                with patch("tools.tracker_reconcile._check_git_evidence", return_value=None):
                    zombies, genuinely = tracker_reconcile.reconcile(
                        str(self.state_dir), str(Path(__file__).parent.parent)
                    )
        self.assertEqual(len(zombies), 0)
        self.assertEqual(len(genuinely), 1)
        self.assertEqual(genuinely[0]["id"], "o1")

    # --- Zombie detection via git log ---

    def test_zombie_detected_via_git_log(self):
        """Detect zombie when item ID found in git log."""
        self._write_tracker([
            {"id": "g1", "title": "Git evidence", "status": "open",
             "notes": "", "pr_link": None},
        ])
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            with patch("tools.tracker_reconcile._check_pr_merged", return_value=False):
                with patch("tools.tracker_reconcile._check_git_evidence",
                           return_value="abc1234 implement g1"):
                    zombies, genuinely = tracker_reconcile.reconcile(
                        str(self.state_dir), str(Path(__file__).parent.parent)
                    )
        self.assertEqual(len(zombies), 1)
        self.assertIn("commit found", zombies[0]["evidence"])

    # --- Skip done items ---

    def test_done_items_skipped(self):
        """Already-done items are not flagged."""
        self._write_tracker([
            {"id": "d1", "title": "Done item", "status": "done",
             "notes": "PR #200", "pr_link": None},
        ])
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            zombies, genuinely = tracker_reconcile.reconcile(
                str(self.state_dir), str(Path(__file__).parent.parent)
            )
        self.assertEqual(len(zombies), 0)
        self.assertEqual(len(genuinely), 0)

    # --- --fix flag ---

    def test_fix_returns_0(self):
        """--fix exits 0 even when zombies found."""
        self._write_tracker([
            {"id": "f1", "title": "Fixable", "status": "in_progress",
             "notes": "PR #300", "pr_link": None},
        ])
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            with patch("tools.tracker_reconcile._check_pr_merged", return_value=True):
                with patch("tools.tracker_reconcile._check_git_evidence", return_value=None):
                    with patch("tools.tracker_reconcile._close_zombie"):
                        rc = tracker_reconcile.main(["--fix"])
        self.assertEqual(rc, 0)

    def test_no_fix_returns_1_on_zombies(self):
        """Without --fix, exits 1 when zombies found."""
        self._write_tracker([
            {"id": "nf1", "title": "Not fixed", "status": "proposed",
             "notes": "PR #400", "pr_link": None},
        ])
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            with patch("tools.tracker_reconcile._check_pr_merged", return_value=True):
                with patch("tools.tracker_reconcile._check_git_evidence", return_value=None):
                    rc = tracker_reconcile.main([])
        self.assertEqual(rc, 1)

    # --- JSON output ---

    def test_json_output(self):
        """--json produces valid JSON with expected keys."""
        self._write_tracker([
            {"id": "j1", "title": "JSON test", "status": "ranked",
             "notes": "", "pr_link": None},
        ])
        import io
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            with patch("tools.tracker_reconcile._check_pr_merged", return_value=False):
                with patch("tools.tracker_reconcile._check_git_evidence", return_value=None):
                    with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                        rc = tracker_reconcile.main(["--json"])
                        output = mock_out.getvalue()
        data = json.loads(output)
        self.assertIn("zombies", data)
        self.assertIn("genuinely_open", data)
        self.assertIn("fixed", data)
        self.assertFalse(data["fixed"])

    # --- Unknown flag ---

    def test_unknown_flag_exits_2(self):
        """Unknown flags exit with code 2."""
        rc = tracker_reconcile.main(["--bogus"])
        self.assertEqual(rc, 2)

    # --- Help ---

    def test_help_exits_0(self):
        """--help exits 0."""
        import io
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = tracker_reconcile.main(["--help"])
        self.assertEqual(rc, 0)

    # --- pr_link field ---

    def test_pr_link_field_used(self):
        """PR numbers extracted from pr_link field."""
        self._write_tracker([
            {"id": "pl1", "title": "PR link field", "status": "in_progress",
             "notes": "", "pr_link": "#555"},
        ])
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            with patch("tools.tracker_reconcile._check_pr_merged", return_value=True) as mock_pr:
                with patch("tools.tracker_reconcile._check_git_evidence", return_value=None):
                    zombies, _ = tracker_reconcile.reconcile(
                        str(self.state_dir), str(Path(__file__).parent.parent)
                    )
        self.assertEqual(len(zombies), 1)
        mock_pr.assert_called_with("555")


if __name__ == "__main__":
    unittest.main()
