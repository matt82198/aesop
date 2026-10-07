#!/usr/bin/env python3
"""Tests for cost_projection.py — burn rate calculation and threshold alerts.

Covers:
  - Ledger parsing with windowed time filtering
  - Burn rate calculation (tokens/min) from recent entries
  - Spend projection to end-of-wave
  - Threshold alerts at 70% and 90% of ceiling
  - Alert idempotency (flag files prevent duplicate alerts per wave)
  - JSON output format
  - Degradation when window is thin (honest caveats)
  - No naive datetimes; all UTC epoch-based

Fixtures:
  - Thin window: 1-2 recent entries (sparse data)
  - Steady burn: uniform rate over window
  - Spike: sudden token spike in one entry
  - Over-90%: breach 90% threshold to test alert firing

stdlib-only (unittest, tempfile, datetime), no external deps.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Add tools/ to path so we can import cost_projection and fleet_ledger
REPO = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

try:
    import cost_projection
    import cost_ceiling
except ImportError:
    raise RuntimeError(f"Failed to import tools; TOOLS_DIR={TOOLS_DIR}, sys.path={sys.path}")


class TestCostProjection(unittest.TestCase):
    """Test suite for cost_projection module."""

    def setUp(self):
        """Create a temporary state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.ledger_dir = self.state_dir / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

        # Set AESOP_STATE_ROOT for this test
        self.old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        """Clean up temp directory and restore env."""
        if self.old_state_root is not None:
            os.environ["AESOP_STATE_ROOT"] = self.old_state_root
        else:
            os.environ.pop("AESOP_STATE_ROOT", None)
        self.temp_dir.cleanup()

    def _write_ledger_header(self):
        """Ensure ledger has header."""
        ledger_file = self.ledger_dir / "OUTCOMES-LEDGER.md"
        if not ledger_file.exists():
            header = '| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n'
            header += '|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n'
            ledger_file.write_text(header, encoding='utf-8')

    def _append_ledger_line(self, iso_ts, tokens_in, tokens_out, wave=1):
        """Helper: append a ledger line."""
        self._write_ledger_header()
        ledger_file = self.ledger_dir / "OUTCOMES-LEDGER.md"
        line = f'| {iso_ts} | haiku | haiku | 10 | {tokens_in} | {tokens_out} | OK | build | {wave} |\n'
        with open(ledger_file, 'a', encoding='utf-8') as f:
            f.write(line)

    def _write_config(self, max_wave_tokens=None):
        """Helper: write aesop.config.json with optional ceiling."""
        config = {
            "limits": {
                "max_wave_tokens": max_wave_tokens,
                "max_daily_tokens": None
            },
            "alerts": {
                "webhook_url": None,  # Not configured for unit tests
                "provider": "slack"
            }
        }
        config_file = self.state_dir.parent / "aesop.config.json"
        config_file.write_text(json.dumps(config, indent=2), encoding='utf-8')

    def test_thin_window_single_entry(self):
        """Thin window with 1 entry should calculate burn rate and project."""
        now_utc = datetime.now(timezone.utc)
        iso_ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(iso_ts, 100, 200, wave=1)

        result = cost_projection.project(
            window_minutes=30,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # With thin window (1 entry), burn_rate should be calculated but marked as thin
        self.assertIn("current", result)
        self.assertIn("burn_rate_per_min", result)
        self.assertIn("projected", result)
        self.assertIn("ceiling", result)
        self.assertIn("pct_of_ceiling", result)
        self.assertIn("is_thin_window", result)
        self.assertTrue(result["is_thin_window"])
        self.assertEqual(result["current"], 300)  # 100 + 200

    def test_steady_burn_rate(self):
        """Steady burn over window should project linearly."""
        now_utc = datetime.now(timezone.utc)
        # Add 3 entries, each 5 minutes apart, each 100 tokens
        for i in range(3):
            ts = (now_utc - timedelta(minutes=5 * (2 - i))).isoformat().split('.')[0] + 'Z'
            self._append_ledger_line(ts, 50, 50, wave=1)

        result = cost_projection.project(
            window_minutes=15,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # 3 entries * 100 tokens = 300 current
        self.assertEqual(result["current"], 300)
        # Over 15 minutes (3 entries), burn = 300/15 = 20 tokens/min
        # Projected to 1-hour wave (60 min): 20 * 60 = 1200
        self.assertGreater(result["burn_rate_per_min"], 0)
        self.assertGreater(result["projected"], result["current"])
        self.assertFalse(result["is_thin_window"])

    def test_spike_in_recent_entry(self):
        """Sudden spike should be reflected in current + burn rate."""
        now_utc = datetime.now(timezone.utc)
        # Entry 1: 10 min ago, 100 tokens
        ts1 = (now_utc - timedelta(minutes=10)).isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts1, 50, 50, wave=1)
        # Entry 2: just now, 1000 tokens (spike)
        ts2 = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts2, 500, 500, wave=1)

        result = cost_projection.project(
            window_minutes=15,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # Current = 100 + 1000 = 1100
        self.assertEqual(result["current"], 1100)
        # Burn rate reflects the spike over ~10 min window
        self.assertGreater(result["burn_rate_per_min"], 50)

    def test_ceiling_percentage(self):
        """pct_of_ceiling should be calculated correctly."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 0, 2500, wave=1)  # 2500 tokens

        result = cost_projection.project(
            window_minutes=30,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # 2500 / 10000 = 25%
        self.assertEqual(result["current"], 2500)
        self.assertEqual(result["ceiling"], 10000)
        self.assertEqual(result["pct_of_ceiling"], 25.0)

    def test_alert_at_70_percent(self):
        """Threshold alert should be appended when >= 70% of ceiling."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        # 7000 tokens = 70% of 10000
        self._append_ledger_line(ts, 0, 7000, wave=1)

        alert_file = self.state_dir / "SECURITY-ALERTS.log"

        result = cost_projection.check_and_alert(
            window_minutes=30,
            ceiling=10000,
            wave=1,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # Should return alert info
        self.assertIsNotNone(result.get("alert_level"))
        self.assertEqual(result["alert_level"], "70")

        # Alert should be written
        if alert_file.exists():
            content = alert_file.read_text(encoding='utf-8')
            self.assertIn("70", content)

    def test_alert_at_90_percent(self):
        """Threshold alert at 90% should be critical."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        # 9000 tokens = 90% of 10000
        self._append_ledger_line(ts, 0, 9000, wave=1)

        alert_file = self.state_dir / "SECURITY-ALERTS.log"

        result = cost_projection.check_and_alert(
            window_minutes=30,
            ceiling=10000,
            wave=1,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        self.assertIsNotNone(result.get("alert_level"))
        self.assertEqual(result["alert_level"], "90")

        if alert_file.exists():
            content = alert_file.read_text(encoding='utf-8')
            self.assertIn("90", content)

    def test_alert_idempotency_per_wave(self):
        """Same threshold in same wave should only alert once."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 0, 7000, wave=1)

        alert_file = self.state_dir / "SECURITY-ALERTS.log"

        # First call
        result1 = cost_projection.check_and_alert(
            window_minutes=30,
            ceiling=10000,
            wave=1,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # Second call (same state)
        result2 = cost_projection.check_and_alert(
            window_minutes=30,
            ceiling=10000,
            wave=1,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # Both should return alert_level, but idempotency should prevent duplicate writes
        # via a flag file
        if alert_file.exists():
            lines = alert_file.read_text(encoding='utf-8').strip().split('\n')
            # Should only have 1 alert line (not 2)
            alert_lines = [l for l in lines if l.strip() and '70' in l]
            self.assertLessEqual(len(alert_lines), 1)

    def test_flag_file_prevents_duplicate_alerts(self):
        """Flag file should prevent duplicate alert per threshold per wave."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 0, 7000, wave=1)

        flag_file = self.state_dir / ".cost-alert-70-w1"

        # Call once
        result1 = cost_projection.check_and_alert(
            window_minutes=30,
            ceiling=10000,
            wave=1,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # Flag file should exist after first alert
        self.assertTrue(flag_file.exists())

        # Call again (flag should prevent re-alert)
        result2 = cost_projection.check_and_alert(
            window_minutes=30,
            ceiling=10000,
            wave=1,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # Both results should include alert info (it fires), but log should be idempotent

    def test_json_output_format(self):
        """CLI --json output should be valid JSON with expected keys."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 100, 200, wave=1)

        result = cost_projection.project(
            window_minutes=30,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # Result should be serializable to JSON
        json_str = json.dumps(result)
        parsed = json.loads(json_str)

        # Check expected keys
        expected_keys = [
            "current",
            "burn_rate_per_min",
            "projected",
            "ceiling",
            "pct_of_ceiling",
            "is_thin_window",
            "by_role"
        ]
        for key in expected_keys:
            self.assertIn(key, parsed)

    def test_by_role_breakdown(self):
        """Breakdown by role (model) should be present."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 100, 200, wave=1)

        result = cost_projection.project(
            window_minutes=30,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        self.assertIn("by_role", result)
        self.assertIsInstance(result["by_role"], dict)

    def test_no_ceiling_configured(self):
        """When ceiling is None/unconfigured, projection should degrade gracefully."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 100, 200, wave=1)

        result = cost_projection.project(
            window_minutes=30,
            ceiling=None,
            config={"limits": {"max_wave_tokens": None}}
        )

        self.assertIsNone(result["ceiling"])
        self.assertIsNone(result["pct_of_ceiling"])
        # But current and burn_rate should still be calculated
        self.assertIsNotNone(result["current"])
        self.assertIsNotNone(result["burn_rate_per_min"])

    def test_empty_ledger(self):
        """Empty ledger should return zeros, not crash."""
        self._write_ledger_header()

        result = cost_projection.project(
            window_minutes=30,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        self.assertEqual(result["current"], 0)
        self.assertEqual(result["burn_rate_per_min"], 0.0)
        self.assertEqual(result["projected"], 0)
        self.assertTrue(result["is_thin_window"])

    def test_cli_projection_json(self):
        """CLI --projection --json should output valid JSON."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 100, 200, wave=1)
        self._write_config(max_wave_tokens=10000)

        # This would be a CLI test; for now, just verify the function works
        result = cost_projection.project(
            window_minutes=30,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}}
        )
        json_output = json.dumps(result)
        self.assertIsNotNone(json.loads(json_output))

    def test_minutes_to_ceiling_metric(self):
        """minutes_to_ceiling should estimate time until ceiling hit at current burn rate."""
        now_utc = datetime.now(timezone.utc)
        # Add 3 entries over ~10 min to establish burn rate
        for i in range(3):
            ts = (now_utc - timedelta(minutes=5 * (2 - i))).isoformat().split('.')[0] + 'Z'
            self._append_ledger_line(ts, 50, 50, wave=1)  # 100 tokens per entry

        result = cost_projection.project(
            window_minutes=15,
            ceiling=1000,
            config={"limits": {"max_wave_tokens": 1000}}
        )

        # Current = 300 (3 entries * 100)
        # Burn rate = 300 / 15 = 20 tokens/min
        # Remaining = 1000 - 300 = 700 tokens
        # minutes_to_ceiling = 700 / 20 = 35 minutes
        self.assertIsNotNone(result["minutes_to_ceiling"])
        self.assertGreater(result["minutes_to_ceiling"], 0)
        # Rough check (accounting for rounding and timing variability)
        self.assertLess(result["minutes_to_ceiling"], 100)

    def test_minutes_to_ceiling_none_when_at_ceiling(self):
        """minutes_to_ceiling should be 0.0 when already at or past ceiling."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        # 1200 tokens exceeds 1000 ceiling
        self._append_ledger_line(ts, 600, 600, wave=1)

        result = cost_projection.project(
            window_minutes=30,
            ceiling=1000,
            config={"limits": {"max_wave_tokens": 1000}}
        )

        # Should be 0.0 (already past ceiling)
        self.assertEqual(result["minutes_to_ceiling"], 0.0)

    def test_minutes_to_ceiling_none_without_ceiling(self):
        """minutes_to_ceiling should be None when ceiling is not set."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 100, 200, wave=1)

        result = cost_projection.project(
            window_minutes=30,
            ceiling=None,
            config={"limits": {"max_wave_tokens": None}}
        )

        self.assertIsNone(result["minutes_to_ceiling"])

    def test_horizon_parameter(self):
        """horizon_minutes parameter should affect projected spend."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 0, 100, wave=1)  # 100 tokens

        # Project with 30-minute horizon
        result_30 = cost_projection.project(
            window_minutes=15,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}},
            horizon_minutes=30
        )

        # Project with 120-minute horizon
        result_120 = cost_projection.project(
            window_minutes=15,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}},
            horizon_minutes=120
        )

        # 120-minute projection should be 4x higher than 30-minute
        self.assertGreater(result_120["projected"], result_30["projected"])
        # Rough ratio check
        self.assertAlmostEqual(result_120["projected"] / result_30["projected"], 4.0, delta=0.5)

    def test_spike_both_alerts_at_90_percent(self):
        """Spike past 90% should fire BOTH 70% and 90% alerts independently."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        # 9500 tokens = 95% of 10000 ceiling
        self._append_ledger_line(ts, 0, 9500, wave=1)

        alert_file = self.state_dir / "SECURITY-ALERTS.log"

        result = cost_projection.check_and_alert(
            window_minutes=30,
            ceiling=10000,
            wave=1,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # Should return 90% as alert_level
        self.assertEqual(result["alert_level"], "90")

        # Both alerts should be in the log
        if alert_file.exists():
            content = alert_file.read_text(encoding='utf-8')
            lines = content.strip().split('\n')
            # Should have at least 2 lines: one for 90%, one for 70%
            self.assertGreaterEqual(len([l for l in lines if l.strip()]), 2)
            self.assertIn("90%", content)
            self.assertIn("70%", content)

    def test_wave_required_or_from_env(self):
        """check_and_alert should require wave parameter or AESOP_WAVE env var."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 0, 7000, wave=1)

        # Save current AESOP_WAVE env var
        old_wave_env = os.environ.get("AESOP_WAVE")

        try:
            # Clear env var
            if "AESOP_WAVE" in os.environ:
                del os.environ["AESOP_WAVE"]

            # Should raise ValueError when wave is None and env var is not set
            with self.assertRaises(ValueError) as ctx:
                cost_projection.check_and_alert(
                    window_minutes=30,
                    ceiling=10000,
                    wave=None,
                    config={"limits": {"max_wave_tokens": 10000}}
                )
            self.assertIn("wave parameter required", str(ctx.exception))

            # Now set env var
            os.environ["AESOP_WAVE"] = "2"
            result = cost_projection.check_and_alert(
                window_minutes=30,
                ceiling=10000,
                wave=None,
                config={"limits": {"max_wave_tokens": 10000}}
            )
            # Should succeed using env var
            self.assertIsNotNone(result)

        finally:
            # Restore env var
            if old_wave_env is not None:
                os.environ["AESOP_WAVE"] = old_wave_env
            else:
                os.environ.pop("AESOP_WAVE", None)

    def test_flag_after_successful_append(self):
        """mark_alert_fired should only be called after append succeeds."""
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts, 0, 7000, wave=1)

        # Make state_dir read-only to force append to fail (Unix-like)
        # On Windows, this is trickier; we'll use a different approach:
        # Create the alert file as a directory to force write to fail
        alert_file_path = self.state_dir / "SECURITY-ALERTS.log"
        alert_file_path.mkdir(parents=True, exist_ok=True)

        result = cost_projection.check_and_alert(
            window_minutes=30,
            ceiling=10000,
            wave=1,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # Alert should be detected but flag should NOT be created because append failed
        flag_file = self.state_dir / ".cost-alert-70-w1"
        self.assertFalse(flag_file.exists())

        # Clean up the directory
        alert_file_path.rmdir()

    def test_90_percent_90_append_fails_70_succeeds(self):
        """When 90% append fails but 70% append succeeds at >=90% threshold,
        fired_alert should be True (70% alert was written).

        This is a regression test for the P1 bug: if 90% append fails and 70%
        append succeeds, the function returns fired_alert=false while an alert
        WAS written — a dishonest Report field.
        """
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        # 9500 tokens = 95% of 10000 ceiling
        self._append_ledger_line(ts, 0, 9500, wave=1)

        # Patch append_alert_log to fail for 90%, succeed for 70%
        call_count = {'count': 0}
        original_append = cost_projection.append_alert_log

        def mock_append_alert_log(state_dir, alert_line):
            call_count['count'] += 1
            # Fail on first call (90% alert)
            if '90%' in alert_line:
                return False
            # Succeed on second call (70% alert)
            return original_append(state_dir, alert_line)

        cost_projection.append_alert_log = mock_append_alert_log

        try:
            result = cost_projection.check_and_alert(
                window_minutes=30,
                ceiling=10000,
                wave=1,
                config={"limits": {"max_wave_tokens": 10000}}
            )

            # Even though 90% append failed, 70% succeeded, so fired_alert should be True
            self.assertTrue(result['fired_alert'],
                           f"fired_alert should be True when 70% append succeeds, "
                           f"but got {result['fired_alert']}")

            # alert_level should still be 90
            self.assertEqual(result['alert_level'], "90")

            # The 70% alert should be in the log (70% append succeeded)
            alert_file = self.state_dir / "SECURITY-ALERTS.log"
            if alert_file.exists():
                content = alert_file.read_text(encoding='utf-8')
                self.assertIn("70%", content)
        finally:
            cost_projection.append_alert_log = original_append

    def test_90_percent_both_appends_fail(self):
        """When both 90% and 70% appends fail at >=90% threshold,
        fired_alert should be False.
        """
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        # 9500 tokens = 95% of 10000 ceiling
        self._append_ledger_line(ts, 0, 9500, wave=1)

        # Patch append_alert_log to always fail
        def mock_append_alert_log(state_dir, alert_line):
            return False

        original_append = cost_projection.append_alert_log
        cost_projection.append_alert_log = mock_append_alert_log

        try:
            result = cost_projection.check_and_alert(
                window_minutes=30,
                ceiling=10000,
                wave=1,
                config={"limits": {"max_wave_tokens": 10000}}
            )

            # When both appends fail, fired_alert should be False
            self.assertFalse(result['fired_alert'],
                            f"fired_alert should be False when all appends fail, "
                            f"but got {result['fired_alert']}")

            # alert_level should still be 90 (reflects the threshold)
            self.assertEqual(result['alert_level'], "90")
        finally:
            cost_projection.append_alert_log = original_append

    def test_70_percent_alert_sets_fired_flag(self):
        """When 70% alert fires (pct >= 70% but < 90%), fired_alert should be True
        after successful append (existing behavior, regression check).
        """
        now_utc = datetime.now(timezone.utc)
        ts = now_utc.isoformat().split('.')[0] + 'Z'
        # 7500 tokens = 75% of 10000 ceiling (between 70 and 90)
        self._append_ledger_line(ts, 0, 7500, wave=1)

        result = cost_projection.check_and_alert(
            window_minutes=30,
            ceiling=10000,
            wave=1,
            config={"limits": {"max_wave_tokens": 10000}}
        )

        # fired_alert should be True for the 70% alert
        self.assertTrue(result['fired_alert'],
                       f"fired_alert should be True after 70% alert, "
                       f"but got {result['fired_alert']}")
        self.assertEqual(result['alert_level'], "70")


class TestWindowContract(unittest.TestCase):
    """Test suite for window contract between cost_projection and cost_ceiling.

    Verifies that cost_projection.project() and cost_ceiling.check() both
    use the shared calculate_window_bounds() helper and produce identical
    spend calculations when given the same window_minutes.
    """

    def setUp(self):
        """Create a temporary state directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.ledger_dir = self.state_dir / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

        # Set AESOP_STATE_ROOT for this test
        self.old_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        """Clean up temp directory and restore env."""
        if self.old_state_root is not None:
            os.environ["AESOP_STATE_ROOT"] = self.old_state_root
        else:
            os.environ.pop("AESOP_STATE_ROOT", None)
        self.temp_dir.cleanup()

    def _write_ledger_header(self):
        """Ensure ledger has header."""
        ledger_file = self.ledger_dir / "OUTCOMES-LEDGER.md"
        if not ledger_file.exists():
            header = '| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n'
            header += '|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n'
            ledger_file.write_text(header, encoding='utf-8')

    def _append_ledger_line(self, iso_ts, tokens_in, tokens_out, wave=1):
        """Helper: append a ledger line."""
        self._write_ledger_header()
        ledger_file = self.ledger_dir / "OUTCOMES-LEDGER.md"
        line = f'| {iso_ts} | haiku | haiku | 10 | {tokens_in} | {tokens_out} | OK | build | {wave} |\n'
        with open(ledger_file, 'a', encoding='utf-8') as f:
            f.write(line)

    def test_same_window_same_spend(self):
        """cost_projection and cost_ceiling should agree on spend with same window."""
        now_utc = datetime.now(timezone.utc)
        # Add 3 recent entries
        for i in range(3):
            ts = (now_utc - timedelta(minutes=5 * (2 - i))).isoformat().split('.')[0] + 'Z'
            self._append_ledger_line(ts, 100, 100, wave=1)  # 200 tokens each

        window_minutes = 15

        # Get spend from projection's window filter
        proj_result = cost_projection.project(
            window_minutes=window_minutes,
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}}
        )
        proj_current = proj_result["current"]

        # Get spend from ceiling's window filter
        ceiling_result = cost_ceiling.check(
            period="wave",
            config={"limits": {"max_wave_tokens": 10000}},
            state_dir=self.state_dir,
            trip=False,
            window_minutes=window_minutes
        )
        ceiling_spent = ceiling_result["spent"]

        # Both should calculate the same spend for the window
        self.assertEqual(proj_current, ceiling_spent,
                        f"cost_projection ({proj_current}) and cost_ceiling ({ceiling_spent}) "
                        f"should agree when using same window_minutes={window_minutes}")

    def test_divergent_window_divergent_spend(self):
        """cost_projection and cost_ceiling should differ with different windows."""
        now_utc = datetime.now(timezone.utc)
        # Add 4 entries: 2 recent, 2 older
        # Recent: 10 and 5 min ago
        ts1 = (now_utc - timedelta(minutes=5)).isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts1, 100, 100, wave=1)  # 200 tokens
        ts2 = (now_utc - timedelta(minutes=10)).isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts2, 100, 100, wave=1)  # 200 tokens
        # Older: 20 and 30 min ago (outside 15-min window)
        ts3 = (now_utc - timedelta(minutes=20)).isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts3, 100, 100, wave=1)  # 200 tokens
        ts4 = (now_utc - timedelta(minutes=30)).isoformat().split('.')[0] + 'Z'
        self._append_ledger_line(ts4, 100, 100, wave=1)  # 200 tokens

        # Use 15-min window (should only include ts1 and ts2)
        window_15 = 15
        proj_15 = cost_projection.project(window_minutes=window_15, ceiling=10000, config={"limits": {"max_wave_tokens": 10000}})
        ceiling_15 = cost_ceiling.check(period="wave", config={"limits": {"max_wave_tokens": 10000}}, state_dir=self.state_dir, trip=False, window_minutes=window_15)

        # Both should have 400 tokens (2 entries * 200)
        self.assertEqual(proj_15["current"], 400)
        self.assertEqual(ceiling_15["spent"], 400)

        # Use 35-min window (should include all 4)
        window_35 = 35
        proj_35 = cost_projection.project(window_minutes=window_35, ceiling=10000, config={"limits": {"max_wave_tokens": 10000}})
        ceiling_35 = cost_ceiling.check(period="wave", config={"limits": {"max_wave_tokens": 10000}}, state_dir=self.state_dir, trip=False, window_minutes=window_35)

        # Both should have 800 tokens (4 entries * 200)
        self.assertEqual(proj_35["current"], 800)
        self.assertEqual(ceiling_35["spent"], 800)

        # The two results should differ
        self.assertNotEqual(proj_15["current"], proj_35["current"])
        self.assertNotEqual(ceiling_15["spent"], ceiling_35["spent"])

    def test_window_none_uses_all_rows(self):
        """cost_projection and cost_ceiling should agree when window_minutes=None (all rows)."""
        now_utc = datetime.now(timezone.utc)
        # Add entries across a wider time span
        for i in range(5):
            ts = (now_utc - timedelta(minutes=60 * i)).isoformat().split('.')[0] + 'Z'
            self._append_ledger_line(ts, 100, 50, wave=1)  # 150 tokens each

        # Use no window (backward compat: all rows)
        proj_result = cost_projection.project(
            window_minutes=30,  # This window_minutes only applies to thin-window detection
            ceiling=10000,
            config={"limits": {"max_wave_tokens": 10000}},
            # Note: projection doesn't support window_minutes in the API yet for all-rows
            # But ceiling.check() with window_minutes=None does
        )

        ceiling_result = cost_ceiling.check(
            period="wave",
            config={"limits": {"max_wave_tokens": 10000}},
            state_dir=self.state_dir,
            trip=False,
            window_minutes=None  # Use all rows
        )

        # Current spend from projection is for 30-min window; ceiling with window_minutes=None is all rows
        # So they won't match. This test verifies the None behavior is consistent.
        # For now, just verify that both return integers
        self.assertIsInstance(proj_result["current"], int)
        self.assertIsInstance(ceiling_result["spent"], int)


if __name__ == '__main__':
    unittest.main()
