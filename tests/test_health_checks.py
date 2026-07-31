#!/usr/bin/env python3
"""
Tests for health_checks.py heartbeat utilities.

Tests:
  - check_heartbeat_file() with fresh, stale, missing, empty, unparseable files
  - check_watchdog_heartbeat() and check_monitor_heartbeat() wrappers
  - STALE contract: unreadable/absent/unparseable => STALE (never healthy)
"""

import tempfile
import time
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from health_checks import (
    check_heartbeat_file,
    check_watchdog_heartbeat,
    check_monitor_heartbeat,
    WATCHDOG_THRESHOLD_S,
    MONITOR_THRESHOLD_S,
)


class TestCheckHeartbeatFile(unittest.TestCase):
    """Tests for check_heartbeat_file() function."""

    def test_fresh_heartbeat(self):
        """check_heartbeat_file() returns fresh for recent timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            hb_file = tmppath / ".test-heartbeat"

            # Write current time as epoch
            current_epoch = int(time.time())
            hb_file.write_text(str(current_epoch), encoding="utf-8")

            is_stale, age, info = check_heartbeat_file(hb_file, threshold_s=60)

            self.assertFalse(is_stale)
            self.assertLessEqual(age, 2)  # Allow 2s clock skew
            self.assertIsNone(info)

    def test_stale_heartbeat(self):
        """check_heartbeat_file() returns stale for old timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            hb_file = tmppath / ".test-heartbeat"

            # Write timestamp 120 seconds ago
            old_epoch = int(time.time()) - 120
            hb_file.write_text(str(old_epoch), encoding="utf-8")

            is_stale, age, info = check_heartbeat_file(hb_file, threshold_s=60)

            self.assertTrue(is_stale)
            self.assertGreaterEqual(age, 120)
            self.assertIsNotNone(info)

    def test_missing_heartbeat(self):
        """check_heartbeat_file() returns stale for missing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            hb_file = tmppath / ".nonexistent-heartbeat"

            is_stale, age, info = check_heartbeat_file(hb_file, threshold_s=60)

            self.assertTrue(is_stale)
            self.assertEqual(age, 0)  # Missing => age is 0
            self.assertIsNotNone(info)
            self.assertIn("missing", info.lower())

    def test_empty_heartbeat(self):
        """check_heartbeat_file() returns stale for empty file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            hb_file = tmppath / ".test-heartbeat"
            hb_file.write_text("", encoding="utf-8")

            is_stale, age, info = check_heartbeat_file(hb_file, threshold_s=60)

            self.assertTrue(is_stale)
            self.assertEqual(age, 0)
            self.assertIsNotNone(info)

    def test_unparseable_heartbeat(self):
        """check_heartbeat_file() returns stale for non-numeric content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            hb_file = tmppath / ".test-heartbeat"
            hb_file.write_text("not-a-number", encoding="utf-8")

            is_stale, age, info = check_heartbeat_file(hb_file, threshold_s=60)

            self.assertTrue(is_stale)
            self.assertEqual(age, 0)
            self.assertIsNotNone(info)


class TestWatchdogHeartbeat(unittest.TestCase):
    """Tests for check_watchdog_heartbeat() wrapper."""

    def test_watchdog_uses_300s_threshold(self):
        """check_watchdog_heartbeat() uses 300s threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            state_dir = tmppath / "state"
            state_dir.mkdir()

            hb_file = state_dir / ".watchdog-heartbeat"
            # Write timestamp 250s ago (fresh for 300s threshold)
            old_epoch = int(time.time()) - 250
            hb_file.write_text(str(old_epoch), encoding="utf-8")

            is_stale, age, info = check_watchdog_heartbeat(state_dir)

            self.assertFalse(is_stale)
            self.assertGreaterEqual(age, 250)

    def test_watchdog_stale_after_300s(self):
        """check_watchdog_heartbeat() marks stale after 300s."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            state_dir = tmppath / "state"
            state_dir.mkdir()

            hb_file = state_dir / ".watchdog-heartbeat"
            # Write timestamp 350s ago (stale for 300s threshold)
            old_epoch = int(time.time()) - 350
            hb_file.write_text(str(old_epoch), encoding="utf-8")

            is_stale, age, info = check_watchdog_heartbeat(state_dir)

            self.assertTrue(is_stale)
            self.assertGreaterEqual(age, 350)


class TestMonitorHeartbeat(unittest.TestCase):
    """Tests for check_monitor_heartbeat() wrapper."""

    def test_monitor_uses_3600s_threshold(self):
        """check_monitor_heartbeat() uses 3600s threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            state_dir = tmppath / "state"
            state_dir.mkdir()

            hb_file = state_dir / ".monitor-heartbeat"
            # Write timestamp 3500s ago (fresh for 3600s threshold)
            old_epoch = int(time.time()) - 3500
            hb_file.write_text(str(old_epoch), encoding="utf-8")

            is_stale, age, info = check_monitor_heartbeat(state_dir)

            self.assertFalse(is_stale)
            self.assertGreaterEqual(age, 3500)

    def test_monitor_stale_after_3600s(self):
        """check_monitor_heartbeat() marks stale after 3600s."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            state_dir = tmppath / "state"
            state_dir.mkdir()

            hb_file = state_dir / ".monitor-heartbeat"
            # Write timestamp 3700s ago (stale for 3600s threshold)
            old_epoch = int(time.time()) - 3700
            hb_file.write_text(str(old_epoch), encoding="utf-8")

            is_stale, age, info = check_monitor_heartbeat(state_dir)

            self.assertTrue(is_stale)
            self.assertGreaterEqual(age, 3700)


class TestHeartbeatStaleContract(unittest.TestCase):
    """Tests for the STALE contract: absent/unreadable/unparseable => STALE."""

    def test_missing_never_fresh(self):
        """Missing heartbeat must never be reported as fresh."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            hb_file = tmppath / ".nonexistent"

            is_stale, age, info = check_heartbeat_file(hb_file, threshold_s=1)

            self.assertTrue(is_stale, "Missing heartbeat must be STALE")

    def test_unparseable_never_fresh(self):
        """Unparseable heartbeat must never be reported as fresh."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            hb_file = tmppath / ".test-heartbeat"
            hb_file.write_text("invalid", encoding="utf-8")

            is_stale, age, info = check_heartbeat_file(hb_file, threshold_s=1)

            self.assertTrue(is_stale, "Unparseable heartbeat must be STALE")

    def test_empty_never_fresh(self):
        """Empty heartbeat must never be reported as fresh."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            hb_file = tmppath / ".test-heartbeat"
            hb_file.write_text("", encoding="utf-8")

            is_stale, age, info = check_heartbeat_file(hb_file, threshold_s=1)

            self.assertTrue(is_stale, "Empty heartbeat must be STALE")


if __name__ == "__main__":
    unittest.main()
