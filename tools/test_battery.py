#!/usr/bin/env python3
"""test_battery.py -- run the local union test battery, parallel by default.
INDEX: Local union test battery: runs the 4 harnesses (py/node/sh/ui) as parallel subprocesses with per-harness rc capture, stdin closed, logs to temp; parallel mode sets AESOP_TEST_CHILD_TIMEOUT_MS=90000 for node scaffold children; `--serial` fallback, `--skip <h>`, `--json`; exit 0 only when all harnesses green

Runs the four harnesses (python unittest discover, node --test, shell suites,
ui vitest+tsc) as concurrent subprocesses with per-harness rc capture, stdin
closed (the hook suite hangs on never-EOF stdin), and an explicit summary
table. Exit 0 only when every harness exits 0.

Per-harness timeout (AESOP_BATTERY_HARNESS_TIMEOUT_S, default 1800s = 30min):
on expiry, the process tree is killed and rc=124 is recorded with a TIMEOUT
note. Applies in both serial and parallel modes.

Usage:
  python tools/test_battery.py [--serial] [--skip ui|sh|node|py ...] [--json]

--serial runs harnesses one at a time (the pre-wave-29 behavior; fallback if
parallel runs prove load-fragile on a box). Logs land in the state scratch dir
(AESOP_BATTERY_LOGDIR or the system temp dir) as battery-<harness>.log.
"""
import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

HARNESSES = {
    "py": [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
    "node": ["npm", "run", "test:node"],
    "sh": ["npm", "run", "test:sh"],
    "ui": None,  # composite: tsc + vitest, run via _ui_command
}


def _get_harness_timeout():
    """Get per-harness timeout in seconds (env AESOP_BATTERY_HARNESS_TIMEOUT_S, default 1800)."""
    timeout_s = os.environ.get("AESOP_BATTERY_HARNESS_TIMEOUT_S", "1800")
    try:
        return int(timeout_s)
    except ValueError:
        return 1800


def _kill_process_tree(proc):
    """Kill process and all children (Windows: taskkill /T /F, others: SIGKILL)."""
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
            )
        else:
            # Unix: SIGKILL via os.killpg (process group)
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                # Process already gone or not in a group; try direct kill
                try:
                    os.kill(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
    except Exception:
        # Ignore kill errors; process may already be dead
        pass


def _wait_with_timeout(proc, timeout_s):
    """Wait for process with timeout; return (rc, timed_out).

    On timeout, kills the process tree and returns (124, True).
    Otherwise returns (proc.returncode, False).
    """
    try:
        proc.wait(timeout=timeout_s)
        return proc.returncode, False
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        # Wait a moment for kill to take effect, then force reap
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return 124, True


def _ui_command():
    # ui/web needs node_modules; npx resolves local binaries.
    return "npx tsc --noEmit && npx vitest run --silent"


def run_harness(name, logdir, parallel=True):
    """Spawn one harness with stdin closed; return (name, Popen, logfile)."""
    log = Path(logdir) / f"battery-{name}.log"
    f = open(log, "w", encoding="utf-8", errors="replace")
    env = os.environ.copy()
    if parallel and name == "node" and "AESOP_TEST_CHILD_TIMEOUT_MS" not in env:
        # Under 4-harness parallel load, scaffold child processes legitimately
        # exceed the 30s solo ceiling; the tests honor this knob (wave-29).
        env["AESOP_TEST_CHILD_TIMEOUT_MS"] = "90000"
    if name == "ui":
        proc = subprocess.Popen(
            _ui_command(), shell=True, cwd=str(REPO / "ui" / "web"),
            stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, env=env,
        )
    else:
        # npm needs shell resolution on Windows.
        use_shell = os.name == "nt" and HARNESSES[name][0] == "npm"
        cmd = " ".join(HARNESSES[name]) if use_shell else HARNESSES[name]
        proc = subprocess.Popen(
            cmd, shell=use_shell, cwd=str(REPO),
            stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, env=env,
        )
    return name, proc, f, log


def main():
    ap = argparse.ArgumentParser(description="Run the local union test battery")
    ap.add_argument("--serial", action="store_true", help="run harnesses sequentially")
    ap.add_argument("--skip", action="append", default=[], choices=list(HARNESSES),
                    help="skip a harness (repeatable)")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args()

    logdir = os.environ.get("AESOP_BATTERY_LOGDIR") or tempfile.mkdtemp(prefix="aesop-battery-")
    os.makedirs(logdir, exist_ok=True)
    names = [n for n in HARNESSES if n not in args.skip]
    started = time.time()
    results = {}
    timeout_s = _get_harness_timeout()

    if args.serial:
        for n in names:
            name, proc, f, log = run_harness(n, logdir, parallel=False)
            rc, timed_out = _wait_with_timeout(proc, timeout_s)
            f.close()
            result = {"rc": rc, "log": str(log)}
            if timed_out:
                result["note"] = "TIMEOUT"
            results[name] = result
    else:
        procs = [run_harness(n, logdir) for n in names]
        for name, proc, f, log in procs:
            rc, timed_out = _wait_with_timeout(proc, timeout_s)
            f.close()
            result = {"rc": rc, "log": str(log)}
            if timed_out:
                result["note"] = "TIMEOUT"
            results[name] = result

    wall = round(time.time() - started, 1)
    ok = all(r["rc"] == 0 for r in results.values())
    if args.json:
        print(json.dumps({"ok": ok, "wall_s": wall, "mode": "serial" if args.serial else "parallel",
                          "results": results}))
    else:
        print(f"battery mode={'serial' if args.serial else 'parallel'} wall={wall}s")
        for n, r in results.items():
            verdict = "PASS" if r["rc"] == 0 else "FAIL"
            note_str = f" {r['note']}" if "note" in r else ""
            print(f"  {n:5s} rc={r['rc']}  {verdict}{note_str}  log={r['log']}")
        print("BATTERY:", "GREEN" if ok else "RED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
