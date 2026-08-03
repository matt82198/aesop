#!/usr/bin/env python3
"""Scheduled-task cadence gate: live Task Scheduler state vs install-tasks.ps1.

Motivating escape: the refinement monitor was firing HOURLY while its SLA (and
`daemons/install-tasks.ps1`) says every 20 minutes. Every run exited 0, every
heartbeat looked fresh at the moment it was read, and nothing anywhere compared
the *registered cadence* against the *defined cadence*. A daemon that runs at a
third of its intended rate is invisible to liveness checks -- only a cadence
check catches it.

Mechanism:
  1. Parse `daemons/install-tasks.ps1` (the source of truth for task
     definitions) into {task name -> expected repetition interval in minutes}.
  2. Query live state with `schtasks /query /tn <name> /xml`.
  3. FAIL (exit 1) on interval mismatch, missing task, or disabled task.
     Exit 2 (fail-closed) on any query/parse error -- "could not evaluate" is
     never reported as healthy.

Windows-only. On any other platform it prints a SKIPPED-non-windows line and
exits 0, so Linux CI is unaffected; the real consumer is the local
`power_selftest.py` health harness.

CLI:
    python tools/task_cadence_check.py [--json] [--root DIR]
                                       [--install-script PATH] [--help]

Exit codes: 0 = cadences match (or skipped), 1 = drift found, 2 = cannot evaluate.
"""

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TASK_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
SCHTASKS_TIMEOUT_SECONDS = 30

# Substrings Task Scheduler uses when the named task simply does not exist.
# Anything else from schtasks is treated as an evaluation failure (exit 2).
_MISSING_MARKERS = (
    "cannot find the file specified",
    "does not exist",
    "the system cannot find",
)


class CadenceError(Exception):
    """Malformed input: unparseable duration, XML, or install script."""


class TaskQueryError(Exception):
    """schtasks could not be consulted (exit 2 -- fail closed)."""


class TaskMissingError(Exception):
    """The named task is not registered at all (exit 1 -- real drift)."""


# --------------------------------------------------------------------------
# ISO-8601 durations
# --------------------------------------------------------------------------

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def iso8601_to_minutes(text):
    """Convert an ISO-8601 duration (PT5M, PT1H30M, P32DT1H36M) to minutes."""
    if not isinstance(text, str) or not text.strip():
        raise CadenceError("empty or non-string duration: %r" % (text,))
    match = _DURATION_RE.match(text.strip())
    if not match:
        raise CadenceError("unparseable ISO-8601 duration: %r" % (text,))
    parts = {k: float(v) for k, v in match.groupdict().items() if v is not None}
    if not parts:
        raise CadenceError("duration carries no components: %r" % (text,))
    return (
        parts.get("days", 0.0) * 1440.0
        + parts.get("hours", 0.0) * 60.0
        + parts.get("minutes", 0.0)
        + parts.get("seconds", 0.0) / 60.0
    )


# --------------------------------------------------------------------------
# install-tasks.ps1 -> expected cadences
# --------------------------------------------------------------------------

_INT_PARAM_RE = re.compile(r"\[int\]\s*\$(\w+)\s*=\s*(\d+)")
_STR_PARAM_RE = re.compile(r"\[string\]\s*\$(\w+)\s*=\s*'([^']*)'")
_STR_ASSIGN_RE = re.compile(r'^\s*\$(\w+)\s*=\s*"([^"]*)"', re.MULTILINE)
_INTERP_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")
_TASKNAME_ARG_RE = re.compile(r"-TaskName\s+(\$?[\w\"'${}]+)")
_INTERVAL_ARG_RE = re.compile(r"-IntervalMinutes\s+(\$?[\w]+)")


def _expand(value, strings):
    """Expand ${Var} / $Var references in a PowerShell double-quoted string."""

    def sub(match):
        """Resolve one $Var / ${Var} reference against the known strings."""
        name = match.group(1) or match.group(2)
        return strings.get(name, match.group(0))

    return _INTERP_RE.sub(sub, value)


def parse_expected_cadences(ps1_text):
    """Extract {task name -> expected interval minutes} from install-tasks.ps1.

    Binds each `Register-DaemonTask` call site's -TaskName and -IntervalMinutes
    arguments through the script's param defaults and string assignments. The
    `function Register-DaemonTask {` definition is deliberately not mined.
    """
    if not isinstance(ps1_text, str):
        raise CadenceError("install script text must be a string")

    ints = {name: int(val) for name, val in _INT_PARAM_RE.findall(ps1_text)}
    strings = {name: val for name, val in _STR_PARAM_RE.findall(ps1_text)}
    for name, raw in _STR_ASSIGN_RE.findall(ps1_text):
        strings[name] = _expand(raw, strings)

    expected = {}
    for match in re.finditer(r"Register-DaemonTask", ps1_text):
        tail = ps1_text[match.end() : match.end() + 600]
        if tail.lstrip().startswith("{"):
            continue  # the function definition, not a call site
        name_hit = _TASKNAME_ARG_RE.search(tail)
        interval_hit = _INTERVAL_ARG_RE.search(tail)
        if not name_hit or not interval_hit:
            continue

        raw_name = name_hit.group(1).strip()
        if raw_name.startswith("$"):
            task_name = strings.get(raw_name[1:].strip("{}"))
        else:
            task_name = _expand(raw_name.strip("\"'"), strings)
        raw_interval = interval_hit.group(1).strip()
        if raw_interval.startswith("$"):
            minutes = ints.get(raw_interval[1:])
        elif raw_interval.isdigit():
            minutes = int(raw_interval)
        else:
            minutes = None

        if task_name and minutes:
            expected[task_name] = minutes

    if not expected:
        raise CadenceError(
            "no Register-DaemonTask call sites resolved; install script "
            "layout changed and this parser must be updated"
        )
    return expected


# --------------------------------------------------------------------------
# schtasks XML -> live facts
# --------------------------------------------------------------------------


def decode_schtasks_output(raw):
    """Decode schtasks bytes: /xml emits UTF-16, other paths may emit UTF-8."""
    if isinstance(raw, str):
        return raw
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-16", errors="replace")


def parse_task_xml(xml_text):
    """Parse a Task Scheduler task definition into cadence facts.

    Returns {interval_raw, interval_minutes, enabled}. Raises CadenceError if
    the XML is malformed or carries no repetition interval (a task that fires
    once and never repeats is drift, not health).
    """
    text = xml_text.lstrip("\ufeff").strip()
    # ElementTree refuses str input carrying an encoding declaration.
    text = re.sub(r"^<\?xml[^>]*\?>\s*", "", text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise CadenceError("malformed task XML: %s" % (exc,))

    interval_raw = None
    for repetition in root.iter(TASK_NS + "Repetition"):
        node = repetition.find(TASK_NS + "Interval")
        if node is not None and node.text:
            interval_raw = node.text.strip()
            break
    if not interval_raw:
        raise CadenceError("task XML declares no <Repetition><Interval>")

    # <Enabled> is optional; Task Scheduler treats its absence as enabled.
    enabled = True
    settings = root.find(TASK_NS + "Settings")
    if settings is not None:
        node = settings.find(TASK_NS + "Enabled")
        if node is not None and node.text is not None:
            enabled = node.text.strip().lower() != "false"

    return {
        "interval_raw": interval_raw,
        "interval_minutes": iso8601_to_minutes(interval_raw),
        "enabled": enabled,
    }


def query_task_xml(task_name):
    """Run `schtasks /query /tn <name> /xml` and return the decoded XML text."""
    try:
        proc = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/xml"],
            capture_output=True,
            timeout=SCHTASKS_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise TaskQueryError("schtasks not found on PATH")
    except subprocess.TimeoutExpired:
        raise TaskQueryError("schtasks timed out after %ds" % SCHTASKS_TIMEOUT_SECONDS)

    stdout = decode_schtasks_output(proc.stdout or b"")
    stderr = decode_schtasks_output(proc.stderr or b"")
    if proc.returncode != 0:
        blob = (stderr + stdout).lower()
        if any(marker in blob for marker in _MISSING_MARKERS):
            raise TaskMissingError(task_name)
        raise TaskQueryError(
            "schtasks exit %d: %s" % (proc.returncode, (stderr or stdout).strip()[:200])
        )
    if not stdout.strip():
        raise TaskQueryError("schtasks returned empty output for %s" % task_name)
    return stdout


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------


def check_cadences(expected, query=query_task_xml):
    """Compare each expected cadence against live task state.

    `query` is injectable so tests never touch the real Task Scheduler.
    Exit-code precedence is 2 > 1 > 0: an unevaluable task can never be
    masked by otherwise-clean results.
    """
    tasks = []
    worst = 0
    for name in sorted(expected):
        want = expected[name]
        record = {"task": name, "expected_minutes": want}
        try:
            live = parse_task_xml(query(name))
        except TaskMissingError:
            record.update(status="MISSING", detail="task is not registered")
            worst = max(worst, 1)
        except TaskQueryError as exc:
            record.update(status="QUERY_ERROR", detail=str(exc))
            worst = 2
        except CadenceError as exc:
            record.update(status="QUERY_ERROR", detail=str(exc))
            worst = 2
        else:
            record["actual_minutes"] = live["interval_minutes"]
            record["actual_raw"] = live["interval_raw"]
            record["enabled"] = live["enabled"]
            if not live["enabled"]:
                record.update(status="DISABLED", detail="task is registered but disabled")
                worst = max(worst, 1)
            elif abs(live["interval_minutes"] - want) > 1e-9:
                record.update(
                    status="INTERVAL_MISMATCH",
                    detail="runs every %gm, definition says %gm"
                    % (live["interval_minutes"], want),
                )
                worst = max(worst, 1)
            else:
                record.update(status="OK", detail="every %gm as defined" % want)
        tasks.append(record)

    return {
        "status": "OK" if worst == 0 else ("DRIFT" if worst == 1 else "ERROR"),
        "exit_code": worst,
        "tasks": tasks,
    }


def render_text(report):
    """ASCII summary of a report (no colours, no unicode)."""
    lines = ["task-cadence-check: %s" % report["status"]]
    for task in report["tasks"]:
        lines.append(
            "  [%s] %s: %s" % (task["status"], task["task"], task.get("detail", ""))
        )
    if report["exit_code"] == 0:
        lines.append("All scheduled tasks match their install-tasks.ps1 cadence.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

_USAGE = (
    "usage: task_cadence_check.py [--json] [--root DIR] "
    "[--install-script PATH] [--help]"
)


def run_cli(argv, platform=None, query=query_task_xml):
    """Return (exit_code, output_text). Pure enough for tests to drive."""
    platform = platform if platform is not None else sys.platform
    as_json = False
    root = None
    script = None

    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--json":
            as_json = True
        elif arg == "--help" or arg == "-h":
            return 0, _USAGE
        elif arg == "--root":
            if not args:
                return 2, "error: --root requires a value\n" + _USAGE
            root = args.pop(0)
        elif arg == "--install-script":
            if not args:
                return 2, "error: --install-script requires a value\n" + _USAGE
            script = args.pop(0)
        else:
            return 2, "error: unknown argument %r\n%s" % (arg, _USAGE)

    if not str(platform).startswith("win"):
        if as_json:
            return 0, json.dumps(
                {
                    "status": "SKIPPED-non-windows",
                    "exit_code": 0,
                    "platform": str(platform),
                    "tasks": [],
                },
                indent=2,
            )
        return 0, "task-cadence-check: SKIPPED-non-windows (platform=%s)" % platform

    if script:
        script_path = Path(script)
    else:
        base = Path(root) if root else Path(__file__).resolve().parent.parent
        script_path = base / "daemons" / "install-tasks.ps1"

    try:
        source = script_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return 2, "error: cannot read install script %s: %s" % (script_path, exc)

    try:
        expected = parse_expected_cadences(source)
    except CadenceError as exc:
        return 2, "error: %s" % exc

    report = check_cadences(expected, query)
    report["install_script"] = str(script_path)
    if as_json:
        return report["exit_code"], json.dumps(report, indent=2)
    return report["exit_code"], render_text(report)


def main(argv=None):
    """Entry point: print the report and return the gate exit code."""
    code, out = run_cli(list(sys.argv[1:] if argv is None else argv))
    print(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
