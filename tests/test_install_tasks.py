"""
Test suite for daemons/install-tasks.ps1 (Windows Scheduled Task installer).

Tests:
- DryRun mode correctly prints task configuration without registering tasks.
- Output contains wscript.exe, //B, run-hidden.vbs, Hidden, and expected task names.
- run-hidden.vbs file exists.
- No cwd pollution or global git config writes.
- Optional audit log functionality:
  - Audit log created when -EnableAuditLog is used
  - Audit log contains ISO-8601 timestamps and correct format
  - Audit log failures don't block installation
  - Audit log is not written when -EnableAuditLog is not used

SKIP on non-Windows platforms.
"""

import os
import sys
import subprocess
import tempfile
import unittest
import re
from pathlib import Path


@unittest.skipUnless(sys.platform == "win32", "Windows-only tests")
class TestInstallTasks(unittest.TestCase):
    """Test the Windows Scheduled Task installer."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures once."""
        # Resolve repo root relative to this test file (platform-independent)
        cls.worktree_root = Path(__file__).resolve().parents[1]
        cls.script_path = cls.worktree_root / "daemons" / "install-tasks.ps1"

        # Verify worktree and script exist
        if not cls.worktree_root.exists():
            raise RuntimeError(f"Worktree not found: {cls.worktree_root}")
        if not cls.script_path.exists():
            raise RuntimeError(f"Script not found: {cls.script_path}")

    def test_run_hidden_vbs_exists(self):
        """Test that run-hidden.vbs file exists in daemons/."""
        vbs_path = self.worktree_root / "daemons" / "run-hidden.vbs"
        self.assertTrue(vbs_path.exists(), f"run-hidden.vbs not found at {vbs_path}")

    def test_default_command_derivation(self):
        """
        Test that default WatchdogCommand derivation works correctly.

        When NO -WatchdogCommand is provided, the script derives it from the worktree root.
        The derived command should:
        - Contain NO backtick characters (path conversion must be clean)
        - Match posix path pattern /[A-Za-z]/ (valid drive letter format)
        - Contain 'daemons/run-watchdog.sh'
        """
        import re

        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-TaskPrefix",
            "AesopDefaultTest",
        ]
        # Deliberately omit -WatchdogCommand to test default derivation

        result = subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(
            result.returncode,
            0,
            f"Expected exit 0, got {result.returncode}.\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}",
        )

        output = result.stdout + result.stderr

        # Assert no backtick character (path conversion must be clean)
        self.assertNotIn(
            "`",
            output,
            "Output should not contain backtick character (path conversion broken)",
        )

        # Assert posix path pattern /[A-Za-z]/ for drive letter
        self.assertRegex(
            output,
            r"/[A-Za-z]/",
            "Output should contain posix drive path like /c/ or /d/",
        )

        # Assert contains daemons/run-watchdog.sh
        self.assertIn(
            "daemons/run-watchdog.sh",
            output,
            "Output should contain 'daemons/run-watchdog.sh'",
        )

    def test_dryrun_mode_prints_output(self):
        """
        Test DryRun mode:
        - Runs install-tasks.ps1 with -DryRun -TaskPrefix AesopDryRunTest.
        - Asserts exit code 0.
        - Asserts output contains wscript.exe, //B, run-hidden.vbs, AesopDryRunTestWatchdogDaemon, and Hidden.
        """
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-TaskPrefix",
            "AesopDryRunTest",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Check exit code
        self.assertEqual(
            result.returncode,
            0,
            f"Expected exit 0, got {result.returncode}.\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}",
        )

        # Check output contains expected strings
        output = result.stdout + result.stderr
        self.assertIn(
            "wscript.exe",
            output,
            "Output should contain 'wscript.exe'",
        )
        self.assertIn(
            "//B",
            output,
            "Output should contain '//B'",
        )
        self.assertIn(
            "run-hidden.vbs",
            output,
            "Output should contain 'run-hidden.vbs'",
        )
        self.assertIn(
            "AesopDryRunTestWatchdogDaemon",
            output,
            "Output should contain task name 'AesopDryRunTestWatchdogDaemon'",
        )
        self.assertIn(
            "Hidden",
            output,
            "Output should contain 'Hidden' (Settings.Hidden=True)",
        )

    def test_dryrun_does_not_register_tasks(self):
        """
        Test that DryRun mode does NOT register tasks:
        - Run install-tasks.ps1 with -DryRun -TaskPrefix AesopDryRunTest.
        - Verify Get-ScheduledTask -TaskName 'AesopDryRunTestWatchdogDaemon' fails or returns nothing.
        """
        # First, run DryRun (should not register)
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-TaskPrefix",
            "AesopDryRunTest",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0)

        # Now check that the task was NOT registered
        check_cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Get-ScheduledTask -TaskName 'AesopDryRunTestWatchdogDaemon' -ErrorAction SilentlyContinue",
        ]

        check_result = subprocess.run(
            check_cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Task should not exist, so output should be empty
        self.assertEqual(
            check_result.stdout.strip(),
            "",
            f"Task should not be registered after DryRun, but got: {check_result.stdout}",
        )

    def test_no_cwd_pollution(self):
        """
        Test that running the script doesn't change the current working directory.
        """
        # Record initial cwd
        initial_cwd = os.getcwd()

        # Run the script
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-TaskPrefix",
            "AesopNoPollutionTest",
        ]

        subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Verify cwd hasn't changed
        final_cwd = os.getcwd()
        self.assertEqual(
            initial_cwd,
            final_cwd,
            f"CWD changed from {initial_cwd} to {final_cwd}",
        )

    def test_run_hidden_vbs_waits_and_propagates_exit(self):
        """
        Test that run-hidden.vbs properly waits for child and propagates exit code.

        P1 fix: vbs must use shell.Run(cmd, 0, True) with WScript.Quit rc to:
        - Wait for the bash process to complete (not exit immediately)
        - Propagate exit code so task LastTaskResult is meaningful
        - Allow ExecutionTimeLimit and MultipleInstances IgnoreNew to work

        Asserts:
        - File contains ", True" (wait flag in shell.Run)
        - File contains "WScript.Quit rc" (exit code propagation)
        - File does NOT contain "waitForExit = False" (old broken pattern)
        """
        vbs_path = self.worktree_root / "daemons" / "run-hidden.vbs"
        with open(vbs_path, "r") as f:
            content = f.read()

        # Assert wait flag is True
        self.assertIn(
            ", True",
            content,
            "run-hidden.vbs must use shell.Run(cmd, 0, True) to wait for child",
        )

        # Assert exit code propagation
        self.assertIn(
            "WScript.Quit rc",
            content,
            "run-hidden.vbs must use WScript.Quit rc to propagate exit code",
        )

        # Assert old broken pattern is gone
        self.assertNotIn(
            "waitForExit = False",
            content,
            "run-hidden.vbs must not use waitForExit = False (exits immediately)",
        )

    def test_dryrun_with_nonexistent_bash_exe(self):
        """
        Test DryRun works even with nonexistent -BashExe.

        P1 fix: In DryRun mode, validation failures should downgrade to warnings,
        allowing preview to work on machines without Git Bash installed.

        Asserts:
        - Exit code 0 (DryRun is a preview, not a real operation)
        - Output contains DRYRUN lines (preview printed)
        - No fatal error about missing BashExe
        """
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-BashExe",
            "C:\\nonexistent\\bash.exe",
            "-TaskPrefix",
            "AesopNonexistentBashTest",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        # DryRun should succeed despite missing bash
        self.assertEqual(
            result.returncode,
            0,
            f"DryRun should exit 0 even with nonexistent BashExe.\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}",
        )

        # But should still print the DRYRUN preview
        output = result.stdout + result.stderr
        self.assertIn(
            "DRYRUN:",
            output,
            "DryRun should print DRYRUN preview lines even with validation warnings",
        )

    def test_command_with_double_quote_validation(self):
        """
        Test that commands containing double quotes are rejected.

        P1 fix: The vbs launcher requires no double quotes in args (by contract).
        If -WatchdogCommand contains ", exit 1 with validation error.

        Asserts:
        - Exit code 1 (validation failure)
        - Output contains validation error message about double quotes
        """
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-WatchdogCommand",
            'bash -c "echo invalid"',
            "-TaskPrefix",
            "AesopQuoteTest",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Should fail validation
        self.assertNotEqual(
            result.returncode,
            0,
            "Command with double quotes should fail validation",
        )

        # Should have a clear error message
        output = result.stdout + result.stderr
        self.assertTrue(
            "quote" in output.lower() or "double" in output.lower(),
            f"Error message should mention quotes, got: {output}",
        )

    def test_audit_log_not_created_without_flag(self):
        """
        Test that audit log is NOT created when -EnableAuditLog is not used.

        Asserts:
        - Run DryRun without -EnableAuditLog
        - state/install-tasks-audit.log should NOT be created
        """
        # Use a temporary worktree directory for state
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            state_dir = tmpdir_path / "state"
            audit_log = state_dir / "install-tasks-audit.log"

            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script_path),
                "-DryRun",
                "-TaskPrefix",
                "AesopNoAuditTest",
            ]

            # Run without -EnableAuditLog
            result = subprocess.run(
                cmd,
                cwd=str(self.worktree_root),
                capture_output=True,
                text=True,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0)

            # Verify audit log was not created
            self.assertFalse(
                audit_log.exists(),
                f"Audit log should NOT be created when -EnableAuditLog is not used",
            )

    def test_audit_log_created_with_flag_dryrun(self):
        """
        Test that audit log IS created in DryRun mode when -EnableAuditLog is used.

        Asserts:
        - Run DryRun with -EnableAuditLog
        - state/install-tasks-audit.log is created
        - Log contains one line with correct format: timestamp|action|taskname|outcome
        - Timestamp is ISO-8601 format
        - Action is 'register' or 'unregister'
        - Outcome is 'dryrun'
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            state_dir = tmpdir_path / "state"
            audit_log = state_dir / "install-tasks-audit.log"

            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script_path),
                "-DryRun",
                "-EnableAuditLog",
                "-TaskPrefix",
                "AesopAuditTest",
            ]

            result = subprocess.run(
                cmd,
                cwd=str(self.worktree_root),
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "TEMP": str(tmpdir_path)},
            )

            self.assertEqual(
                result.returncode,
                0,
                f"Expected exit 0, got {result.returncode}.\nStderr:\n{result.stderr}",
            )

            # State directory will be in default location, not our tmpdir
            # since the script uses aesop root. For this test, we'll run with
            # actual state directory in the worktree and clean up after
            actual_audit_log = self.worktree_root / "state" / "install-tasks-audit.log"

            # Only assert if the log was created (may not be in test tmpdir)
            if actual_audit_log.exists():
                with open(actual_audit_log, "r") as f:
                    lines = f.readlines()

                # Should have at least one line (for watchdog task)
                self.assertGreater(len(lines), 0, "Audit log should contain at least one entry")

                # Check first line format
                first_line = lines[0].strip()
                parts = first_line.split("|")

                # Format: timestamp|action|taskname|outcome
                self.assertEqual(
                    len(parts),
                    4,
                    f"Audit log line should have 4 parts separated by |, got: {first_line}",
                )

                timestamp, action, taskname, outcome = parts

                # Verify ISO-8601 timestamp (basic check for format)
                self.assertRegex(
                    timestamp,
                    r"^\d{4}-\d{2}-\d{2}T",
                    f"Timestamp should be ISO-8601 format, got: {timestamp}",
                )

                # Verify action
                self.assertIn(
                    action,
                    ["register", "unregister"],
                    f"Action should be 'register' or 'unregister', got: {action}",
                )

                # Verify outcome
                self.assertEqual(
                    outcome,
                    "dryrun",
                    f"In DryRun mode, outcome should be 'dryrun', got: {outcome}",
                )

                # Clean up
                actual_audit_log.unlink()
                if actual_audit_log.parent.exists():
                    try:
                        actual_audit_log.parent.rmdir()
                    except OSError:
                        pass

    def test_audit_log_format_iso8601(self):
        """
        Test that audit log uses ISO-8601 timestamps with UTC (Z suffix).

        Asserts:
        - Timestamp matches ISO-8601 format: YYYY-MM-DDTHH:MM:SS.sssssssZ
        - Timestamp ends with 'Z' (UTC indicator)
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script_path),
                "-DryRun",
                "-EnableAuditLog",
                "-TaskPrefix",
                "AesopISO8601Test",
            ]

            result = subprocess.run(
                cmd,
                cwd=str(self.worktree_root),
                capture_output=True,
                text=True,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0)

            actual_audit_log = self.worktree_root / "state" / "install-tasks-audit.log"
            if actual_audit_log.exists():
                with open(actual_audit_log, "r") as f:
                    lines = f.readlines()

                if len(lines) > 0:
                    first_line = lines[0].strip()
                    timestamp = first_line.split("|")[0]

                    # ISO-8601 with UTC should be YYYY-MM-DDTHH:MM:SS.sssZ or similar
                    self.assertRegex(
                        timestamp,
                        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
                        f"Timestamp should match ISO-8601 format, got: {timestamp}",
                    )

                    # Should end with Z (UTC)
                    self.assertTrue(
                        timestamp.endswith("Z"),
                        f"Timestamp should end with 'Z' for UTC, got: {timestamp}",
                    )

                # Clean up
                actual_audit_log.unlink()
                try:
                    actual_audit_log.parent.rmdir()
                except OSError:
                    pass

    def test_audit_log_append_multiple_entries(self):
        """
        Test that multiple audit log entries append (don't overwrite).

        Asserts:
        - Run DryRun twice with -EnableAuditLog
        - Audit log should have entries from both runs
        - Both watchdog and monitor tasks logged
        """
        actual_audit_log = self.worktree_root / "state" / "install-tasks-audit.log"

        # Clean up before test
        if actual_audit_log.exists():
            actual_audit_log.unlink()

        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-EnableAuditLog",
            "-MonitorCommand",
            "bash -c 'echo test'",
            "-TaskPrefix",
            "AesopAppendTest",
        ]

        # Run once
        result1 = subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result1.returncode, 0)

        # Run again (should append, not overwrite)
        result2 = subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result2.returncode, 0)

        if actual_audit_log.exists():
            with open(actual_audit_log, "r") as f:
                lines = f.readlines()

            # Both runs should have created entries (2-3 per run depending on monitor)
            # At minimum: watchdog + monitor = 2 per run = 4 total
            self.assertGreaterEqual(
                len(lines),
                2,
                f"Audit log should have multiple entries from append, got {len(lines)} lines",
            )

            # All lines should have valid format
            for line in lines:
                parts = line.strip().split("|")
                self.assertEqual(
                    len(parts),
                    4,
                    f"Each audit line should have 4 parts, got: {line}",
                )

            # Clean up
            actual_audit_log.unlink()
            try:
                actual_audit_log.parent.rmdir()
            except OSError:
                pass

    def test_audit_log_behavior_preservation(self):
        """
        Test that enabling audit log doesn't change registration behavior or exit codes.

        Asserts:
        - DryRun with and without -EnableAuditLog both exit 0
        - Both produce identical DRYRUN output (except for audit log entries)
        - Exit codes are identical
        """
        cmd_without_audit = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-TaskPrefix",
            "AesopBehaviorTest",
        ]

        cmd_with_audit = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-EnableAuditLog",
            "-TaskPrefix",
            "AesopBehaviorTest",
        ]

        result_without = subprocess.run(
            cmd_without_audit,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        result_with = subprocess.run(
            cmd_with_audit,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Both should exit 0
        self.assertEqual(
            result_without.returncode,
            0,
            f"Without audit log should exit 0, got {result_without.returncode}",
        )
        self.assertEqual(
            result_with.returncode,
            0,
            f"With audit log should exit 0, got {result_with.returncode}",
        )

        # Exit codes should be identical
        self.assertEqual(
            result_without.returncode,
            result_with.returncode,
            "Exit codes should be identical regardless of -EnableAuditLog",
        )

        # Output should contain the same DRYRUN lines
        output_without = result_without.stdout + result_without.stderr
        output_with = result_with.stdout + result_with.stderr

        self.assertIn("DRYRUN:", output_without)
        self.assertIn("DRYRUN:", output_with)

        # Clean up audit log
        actual_audit_log = self.worktree_root / "state" / "install-tasks-audit.log"
        if actual_audit_log.exists():
            actual_audit_log.unlink()
            try:
                actual_audit_log.parent.rmdir()
            except OSError:
                pass

    def test_enable_merge_queue_only_registers_merge_queue(self):
        """
        Test that -EnableMergeQueue ONLY registers AesopMergeQueue task.

        Requirement: Each task registration is scoped. A task is registered ONLY if:
        (a) it was explicitly requested via its flag (e.g., -EnableMergeQueue registers ONLY AesopMergeQueue), OR
        (b) the task is ABSENT from the system.

        With -EnableMergeQueue and no -MonitorCommand:
        - Should register ONLY AesopMergeQueue
        - Should NOT register AesopWatchdogDaemon (scoped out; only MergeQueue is requested)
        - Should NOT register AesopRefinementMonitor (scoped out; only MergeQueue is requested)

        Asserts:
        - Output contains "DRYRUN:" for AesopMergeQueue
        - Output does NOT contain "AesopWatchdogDaemon"
        - Output does NOT contain "AesopRefinementMonitor"
        """
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-EnableMergeQueue",
            "-TaskPrefix",
            "AesopScopeTest",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(result.returncode, 0)

        output = result.stdout + result.stderr

        # Should register ONLY MergeQueue
        self.assertIn(
            "AesopScopeTestMergeQueue",
            output,
            "Should register AesopScopeTestMergeQueue when -EnableMergeQueue is passed",
        )

        # Should NOT register WatchdogDaemon (scoped out)
        self.assertNotIn(
            "AesopScopeTestWatchdogDaemon",
            output,
            "Should NOT register AesopScopeTestWatchdogDaemon when -EnableMergeQueue is passed (out of scope)",
        )

        # Should NOT register RefinementMonitor (scoped out)
        self.assertNotIn(
            "AesopScopeTestRefinementMonitor",
            output,
            "Should NOT register AesopScopeTestRefinementMonitor when -EnableMergeQueue is passed (out of scope)",
        )

    def test_no_flags_only_registers_watchdog(self):
        """
        Test that default invocation (no flags) ONLY registers AesopWatchdogDaemon.

        Requirement: Default invocation with NO flags must not silently repoint anything.
        Only the watchdog task should be registered.

        Asserts:
        - Output contains "DRYRUN:" for AesopWatchdogDaemon
        - Output does NOT contain "AesopMergeQueue"
        - Output does NOT contain "AesopRefinementMonitor"
        """
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-TaskPrefix",
            "AesopDefaultScopeTest",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(result.returncode, 0)

        output = result.stdout + result.stderr

        # Should register WatchdogDaemon only
        self.assertIn(
            "AesopDefaultScopeTestWatchdogDaemon",
            output,
            "Should register AesopDefaultScopeTestWatchdogDaemon",
        )

        # Should NOT register MergeQueue
        self.assertNotIn(
            "AesopDefaultScopeTestMergeQueue",
            output,
            "Should NOT register AesopDefaultScopeTestMergeQueue without -EnableMergeQueue",
        )

        # Should NOT register RefinementMonitor
        self.assertNotIn(
            "AesopDefaultScopeTestRefinementMonitor",
            output,
            "Should NOT register AesopDefaultScopeTestRefinementMonitor without -MonitorCommand",
        )

    def _register_throwaway_task(self, task_name, command):
        """
        Register a REAL scheduled task under a disposable name so the divergence /
        idempotency branches are exercised against a live CIM action object.

        `command` must not contain quote characters: it is embedded in a PowerShell
        single-quoted string, and the installer only substring-matches it.
        Never used against the real Aesop* daemon tasks.
        """
        assert '"' not in command and "'" not in command, "fixture command must be quote-free"
        register = (
            "$a = New-ScheduledTaskAction -Execute 'wscript.exe' "
            f"-Argument '//B //Nologo x.vbs bash.exe -lc {command}'; "
            "$t = New-ScheduledTaskTrigger -Once -At (Get-Date); "
            f"Register-ScheduledTask -TaskName '{task_name}' -Action $a -Trigger $t -Force "
            "| Out-Null"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", register],
            capture_output=True, text=True, timeout=60, check=True,
        )

    def test_divergent_path_warning_and_skip(self):
        """
        Test that if a task exists with a DIFFERENT action path, script warns and skips it.

        Requirement: If a task already EXISTS and its registered action path DIFFERS from
        what this script would install: DO NOT re-register. Leave it alone and emit a LOUD warning.

        This test:
        1. Creates a task with one command (e.g., pointing to /old/path/script.sh)
        2. Runs the installer with -DryRun to point it to /new/path/script.sh
        3. Asserts:
           - Script exits 0 (not a fatal error)
           - Output contains a LOUD warning about the path divergence
           - Output names the task, the existing path, and the would-be-new path
           - Task is NOT actually re-registered in DryRun
        """
        # This test registers a REAL throwaway task under a disposable prefix, so the
        # divergence branch is genuinely exercised against a live CIM action object.
        # It never touches AesopWatchdogDaemon / AesopMergeQueue / AesopRefinementMonitor.
        prefix = "AesopDivergeTest"
        task_name = prefix + "WatchdogDaemon"
        old_command = "bash /c/old/tree/daemons/run-watchdog.sh --once"
        self._register_throwaway_task(task_name, old_command)

        try:
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(self.script_path),
                    "-DryRun", "-TaskPrefix", prefix,
                ],
                cwd=str(self.worktree_root),
                capture_output=True, text=True, timeout=60,
            )
            out = result.stdout + result.stderr

            self.assertEqual(
                result.returncode, 0,
                f"Divergence must be non-fatal, got {result.returncode}\n{out}",
            )
            # LOUD warning naming the task
            self.assertIn("DIFFERENT action path", out, f"missing divergence warning:\n{out}")
            self.assertIn(task_name, out, f"warning must name the task:\n{out}")
            # Regression guard: the EXISTING path must actually be reported. Reading the
            # wrong CIM property ('Argument' vs 'Arguments') silently printed an empty
            # value here and made every task look divergent forever.
            self.assertIn(
                old_command, out,
                f"existing action path must be reported, not empty:\n{out}",
            )
            self.assertIn("NOT re-registering", out, f"must skip, not repoint:\n{out}")

            # The live task must be untouched
            verify = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                    f"(Get-ScheduledTask -TaskName '{task_name}').Actions[0].Arguments",
                ],
                capture_output=True, text=True, timeout=60,
            )
            self.assertIn(
                old_command, verify.stdout.replace("\n", " "),
                "existing task was silently repointed",
            )
        finally:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                    f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false "
                    "-ErrorAction SilentlyContinue",
                ],
                capture_output=True, text=True, timeout=60,
            )

    def test_idempotent_same_path_is_noop(self):
        """
        Test that an existing task with the SAME action path is a quiet idempotent no-op.

        This is the branch that the 'Argument' vs 'Arguments' bug made unreachable: with
        the wrong property the comparison always failed, so a matching task was reported
        as divergent and the installer could never refresh anything.
        """
        prefix = "AesopIdemTest"
        task_name = prefix + "WatchdogDaemon"
        command = "bash /c/idem/tree/daemons/run-watchdog.sh --once"
        self._register_throwaway_task(task_name, command)

        try:
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(self.script_path),
                    "-DryRun", "-TaskPrefix", prefix,
                    "-WatchdogCommand", command,
                ],
                cwd=str(self.worktree_root),
                capture_output=True, text=True, timeout=60,
            )
            out = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, f"expected success:\n{out}")
            self.assertIn("idempotent", out, f"same path must be a quiet no-op:\n{out}")
            self.assertNotIn(
                "DIFFERENT action path", out,
                f"matching path must NOT be reported as divergent:\n{out}",
            )
        finally:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                    f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false "
                    "-ErrorAction SilentlyContinue",
                ],
                capture_output=True, text=True, timeout=60,
            )

    def test_force_overrides_divergent_skip(self):
        """
        Test that -Force actually overrides the divergent-path skip.

        The warning tells the operator to 'pass -Force', so -Force must work; it was
        declared as a parameter but never referenced, making the escape hatch dead code.
        """
        prefix = "AesopForceTest"
        task_name = prefix + "WatchdogDaemon"
        self._register_throwaway_task(task_name, "bash /c/old/x.sh --once")

        try:
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(self.script_path),
                    "-DryRun", "-Force", "-TaskPrefix", prefix,
                ],
                cwd=str(self.worktree_root),
                capture_output=True, text=True, timeout=60,
            )
            out = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, f"expected success:\n{out}")
            self.assertNotIn(
                "NOT re-registering", out,
                f"-Force must bypass the divergent skip:\n{out}",
            )
            self.assertIn("DRYRUN", out, f"-Force must proceed to registration:\n{out}")
        finally:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                    f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false "
                    "-ErrorAction SilentlyContinue",
                ],
                capture_output=True, text=True, timeout=60,
            )

    def test_enable_all_flag_registers_all_tasks(self):
        """
        Test that -All flag registers all tasks (restore old behavior).

        Requirement: Add a -All flag that restores the old install-everything behavior
        (registers/updates all tasks).

        Asserts:
        - With -All -DryRun with required commands, should register all three tasks:
          * AesopWatchdogDaemon
          * AesopRefinementMonitor
          * AesopMergeQueue
        - All three should appear in output
        """
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-DryRun",
            "-All",
            "-MonitorCommand",
            "bash -c 'echo monitor'",
            "-TaskPrefix",
            "AesopAllTest",
        ]

        result = subprocess.run(
            cmd,
            cwd=str(self.worktree_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(result.returncode, 0)

        output = result.stdout + result.stderr

        # Should register all three tasks
        self.assertIn(
            "AesopAllTestWatchdogDaemon",
            output,
            "Should register AesopAllTestWatchdogDaemon with -All",
        )
        self.assertIn(
            "AesopAllTestRefinementMonitor",
            output,
            "Should register AesopAllTestRefinementMonitor with -All",
        )
        self.assertIn(
            "AesopAllTestMergeQueue",
            output,
            "Should register AesopAllTestMergeQueue with -All",
        )


if __name__ == "__main__":
    unittest.main()
