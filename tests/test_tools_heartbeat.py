#!/usr/bin/env python3
"""Unit tests for heartbeat.py liveness registry."""
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestHeartbeat(unittest.TestCase):
    """Test cases for heartbeat.py beat and check commands."""

    def setUp(self):
        """Create temporary directories for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.hb_script = Path(__file__).parent.parent / "tools" / "heartbeat.py"
        self.state_dir = Path(self.temp_dir) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up temporary directories."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _run_hb(self, *args, env_overrides=None):
        """Run heartbeat.py with arguments."""
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        if env_overrides:
            env.update(env_overrides)

        cmd = [sys.executable, str(self.hb_script)] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return result

    def test_beat_writes_heartbeat(self):
        """Test that beat writes heartbeat file."""
        result = self._run_hb("beat", "test_service")
        self.assertEqual(result.returncode, 0)
        self.assertIn("written to state", result.stdout)

        # Verify file was created
        hb_file = self.state_dir / "heartbeats" / "test_service"
        self.assertTrue(hb_file.exists())

    def test_beat_with_status(self):
        """Test beat with optional status word."""
        result = self._run_hb("beat", "test_service", "running")
        self.assertEqual(result.returncode, 0)

        # Verify file contains epoch and status
        hb_file = self.state_dir / "heartbeats" / "test_service"
        content = hb_file.read_text().strip()
        lines = content.split('\n')
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1], "running")

    def test_check_empty_registry(self):
        """Test check with no heartbeat files in state dir."""
        # Use a very high max-age to ignore any brain/.heartbeats that may exist
        result = self._run_hb("check", "--max-age", "99999999")
        # Should exit 0 (no fresh heartbeats to fail on)
        self.assertEqual(result.returncode, 0)

    def test_check_single_fresh_beat(self):
        """Test check with single fresh heartbeat."""
        # Write a fresh beat
        self._run_hb("beat", "service1")

        # Check should report ALIVE
        # Use high max-age to avoid failure from potentially stale brain beats
        result = self._run_hb("check", "--max-age", "99999999")
        self.assertEqual(result.returncode, 0)
        self.assertIn("state/service1", result.stdout)
        self.assertIn("ALIVE", result.stdout)

    def test_check_stale_heartbeat(self):
        """Test stale heartbeat detection (> max-age)."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        hb_dir = self.state_dir / "heartbeats"
        hb_dir.mkdir(parents=True, exist_ok=True)

        # Write stale heartbeat (400 seconds old)
        import time
        stale_epoch = int(time.time()) - 400
        stale_hb = hb_dir / "stale_service"
        stale_hb.write_text(str(stale_epoch) + "\n")

        # Check should report STALE and exit 1
        result = self._run_hb("check", "--max-age", "300")
        self.assertEqual(result.returncode, 1)
        self.assertIn("STALE", result.stdout)


    def test_multiple_heartbeats_mixed(self):
        """Test multiple heartbeats with mixed fresh/stale."""
        hb_dir = self.state_dir / "heartbeats"
        hb_dir.mkdir(parents=True, exist_ok=True)

        import time
        now = time.time()

        # Fresh beat
        fresh = hb_dir / "fresh"
        fresh.write_text(str(int(now)) + "\n")

        # Stale beat (350s old)
        stale = hb_dir / "stale"
        stale.write_text(str(int(now - 350)) + "\n")

        # Check should report both and exit 1 due to stale
        result = self._run_hb("check", "--max-age", "300")
        self.assertEqual(result.returncode, 1)
        self.assertIn("fresh", result.stdout)
        self.assertIn("stale", result.stdout)

    def test_graceful_degradation_missing_state_dir(self):
        """Test graceful degradation when state directory doesn't exist."""
        bad_state = Path(self.temp_dir) / "nonexistent"
        env = {"AESOP_STATE_ROOT": str(bad_state)}
        # Use high max-age to ignore stale brain beats
        result = self._run_hb("check", "--max-age", "99999999", env_overrides=env)
        # Should exit 0 (no fresh state heartbeats found)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
