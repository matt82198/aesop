#!/usr/bin/env python3
"""Tests for orchestrator_context_meter.py.

Test isolation: uses tempfile dirs, no cwd/global-git pollution.
No sleeps: timestamps are injected via mocking/patching.
Subprocess-safe: uses sys.executable + explicit timeouts if needed.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# Import the meter module
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
import orchestrator_context_meter as meter


class TestCheckpointAge(unittest.TestCase):
    """Test checkpoint age computation."""

    def setUp(self):
        """Create temp directories and set state root."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cwd_dir = Path(self.temp_dir.name) / "cwd"
        self.cwd_dir.mkdir(parents=True, exist_ok=True)

        # Save original cwd and env
        self.original_cwd = os.getcwd()
        self.original_state_root = os.environ.get("AESOP_STATE_ROOT")

        # Set new cwd and state root
        os.chdir(self.cwd_dir)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        """Restore original state."""
        os.chdir(self.original_cwd)
        if self.original_state_root:
            os.environ["AESOP_STATE_ROOT"] = self.original_state_root
        elif "AESOP_STATE_ROOT" in os.environ:
            del os.environ["AESOP_STATE_ROOT"]
        self.temp_dir.cleanup()

    def test_missing_state_md(self):
        """Test with missing STATE.md."""
        # Create BUILDLOG.md but not STATE.md
        buildlog = self.state_dir / "BUILDLOG.md"
        buildlog.write_text("# BUILDLOG\n")

        age_s, status = meter.compute_checkpoint_age(meter.get_checkpoint_files())
        self.assertIsNone(age_s)
        self.assertIn("missing", status.lower())

    def test_missing_buildlog_md(self):
        """Test with missing BUILDLOG.md."""
        # Create STATE.md but not BUILDLOG.md
        state_file = self.cwd_dir / "STATE.md"
        state_file.write_text("# STATE\n")

        age_s, status = meter.compute_checkpoint_age(meter.get_checkpoint_files())
        self.assertIsNone(age_s)
        self.assertIn("missing", status.lower())

    @mock.patch("time.time")
    def test_fresh_checkpoint(self, mock_time):
        """Test with fresh checkpoint (< 1 hour old)."""
        now = 1000000
        mock_time.return_value = now

        # Create checkpoint files 10 minutes ago
        checkpoint_time = now - 600

        state_file = self.cwd_dir / "STATE.md"
        buildlog = self.state_dir / "BUILDLOG.md"
        state_file.write_text("# STATE\n")
        buildlog.write_text("# BUILDLOG\n")

        # Set mtime to 10 minutes ago
        os.utime(str(state_file), (checkpoint_time, checkpoint_time))
        os.utime(str(buildlog), (checkpoint_time, checkpoint_time))

        age_s, status = meter.compute_checkpoint_age(meter.get_checkpoint_files())
        self.assertIsNotNone(age_s)
        self.assertEqual(status, "OK")
        self.assertAlmostEqual(age_s, 600, delta=2)

    @mock.patch("time.time")
    def test_stale_checkpoint(self, mock_time):
        """Test with stale checkpoint (> 12 hours old)."""
        now = 1000000
        mock_time.return_value = now

        # Create checkpoint files 24 hours ago
        checkpoint_time = now - (24 * 3600)

        state_file = self.cwd_dir / "STATE.md"
        buildlog = self.state_dir / "BUILDLOG.md"
        state_file.write_text("# STATE\n")
        buildlog.write_text("# BUILDLOG\n")

        os.utime(str(state_file), (checkpoint_time, checkpoint_time))
        os.utime(str(buildlog), (checkpoint_time, checkpoint_time))

        age_s, status = meter.compute_checkpoint_age(meter.get_checkpoint_files())
        self.assertIsNotNone(age_s)
        self.assertEqual(status, "OK")
        self.assertGreater(age_s, 12 * 3600)


class TestActivityCount(unittest.TestCase):
    """Test activity counting since checkpoint."""

    def setUp(self):
        """Create temp directories."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cwd_dir = Path(self.temp_dir.name) / "cwd"
        self.cwd_dir.mkdir(parents=True, exist_ok=True)

        self.original_cwd = os.getcwd()
        self.original_state_root = os.environ.get("AESOP_STATE_ROOT")

        os.chdir(self.cwd_dir)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        """Restore original state."""
        os.chdir(self.original_cwd)
        if self.original_state_root:
            os.environ["AESOP_STATE_ROOT"] = self.original_state_root
        elif "AESOP_STATE_ROOT" in os.environ:
            del os.environ["AESOP_STATE_ROOT"]
        self.temp_dir.cleanup()

    @mock.patch("time.time")
    def test_little_activity_since_checkpoint(self, mock_time):
        """Test with little activity since checkpoint."""
        now = 1000000
        mock_time.return_value = now

        checkpoint_time = now - 3600  # 1 hour ago

        # Create checkpoint files
        state_file = self.cwd_dir / "STATE.md"
        buildlog = self.state_dir / "BUILDLOG.md"
        state_file.write_text("# STATE\n")
        buildlog.write_text("# BUILDLOG\n")

        os.utime(str(state_file), (checkpoint_time, checkpoint_time))
        os.utime(str(buildlog), (checkpoint_time, checkpoint_time))

        checkpoint_files = meter.get_checkpoint_files()
        activity, status = meter.count_activity_since_checkpoint(checkpoint_files, self.state_dir)

        self.assertEqual(status, "OK")
        self.assertLessEqual(activity, 5)  # Should be very low

    @mock.patch("time.time")
    def test_many_activities_since_checkpoint(self, mock_time):
        """Test with many activities since checkpoint."""
        now = 1000000
        mock_time.return_value = now

        checkpoint_time = now - 3600  # 1 hour ago

        # Create checkpoint files
        state_file = self.cwd_dir / "STATE.md"
        buildlog = self.state_dir / "BUILDLOG.md"
        state_file.write_text("# STATE\n")
        buildlog.write_text("# BUILDLOG\n")

        os.utime(str(state_file), (checkpoint_time, checkpoint_time))
        os.utime(str(buildlog), (checkpoint_time, checkpoint_time))

        # Create activity log files newer than checkpoint
        activity_log = self.state_dir / "ACTIVITY.log"
        activity_log.write_text("activity line 1\nactivity line 2\nactivity line 3\n")
        os.utime(str(activity_log), (now - 300, now - 300))

        # Update orchestrator status to be post-checkpoint
        status_file = self.state_dir / "orchestrator-status.json"
        status_file.write_text('{"activity": "test"}')
        os.utime(str(status_file), (now - 100, now - 100))

        checkpoint_files = meter.get_checkpoint_files()
        activity, status = meter.count_activity_since_checkpoint(checkpoint_files, self.state_dir)

        self.assertEqual(status, "OK")
        # Should detect at least status file update + activity log
        self.assertGreaterEqual(activity, 2)


class TestTokenLedger(unittest.TestCase):
    """Test token ledger reading."""

    def setUp(self):
        """Create temp directories."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.original_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        """Restore original state."""
        if self.original_state_root:
            os.environ["AESOP_STATE_ROOT"] = self.original_state_root
        elif "AESOP_STATE_ROOT" in os.environ:
            del os.environ["AESOP_STATE_ROOT"]
        self.temp_dir.cleanup()

    def test_missing_ledger(self):
        """Test with missing token ledger."""
        tokens, status = meter.read_token_ledger(self.state_dir)
        self.assertIsNone(tokens)
        self.assertEqual(status, "UNAVAILABLE")

    def test_read_ledger(self):
        """Test reading token ledger."""
        ledger_dir = self.state_dir / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = ledger_dir / "fleet-usage.jsonl"

        # Write sample ledger entries
        entries = [
            {"tokens": 100, "model": "haiku"},
            {"tokens": 250, "model": "sonnet"},
            {"tokens": 500, "model": "opus"},
        ]
        with open(ledger_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        tokens, status = meter.read_token_ledger(self.state_dir)
        self.assertEqual(status, "OK")
        self.assertEqual(tokens, 850)


class TestVerdictEvaluation(unittest.TestCase):
    """Test verdict logic."""

    def test_ok_verdict(self):
        """Test OK verdict."""
        verdict = meter.evaluate_verdict(
            checkpoint_age_s=600,  # 10 minutes
            activity_count=3,
            checkpoint_hours_threshold=4,
            activity_checkpoint_threshold=10,
            activity_clear_threshold=20,
        )
        self.assertEqual(verdict[0], "OK")

    def test_advise_checkpoint_by_age(self):
        """Test ADVISE-CHECKPOINT due to age."""
        verdict = meter.evaluate_verdict(
            checkpoint_age_s=5 * 3600,  # 5 hours
            activity_count=3,
            checkpoint_hours_threshold=4,
            activity_checkpoint_threshold=10,
            activity_clear_threshold=20,
        )
        self.assertEqual(verdict[0], "ADVISE-CHECKPOINT")

    def test_advise_checkpoint_by_activity(self):
        """Test ADVISE-CHECKPOINT due to activity."""
        verdict = meter.evaluate_verdict(
            checkpoint_age_s=3600,  # 1 hour
            activity_count=15,
            checkpoint_hours_threshold=4,
            activity_checkpoint_threshold=10,
            activity_clear_threshold=20,
        )
        self.assertEqual(verdict[0], "ADVISE-CHECKPOINT")

    def test_advise_clear_by_age(self):
        """Test ADVISE-CLEAR due to age."""
        verdict = meter.evaluate_verdict(
            checkpoint_age_s=13 * 3600,  # 13 hours
            activity_count=3,
            checkpoint_hours_threshold=4,
            activity_checkpoint_threshold=10,
            activity_clear_threshold=20,
        )
        self.assertEqual(verdict[0], "ADVISE-CLEAR")

    def test_advise_clear_by_activity(self):
        """Test ADVISE-CLEAR due to activity."""
        verdict = meter.evaluate_verdict(
            checkpoint_age_s=3600,  # 1 hour
            activity_count=25,
            checkpoint_hours_threshold=4,
            activity_checkpoint_threshold=10,
            activity_clear_threshold=20,
        )
        self.assertEqual(verdict[0], "ADVISE-CLEAR")


class TestReadOnlyProperty(unittest.TestCase):
    """Test that meter does not mutate state."""

    def setUp(self):
        """Create temp state."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cwd_dir = Path(self.temp_dir.name) / "cwd"
        self.cwd_dir.mkdir(parents=True, exist_ok=True)

        self.original_cwd = os.getcwd()
        self.original_state_root = os.environ.get("AESOP_STATE_ROOT")

        os.chdir(self.cwd_dir)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        """Restore original state."""
        os.chdir(self.original_cwd)
        if self.original_state_root:
            os.environ["AESOP_STATE_ROOT"] = self.original_state_root
        elif "AESOP_STATE_ROOT" in os.environ:
            del os.environ["AESOP_STATE_ROOT"]
        self.temp_dir.cleanup()

    def test_no_file_mutations(self):
        """Test that meter does not modify any files."""
        # Create fixture state
        state_file = self.cwd_dir / "STATE.md"
        buildlog = self.state_dir / "BUILDLOG.md"
        state_file.write_text("# STATE\n")
        buildlog.write_text("# BUILDLOG\n")

        # Record original mtimes
        state_mtime_before = state_file.stat().st_mtime
        buildlog_mtime_before = buildlog.stat().st_mtime

        # Run meter
        checkpoint_files = meter.get_checkpoint_files()
        meter.compute_checkpoint_age(checkpoint_files)
        meter.count_activity_since_checkpoint(checkpoint_files, self.state_dir)
        meter.read_token_ledger(self.state_dir)

        # Verify no mutations
        state_mtime_after = state_file.stat().st_mtime
        buildlog_mtime_after = buildlog.stat().st_mtime

        self.assertEqual(state_mtime_before, state_mtime_after)
        self.assertEqual(buildlog_mtime_before, buildlog_mtime_after)


class TestCLIInterface(unittest.TestCase):
    """Test CLI argument parsing and output."""

    def setUp(self):
        """Create temp directories."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cwd_dir = Path(self.temp_dir.name) / "cwd"
        self.cwd_dir.mkdir(parents=True, exist_ok=True)

        self.original_cwd = os.getcwd()
        self.original_state_root = os.environ.get("AESOP_STATE_ROOT")

        os.chdir(self.cwd_dir)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        """Restore original state."""
        os.chdir(self.original_cwd)
        if self.original_state_root:
            os.environ["AESOP_STATE_ROOT"] = self.original_state_root
        elif "AESOP_STATE_ROOT" in os.environ:
            del os.environ["AESOP_STATE_ROOT"]
        self.temp_dir.cleanup()

    def test_cli_default_mode_ok(self):
        """Test CLI in default mode with OK verdict."""
        # Create checkpoint files
        state_file = self.cwd_dir / "STATE.md"
        buildlog = self.state_dir / "BUILDLOG.md"
        state_file.write_text("# STATE\n")
        buildlog.write_text("# BUILDLOG\n")

        # Get path to meter script (parent project directory)
        meter_script = Path(__file__).parent.parent / "tools" / "orchestrator_context_meter.py"

        # Run CLI from the test's cwd directory
        result = subprocess.run(
            [sys.executable, str(meter_script)],
            cwd=str(self.cwd_dir),
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "AESOP_STATE_ROOT": str(self.state_dir)},
        )

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("OK", result.stdout)

    def test_cli_json_output(self):
        """Test CLI JSON output format."""
        # Create checkpoint files
        state_file = self.cwd_dir / "STATE.md"
        buildlog = self.state_dir / "BUILDLOG.md"
        state_file.write_text("# STATE\n")
        buildlog.write_text("# BUILDLOG\n")

        # Get path to meter script
        meter_script = Path(__file__).parent.parent / "tools" / "orchestrator_context_meter.py"

        # Run CLI with --json
        result = subprocess.run(
            [sys.executable, str(meter_script), "--json"],
            cwd=str(self.cwd_dir),
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "AESOP_STATE_ROOT": str(self.state_dir)},
        )

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # Parse JSON to verify format
        output_json = json.loads(result.stdout)
        self.assertIn("verdict", output_json)
        self.assertIn("reason", output_json)
        self.assertIn("signals", output_json)

    def test_cli_error_mode(self):
        """Test CLI error exit code when checkpoint is missing."""
        # Don't create any checkpoint files

        # Run CLI
        result = subprocess.run(
            [sys.executable, "tools/orchestrator_context_meter.py"],
            cwd=self.cwd_dir.parent,
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 2)  # Error exit code


if __name__ == "__main__":
    unittest.main()
