"""
Tests for daemons/install-tasks.ps1 -- plan mode only.

The installer's -Plan switch computes what it would do from an explicit
-ExistingTasksJson fixture and prints JSON. -Plan never calls a Task Scheduler
cmdlet, and every invocation in this file runs with AESOP_INSTALL_TASKS_DRY_RUN=1,
so the installer's own guard is exercised on the same path.

Tests:
- No existing task -> decision 'register'.
- Existing task identical to the spec -> 'unchanged'.
- Interval, command, and principal divergence -> 're-register-because-<field>-diverged'.
- Legacy wscript action at the root -> re-register on action.Execute + action.Arguments.
- Same-named task outside the root folder is ignored, never managed.
- AESOP_INSTALL_TASKS_DRY_RUN=1 refuses the real path (-Uninstall and bare install).
- This file never names a scheduler cmdlet (source guard).

SKIP on non-Windows platforms.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "daemons" / "install-tasks.ps1"
TASK_PREFIX = "AesopPlanTest"
WATCHDOG_NAME = TASK_PREFIX + "WatchdogDaemon"
ROOT_FOLDER = "\\"
LEGACY_FOLDER = "\\Aesop\\"


def _plan_env():
    env = dict(os.environ)
    env["AESOP_INSTALL_TASKS_DRY_RUN"] = "1"
    return env


def _run_installer(args, env=None):
    cmd = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
    ] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env if env is not None else _plan_env(),
    )


@unittest.skipUnless(sys.platform == "win32", "install-tasks.ps1 is Windows-only")
class InstallTasksPlanTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_fixture(self, records):
        path = self.tmpdir / "existing.json"
        path.write_text(json.dumps(records), encoding="utf-8")
        return path

    def _plan(self, records):
        fixture = self._write_fixture(records)
        result = _run_installer(
            [
                "-Plan",
                "-ExistingTasksJson", str(fixture),
                "-TaskPrefix", TASK_PREFIX,
                "-WatchdogCommand", "bash -c 'echo watchdog'",
            ]
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["mode"], "plan")
        return plan

    def _watchdog_item(self, plan):
        matches = [t for t in plan["tasks"] if t["name"] == WATCHDOG_NAME]
        self.assertEqual(len(matches), 1, plan)
        return matches[0]

    def _root_record(self, execute, arguments, logon="Interactive", interval=5):
        return {
            "TaskName": WATCHDOG_NAME,
            "TaskPath": ROOT_FOLDER,
            "Execute": execute,
            "Arguments": arguments,
            "LogonType": logon,
            "IntervalMinutes": interval,
        }

    def test_no_existing_task_registers(self):
        plan = self._plan([])
        item = self._watchdog_item(plan)
        self.assertEqual(item["decision"], "register")
        self.assertEqual(item["divergedFields"], [])
        self.assertEqual(item["intervalMinutes"], 5)
        self.assertEqual(item["logonType"], "Interactive")
        self.assertTrue(item["execute"].lower().endswith("daemons\\bin\\run-hidden.exe"), item["execute"])
        self.assertIn("-lc", item["arguments"])
        self.assertIn("echo watchdog", item["arguments"])
        self.assertNotIn("wscript", item["execute"].lower())

    def test_identical_existing_task_is_unchanged(self):
        spec = self._watchdog_item(self._plan([]))
        plan = self._plan([self._root_record(spec["execute"], spec["arguments"])])
        item = self._watchdog_item(plan)
        self.assertEqual(item["decision"], "unchanged")
        self.assertEqual(item["divergedFields"], [])

    def test_interval_divergence_reregisters(self):
        spec = self._watchdog_item(self._plan([]))
        plan = self._plan([self._root_record(spec["execute"], spec["arguments"], interval=20)])
        item = self._watchdog_item(plan)
        self.assertEqual(item["decision"], "re-register-because-trigger.interval-diverged")
        self.assertEqual(item["divergedFields"], ["trigger.interval"])

    def test_command_divergence_reregisters(self):
        spec = self._watchdog_item(self._plan([]))
        plan = self._plan([self._root_record(spec["execute"], '"bash" -lc "echo other"')])
        item = self._watchdog_item(plan)
        self.assertEqual(item["decision"], "re-register-because-action.Arguments-diverged")
        self.assertEqual(item["divergedFields"], ["action.Arguments"])

    def test_principal_divergence_reregisters(self):
        spec = self._watchdog_item(self._plan([]))
        plan = self._plan([self._root_record(spec["execute"], spec["arguments"], logon="Password")])
        item = self._watchdog_item(plan)
        self.assertEqual(item["decision"], "re-register-because-principal.LogonType-diverged")
        self.assertEqual(item["divergedFields"], ["principal.LogonType"])

    def test_legacy_wscript_action_reregisters_on_execute_and_arguments(self):
        legacy = self._root_record(
            "wscript.exe",
            '//B //Nologo "C:\\aesop\\daemons\\run-hidden.vbs" "bash.exe" -lc "bash x"',
        )
        item = self._watchdog_item(self._plan([legacy]))
        self.assertEqual(
            item["decision"],
            "re-register-because-action.Execute+action.Arguments-diverged",
        )

    def test_legacy_folder_copy_is_ignored_and_root_registers(self):
        legacy_copy = {
            "TaskName": WATCHDOG_NAME,
            "TaskPath": LEGACY_FOLDER,
            "Execute": "wscript.exe",
            "Arguments": "legacy",
            "LogonType": "Interactive",
            "IntervalMinutes": 5,
        }
        plan = self._plan([legacy_copy])
        item = self._watchdog_item(plan)
        self.assertEqual(item["decision"], "register")
        ignored = [i for i in plan["ignored"] if i["name"] == WATCHDOG_NAME]
        self.assertEqual(len(ignored), 1, plan)
        self.assertEqual(ignored[0]["taskPath"], LEGACY_FOLDER)

    def test_guard_refuses_real_install_path(self):
        result = _run_installer(
            ["-TaskPrefix", TASK_PREFIX, "-WatchdogCommand", "bash -c 'echo x'"],
            env=_plan_env(),
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("AESOP_INSTALL_TASKS_DRY_RUN=1", result.stdout + result.stderr)

    def test_guard_refuses_uninstall_path(self):
        result = _run_installer(["-Uninstall", "-TaskPrefix", TASK_PREFIX], env=_plan_env())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("AESOP_INSTALL_TASKS_DRY_RUN=1", result.stdout + result.stderr)

    def test_test_file_names_no_scheduler_cmdlet(self):
        # Source guard: this file must not name a Task Scheduler cmdlet, so no test path can
        # reach one. The pattern is spelled out in pieces so this check does not match itself.
        text = Path(__file__).read_text(encoding="utf-8")
        hits = re.findall(r"(Register|Unregister|Get|Set)-Scheduled", text)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
