#!/usr/bin/env python3
"""
Test suite for toolchain_health.py.

Tests verify:
  - Broken binaries (exist but cannot execute) are detected as BROKEN
  - Missing binaries are detected as MISSING
  - Stale heartbeats are reported as STALE when StateAPI unavailable
  - Zero-checks-performed exits non-zero
  - Healthy state exits 0
  - JSON output is valid and structured correctly

All tests use fixtures and never modify committed code or git config.
"""

import ast
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
import tempfile

# Ensure tools directory is on path
REPO_ROOT = Path(__file__).parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import toolchain_health

TOOLCHAIN_SOURCE = TOOLS_DIR / "toolchain_health.py"

_STATE_TMP = None
_PRIOR_STATE_ROOT = None


def setUpModule():
    """Point AESOP_STATE_ROOT at a temp dir.

    run_checks() now builds a real ReadAPI, which mkdirs its state directory;
    without this the suite would create ./state in whatever cwd it runs from.
    """
    global _STATE_TMP, _PRIOR_STATE_ROOT
    _PRIOR_STATE_ROOT = os.environ.get("AESOP_STATE_ROOT")
    _STATE_TMP = tempfile.TemporaryDirectory()
    os.environ["AESOP_STATE_ROOT"] = _STATE_TMP.name


def tearDownModule():
    global _STATE_TMP, _PRIOR_STATE_ROOT
    if _PRIOR_STATE_ROOT is None:
        os.environ.pop("AESOP_STATE_ROOT", None)
    else:
        os.environ["AESOP_STATE_ROOT"] = _PRIOR_STATE_ROOT
    if _STATE_TMP is not None:
        _STATE_TMP.cleanup()
        _STATE_TMP = None


class TestStateAPIImportIsLive(unittest.TestCase):
    """The heartbeat checks must actually RUN, not silently fall back to skipped.

    Regression for the dead-check escape: toolchain_health imported
    `StateReadAPI` from state_store.read_api, but the class there is `ReadAPI`.
    The import therefore raised on every run and fell into the
    `= None` fallback, so both heartbeat checks were permanently reported as
    "unavailable" rather than being evaluated.
    """

    def test_imported_state_store_symbols_exist(self):
        """Every name imported from state_store.read_api must exist in it."""
        import state_store.read_api as read_api_mod

        tree = ast.parse(TOOLCHAIN_SOURCE.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "state_store.read_api":
                imported.extend(alias.name for alias in node.names)

        self.assertTrue(
            imported,
            "toolchain_health must import the StateAPI read facade from state_store.read_api",
        )
        for name in imported:
            self.assertTrue(
                hasattr(read_api_mod, name),
                "toolchain_health imports '%s' from state_store.read_api, but that "
                "module defines no such symbol -- the import raises and the "
                "heartbeat checks are silently skipped" % name,
            )

    def test_facade_is_bound_not_none(self):
        """The module global must be the real facade class, not the None fallback."""
        from state_store.read_api import ReadAPI as FacadeReadAPI

        self.assertIsNotNone(
            toolchain_health.ReadAPI,
            "ReadAPI fell back to None: the heartbeat checks are dead",
        )
        self.assertIs(toolchain_health.ReadAPI, FacadeReadAPI)

    def test_run_checks_passes_a_live_state_api_to_heartbeat(self):
        """run_checks must hand check_heartbeat a real facade instance."""
        with mock.patch("toolchain_health.check_binary") as mock_binary:
            with mock.patch("toolchain_health.check_heartbeat") as mock_hb:
                mock_binary.return_value = (True, None)
                mock_hb.return_value = (True, None)

                toolchain_health.run_checks(json_mode=False, max_age_seconds=300)

        self.assertTrue(mock_hb.call_args_list, "no heartbeat check ran at all")
        for call in mock_hb.call_args_list:
            state_api = call[0][3] if len(call[0]) > 3 else call[1].get("state_api")
            self.assertIsNotNone(
                state_api,
                "check_heartbeat received state_api=None: the check is skipped, not live",
            )

    def test_facade_exposes_the_method_the_check_calls(self):
        """check_heartbeat_fresh must exist on the facade with a usable signature."""
        self.assertTrue(hasattr(toolchain_health.ReadAPI, "check_heartbeat_fresh"))


class TestBinaryChecks(unittest.TestCase):
    """Tests for binary detection and execution checking."""

    def test_missing_binary(self):
        """Test that a missing binary is reported as MISSING."""
        is_ok, message = toolchain_health.check_binary(
            "nonexistent_binary_xyz", ["nonexistent_xyz_1", "nonexistent_xyz_2"]
        )
        self.assertFalse(is_ok)
        self.assertTrue("not found" in message.lower() or "not available" in message.lower())

    def test_broken_binary_exists_but_not_executable(self):
        """Test that a binary that exists but cannot execute is reported as BROKEN.

        Simulates the bash.exe scenario: file exists but delegates to deleted
        target, so execution fails.
        """
        # Create a stub that "exists" but fails when executed
        with tempfile.TemporaryDirectory() as tmp_dir:
            broken_path = Path(tmp_dir) / "broken_stub.exe"
            broken_path.write_text("This is a broken stub that cannot execute")
            broken_path.chmod(0o644)  # Not executable on Unix, but on Windows...

            # Mock subprocess.run to simulate exec failure
            with mock.patch("subprocess.run") as mock_run:
                mock_run.side_effect = OSError("Cannot execute")

                is_ok, message = toolchain_health.check_binary("broken_test", [str(broken_path)])
                self.assertFalse(is_ok)
                self.assertTrue("broken" in message.lower() or "cannot execute" in message.lower())

    def test_available_binary_returns_ok(self):
        """Test that an available binary (like 'git') is reported as OK."""
        # Git should be available on this system
        is_ok, message = toolchain_health.check_binary("git", ["git"])
        assert is_ok
        assert message is None

    def test_binary_check_with_multiple_candidates(self):
        """Test binary check tries all candidates and uses first working one."""
        candidates = [
            "nonexistent_xyz_1",
            "nonexistent_xyz_2",
            "python",  # Should be available
        ]
        is_ok, message = toolchain_health.check_binary("test_multi", candidates)
        assert is_ok


class TestHeartbeatChecks(unittest.TestCase):
    """Tests for heartbeat file detection via StateAPI facade."""

    def test_heartbeat_check_with_stateapi_unavailable(self):
        """Test that heartbeat check reports unavailable when StateAPI can't load."""
        # StateAPI is None, so check should report unavailable
        is_ok, message = toolchain_health.check_heartbeat(
            "test", ".watchdog-heartbeat", max_age_seconds=300, state_api=None
        )
        assert not is_ok
        assert "unavailable" in message.lower()

    def test_heartbeat_check_with_stateapi_mock(self):
        """Test heartbeat check when StateAPI is available and fresh."""
        # Mock StateAPI that returns fresh heartbeat
        mock_api = mock.Mock()
        mock_api.check_heartbeat_fresh.return_value = True

        is_ok, message = toolchain_health.check_heartbeat(
            "test", ".watchdog-heartbeat", max_age_seconds=300, state_api=mock_api
        )
        assert is_ok
        assert message is None

    def test_heartbeat_check_with_stateapi_stale(self):
        """Test heartbeat check when StateAPI reports stale."""
        # Mock StateAPI that returns stale heartbeat
        mock_api = mock.Mock()
        mock_api.check_heartbeat_fresh.return_value = False

        is_ok, message = toolchain_health.check_heartbeat(
            "test", ".watchdog-heartbeat", max_age_seconds=300, state_api=mock_api
        )
        assert not is_ok
        assert "stale" in message.lower() or "missing" in message.lower()


class TestHealthyStateAndZeroChecks(unittest.TestCase):
    """Tests for full-run scenarios: healthy state, zero checks, exit codes."""

    def test_zero_checks_performed_exits_nonzero(self):
        """Test that zero-checks-performed exits with code 2 (error)."""
        # Mock both binary and heartbeat checks to return empty
        with mock.patch("toolchain_health.REQUIRED_BINARIES", {}):
            with mock.patch("toolchain_health.HEARTBEAT_FILENAMES", {}):
                exit_code = toolchain_health.run_checks(json_mode=False, max_age_seconds=300)
                assert exit_code == 2

    def test_zero_checks_json_mode(self):
        """Test that zero-checks-performed outputs JSON with ERROR status."""
        with mock.patch("toolchain_health.REQUIRED_BINARIES", {}):
            with mock.patch("toolchain_health.HEARTBEAT_FILENAMES", {}):
                with mock.patch("builtins.print") as mock_print:
                    exit_code = toolchain_health.run_checks(json_mode=True, max_age_seconds=300)
                    assert exit_code == 2
                    # Capture the print output
                    output_str = mock_print.call_args[0][0]
                    output = json.loads(output_str)
                    assert output["status"] == "ERROR"

    def test_healthy_state_exits_zero(self):
        """Test that a healthy state (all checks pass) exits with 0."""
        with mock.patch("toolchain_health.check_binary") as mock_binary:
            with mock.patch("toolchain_health.check_heartbeat") as mock_hb:
                # All checks pass
                mock_binary.return_value = (True, None)
                mock_hb.return_value = (True, None)

                exit_code = toolchain_health.run_checks(json_mode=False, max_age_seconds=300)
                assert exit_code == 0

    def test_found_issues_exits_one(self):
        """Test that found issues cause exit code 1."""
        with mock.patch("toolchain_health.check_binary") as mock_binary:
            with mock.patch("toolchain_health.check_heartbeat") as mock_hb:
                # Binary is broken
                mock_binary.return_value = (False, "Binary broken")
                mock_hb.return_value = (True, None)

                exit_code = toolchain_health.run_checks(json_mode=False, max_age_seconds=300)
                assert exit_code == 1


class TestJSONOutput(unittest.TestCase):
    """Tests for JSON output structure and validity."""

    def test_json_output_is_valid(self):
        """Test that JSON output is valid and parseable."""
        with mock.patch("toolchain_health.check_binary") as mock_binary:
            with mock.patch("toolchain_health.check_heartbeat") as mock_hb:
                mock_binary.return_value = (False, "Binary broken")
                mock_hb.return_value = (True, None)

                with mock.patch("builtins.print") as mock_print:
                    toolchain_health.run_checks(json_mode=True, max_age_seconds=300)
                    output_str = mock_print.call_args[0][0]
                    # This will raise if JSON is invalid
                    output = json.loads(output_str)
                    assert "status" in output
                    assert "findings" in output
                    assert isinstance(output["findings"], list)

    def test_json_includes_finding_details(self):
        """Test that JSON findings include type, details, and messages."""
        with mock.patch("toolchain_health.check_binary") as mock_binary:
            with mock.patch("toolchain_health.check_heartbeat") as mock_hb:
                mock_binary.return_value = (False, "Binary broken")
                mock_hb.return_value = (False, "Heartbeat stale")

                with mock.patch("builtins.print") as mock_print:
                    toolchain_health.run_checks(json_mode=True, max_age_seconds=300)
                    output_str = mock_print.call_args[0][0]
                    output = json.loads(output_str)

                    # Should have at least 1 finding
                    assert len(output["findings"]) >= 1
                    finding = output["findings"][0]
                    assert "type" in finding
                    assert "message" in finding


class TestCommandLineArgs(unittest.TestCase):
    """Tests for CLI argument parsing and behavior."""

    def test_json_flag_produces_json_output(self):
        """Test that --json flag produces JSON output."""
        with mock.patch("toolchain_health.check_binary") as mock_binary:
            with mock.patch("toolchain_health.check_heartbeat") as mock_hb:
                mock_binary.return_value = (True, None)
                mock_hb.return_value = (True, None)

                with mock.patch("builtins.print") as mock_print:
                    with mock.patch("sys.argv", ["toolchain_health.py", "--json"]):
                        try:
                            toolchain_health.main()
                        except SystemExit:
                            pass

                    output_str = mock_print.call_args[0][0]
                    # Should be valid JSON
                    json.loads(output_str)

    def test_max_age_flag_overrides_threshold(self):
        """Test that --max-age flag is honored."""
        with mock.patch("toolchain_health.check_heartbeat") as mock_hb:
            with mock.patch("toolchain_health.check_binary") as mock_binary:
                mock_binary.return_value = (True, None)
                mock_hb.return_value = (True, None)

                with mock.patch("sys.argv", ["toolchain_health.py", "--max-age", "600"]):
                    try:
                        toolchain_health.main()
                    except SystemExit:
                        pass

                    # Check that check_heartbeat was called with threshold=600
                    calls = mock_hb.call_args_list
                    if calls:
                        # One of the calls should have 600 in its args
                        assert any(600 in call[0] for call in calls)
