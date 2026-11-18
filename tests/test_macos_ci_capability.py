#!/usr/bin/env python3
"""Tests for macOS (darwin) portability across CI capability, Linux shape check,
TUI launcher, and literal user-path regression checks.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from tools import ci_capability as cc  # noqa: E402
from tools import launch_tui  # noqa: E402
from tools import linux_shape_check as lsc  # noqa: E402


def _completed(rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["sysctl", "-n", "hw.memsize"],
        returncode=rc,
        stdout=stdout,
        stderr=stderr,
    )


class TestDarwinCiCapability(unittest.TestCase):
    """Darwin RAM/CPU probing and mode evaluation in tools/ci_capability.py."""

    def test_probe_ram_gb_darwin_uses_sysctl_hw_memsize(self):
        real_open = open

        def no_proc_meminfo(path, *args, **kwargs):
            if str(path) == "/proc/meminfo":
                raise FileNotFoundError(path)
            return real_open(path, *args, **kwargs)

        with mock.patch.object(cc.sys, "platform", "darwin"), \
                mock.patch("builtins.open", side_effect=no_proc_meminfo), \
                mock.patch.object(cc.subprocess, "run", return_value=_completed(0, "17179869184\n")) as mock_run, \
                mock.patch.object(cc.os, "cpu_count", return_value=10), \
                mock.patch.object(cc.platform, "system", return_value="Darwin"), \
                mock.patch.object(cc.platform, "release", return_value="24.0.0"), \
                mock.patch.object(cc.platform, "version", return_value="Darwin Kernel Version 24.0.0"), \
                mock.patch.object(cc.platform, "machine", return_value="arm64"), \
                mock.patch.object(cc.platform, "node", return_value="mac-mini.local"), \
                mock.patch.object(cc.shutil, "which", return_value=None):
            probes = cc.probe_all(repo_root=str(REPO_ROOT))

        self.assertEqual(probes["cpu_cores"], 10)
        self.assertEqual(probes["ram_gb"], 16.0)
        mock_run.assert_called()
        sysctl_calls = [
            c for c in mock_run.call_args_list
            if c.args and c.args[0] == ["sysctl", "-n", "hw.memsize"]
        ]
        self.assertEqual(len(sysctl_calls), 1)
        kwargs = sysctl_calls[0].kwargs
        self.assertEqual(kwargs.get("encoding"), "utf-8")
        self.assertEqual(kwargs.get("errors"), "replace")
        self.assertIsInstance(kwargs.get("timeout"), (int, float))
        self.assertGreater(kwargs["timeout"], 0)

    def test_probe_ram_gb_darwin_nonzero_rc_returns_none(self):
        with mock.patch.object(cc.sys, "platform", "darwin"), \
                mock.patch.object(cc.subprocess, "run", return_value=_completed(1, "42")):
            self.assertIsNone(cc._probe_ram_gb())

    def test_probe_ram_gb_darwin_timeout_returns_none(self):
        with mock.patch.object(cc.sys, "platform", "darwin"), \
                mock.patch.object(
                    cc.subprocess, "run",
                    side_effect=subprocess.TimeoutExpired(["sysctl", "-n", "hw.memsize"], 5),
                ):
            self.assertIsNone(cc._probe_ram_gb())

    def test_report_darwin_evaluates_native_receipt_and_runner(self):
        probes = {
            "os": {"system": "Darwin", "release": "24.0.0", "version": "Darwin", "machine": "arm64"},
            "cpu_cores": 8,
            "ram_gb": 16.0,
            "windows_code_integrity": None,
            "wsl": {"present": False},
            "docker": {"present": False},
            "gh": {"present": True, "authenticated": True},
            "cloudflared": {"present": False},
            "receipt_tools": {"emit_receipt": True, "verify_receipt": True, "verify_workflow": True},
        }
        data = cc.report(probes=probes)
        modes = {m["mode"]: m for m in data["modes"]}
        self.assertTrue(modes["self-hosted-runner"]["runnable"])
        self.assertIn("8 cores / 16.0 GB RAM", modes["self-hosted-runner"]["why"])
        self.assertTrue(modes["local-receipt-gate"]["runnable"])
        self.assertIn("native", modes["local-receipt-gate"]["why"])


class TestDarwinLinuxShapeCheck(unittest.TestCase):
    """Darwin host runs native POSIX leg without probing wsl.exe."""

    def test_host_is_windows_false_on_darwin(self):
        with mock.patch.object(lsc.sys, "platform", "darwin"):
            self.assertFalse(lsc.host_is_windows())

    def test_main_runs_native_leg_on_darwin_without_probing_wsl(self):
        with mock.patch.object(lsc.sys, "platform", "darwin"), \
                mock.patch.object(lsc.sys, "argv", ["linux_shape_check.py"]), \
                mock.patch.object(lsc, "get_repo_root", return_value=REPO_ROOT), \
                mock.patch.object(lsc, "get_changed_files", return_value=["tests/chat-render.test.mjs"]), \
                mock.patch.object(lsc, "run_native_leg", return_value=0) as mock_native, \
                mock.patch.object(lsc, "wsl_status", side_effect=AssertionError("wsl_status must not be called on darwin")), \
                mock.patch("builtins.print"):
            rc = lsc.main()
        self.assertEqual(rc, 0)
        mock_native.assert_called_once()


class TestDarwinLaunchTuiBashResolution(unittest.TestCase):
    """macOS (darwin) bash resolution in tools/launch_tui.py."""

    def test_find_terminal_darwin_prefers_homebrew_arm64(self):
        existing = {"/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash"}
        with mock.patch.object(launch_tui.sys, "platform", "darwin"), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(launch_tui.shutil, "which", return_value=None), \
                mock.patch.object(launch_tui.os.path, "exists", side_effect=lambda p: p in existing):
            term, cmd_builder, is_wt = launch_tui.find_terminal()
        self.assertEqual(term, "/opt/homebrew/bin/bash")
        self.assertFalse(is_wt)
        cmd = cmd_builder("scripts/tui.sh", "Aesop")
        self.assertEqual(cmd[0], "/opt/homebrew/bin/bash")

    def test_find_terminal_darwin_falls_back_to_usr_local_and_bin_bash(self):
        existing_intel = {"/usr/local/bin/bash", "/bin/bash"}
        with mock.patch.object(launch_tui.sys, "platform", "darwin"), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(launch_tui.shutil, "which", return_value=None), \
                mock.patch.object(launch_tui.os.path, "exists", side_effect=lambda p: p in existing_intel):
            term, _, _ = launch_tui.find_terminal()
        self.assertEqual(term, "/usr/local/bin/bash")

        existing_sys = {"/bin/bash"}
        with mock.patch.object(launch_tui.sys, "platform", "darwin"), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(launch_tui.shutil, "which", return_value=None), \
                mock.patch.object(launch_tui.os.path, "exists", side_effect=lambda p: p in existing_sys):
            term, _, _ = launch_tui.find_terminal()
        self.assertEqual(term, "/bin/bash")

    def test_main_darwin_spawns_with_start_new_session_and_no_creationflags(self):
        fake_proc = mock.MagicMock(pid=4242)
        existing = {"/tmp/tui.sh", "/bin/bash"}
        with mock.patch.object(launch_tui.sys, "platform", "darwin"), \
                mock.patch.object(launch_tui.sys, "argv", ["launch_tui.py", "--script", "/tmp/tui.sh"]), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(launch_tui.shutil, "which", return_value=None), \
                mock.patch.object(launch_tui.os.path, "exists", side_effect=lambda p: p in existing), \
                mock.patch.object(launch_tui.subprocess, "Popen", return_value=fake_proc) as mock_popen, \
                mock.patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                launch_tui.main()
        self.assertEqual(cm.exception.code, 0)
        kwargs = mock_popen.call_args.kwargs
        self.assertTrue(kwargs.get("start_new_session"))
        self.assertNotIn("creationflags", kwargs)


class TestNoLiteralUserPathsRegression(unittest.TestCase):
    """Ensure hardcoded personal Windows/user paths stay out of checked files."""

    def test_no_literal_personal_user_paths(self):
        # Assemble patterns at runtime so this test file itself stays clean.
        forbidden = [
            "C:/Users/" + "Mattt",
            "C:/Users/" + "matt8",
            "C:\\Users\\" + "Mattt",
            "C:\\Users\\" + "matt8",
        ]
        targets = [
            REPO_ROOT / "tests" / "chat-render.test.mjs",
            REPO_ROOT / "tools" / "launch_tui.py",
        ]
        ci_to_green = REPO_ROOT / ".claude" / "workflows" / "ci-to-green.js"
        if ci_to_green.exists():
            targets.append(ci_to_green)

        for path in targets:
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in forbidden:
                self.assertFalse(
                    needle in text,
                    f"Hardcoded user path {needle!r} found in {path.relative_to(REPO_ROOT)}",
                )


if __name__ == "__main__":
    unittest.main()
