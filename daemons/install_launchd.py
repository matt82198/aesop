#!/usr/bin/env python3
"""macOS launchd agent installer for Aesop daemons.

Generates, plans, installs, and uninstalls ~/Library/LaunchAgents/*.plist
definitions for:
  - com.aesop.watchdog
  - com.aesop.selfheal
  - com.aesop.monitor
  - com.aesop.merge-queue

Interval cadences are derived from daemons/install-tasks.ps1 when present
so Windows Task Scheduler and macOS launchd stay aligned. All paths are
resolved dynamically from AESOP_ROOT / HOME / CLI flags (zero hardcoded
user paths).

Usage:
  python3 daemons/install_launchd.py --plan --json
  python3 daemons/install_launchd.py --install [--dest-dir DIR] [--no-launchctl]
  python3 daemons/install_launchd.py --uninstall [--dest-dir DIR] [--no-launchctl]
"""

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_WATCHDOG_MINUTES = 5
DEFAULT_MONITOR_MINUTES = 20
DEFAULT_MERGE_QUEUE_MINUTES = 5
DEFAULT_SELFHEAL_SECONDS = 60

_PS1_INT_PARAM_RE = re.compile(r"\[int\]\s*\$(\w+)\s*=\s*(\d+)")

_POSIX_PATH_ENV = (
    "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
)


def _read_ps1_intervals(ps1_path):
    """Extract interval minutes from daemons/install-tasks.ps1 when present."""
    defaults = {
        "WatchdogIntervalMinutes": DEFAULT_WATCHDOG_MINUTES,
        "MonitorIntervalMinutes": DEFAULT_MONITOR_MINUTES,
        "MergeQueueIntervalMinutes": DEFAULT_MERGE_QUEUE_MINUTES,
    }
    path = Path(ps1_path)
    if not path.is_file():
        return defaults
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return defaults

    found = dict(defaults)
    for name, raw_val in _PS1_INT_PARAM_RE.findall(text):
        if name in found:
            val = int(raw_val)
            if val > 0:
                found[name] = val
    return found


def build_agent_specs(
    aesop_root,
    home_dir,
    dest_dir,
    state_dir=None,
    selfheal_interval_seconds=DEFAULT_SELFHEAL_SECONDS,
):
    """Build launchd agent specs and plist dicts using dynamic paths only."""
    root_path = Path(aesop_root)
    home_path = Path(home_dir)
    dest_path = Path(dest_dir)
    state_path = Path(state_dir) if state_dir else (root_path / "state")

    intervals = _read_ps1_intervals(root_path / "daemons" / "install-tasks.ps1")
    watchdog_sec = intervals["WatchdogIntervalMinutes"] * 60
    monitor_sec = intervals["MonitorIntervalMinutes"] * 60
    merge_queue_sec = intervals["MergeQueueIntervalMinutes"] * 60
    selfheal_sec = int(selfheal_interval_seconds)

    root_posix = root_path.as_posix()
    home_posix = home_path.as_posix()
    state_posix = state_path.as_posix()

    definitions = [
        (
            "com.aesop.watchdog",
            "watchdog",
            watchdog_sec,
            ["/bin/bash", root_posix + "/daemons/run-watchdog.sh", "--once"],
        ),
        (
            "com.aesop.selfheal",
            "selfheal",
            selfheal_sec,
            ["/bin/bash", root_posix + "/daemons/selfheal.sh", "--once"],
        ),
        (
            "com.aesop.monitor",
            "monitor",
            monitor_sec,
            ["/usr/bin/env", "node", root_posix + "/monitor/collect-signals.mjs"],
        ),
        (
            "com.aesop.merge-queue",
            "merge-queue",
            merge_queue_sec,
            ["/bin/bash", root_posix + "/daemons/run-merge-queue.sh", "--once"],
        ),
    ]

    agents = []
    for label, short_name, interval_sec, program_args in definitions:
        plist_name = label + ".plist"
        plist_path = dest_path / plist_name
        plist_dict = {
            "Label": label,
            "ProgramArguments": program_args,
            "StartInterval": interval_sec,
            "RunAtLoad": True,
            "WorkingDirectory": root_posix,
            "EnvironmentVariables": {
                "HOME": home_posix,
                "AESOP_ROOT": root_posix,
                "PATH": _POSIX_PATH_ENV,
            },
            "StandardOutPath": state_posix + "/launchd-" + short_name + ".log",
            "StandardErrorPath": state_posix + "/launchd-" + short_name + ".err.log",
        }
        agents.append(
            {
                "label": label,
                "plist_name": plist_name,
                "plist_path": plist_path.as_posix(),
                "start_interval_seconds": interval_sec,
                "interval_minutes": interval_sec / 60.0,
                "program_arguments": program_args,
                "working_directory": root_posix,
                "environment_variables": dict(plist_dict["EnvironmentVariables"]),
                "plist": plist_dict,
            }
        )
    return agents


def _run_launchctl(args):
    """Run launchctl with explicit UTF-8 decoding."""
    return subprocess.run(
        ["launchctl"] + list(args),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _gui_domain():
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return "gui/%d" % uid


def _bootout_plist(plist_path):
    domain = _gui_domain()
    _run_launchctl(["bootout", domain, str(plist_path)])


def _bootstrap_plist(plist_path):
    domain = _gui_domain()
    proc = _run_launchctl(["bootstrap", domain, str(plist_path)])
    if proc.returncode != 0:
        raise RuntimeError(
            "launchctl bootstrap failed (%d): %s"
            % (proc.returncode, (proc.stderr or proc.stdout).strip())
        )


def install_agents(agents, use_launchctl=True):
    """Write XML .plist files and optionally bootstrap them via launchctl."""
    if use_launchctl and sys.platform != "darwin":
        raise RuntimeError(
            "launchctl is only available on macOS (darwin); pass --no-launchctl or --plan"
        )

    results = []
    for agent in agents:
        plist_path = Path(agent["plist_path"])
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        xml_bytes = plistlib.dumps(agent["plist"], fmt=plistlib.FMT_XML)
        plist_path.write_bytes(xml_bytes)
        if use_launchctl:
            _bootout_plist(plist_path)
            _bootstrap_plist(plist_path)
        results.append(
            {
                "label": agent["label"],
                "plist_path": plist_path.as_posix(),
                "action": "installed",
                "launchctl": use_launchctl,
            }
        )
    return results


def uninstall_agents(agents, use_launchctl=True):
    """Unload (when requested) and remove managed .plist files."""
    if use_launchctl and sys.platform != "darwin":
        raise RuntimeError(
            "launchctl is only available on macOS (darwin); pass --no-launchctl or --plan"
        )

    results = []
    for agent in agents:
        plist_path = Path(agent["plist_path"])
        existed = plist_path.exists()
        if use_launchctl and existed:
            _bootout_plist(plist_path)
        if existed:
            plist_path.unlink()
        results.append(
            {
                "label": agent["label"],
                "plist_path": plist_path.as_posix(),
                "action": "removed" if existed else "not-found",
                "launchctl": use_launchctl,
            }
        )
    return results


def _default_aesop_root():
    env_root = os.environ.get("AESOP_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parent.parent


def _default_home():
    env_home = os.environ.get("HOME")
    if env_home:
        return Path(env_home)
    return Path.home()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install or plan macOS launchd agents for Aesop daemons."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--plan",
        action="store_true",
        help="Emit the launchd agent plan without writing files or calling launchctl.",
    )
    mode_group.add_argument(
        "--install",
        action="store_true",
        help="Write LaunchAgents .plist files (default action).",
    )
    mode_group.add_argument(
        "--uninstall",
        action="store_true",
        help="Unload and remove LaunchAgents .plist files.",
    )
    parser.add_argument(
        "--aesop-root",
        default=None,
        help="Aesop repository root (default: $AESOP_ROOT or repo root).",
    )
    parser.add_argument(
        "--home",
        default=None,
        help="Home directory used for $HOME and default LaunchAgents dir.",
    )
    parser.add_argument(
        "--dest-dir",
        default=None,
        help="Destination LaunchAgents directory (default: <home>/Library/LaunchAgents).",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="State directory for launchd stdout/stderr logs (default: <aesop-root>/state).",
    )
    parser.add_argument(
        "--no-launchctl",
        action="store_true",
        help="Skip launchctl bootstrap/bootout (write/remove .plist files only).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    args = parser.parse_args(argv)

    aesop_root = Path(args.aesop_root) if args.aesop_root else _default_aesop_root()
    home_dir = Path(args.home) if args.home else _default_home()
    dest_dir = (
        Path(args.dest_dir)
        if args.dest_dir
        else (home_dir / "Library" / "LaunchAgents")
    )
    if args.state_dir:
        state_dir = Path(args.state_dir)
    elif not args.aesop_root and os.environ.get("AESOP_STATE_ROOT"):
        state_dir = Path(os.environ["AESOP_STATE_ROOT"])
    else:
        state_dir = aesop_root / "state"

    agents = build_agent_specs(
        aesop_root=aesop_root,
        home_dir=home_dir,
        dest_dir=dest_dir,
        state_dir=state_dir,
    )

    if args.plan:
        payload = {
            "mode": "plan",
            "platform": "darwin",
            "aesop_root": aesop_root.as_posix(),
            "home": home_dir.as_posix(),
            "dest_dir": dest_dir.as_posix(),
            "agents": agents,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for agent in agents:
                print(
                    "%s: StartInterval=%ds -> %s"
                    % (
                        agent["label"],
                        agent["start_interval_seconds"],
                        " ".join(agent["program_arguments"]),
                    )
                )
        return 0

    use_launchctl = not args.no_launchctl
    try:
        if args.uninstall:
            results = uninstall_agents(agents, use_launchctl=use_launchctl)
            mode_name = "uninstall"
        else:
            results = install_agents(agents, use_launchctl=use_launchctl)
            mode_name = "install"
    except RuntimeError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"mode": mode_name, "results": results}, indent=2))
    else:
        for item in results:
            print("%s: %s (%s)" % (item["action"], item["label"], item["plist_path"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
