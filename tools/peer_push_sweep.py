#!/usr/bin/env python3
"""Peer push sweep: push committed codex/* and agy/* worktree branches to origin.

INDEX: Pushes one unpushed codex/agy worktree branch per watchdog cycle via a locked detached worker.

Why: Codex and agy lanes commit to their worktree branches but never push. The sweep runs
once per watchdog cycle (daemons/run-watchdog.sh) and pushes at most ONE unpushed branch per
cycle, oldest first. The push runs in a detached worker that holds a lock file, so the
5-minute cycle never waits on the ~12 minute pre-push gate.

Usage:
  peer_push_sweep.py [--aesop-root DIR] [--config FILE] [--repo DIR]... [--pattern GLOB]...
                     [--state-dir DIR] [--log FILE] [--dry-run]

Configuration (aesop.config.json, under "peer_push"; same repos-list shape as the backup fleet):
  {"peer_push": {"repos": [{"path": "~/scripts"}], "branch_patterns": ["codex/*", "agy/*"]}}
Defaults: the aesop root, ~/.claude, ~/scripts, ~/conductor3; patterns codex/* and agy/*.

Rules:
  - never push main, master or a detached HEAD;
  - never --force or --no-verify; the repo's own pre-push hook must run;
  - a refusal is logged BLOCKED with its reason and the same sha is not retried;
  - uncommitted changes are reported (DIRTY), never committed.
Exit codes: 0 = cycle ran (including IN-FLIGHT and nothing-to-do); 2 = bad config or usage.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_PATTERNS = ("codex/*", "agy/*")
DEFAULT_REPOS = ("~/.claude", "~/scripts", "~/conductor3")
PROTECTED_BRANCHES = frozenset({"main", "master"})
LOCK_NAME = ".peer-push.lock"
STATE_NAME = ".peer-push-state.json"
LOG_NAME = "FLEET-BACKUP.log"
STALE_LOCK_S = 3 * 60 * 60
PUSH_TIMEOUT_S = 60 * 60
GIT_TIMEOUT_S = 120
REASON_MAX = 300
EXIT_OK = 0
EXIT_USAGE = 2
# Windows creation flags: detach the worker from the cycle's console.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


class ConfigError(Exception):
    """aesop.config.json peer_push section is unreadable or malformed."""


@dataclasses.dataclass(frozen=True)
class Candidate:
    """A pattern-matching worktree branch and what the sweep knows about it."""

    repo: str
    wt: str
    branch: str
    sha: str
    unpushed: int
    commit_time: int
    dirty: bool

    @property
    def key(self) -> str:
        return f"{self.wt}|{self.branch}"


# ---------------------------------------------------------------- logging

def now_stamp() -> str:
    """Local timestamp in the watchdog log format."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def format_line(repo: str, branch: str, sha: str, outcome: str, reason: str) -> str:
    """One action line: [ts] peer-push <repo> <branch> <sha12> <OUTCOME> <reason>."""
    clean = " ".join(reason.split())[:REASON_MAX] or "-"
    return f"[{now_stamp()}] peer-push {repo} {branch} {(sha or '-')[:12]} {outcome} {clean}"


# ---------------------------------------------------------------- git helpers

def git(repo_dir: str, *args: str, timeout: int = GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    """Run git -C <repo_dir> <args> without a shell, stdin closed, never prompting."""
    return subprocess.run(
        ["git", "-C", repo_dir, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


def parse_worktrees(porcelain: str) -> list[dict]:
    """Parse `git worktree list --porcelain` into dicts (worktree, HEAD, branch, flags)."""
    records: list[dict] = []
    current: dict = {}
    for line in porcelain.splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "branch" and value.startswith("refs/heads/"):
            current["branch"] = value[len("refs/heads/"):]
        elif key in ("worktree", "HEAD"):
            current[key] = value
        elif key in ("detached", "bare", "prunable", "locked"):
            current[key] = True
    if current:
        records.append(current)
    return records


def is_candidate_branch(branch: str | None, patterns) -> bool:
    """True for a named, non-protected branch matching one of the patterns."""
    if not branch or branch in PROTECTED_BRANCHES:
        return False
    return any(fnmatch.fnmatchcase(branch, pattern) for pattern in patterns)


def count_unpushed(wt: str, branch: str) -> int | None:
    """Commits on HEAD not on origin/<branch>; without that ref, not on any origin ref."""
    remote_ref = f"refs/remotes/origin/{branch}"
    if git(wt, "rev-parse", "--verify", "-q", remote_ref).returncode == 0:
        rev_args = [f"{remote_ref}..HEAD"]
    else:
        rev_args = ["HEAD", "--not", "--remotes=origin"]
    res = git(wt, "rev-list", "--count", *rev_args)
    if res.returncode != 0:
        return None
    return int(res.stdout.strip() or 0)


def build_candidate(repo_name: str, wt: str, branch: str) -> Candidate | None:
    """Gather sha, unpushed count, commit time and dirty flag; None when git cannot say."""
    sha = git(wt, "rev-parse", "HEAD")
    unpushed = count_unpushed(wt, branch)
    commit_time = git(wt, "log", "-1", "--format=%ct", "HEAD")
    status = git(wt, "status", "--porcelain")
    if sha.returncode != 0 or unpushed is None or commit_time.returncode != 0 or status.returncode != 0:
        print(f"peer-push: skipped {repo_name} {branch}: git could not read worktree {wt}", file=sys.stderr)
        return None
    return Candidate(
        repo=repo_name,
        wt=wt,
        branch=branch,
        sha=sha.stdout.strip(),
        unpushed=unpushed,
        commit_time=int(commit_time.stdout.strip() or 0),
        dirty=bool(status.stdout.strip()),
    )


def scan_repo(repo_path: str, patterns) -> list[Candidate]:
    """All pattern-matching, non-prunable worktrees of one repo (shared by all its worktrees)."""
    listing = git(repo_path, "worktree", "list", "--porcelain")
    if listing.returncode != 0:
        print(f"peer-push: skipped {repo_path}: not a readable git repo", file=sys.stderr)
        return []
    repo_name = Path(repo_path).name or repo_path
    found: list[Candidate] = []
    for wt in parse_worktrees(listing.stdout):
        path, branch = wt.get("worktree", ""), wt.get("branch")
        if wt.get("prunable") or not path or not os.path.isdir(path):
            continue
        if not is_candidate_branch(branch, patterns):
            continue
        cand = build_candidate(repo_name, path, branch)
        if cand is not None:
            found.append(cand)
    return found


# ---------------------------------------------------------------- lock

def lock_holder(lock_path: Path) -> dict | None:
    """Holder record of a live lock, or None when no lock or the lock is stale."""
    try:
        age = time.time() - lock_path.stat().st_mtime
    except FileNotFoundError:
        return None
    if age > STALE_LOCK_S:
        return None
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def reclaim_stale_lock(lock_path: Path) -> None:
    """Remove a lock older than STALE_LOCK_S (its worker died); a live lock is untouched."""
    try:
        age = time.time() - lock_path.stat().st_mtime
    except FileNotFoundError:
        return
    if age > STALE_LOCK_S:
        lock_path.unlink(missing_ok=True)


def acquire_lock(lock_path: Path, holder: dict) -> bool:
    """Atomically create the lock with the holder record; False when someone else holds it."""
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(holder, fh)
    return True


# ---------------------------------------------------------------- state and log

def load_state(state_dir: Path) -> dict:
    """Blocked-sha markers and last-logged signatures (dedupe); corrupt state starts fresh."""
    try:
        data = json.loads((state_dir / STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("blocked", {})
    data.setdefault("last", {})
    return data


def save_state(state_dir: Path, state: dict) -> None:
    """Atomic write of the state file."""
    target = state_dir / STATE_NAME
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, target)


def emit(state: dict, log_path: Path, repo: str, branch: str, key: str, sha: str,
         outcome: str, reason: str) -> None:
    """Append one action line unless the same outcome for this key was already logged."""
    slot = f"{key}|{outcome}"
    signature = f"{sha}|{reason}"
    if state["last"].get(slot) == signature:
        return
    state["last"][slot] = signature
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(format_line(repo, branch, sha, outcome, reason) + "\n")


# ---------------------------------------------------------------- selection

def choose_target(cands: list[Candidate], blocked: dict) -> tuple[Candidate | None, list[Candidate]]:
    """Oldest unpushed candidate whose sha is not a refused sha; also returns the skipped refusals."""
    skipped: list[Candidate] = []
    pending = sorted((c for c in cands if c.unpushed > 0), key=lambda c: (c.commit_time, c.branch))
    for cand in pending:
        marker = blocked.get(cand.key)
        if marker and marker.get("sha") == cand.sha:
            skipped.append(cand)
            continue
        return cand, skipped
    return None, skipped


# ---------------------------------------------------------------- worker

def tail_reason(stdout: str, stderr: str, rc: int) -> str:
    """Last few output lines of a failed push, joined: the hook or remote reason."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() + (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return f"git push exit {rc}"
    return " | ".join(lines[-3:])


def run_push(spec: dict) -> tuple[str, str, str]:
    """Plain `git push -u origin <branch>` (hooks run; no force, no no-verify)."""
    wt, branch = spec["wt"], spec["branch"]
    sha_now = git(wt, "rev-parse", "HEAD").stdout.strip() or spec["sha"]
    try:
        res = git(wt, "push", "-u", "origin", branch, timeout=PUSH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "BLOCKED", sha_now, f"push timed out after {PUSH_TIMEOUT_S}s"
    if res.returncode == 0:
        return "PUSHED", sha_now, "pushed to origin"
    return "BLOCKED", sha_now, tail_reason(res.stdout, res.stderr, res.returncode)


def record_outcome(spec: dict, outcome: str, sha: str, reason: str) -> None:
    """Persist the worker result before the lock is released."""
    state_dir = Path(spec["state_dir"])
    state = load_state(state_dir)
    if outcome == "PUSHED":
        state["blocked"].pop(spec["key"], None)
    elif outcome == "BLOCKED":
        state["blocked"][spec["key"]] = {"sha": sha, "reason": reason}
    emit(state, Path(spec["log"]), spec["repo"], spec["branch"], spec["key"], sha, outcome, reason)
    save_state(state_dir, state)


def push_worker(spec: dict) -> int:
    """Detached worker entry: push, record the outcome, always release the lock."""
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    lock_path = Path(spec["lock"])
    try:
        outcome, sha, reason = run_push(spec)
    except Exception as exc:  # the lock must be released and the failure logged
        outcome, sha, reason = "BLOCKED", spec["sha"], f"worker error: {exc}"
    try:
        record_outcome(spec, outcome, sha, reason)
    finally:
        lock_path.unlink(missing_ok=True)
    return EXIT_OK


def spawn_worker(spec: dict) -> None:
    """Start a detached worker that outlives this cycle."""
    cmd = [sys.executable, str(Path(__file__).resolve()), "--push-worker", json.dumps(spec)]
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


# ---------------------------------------------------------------- sweep

def report_dry_run(cands: list[Candidate], state_dir: Path) -> int:
    """Print what a live cycle would do, reading (never writing) lock and state."""
    for cand in cands:
        if cand.dirty:
            print(f"DRY-RUN dirty {cand.repo} {cand.branch} {cand.sha[:12]} dirty, not committed")
    holder = lock_holder(state_dir / LOCK_NAME)
    if holder is not None:
        print(f"DRY-RUN in-flight {holder.get('repo', '-')} {holder.get('branch', '-')} "
              f"{str(holder.get('sha', '-'))[:12]} push in flight")
        return EXIT_OK
    blocked = load_state(state_dir)["blocked"]
    target, skipped = choose_target(cands, blocked)
    for cand in skipped:
        reason = blocked.get(cand.key, {}).get("reason", "")
        print(f"DRY-RUN blocked {cand.repo} {cand.branch} {cand.sha[:12]} {reason} (sha unchanged, not retried)")
    if target is None:
        print("DRY-RUN nothing-to-push")
    queue = sorted((c for c in cands if c.unpushed > 0 and c not in skipped),
                   key=lambda c: (c.commit_time, c.branch))
    for position, cand in enumerate(queue):
        verb = "would-push" if target is not None and cand.key == target.key else "queued"
        print(f"DRY-RUN {verb} {cand.repo} {cand.branch} {cand.sha[:12]} "
              f"{cand.unpushed} commit(s) not on origin (queue position {position + 1})")
    print("DRY-RUN note: origin state is read from local refs/remotes (no fetch); nothing written")
    return EXIT_OK


def start_push(cand: Candidate, state: dict, state_dir: Path, log_path: Path, lock_path: Path) -> None:
    """Take the lock and launch the detached worker for one candidate."""
    holder = {"repo": cand.repo, "branch": cand.branch, "sha": cand.sha, "key": cand.key,
              "wt": cand.wt, "pid": os.getpid(), "started": int(time.time())}
    if not acquire_lock(lock_path, holder):
        emit_in_flight(state, log_path, lock_holder(lock_path) or {})
        return
    spec = dict(holder, lock=str(lock_path), state_dir=str(state_dir), log=str(log_path))
    save_state(state_dir, state)
    try:
        spawn_worker(spec)
    except OSError as exc:
        lock_path.unlink(missing_ok=True)
        emit(state, log_path, cand.repo, cand.branch, cand.key, cand.sha, "BLOCKED",
             f"worker launch failed: {exc}")


def emit_in_flight(state: dict, log_path: Path, holder: dict) -> None:
    """Log IN-FLIGHT for the holder of the lock (the push that is running now)."""
    emit(state, log_path, str(holder.get("repo", "-")), str(holder.get("branch", "-")),
         str(holder.get("key", "lock")), str(holder.get("sha", "-")), "IN-FLIGHT", "push in flight")


def run_live(cands: list[Candidate], state_dir: Path, log_path: Path) -> int:
    """One live cycle: DIRTY reports, IN-FLIGHT check, then at most one launched push."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / LOCK_NAME
    state = load_state(state_dir)
    for cand in cands:
        if cand.dirty:
            emit(state, log_path, cand.repo, cand.branch, cand.key, cand.sha, "DIRTY",
                 "dirty, not committed")
    reclaim_stale_lock(lock_path)
    holder = lock_holder(lock_path)
    if holder is not None:
        emit_in_flight(state, log_path, holder)
        save_state(state_dir, state)
        return EXIT_OK
    target, skipped = choose_target(cands, state["blocked"])
    for cand in skipped:
        reason = state["blocked"].get(cand.key, {}).get("reason", "")
        emit(state, log_path, cand.repo, cand.branch, cand.key, cand.sha, "BLOCKED",
             f"{reason} (sha unchanged, not retried)")
    if target is None:
        save_state(state_dir, state)
        return EXIT_OK
    start_push(target, state, state_dir, log_path, lock_path)
    return EXIT_OK


def sweep(repos: list[str], patterns, state_dir: Path, log_path: Path, dry_run: bool) -> int:
    """Scan every repo, then dry-run report or one live push decision."""
    cands: list[Candidate] = []
    for repo in repos:
        if not os.path.isdir(repo):
            print(f"peer-push: skipped {repo}: no such directory", file=sys.stderr)
            continue
        cands.extend(scan_repo(repo, patterns))
    if dry_run:
        return report_dry_run(cands, state_dir)
    return run_live(cands, state_dir, log_path)


# ---------------------------------------------------------------- config and CLI

def load_peer_config(path: Path) -> dict:
    """The peer_push object from aesop.config.json; a missing file means no overrides."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    section = data.get("peer_push", {}) if isinstance(data, dict) else {}
    if not isinstance(section, dict):
        raise ConfigError("peer_push must be an object")
    return section


def configured_repos(section: dict) -> list[str]:
    """repos: [{"path": ...}] entries, validated (fail closed on a bad entry)."""
    entries = section.get("repos")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ConfigError("peer_push.repos must be a list")
    paths = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ConfigError("each peer_push.repos entry needs a string path")
        paths.append(entry["path"])
    return paths


def configured_patterns(section: dict) -> list[str]:
    """branch_patterns: non-empty list of glob strings, or the defaults."""
    patterns = section.get("branch_patterns")
    if patterns is None:
        return list(DEFAULT_PATTERNS)
    if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) and p for p in patterns):
        raise ConfigError("peer_push.branch_patterns must be a non-empty list of strings")
    return patterns


def unique_paths(paths: list[str]) -> list[str]:
    """Expand ~, resolve, and drop duplicates while keeping order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in paths:
        resolved = os.path.normcase(os.path.realpath(os.path.expanduser(raw)))
        if resolved not in seen:
            seen.add(resolved)
            out.append(os.path.expanduser(raw))
    return out


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Push committed codex/* and agy/* worktree branches (one per cycle).")
    parser.add_argument("--aesop-root", help="aesop checkout (default: $AESOP_ROOT or this script's parent)")
    parser.add_argument("--config", help="aesop.config.json path (default: <aesop-root>/aesop.config.json)")
    parser.add_argument("--repo", action="append", default=[], help="repo to sweep (repeatable; overrides config)")
    parser.add_argument("--pattern", action="append", default=[], help="branch glob (repeatable; overrides config)")
    parser.add_argument("--state-dir", help="lock/state dir (default: $AESOP_STATE_ROOT or <aesop-root>/state)")
    parser.add_argument("--log", help="log file (default: <state-dir>/FLEET-BACKUP.log)")
    parser.add_argument("--dry-run", action="store_true", help="report what would be pushed; write nothing")
    parser.add_argument("--push-worker", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.push_worker:
        return push_worker(json.loads(args.push_worker))
    aesop_root = Path(args.aesop_root or os.environ.get("AESOP_ROOT")
                      or Path(__file__).resolve().parent.parent)
    try:
        section = load_peer_config(Path(args.config) if args.config else aesop_root / "aesop.config.json")
        repos = args.repo or configured_repos(section) or [str(aesop_root), *DEFAULT_REPOS]
        patterns = args.pattern or configured_patterns(section)
    except ConfigError as exc:
        print(f"peer-push: config error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    state_dir = Path(args.state_dir or os.environ.get("AESOP_STATE_ROOT") or aesop_root / "state")
    log_path = Path(args.log) if args.log else state_dir / LOG_NAME
    return sweep(unique_paths(repos), patterns, state_dir, log_path, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
