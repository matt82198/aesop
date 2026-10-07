#!/usr/bin/env python3
"""
Tests for tools/common.py freshness checking with future-timestamp handling.

Audit: P2 bug fix for heartbeat staleness helper.
Bug: max(0, age_seconds) clamped negative ages (future-dated timestamps from
clock skew) to 0, reporting dead watchdogs as fresh forever.
Fix: Timestamps more than 120s in the FUTURE are treated as stale, not clamped.

Tests verify:
1. Past timestamps -> fresh/stale correctly by threshold (original behavior)
2. Future timestamps within clock-skew tolerance (< 120s) -> clamped to 0, fresh
3. Future timestamps beyond tolerance (>= 120s) -> reported as stale, not age=0-fresh
4. Edge cases at boundaries
"""

import unittest
import sys
import tempfile
import time
from pathlib import Path

# Add tools to path for import
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from common import check_heartbeat_staleness


class TestFreshnessPastTimestamps(unittest.TestCase):
    """Test freshness checking with past-dated heartbeats (normal case)."""

    def setUp(self):
        """Create temp directory for heartbeat files."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def test_fresh_heartbeat_recent(self):
        """Verify fresh heartbeat is not stale."""
        hb_file = self.state_dir / "test-hb"
        now = int(time.time())
        hb_file.write_text(str(now))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertFalse(is_stale, "Recent heartbeat should be fresh")
        self.assertLessEqual(age_s, 2, "Age should be 0-1 seconds")
        self.assertIsNone(info, "Fresh heartbeat should have no info message")

    def test_stale_heartbeat_past(self):
        """Verify stale (old) heartbeat is detected as stale."""
        hb_file = self.state_dir / "test-hb"
        old_time = int(time.time()) - 400  # 400 seconds old
        hb_file.write_text(str(old_time))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertTrue(is_stale, "Old heartbeat should be stale")
        self.assertGreaterEqual(age_s, 399, "Age should be >= 399 seconds")
        self.assertIsNotNone(info, "Stale heartbeat should have info message")
        self.assertIn("stale", info.lower(), "Info should mention staleness")

    def test_heartbeat_exactly_at_threshold(self):
        """Test boundary: heartbeat exactly at threshold is stale."""
        hb_file = self.state_dir / "test-hb"
        old_time = int(time.time()) - 300  # Exactly 300 seconds old
        hb_file.write_text(str(old_time))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        # At threshold should be stale (>= comparison)
        self.assertTrue(is_stale, "Heartbeat at threshold should be stale")
        self.assertGreaterEqual(age_s, 299, "Age should be >= 299")

    def test_heartbeat_just_under_threshold(self):
        """Test boundary: heartbeat just under threshold is fresh."""
        hb_file = self.state_dir / "test-hb"
        # 295s old vs 300s threshold: the margin must absorb wall-clock drift
        # between write and check on a loaded runner (a 1s margin flaked in
        # full-suite runs); the exact ==threshold boundary is covered above.
        old_time = int(time.time()) - 295
        hb_file.write_text(str(old_time))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        # Under threshold should be fresh
        self.assertFalse(is_stale, "Heartbeat under threshold should be fresh")
        self.assertIsNone(info, "Fresh heartbeat should have no info message")


class TestFreshnessFutureTimestamps(unittest.TestCase):
    """Test freshness checking with future-dated heartbeats (clock skew case)."""

    def setUp(self):
        """Create temp directory for heartbeat files."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def test_future_timestamp_within_tolerance(self):
        """
        Verify future timestamp within tolerance (60s) is treated fresh.
        Clock skew recovery: small negative ages are clamped to 0 and treated fresh.
        """
        hb_file = self.state_dir / "test-hb"
        future_time = int(time.time()) + 60  # 60 seconds in future
        hb_file.write_text(str(future_time))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        # Within tolerance (120s): should be treated as fresh (clamped to 0)
        self.assertFalse(is_stale, "Future timestamp within tolerance should be fresh")
        self.assertEqual(age_s, 0, "Clamped age should be 0")
        self.assertIsNone(info, "Fresh heartbeat should have no info message")

    def test_future_timestamp_at_tolerance_boundary(self):
        """Test boundary: timestamp exactly 120s in future (at tolerance)."""
        hb_file = self.state_dir / "test-hb"
        future_time = int(time.time()) + 120  # Exactly 120 seconds in future
        hb_file.write_text(str(future_time))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        # At boundary (120s): technically within tolerance, should be clamped
        # The check is age_seconds < -120, so 120 seconds future (-120 age) is NOT stale
        self.assertFalse(is_stale, "Timestamp at 120s boundary should be treated fresh")
        self.assertEqual(age_s, 0, "Clamped age should be 0")

    def test_future_timestamp_beyond_tolerance(self):
        """
        Verify future timestamp beyond tolerance (200s) is treated STALE.
        This is the critical fix: dead watchdog with clock-skewed timestamp
        should NOT appear fresh forever.
        """
        hb_file = self.state_dir / "test-hb"
        future_time = int(time.time()) + 200  # 200 seconds in future
        hb_file.write_text(str(future_time))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        # Beyond tolerance (>120s): should be reported as STALE
        self.assertTrue(is_stale, "Future timestamp beyond tolerance should be stale")
        self.assertEqual(age_s, 0, "Stale future timestamp should report age=0")
        self.assertIsNotNone(info, "Stale future timestamp should have info message")
        self.assertIn("future", info.lower(), "Info should mention future/clock skew")

    def test_future_timestamp_far_beyond_tolerance(self):
        """Verify far-future timestamp (>500s) is treated as stale."""
        hb_file = self.state_dir / "test-hb"
        future_time = int(time.time()) + 500  # 500 seconds in future
        hb_file.write_text(str(future_time))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertTrue(is_stale, "Far-future timestamp should be stale")
        self.assertEqual(age_s, 0, "Stale future timestamp should report age=0")
        self.assertIsNotNone(info, "Stale future timestamp should have info message")

    def test_future_timestamp_121s_just_beyond_tolerance(self):
        """Test just beyond tolerance boundary: 121s future should be stale."""
        hb_file = self.state_dir / "test-hb"
        future_time = int(time.time()) + 121  # 121 seconds in future
        hb_file.write_text(str(future_time))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        # 121s future (-121 age) should trigger stale (< -120)
        self.assertTrue(is_stale, "Timestamp 121s in future should be stale")
        self.assertEqual(age_s, 0, "Stale future timestamp should report age=0")


class TestFreshnessEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions."""

    def setUp(self):
        """Create temp directory for heartbeat files."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def test_missing_heartbeat_file(self):
        """Verify missing file is stale with age=0."""
        hb_file = self.state_dir / "nonexistent-hb"

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertTrue(is_stale, "Missing file should be stale")
        self.assertEqual(age_s, 0, "Missing file should report age=0")
        self.assertIsNotNone(info, "Missing file should have info message")
        self.assertIn("missing", info.lower())

    def test_empty_heartbeat_file(self):
        """Verify empty file is stale with age=0."""
        hb_file = self.state_dir / "empty-hb"
        hb_file.write_text("")

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertTrue(is_stale, "Empty file should be stale")
        self.assertEqual(age_s, 0, "Empty file should report age=0")
        self.assertIsNotNone(info, "Empty file should have info message")

    def test_unparseable_heartbeat_file(self):
        """Verify unparseable content is stale with age=0."""
        hb_file = self.state_dir / "bad-hb"
        hb_file.write_text("not-a-number")

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertTrue(is_stale, "Unparseable file should be stale")
        self.assertEqual(age_s, 0, "Unparseable file should report age=0")
        self.assertIsNotNone(info, "Unparseable file should have info message")


class TestFreshnessBackwardCompatibility(unittest.TestCase):
    """
    Verify the fix maintains backward compatibility with existing callers.
    Callers expect: (is_stale: bool, age_s: int >= 0, info: str or None)
    """

    def setUp(self):
        """Create temp directory for heartbeat files."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)

    def tearDown(self):
        """Clean up temp directory."""
        self.tmpdir.cleanup()

    def test_return_type_is_tuple_of_three(self):
        """Verify return type is always (bool, int, str|None)."""
        hb_file = self.state_dir / "test-hb"
        hb_file.write_text(str(int(time.time())))

        result = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertIsInstance(result, tuple, "Return should be tuple")
        self.assertEqual(len(result), 3, "Return should have 3 elements")

        is_stale, age_s, info = result
        self.assertIsInstance(is_stale, bool, "First element should be bool")
        self.assertIsInstance(age_s, int, "Second element should be int")
        self.assertGreaterEqual(age_s, 0, "Age should always be >= 0")
        self.assertTrue(
            info is None or isinstance(info, str),
            "Third element should be None or str"
        )

    def test_age_s_always_non_negative(self):
        """Verify age_s is always >= 0 (never negative)."""
        hb_file = self.state_dir / "test-hb"

        # Test with future timestamp
        future_time = int(time.time()) + 500
        hb_file.write_text(str(future_time))
        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)
        self.assertGreaterEqual(age_s, 0, "Future timestamp should have age >= 0")

        # Test with past timestamp
        hb_file.write_text(str(int(time.time()) - 100))
        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)
        self.assertGreaterEqual(age_s, 0, "Past timestamp should have age >= 0")

    def test_missing_file_returns_age_zero(self):
        """
        Verify missing/unparseable files always return age=0.
        Callers use age_s == 0 to distinguish missing from stale.
        """
        hb_file = self.state_dir / "nonexistent-hb"

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        self.assertEqual(age_s, 0, "Missing file must return age=0")

    def test_caller_pattern_works_with_future_timestamp(self):
        """
        Simulate typical caller pattern: check is_stale, then handle based on age_s.
        Verify pattern doesn't break with future timestamp.
        """
        hb_file = self.state_dir / "test-hb"
        future_time = int(time.time()) + 150  # Beyond tolerance
        hb_file.write_text(str(future_time))

        is_stale, age_s, info = check_heartbeat_staleness(hb_file, threshold_s=300)

        # Typical pattern from healthcheck.py:
        if not is_stale:
            status = "FRESH"
        elif age_s == 0:
            # File missing/unparseable
            status = "MISSING_OR_UNPARSEABLE"
        else:
            # File exists but is stale
            status = "STALE_BUT_READABLE"

        # Future timestamp beyond tolerance should be treated like missing
        # (age=0 with is_stale=True)
        self.assertTrue(is_stale, "Future timestamp should be stale")
        self.assertEqual(age_s, 0, "Future timestamp should report age=0")
        self.assertEqual(status, "MISSING_OR_UNPARSEABLE")


if __name__ == "__main__":
    unittest.main()
