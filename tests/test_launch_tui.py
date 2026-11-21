#!/usr/bin/env python3
"""
Tests for launch_tui.py — portable git-bash resolver.
"""

import unittest
from unittest import mock
from pathlib import Path
import sys
import os

# Add parent directory to path so we can import launch_tui
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from launch_tui import (
    _script_match_patterns,
    find_bash_process_by_script,
    resolve_git_bash_path,
)


class TestResolveGitBashPath(unittest.TestCase):
    """Test portable git-bash resolver with priority order."""

    def test_env_var_wins(self):
        """env var AESOP_GIT_BASH should be returned first."""
        env_path = r"C:\Custom\git-bash.exe"
        with mock.patch.dict(os.environ, {"AESOP_GIT_BASH": env_path}):
            with mock.patch("os.path.exists", return_value=True):
                result = resolve_git_bash_path()
                self.assertEqual(result, env_path)

    def test_which_git_bash_second(self):
        """shutil.which('git-bash') should be tried second."""
        which_path = r"C:\SomeLocation\git-bash.exe"
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("shutil.which") as mock_which:
                with mock.patch("os.path.exists", return_value=True):
                    # which('git-bash') succeeds
                    mock_which.side_effect = lambda x: (
                        which_path if x == "git-bash" else None
                    )
                    result = resolve_git_bash_path()
                    self.assertEqual(result, which_path)

    def test_git_derived_third(self):
        """Derive from which('git') -> ../git-bash.exe on Windows."""
        git_path = r"C:\Program Files\Git\cmd\git.exe"
        expected_bash = r"C:\Program Files\Git\git-bash.exe"
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("shutil.which") as mock_which:
                with mock.patch("os.path.exists", return_value=True):
                    # which('git-bash') fails, which('git') succeeds
                    mock_which.side_effect = lambda x: (
                        git_path if x == "git" else None
                    )
                    result = resolve_git_bash_path()
                    # The derived path should be from git_path's parent + git-bash.exe
                    # git_path: C:\Program Files\Git\cmd\git.exe
                    # parent: C:\Program Files\Git\cmd
                    # parent.parent: C:\Program Files\Git
                    # git-bash.exe: C:\Program Files\Git\git-bash.exe
                    self.assertEqual(result, expected_bash)

    def test_default_fallback(self):
        """Default hardcoded path as last resort."""
        default_path = r"C:\Program Files\Git\git-bash.exe"
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("shutil.which", return_value=None):
                with mock.patch("os.path.exists", return_value=True):
                    result = resolve_git_bash_path()
                    self.assertEqual(result, default_path)

    def test_none_found_error(self):
        """Clear error message when nothing is found."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("shutil.which", return_value=None):
                with mock.patch("os.path.exists", return_value=False):
                    with self.assertRaises(FileNotFoundError) as cm:
                        resolve_git_bash_path()
                    error_msg = str(cm.exception)
                    # Check that error message is informative
                    self.assertIn("git-bash.exe not found", error_msg)
                    self.assertIn("Tried (in order)", error_msg)
                    # Should include the default fallback path
                    self.assertIn("C:\\Program Files\\Git", error_msg)


class TestFindBashProcessShellOnly(unittest.TestCase):
    """Prove find_bash_process_by_script uses /proc, bash, or ps -- never PowerShell."""

    def test_proc_scan_finds_matching_bash_and_prefers_winpid(self):
        import tempfile

        with tempfile.TemporaryDirectory() as proc_dir:
            p1 = Path(proc_dir) / "4201"
            p1.mkdir()
            script = str(Path(proc_dir) / "tui.sh")
            (p1 / "cmdline").write_bytes(f"/usr/bin/bash\x00{script}\x00".encode("utf-8"))
            (p1 / "winpid").write_text("9876\n", encoding="utf-8")

            pid = find_bash_process_by_script(script, proc_root=proc_dir)
            self.assertEqual(pid, 9876)

    def test_windows_fallback_uses_bash_never_powershell(self):
        seen_cmds = []

        def fake_run(cmd, **kwargs):
            seen_cmds.append(cmd)
            res = mock.Mock()
            res.returncode = 0
            res.stdout = "5432\n"
            return res

        with mock.patch("launch_tui.os.name", "nt"), mock.patch(
            "launch_tui._resolve_bash_cli", return_value="/usr/bin/bash"
        ), mock.patch("launch_tui.subprocess.run", side_effect=fake_run):
            pid = find_bash_process_by_script("D:\\demo\\run.sh", proc_root="")
            self.assertEqual(pid, 5432)
            self.assertEqual(len(seen_cmds), 1)
            self.assertNotIn("powershell", seen_cmds[0][0].lower())
            self.assertIn("bash", seen_cmds[0][0].lower())

        patterns = _script_match_patterns("D:\\demo\\run.sh")
        self.assertTrue(any(p.startswith("/d/") for p in patterns))


if __name__ == "__main__":
    unittest.main()
