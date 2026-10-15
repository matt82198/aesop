#!/usr/bin/env python3
"""
Test-isolation tripwire: fails closed if the REAL developer profile changes
while a test suite runs.
INDEX: Test-isolation tripwire (incident 2026-10-05: installSkills() in bin/cli.js overwrote real ~/.claude/skills during a Node test run); snapshots sha256 of ~/.claude/{skills,settings.json,memory,hooks} + `git config --global -l` before/after wrapping a command, FAILS CLOSED naming every changed path when the profile exists, degrades loudly-but-green ("no profile present, tripwire inert") when ~/.claude is absent (CI); CLI: `[--root DIR] -- <command...>`; exit = max(wrapped command's exit code, tripwire finding); stdlib-only. Extended 2026-10-06 (shell-test-isolation incident) to also snapshot fleet-state heartbeat/state files, and Windows Aesop* scheduled-task DEFINITIONS. Daemon-written files use VALIDITY checks (heartbeat epoch bounds, log growth, JSON parse+shape) instead of hash comparisons since daemons legitimately update them during test runs. Fixed 2026-10-06 (flaky-tripwire incident): the scheduled-task check was fingerprinting `schtasks /query /fo CSV`'s Status column, which the live AesopWatchdogDaemon/AesopRefinementMonitor flip between Running/Ready every few minutes with no test touching tasks, alternating OK/FAIL with no real change -- now lists Aesop* task names via CSV (AESOP_TRIPWIRE_SCHTASKS_CMD override), then hashes each task's DEFINITION ONLY via `schtasks /query /tn <name> /xml` (AESOP_TRIPWIRE_SCHTASKS_XML_CMD override, `{name}` placeholder) -- the exported Task Scheduler XML schema never contains LastRunTime/NextRunTime/Last Result/State, only Triggers/Actions/Principals/Settings, so run-state churn can no longer flip this check.

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
fleet-state heartbeat file because the hash-snapshot approach
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
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Add the repo root to sys.path so we can import tools.common (works whether
# this script is run from repo root or from anywhere else)
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import the shared resolver
from tools import common

ABSENT = "<absent>"

# Logical name -> path relative to the profile root, checked whenever the root's
# .claude directory exists.
SENSITIVE_RELPATHS = {
    "skills": ".claude/skills",
    "settings": ".claude/settings.json",
    "memory": ".claude/memory",
    "hooks": ".claude/hooks",
}

# DAEMON-WRITTEN heartbeat files under the fleet-state root. These are written by:
# - daemons/backup-fleet.sh writes .watchdog-heartbeat with `date +%s` every 150s
# - monitor/collect-signals.mjs writes .monitor-heartbeat with epoch seconds
# These must use VALIDITY checks (epoch bounds) not hash comparisons.
# Paths are relative to the conductor root (derived from CONDUCTOR_ROOT env or parent-of-AESOP_ROOT).
CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS = {
    "watchdog_heartbeat": "state/.watchdog-heartbeat",
    "monitor_heartbeat": "monitor/.monitor-heartbeat",
}

# DAEMON-WRITTEN log files under the fleet-state root. These may only GROW
# (append-only). Checked via size comparison, not hashes.
# Patterns are relative to the conductor root.
CONDUCTOR_DAEMON_LOG_PATTERNS = ["state/FLEET-BACKUP.log", "state/cron-*.log"]

# DAEMON-WRITTEN repos JSON file. Must parse as JSON and maintain top-level shape.
# Path is relative to the conductor root.
CONDUCTOR_DAEMON_REPOS_JSON_RELPATH = "state/.watchdog-repos.json"

# STATIC JSON files under the fleet-state root (lock files, tracker.json, etc.)
# These use hash comparisons since they should never be touched by running daemons
# during a test suite. Pattern is relative to the conductor root.
CONDUCTOR_STATIC_JSON_GLOB = "state/*.json"

# LIVE-WRITER allowlist -- the ONE place a live daemon's watched output is
# registered (2026-10-15 incident: the usage-watchdog daemon rewrites its status
# JSON every tick, so a test run that overlapped it failed the push). Keys are
# fleet-state-relative paths. `evidence` are fleet-state-relative globs for the
# writer's pidfile or heartbeat. An entry is honoured ONLY when an evidence file
# has an mtime inside the run window; an allowlisted file WITHOUT that evidence
# still FAILS. Names enumerated from daemons/, tools/ and the live fleet-state
# root on 2026-10-15:
#  - state/.watchdog-heartbeat and monitor/.monitor-heartbeat are NOT listed:
#    they already use epoch VALIDITY checks, which a live tick passes.
#  - state/FLEET-BACKUP.log and state/cron-*.log are NOT listed: growth checks.
#  - Task-event and receipt-spool logs are NOT listed: the receipt spool lives
#    under the git common dir and task-events.jsonl is outside the watched set,
#    so an entry here would be dead allowance.
LIVE_WRITER_ALLOWLIST = {
    "state/usage-watchdog.status.json": {
        "writer": "~/scripts/usage_watchdog.py (status table rewritten every tick)",
        "evidence": ("state/usage-watchdog*.pid", "state/usage-watchdog*heartbeat*"),
    },
}

_REFLOG_TS_RE = re.compile(r"HEAD@\{(\d+)\}")


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


def _list_aesop_task_names():
    """List Aesop* Windows scheduled task names via `schtasks /query /fo CSV`
    (NAME ONLY -- the Status/Next Run Time columns are run-state, never
    hashed). AESOP_TRIPWIRE_SCHTASKS_CMD lets tests stub the command on any
    platform; otherwise this is Windows-only. Returns None if the list could
    not be determined (non-Windows, schtasks unavailable/failed, unparseable
    CSV) -- distinct from an empty list, which means "ran fine, no Aesop
    tasks registered"."""
    override = os.environ.get("AESOP_TRIPWIRE_SCHTASKS_CMD")
    if override:
        cmd = shlex.split(override)
    elif sys.platform == "win32":
        cmd = ["schtasks", "/query", "/fo", "CSV"]
    else:
        return None
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    try:
        rows = list(csv.reader(res.stdout.splitlines()))
    except csv.Error:
        return None
    if not rows:
        return None
    header = [c.strip().lower() for c in rows[0]]
    try:
        name_idx = header.index("taskname")
    except ValueError:
        return None
    return sorted({
        row[name_idx] for row in rows[1:]
        if len(row) > name_idx and "aesop" in row[name_idx].lower()
    })


def _query_task_definition_xml(name: str):
    """Fetch one task's exported Task Scheduler definition XML via
    `schtasks /query /tn <name> /xml`. The exported Task schema (RegistrationInfo/
    Triggers/Actions/Principals/Settings) structurally never contains
    LastRunTime/NextRunTime/Last Result/State -- those are runtime query-only
    fields the Scheduler service tracks separately -- so this is DEFINITION
    ONLY by construction, with no field-stripping needed.
    AESOP_TRIPWIRE_SCHTASKS_XML_CMD lets tests stub this per-task; it is a
    shlex-split template where the literal token "{name}" is replaced with
    the task name (as one argv entry, so names with spaces/backslashes are
    never re-split). Returns None on any failure (unavailable/non-Windows/
    error/timeout)."""
    override = os.environ.get("AESOP_TRIPWIRE_SCHTASKS_XML_CMD")
    if override:
        template = shlex.split(override)
        cmd = [name if tok == "{name}" else tok for tok in template]
    elif sys.platform == "win32":
        cmd = ["schtasks", "/query", "/tn", name, "/xml"]
    else:
        return None
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def snapshot_scheduled_tasks():
    """Fingerprint Aesop* Windows scheduled task DEFINITIONS -- never
    run-state. Lists Aesop* task names (`schtasks /query /fo CSV`, name
    column only), then for each task hashes its full definition XML
    (`schtasks /query /tn <name> /xml`), which structurally excludes
    LastRunTime/NextRunTime/Last Result/State (see
    _query_task_definition_xml). Returns {task_name: sha256_of_xml}, or the
    ABSENT sentinel when the list is undeterminable (non-Windows, schtasks
    unavailable/failed) or empty (no Aesop tasks registered) -- matching this
    tripwire's loudly-but-green contract. A task whose XML query itself
    fails mid-run is recorded as an explicit error string (not silently
    dropped), so losing query access to a previously-queryable task still
    registers as a change."""
    names = _list_aesop_task_names()
    if not names:
        return ABSENT
    result = {}
    for name in names:
        xml_text = _query_task_definition_xml(name)
        if xml_text is None:
            result[name] = "ERROR: /xml query failed"
            continue
        result[name] = hashlib.sha256(xml_text.encode("utf-8", "replace")).hexdigest()
    return result


def _git_checkout_query(checkout: Path, *args: str):
    """Run `git -C <checkout> <args>`; return stdout, or None on ANY failure
    (git missing, non-zero exit, timeout). AESOP_TRIPWIRE_GIT_CMD lets tests
    stub the git binary, so the fail-closed path is provable."""
    override = os.environ.get("AESOP_TRIPWIRE_GIT_CMD")
    base = shlex.split(override) if override else ["git"]
    try:
        res = subprocess.run(
            base + ["-C", str(checkout), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def snapshot_git_checkout(checkout: Path):
    """Describe a watched root that is its own git checkout (has .git), or return
    None when it is not one. Records HEAD, the porcelain status map (path -> XY)
    and the tracked-file set. Any git failure yields {"error": ...}, which makes
    the attribution fail closed."""
    if not (checkout / ".git").exists():
        return None
    top = _git_checkout_query(checkout, "rev-parse", "--show-toplevel")
    head = _git_checkout_query(checkout, "rev-parse", "HEAD")
    status_out = _git_checkout_query(
        checkout, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all")
    tracked_out = _git_checkout_query(checkout, "ls-files", "-z")
    if top is None or head is None or status_out is None or tracked_out is None:
        return {"error": f"git could not answer for {checkout}"}
    try:
        if Path(top.strip()).resolve() != checkout.resolve():
            return {"error": f"{checkout} is not its own git toplevel"}
    except OSError as e:
        return {"error": f"toplevel unresolvable: {e}"}
    status = {}
    for entry in status_out.split("\0"):
        if len(entry) > 3:
            status[entry[3:]] = entry[:2]
    tracked = {t for t in tracked_out.split("\0") if t}
    return {"head": head.strip(), "status": status, "tracked": tracked, "error": None}


def _sensitive_changed_rels(name: str, b, a):
    """Profile-relative paths (e.g. '.claude/hooks/a.py') whose content differs
    between two snapshots of one SENSITIVE_RELPATHS entry. Returns [] when
    unchanged, or None when the change cannot be enumerated file by file."""
    if b == a:
        return []
    base = SENSITIVE_RELPATHS[name]

    def full(key):
        return base if key == "<file>" else f"{base}/{key}"

    if isinstance(b, dict) and isinstance(a, dict):
        rels = [k for k in set(b) | set(a) if b.get(k) != a.get(k)]
    elif isinstance(b, dict):
        rels = list(b)
    elif isinstance(a, dict):
        rels = list(a)
    else:
        return None
    if not rels:
        return None
    return [full(k) for k in sorted(rels)]


def _reflog_times_in_window(checkout: Path, start: int, end: int) -> list:
    """Unix times of HEAD reflog entries of `checkout` inside the run window
    [start-1, end+1] (+/-1s for integer truncation). [] when git cannot answer."""
    out = _git_checkout_query(
        checkout, "log", "-g", "-n", "200", "--date=unix", "--format=%gd", "HEAD")
    if out is None:
        return []
    stamps = [int(m.group(1)) for m in _REFLOG_TS_RE.finditer(out)]
    return sorted(t for t in stamps if start - 1 <= t <= end + 1)


def _evidence_in_window(conductor_root, globs, start: int, end: int) -> list:
    """(fleet-state-relative path, mtime) for each pidfile/heartbeat matching
    `globs` whose mtime falls inside the run window. [] when none does."""
    hits = []
    if conductor_root is None:
        return hits
    for pattern in globs:
        for p in sorted(conductor_root.glob(pattern)):
            try:
                if not p.is_file():
                    continue
                mtime = int(p.stat().st_mtime)
            except OSError:
                continue
            if start - 1 <= mtime <= end + 1:
                hits.append((str(p.relative_to(conductor_root)).replace("\\", "/"), mtime))
    return hits


def build_attributor(before: dict, after: dict):
    """Return attribute(scope, rel) -> (classification, detail) for one run.

    scope "profile": rel is profile-relative ('.claude/hooks/a.py').
    scope "fleet":   rel is fleet-state-relative ('state/usage-watchdog.status.json').
    Classifications:
      "git-deploy"  profile file inside a git checkout whose HEAD moved during the
                    run (reflog entry inside the window) and which is now a clean
                    tracked file: another lane's `git pull`/checkout, not a test.
      "live-writer" fleet file registered in LIVE_WRITER_ALLOWLIST with a
                    pidfile/heartbeat mtime inside the window.
      "unrelated"   everything else; it FAILS the run.
    """
    start = int(before.get("snapshot_time", 0))
    end = int(after.get("snapshot_time", start))
    cp = after.get("conductor_root")
    conductor_root = Path(cp) if cp else None
    b_co, a_co = before.get("git_checkout"), after.get("git_checkout")
    git_ok = (
        isinstance(b_co, dict) and isinstance(a_co, dict)
        and not b_co.get("error") and not a_co.get("error")
        and b_co["head"] != a_co["head"]
    )
    reflog, differs = [], set()
    if git_ok:
        checkout = Path(after["claude_root"])
        reflog = _reflog_times_in_window(checkout, start, end)
        head_diff = _git_checkout_query(checkout, "diff", "--name-only", "-z", "HEAD")
        if head_diff is None or not reflog:
            git_ok = False
        else:
            differs = {p for p in head_diff.split("\0") if p}
    old = b_co["head"][:12] if git_ok else ""
    new = a_co["head"][:12] if git_ok else ""

    def attribute_profile(rel: str):
        if not git_ok or not rel.startswith(".claude/"):
            return "unrelated", "no attributable git HEAD advance"
        co_rel = rel[len(".claude/"):]
        tracked = a_co["tracked"] | b_co["tracked"]
        if co_rel not in tracked or co_rel in a_co["status"] or co_rel in differs:
            return "unrelated", "not a clean tracked file at the advanced HEAD"
        return "git-deploy", f"changed by git ({old}..{new}, reflog {reflog[-1]})"

    def attribute_fleet(rel: str):
        spec = LIVE_WRITER_ALLOWLIST.get(rel)
        if spec is None:
            return "unrelated", "not a registered live-writer file"
        hits = _evidence_in_window(conductor_root, spec["evidence"], start, end)
        if not hits:
            return "unrelated", (
                f"registered live writer ({spec['writer']}) but no pidfile or "
                "heartbeat mtime inside the run window")
        ev_rel, mtime = hits[0]
        return "live-writer", (
            f"written by live writer {spec['writer']} "
            f"(evidence fleet-state/{ev_rel} mtime {mtime})")

    def attribute(scope: str, rel: str):
        return attribute_profile(rel) if scope == "profile" else attribute_fleet(rel)

    return attribute


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

    # Resolve the conductor root. When running tests with --root, this will be
    # the fake home; the conductor root should exist relative to it.
    # Set up AESOP_ROOT so common.get_conductor_root() computes correctly when
    # --root is given (used by tests to pin the fake home).
    old_aesop_root = os.environ.get("AESOP_ROOT")
    try:
        # When testing with --root, set AESOP_ROOT to root so conductor root resolves
        # relative to the test fixture, not the real repo.
        if old_aesop_root is None and "CONDUCTOR_ROOT" not in os.environ:
            os.environ["AESOP_ROOT"] = str(root)
        conductor_root = common.get_conductor_root()
    finally:
        # Restore the original AESOP_ROOT
        if old_aesop_root is None:
            os.environ.pop("AESOP_ROOT", None)
        else:
            os.environ["AESOP_ROOT"] = old_aesop_root

    conductor_present = conductor_root.exists()
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
        "conductor_file_count": 0,
        "claude_root": str(claude_root),
        "conductor_root": str(conductor_root) if conductor_present else None,
        "git_checkout": None,  # ~/.claude git state, when it is its own checkout
    }
    if conductor_present:
        snap["conductor_file_count"] = sum(1 for p in conductor_root.rglob("*") if p.is_file())
    if profile_present:
        for name, rel in SENSITIVE_RELPATHS.items():
            snap["paths"][name] = snapshot_path(root / rel)
        snap["git_checkout"] = snapshot_git_checkout(claude_root)
    if conductor_present:
        # DAEMON-WRITTEN heartbeats: snapshot for epoch bounds checking
        for name, rel in CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.items():
            path = conductor_root / rel
            snap["conductor_daemon_heartbeats"][name] = snapshot_heartbeat(path)

        # DAEMON-WRITTEN logs: snapshot for growth checking
        for pattern in CONDUCTOR_DAEMON_LOG_PATTERNS:
            for path in sorted(conductor_root.glob(pattern)):
                rel = str(path.relative_to(conductor_root)).replace("\\", "/")
                snap["conductor_daemon_logs"][rel] = snapshot_log_file(path)

        # DAEMON-WRITTEN repos JSON: snapshot for parse check
        repos_path = conductor_root / CONDUCTOR_DAEMON_REPOS_JSON_RELPATH
        snap["conductor_daemon_repos_json"]["repos"] = snapshot_repos_json(repos_path)

        # STATIC JSON files: hash comparison as before (but exclude .watchdog-repos.json)
        # since that's handled by daemon_repos_json above
        exclude_repos = {CONDUCTOR_DAEMON_REPOS_JSON_RELPATH}
        snap["conductor_static_json"] = snapshot_glob(
            conductor_root, CONDUCTOR_STATIC_JSON_GLOB, exclude_files=exclude_repos
        )
    return snap


def diff_snapshots(before: dict, after: dict, attributor=None, ledger=None):
    """Return a list of human-readable FAILURE descriptions, or [] if clean.

    attributor: callable from build_attributor(); when given, each changed
    profile file or fleet-state static JSON file is classified before judging.
    Only "unrelated" paths become failures. ledger: optional list that receives
    one (path, classification, detail) tuple per attributed-or-unrelated path.

    DAEMON-WRITTEN files use VALIDITY checks (not hash comparisons):
    - Heartbeats: epoch integer must be >= before and within [before, now+60s]
    - Logs: size must be >= before size (append-only)
    - Repos JSON: must parse and maintain top-level dict shape

    STATIC files continue to use hash comparisons."""
    changes = []

    def classify(scope: str, rel: str):
        if attributor is None:
            cls, detail = "unrelated", "no attribution requested"
        else:
            cls, detail = attributor(scope, rel)
        return cls, detail

    if before["profile_present"] and after["profile_present"]:
        names = sorted(set(before["paths"]) | set(after["paths"]))
        for name in names:
            b = before["paths"].get(name, ABSENT)
            a = after["paths"].get(name, ABSENT)
            if b != a:
                rels = _sensitive_changed_rels(name, b, a)
                if rels is None:
                    rels = [SENSITIVE_RELPATHS.get(name, name)]
                for full in rels:
                    cls, detail = classify("profile", full)
                    if ledger is not None:
                        ledger.append((f"~/{full}", cls, detail))
                    if cls == "unrelated":
                        changes.append(f"~/{full} ({name}) changed")
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
                changes.append(f"fleet-state/{rel} ({name}) invalid: {a_snap.get('error', 'unknown')}")
                continue

            # If before was ABSENT but after is present, the file was CREATED during the
            # run. A legitimate daemon creates it with a fresh epoch; a test writing a
            # far-past placeholder epoch must still fail, not pass as "new file".
            if b_val == ABSENT and a_val != ABSENT:
                rel = CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.get(name, name)
                before_time = before.get("snapshot_time", now)
                try:
                    created_epoch = int(a_val)
                except (ValueError, TypeError):
                    created_epoch = -1
                if not (before_time - 60 <= created_epoch <= now + 60):
                    changes.append(
                        f"fleet-state/{rel} ({name}) created with stale/placeholder epoch: {a_val}"
                    )
                continue

            # If before was present but after is ABSENT, that's an escape
            if b_val != ABSENT and a_val == ABSENT:
                rel = CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.get(name, name)
                changes.append(f"fleet-state/{rel} ({name}) disappeared")
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
                        f"fleet-state/{rel} ({name}) decreased: before={b_epoch}, after={a_epoch}"
                    )
                elif a_epoch > now + 60:
                    rel = CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.get(name, name)
                    changes.append(
                        f"fleet-state/{rel} ({name}) far in future: after={a_epoch}, now={now}"
                    )
            except (ValueError, TypeError):
                # Should not happen if a_snap["valid"] is True, but be defensive
                rel = CONDUCTOR_DAEMON_HEARTBEAT_RELPATHS.get(name, name)
                changes.append(f"fleet-state/{rel} ({name}) non-integer value")

        # Check DAEMON-WRITTEN log files for growth
        before_logs = before.get("conductor_daemon_logs", {})
        after_logs = after.get("conductor_daemon_logs", {})
        for rel in sorted(set(before_logs) | set(after_logs)):
            b_snap = before_logs.get(rel, {"size": -1})
            a_snap = after_logs.get(rel, {"size": -1})
            b_size = b_snap.get("size", -1)
            a_size = a_snap.get("size", -1)

            if b_size != -1 and a_size < b_size:
                changes.append(f"fleet-state/{rel} truncated (before: {b_size}, after: {a_size})")

        # Check DAEMON-WRITTEN repos JSON for parse validity
        before_repos = before.get("conductor_daemon_repos_json", {}).get("repos", {})
        after_repos = after.get("conductor_daemon_repos_json", {}).get("repos", {})

        if before_repos.get("valid", True) and not after_repos.get("valid", True):
            changes.append(
                f"fleet-state/state/.watchdog-repos.json invalid: "
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
                    f"fleet-state/state/.watchdog-repos.json shape changed "
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
                    cls, detail = classify("fleet", rel)
                    if ledger is not None:
                        ledger.append((f"fleet-state/{rel}", cls, detail))
                    if cls == "unrelated":
                        note = f" [{detail}]" if rel in LIVE_WRITER_ALLOWLIST else ""
                        changes.append(f"fleet-state/{rel} (fleet-state root) changed{note}")

    elif before["conductor_present"] != after["conductor_present"]:
        # A bare, EMPTY fleet-state root being created is inert: it holds no state
        # the tripwire could not check, and CI runners have no fleet-state root at all.
        # Any FILE created under it is drift, because its content is unvalidated.
        if not after["conductor_present"]:
            changes.append("fleet-state root disappeared during the run")
        elif after.get("conductor_file_count", 0) > 0:
            changes.append(
                "fleet-state root appeared during the run with "
                f"{after['conductor_file_count']} file(s) created under it"
            )

    if before["scheduled_tasks"] != after["scheduled_tasks"]:
        b_tasks = before["scheduled_tasks"] if isinstance(before["scheduled_tasks"], dict) else {}
        a_tasks = after["scheduled_tasks"] if isinstance(after["scheduled_tasks"], dict) else {}
        added = sorted(set(a_tasks) - set(b_tasks))
        removed = sorted(set(b_tasks) - set(a_tasks))
        changed = sorted(n for n in (set(a_tasks) & set(b_tasks)) if a_tasks[n] != b_tasks[n])
        detail = []
        if added:
            detail.append("new task(s): " + ", ".join(added))
        if removed:
            detail.append("removed task(s): " + ", ".join(removed))
        if changed:
            detail.append("definition changed: " + ", ".join(changed))
        if not detail:
            detail.append("unspecified (schtasks became unavailable on one side)")
        changes.append(
            "Aesop* Windows scheduled task definition(s) changed "
            "(schtasks /query /tn NAME /xml): " + "; ".join(detail)
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
    ledger = []
    changes = diff_snapshots(
        before, after, attributor=build_attributor(before, after), ledger=ledger)
    for path, cls, detail in ledger:
        if cls in ("git-deploy", "live-writer"):
            print(f"[test_isolation_tripwire] INFO: {path} {detail}, not a test",
                  file=sys.stderr)
    if ledger:
        verdict = "; ".join(f"{path}={cls}" for path, cls, _ in ledger)
        print(f"[test_isolation_tripwire] VERDICT: {len(ledger)} changed path(s) "
              f"classified: {verdict}", file=sys.stderr)

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
