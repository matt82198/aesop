#!/usr/bin/env python3
"""Tests for tools/merge_telemetry.py.

Fixture-based (no network); tests: fix-round derivation, idempotent re-append,
fail-closed on gh unavailable, JSON output shape.
"""

import json
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

# Add tools to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

# Mock common.get_state_dir before importing merge_telemetry
with patch('common.get_state_dir') as mock_get_state:
    temp_state = Path(tempfile.mkdtemp())
    mock_get_state.return_value = temp_state
    import merge_telemetry


class TestFixRoundDerivation(unittest.TestCase):
    """Test fix-round derivation from CI runs."""

    def test_no_runs(self):
        """Empty runs list yields 0 fix rounds."""
        self.assertEqual(merge_telemetry.derive_fix_rounds([]), 0)

    def test_single_run_no_fix_rounds(self):
        """One run = 0 fix rounds."""
        runs = [
            {"headSha": "abc123", "status": "COMPLETED"}
        ]
        self.assertEqual(merge_telemetry.derive_fix_rounds(runs), 0)

    def test_two_different_shas_one_fix_round(self):
        """Two distinct SHAs = 1 fix round."""
        runs = [
            {"headSha": "abc123", "status": "COMPLETED"},
            {"headSha": "def456", "status": "COMPLETED"},
        ]
        self.assertEqual(merge_telemetry.derive_fix_rounds(runs), 1)

    def test_three_distinct_shas_two_fix_rounds(self):
        """Three distinct SHAs = 2 fix rounds."""
        runs = [
            {"headSha": "abc123", "status": "COMPLETED"},
            {"headSha": "abc123", "status": "COMPLETED"},  # same SHA
            {"headSha": "def456", "status": "COMPLETED"},
            {"headSha": "ghi789", "status": "COMPLETED"},
        ]
        self.assertEqual(merge_telemetry.derive_fix_rounds(runs), 2)

    def test_runs_with_missing_sha(self):
        """Runs with missing headSha are skipped."""
        runs = [
            {"headSha": "abc123", "status": "COMPLETED"},
            {"status": "COMPLETED"},  # missing headSha
            {"headSha": "def456", "status": "COMPLETED"},
        ]
        self.assertEqual(merge_telemetry.derive_fix_rounds(runs), 1)


class TestIdempotentAppend(unittest.TestCase):
    """Test idempotent ledger append (no duplicates on re-run)."""

    def setUp(self):
        """Create temp ledger for each test."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.ledger_path = self.temp_dir / "merge-telemetry.jsonl"

    def test_append_new_row(self):
        """Append new row to empty ledger."""
        rows = [
            {
                "pr_number": 100,
                "ci_attempts": 2,
                "fix_rounds": 1,
            }
        ]
        merge_telemetry.append_to_ledger(self.ledger_path, rows)

        # Check file created
        self.assertTrue(self.ledger_path.exists())

        # Check content
        content = self.ledger_path.read_text(encoding='utf-8').strip()
        lines = content.split('\n')
        self.assertEqual(len(lines), 1)

        obj = json.loads(lines[0])
        self.assertEqual(obj["pr_number"], 100)

    def test_idempotent_re_append(self):
        """Re-appending same PR number skips the duplicate."""
        row1 = {"pr_number": 100, "ci_attempts": 2}
        row2 = {"pr_number": 101, "ci_attempts": 3}

        # First append
        merge_telemetry.append_to_ledger(self.ledger_path, [row1, row2])

        # Second append: try to add row1 again plus a new row3
        row3 = {"pr_number": 102, "ci_attempts": 1}
        merge_telemetry.append_to_ledger(self.ledger_path, [row1, row3])

        # Check ledger has 3 unique rows (row1 not duplicated)
        content = self.ledger_path.read_text(encoding='utf-8').strip()
        lines = [l for l in content.split('\n') if l.strip()]
        self.assertEqual(len(lines), 3)

        pr_numbers = [json.loads(l)["pr_number"] for l in lines]
        self.assertIn(100, pr_numbers)
        self.assertIn(101, pr_numbers)
        self.assertIn(102, pr_numbers)
        self.assertEqual(pr_numbers.count(100), 1)  # No duplicate

    def test_load_nonexistent_ledger(self):
        """Loading nonexistent ledger returns empty dict."""
        result = merge_telemetry.load_ledger(self.temp_dir / "nonexistent.jsonl")
        self.assertEqual(result, {})


class TestComputeDerivedMetrics(unittest.TestCase):
    """Test derived metric calculations."""

    def test_empty_rows(self):
        """Empty rows yield zero metrics."""
        metrics = merge_telemetry.compute_derived_metrics([])
        self.assertEqual(metrics["pr_count"], 0)
        self.assertEqual(metrics["ci_runs_per_merged_pr"], 0)
        self.assertEqual(metrics["fix_rounds_per_pr"], 0)
        self.assertEqual(metrics["red_rate"], 0.0)

    def test_single_pr_metrics(self):
        """Single PR metrics computed correctly."""
        rows = [
            {
                "time_to_merge_sec": 300,
                "ci_attempts": 2,
                "fix_rounds": 1,
                "contended_file": False,
                "red_flag": False,
            }
        ]
        metrics = merge_telemetry.compute_derived_metrics(rows)
        self.assertEqual(metrics["pr_count"], 1)
        self.assertEqual(metrics["ci_runs_per_merged_pr"], 2.0)
        self.assertEqual(metrics["fix_rounds_per_pr"], 1.0)
        self.assertEqual(metrics["median_time_to_merge_sec"], 300)
        self.assertEqual(metrics["red_rate"], 0.0)
        self.assertEqual(metrics["contended_touch_rate"], 0.0)

    def test_contended_touch_rate(self):
        """Contended-file touch rate computed as fraction."""
        rows = [
            {"contended_file": True, "ci_attempts": 1, "fix_rounds": 0, "red_flag": False},
            {"contended_file": False, "ci_attempts": 1, "fix_rounds": 0, "red_flag": False},
            {"contended_file": True, "ci_attempts": 1, "fix_rounds": 0, "red_flag": False},
        ]
        metrics = merge_telemetry.compute_derived_metrics(rows)
        self.assertAlmostEqual(metrics["contended_touch_rate"], 2/3)

    def test_red_rate(self):
        """Red rate (failures) computed as fraction."""
        rows = [
            {"red_flag": True, "ci_attempts": 1, "fix_rounds": 0},
            {"red_flag": False, "ci_attempts": 1, "fix_rounds": 0},
            {"red_flag": False, "ci_attempts": 1, "fix_rounds": 0},
        ]
        metrics = merge_telemetry.compute_derived_metrics(rows)
        self.assertAlmostEqual(metrics["red_rate"], 1/3)

    def test_median_ttm(self):
        """Median time-to-merge is correct."""
        rows = [
            {"time_to_merge_sec": 100, "ci_attempts": 1, "fix_rounds": 0},
            {"time_to_merge_sec": 200, "ci_attempts": 1, "fix_rounds": 0},
            {"time_to_merge_sec": 300, "ci_attempts": 1, "fix_rounds": 0},
        ]
        metrics = merge_telemetry.compute_derived_metrics(rows)
        self.assertEqual(metrics["median_time_to_merge_sec"], 200)


class TestTimeToMerge(unittest.TestCase):
    """Test time-to-merge calculation."""

    def test_valid_iso_timestamps(self):
        """Valid ISO timestamps compute correct delta."""
        created = "2026-08-01T10:00:00Z"
        merged = "2026-08-01T10:05:00Z"
        ttm = merge_telemetry.compute_time_to_merge(created, merged)
        self.assertEqual(ttm, 300)  # 5 minutes

    def test_invalid_created_timestamp(self):
        """Invalid created timestamp returns 0."""
        created = "not-a-date"
        merged = "2026-08-01T10:05:00Z"
        ttm = merge_telemetry.compute_time_to_merge(created, merged)
        self.assertEqual(ttm, 0)

    def test_merged_before_created_returns_zero(self):
        """Merged before created (shouldn't happen) returns max(0, delta)."""
        created = "2026-08-01T10:05:00Z"
        merged = "2026-08-01T10:00:00Z"
        ttm = merge_telemetry.compute_time_to_merge(created, merged)
        self.assertEqual(ttm, 0)


class TestTelemetryForPR(unittest.TestCase):
    """Test single-PR telemetry computation."""

    def test_missing_pr_number_returns_none(self):
        """PR without number returns None."""
        pr = {"title": "test", "mergedAt": "2026-08-01T10:00:00Z"}
        result = merge_telemetry.telemetry_for_pr(pr)
        self.assertIsNone(result)

    def test_missing_merged_at_returns_none(self):
        """PR without mergedAt returns None."""
        pr = {"number": 100, "title": "test"}
        result = merge_telemetry.telemetry_for_pr(pr)
        self.assertIsNone(result)

    @patch('merge_telemetry.get_pr_runs')
    @patch('merge_telemetry.get_merge_commits_on_head')
    @patch('merge_telemetry.check_contended_files')
    def test_valid_pr_data(self, mock_contended, mock_merge_commits, mock_runs):
        """Valid PR data yields complete telemetry row."""
        mock_runs.return_value = [
            {"headSha": "abc123", "conclusion": "success"},
            {"headSha": "def456", "conclusion": "success"},
        ]
        mock_merge_commits.return_value = 2
        mock_contended.return_value = False

        pr = {
            "number": 100,
            "title": "Test PR",
            "createdAt": "2026-08-01T10:00:00Z",
            "mergedAt": "2026-08-01T10:05:00Z",
            "headRefName": "test-branch",
            "headRefOid": "abc123",
            "baseRefOid": "def456",
        }

        result = merge_telemetry.telemetry_for_pr(pr)
        self.assertIsNotNone(result)
        self.assertEqual(result["pr_number"], 100)
        self.assertEqual(result["ci_attempts"], 2)
        self.assertEqual(result["fix_rounds"], 1)  # 2 distinct SHAs - 1
        self.assertEqual(result["update_branch_amplification"], 2)
        self.assertFalse(result["contended_file"])


class TestFailClosed(unittest.TestCase):
    """Test fail-closed behavior when gh is unavailable."""

    @patch('merge_telemetry.subprocess.run')
    def test_gh_unavailable_returns_error_dict(self, mock_run):
        """When gh command not found, returns error dict."""
        mock_run.side_effect = FileNotFoundError()
        result = merge_telemetry.gh("pr", "list")
        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertEqual(result.get("rc"), 2)

    @patch('merge_telemetry.subprocess.run')
    def test_gh_timeout_returns_error_dict(self, mock_run):
        """When gh times out, returns error dict."""
        mock_run.side_effect = merge_telemetry.subprocess.TimeoutExpired("gh", 120)
        result = merge_telemetry.gh("pr", "list")
        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertEqual(result.get("rc"), 2)

    @patch('merge_telemetry.subprocess.run')
    def test_gh_auth_failure_returns_rc2(self, mock_run):
        """When gh auth fails, returns rc=2."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="authentication failed"
        )
        result = merge_telemetry.gh("pr", "list")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("rc"), 2)


class TestJSONOutput(unittest.TestCase):
    """Test JSON output format."""

    def test_json_shape(self):
        """JSON output has rows and metrics keys."""
        rows = [
            {
                "pr_number": 100,
                "ci_attempts": 2,
                "fix_rounds": 1,
                "contended_file": False,
                "red_flag": False,
            }
        ]
        metrics = merge_telemetry.compute_derived_metrics(rows)

        output = {
            "rows": rows,
            "metrics": metrics,
        }

        # Should be JSON serializable
        json_str = json.dumps(output)
        parsed = json.loads(json_str)

        self.assertIn("rows", parsed)
        self.assertIn("metrics", parsed)
        self.assertEqual(len(parsed["rows"]), 1)
        self.assertIn("ci_runs_per_merged_pr", parsed["metrics"])


if __name__ == "__main__":
    unittest.main()
