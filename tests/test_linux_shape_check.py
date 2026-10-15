#!/usr/bin/env python3
"""
Red-first tests for linux_shape_check.py
INDEX: Test suite for Linux shape checker (mocked WSL, fixture ranges)

Test cases:
  - Range without shell/node changes -> skipped (exit 0)
  - With changes + WSL unavailable -> NOTICE exit 0
  - With changes + WSL unavailable + AESOP_REQUIRE_LINUX_SHAPE=1 -> exit 1
  - With changes + mocked WSL failure -> exit 1 with output
  - USERPROFILE unset on Node tests
"""

import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import linux_shape_check as lsc


def _main_captured():
    """Run lsc.main() with its stdout captured so mocked sub-step output stays out of the suite log."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = lsc.main()
    return rc, buf.getvalue()


class TestLinuxShapeCheckRanges(unittest.TestCase):
    """Red-first tests for range-based filtering."""

    def test_range_without_shell_changes_skipped(self):
        """No shell/workflow/node changes -> skip (exit 0)."""
        changed_files = ["src/main.ts", "README.md", "package.json"]
        self.assertFalse(lsc.should_check_linux_shape(changed_files))

    def test_range_with_shell_script_changes_triggers(self):
        """*.sh change -> trigger check."""
        changed_files = ["tools/helper.sh", "tests/test.sh"]
        self.assertTrue(lsc.should_check_linux_shape(changed_files))

    def test_range_with_hooks_changes_triggers(self):
        """hooks/ change -> trigger check."""
        changed_files = ["hooks/pre-push-policy.sh"]
        self.assertTrue(lsc.should_check_linux_shape(changed_files))

    def test_range_with_workflow_changes_triggers(self):
        """.github/workflows/*.yml change -> trigger check."""
        changed_files = [".github/workflows/ci.yml"]
        self.assertTrue(lsc.should_check_linux_shape(changed_files))

    def test_range_with_node_test_changes_triggers(self):
        """tests/**/*.test.mjs change -> trigger check."""
        changed_files = ["tests/helpers/isolated-env.test.mjs"]
        self.assertTrue(lsc.should_check_linux_shape(changed_files))

    def test_range_with_run_shell_tests_sh_triggers(self):
        """tools/run_shell_tests.sh change -> trigger check."""
        changed_files = ["tools/run_shell_tests.sh"]
        self.assertTrue(lsc.should_check_linux_shape(changed_files))


class TestLinuxShapeCheckWSL(unittest.TestCase):
    """Red-first tests for WSL availability detection."""

    @mock.patch("subprocess.run")
    def test_wsl_available_with_distros(self, mock_run):
        """WSL available when wsl -l -q returns distro names and -e true succeeds."""
        # First call: wsl -l -q returns distros (UTF-16LE encoded)
        # Second call: wsl -e true succeeds
        mock_run.side_effect = [
            mock.Mock(
                returncode=0,
                stdout="Ubuntu\nDebian\n".encode('utf-16-le'),
                stderr=b"",
            ),
            mock.Mock(
                returncode=0,
                stdout=b"",
                stderr=b"",
            ),
        ]
        self.assertTrue(lsc.wsl_available())

    @mock.patch("subprocess.run")
    def test_wsl_unavailable_on_error(self, mock_run):
        """WSL unavailable when wsl.exe call fails."""
        mock_run.side_effect = Exception("wsl.exe not found")
        self.assertFalse(lsc.wsl_available())

    @mock.patch("subprocess.run")
    def test_wsl_unavailable_no_distros(self, mock_run):
        """WSL unavailable when no distros installed (wsl -l -q returns empty)."""
        # wsl -l -q returns empty (no distros)
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=b"",
            stderr=b"",
        )
        self.assertFalse(lsc.wsl_available())

    @mock.patch("subprocess.run")
    def test_wsl_unavailable_when_executable_fails(self, mock_run):
        """WSL unavailable when distro listed but -e true fails."""
        # First call: wsl -l -q returns a distro
        # Second call: wsl -e true fails
        mock_run.side_effect = [
            mock.Mock(
                returncode=0,
                stdout="Ubuntu\n".encode('utf-16-le'),
                stderr=b"",
            ),
            mock.Mock(
                returncode=1,
                stdout=b"",
                stderr=b"",
            ),
        ]
        self.assertFalse(lsc.wsl_available())


class TestLinuxShapeCheckPathConversion(unittest.TestCase):
    """Red-first tests for Windows->WSL path conversion."""

    @mock.patch("subprocess.run")
    def test_wslpath_conversion_success(self, mock_run):
        """wslpath -a conversion succeeds."""
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=b"/mnt/c/Users/matt8/aesop\n",
            stderr=b"",
        )
        wsl_path = lsc.compute_wsl_path(Path("C:\\Users\\matt8\\aesop"))
        self.assertEqual(wsl_path, "/mnt/c/Users/matt8/aesop")

    @mock.patch("subprocess.run")
    def test_wslpath_conversion_fallback(self, mock_run):
        """Fallback /mnt conversion on wslpath error."""
        mock_run.side_effect = Exception("wslpath failed")
        wsl_path = lsc.compute_wsl_path(Path("C:\\Users\\test\\repo"))
        self.assertIn("/mnt/c/", wsl_path.lower())


class TestWslStatus(unittest.TestCase):
    """wsl_status() is tri-state: absent / no-distro are skips, error is a failure."""

    @mock.patch("subprocess.run")
    def test_absent_when_wsl_exe_missing(self, mock_run):
        mock_run.side_effect = FileNotFoundError("wsl.exe")
        self.assertEqual(lsc.wsl_status(), "absent")

    @mock.patch("subprocess.run")
    def test_no_distro_when_list_is_empty(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        self.assertEqual(lsc.wsl_status(), "no-distro")

    @mock.patch("subprocess.run")
    def test_no_distro_when_output_is_only_message_text(self, mock_run):
        msg = "Windows Subsystem for Linux has no installed distributions.\n"
        mock_run.return_value = mock.Mock(returncode=0, stdout=msg.encode("utf-16-le"), stderr=b"")
        self.assertEqual(lsc.wsl_status(), "no-distro")

    @mock.patch("subprocess.run")
    def test_bom_only_output_is_no_distro(self, mock_run):
        """wsl.exe -l -q leads with a UTF-16LE BOM; stripped, it lists no distro."""
        mock_run.return_value = mock.Mock(returncode=0, stdout="﻿\r\n".encode("utf-16-le"), stderr=b"")
        self.assertEqual(lsc.wsl_status(), "no-distro")
        self.assertEqual(mock_run.call_count, 1)

    @mock.patch("subprocess.run")
    def test_bom_prefixed_distro_name_is_still_listed(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="﻿Ubuntu\r\n".encode("utf-16-le"), stderr=b"")
        self.assertEqual(lsc.wsl_status(), "ok")

    @mock.patch("subprocess.run")
    def test_error_when_list_call_times_out(self, mock_run):
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd="wsl.exe", timeout=5)
        self.assertEqual(lsc.wsl_status(), "error")

    @mock.patch("subprocess.run")
    def test_error_when_list_call_exits_nonzero(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=1, stdout=b"", stderr=b"boom")
        self.assertEqual(lsc.wsl_status(), "error")

    @mock.patch("subprocess.run")
    def test_error_when_distro_listed_but_exec_raises(self, mock_run):
        mock_run.side_effect = [
            mock.Mock(returncode=0, stdout="Ubuntu\n".encode("utf-16-le"), stderr=b""),
            OSError("exec failed"),
        ]
        self.assertEqual(lsc.wsl_status(), "error")

    @mock.patch("subprocess.run")
    def test_ok_when_distro_listed_and_exec_succeeds(self, mock_run):
        mock_run.side_effect = [
            mock.Mock(returncode=0, stdout="Ubuntu\n".encode("utf-16-le"), stderr=b""),
            mock.Mock(returncode=0, stdout=b"", stderr=b""),
        ]
        self.assertEqual(lsc.wsl_status(), "ok")


class TestLinuxShapeCheckIntegration(unittest.TestCase):
    """Integration tests with mocked WSL calls."""

    def setUp(self):
        # Isolate from the caller's environment: an inherited AESOP_REQUIRE_LINUX_SHAPE=1
        # (set in the shell or by a receipt run) must not leak into these cases.
        env_patch = mock.patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("AESOP_REQUIRE_LINUX_SHAPE", None)
        # These cases exercise the Windows/WSL leg; pin the host so a Linux CI
        # runner never executes the real native shell/Node suites from inside a test.
        host_patch = mock.patch("linux_shape_check.host_is_windows", return_value=True)
        host_patch.start()
        self.addCleanup(host_patch.stop)

    @mock.patch.dict(os.environ, {}, clear=False)
    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_status")
    def test_skip_on_no_changes(self, mock_wsl, mock_changed):
        """No changes -> skip (exit 0)."""
        mock_changed.return_value = ["src/main.ts"]
        mock_wsl.return_value = lsc.WSL_OK
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc, _out = _main_captured()
        self.assertEqual(rc, 0)

    @mock.patch.dict(os.environ, {}, clear=False)
    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_status")
    def test_notice_on_wsl_unavailable(self, mock_wsl, mock_changed):
        """WSL unavailable -> NOTICE exit 0 (unless AESOP_REQUIRE_LINUX_SHAPE)."""
        mock_changed.return_value = ["tools/helper.sh"]
        mock_wsl.return_value = lsc.WSL_ABSENT
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc, _out = _main_captured()
        self.assertEqual(rc, 0)

    @mock.patch.dict(os.environ, {"AESOP_REQUIRE_LINUX_SHAPE": "1"})
    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_status")
    def test_fail_on_wsl_unavailable_with_require_flag(self, mock_wsl, mock_changed):
        """WSL unavailable + AESOP_REQUIRE_LINUX_SHAPE=1 -> exit 1."""
        mock_changed.return_value = ["tools/helper.sh"]
        mock_wsl.return_value = lsc.WSL_ABSENT
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc, _out = _main_captured()
        self.assertEqual(rc, 1)

    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_status")
    @mock.patch("linux_shape_check.compute_wsl_path")
    @mock.patch("linux_shape_check.run_shell_tests_wsl")
    def test_fail_on_shell_test_failure(self, mock_run_shell, mock_path, mock_wsl, mock_changed):
        """Shell tests fail -> exit 1 with output."""
        mock_changed.return_value = ["tools/helper.sh"]
        mock_wsl.return_value = lsc.WSL_OK
        mock_path.return_value = "/mnt/c/Users/matt8/aesop"
        mock_run_shell.return_value = (1, "Shell test error output")
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc, _out = _main_captured()
        self.assertEqual(rc, 1)

    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_status")
    @mock.patch("linux_shape_check.compute_wsl_path")
    @mock.patch("linux_shape_check.run_node_tests_wsl")
    def test_fail_on_node_test_failure(self, mock_run_node, mock_path, mock_wsl, mock_changed):
        """Node tests fail -> exit 1 with output."""
        mock_changed.return_value = ["tests/test.test.mjs"]
        mock_wsl.return_value = lsc.WSL_OK
        mock_path.return_value = "/mnt/c/Users/matt8/aesop"
        mock_run_node.return_value = (1, "Node test error output")
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc, _out = _main_captured()
        self.assertEqual(rc, 1)

    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_status")
    @mock.patch("linux_shape_check.compute_wsl_path")
    @mock.patch("linux_shape_check.run_shell_tests_wsl")
    def test_pass_on_shell_test_success(self, mock_run_shell, mock_path, mock_wsl, mock_changed):
        """Shell tests pass -> exit 0."""
        mock_changed.return_value = ["tools/helper.sh"]
        mock_wsl.return_value = lsc.WSL_OK
        mock_path.return_value = "/mnt/c/Users/matt8/aesop"
        mock_run_shell.return_value = (0, "All tests passed")
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc, _out = _main_captured()
        self.assertEqual(rc, 0)


class TestIntegrationOutputIsCaptured(unittest.TestCase):
    """The mocked sub-step FAIL fixtures above must not leak into the parent suite log.

    Incident 2026-10-15: `python tools/ci_shard_runner.py 0 4` printed `FAIL: Node tests exited 1`,
    `FAIL: Shell tests exited 1` and `ERROR: WSL required` into its own output. Those lines are
    fixture output from TestLinuxShapeCheckIntegration (the shard exited 0, all tests passed), but
    they read as real sub-step failures. Driving the integration class in a child interpreter and
    inspecting its stdout proves the leak itself, not a source-text property.
    """

    def test_integration_run_does_not_leak_fixture_failures(self):
        repo_root = Path(__file__).resolve().parent.parent
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_linux_shape_check.TestLinuxShapeCheckIntegration"],
            cwd=str(repo_root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        combined = proc.stdout + proc.stderr
        self.assertNotIn("FAIL: Node tests exited 1", combined)
        self.assertNotIn("FAIL: Shell tests exited 1", combined)
        self.assertNotIn("ERROR: WSL required", combined)


class TestLinuxShapeCheckHostPlatform(unittest.TestCase):
    """Red-first: on Linux the leg runs natively (no WSL probe); on Windows a probe error fails closed."""

    def setUp(self):
        env_patch = mock.patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("AESOP_REQUIRE_LINUX_SHAPE", None)

    def _run_main(self, changed, host_windows):
        # Capture stdout: the mocked sub-step FAIL fixtures must not reach the suite log.
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]), \
                mock.patch("linux_shape_check.get_changed_files", return_value=changed), \
                mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")), \
                mock.patch("linux_shape_check.host_is_windows", return_value=host_windows), \
                contextlib.redirect_stdout(io.StringIO()):
            return lsc.main()

    def test_linux_host_runs_shell_leg_natively_without_probing_wsl(self):
        """Linux runner: shell suites run natively; wsl_status is never consulted."""
        with mock.patch("linux_shape_check.wsl_status", side_effect=AssertionError("WSL probed on Linux")) as probe, \
                mock.patch("linux_shape_check.run_shell_tests_native", return_value=(0, "ok")) as native:
            rc = self._run_main(["tools/helper.sh"], host_windows=False)
        self.assertEqual(rc, 0)
        native.assert_called_once()
        probe.assert_not_called()

    def test_linux_host_native_shell_failure_exits_1(self):
        with mock.patch("linux_shape_check.run_shell_tests_native", return_value=(1, "boom")):
            rc = self._run_main(["tools/helper.sh"], host_windows=False)
        self.assertEqual(rc, 1)

    def test_linux_host_runs_node_leg_natively(self):
        with mock.patch("linux_shape_check.run_node_tests_native", return_value=(0, "ok")) as native:
            rc = self._run_main(["tests/x.test.mjs"], host_windows=False)
        self.assertEqual(rc, 0)
        native.assert_called_once()

    def test_native_node_leg_unsets_userprofile(self):
        """Node suites run with USERPROFILE unset, natively, as on the WSL leg."""
        completed = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.dict(os.environ, {"USERPROFILE": "C:\\Users\\x"}), \
                mock.patch("linux_shape_check.subprocess.run", return_value=completed) as run:
            rc, _ = lsc.run_node_tests_native(Path("."), ["tests/x.test.mjs"])
        self.assertEqual(rc, 0)
        self.assertNotIn("USERPROFILE", run.call_args.kwargs["env"])

    def test_windows_probe_error_fails_closed_without_require_flag(self):
        """WSL probe error is a failure, not a silent skip."""
        with mock.patch("linux_shape_check.wsl_status", return_value=lsc.WSL_ERROR), \
                mock.patch("linux_shape_check.run_shell_tests_wsl") as wsl_run:
            rc = self._run_main(["tools/helper.sh"], host_windows=True)
        self.assertEqual(rc, 1)
        wsl_run.assert_not_called()

    def test_windows_no_distro_skips_with_reason(self):
        """WSL present but no distro: skip (exit 0) with an install reason."""
        with mock.patch("linux_shape_check.wsl_status", return_value=lsc.WSL_NO_DISTRO), \
                mock.patch("linux_shape_check.run_shell_tests_wsl") as wsl_run:
            rc = self._run_main(["tools/helper.sh"], host_windows=True)
        self.assertEqual(rc, 0)
        wsl_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
