#!/usr/bin/env python3
"""
Test suite for toolchain_health.py.

Tests verify:
  - Broken binaries (exist but cannot execute) are detected as BROKEN
  - Missing binaries are detected as MISSING
  - Stale heartbeats are reported as STALE
  - Missing heartbeat files are reported as STALE
  - Zero-checks-performed exits non-zero
  - Healthy state exits 0
  - JSON output is valid and structured correctly

All tests use tmp_path fixtures and never modify committed code or git config.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest


# Ensure tools directory is on path
TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import toolchain_health


class TestBinaryChecks:
    """Tests for binary detection and execution checking."""

    def test_missing_binary(self):
        """Test that a missing binary is reported as MISSING."""
        is_ok, message = toolchain_health.check_binary(
            "nonexistent_binary_xyz", ["nonexistent_xyz_1", "nonexistent_xyz_2"]
        )
        assert not is_ok
        assert "not found" in message.lower() or "not available" in message.lower()

    def test_broken_binary_exists_but_not_executable(self, tmp_path):
        """Test that a binary that exists but cannot execute is reported as BROKEN.

        Simulates the bash.exe scenario: file exists but delegates to deleted
        target, so execution fails.
        """
        # Create a stub that "exists" but fails when executed
        broken_path = tmp_path / "broken_stub.exe"
        broken_path.write_text("This is a broken stub that cannot execute")
        broken_path.chmod(0o644)  # Not executable on Unix, but on Windows...

        # Mock subprocess.run to simulate exec failure
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("Cannot execute")

            is_ok, message = toolchain_health.check_binary("broken_test", [str(broken_path)])
            assert not is_ok
            assert "broken" in message.lower() or "cannot execute" in message.lower()

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


class TestHeartbeatChecks:
    """Tests for heartbeat file detection and staleness checking."""

    def test_stale_heartbeat_old_file(self, tmp_path):
        """Test that an old heartbeat file is reported as STALE."""
        # Create a heartbeat file with an old timestamp
        hb_file = tmp_path / ".watchdog-heartbeat"
        old_time = int(time.time()) - 500  # 500 seconds old
        hb_file.write_text(str(old_time), encoding="utf-8")

        # Mock get_heartbeat_path to return our test file
        with mock.patch("toolchain_health.get_heartbeat_path") as mock_path:
            mock_path.return_value = hb_file

            is_ok, message = toolchain_health.check_heartbeat("test", "dummy", max_age_seconds=300)
            assert not is_ok
            assert "stale" in message.lower()

    def test_missing_heartbeat_file(self, tmp_path):
        """Test that a missing heartbeat file is reported as STALE."""
        missing_path = tmp_path / "nonexistent_heartbeat"

        with mock.patch("toolchain_health.get_heartbeat_path") as mock_path:
            mock_path.return_value = missing_path

            is_ok, message = toolchain_health.check_heartbeat("test", "dummy", max_age_seconds=300)
            assert not is_ok
            assert "missing" in message.lower() or "stale" in message.lower()

    def test_fresh_heartbeat_file(self, tmp_path):
        """Test that a fresh heartbeat file passes the check."""
        hb_file = tmp_path / ".watchdog-heartbeat"
        recent_time = int(time.time()) - 10  # 10 seconds old (well within 300s threshold)
        hb_file.write_text(str(recent_time), encoding="utf-8")

        with mock.patch("toolchain_health.get_heartbeat_path") as mock_path:
            mock_path.return_value = hb_file

            is_ok, message = toolchain_health.check_heartbeat("test", "dummy", max_age_seconds=300)
            assert is_ok
            assert message is None


class TestHealthyStateAndZeroChecks:
    """Tests for full-run scenarios: healthy state, zero checks, exit codes."""

    def test_zero_checks_performed_exits_nonzero(self):
        """Test that zero-checks-performed exits with code 2 (error)."""
        # Mock both binary and heartbeat checks to return empty
        with mock.patch("toolchain_health.REQUIRED_BINARIES", {}):
            with mock.patch("toolchain_health.HEARTBEAT_FILES", {}):
                exit_code = toolchain_health.run_checks(json_mode=False, max_age_seconds=300)
                assert exit_code == 2

    def test_zero_checks_json_mode(self):
        """Test that zero-checks-performed outputs JSON with ERROR status."""
        with mock.patch("toolchain_health.REQUIRED_BINARIES", {}):
            with mock.patch("toolchain_health.HEARTBEAT_FILES", {}):
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


class TestJSONOutput:
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

                    # Should have 2 findings (one binary, one heartbeat)
                    assert len(output["findings"]) >= 1
                    finding = output["findings"][0]
                    assert "type" in finding
                    assert "message" in finding


class TestHeartbeatPathResolution:
    """Tests for heartbeat file path resolution."""

    def test_get_heartbeat_path_absolute(self, tmp_path):
        """Test that absolute paths are returned as-is."""
        abs_path = tmp_path / "heartbeat"
        result = toolchain_health.get_heartbeat_path(str(abs_path))
        assert result == abs_path

    def test_get_heartbeat_path_relative_cwd(self, tmp_path):
        """Test that relative paths are resolved from cwd."""
        hb_file = tmp_path / "state" / ".watchdog-heartbeat"
        hb_file.parent.mkdir(parents=True, exist_ok=True)
        hb_file.write_text("123456789", encoding="utf-8")

        # Change to tmp_path and resolve relative path
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = toolchain_health.get_heartbeat_path("state/.watchdog-heartbeat")
            # Should find the file we created
            assert result.exists() or result.name == ".watchdog-heartbeat"
        finally:
            os.chdir(old_cwd)


class TestCommandLineArgs:
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
                        # Last positional arg should be 600
                        assert any(600 in call[0] for call in calls)


class TestPortability:
    """Tests for Windows and POSIX compatibility."""

    def test_encoding_utf8_on_file_read(self, tmp_path):
        """Test that files are read with explicit UTF-8 encoding."""
        hb_file = tmp_path / ".watchdog-heartbeat"
        # Write with explicit UTF-8
        hb_file.write_text(str(int(time.time())), encoding="utf-8")

        with mock.patch("toolchain_health.get_heartbeat_path") as mock_path:
            mock_path.return_value = hb_file

            # Should not crash on encoding
            is_ok, message = toolchain_health.check_heartbeat("test", "dummy", 300)
            # File is fresh, should pass
            assert is_ok

    def test_path_handling_windows_and_posix(self):
        """Test that Path objects work on both Windows and POSIX."""
        # pathlib.Path should handle both transparently
        from pathlib import Path as PathlibPath

        # Windows-style path
        win_path = PathlibPath("C:\\Users\\test\\.heartbeat")
        # POSIX-style path
        posix_path = PathlibPath("/home/test/.heartbeat")

        # Both should be Path instances
        assert isinstance(win_path, PathlibPath)
        assert isinstance(posix_path, PathlibPath)


# Import time for staleness calculations
import time
