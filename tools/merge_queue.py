#!/usr/bin/env python3
"""Merge-queue advancer -- deterministic, stateless, ONE bounded pass per invocation.

THE ACTOR. Measured problem: green->merge dead time is ~31.75 HOURS when no
interactive session is running and 9-109 seconds when one is. Merging must not
depend on a live session, so this module is designed to run from a 5-minute
scheduled task (daemons/run-merge-queue.sh -> AesopMergeQueue).

Contract (every invocation):
  * ONE bounded pass, target < 60s wall (PASS_BUDGET_S admission budget).
  * ZERO sleep calls -- there is no polling loop anywhere in this module; the
    scheduler IS the loop. A test scans this file's own source to prove it.
  * Idempotent: a second pass over unchanged state performs no mutation and
    appends no new exception row.
  * Fail-closed everywhere: unknown/absent evidence is never treated as green.
  * NEVER: --admin, --auto, force-push, review-thread resolution, secret-scan
    tampering, or any model call. Everything here is deterministic.

Preconditions (exit 2 + exception row if any fails):
  1. `gh auth status` succeeds.
  2. branch protection enforce_admins.enabled == true on main.
  3. the required-status-check context set equals EXPECTED_REQUIRED_CHECKS.

Queue semantics:
  * Queue  = open PRs labeled `merge-queue`, PR-number ascending;
             `merge-priority` jumps the line.
  * Admission is greedy and file-disjoint (`gh pr view --json files`).
  * Batch of 1 -> singleton fast path (merge + verify state == MERGED). This is
    the common case and the whole point of the tool.
  * Batch of >1 -> build integrate/q-<epoch>, push, open a `merge-queue-batch`
    PR, and EXIT. The NEXT pass evaluates that batch.
  * Batch green -> merge it, then close members ONLY after
    `git merge-base --is-ancestor <headRefOid> origin/main` proves the content
    actually landed. Ancestor-fail is an exception row, never a close.
  * Batch red  -> re-read each member's own checks, evict individually-red
    members (queue-rejected + comment), dissolve the batch. Bounded; no bisect
    in this increment (that is Q3).

Exceptions are appended to state/merge-queue/exceptions.jsonl, one JSON object
per line with keys: ts, pr, kind, detail, run_url.

Usage:
    python tools/merge_queue.py --advance [--json] [--repo OWNER/NAME]

Exit codes: 0 = pass completed (incl. no-op / lock contention),
            1 = pass ran but an action failed,
            2 = precondition or usage failure.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

# Transport primitives are IMPORTED from merge_train, never duplicated. They
# become module globals here so tests can patch them directly. (`is_ancestor`
# is NOT imported: it does not exist on origin/main yet -- B1.3 of the
# merge-pipeline plan owns that addition -- and this daemon must not depend on
# an unlanded symbol, so the guard is defined locally over the shared `git`.)
from merge_train import gh, git  # noqa: E402
from common import get_state_dir  # noqa: E402
from generated_paths import GENERATED_PATHS  # noqa: E402

DEFAULT_REPO = "matt82198/aesop"

# The exact required-check context set today. A mismatch means branch
# protection changed underneath us; the daemon refuses to merge until a human
# reconciles this constant with reality (fail-closed by construction).
EXPECTED_REQUIRED_CHECKS = ("ci (0)", "ci (1)", "ci (2)", "ci (3)", "windows")

QUEUE_LABEL = "merge-queue"
PRIORITY_LABEL = "merge-priority"
BATCH_LABEL = "merge-queue-batch"
REJECT_LABEL = "queue-rejected"

PASS_BUDGET_S = 45.0
MAX_QUEUE = 25
LOCK_STALE_S = 600

LOCK_DIRNAME = ".merge-queue-lock"
HEARTBEAT_NAME = ".merge-queue-heartbeat"

PR_FIELDS = ("number,title,state,mergeable,mergeStateStatus,statusCheckRollup,"
             "headRefName,headRefOid,labels,body,url")

# Only these check-run conclusions count as green. SKIPPED is included because
# that is how GitHub branch protection itself resolves a skipped required job;
# excluding it would deadlock every legitimately-skipped required check.
# Everything else -- FAILURE, CANCELLED, TIMED_OUT, ACTION_REQUIRED, STALE,
# STARTUP_FAILURE, NEUTRAL, null, or an unrecognised value -- is NOT green.
#
# This is intentionally STRICTER than merge_train.GREEN_CONCLUSIONS, which also
# admits NEUTRAL. The two are deliberately not shared: this daemon merges with
# nobody watching, so its green-check owns its own definition of green and errs
# toward doing nothing. A daemon that does nothing costs one 5-minute tick; a
# daemon that merges on a wrong green costs a bad commit on main.
GREEN_CONCLUSIONS = frozenset({"SUCCESS", "SKIPPED"})

_VERDICT_RANK = {"green": 0, "pending": 1, "not_green": 2}

MEMBERS_RE = re.compile(r"^Members:\s*(.+)$", re.MULTILINE)

# The integration-branch name is minted by build_batch() and is therefore the
# ONE piece of batch state that cannot silently fail to be written. See
# list_open_batches() for why that matters.
BATCH_BRANCH_RE = re.compile(r"^integrate/q-\d+$")

# Subject line build_batch() gives every member merge, and the fallback member
# source for a batch opened before the body contract existed.
INTEGRATE_COMMIT_RE = re.compile(r"^integrate #(\d+) into ", re.MULTILINE)

BATCH_LABEL_COLOR = "5319e7"
BATCH_LABEL_DESC = "Integration batch opened by the merge-queue advancer"


# ---------------------------------------------------------------------------
# State surface
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    """Deterministic UTC timestamp for ledger rows."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_root() -> Path:
    """Resolve the state directory (AESOP_STATE_ROOT aware)."""
    return get_state_dir()


def exceptions_path() -> Path:
    """Append-only exception ledger path."""
    return state_root() / "merge-queue" / "exceptions.jsonl"


def read_exceptions() -> list:
    """Read every exception row. Missing/corrupt lines are skipped, not fatal."""
    path = exceptions_path()
    rows = []
    if not path.exists():
        return rows
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return rows


def record_exception(pr, kind: str, detail: str, run_url: str = "") -> dict:
    """Append one exception row; dedupe on (pr, kind, detail) for idempotence.

    Row schema (exactly these five keys, in this order):
        {"ts": ISO8601Z, "pr": int, "kind": str, "detail": str, "run_url": str}

    A repeated condition (same PR, same kind, same detail) never appends twice,
    so a scheduled task re-observing an unchanged state stays a true no-op.
    """
    row = {
        "ts": utc_now_iso(),
        "pr": int(pr) if pr is not None else 0,
        "kind": str(kind),
        "detail": str(detail)[:500],
        "run_url": str(run_url or ""),
    }
    for existing in read_exceptions():
        if (existing.get("pr") == row["pr"]
                and existing.get("kind") == row["kind"]
                and existing.get("detail") == row["detail"]):
            return row
    path = exceptions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=False) + "\n")
    return row


def is_ancestor(head_oid: str) -> bool:
    """True only if head_oid provably landed on origin/main.

    This is the close guard: `git merge-base --is-ancestor` exits 0 for an
    ancestor and non-zero otherwise, so any git failure reads as NOT landed.
    """
    if not head_oid:
        return False
    ok, _ = git("merge-base", "--is-ancestor", head_oid, "origin/main")
    return bool(ok)


def beat_heartbeat(path: Path = None) -> Path:
    """Write the epoch-seconds heartbeat for this pass."""
    path = path or (state_root() / HEARTBEAT_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(int(time.time())), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Single-instance lock (fail-closed on contention, mkdir-atomic)
# ---------------------------------------------------------------------------

def acquire_lock(lock_dir: Path = None, stale_s: int = LOCK_STALE_S) -> bool:
    """Atomically claim the advancer lock. False means another pass holds it.

    Contention is NOT an error: the scheduler fires again in 5 minutes. Only a
    demonstrably stale lock (older than stale_s) is reclaimed, so a crashed
    holder cannot wedge the queue forever while a live one is never stolen from.
    """
    lock_dir = Path(lock_dir) if lock_dir else (state_root() / LOCK_DIRNAME)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(lock_dir)
    except FileExistsError:
        age = None
        ts_file = lock_dir / "timestamp"
        try:
            age = int(time.time()) - int(ts_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            age = None
        # Unreadable/absent timestamp is treated as stale ONLY once the
        # directory mtime is itself older than the threshold.
        if age is None:
            try:
                age = int(time.time() - lock_dir.stat().st_mtime)
            except OSError:
                return False
        if age < stale_s:
            return False
        shutil.rmtree(lock_dir, ignore_errors=True)
        try:
            os.mkdir(lock_dir)
        except OSError:
            return False
    except OSError:
        return False
    (lock_dir / "timestamp").write_text(str(int(time.time())), encoding="utf-8")
    (lock_dir / "pid").write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock(lock_dir: Path = None) -> None:
    """Release the lock, but only if this process owns it."""
    lock_dir = Path(lock_dir) if lock_dir else (state_root() / LOCK_DIRNAME)
    try:
        owner = (lock_dir / "pid").read_text(encoding="utf-8").strip()
    except OSError:
        return
    if owner == str(os.getpid()):
        shutil.rmtree(lock_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------

def _errored(result) -> bool:
    return isinstance(result, dict) and "error" in result


def check_gh_auth() -> tuple:
    """gh must be authenticated. Any non-zero exit fails closed."""
    result = gh("auth", "status")
    if _errored(result):
        return False, "gh auth status failed: %s" % str(result.get("error", ""))[:160]
    return True, "gh authenticated"


def check_enforce_admins(repo: str = DEFAULT_REPO) -> tuple:
    """enforce_admins must be asserted; without it a merge could bypass gates."""
    result = gh("api", "repos/%s/branches/main/protection" % repo,
                "--jq", ".enforce_admins.enabled")
    if _errored(result):
        return False, "cannot read branch protection: %s" % str(result.get("error", ""))[:160]
    if result is True or (isinstance(result, str) and result.strip().lower() == "true"):
        return True, "enforce_admins asserted"
    return False, "enforce_admins is not enabled (got: %r)" % (result,)


def check_required_contexts(repo: str = DEFAULT_REPO) -> tuple:
    """The required-check context set must equal EXPECTED_REQUIRED_CHECKS."""
    result = gh("api", "repos/%s/branches/main/protection" % repo,
                "--jq", ".required_status_checks.contexts")
    if _errored(result):
        return False, "cannot read required checks: %s" % str(result.get("error", ""))[:160]
    if not isinstance(result, list):
        return False, "required-check contexts not a list (got: %r)" % (result,)
    actual = set(result)
    expected = set(EXPECTED_REQUIRED_CHECKS)
    if actual != expected:
        return False, ("required-check set drift: expected %s, got %s"
                       % (sorted(expected), sorted(actual)))
    return True, "required-check set matches"


def preconditions(repo: str = DEFAULT_REPO) -> tuple:
    """Run every precondition in order; first failure short-circuits."""
    for probe in (check_gh_auth,
                  lambda: check_enforce_admins(repo),
                  lambda: check_required_contexts(repo)):
        ok, detail = probe()
        if not ok:
            return False, detail
    return True, "preconditions satisfied"


# ---------------------------------------------------------------------------
# Fail-closed check bucketing (independent of merge_train.pr_state)
# ---------------------------------------------------------------------------

def classify_check(entry: dict) -> tuple:
    """Bucket ONE statusCheckRollup entry -> (name, verdict, url).

    verdict is one of 'green' / 'pending' / 'not_green'. This is deliberately
    NOT merge_train.pr_state's bucketing: CANCELLED, TIMED_OUT, ACTION_REQUIRED,
    NEUTRAL, a null conclusion and any unrecognised shape all bucket to
    'not_green' here, regardless of what any other tool does with them.
    """
    if not isinstance(entry, dict):
        return "", "not_green", ""
    name = entry.get("name") or entry.get("context") or ""
    url = (entry.get("detailsUrl") or entry.get("targetUrl")
           or entry.get("url") or "")
    status = str(entry.get("status") or "").upper()
    conclusion = str(entry.get("conclusion") or "").upper()
    state = str(entry.get("state") or "").upper()

    if status:
        if status != "COMPLETED":
            return name, "pending", url
        return name, ("green" if conclusion in GREEN_CONCLUSIONS else "not_green"), url
    if state:
        if state in ("PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS"):
            return name, "pending", url
        return name, ("green" if state == "SUCCESS" else "not_green"), url
    # Unknown shape: no status, no state. Fail closed.
    return name, "not_green", url


def required_checks_green(rollup, expected=EXPECTED_REQUIRED_CHECKS) -> tuple:
    """Verdict over the REQUIRED context set -> (verdict, detail, run_url).

    A required context absent from the rollup is 'not_green', never 'pending'
    and never green -- an empty or partial rollup must never read as success.
    Duplicate names collapse to their worst verdict.
    """
    seen = {}
    for entry in (rollup or []):
        name, verdict, url = classify_check(entry)
        if not name:
            continue
        prior = seen.get(name)
        if prior is None or _VERDICT_RANK[verdict] > _VERDICT_RANK[prior[0]]:
            seen[name] = (verdict, url)

    missing = [c for c in expected if c not in seen]
    if missing:
        return ("not_green",
                "required check(s) absent from rollup: %s" % ", ".join(missing),
                "")
    for context in expected:
        verdict, url = seen[context]
        if verdict == "not_green":
            return "not_green", "%s is not green" % context, url
    for context in expected:
        verdict, url = seen[context]
        if verdict == "pending":
            return "pending", "%s is pending" % context, url
    return "green", "all required checks green", ""


# ---------------------------------------------------------------------------
# GitHub reads
# ---------------------------------------------------------------------------

def label_names(pr: dict) -> set:
    """Label-name set for a PR payload."""
    return {(lbl or {}).get("name", "") for lbl in (pr.get("labels") or [])}


def pr_view(number: int, fields: str = PR_FIELDS):
    """Read one PR. Returns None on any gh failure (caller fails closed)."""
    raw = gh("pr", "view", str(number), "--json", fields)
    if _errored(raw) or not isinstance(raw, dict):
        return None
    return raw


def pr_files(number: int) -> list:
    """Changed-file paths for a PR. Empty list means 'unknown' -> never admit."""
    raw = gh("pr", "view", str(number), "--json", "files")
    if _errored(raw) or not isinstance(raw, dict):
        return []
    return [f.get("path", "") for f in (raw.get("files") or []) if f.get("path")]


def list_queue(label: str) -> list:
    """Open PRs carrying `label`, as returned by gh (order not yet normalised)."""
    raw = gh("pr", "list", "--state", "open", "--label", label,
             "--limit", str(MAX_QUEUE * 2),
             "--json", "number,title,labels,body,headRefName")
    if _errored(raw) or not isinstance(raw, list):
        return []
    return raw


def order_queue(prs: list) -> list:
    """PR-number ascending; `merge-priority` jumps the line."""
    def sort_key(pr):
        priority = 0 if PRIORITY_LABEL in label_names(pr) else 1
        return (priority, pr.get("number", 0))
    return sorted(prs, key=sort_key)


def partition_disjoint(entries: list) -> list:
    """Greedy file-disjoint admission over (pr_number, paths) in queue order.

    A PR whose file set intersects an already-admitted PR waits for a later
    pass. A PR with an unknown (empty) file set is never admitted.
    """
    admitted = []
    claimed = set()
    for number, paths in entries:
        paths = set(p for p in (paths or []) if p)
        if not paths:
            continue
        if paths & claimed:
            continue
        claimed |= paths
        admitted.append(number)
    return admitted


def list_open_prs() -> list:
    """Every open PR, unfiltered. Used for label-independent batch discovery."""
    raw = gh("pr", "list", "--state", "open", "--limit", str(MAX_QUEUE * 4),
             "--json", "number,title,labels,body,headRefName")
    if _errored(raw) or not isinstance(raw, list):
        return []
    return raw


def is_batch_branch(head_ref: str) -> bool:
    """True for a branch this module minted for an integration batch."""
    return bool(BATCH_BRANCH_RE.match(str(head_ref or "").strip()))


def list_open_batches() -> list:
    """Every OPEN batch PR, found by label OR by integration-branch name.

    Discovery must not rest on the label alone. Measured 2026-08-02: the
    `merge-queue-batch` label did not exist in the repository, so the
    `gh pr edit --add-label` at batch creation failed silently and
    `gh pr list --label merge-queue-batch` answered `[]` with EXIT 0 on every
    later pass. Two passes therefore each opened a fresh batch (#727, #728) over
    the same seven members. `gh` reporting "no such label" as an empty result
    rather than an error makes the label a read that can be quietly wrong.

    The branch name cannot be wrong the same way: build_batch() creates
    `integrate/q-<epoch>` and pushes it BEFORE the PR exists, so an open PR on
    such a branch IS a batch whatever its labels say. The label stays as the
    cheap path and a human-visible marker; the branch is the guarantee.

    Deduplicated on PR number, label-sourced rows first.
    """
    found = {}
    for pr in list_queue(BATCH_LABEL):
        number = pr.get("number")
        if number:
            found[number] = pr
    for pr in list_open_prs():
        number = pr.get("number")
        if number and number not in found and is_batch_branch(pr.get("headRefName", "")):
            found[number] = pr
    return list(found.values())


def parse_members(body: str) -> list:
    """Extract batch member PR numbers from the batch PR body 'Members:' line."""
    if not body:
        return []
    match = MEMBERS_RE.search(body)
    if not match:
        return []
    return [int(n) for n in re.findall(r"#(\d+)", match.group(1))]


def remote_branch_exists(branch: str) -> bool:
    """True only if `branch` provably still exists on origin. Fail-closed."""
    if not branch:
        return False
    ok, out = git("ls-remote", "--heads", "origin", branch)
    return bool(ok and (out or "").strip())


def members_from_branch(branch: str) -> list:
    """Member numbers read off the batch branch's own merge commits.

    The fallback for a batch opened before the body 'Members:' contract, and the
    reason batch construction writes that subject line at all. Order-preserving
    and deduplicated; an unreadable branch yields [] and the caller fails closed.
    """
    if not branch:
        return []
    git("fetch", "origin", branch)
    ok, out = git("log", "--pretty=%s", "origin/main..origin/%s" % branch)
    if not ok:
        return []
    members, seen = [], set()
    for raw in INTEGRATE_COMMIT_RE.findall(out or ""):
        number = int(raw)
        if number not in seen:
            seen.add(number)
            members.append(number)
    return members


def resolve_batch_members(batch: dict) -> list:
    """Member set of one batch PR: body first, branch commits as the fallback.

    A parseable body costs no git call at all, which keeps the common path pure
    API. Only a pre-contract batch pays for the branch read.
    """
    members = parse_members((batch or {}).get("body") or "")
    if members:
        return members
    return members_from_branch((batch or {}).get("headRefName", ""))


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def merge_and_verify(number: int, method: str = "--merge") -> tuple:
    """Merge a PR and PROVE it: exit 0 is not merged, state == MERGED is.

    Never --admin, never --auto. If the verify read says anything other than
    MERGED the call is a failure, full stop.
    """
    result = gh("pr", "merge", str(number), method)
    if _errored(result):
        err = str(result.get("error", ""))
        if "already merged" not in err.lower():
            return False, "merge failed: %s" % err[:200]
    verify = gh("pr", "view", str(number), "--json", "state", "--jq", ".state")
    if verify == "MERGED":
        return True, "MERGED (verified)"
    return False, "merge returned but state=%r -- NOT MERGED" % (verify,)


def evict_member(number: int, detail: str, run_url: str = "") -> None:
    """Kick a PR out of the queue: queue-rejected label + explanatory comment."""
    gh("pr", "edit", str(number), "--add-label", REJECT_LABEL,
       "--remove-label", QUEUE_LABEL)
    body = "Evicted from the merge queue: %s" % detail
    if run_url:
        body += "\n\nFailing run: %s" % run_url
    gh("pr", "comment", str(number), "--body", body)


# ---------------------------------------------------------------------------
# Singleton fast path -- THE COMMON CASE
# ---------------------------------------------------------------------------

def advance_singleton(number: int, summary: dict) -> bool:
    """Merge one PR when, and only when, today's exact condition holds."""
    info = pr_view(number)
    if info is None:
        record_exception(number, "pr_read_failed", "gh pr view returned no payload")
        return False
    if info.get("state") != "OPEN":
        return False

    verdict, detail, run_url = required_checks_green(info.get("statusCheckRollup"))
    if verdict == "pending":
        summary["actions"].append("#%d: checks pending" % number)
        return False
    if verdict == "not_green":
        # Ordinary red CI is not an exception -- Q3's bisect owns that lane.
        summary["actions"].append("#%d: not green (%s)" % (number, detail))
        return False

    mergeable = info.get("mergeable")
    merge_state = info.get("mergeStateStatus")
    if mergeable == "CONFLICTING" or merge_state == "DIRTY":
        record_exception(number, "conflict",
                         "mergeable=%s mergeStateStatus=%s" % (mergeable, merge_state))
        return False
    if merge_state == "BLOCKED":
        # Required checks are green, so the block is a human gate (unresolved
        # review conversation / missing approval). Never auto-resolve it.
        record_exception(number, "conversation_blocked",
                         "required checks green but mergeStateStatus=BLOCKED")
        return False
    if mergeable != "MERGEABLE" or merge_state != "CLEAN":
        summary["actions"].append(
            "#%d: not ready (mergeable=%s mergeStateStatus=%s)"
            % (number, mergeable, merge_state))
        return False

    ok, detail = merge_and_verify(number)
    if ok:
        summary["merged"].append(number)
        summary["actions"].append("#%d: %s" % (number, detail))
        return True
    record_exception(number, "merge_verify_failed", detail)
    summary["status"] = "error"
    return False


# ---------------------------------------------------------------------------
# Batch construction (>1 admitted) -- build, push, open PR, EXIT
# ---------------------------------------------------------------------------

def dirty_paths(porcelain: str) -> list:
    """Repo-relative paths from `git status --porcelain` output.

    Handles the three shapes that matter: ' M path', '?? path', and a rename
    'R  old -> new' (the destination is what is dirty). Quoted paths -- git
    quotes names containing spaces -- are unwrapped.
    """
    paths = []
    for line in (porcelain or "").splitlines():
        entry = line[3:] if len(line) > 3 else ""
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip()
        if len(entry) >= 2 and entry[0] == '"' and entry[-1] == '"':
            entry = entry[1:-1]
        if entry:
            paths.append(entry.replace("\\", "/"))
    return paths


def restore_generated_paths(paths) -> list:
    """`git restore` the named generated files, one path at a time.

    Deliberately NOT `git stash` (the stash stack is shared across worktrees, so
    stashing here would silently swallow another lane's work in progress) and
    deliberately NOT a blanket `git checkout .`. Only paths the caller has
    already proven to be in the GENERATED_PATHS registry ever reach this.
    """
    restored = []
    for path in paths:
        ok, _ = git("restore", "--", path)
        if ok:
            restored.append(path)
    return restored


def worktree_is_safe() -> tuple:
    """May this process build an integration branch in the working tree?

    The singleton fast path is pure GitHub API and never touches the working
    tree. Batch construction does (`git checkout -B`), and this daemon runs
    unattended in a tree a human may also be using -- so it refuses unless the
    tree is BOTH on main AND clean. Otherwise a 5-minute timer could silently
    yank someone's checked-out branch out from under them, or build a batch on
    top of an unrelated base. Fail-closed: unknown state = unsafe.

    The branch is checked FIRST so that a tree someone else has checked out is
    rejected before this function would modify anything in it.

    Generated-file tolerance: `tools/verify_test_suite_count.py --check`
    auto-corrects the count lines in tests/CLAUDE.md and WRITES the file, so a
    gate run in the scheduled task's project root left the tree dirty and
    stalled the next pass over output the repo itself generates. Such paths are
    restored by name -- but only when EVERY dirty path is a registered generated
    file. One unregistered edit poisons the whole tree and nothing is touched.
    """
    ok, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if not ok:
        return False, "cannot read current branch"
    branch = branch.strip()
    if branch != "main":
        return False, "working tree is on '%s', not main" % branch

    ok, out = git("status", "--porcelain")
    if not ok:
        return False, "cannot read git status"
    if not out.strip():
        return True, "working tree is clean and on main"

    dirty = dirty_paths(out)
    if not dirty or any(path not in GENERATED_PATHS for path in dirty):
        return False, "working tree is dirty"

    restored = restore_generated_paths(dirty)
    ok, out = git("status", "--porcelain")
    if not ok:
        return False, "cannot read git status"
    if out.strip():
        # Restore did not clean it (e.g. the path was untracked, which
        # `git restore` cannot undo). Refuse exactly as before.
        return False, "working tree is dirty"
    return True, ("working tree is on main; restored generated file(s): %s"
                  % ", ".join(restored))


def pr_has_label(number: int, label: str) -> bool:
    """Read back a PR's labels. Any read failure answers False (fail-closed)."""
    info = pr_view(number, "labels")
    if info is None:
        return False
    return label in label_names(info)


def apply_batch_label(number: int, summary: dict) -> bool:
    """Label the batch PR and PROVE it stuck; create the label if it is missing.

    `gh pr edit --add-label` against a label the repository does not define
    fails, and build_batch used to ignore the result -- which is exactly how
    #727 ended up with an empty label set and became invisible to every
    subsequent pass. So: apply, read back, and if it did not stick create the
    label once and retry. A still-unlabelled batch is an exception row, never a
    silent success. (Discovery no longer DEPENDS on the label -- see
    list_open_batches -- but a batch a human cannot see in the label filter is
    still a defect worth surfacing.)
    """
    gh("pr", "edit", str(number), "--add-label", BATCH_LABEL)
    if pr_has_label(number, BATCH_LABEL):
        return True

    gh("label", "create", BATCH_LABEL,
       "--description", BATCH_LABEL_DESC, "--color", BATCH_LABEL_COLOR)
    gh("pr", "edit", str(number), "--add-label", BATCH_LABEL)
    if pr_has_label(number, BATCH_LABEL):
        summary["actions"].append("created missing '%s' label" % BATCH_LABEL)
        return True

    record_exception(number, "batch_label_failed",
                     "could not apply '%s'; the batch is discoverable by its "
                     "integrate/q-* branch but not by label filter" % BATCH_LABEL)
    return False


def build_batch(members: list, summary: dict, epoch: int = None) -> str:
    """Build integrate/q-<epoch> from origin/main and open the batch PR.

    A member that conflicts is dropped (exception row) and the rest continue.
    Returns the batch branch name, or "" if no batch was opened.
    """
    safe, why = worktree_is_safe()
    if not safe:
        record_exception(0, "unsafe_worktree",
                         "cannot build an integration branch: %s" % why)
        summary["status"] = "error"
        return ""

    epoch = int(epoch if epoch is not None else time.time())
    branch = "integrate/q-%d" % epoch

    ok, out = git("fetch", "origin", "main")
    if not ok:
        record_exception(0, "git_failed", "fetch origin main: %s" % out[:200])
        summary["status"] = "error"
        return ""
    ok, out = git("checkout", "-B", branch, "origin/main")
    if not ok:
        record_exception(0, "git_failed", "checkout %s: %s" % (branch, out[:200]))
        summary["status"] = "error"
        return ""

    included = []
    for number in members:
        info = pr_view(number, "headRefOid,headRefName,title")
        if info is None or not info.get("headRefOid"):
            record_exception(number, "pr_read_failed",
                             "no headRefOid while building %s" % branch)
            continue
        sha = info["headRefOid"]
        head_ref = info.get("headRefName", "")
        if head_ref:
            git("fetch", "origin", head_ref)
        else:
            git("fetch", "origin", sha)
        ok, out = git("merge", sha, "--no-edit",
                      "-m", "integrate #%d into %s" % (number, branch))
        if not ok:
            git("merge", "--abort")
            record_exception(number, "member_conflict",
                             "conflicts against %s: %s" % (branch, out[:200]))
            continue
        included.append(number)

    if len(included) < 2:
        # Nothing worth batching; drop the branch and let the next pass take
        # the survivor through the singleton fast path.
        git("checkout", "main")
        git("branch", "-D", branch)
        summary["actions"].append(
            "batch %s abandoned (%d clean member(s))" % (branch, len(included)))
        return ""

    ok, out = git("push", "-u", "origin", branch)
    if not ok:
        git("checkout", "main")
        git("branch", "-D", branch)
        record_exception(0, "git_failed", "push %s: %s" % (branch, out[:200]))
        summary["status"] = "error"
        return ""

    body = ("Merge-queue batch built by tools/merge_queue.py.\n\n"
            "Members: %s\n\nMembers are closed only after "
            "`git merge-base --is-ancestor` proves their content landed on main."
            % ", ".join("#%d" % n for n in included))
    created = gh("pr", "create", "--base", "main", "--head", branch,
                 "--title", "merge-queue batch q-%d" % epoch, "--body", body)
    if _errored(created):
        record_exception(0, "batch_pr_create_failed",
                         str(created.get("error", ""))[:200])
        summary["status"] = "error"
        return ""

    number = _pr_number_from_url(created if isinstance(created, str) else "")
    if number:
        apply_batch_label(number, summary)
    summary["batch"] = {"branch": branch, "pr": number, "members": included}
    summary["actions"].append(
        "opened batch %s with %s" % (branch, ", ".join("#%d" % n for n in included)))
    git("checkout", "main")
    return branch


def _pr_number_from_url(url: str):
    """Last numeric path segment of a PR URL."""
    number = None
    for part in (url or "").rstrip("/").split("/"):
        if part.isdigit():
            number = int(part)
    return number


# ---------------------------------------------------------------------------
# Batch evaluation on a later pass
# ---------------------------------------------------------------------------

def close_landed_members(members: list, batch_label: str, summary: dict) -> None:
    """Close members ONLY when their content provably landed on origin/main."""
    git("fetch", "origin", "main")
    for number in members:
        info = pr_view(number, "headRefOid,state")
        if info is None or not info.get("headRefOid"):
            record_exception(number, "pr_read_failed",
                             "no headRefOid while closing for %s" % batch_label)
            continue
        if info.get("state") != "OPEN":
            continue
        if not is_ancestor(info["headRefOid"]):
            record_exception(number, "ancestor_check_failed",
                             "headRefOid is not an ancestor of origin/main after "
                             "%s merged; refusing to close" % batch_label)
            summary["status"] = "error"
            continue
        gh("pr", "close", str(number), "--comment", "merged via %s" % batch_label)
        summary["actions"].append("#%d closed (merged via %s)" % (number, batch_label))


def dissolve_batch(batch_number: int, branch: str, reason: str) -> None:
    """Close the batch PR and delete its remote branch. No force, no rewrite."""
    gh("pr", "close", str(batch_number), "--comment",
       "Dissolving merge-queue batch: %s" % reason)
    if branch:
        git("push", "origin", "--delete", branch)


def handle_batch_pr(batch: dict, summary: dict) -> None:
    """Evaluate one in-flight batch PR: merge it, or dissolve it. Bounded."""
    number = batch.get("number")
    info = pr_view(number)
    if info is None:
        record_exception(number, "pr_read_failed", "batch PR unreadable")
        summary["status"] = "error"
        return

    members = resolve_batch_members(info)
    branch = info.get("headRefName", "")
    label = "#%d (%s)" % (number, branch or "batch")
    if not members:
        # Neither the body nor the branch commits name members. Distinguish a
        # STALE batch (branch already deleted -- nothing to merge or dissolve,
        # just report it) from a live batch whose provenance is unreadable.
        # Either way this pass merges nothing and rebatches nothing: returning
        # here leaves the batch open, so _advance's queue stays suppressed.
        if branch and not remote_branch_exists(branch):
            record_exception(number, "batch_branch_missing",
                             "batch %s has no branch on origin; stale batch PR "
                             "left open for a human" % label)
        else:
            record_exception(number, "batch_members_unparseable",
                             "batch PR body has no parseable 'Members:' line")
        summary["status"] = "error"
        return

    verdict, detail, run_url = required_checks_green(info.get("statusCheckRollup"))
    if verdict == "pending":
        summary["actions"].append("batch %s: checks pending" % label)
        return

    if verdict == "green":
        merge_state = info.get("mergeStateStatus")
        if merge_state == "BLOCKED":
            record_exception(number, "conversation_blocked",
                             "batch %s green but mergeStateStatus=BLOCKED" % label)
            return
        ok, merge_detail = merge_and_verify(number)
        if not ok:
            record_exception(number, "merge_verify_failed", merge_detail)
            summary["status"] = "error"
            return
        summary["merged"].append(number)
        summary["actions"].append("batch %s %s" % (label, merge_detail))
        close_landed_members(members, label, summary)
        if branch:
            git("push", "origin", "--delete", branch)
        return

    # Red batch. Re-read every member's OWN checks, evict the individually-red
    # ones, and dissolve the batch either way. No bisect in this increment.
    red_members = []
    for member in members:
        member_info = pr_view(member)
        if member_info is None:
            record_exception(member, "pr_read_failed",
                             "unreadable during red-batch triage of %s" % label)
            continue
        m_verdict, m_detail, m_url = required_checks_green(
            member_info.get("statusCheckRollup"))
        if m_verdict == "not_green":
            red_members.append(member)
            record_exception(member, "member_red",
                             "individually red in %s: %s" % (label, m_detail), m_url)
            evict_member(member, m_detail, m_url)

    if not red_members:
        for member in members:
            record_exception(member, "batch_red_dissolved",
                             "all members individually green but %s was red: %s"
                             % (label, detail), run_url)
            evict_member(member, "batch %s red with every member individually "
                                 "green" % label, run_url)

    dissolve_batch(number, branch, detail)
    summary["actions"].append("batch %s dissolved (%s)" % (label, detail))
    summary["status"] = "error"


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------

def _advance(summary: dict, started: float) -> None:
    """One bounded advance. Batches first; a queue never races an in-flight batch.

    Every open batch's members are resolved BEFORE any queue work so that a PR
    already folded into an in-flight batch can never be batched a second time.
    While any batch is open the pass evaluates it and stops -- that early return
    is the primary guard; the member exclusion below is the backstop for a
    batch that is open but not the one being evaluated.
    """
    batches = order_queue(list_open_batches())
    batched_members = set()
    for batch in batches:
        batched_members.update(resolve_batch_members(batch))
    summary["batched_members"] = sorted(batched_members)

    if batches:
        handle_batch_pr(batches[0], summary)
        return

    queue = [pr for pr in order_queue(list_queue(QUEUE_LABEL))
             if BATCH_LABEL not in label_names(pr)
             and pr.get("number") not in batched_members
             and not is_batch_branch(pr.get("headRefName", ""))][:MAX_QUEUE]
    if not queue:
        summary["actions"].append("queue empty")
        return

    entries = []
    for pr in queue:
        if time.monotonic() - started > PASS_BUDGET_S:
            summary["actions"].append("admission budget reached; deferring the tail")
            break
        entries.append((pr["number"], pr_files(pr["number"])))

    admitted = partition_disjoint(entries)
    summary["admitted"] = admitted
    if not admitted:
        summary["actions"].append("no file-disjoint PR admissible this pass")
        return

    if len(admitted) == 1:
        advance_singleton(admitted[0], summary)
    else:
        build_batch(admitted, summary)


def run_pass(repo: str = DEFAULT_REPO) -> tuple:
    """Execute ONE pass. Returns (exit_code, summary)."""
    started = time.monotonic()
    summary = {
        "ts": utc_now_iso(),
        "repo": repo,
        "status": "ok",
        "actions": [],
        "merged": [],
        "admitted": [],
        "batched_members": [],
        "batch": None,
    }

    ok, detail = preconditions(repo)
    if not ok:
        record_exception(0, "precondition_failed", detail)
        summary["status"] = "precondition_failed"
        summary["detail"] = detail
        return 2, summary

    lock_dir = state_root() / LOCK_DIRNAME
    if not acquire_lock(lock_dir):
        summary["status"] = "lock_contention"
        summary["actions"].append("another advancer pass holds the lock")
        return 0, summary

    try:
        beat_heartbeat()
        _advance(summary, started)
    finally:
        release_lock(lock_dir)

    summary["elapsed_s"] = round(time.monotonic() - started, 2)
    return (1 if summary["status"] == "error" else 0), summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge-queue advancer: one bounded, zero-sleep, idempotent pass")
    parser.add_argument("--advance", action="store_true",
                        help="Run one advance pass (required action)")
    parser.add_argument("--repo", default=DEFAULT_REPO,
                        help="owner/name of the repository (default: %s)" % DEFAULT_REPO)
    parser.add_argument("--json", action="store_true",
                        help="Emit the pass summary as JSON")
    args = parser.parse_args(argv)

    if not args.advance:
        parser.print_help()
        return 2

    code, summary = run_pass(repo=args.repo)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=False))
    else:
        print("merge-queue pass: %s" % summary["status"])
        for action in summary["actions"]:
            print("  - %s" % action)
        if summary.get("detail"):
            print("  detail: %s" % summary["detail"])
        if summary["merged"]:
            print("  merged: %s" % ", ".join("#%d" % n for n in summary["merged"]))
    return code


if __name__ == "__main__":
    sys.exit(main())
