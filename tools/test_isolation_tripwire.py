#!/usr/bin/env python3
"""
Test-isolation tripwire: fails closed if the REAL developer profile changes
while a test suite runs.
INDEX: Test-isolation tripwire (incident 2026-10-05: installSkills() in bin/cli.js overwrote real ~/.claude/skills during a Node test run); snapshots sha256 of ~/.claude/{skills,settings.json,memory,hooks} + `git config --global -l` before/after wrapping a command, FAILS CLOSED naming every changed path when the profile exists, degrades loudly-but-green ("no profile present, tripwire inert") when ~/.claude is absent (CI); CLI: `[--root DIR] -- <command...>`; exit = max(wrapped command's exit code, tripwire finding); stdlib-only. Extended 2026-10-06 (shell-test-isolation incident) to also snapshot `<root>/conductor3/state/.watchdog-heartbeat`, `conductor3/monitor/.monitor-heartbeat`, `conductor3/state/*.json`, and Windows Aesop* scheduled-task registrations (`schtasks /query /fo CSV`, Windows-only / AESOP_TRIPWIRE_SCHTASKS_CMD override for tests).

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
import os
import shlex
import shutil
import subprocess
import sys
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

# Logical name -> path relative to the profile root, checked whenever the root's
# conductor3 directory exists. Added after the 2026-10-06 incident: a shell-test
# lane left the test placeholder "1234567890" in the LIVE
# ~/conductor3/state/.watchdog-heartbeat because tests/test-selfheal.sh and
# daemons/{selfheal,backup-fleet,run-watchdog}.sh fall back to AESOP_ROOT/
# CONDUCTOR_ROOT defaults that resolve to the real conductor3 whenever a test
# invocation forgets to pin them. tools/run_shell_tests.sh now exports an
# isolated root for the whole suite (the structural fix); this tripwire is the
# independent proof it held, and the backstop for any future shell escape.
CONDUCTOR_FILE_RELPATHS = {
    "watchdog_heartbeat": "conductor3/state/.watchdog-heartbeat",
    "monitor_heartbeat": "conductor3/monitor/.monitor-heartbeat",
}

# Lock/state JSON files under conductor3/state/ (tracker.json,
# orchestrator-status.json, .watchdog-repos.json, etc.) -- globbed rather than
# individually named since the set of state files evolves.
CONDUCTOR_STATE_JSON_GLOB = "conductor3/state/*.json"


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


def snapshot_path(abs_path: Path):
    """Fingerprint one sensitive path: {relpath: hash} for a dir, {'<file>': hash}
    for a single file, or the ABSENT sentinel."""
    if not abs_path.exists():
        return ABSENT
    if abs_path.is_file():
        return {"<file>": _hash_file(abs_path)}
    return _hash_tree(abs_path)


def snapshot_glob(root: Path, pattern: str) -> dict:
    """Return {relpath: sha256} for every file matching `pattern` under root
    (sorted, deterministic); empty dict if nothing matches."""
    out = {}
    for p in sorted(root.glob(pattern)):
        if p.is_file():
            rel = str(p.relative_to(root)).replace("\\", "/")
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
    snap = {
        "profile_present": profile_present,
        "paths": {},
        "git_global_config": snapshot_git_global_config(root),
        "conductor_present": conductor_present,
        "conductor_paths": {},
        "scheduled_tasks": snapshot_scheduled_tasks(),
    }
    if profile_present:
        for name, rel in SENSITIVE_RELPATHS.items():
            snap["paths"][name] = snapshot_path(root / rel)
    if conductor_present:
        for name, rel in CONDUCTOR_FILE_RELPATHS.items():
            snap["conductor_paths"][name] = snapshot_path(root / rel)
        snap["conductor_paths"]["state_json"] = snapshot_glob(root, CONDUCTOR_STATE_JSON_GLOB)
    return snap


def diff_snapshots(before: dict, after: dict):
    """Return a list of human-readable changed-path descriptions, or [] if clean."""
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
        names = sorted(set(before["conductor_paths"]) | set(after["conductor_paths"]))
        for name in names:
            b = before["conductor_paths"].get(name, ABSENT)
            a = after["conductor_paths"].get(name, ABSENT)
            if b == a:
                continue
            if name == "state_json":
                b_files = b if isinstance(b, dict) else {}
                a_files = a if isinstance(a, dict) else {}
                for rel in sorted(set(b_files) | set(a_files)):
                    if b_files.get(rel) != a_files.get(rel):
                        # `rel` from snapshot_glob() is already root-relative
                        # (it includes the "conductor3/state/" prefix via
                        # Path.relative_to(root)) -- do not prepend it again.
                        changes.append(f"~/{rel} (conductor3 state json) changed")
            else:
                rel = CONDUCTOR_FILE_RELPATHS.get(name, name)
                changes.append(f"~/{rel} ({name}) changed")
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
