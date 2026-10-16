"""
Stub-driven tests for daemons/install-tasks.ps1 (the REAL install path, scheduler cmdlets stubbed).

This is the ONLY test file allowed to name Task Scheduler cmdlets. It shadows Get-ScheduledTask,
Register-ScheduledTask and Unregister-ScheduledTask with a recording stub, so the installer's
real registration branch runs without ever reaching the real scheduler. The sibling file
tests/test_install_tasks.py drives -Plan only and is guarded against naming those cmdlets.

Tests:
- Hermeticity: a seeded probe task is looked up through the stub and never leaks to the real scheduler.
- Interval change: an existing task with the same command but a different trigger interval is
  re-registered (REGISTER reaches the stub), never reported idempotent.
- Identical command and trigger: a quiet idempotent no-op with no REGISTER/UNREGISTER.

These tests do NOT set AESOP_INSTALL_TASKS_DRY_RUN: they exercise the real branch against stubs.

SKIP on non-Windows platforms.
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_PS_STUB_PRELUDE = r"""
# Import the real module FIRST, then define the stubs in this script scope so they shadow it.
# Without this, the installer's first New-ScheduledTask* call auto-imports the ScheduledTasks
# module into the installer scope, and Register-ScheduledTask then resolves to the REAL cmdlet.
Import-Module ScheduledTasks -ErrorAction Stop
$global:AesopStubDb = '@@DB@@'
$global:AesopStubLog = '@@LOG@@'
function Add-AesopStubLog([string]$Line) {
    Add-Content -LiteralPath $global:AesopStubLog -Value $Line -Encoding UTF8
}
function Get-AesopStubStore {
    $store = @{}
    if (Test-Path -LiteralPath $global:AesopStubDb) {
        foreach ($line in (Get-Content -LiteralPath $global:AesopStubDb -Encoding UTF8)) {
            $parts = $line -split "`t", 2
            if ($parts.Count -eq 2) { $store[$parts[0]] = $parts[1] }
        }
    }
    return $store
}
function Save-AesopStubStore($store) {
    $lines = @()
    foreach ($key in $store.Keys) { $lines += ($key + "`t" + $store[$key]) }
    Set-Content -LiteralPath $global:AesopStubDb -Value $lines -Encoding UTF8
}
function Get-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName, [string]$TaskPath)
    Add-AesopStubLog ("GET " + $TaskName)
    $store = Get-AesopStubStore
    if (-not $store.ContainsKey($TaskName)) {
        Write-Error "No MSFT_ScheduledTask objects found with property 'TaskName' equal to '$TaskName'"
        return $null
    }
    # CIM reports the repetition interval and duration as ISO-8601 duration strings (e.g. PT1H).
    # Seeded per task via "<name>|interval"; defaults to the installer's 5-minute watchdog default.
    $interval = 'PT5M'
    if ($store.ContainsKey($TaskName + '|interval')) { $interval = $store[$TaskName + '|interval'] }
    $execute = ''
    if ($store.ContainsKey($TaskName + '|execute')) { $execute = $store[$TaskName + '|execute'] }
    return [pscustomobject]@{
        TaskName  = $TaskName
        TaskPath  = '\'
        Actions   = @([pscustomobject]@{ Execute = $execute; Arguments = $store[$TaskName] })
        Principal = [pscustomobject]@{ LogonType = 'Interactive' }
        Triggers  = @([pscustomobject]@{ Repetition = [pscustomobject]@{ Interval = $interval; Duration = 'P3650D' } })
    }
}
function Register-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName, [string]$TaskPath, $Action, $Trigger, $Settings, [switch]$Force)
    Add-AesopStubLog ("REGISTER " + $TaskName)
    $store = Get-AesopStubStore
    $store[$TaskName] = [string]$Action.Arguments
    $store[$TaskName + '|execute'] = [string]$Action.Execute
    Save-AesopStubStore $store
    return $null
}
function Unregister-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName, [string]$TaskPath, [switch]$Confirm)
    Add-AesopStubLog ("UNREGISTER " + $TaskName)
    $store = Get-AesopStubStore
    if ($store.ContainsKey($TaskName)) {
        $store.Remove($TaskName)
        Save-AesopStubStore $store
    } else {
        Write-Error "No MSFT_ScheduledTask objects found with property 'TaskName' equal to '$TaskName'"
    }
}
"""


def _action_args(bash_exe, command):
    """Task action Arguments as the installer writes them: bash -lc <command>."""
    return f'"{bash_exe}" -lc "{command}"'


def _ps_literal(value):
    """PowerShell literal for one installer argument: bare -Switch tokens pass through,
    everything else becomes a single-quoted string with quotes doubled."""
    if re.fullmatch(r"-[A-Za-z]+", value):
        return value
    return "'" + value.replace("'", "''") + "'"


DEFAULT_BASH = r"C:\Program Files\Git\bin\bash.exe"


@unittest.skipUnless(sys.platform == "win32", "Windows-only tests")
class TestInstallTasksStubbed(unittest.TestCase):
    """Drive the real install path with the Task Scheduler cmdlets stubbed."""

    @classmethod
    def setUpClass(cls):
        cls.worktree_root = Path(__file__).resolve().parents[1]
        cls.script_path = cls.worktree_root / "daemons" / "install-tasks.ps1"
        if not cls.script_path.exists():
            raise RuntimeError(f"Script not found: {cls.script_path}")
        cls.launcher = str(cls.script_path.parent / "bin" / "run-hidden.exe")

    def _run_installer_stubbed(self, tmp, installer_args, tasks=None, intervals=None):
        """
        Run daemons/install-tasks.ps1 with its Task Scheduler cmdlets replaced by the
        recording stub in _PS_STUB_PRELUDE, so the real scheduler is never reached.
        `tasks` seeds the stub store as {task_name: (execute, arguments)};
        `intervals` seeds {task_name: ISO-8601 repetition interval} (e.g. "PT1H").
        Returns (CompletedProcess, stub_call_lines, final_store_text).
        """
        db = tmp / "scheduler-stub-tasks.tsv"
        log = tmp / "scheduler-stub-calls.log"
        rows = []
        for name, (execute, arguments) in (tasks or {}).items():
            rows.append(f"{name}\t{arguments}\n")
            rows.append(f"{name}|execute\t{execute}\n")
        rows += [f"{name}|interval\t{iv}\n" for name, iv in (intervals or {}).items()]
        db.write_text("".join(rows), encoding="utf-8")
        prelude = _PS_STUB_PRELUDE.replace("@@DB@@", str(db)).replace("@@LOG@@", str(log))
        call = " ".join(_ps_literal(arg) for arg in installer_args)
        wrapper = tmp / "run-installer-with-scheduler-stub.ps1"
        wrapper.write_text(
            prelude + f"\n& {_ps_literal(str(self.script_path))} {call}\nexit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
            cwd=str(self.worktree_root),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        calls = log.read_text(encoding="utf-8-sig").splitlines() if log.exists() else []
        return result, calls, db.read_text(encoding="utf-8-sig")

    def test_stubbed_run_never_reaches_real_scheduler(self):
        """
        Hermeticity guard. A seeded probe task must stay OFF the REAL Windows Task Scheduler:
        the installer's scheduler cmdlets go to the recording stub, so the stub log shows the
        probe lookup and a live `schtasks /query /tn` finds nothing.
        """
        prefix = "AesopHermeticProbe"
        task_name = prefix + "WatchdogDaemon"
        with tempfile.TemporaryDirectory() as tmp:
            result, calls, _store = self._run_installer_stubbed(
                Path(tmp), ["-DryRun", "-TaskPrefix", prefix],
                tasks={task_name: (self.launcher, _action_args(DEFAULT_BASH, "bash /c/probe/x.sh --once"))},
            )
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(f"GET {task_name}", calls, "stub seam was not used for the probe lookup")
        probe = subprocess.run(
            ["schtasks", "/query", "/tn", task_name],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertNotEqual(
            probe.returncode, 0,
            f"probe task leaked into the REAL scheduler:\n{probe.stdout}",
        )

    def test_interval_change_reregisters_not_idempotent(self):
        """
        An existing task with the SAME command but a DIFFERENT trigger interval must be
        re-registered, not reported as idempotent.

        Repro: a task registered at 60 minutes, reinstalled with -WatchdogIntervalMinutes 20.
        The command matches, so a command-only comparison would keep the 60-minute cadence.
        Expected: a re-registration at the requested interval (REGISTER reaches the stub).
        """
        prefix = "AesopIntervalTest"
        task_name = prefix + "WatchdogDaemon"
        command = "bash /c/iv/tree/daemons/run-watchdog.sh --once"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bash = tmp_path / "fake-bash.exe"
            fake_bash.write_bytes(b"")
            result, calls, _store = self._run_installer_stubbed(
                tmp_path,
                ["-WatchdogIntervalMinutes", "20", "-BashExe", str(fake_bash),
                 "-WatchdogCommand", command, "-TaskPrefix", prefix],
                tasks={task_name: (self.launcher, _action_args(str(fake_bash), command))},
                intervals={task_name: "PT1H"},
            )
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, f"expected success:\n{out}")
        self.assertIn("re-registering", out, f"interval mismatch must be reported as a re-registration:\n{out}")
        self.assertNotIn(
            "idempotent", out,
            f"60-minute task requested at 20 minutes must NOT be idempotent:\n{out}",
        )
        self.assertIn(
            f"REGISTER {task_name}", calls,
            f"interval mismatch must re-register the task; stub calls: {calls}",
        )

    def test_same_interval_and_command_is_idempotent(self):
        """
        An existing task whose action, principal and trigger interval all match is a quiet
        idempotent no-op: no REGISTER and no UNREGISTER reach the stub.
        """
        prefix = "AesopIntervalSameTest"
        task_name = prefix + "WatchdogDaemon"
        command = "bash /c/ivsame/tree/daemons/run-watchdog.sh --once"
        with tempfile.TemporaryDirectory() as tmp:
            result, calls, _store = self._run_installer_stubbed(
                Path(tmp),
                ["-WatchdogIntervalMinutes", "20", "-WatchdogCommand", command,
                 "-TaskPrefix", prefix, "-DryRun"],
                tasks={task_name: (self.launcher, _action_args(DEFAULT_BASH, command))},
                intervals={task_name: "PT20M"},
            )
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, f"expected success:\n{out}")
        self.assertIn("idempotent", out, f"same command and interval must be a no-op:\n{out}")
        self.assertNotIn("re-registering", out, f"same interval must not be re-registered:\n{out}")
        self.assertFalse(
            [c for c in calls if c.startswith(("REGISTER", "UNREGISTER"))],
            f"idempotent task must not be re-registered; stub calls: {calls}",
        )


if __name__ == "__main__":
    unittest.main()
