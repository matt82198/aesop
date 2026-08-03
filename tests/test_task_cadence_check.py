"""
Test suite for tools/task_cadence_check.py (scheduled-task cadence gate).

Covers the pure parsing/comparison layer with fixtures only -- no real
schtasks invocation, so the suite runs identically on Windows and Linux CI:

- ISO-8601 repetition-duration parsing (PT5M / PT1H / PT1H30M / P1D...).
- Expected-cadence extraction from daemons/install-tasks.ps1 source text
  (param defaults + ${TaskPrefix} interpolation + Register-DaemonTask binding).
- Live-task XML parsing (namespaced Task XML, UTF-16 declaration, Enabled
  default-true when the element is absent).
- Verdict logic: OK, interval mismatch, disabled task, missing task, query error.
- Real repo parse: the checked-in install-tasks.ps1 yields the documented cadences.
"""

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Load the tool by explicit path: tools/ is not a package, and a bare sibling
# import would be unresolvable to the G5 import-resolution gate.
_SPEC = importlib.util.spec_from_file_location(
    "task_cadence_check", REPO_ROOT / "tools" / "task_cadence_check.py"
)
tcc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tcc)


WATCHDOG_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <URI>\\AesopWatchdogDaemon</URI>
  </RegistrationInfo>
  <Settings>
    <Hidden>true</Hidden>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
  </Settings>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2026-07-23T12:59:10-05:00</StartBoundary>
      <Repetition>
        <Interval>PT5M</Interval>
        <Duration>P9999D</Duration>
      </Repetition>
    </TimeTrigger>
  </Triggers>
</Task>"""

HOURLY_MONITOR_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><URI>\\AesopRefinementMonitor</URI></RegistrationInfo>
  <Settings><Enabled>true</Enabled></Settings>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2026-07-23T12:59:10-05:00</StartBoundary>
      <Repetition><Interval>PT1H</Interval><Duration>P30D</Duration></Repetition>
    </TimeTrigger>
  </Triggers>
</Task>"""

DISABLED_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><URI>\\AesopWatchdogDaemon</URI></RegistrationInfo>
  <Settings><Enabled>false</Enabled></Settings>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>PT5M</Interval></Repetition>
    </TimeTrigger>
  </Triggers>
</Task>"""

NO_REPETITION_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><URI>\\AesopWatchdogDaemon</URI></RegistrationInfo>
  <Settings><Enabled>true</Enabled></Settings>
  <Triggers>
    <TimeTrigger><StartBoundary>2026-07-23T12:59:10-05:00</StartBoundary></TimeTrigger>
  </Triggers>
</Task>"""

PS1_FIXTURE = """param(
    [string]$BashExe = 'C:\\Program Files\\Git\\bin\\bash.exe',
    [int]$WatchdogIntervalMinutes = 5,
    [int]$MonitorIntervalMinutes = 20,
    [string]$TaskPrefix = 'Aesop'
)

function Register-DaemonTask {
    param([string]$TaskName, [int]$IntervalMinutes)
}

function Main {
    $watchdogTaskName = "${TaskPrefix}WatchdogDaemon"
    Register-DaemonTask `
        -TaskName $watchdogTaskName `
        -Command $WatchdogCommand `
        -IntervalMinutes $WatchdogIntervalMinutes `
        -BashExe $BashExe

    if ($MonitorCommand) {
        $monitorTaskName = "${TaskPrefix}RefinementMonitor"
        Register-DaemonTask `
            -TaskName $monitorTaskName `
            -Command $MonitorCommand `
            -IntervalMinutes $MonitorIntervalMinutes
    }
}
"""


class TestDurationParsing(unittest.TestCase):
    """ISO-8601 duration -> minutes."""

    def test_minutes(self):
        self.assertEqual(tcc.iso8601_to_minutes("PT5M"), 5.0)
        self.assertEqual(tcc.iso8601_to_minutes("PT20M"), 20.0)

    def test_hours(self):
        self.assertEqual(tcc.iso8601_to_minutes("PT1H"), 60.0)
        self.assertEqual(tcc.iso8601_to_minutes("PT1H30M"), 90.0)

    def test_days_and_seconds(self):
        self.assertEqual(tcc.iso8601_to_minutes("P1D"), 1440.0)
        self.assertEqual(tcc.iso8601_to_minutes("PT90S"), 1.5)
        self.assertEqual(tcc.iso8601_to_minutes("P32DT1H36M"), 32 * 1440 + 96)

    def test_invalid_raises(self):
        for bad in ("", "5M", "PT", "banana", None):
            with self.assertRaises(tcc.CadenceError):
                tcc.iso8601_to_minutes(bad)


class TestPs1Parsing(unittest.TestCase):
    """Expected cadences extracted from install-tasks.ps1 source text."""

    def test_fixture_yields_both_tasks(self):
        expected = tcc.parse_expected_cadences(PS1_FIXTURE)
        self.assertEqual(
            expected, {"AesopWatchdogDaemon": 5, "AesopRefinementMonitor": 20}
        )

    def test_function_definition_is_not_a_registration(self):
        # 'function Register-DaemonTask {' must not be mined as a call site.
        expected = tcc.parse_expected_cadences(PS1_FIXTURE)
        self.assertNotIn("", expected)
        self.assertEqual(len(expected), 2)

    def test_prefix_override_is_honoured(self):
        src = PS1_FIXTURE.replace("$TaskPrefix = 'Aesop'", "$TaskPrefix = 'Zed'")
        expected = tcc.parse_expected_cadences(src)
        self.assertIn("ZedWatchdogDaemon", expected)
        self.assertIn("ZedRefinementMonitor", expected)

    def test_empty_source_is_an_error(self):
        with self.assertRaises(tcc.CadenceError):
            tcc.parse_expected_cadences("# nothing here\n")

    def test_real_install_script_matches_documented_cadences(self):
        script = REPO_ROOT / "daemons" / "install-tasks.ps1"
        expected = tcc.parse_expected_cadences(
            script.read_text(encoding="utf-8", errors="replace")
        )
        self.assertEqual(expected.get("AesopWatchdogDaemon"), 5)
        self.assertEqual(expected.get("AesopRefinementMonitor"), 20)


class TestXmlParsing(unittest.TestCase):
    """Live task XML -> interval/enabled facts."""

    def test_interval_and_default_enabled(self):
        task = tcc.parse_task_xml(WATCHDOG_XML)
        self.assertEqual(task["interval_minutes"], 5.0)
        # <Enabled> absent means enabled in Task Scheduler semantics.
        self.assertTrue(task["enabled"])
        self.assertEqual(task["interval_raw"], "PT5M")

    def test_explicit_disabled(self):
        self.assertFalse(tcc.parse_task_xml(DISABLED_XML)["enabled"])

    def test_hourly_interval(self):
        self.assertEqual(tcc.parse_task_xml(HOURLY_MONITOR_XML)["interval_minutes"], 60.0)

    def test_missing_repetition_is_an_error(self):
        with self.assertRaises(tcc.CadenceError):
            tcc.parse_task_xml(NO_REPETITION_XML)

    def test_utf16_bytes_are_decoded(self):
        raw = b"\xff\xfe" + WATCHDOG_XML.encode("utf-16-le")
        self.assertEqual(tcc.decode_schtasks_output(raw), WATCHDOG_XML)

    def test_utf8_bytes_are_decoded(self):
        raw = WATCHDOG_XML.encode("utf-8")
        self.assertEqual(tcc.decode_schtasks_output(raw), WATCHDOG_XML)

    def test_garbage_xml_is_an_error(self):
        with self.assertRaises(tcc.CadenceError):
            tcc.parse_task_xml("not xml at all")


class TestVerdicts(unittest.TestCase):
    """check_cadences() with an injected query function -- no schtasks."""

    def test_all_ok(self):
        def query(name):
            return WATCHDOG_XML if "Watchdog" in name else HOURLY_MONITOR_XML

        report = tcc.check_cadences({"AesopWatchdogDaemon": 5}, query)
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["tasks"][0]["status"], "OK")

    def test_interval_mismatch_fails(self):
        # The real escape: monitor firing hourly against a 20-minute SLA.
        report = tcc.check_cadences(
            {"AesopRefinementMonitor": 20}, lambda n: HOURLY_MONITOR_XML
        )
        self.assertEqual(report["exit_code"], 1)
        task = report["tasks"][0]
        self.assertEqual(task["status"], "INTERVAL_MISMATCH")
        self.assertEqual(task["expected_minutes"], 20)
        self.assertEqual(task["actual_minutes"], 60.0)

    def test_disabled_task_fails(self):
        report = tcc.check_cadences({"AesopWatchdogDaemon": 5}, lambda n: DISABLED_XML)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["tasks"][0]["status"], "DISABLED")

    def test_missing_task_fails_with_one(self):
        def query(name):
            raise tcc.TaskMissingError(name)

        report = tcc.check_cadences({"AesopWatchdogDaemon": 5}, query)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["tasks"][0]["status"], "MISSING")

    def test_query_error_fails_closed_with_two(self):
        def query(name):
            raise tcc.TaskQueryError("schtasks blew up")

        report = tcc.check_cadences({"AesopWatchdogDaemon": 5}, query)
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["tasks"][0]["status"], "QUERY_ERROR")

    def test_query_error_outranks_mismatch(self):
        names = {"AesopWatchdogDaemon": 5, "AesopRefinementMonitor": 20}

        def query(name):
            if "Monitor" in name:
                raise tcc.TaskQueryError("boom")
            return HOURLY_MONITOR_XML  # mismatch for the watchdog

        self.assertEqual(tcc.check_cadences(names, query)["exit_code"], 2)

    def test_unparseable_xml_is_a_query_error(self):
        report = tcc.check_cadences({"AesopWatchdogDaemon": 5}, lambda n: "<bad")
        self.assertEqual(report["exit_code"], 2)


class TestCli(unittest.TestCase):
    """CLI surface: non-Windows skip and JSON shape."""

    def test_non_windows_skips_green(self):
        code, out = tcc.run_cli([], platform="linux")
        self.assertEqual(code, 0)
        self.assertIn("SKIPPED-non-windows", out)

    def test_non_windows_skip_in_json_mode(self):
        import json

        code, out = tcc.run_cli(["--json"], platform="linux")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "SKIPPED-non-windows")
        self.assertEqual(payload["exit_code"], 0)

    def test_unknown_flag_fails_closed(self):
        code, _ = tcc.run_cli(["--bogus"], platform="linux")
        self.assertEqual(code, 2)

    def test_render_text_is_ascii(self):
        report = tcc.check_cadences(
            {"AesopRefinementMonitor": 20}, lambda n: HOURLY_MONITOR_XML
        )
        text = tcc.render_text(report)
        text.encode("ascii")  # raises if non-ASCII slipped in
        self.assertIn("INTERVAL_MISMATCH", text)


if __name__ == "__main__":
    unittest.main()
