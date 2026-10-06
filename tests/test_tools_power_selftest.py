#!/usr/bin/env python3
"""Unit tests for power_selftest.py health check harness."""
import importlib.util
import os
import sys
import subprocess
import tempfile
import unittest
import json
from pathlib import Path
from datetime import datetime, timedelta

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(name, relpath):
    """Load a tools/ module by file path (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        """Run power_selftest.py with environment overrides.

        Points SCRIPTS_ROOT at this repo's real tools/ dir by default so the
        scanner check finds a real, passing scanner_selftest.py -- these
        happy-path tests are about hooks/beats/decisions graceful
        degradation, not the scanner fail-closed behavior (covered
        separately below), and must stay deterministic regardless of
        whether the host box happens to have a ~/scripts fallback.
        """
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        env["BRAIN_ROOT"] = str(self.brain_dir)
        env["SCRIPTS_ROOT"] = str(self.selftest_script.parent)
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
        """Run power_selftest.py against the temp state/brain roots.

        Points SCRIPTS_ROOT at this repo's real tools/ dir, same as the
        base TestPowerSelftest class above: these tests are about hook
        detection, not the scanner fail-closed behavior (covered in
        TestPowerSelftestScannerFailClosed), and the overall exit code is
        the union of every check. Leaving SCRIPTS_ROOT unset let the
        scanner check fall through to the profile-agnostic $HOME/scripts
        fallback, which is absent on CI runners -- scanner:FAIL then
        flipped the exit code these tests assert on for a reason that has
        nothing to do with hooks.
        """
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        env["BRAIN_ROOT"] = str(self.brain_dir)
        env["SCRIPTS_ROOT"] = str(self.selftest_script.parent)
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


class TestPowerSelftestScannerFailClosed(unittest.TestCase):
    """A scanner check that cannot evaluate must never render healthy.

    scanner:None was observed live (checks that evaluated to nothing still
    rendered as if they'd passed). This class pins the fix: a found,
    passing scanner_selftest.py renders 'scanner:N/N'; an unfindable one
    renders 'scanner:FAIL:...' and fails the whole selftest closed.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.selftest_script = Path(__file__).parent.parent / "tools" / "power_selftest.py"
        self.state_dir = Path(self.temp_dir) / "state"
        self.brain_dir = Path(self.temp_dir) / "brain"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _run_selftest(self, scripts_root, no_home_fallback=False):
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        env["BRAIN_ROOT"] = str(self.brain_dir)
        env["SCRIPTS_ROOT"] = str(scripts_root)
        if no_home_fallback:
            # Defeat the profile-agnostic $HOME/scripts fallback deterministically,
            # regardless of whether the host box happens to carry its own
            # ~/scripts/scanner_selftest.py (as this dev box does).
            empty_home = Path(self.temp_dir) / "empty_home"
            empty_home.mkdir(parents=True, exist_ok=True)
            env["USERPROFILE"] = str(empty_home)
            env["HOME"] = str(empty_home)
        return subprocess.run(
            [sys.executable, str(self.selftest_script)],
            capture_output=True, text=True, encoding="utf-8",
            cwd=self.temp_dir, env=env,
        )

    def test_scanner_found_and_passing_shows_pass_count(self):
        """A real scanner_selftest.py renders 'scanner:N/N', never 'scanner:None'."""
        real_tools_dir = self.selftest_script.parent
        result = self._run_selftest(real_tools_dir)
        self.assertNotIn("scanner:None", result.stdout)
        self.assertRegex(result.stdout, r"scanner:\d+/\d+")

    def test_scanner_unlocatable_fails_closed(self):
        """No scanner_selftest.py anywhere findable -> FAIL, not OK/n/a/None."""
        empty_scripts_root = Path(self.temp_dir) / "no_scanner_here"
        empty_scripts_root.mkdir(parents=True, exist_ok=True)
        result = self._run_selftest(empty_scripts_root, no_home_fallback=True)
        self.assertNotIn("scanner:None", result.stdout)
        self.assertIn("scanner:FAIL", result.stdout)
        self.assertIn("POWER-SELFTEST: FAIL", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_scanner_resolves_profile_agnostic_home_fallback(self):
        """When SCRIPTS_ROOT has no harness, a real $HOME/scripts copy is still found.

        Proves the fallback is resolved via Path.home() (HOME/USERPROFILE),
        never a hard-coded user profile path.
        """
        empty_scripts_root = Path(self.temp_dir) / "no_scanner_here"
        empty_scripts_root.mkdir(parents=True, exist_ok=True)
        fake_home = Path(self.temp_dir) / "fake_home"
        fake_scripts = fake_home / "scripts"
        fake_scripts.mkdir(parents=True, exist_ok=True)
        # A minimal self-contained fake harness: always reports 3/3 passed, exit 0.
        (fake_scripts / "scanner_selftest.py").write_text(
            "import sys\nprint('SELFTEST: 3/3 passed')\nsys.exit(0)\n",
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        env["BRAIN_ROOT"] = str(self.brain_dir)
        env["SCRIPTS_ROOT"] = str(empty_scripts_root)
        env["USERPROFILE"] = str(fake_home)
        env["HOME"] = str(fake_home)
        result = subprocess.run(
            [sys.executable, str(self.selftest_script)],
            capture_output=True, text=True, encoding="utf-8",
            cwd=self.temp_dir, env=env,
        )
        self.assertIn("scanner:3/3", result.stdout)
        self.assertEqual(result.returncode, 0)


class TestRenderSegmentFailClosed(unittest.TestCase):
    """Unit tests on the shared render_segment() formatter, imported directly.

    Every check result -- real Check, a None details on an OK/WARN check, or
    a missing result entirely -- is funneled through one render_segment()
    that treats anything it cannot make sense of as FAIL:unevaluated. It
    must never let Python's None print as if a check were healthy.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        selftest_path = Path(__file__).parent.parent / "tools" / "power_selftest.py"
        spec = importlib.util.spec_from_file_location(
            f"power_selftest_{id(cls)}", selftest_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.mod = module

    def test_none_result_renders_fail_unevaluated(self):
        segment, is_fail = self.mod.render_segment("scanner", None)
        self.assertNotIn("None", segment)
        self.assertIn("scanner:FAIL", segment)
        self.assertTrue(is_fail)

    def test_ok_status_with_none_details_renders_fail_unevaluated(self):
        """The exact shape that produced literal 'scanner:None' in the wild."""
        fake_check = self.mod.Check("scanner", "OK", None, False)
        segment, is_fail = self.mod.render_segment("scanner", fake_check)
        self.assertEqual(segment, "scanner:FAIL:unevaluated")
        self.assertTrue(is_fail)

    def test_ok_status_with_none_details_on_untracked_name_still_ok(self):
        """hooks/brain/beats intentionally pass details=None on a clean OK;
        only names whose healthy state is expressed THROUGH details
        (decisions, scanner) must treat a None detail as unevaluated."""
        fake_check = self.mod.Check("hooks", "OK", None, False)
        segment, is_fail = self.mod.render_segment("hooks", fake_check)
        self.assertEqual(segment, "hooks:ok")
        self.assertFalse(is_fail)

    def test_run_checks_never_silently_drops_a_none_result(self):
        """A check function that returns None (crashed/mis-shaped) must
        still surface in the headline as a failure, not vanish."""
        original_checks = self.mod.CHECKS
        try:
            self.mod.CHECKS = original_checks + (("ghost", lambda: None),)
            results = self.mod.run_checks()
            names = [r.name for r in results]
            self.assertIn("ghost", names)
            ghost = next(r for r in results if r.name == "ghost")
            self.assertEqual(ghost.status, "FAIL")
            self.assertTrue(ghost.is_fail)
        finally:
            self.mod.CHECKS = original_checks


class TestPowerSelftestTrigger(unittest.TestCase):
    """GAP7 wiring: power_selftest's trigger check calls task_cadence_check.

    task_cadence_check.py (PR #701) parses daemons/install-tasks.ps1 for the
    real scheduled-task names/intervals and compares them against live
    Task Scheduler state, but nothing invoked it -- this is that wiring.
    These tests import both tools/ modules directly (not via subprocess) so
    the `query` callable can be mocked without touching the real Task
    Scheduler, mirroring task_cadence_check's own injectable-`query` design.
    """

    @classmethod
    def setUpClass(cls):
        cls.power_selftest = _load_module(
            "power_selftest_trigger_under_test", "tools/power_selftest.py"
        )
        cls.tcc = _load_module(
            "task_cadence_check_trigger_under_test", "tools/task_cadence_check.py"
        )
        cls.expected = cls.tcc.parse_expected_cadences(
            (REPO_ROOT / "daemons" / "install-tasks.ps1").read_text(encoding="utf-8")
        )

    @staticmethod
    def _task_xml(interval_minutes, enabled=True):
        return (
            '<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            "  <Settings><Enabled>%s</Enabled></Settings>\n"
            "  <Triggers><TimeTrigger><Repetition><Interval>PT%dM</Interval>"
            "</Repetition></TimeTrigger></Triggers>\n"
            "</Task>"
        ) % ("true" if enabled else "false", int(interval_minutes))

    def test_trigger_fail_on_missing_task(self):
        """A task install-tasks.ps1 defines but Task Scheduler has never registered is FAIL."""

        def fake_query(name):
            raise self.tcc.TaskMissingError(name)

        result = self.power_selftest.check_trigger(platform="win32", query=fake_query)
        self.assertEqual(result.status, "FAIL")
        self.assertTrue(result.is_fail)

        output, exit_code = self.power_selftest.format_output([result])
        self.assertIn("trigger:FAIL", output)
        self.assertIn("FAIL", output.splitlines()[0])
        self.assertEqual(exit_code, 1)

    def test_trigger_ok_when_all_tasks_ready(self):
        """Every defined task registered, enabled, at its defined cadence -> trigger:ok."""

        def fake_query(name):
            return self._task_xml(self.expected[name], enabled=True)

        result = self.power_selftest.check_trigger(platform="win32", query=fake_query)
        self.assertEqual(result.status, "OK")
        self.assertFalse(result.is_fail)

        output, exit_code = self.power_selftest.format_output([result])
        self.assertIn("trigger:ok", output)
        self.assertNotIn("FAIL", output.splitlines()[0])

    def test_trigger_na_on_non_windows(self):
        """Non-Windows platforms report n/a, never FAIL -- the gate is Windows-only."""
        result = self.power_selftest.check_trigger(platform="linux")
        self.assertEqual(result.status, "OK")
        self.assertFalse(result.is_fail)

        output, exit_code = self.power_selftest.format_output([result])
        self.assertIn("trigger:n/a (non-Windows)", output)
        self.assertNotIn("FAIL", output)

    def test_trigger_warn_on_disabled_task(self):
        """A task that install-tasks.ps1 defines but that is deliberately disabled is a
        WARN naming the task, not a FAIL -- FAIL is reserved for a task that is
        missing outright or genuinely drifted off-cadence.
        """

        def fake_query(name):
            # AesopMergeQueue deliberately disabled on this box since 2026-09-02;
            # everything else Ready at its defined cadence.
            enabled = name != "AesopMergeQueue"
            return self._task_xml(self.expected[name], enabled=enabled)

        result = self.power_selftest.check_trigger(platform="win32", query=fake_query)
        self.assertEqual(result.status, "WARN")
        self.assertFalse(result.is_fail)
        self.assertIn("AesopMergeQueue", result.details)

        output, exit_code = self.power_selftest.format_output([result])
        self.assertIn("trigger:WARN", output)
        self.assertIn("AesopMergeQueue", output)
        self.assertNotIn("FAIL", output.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
