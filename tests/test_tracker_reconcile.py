#!/usr/bin/env python3
"""Tests for tracker_reconcile.py — zombie detection and reconciliation."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_zombie_detected_for_todo_and_in_progress_statuses(self):
        """Items with status 'todo' or 'in-progress' and a merged PR are zombies."""
        for status in ("todo", "in-progress"):
            with self.subTest(status=status):
                self._write_tracker([
                    {"id": "s1", "title": "Status item", "status": status,
                     "notes": "PR #300", "pr_link": None},
                ])
                with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
                    with patch("tools.tracker_reconcile._check_pr_merged", return_value=True):
                        with patch("tools.tracker_reconcile._check_git_evidence", return_value=None):
                            zombies, genuinely = tracker_reconcile.reconcile(
                                str(self.state_dir), str(Path(__file__).parent.parent)
                            )
                self.assertEqual([z["id"] for z in zombies], ["s1"])
                self.assertEqual(genuinely, [])

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

    # --- --fix close failure must never print FIXED (P0 wrong data) ---

    def _fix_with_failing_close(self, argv):
        """Run main(argv) with one zombie whose StateAPI append raises."""
        import io

        class _BrokenAPI:
            def __init__(self, db_path):
                pass

            def append(self, *args, **kwargs):
                raise RuntimeError("simulated append failure")

            def close(self):
                pass

        self._write_tracker([
            {"id": "cf1", "title": "Close fails", "status": "in_progress",
             "notes": "PR #600", "pr_link": None},
        ])
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            with patch("tools.tracker_reconcile._check_pr_merged", return_value=True):
                with patch("tools.tracker_reconcile._check_git_evidence", return_value=None):
                    with patch("state_store.api.StateAPI", _BrokenAPI):
                        with patch("sys.stdout", out), patch("sys.stderr", err):
                            rc = tracker_reconcile.main(argv)
        return rc, out.getvalue()

    def test_fix_close_failure_exits_nonzero(self):
        """--fix exits nonzero when the close write raises."""
        rc, _ = self._fix_with_failing_close(["--fix"])
        self.assertNotEqual(rc, 0)

    def test_fix_close_failure_prints_no_fixed_line(self):
        """--fix never prints a FIXED line for an item whose close failed."""
        _, stdout = self._fix_with_failing_close(["--fix"])
        fixed_lines = [ln for ln in stdout.splitlines() if ln.startswith("FIXED")]
        self.assertEqual(fixed_lines, [])

    def test_fix_close_failure_json_not_fixed(self):
        """--fix --json reports fixed=false when the close write raises."""
        _, stdout = self._fix_with_failing_close(["--fix", "--json"])
        data = json.loads(stdout)
        self.assertFalse(data["fixed"])
        self.assertEqual(len(data["zombies"]), 1)
        self.assertFalse(data["zombies"][0]["closed"])

    def test_fix_close_success_prints_fixed_line(self):
        """Control: a successful close still prints FIXED and exits 0."""
        import io

        class _RecordingAPI:
            calls = []

            def __init__(self, db_path):
                pass

            def append(self, *args, **kwargs):
                _RecordingAPI.calls.append(args)

            def close(self):
                pass

        self._write_tracker([
            {"id": "ok1", "title": "Close ok", "status": "in_progress",
             "notes": "PR #601", "pr_link": None},
        ])
        out = io.StringIO()
        with patch.dict(os.environ, {"AESOP_STATE_ROOT": str(self.state_dir)}):
            with patch("tools.tracker_reconcile._check_pr_merged", return_value=True):
                with patch("tools.tracker_reconcile._check_git_evidence", return_value=None):
                    with patch("state_store.api.StateAPI", _RecordingAPI):
                        with patch("sys.stdout", out):
                            rc = tracker_reconcile.main(["--fix"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(_RecordingAPI.calls), 1)
        self.assertTrue(any(ln.startswith("FIXED ok1") for ln in out.getvalue().splitlines()))

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
