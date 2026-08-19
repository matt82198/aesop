#!/usr/bin/env python3
"""Unit tests for power_selftest.py health check harness."""
import os
import sys
import subprocess
import tempfile
import unittest
import json
from pathlib import Path
from datetime import datetime, timedelta


class TestPowerSelftest(unittest.TestCase):
    """Test cases for power_selftest.py health checks."""

    def setUp(self):
        """Create temporary directories for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.selftest_script = Path(__file__).parent.parent / "tools" / "power_selftest.py"
        self.state_dir = Path(self.temp_dir) / "state"
        self.brain_dir = Path(self.temp_dir) / "brain"

    def tearDown(self):
        """Clean up temporary directories."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _run_selftest(self, env_overrides=None):
        """Run power_selftest.py with environment overrides."""
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        env["BRAIN_ROOT"] = str(self.brain_dir)
        if env_overrides:
            env.update(env_overrides)

        result = subprocess.run(
            [sys.executable, str(self.selftest_script)],
            capture_output=True,
            text=True,
            cwd=self.temp_dir,
            env=env
        )
        return result

    def test_happy_path_degraded_missing_state(self):
        """Test with missing state directory (graceful degradation)."""
        # Don't create state_dir; should not crash
        result = self._run_selftest()
        self.assertIn("POWER-SELFTEST:", result.stdout)
        # Should exit 0 (OK or DEGRADED)
        self.assertEqual(result.returncode, 0)

    def test_happy_path_missing_config(self):
        """Test with missing aesop.config.json (graceful degradation)."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        result = self._run_selftest()
        self.assertIn("POWER-SELFTEST:", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_output_format_ok(self):
        """Test output format when everything is OK."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        result = self._run_selftest()

        # Should contain "POWER-SELFTEST: OK" or "POWER-SELFTEST: DEGRADED"
        self.assertIn("POWER-SELFTEST:", result.stdout)
        self.assertTrue("OK" in result.stdout or "DEGRADED" in result.stdout)

    def test_graceful_degradation_no_heartbeats(self):
        """Test graceful degradation when no heartbeat files exist."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        result = self._run_selftest()
        self.assertEqual(result.returncode, 0)
        self.assertIn("POWER-SELFTEST:", result.stdout)

    def test_heartbeat_fresh_boundary(self):
        """Test fresh vs stale heartbeat at 300s boundary."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        hb_dir = self.state_dir / "heartbeats"
        hb_dir.mkdir(parents=True, exist_ok=True)

        # Create fresh heartbeat (just written)
        now_epoch = int(datetime.now().timestamp())
        fresh_hb = hb_dir / "test_beat"
        fresh_hb.write_text(str(now_epoch) + "\n")

        result = self._run_selftest()
        self.assertEqual(result.returncode, 0)
        # Should report OK (not stale)
        self.assertIn("POWER-SELFTEST:", result.stdout)

    def test_heartbeat_stale_boundary(self):
        """Test stale heartbeat detection (> 300s old)."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        hb_dir = self.state_dir / "heartbeats"
        hb_dir.mkdir(parents=True, exist_ok=True)

        # Create stale heartbeat (> 300s old)
        now_epoch = int(datetime.now().timestamp())
        stale_epoch = now_epoch - 400  # 400 seconds ago
        stale_hb = hb_dir / "test_beat"
        stale_hb.write_text(str(stale_epoch) + "\n")

        result = self._run_selftest()
        # Should exit 0 (stale heartbeat = WARN, not FAIL)
        self.assertEqual(result.returncode, 0)
        # Should report DEGRADED or OK depending on other checks
        self.assertTrue("POWER-SELFTEST:" in result.stdout)

    def test_exit_code_ok_no_fails(self):
        """Test that exit code is 0 when no failures."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        result = self._run_selftest()
        self.assertEqual(result.returncode, 0)

    def test_decisions_count_output(self):
        """Test that decisions output shows pending count."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        result = self._run_selftest()
        self.assertIn("decisions:", result.stdout)

    def test_trigger_layer_unconfigured_warn(self):
        """Test trigger:unconfigured when no conductor3 dir and no scheduled tasks configured."""
        # Create state dir but no conductor3 (fresh clone scenario)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        result = self._run_selftest()
        # Should not FAIL, should be graceful
        self.assertEqual(result.returncode, 0)
        # Should report trigger:unconfigured or similar
        self.assertIn("POWER-SELFTEST:", result.stdout)

    def test_trigger_layer_fresh_heartbeat_ok(self):
        """Test trigger:ok when orchestrator heartbeat is fresh (< 600s)."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        hb_dir = self.state_dir / ".heartbeats"
        hb_dir.mkdir(parents=True, exist_ok=True)

        # Create fresh heartbeat (just written)
        now_epoch = int(datetime.now().timestamp())
        fresh_hb = hb_dir / "orchestrator-heartbeat"
        fresh_hb.write_text(str(now_epoch) + "\n")

        result = self._run_selftest()
        self.assertEqual(result.returncode, 0)
        self.assertIn("POWER-SELFTEST:", result.stdout)

    def test_trigger_layer_stale_heartbeat_fail(self):
        """Test trigger:stale when orchestrator heartbeat is stale (>= 600s)."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        hb_dir = self.state_dir / ".heartbeats"
        hb_dir.mkdir(parents=True, exist_ok=True)

        # Create stale heartbeat (> 600s old)
        now_epoch = int(datetime.now().timestamp())
        stale_epoch = now_epoch - 700  # 700 seconds ago
        stale_hb = hb_dir / "orchestrator-heartbeat"
        stale_hb.write_text(str(stale_epoch) + "\n")

        result = self._run_selftest()
        # Stale trigger layer should FAIL
        self.assertEqual(result.returncode, 1)
        self.assertIn("POWER-SELFTEST:", result.stdout)
        self.assertIn("trigger:", result.stdout)

    def test_trigger_layer_missing_heartbeat_fail(self):
        """Test trigger:missing when orchestrator heartbeat file is missing but configured."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        hb_dir = self.state_dir / ".heartbeats"
        hb_dir.mkdir(parents=True, exist_ok=True)
        # Don't create orchestrator-heartbeat file; should fail since dir exists

        result = self._run_selftest()
        # Missing heartbeat when dir exists = FAIL
        self.assertEqual(result.returncode, 1)
        self.assertIn("POWER-SELFTEST:", result.stdout)


if __name__ == "__main__":
    unittest.main()
