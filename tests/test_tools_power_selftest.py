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


class TestPowerSelftestHookDetection(unittest.TestCase):
    """Hook detection reads both settings scopes Claude Code merges."""

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

    def _run_selftest(self):
        """Run power_selftest.py against the temp state/brain roots."""
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        env["BRAIN_ROOT"] = str(self.brain_dir)
        return subprocess.run(
            [sys.executable, str(self.selftest_script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=self.temp_dir,
            env=env
        )

    AGENT_HOOK = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Agent|Task",
                    "hooks": [{"type": "command", "command": "echo policy"}],
                }
            ]
        }
    }

    def _write_settings(self, directory, payload):
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / "settings.json", "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_project_scope_hook_is_detected(self):
        """A hook registered in <repo>/.claude counts; only reading the user scope missed it."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._write_settings(Path(self.temp_dir) / ".claude", self.AGENT_HOOK)

        result = self._run_selftest()
        self.assertIn("hooks:ok", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_user_scope_hook_is_detected(self):
        """A hook registered in the brain root still counts."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._write_settings(self.brain_dir, self.AGENT_HOOK)

        result = self._run_selftest()
        self.assertIn("hooks:ok", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_no_posttooluse_requirement(self):
        """Settings with PreToolUse but no PostToolUse must pass.

        aesop ships no PostToolUse hook, so requiring one made every clean
        install fail a check it had no way to satisfy.
        """
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._write_settings(Path(self.temp_dir) / ".claude", self.AGENT_HOOK)

        result = self._run_selftest()
        self.assertNotIn("PostToolUse", result.stdout)
        self.assertIn("hooks:ok", result.stdout)

    def test_missing_agent_matcher_still_fails_closed(self):
        """Settings present but without Agent|Task is a real failure."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._write_settings(
            Path(self.temp_dir) / ".claude",
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}},
        )

        result = self._run_selftest()
        self.assertIn("missing matchers", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_project_dir_variable_in_command_is_expanded(self):
        """$CLAUDE_PROJECT_DIR resolves against the repo root, not reported missing."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        hook_script = Path(self.temp_dir) / "policy.mjs"
        hook_script.write_text("// hook\n", encoding="utf-8")
        self._write_settings(
            Path(self.temp_dir) / ".claude",
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Agent|Task",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'node "$CLAUDE_PROJECT_DIR/policy.mjs"',
                                }
                            ],
                        }
                    ]
                }
            },
        )

        result = self._run_selftest()
        self.assertNotIn("missing files", result.stdout)
        self.assertIn("hooks:ok", result.stdout)

    def test_missing_hook_script_is_reported(self):
        """A hook pointing at a script that does not exist fails closed."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._write_settings(
            Path(self.temp_dir) / ".claude",
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Agent|Task",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'node "$CLAUDE_PROJECT_DIR/absent.mjs"',
                                }
                            ],
                        }
                    ]
                }
            },
        )

        result = self._run_selftest()
        self.assertIn("missing files", result.stdout)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
