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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.health_checks import (
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


class TestNoLintEvasion(unittest.TestCase):
    """Regression guard: heartbeat filenames must stay literal in the source.

    Commit 16b3f8e3 split ".watchdog-heartbeat" into (".watchdog" + "-heartbeat")
    so stateapi_lint's pattern match would not see it, then ratcheted the baseline
    down on the strength of the hidden violations. The filenames must appear as
    contiguous string literals so the lint can count them honestly; the two
    resulting violations are recorded in .stateapi-baseline.json instead.
    """

    SOURCE = Path(__file__).parent.parent / "tools" / "health_checks.py"

    def _source(self):
        return self.SOURCE.read_text(encoding="utf-8")

    def test_watchdog_heartbeat_name_is_literal(self):
        """.watchdog-heartbeat appears as one contiguous literal."""
        self.assertIn(
            '".watchdog-heartbeat"',
            self._source(),
            "watchdog heartbeat filename must be a literal, not concat-assembled",
        )

    def test_monitor_heartbeat_name_is_literal(self):
        """.monitor-heartbeat appears as one contiguous literal."""
        self.assertIn(
            '".monitor-heartbeat"',
            self._source(),
            "monitor heartbeat filename must be a literal, not concat-assembled",
        )

    def test_no_concatenated_heartbeat_fragments(self):
        """No string-concat assembly of any heartbeat filename."""
        source = self._source()
        for fragment in ('" + "-heartbeat"', "' + '-heartbeat'",
                         '"-heartbeat" +', "'-heartbeat' +",
                         '"heartbeat" +', '" + "heartbeat"'):
            self.assertNotIn(
                fragment,
                source,
                "concat-assembled heartbeat filename (lint evasion): %r" % fragment,
            )

    def test_violations_are_in_stateapi_baseline(self):
        """Both heartbeat reads are honestly recorded in the ratchet baseline."""
        import json

        baseline_file = Path(__file__).parent.parent / ".stateapi-baseline.json"
        violations = json.loads(baseline_file.read_text(encoding="utf-8"))["violations"]
        for key in ("tools/health_checks.py@watchdog-hb",
                    "tools/health_checks.py@monitor-hb"):
            self.assertIn(
                key,
                violations,
                "baseline must record the real violation, not hide it: %s" % key,
            )


if __name__ == "__main__":
    unittest.main()
