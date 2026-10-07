#!/usr/bin/env python3
"""
Red-first tests for linux_shape_check.py
INDEX: Test suite for Linux shape checker (mocked WSL, fixture ranges)

Test cases:
  - Range without shell/node changes → skipped (exit 0)
  - With changes + WSL unavailable → NOTICE exit 0
  - With changes + WSL unavailable + AESOP_REQUIRE_LINUX_SHAPE=1 → exit 1
  - With changes + mocked WSL failure → exit 1 with output
  - USERPROFILE unset on Node tests
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import linux_shape_check as lsc


class TestLinuxShapeCheckRanges(unittest.TestCase):
    """Red-first tests for range-based filtering."""

    def test_range_without_shell_changes_skipped(self):
        """No shell/workflow/node changes → skip (exit 0)."""
        changed_files = ["src/main.ts", "README.md", "package.json"]
        self.assertFalse(lsc.should_check_linux_shape(changed_files))

    def test_range_with_shell_script_changes_triggers(self):
        """*.sh change → trigger check."""
        changed_files = ["tools/helper.sh", "tests/test.sh"]
        self.assertTrue(lsc.should_check_linux_shape(changed_files))

    def test_range_with_hooks_changes_triggers(self):
        """hooks/ change → trigger check."""
        changed_files = ["hooks/pre-push-policy.sh"]
        self.assertTrue(lsc.should_check_linux_shape(changed_files))

    def test_range_with_workflow_changes_triggers(self):
        """.github/workflows/*.yml change → trigger check."""
        changed_files = [".github/workflows/ci.yml"]
        self.assertTrue(lsc.should_check_linux_shape(changed_files))

    def test_range_with_node_test_changes_triggers(self):
        """tests/**/*.test.mjs change → trigger check."""
        changed_files = ["tests/helpers/isolated-env.test.mjs"]
        self.assertTrue(lsc.should_check_linux_shape(changed_files))

    def test_range_with_run_shell_tests_sh_triggers(self):
        """tools/run_shell_tests.sh change → trigger check."""
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
    """Red-first tests for Windows→WSL path conversion."""

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


class TestLinuxShapeCheckIntegration(unittest.TestCase):
    """Integration tests with mocked WSL calls."""

    @mock.patch.dict(os.environ, {}, clear=False)
    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_available")
    def test_skip_on_no_changes(self, mock_wsl, mock_changed):
        """No changes → skip (exit 0)."""
        mock_changed.return_value = ["src/main.ts"]
        mock_wsl.return_value = True
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc = lsc.main()
        self.assertEqual(rc, 0)

    @mock.patch.dict(os.environ, {}, clear=False)
    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_available")
    def test_notice_on_wsl_unavailable(self, mock_wsl, mock_changed):
        """WSL unavailable → NOTICE exit 0 (unless AESOP_REQUIRE_LINUX_SHAPE)."""
        mock_changed.return_value = ["tools/helper.sh"]
        mock_wsl.return_value = False
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc = lsc.main()
        self.assertEqual(rc, 0)

    @mock.patch.dict(os.environ, {"AESOP_REQUIRE_LINUX_SHAPE": "1"})
    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_available")
    def test_fail_on_wsl_unavailable_with_require_flag(self, mock_wsl, mock_changed):
        """WSL unavailable + AESOP_REQUIRE_LINUX_SHAPE=1 → exit 1."""
        mock_changed.return_value = ["tools/helper.sh"]
        mock_wsl.return_value = False
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc = lsc.main()
        self.assertEqual(rc, 1)

    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_available")
    @mock.patch("linux_shape_check.compute_wsl_path")
    @mock.patch("linux_shape_check.run_shell_tests_wsl")
    def test_fail_on_shell_test_failure(self, mock_run_shell, mock_path, mock_wsl, mock_changed):
        """Shell tests fail → exit 1 with output."""
        mock_changed.return_value = ["tools/helper.sh"]
        mock_wsl.return_value = True
        mock_path.return_value = "/mnt/c/Users/matt8/aesop"
        mock_run_shell.return_value = (1, "Shell test error output")
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc = lsc.main()
        self.assertEqual(rc, 1)

    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_available")
    @mock.patch("linux_shape_check.compute_wsl_path")
    @mock.patch("linux_shape_check.run_node_tests_wsl")
    def test_fail_on_node_test_failure(self, mock_run_node, mock_path, mock_wsl, mock_changed):
        """Node tests fail → exit 1 with output."""
        mock_changed.return_value = ["tests/test.test.mjs"]
        mock_wsl.return_value = True
        mock_path.return_value = "/mnt/c/Users/matt8/aesop"
        mock_run_node.return_value = (1, "Node test error output")
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc = lsc.main()
        self.assertEqual(rc, 1)

    @mock.patch("linux_shape_check.get_changed_files")
    @mock.patch("linux_shape_check.wsl_available")
    @mock.patch("linux_shape_check.compute_wsl_path")
    @mock.patch("linux_shape_check.run_shell_tests_wsl")
    def test_pass_on_shell_test_success(self, mock_run_shell, mock_path, mock_wsl, mock_changed):
        """Shell tests pass → exit 0."""
        mock_changed.return_value = ["tools/helper.sh"]
        mock_wsl.return_value = True
        mock_path.return_value = "/mnt/c/Users/matt8/aesop"
        mock_run_shell.return_value = (0, "All tests passed")
        with mock.patch("sys.argv", ["linux_shape_check.py", "--range", "origin/main..HEAD"]):
            with mock.patch("linux_shape_check.get_repo_root", return_value=Path(".")):
                rc = lsc.main()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
