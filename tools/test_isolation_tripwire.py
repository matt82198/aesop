#!/usr/bin/env python3
"""
Test-isolation tripwire: fails closed if the REAL developer profile changes
while a test suite runs.
INDEX: Test-isolation tripwire (incident 2026-10-05: installSkills() in bin/cli.js overwrote real ~/.claude/skills during a Node test run); snapshots sha256 of ~/.claude/{skills,settings.json,memory,hooks} + `git config --global -l` before/after wrapping a command, FAILS CLOSED naming every changed path when the profile exists, degrades loudly-but-green ("no profile present, tripwire inert") when ~/.claude is absent (CI); CLI: `[--root DIR] -- <command...>`; exit = max(wrapped command's exit code, tripwire finding); stdlib-only. Extended 2026-10-06 (shell-test-isolation incident) to also snapshot `<root>/conductor3/state/.watchdog-heartbeat`, `conductor3/monitor/.monitor-heartbeat`, `conductor3/state/*.json`, and Windows Aesop* scheduled-task registrations (`schtasks /query /fo CSV`, Windows-only / AESOP_TRIPWIRE_SCHTASKS_CMD override for tests). Daemon-written files use VALIDITY checks (heartbeat epoch bounds, log growth, JSON parse+shape) instead of hash comparisons since daemons legitimately update them during test runs.

Why this exists: tests/CLAUDE.md and LANE-CONTRACT.md have said "tests must not
pollute cwd or global state" as prose for a long time. Prose is not a gate. On
2026-10-05, running the Node suite on a developer box let bin/cli.js's
installSkills() write aesop's scaffold skill templates over the REAL
~/.claude/skills/{power,dashboard,buildsystem}/SKILL.md (4 files, restored from
git), because a test forgot --no-skills / AESOP_SKILLS_HOME. tests/helpers/
isolated-env.mjs is the primary fix (the Node test process can no longer reach
the real HOME at all, structurally); this tripwire is the independent proof
that it worked, and the backstop for any future escape (a Python test, a shell
hook test, anything invoked outside the Node harness).

On 2026-10-06, a shell-test lane left "1234567890" in the LIVE
~/conductor3/state/.watchdog-heartbeat because the hash-snapshot approach
failed to distinguish between legitimate daemon ticks (+300s) and escape writes
(placeholder test value, far in the past). Solution: DAEMON-WRITTEN files now
use VALIDITY checks instead. Heartbeat must be an epoch integer >= before and
within [before, now+60s]; logs may only grow; repos.json must parse as JSON.

Usage:
  python tools/test_isolation_tripwire.py -- npm run test:node
  python tools/test_isolation_tripwire.py -- python -m unittest discover -s tests
  python tools/test_isolation_tripwire.py --root /tmp/fake-home -- true   # tests

--root overrides which directory stands in for "the profile" (default: the
real home directory). Tests point it at a fake fixture so they never touch the
developer's actual ~/.claude or global git config while proving the gate bites.
When --root is given, it is also used as HOME/USERPROFILE for the
`git config --global -l` subprocess, so the git-config check is fully
sandboxed under the fixture too.

Exit codes:
  0: no drift (or profile absent -> inert) and the wrapped command exited 0
  1 (or the wrapped command's own exit code if higher): drift detected; every
     changed path is named on stderr
  2: usage error (no `--` separator, or no command given)

The wrapped command's exit code and the tripwire's own finding are combined as
max(command_exit, tripwire_exit), so neither a red suite nor a tripwire finding
can be swallowed by the other.
"""
import argparse
import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ABSENT = "<absent>"

# Logical name -> path relative to the profile root, checked whenever the root's
# .claude directory exists.
SENSITIVE_RELPATHS = {
    "skills": ".claude/skills",
    "settings": ".claude/settings.json",
    "memory": ".claude/memory",
    "hooks": ".claude/hooks",
}

# DAEMON-WRITTEN heartbeat files under conductor3. These are written by:
# - daemons/backup-fleet.sh writes .watchdog-heartbeat with `date +%s` every 150s
# - monitor/collect-signals.mjs writes .monitor-heartbeat with epoch seconds
# These must use VALIDITY checks (epoch bounds) not hash comparisons.
CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS = {
    "watchdog_heartbeat": "conductor3/state/.watchdog-heartbeat",
    "monitor_heartbeat": "conductor3/monitor/.monitor-heartbeat",
}

# DAEMON-WRITTEN log files under conductor3/state/. These may only GROW
# (append-only). Checked via size comparison, not hashes.
CONDUCTOR_DAEMON_LOG_PATTERNS = ["conductor3/state/FLEET-BACKUP.log", "conductor3/state/cron-*.log"]

# DAEMON-WRITTEN repos JSON file. Must parse as JSON and maintain top-level shape.
CONDUCTOR_DAEMON_REPOS_JSON_RELPATH = "conductor3/state/.watchdog-repos.json"

# STATIC JSON files under conductor3/state/ (lock files, tracker.json, etc.)
# These use hash comparisons since they should never be touched by running daemons
# during a test suite.
CONDUCTOR_STATIC_JSON_GLOB = "conductor3/state/*.json"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_tree(path: Path) -> dict:
    """Return {relpath: sha256} for every file under path (sorted, deterministic)."""
    out = {}
    for p in sorted(path.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(path)).replace("\\", "/")
            try:
                out[rel] = _hash_file(p)
            except OSError:
                out[rel] = "<unreadable>"
    return out


def validate_heartbeat(path: Path) -> str:
    """Validate a heartbeat file contains an epoch timestamp integer.
    Returns: the integer value if valid, or an error description string starting with "ERROR:"."""
    if not path.exists():
        return ABSENT
    try:
        content = path.read_text(encoding="utf-8").strip()
        epoch = int(content)
        return str(epoch)
    except (OSError, ValueError):
        return f"ERROR: non-integer or unreadable"


def snapshot_heartbeat(path: Path, before_value: str = None) -> dict:
    """Snapshot a daemon-written heartbeat file for validity checking.
    Returns: {"value": epoch_int_as_str, "valid": bool, "error": str_or_None}
    If before_value is provided, the after-snapshot will include it for bounds checking."""
    if not path.exists():
        return {"value": ABSENT, "valid": True, "error": None}

    try:
        content = path.read_text(encoding="utf-8").strip()
        epoch = int(content)
        return {"value": epoch, "valid": True, "error": None, "before": before_value}
    except (OSError, ValueError):
        return {"value": content if path.exists() else ABSENT, "valid": False,
                "error": "non-integer or unreadable"}


def validate_log_file(path: Path, before_size: int) -> tuple:
    """Validate an append-only log file: size must be >= before_size.
    Returns: (valid: bool, error_msg: str_or_None)"""
    if not path.exists():
        # Log disappeared -> only fail if it existed before
        return (before_size == -1, "log file disappeared" if before_size != -1 else None)

    try:
        after_size = path.stat().st_size
        if after_size < before_size:
            return (False, f"log truncated (before: {before_size}, after: {after_size})")
        return (True, None)
    except OSError as e:
        return (False, f"unreadable log: {e}")


def snapshot_log_file(path: Path) -> dict:
    """Snapshot a log file for growth validation.
    Returns: {"size": int or ABSENT}"""
    if not path.exists():
        return {"size": -1}  # -1 means absent (so if it appears, growth check still works)
    try:
        return {"size": path.stat().st_size}
    except OSError:
        return {"size": -1}


def validate_repos_json(path: Path) -> tuple:
    """Validate .watchdog-repos.json parses as JSON and has expected top-level structure.
    Returns: (valid: bool, error_msg: str_or_None)"""
    if not path.exists():
        return (True, None)  # Absent is OK

    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        # Top-level must be a dict (not a list, string, etc.)
        if not isinstance(data, dict):
            return (False, "repos.json top-level is not a dict")
        return (True, None)
    except (OSError, json.JSONDecodeError) as e:
        return (False, f"repos.json invalid: {e}")


def snapshot_repos_json(path: Path) -> dict:
    """Snapshot .watchdog-repos.json for validity checking.
    Returns: {"valid": bool, "keys": list_or_None, "error": str_or_None}"""
    if not path.exists():
        return {"valid": True, "keys": None, "error": None}

    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            return {"valid": False, "keys": None, "error": "top-level not a dict"}
        return {"valid": True, "keys": sorted(data.keys()), "error": None}
    except (OSError, json.JSONDecodeError) as e:
        return {"valid": False, "keys": None, "error": str(e)}


def snapshot_path(abs_path: Path):
    """Fingerprint one sensitive path: {relpath: hash} for a dir, {'<file>': hash}
    for a single file, or the ABSENT sentinel."""
    if not abs_path.exists():
        return ABSENT
    if abs_path.is_file():
        return {"<file>": _hash_file(abs_path)}
    return _hash_tree(abs_path)


def snapshot_glob(root: Path, pattern: str, exclude_files: set = None) -> dict:
    """Return {relpath: sha256} for every file matching `pattern` under root
    (sorted, deterministic); empty dict if nothing matches.
    exclude_files: set of relpaths to skip (e.g., daemon-written files)."""
    exclude_files = exclude_files or set()
    out = {}
    for p in sorted(root.glob(pattern)):
        if p.is_file():
            rel = str(p.relative_to(root)).replace("\\", "/")
            if rel in exclude_files:
                continue
            try:
                out[rel] = _hash_file(p)
            except OSError:
                out[rel] = "<unreadable>"
    return out


def snapshot_scheduled_tasks():
    """Fingerprint Aesop* Windows scheduled task REGISTRATIONS (TaskName +
    Status only) via `schtasks /query /fo CSV`, filtered to rows naming
    "aesop" (the watchdog/monitor/selfheal scheduled tasks). Deliberately
    drops the "Next Run Time" column: it ticks forward on every query purely
    from the clock advancing, which would otherwise fail this check on every
    single run regardless of whether any registration actually changed.
    AESOP_TRIPWIRE_SCHTASKS_CMD lets tests stub the command on any platform;
    otherwise this check is Windows-only and degrades to the ABSENT sentinel
    everywhere else (or if schtasks itself is unavailable/fails/returns
    unparseable CSV), matching this tripwire's loudly-but-green contract."""
    override = os.environ.get("AESOP_TRIPWIRE_SCHTASKS_CMD")
    if override:
        cmd = shlex.split(override)
    elif sys.platform == "win32":
        cmd = ["schtasks", "/query", "/fo", "CSV"]
    else:
        return ABSENT
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ABSENT
    if res.returncode != 0:
        return ABSENT
    try:
        rows = list(csv.reader(res.stdout.splitlines()))
    except csv.Error:
        return ABSENT
    if not rows:
        return ABSENT
    header = [c.strip().lower() for c in rows[0]]
    try:
        name_idx = header.index("taskname")
        status_idx = header.index("status")
    except ValueError:
        return ABSENT
    aesop_rows = sorted(
        f"{row[name_idx]}|{row[status_idx]}"
        for row in rows[1:]
        if len(row) > max(name_idx, status_idx) and "aesop" in row[name_idx].lower()
    )
    if not aesop_rows:
        return ABSENT
    text = "\n".join(aesop_rows)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def snapshot_git_global_config(root: Path):
    """Return a fingerprint of `git config --global -l`, run with HOME/USERPROFILE
    pinned to `root` so --root fixtures fully sandbox this check too."""
    env = dict(os.environ)
    env["HOME"] = str(root)
    env["USERPROFILE"] = str(root)
    try:
        res = subprocess.run(
            ["git", "config", "--global", "-l"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, env=env, cwd=str(root),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ABSENT
    if res.returncode != 0:
        return ABSENT
    text = res.stdout
    keys = sorted({line.split("=", 1)[0] for line in text.splitlines() if "=" in line})
    return {"hash": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(), "keys": keys}


def take_snapshot(root: Path) -> dict:
    claude_root = root / ".claude"
    profile_present = claude_root.exists()
    conductor_present = (root / "conductor3").exists()
    now = int(time.time())
    snap = {
        "profile_present": profile_present,
        "paths": {},
        "git_global_config": snapshot_git_global_config(root),
        "conductor_present": conductor_present,
        "conductor_daemon_heartbeats": {},  # Validity checks, not hashes
        "conductor_daemon_logs": {},        # Growth checks, not hashes
        "conductor_daemon_repos_json": {},  # JSON parse check, not hash
        "conductor_static_json": {},        # Hash checks for static files
        "scheduled_tasks": snapshot_scheduled_tasks(),
        "snapshot_time": now,
    }
    if profile_present:
        for name, rel in SENSITIVE_RELPATHS.items():
            snap["paths"][name] = snapshot_path(root / rel)
    if conductor_present:
        # DAEMON-WRITTEN heartbeats: snapshot for epoch bounds checking
        for name, rel in CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.items():
            path = root / rel
            snap["conductor_daemon_heartbeats"][name] = snapshot_heartbeat(path)

        # DAEMON-WRITTEN logs: snapshot for growth checking
        for pattern in CONDUCTOR_DAEMON_LOG_PATTERNS:
            for path in sorted(root.glob(pattern)):
                rel = str(path.relative_to(root)).replace("\\", "/")
                snap["conductor_daemon_logs"][rel] = snapshot_log_file(path)

        # DAEMON-WRITTEN repos JSON: snapshot for parse check
        repos_path = root / CONDUCTOR_DAEMON_REPOS_JSON_RELPATH
        snap["conductor_daemon_repos_json"]["repos"] = snapshot_repos_json(repos_path)

        # STATIC JSON files: hash comparison as before (but exclude .watchdog-repos.json)
        # since that's handled by daemon_repos_json above
        exclude_repos = {CONDUCTOR_DAEMON_REPOS_JSON_RELPATH}
        snap["conductor_static_json"] = snapshot_glob(
            root, CONDUCTOR_STATIC_JSON_GLOB, exclude_files=exclude_repos
        )
    return snap


def diff_snapshots(before: dict, after: dict):
    """Return a list of human-readable changed-path descriptions, or [] if clean.

    DAEMON-WRITTEN files use VALIDITY checks (not hash comparisons):
    - Heartbeats: epoch integer must be >= before and within [before, now+60s]
    - Logs: size must be >= before size (append-only)
    - Repos JSON: must parse and maintain top-level dict shape

    STATIC files continue to use hash comparisons."""
    changes = []

    if before["profile_present"] and after["profile_present"]:
        names = sorted(set(before["paths"]) | set(after["paths"]))
        for name in names:
            b = before["paths"].get(name, ABSENT)
            a = after["paths"].get(name, ABSENT)
            if b != a:
                rel = SENSITIVE_RELPATHS.get(name, name)
                changes.append(f"~/{rel} ({name}) changed")
    elif before["profile_present"] != after["profile_present"]:
        changes.append(
            "~/.claude " + ("appeared" if after["profile_present"] else "disappeared")
            + " during the run"
        )
    # else: absent in both before and after snapshots -> inert, nothing to diff.

    if before["conductor_present"] and after["conductor_present"]:
        now = after.get("snapshot_time", int(time.time()))

        # Check DAEMON-WRITTEN heartbeat files for validity
        before_hbs = before.get("conductor_daemon_heartbeats", {})
        after_hbs = after.get("conductor_daemon_heartbeats", {})
        for name in sorted(set(before_hbs) | set(after_hbs)):
            b_snap = before_hbs.get(name, {"value": ABSENT, "valid": True})
            a_snap = after_hbs.get(name, {"value": ABSENT, "valid": True})

            b_val = b_snap.get("value", ABSENT)
            a_val = a_snap.get("value", ABSENT)

            # Both absent is OK
            if b_val == ABSENT and a_val == ABSENT:
                continue

            # If the after-snapshot marks the value as invalid (non-integer), fail
            if not a_snap.get("valid", True):
                rel = CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.get(name, name)
                changes.append(f"~/{rel} ({name}) invalid: {a_snap.get('error', 'unknown')}")
                continue

            # If before was ABSENT but after is present, that's OK (file created during run)
            if b_val == ABSENT and a_val != ABSENT:
                continue

            # If before was present but after is ABSENT, that's an escape
            if b_val != ABSENT and a_val == ABSENT:
                rel = CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.get(name, name)
                changes.append(f"~/{rel} ({name}) disappeared")
                continue

            # Both present: check bounds
            # - after must be >= before (no going backwards)
            # - after must be <= now + 60 seconds (plausible max drift)
            try:
                b_epoch = int(b_val)
                a_epoch = int(a_val)
                if a_epoch < b_epoch:
                    rel = CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.get(name, name)
                    changes.append(
                        f"~/{rel} ({name}) decreased: before={b_epoch}, after={a_epoch}"
                    )
                elif a_epoch > now + 60:
                    rel = CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.get(name, name)
                    changes.append(
                        f"~/{rel} ({name}) far in future: after={a_epoch}, now={now}"
                    )
            except (ValueError, TypeError):
                # Should not happen if a_snap["valid"] is True, but be defensive
                rel = CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.get(name, name)
                changes.append(f"~/{rel} ({name}) non-integer value")

        # Check DAEMON-WRITTEN log files for growth
        before_logs = before.get("conductor_daemon_logs", {})
        after_logs = after.get("conductor_daemon_logs", {})
        for rel in sorted(set(before_logs) | set(after_logs)):
            b_snap = before_logs.get(rel, {"size": -1})
            a_snap = after_logs.get(rel, {"size": -1})
            b_size = b_snap.get("size", -1)
            a_size = a_snap.get("size", -1)

            if b_size != -1 and a_size < b_size:
                changes.append(f"~/{rel} truncated (before: {b_size}, after: {a_size})")

        # Check DAEMON-WRITTEN repos JSON for parse validity
        before_repos = before.get("conductor_daemon_repos_json", {}).get("repos", {})
        after_repos = after.get("conductor_daemon_repos_json", {}).get("repos", {})

        if before_repos.get("valid", True) and not after_repos.get("valid", True):
            changes.append(
                f"~/conductor3/state/.watchdog-repos.json invalid: "
                f"{after_repos.get('error', 'unknown')}"
            )
        # If both were valid dicts, check that keys are consistent (shape unchanged)
        elif (before_repos.get("valid") and after_repos.get("valid") and
              before_repos.get("keys") and after_repos.get("keys")):
            b_keys = set(before_repos.get("keys", []))
            a_keys = set(after_repos.get("keys", []))
            if b_keys != a_keys:
                # Top-level shape changed (keys added/removed)
                added = sorted(a_keys - b_keys)
                removed = sorted(b_keys - a_keys)
                detail = []
                if added:
                    detail.append(f"added keys: {', '.join(added)}")
                if removed:
                    detail.append(f"removed keys: {', '.join(removed)}")
                changes.append(
                    f"~/conductor3/state/.watchdog-repos.json shape changed "
                    f"({'; '.join(detail)})"
                )

        # Check STATIC JSON files (hash comparison as before)
        before_static = before.get("conductor_static_json", {})
        after_static = after.get("conductor_static_json", {})
        if before_static != after_static:
            b_files = before_static if isinstance(before_static, dict) else {}
            a_files = after_static if isinstance(after_static, dict) else {}
            for rel in sorted(set(b_files) | set(a_files)):
                if b_files.get(rel) != a_files.get(rel):
                    changes.append(f"~/{rel} (conductor3 static json) changed")

    elif before["conductor_present"] != after["conductor_present"]:
        changes.append(
            "~/conductor3 " + ("appeared" if after["conductor_present"] else "disappeared")
            + " during the run"
        )

    if before["scheduled_tasks"] != after["scheduled_tasks"]:
        changes.append(
            "Aesop* Windows scheduled task registrations changed (schtasks /query /fo CSV)"
        )

    gb, ga = before["git_global_config"], after["git_global_config"]
    if gb != ga:
        if gb == ABSENT or ga == ABSENT:
            present = ga if ga != ABSENT else gb
            verb = "appeared" if ga != ABSENT else "disappeared"
            keys_note = f" (keys: {', '.join(present['keys'])})" if present.get("keys") else ""
            changes.append(f"global git config (git config --global -l) {verb}{keys_note}")
        else:
            added = sorted(set(ga["keys"]) - set(gb["keys"]))
            removed = sorted(set(gb["keys"]) - set(ga["keys"]))
            detail = []
            if added:
                detail.append("added keys: " + ", ".join(added))
            if removed:
                detail.append("removed keys: " + ", ".join(removed))
            if gb["hash"] != ga["hash"] and not added and not removed:
                detail.append("values changed for existing keys")
            changes.append(
                "global git config (git config --global -l) changed ("
                + "; ".join(detail or ["unspecified"]) + ")"
            )
    return changes


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(
        description="Fail closed if the real developer profile changes during a test run",
        usage="test_isolation_tripwire.py [--root DIR] -- <command...>",
    )
    parser.add_argument(
        "--root", type=Path, default=Path.home(),
        help="Profile root to protect (default: real home dir); tests point this "
             "at a fake 'real' dir fixture",
    )

    if "--" not in argv:
        print(
            "ERROR: no command given; usage: "
            "test_isolation_tripwire.py [--root DIR] -- <command...>",
            file=sys.stderr,
        )
        return 2
    sep = argv.index("--")
    own_args, command = argv[:sep], argv[sep + 1:]
    if not command:
        print("ERROR: empty command after `--`", file=sys.stderr)
        return 2
    args = parser.parse_args(own_args)

    root = args.root.resolve()
    before = take_snapshot(root)

    if not before["profile_present"]:
        print(
            f"[test_isolation_tripwire] {root}/.claude is absent -- no profile "
            "present, tripwire inert for ~/.claude checks this run "
            "(global git config is still checked)",
            file=sys.stderr,
        )

    # Windows quirk discovered wiring this into `npm run test:sh`: subprocess.run
    # given a bare executable name (e.g. "bash") can resolve to a WSL App
    # Execution Alias stub ("Windows Subsystem for Linux has no installed
    # distributions") instead of the real PATH entry (Git's bash.exe), even
    # though `where`/shutil.which correctly rank Git's bash first. Pre-resolve
    # the wrapped command's own executable through shutil.which so the alias
    # interception never gets a bare name to latch onto; fall back to the
    # original token unchanged if which() can't find it (e.g. it's a shell
    # builtin or already an absolute path), so behavior elsewhere is unaffected.
    resolved_command = list(command)
    if resolved_command:
        which_path = shutil.which(resolved_command[0])
        if which_path:
            resolved_command[0] = which_path

    proc = subprocess.run(resolved_command, cwd=str(Path.cwd()))
    cmd_exit = proc.returncode if proc.returncode is not None else 1

    after = take_snapshot(root)
    changes = diff_snapshots(before, after)

    if changes:
        print(
            "[test_isolation_tripwire] FAIL: the real profile changed during this run:",
            file=sys.stderr,
        )
        for c in changes:
            print(f"  - {c}", file=sys.stderr)
        print(
            "[test_isolation_tripwire] A test reached the real profile -- see "
            "tests/helpers/isolated-env.mjs and LANE-CONTRACT.md. Restore the "
            "changed path(s) from git and fix the test's isolation before "
            "re-running.",
            file=sys.stderr,
        )
        tripwire_exit = 1
    else:
        tripwire_exit = 0
        if before["profile_present"]:
            print("[test_isolation_tripwire] OK: real profile unchanged", file=sys.stderr)

    return max(cmd_exit, tripwire_exit)


if __name__ == "__main__":
    sys.exit(main())
