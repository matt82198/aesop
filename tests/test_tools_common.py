#!/usr/bin/env python3
"""
Tests for tools/common.py — shared utility functions.

TDD: These tests verify:
1. get_state_dir() respects AESOP_STATE_ROOT env var and ./state fallback
2. check_heartbeat_staleness() correctly identifies fresh/stale/missing heartbeats
"""

import unittest
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

# Add tools to path for import
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from common import get_state_dir, check_heartbeat_staleness


class TestGetStateDir(unittest.TestCase):
    """Test state directory resolution."""

    def setUp(self):
        """Save original env var."""
        self.orig_aesop_state_root = os.environ.get("AESOP_STATE_ROOT")

    def tearDown(self):
        """Restore original env var."""
        if self.orig_aesop_state_root:
            os.environ["AESOP_STATE_ROOT"] = self.orig_aesop_state_root
        elif "AESOP_STATE_ROOT" in os.environ:
            del os.environ["AESOP_STATE_ROOT"]

    def test_get_state_dir_respects_env_var(self):
        """Test that AESOP_STATE_ROOT env var overrides default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["AESOP_STATE_ROOT"] = tmpdir
            result = get_state_dir()
            self.assertEqual(result, Path(tmpdir))

    def test_get_state_dir_fallback_to_state_subdir(self):
        """Test fallback to ./state when env var not set."""
        if "AESOP_STATE_ROOT" in os.environ:
            del os.environ["AESOP_STATE_ROOT"]
        result = get_state_dir()
        expected = Path.cwd() / "state"
        self.assertEqual(result, expected)

    def test_get_state_dir_returns_pathlib_path(self):
        """Test that result is always a Path object."""
        result = get_state_dir()
        self.assertIsInstance(result, Path)


class TestCheckHeartbeatStaleness(unittest.TestCase):
    """Test heartbeat staleness detection."""

    def setUp(self):
        """Create temp directory for heartbeat files."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def test_fresh_heartbeat(self):
        """Test that fresh heartbeat is not stale."""
        hb_file = self.state_dir / "test-hb"
        now = int(time.time())
        hb_file.write_text(str(now))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertFalse(is_stale)
        self.assertLessEqual(age_s, 2)  # Should be 0 or 1
        self.assertIsNone(info)

    def test_stale_heartbeat(self):
        """Test that stale heartbeat is detected."""
        hb_file = self.state_dir / "test-hb"
        old_time = int(time.time()) - 400  # 400 seconds old
        hb_file.write_text(str(old_time))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertTrue(is_stale)
        self.assertGreaterEqual(age_s, 399)
        self.assertIsNotNone(info)
        self.assertIn("stale", info.lower())

    def test_missing_heartbeat_file(self):
        """Test that missing file is reported as stale."""
        hb_file = self.state_dir / "nonexistent-hb"

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertTrue(is_stale)
        self.assertIsNotNone(info)
        self.assertIn("missing", info.lower())

    def test_unparseable_heartbeat(self):
        """Test that unparseable content is reported as stale."""
        hb_file = self.state_dir / "bad-hb"
        hb_file.write_text("not-a-number")

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertTrue(is_stale)
        self.assertIsNotNone(info)
        self.assertIn("unreadable", info.lower())

    def test_empty_heartbeat_file(self):
        """Test that empty file is reported as stale."""
        hb_file = self.state_dir / "empty-hb"
        hb_file.write_text("")

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertTrue(is_stale)
        self.assertIsNotNone(info)

    def test_heartbeat_exactly_at_threshold(self):
        """Test boundary condition: heartbeat exactly at threshold.

        TDD fix: Use logical time (mocked) instead of wall-clock to avoid
        CI flakes where execution time can cross the boundary."""
        hb_file = self.state_dir / "test-hb"

        with mock.patch("time.time") as mock_time:
            # Write heartbeat at logical time 1000.0
            mock_time.return_value = 1000.0
            old_time = 1000 - 300  # Exactly 300 seconds old
            hb_file.write_text(str(old_time))

            # Check staleness at same logical time
            mock_time.return_value = 1000.0
            is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

            # At threshold should be stale (>= comparison)
            self.assertTrue(is_stale)

    def test_heartbeat_just_under_threshold(self):
        """Test boundary condition: heartbeat just under threshold.

        TDD fix: Use logical time (mocked) instead of wall-clock to ensure
        the age stays just under threshold regardless of CI execution speed.
        Root cause: On slow runners, wall-clock time between write and check
        can exceed the 1-second margin (299 sec old becomes 300+ sec old),
        causing the assertion to fail."""
        hb_file = self.state_dir / "test-hb"

        with mock.patch("time.time") as mock_time:
            # Write heartbeat at logical time 1000.0
            mock_time.return_value = 1000.0
            old_time = 1000 - 299  # 299 seconds old (just under threshold)
            hb_file.write_text(str(old_time))

            # Check staleness at same logical time (no time passage)
            # so age remains 299 (< 300 threshold)
            mock_time.return_value = 1000.0
            is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

            # Under threshold should not be stale
            self.assertFalse(is_stale)
            self.assertIsNone(info)

    def test_age_calculation_accuracy(self):
        """Test that age is calculated accurately.

        TDD fix: Use logical time (mocked) to eliminate wall-clock variability
        and verify exact age calculation."""
        hb_file = self.state_dir / "test-hb"
        age_expected = 123

        with mock.patch("time.time") as mock_time:
            # Write heartbeat at logical time 1000.0
            mock_time.return_value = 1000.0
            old_time = 1000 - age_expected
            hb_file.write_text(str(old_time))

            # Check staleness at same logical time
            mock_time.return_value = 1000.0
            is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

            # Age should be exactly 123 seconds (no wall-clock variation)
            self.assertEqual(age_s, age_expected)


if __name__ == "__main__":
    unittest.main()
