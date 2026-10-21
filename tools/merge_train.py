#!/usr/bin/env python3
"""Merge train -- integration-batch (default) or serial merge for GitHub PRs.
INDEX: Merge train (debottleneck B1): integration mode is the DEFAULT — batches PRs into an `integrate/<batch-name>` branch, runs CI once, squash-merges, closes superseded PRs with ancestor verification (B1.3 correctness guard blocks unlanded content). B1.1: auto-named batches, --dry-run trials, --serial escape hatch. B1.2: stacked PR chains (baseRefName graph). B1.4: enforce_admins runtime check. B1.5: no update_branch in integration (FLAG 1 APPROVED); serial --rebase-behind flag. B1.6: regeneration hook stub (A1/A2/A3 generators). Serial mode (legacy `--serial`) processes PRs one-at-a-time; on --rebase-behind a BEHIND PR is refused (server-side branch update banned, merge main locally). Check classification is fail-closed via the `GREEN_CONCLUSIONS` allow-list (`SUCCESS`/`NEUTRAL`/`SKIPPED` only) and `check_outcome()`, which reads `conclusion` on CheckRun entries and `state` on legacy StatusContext entries — CANCELLED/TIMED_OUT/ACTION_REQUIRED/unknown are never green. Keep it an allow-list: a deny-list lets COMPLETED-but-not-FAILURE outcomes fall through to a merge. All modes: MERGED state verification mandatory; no --admin/--auto emitted

Integration mode (DEFAULT): batch PRs into a single integration branch,
test once, merge to main, close superseded PRs; handles stacked PRs and chains.
Serial mode (--serial): wait for CI, merge one at a time (legacy). BEHIND PRs are refused, never updated server-side.

Usage:
    python tools/merge_train.py 492 493 494          # integration batch mode (default)
    python tools/merge_train.py --dry-run 492 493    # trial merge, print partition, no push
    python tools/merge_train.py --serial 492 493 494 # legacy serial mode
    python tools/merge_train.py --file pr-list.txt   # one number per line
    python tools/merge_train.py --integration my-batch 492 493  # named batch
"""
import argparse
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Worktree ownership marker (create_integration_branch guard). Loaded by path so this
# module works from any cwd; an import failure aborts the module (fail closed).
_wt_spec = importlib.util.spec_from_file_location(
    "worktree_marker", Path(__file__).resolve().parent / "worktree_marker.py")
_wt_marker = importlib.util.module_from_spec(_wt_spec)
_wt_spec.loader.exec_module(_wt_marker)
read_worktree_marker = _wt_marker.read_marker
MarkerError = _wt_marker.MarkerError

# Ensure stdout can encode UTF-8 (fixes Windows cp1252 UnicodeEncodeError on PR titles
# containing characters like U+FEFF).
#
# Do NOT wrap sys.stdout in a TextIOWrapper at import time: the wrapper captures the
# CURRENT sys.stdout.buffer, and a test runner that swaps or closes stdout between tests
# leaves the wrapper writing to a dead file object -- raising ValueError: I/O operation
# on closed file from INSIDE the wrapper, before any print-level guard can catch it.
# reconfigure() mutates the live stream instead of capturing it, and is a no-op when the
# stream does not support it.
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (ValueError, AttributeError, OSError):
    pass

# Custom print function that handles closed stdout gracefully (for test environments)
_builtin_print = print
def print(*args, **kwargs):
    """Print with flush=True by default, handles closed stdout gracefully."""
    kwargs.setdefault('flush', True)
    try:
        _builtin_print(*args, **kwargs)
    except (ValueError, OSError) as e:
        # Handle case where stdout is closed (test environment)
        if "closed file" in str(e) or "I/O operation" in str(e):
            pass  # Silently skip if stdout is closed
        else:
            raise

# Import halt.py with FAIL CLOSED on import failure
try:
    from halt import is_halted, get_halt_info, active_halt
except ImportError:
    try:
        # Fallback for when called from tools/ directory or via importlib
        # Try to locate halt.py relative to this file's directory
        this_dir = Path(__file__).parent
        halt_py = this_dir / "halt.py"
        if halt_py.exists():
            spec = importlib.util.spec_from_file_location("halt", halt_py)
            if spec and spec.loader:
                halt_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(halt_module)
                is_halted = halt_module.is_halted
                get_halt_info = halt_module.get_halt_info
                active_halt = halt_module.active_halt
            else:
                raise ImportError("Could not load halt.py spec")
        else:
            raise ImportError("halt.py not found")
    except ImportError:
        print(f"[FATAL] halt.py not found or unimportable -- merge_train refuses to run", file=sys.stderr)
        sys.exit(2)

# Transient error patterns from gh run rerun that indicate we should keep the PR queued
# without consuming a retry (TOCTOU races, workflow state changes, etc.)
RETRIABLE_RERUN_ERRORS = [
    "already running",
    "workflow is already running",
    "run not found",
    "could not find run",
    "workflow completed",
]

# The ONLY check outcomes that count as green. This is an allow-list, deliberately:
# a CANCELLED / TIMED_OUT / ACTION_REQUIRED / STALE / STARTUP_FAILURE check is
# "COMPLETED" and is not "FAILURE", so a deny-list lets it fall through to a merge.
# Recorded lesson: fail-closed on CANCELLED/unknown states. tools/auto_merge.py
# buckets the same way (bucket == 'cancel' is red there).
GREEN_CONCLUSIONS = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})


def halt_status():
    """Return (halted, info) across BOTH kill-switch sentinels.

    Consults the checkout-scoped .HALT and the box-wide .HALT that `halt.py set`
    writes (active_halt honours halt.py's AESOP_STATE_ROOT isolation rule). Fails
    closed: an error reading either reader reports HALTED, with the error as the
    reason, never a clear state.
    """
    try:
        local_halted = is_halted()
        active = active_halt()
        if active is not None:
            return True, active[1] or {}
        if local_halted:
            return True, get_halt_info() or {}
    except Exception as exc:  # any reader failure must halt, never clear
        return True, {"reason": f"halt state unreadable ({type(exc).__name__}: {exc})",
                      "timestamp": ""}
    return False, None


def _check_halt(context=""):
    """Check if halt is set. If so, print reason and exit with code 1.

    Args:
        context: Description of when this check occurs (for log clarity)

    Returns:
        None if not halted; otherwise exits with code 1.
    """
    halted, info = halt_status()
    if halted:
        reason = info.get("reason", "(unknown)") if info else "(unknown)"
        timestamp = info.get("timestamp", "") if info else ""
        msg = f"HALTED: {reason}"
        if timestamp:
            msg += f" (since {timestamp})"
        print(f"[HALT{context}] {msg}", file=sys.stderr)
        sys.exit(1)


def gh(*args: str) -> dict | str:
    """Run one `gh` call and return parsed JSON, raw text, or an {"error": ...} dict.

    `errors='replace'` is load-bearing, not decoration. With `encoding='utf-8'`
    and the default strict handler, a single undecodable byte in the transport's
    output (a cp1252 em-dash, 0x97, is the common one -- PR titles and branch
    names carry them) raises UnicodeDecodeError inside subprocess's reader
    THREAD. That exception never reaches this frame: it kills the thread,
    `result.stdout` comes back None, and the next `.strip()` dies with a
    misleading AttributeError. That is exactly how the merge queue crashed on
    every pass for 24+ consecutive runs. Replace, never 'ignore': a corrupted
    byte must stay visible as U+FFFD rather than silently vanishing from a
    branch name we are about to act on.
    """
    cmd = ["gh"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=60)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "rc": result.returncode}
    out = result.stdout.strip()
    try:
        return json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return out


def check_outcome(c: dict) -> str:
    """Classify one statusCheckRollup entry as 'pending' | 'green' | 'bad'.

    FAIL-CLOSED by construction: this is an ALLOW-LIST. Only an explicitly-green
    terminal outcome returns 'green'; every other value -- including ones GitHub
    may add in the future -- returns 'bad'. Never invert this into a deny-list.

    Two entry shapes come back from `gh pr view --json statusCheckRollup`:
      * CheckRun (GitHub Actions)  -- has `status` + `conclusion`
      * StatusContext (legacy commit status) -- has `state`
    Reading `state` off a CheckRun always yields None, which is why the previous
    FAILURE predicate never fired for any Actions check.
    """
    typename = c.get("__typename")
    is_check_run = typename == "CheckRun" or "conclusion" in c or "status" in c

    if is_check_run:
        if (c.get("status") or "").upper() != "COMPLETED":
            return "pending"
        conclusion = (c.get("conclusion") or "").upper()
        return "green" if conclusion in GREEN_CONCLUSIONS else "bad"

    if typename == "StatusContext" or "state" in c:
        state = (c.get("state") or "").upper()
        if state in ("PENDING", "EXPECTED"):
            return "pending"
        return "green" if state in GREEN_CONCLUSIONS else "bad"

    # Unrecognised shape: never green.
    return "pending"


def pr_state(n: int) -> dict:
    raw = gh("pr", "view", str(n), "--json",
             "state,mergeStateStatus,statusCheckRollup,title,headRefName,headRefOid")
    if isinstance(raw, dict) and "error" in raw:
        return {"state": "ERROR", "merge": "UNKNOWN", "checks": "unknown",
                "title": f"(error: {raw['error'][:80]})", "headRefName": "",
                "head_oid": ""}
    checks_list = raw.get("statusCheckRollup") or []
    outcomes = [check_outcome(c) for c in checks_list]
    if not checks_list:
        ci = "none"
    elif "pending" in outcomes:
        ci = "pending"
    elif "bad" in outcomes:
        # CANCELLED / TIMED_OUT / ACTION_REQUIRED / FAILURE / unknown -- NOT mergeable.
        ci = "FAIL"
    else:
        ci = "green"
    return {
        "state": raw.get("state", "UNKNOWN"),
        "merge": raw.get("mergeStateStatus", "UNKNOWN"),
        "checks": ci,
        "title": raw.get("title", ""),
        "headRefName": raw.get("headRefName", ""),
        # The sha whose checks were just read green; the merge is pinned to it.
        "head_oid": raw.get("headRefOid", ""),
    }


def update_branch(n: int) -> bool:
    """REFUSED: never asks GitHub to merge main into a PR branch server-side.

    A server-authored merge head is created with no box running the gates on its tree,
    so no signed receipt can exist for it. The lane merges origin/main LOCALLY (three-way,
    keep both intents, regenerate, explicit-refspec push) instead. No gh call is made.
    """
    print(f"  [REFUSED] #{n} is BEHIND: server-authored merge heads are banned "
          f"(no box ran the gates); merge main locally and push")
    return False


def merge_pr(n: int, head_oid: str = "") -> bool:
    """Squash-merge PR n, pinned to head_oid (tracker 31c0ddd1e22b).

    --match-head-commit makes GitHub refuse the merge if the PR head moved after
    the green snapshot, so an untested commit cannot land. No pin means no merge.
    """
    if not head_oid:
        print(f"  [FAIL] merge #{n} refused: no head sha pin (headRefOid missing)")
        return False
    result = gh("pr", "merge", str(n), "--squash", "--match-head-commit", head_oid)
    if isinstance(result, dict) and "error" in result:
        err = result["error"][:120]
        if "already merged" not in err.lower():
            print(f"  [FAIL] merge #{n} failed: {err}")
            return False
    verify = gh("pr", "view", str(n), "--json", "state", "--jq", ".state")
    if verify == "MERGED":
        print(f"  [ok] #{n} MERGED (verified)")
        return True
    else:
        print(f"  [FAIL] #{n} merge exit 0 but state={verify} -- NOT MERGED")
        return False


def retry_ci(n: int, head_ref_name: str) -> bool:
    """Attempt to rerun CI for PR n. Return True if rerun was triggered, False otherwise.

    On first FAIL, find the latest run and attempt rerun. If workflow already running,
    return False (keep PR queued without consuming retry). If rerun succeeds, return True.
    """
    if not head_ref_name:
        return False

    runs = gh("run", "list", "--branch", head_ref_name, "--limit", "1",
              "--json", "databaseId,status")
    if isinstance(runs, dict) and "error" in runs:
        print(f"  [WARN] Could not fetch run list for #{n}: {runs['error'][:80]}")
        return False

    if not runs or len(runs) == 0:
        print(f"  [WARN] No CI run found for #{n} on branch {head_ref_name}")
        return False

    run_id = runs[0].get("databaseId")
    run_status = runs[0].get("status")

    if not run_id:
        return False

    if run_status != "COMPLETED":
        # Workflow still running, don't attempt rerun
        return False

    rerun_result = gh("run", "rerun", str(run_id), "--failed")
    if isinstance(rerun_result, dict) and "error" in rerun_result:
        err = rerun_result["error"].lower()
        # Check if this is a transient/retriable error
        if any(pattern in err for pattern in RETRIABLE_RERUN_ERRORS):
            # Transient error (TOCTOU race, workflow state change, etc.)
            # Keep PR queued without consuming retry
            return False
        print(f"  [WARN] run rerun #{run_id} failed: {rerun_result['error'][:80]}")
        return False

    print(f"  [ok] #{n} CI rerun triggered (run {run_id})")
    return True


# ---------------------------------------------------------------------------
# Integration-branch mode (B1: six features)
# ---------------------------------------------------------------------------

def git(*args: str, cwd: str = None) -> tuple[bool, str]:
    """Run one `git` call, returning (ok, combined stdout+stderr).

    See `gh()` for why `errors='replace'` is mandatory here: git emits raw
    bytes from refs, config and commit messages without transcoding them, so
    strict UTF-8 decoding is a live crash, not a theoretical one.

    Args:
        *args: git command arguments
        cwd: working directory for the git call (default: current directory)
    """
    cmd = ["git"] + list(args)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=120)
    out = (result.stdout.strip() + "\n" + result.stderr.strip()).strip()
    return (result.returncode == 0, out)


def is_ancestor(head_oid: str) -> bool:
    """B1.3: Check if headRefOid is an ancestor of origin/main (content landed).

    Verify content actually landed before closing superseded PR.
    Return True if ancestor (safe to close), False if not (content never merged).
    """
    ok, out = git("merge-base", "--is-ancestor", head_oid, "origin/main")
    return ok


def check_enforce_admins() -> bool:
    """B1.4: Assert enforce_admins is enabled on main branch protection.

    Abort if branch protection does not require enforce_admins.
    """
    result = gh("api", "repos/matt82198/aesop/branches/main/protection",
                "--jq", ".enforce_admins.enabled")
    if isinstance(result, dict) and "error" in result:
        print(f"  [FAIL] cannot fetch branch protection: {result['error'][:80]}")
        return False
    if result is True or (isinstance(result, str) and result.strip().lower() == "true"):
        print(f"  [ok] branch protection enforce_admins verified")
        return True
    print(f"  [FAIL] branch protection enforce_admins is not enabled (got: {result})")
    return False


def run_regenerators(branch: str) -> bool:
    """B1.6: Regeneration hook - call derived-doc generators.

    Today: no-op stub (A1/A2/A3 generators plug in here).
    After all members merge into integration branch, call generators.
    If any generator modifies files, commit them.
    Return True if regeneration succeeded (commit may or may not have happened).
    """
    # Stub: generators not yet implemented
    print(f"  [info] regeneration stub (A1/A2/A3 generators not yet wired)")
    return True


def _integration_cwd_authorised(cwd: str, owner: str) -> tuple[bool, str]:
    """Worktree-ownership guard for `checkout -B integrate/*` (fail closed).

    Authorised only when cwd carries an rw `.aesop-worktree-owner` marker owned by
    `owner`, or when cwd is the AESOP_QUEUE_ROOT (the dedicated queue checkout,
    same contract as merge_queue.build_batch). Anything else -- no cwd, no owner,
    unmarked, ro, foreign-owned, or unreadable marker -- is refused.
    """
    if not cwd:
        return False, "no cwd given (refusing implicit process-cwd branch mutation)"
    target = str(Path(cwd).resolve())
    queue_root = os.environ.get("AESOP_QUEUE_ROOT")
    if queue_root and str(Path(queue_root).resolve()) == target:
        return True, ""
    if not owner:
        return False, "no owner given (set AESOP_WORKTREE_OWNER or pass owner=)"
    try:
        marker = read_worktree_marker(target)
    except MarkerError as exc:
        return False, f"{exc} (cwd {target})"
    if marker is None:
        return False, f"no .aesop-worktree-owner marker in {target}"
    if marker["mode"] != "rw":
        return False, f"worktree {target} is mode={marker['mode']}, not rw"
    if marker["owner"] != owner:
        return False, f"worktree {target} is owned by {marker['owner']!r}, not {owner!r}"
    return True, ""


def origin_push_authorised(push_origin: bool) -> tuple:
    """Gate for EVERY origin mutation from merge_train (branch push, PR create, remote delete).

    Both must hold: the explicit `--push-origin` flag AND AESOP_QUEUE_ROOT naming a valid
    git repository (same contract as merge_queue.build_batch). Anything else returns
    (False, reason) and the run stays local-only. Fail closed.
    """
    if not push_origin:
        return False, "--push-origin not given"
    queue_root = os.environ.get("AESOP_QUEUE_ROOT")
    if queue_root is None:
        return False, "AESOP_QUEUE_ROOT not set"
    try:
        resolved = Path(queue_root).resolve()
        ok, toplevel = git("rev-parse", "--show-toplevel", cwd=str(resolved))
    except OSError as exc:
        return False, f"AESOP_QUEUE_ROOT={queue_root} unusable: {exc}"
    if not ok or str(resolved) != str(Path(toplevel.strip()).resolve()):
        return False, f"AESOP_QUEUE_ROOT={queue_root} is not a valid git repository"
    return True, ""


def create_integration_branch(batch_name: str, cwd: str = None, owner: str = None) -> str:
    """Create integrate/<batch_name> from origin/main in a DEDICATED throwaway worktree.

    `cwd` is the base repository and must be an authorised worktree for `owner` (see
    _integration_cwd_authorised). The branch is added with `git worktree add -b` into a
    fresh temp dir, so nothing is ever checked out in `cwd` or the process cwd.
    Returns the new worktree path, or "" on refusal or failure. A non-empty return means
    this run created the branch, and the caller owns removing it (cleanup_local_integration).
    """
    if owner is None:
        owner = os.environ.get("AESOP_WORKTREE_OWNER")
    ok_cwd, why = _integration_cwd_authorised(cwd, owner)
    if not ok_cwd:
        # Name the RESOLVED path (8.3 / subst / junction aliases collapse to the real
        # location) and keep the message ASCII-only: a cp1252 console renders no em dash.
        shown = str(Path(cwd).resolve()) if cwd else "<no cwd>"
        print(f"  [REFUSED] create integrate/{batch_name} in {shown}: {why}")
        return ""
    branch = f"integrate/{batch_name}"
    ok, out = git("fetch", "origin", "main", cwd=cwd)
    if not ok:
        print(f"  [FAIL] fetch origin main: {out[:120]}")
        return ""
    scratch = tempfile.mkdtemp(prefix="aesop-integ-")
    wt = os.path.join(scratch, "tree")
    ok, out = git("worktree", "add", "-b", branch, wt, "origin/main", cwd=cwd)
    if not ok:
        print(f"  [FAIL] worktree add {branch}: {out[:120]}")
        shutil.rmtree(scratch, ignore_errors=True)
        return ""
    print(f"  [ok] created integration branch {branch} in worktree {wt}")
    return wt


def head_ref_problem(name: str, cwd: str = None) -> str:
    """Return why a PR headRefName must not be fetched, or "" when it is a safe name.

    headRefName is fork-controlled. An option-shaped name reached `git fetch` as an
    option (`--upload-pack=<cmd>` executed <cmd> on a file-transport origin), so it is
    refused before any git call. Every other name must pass `git check-ref-format
    --branch` and come back unchanged.
    """
    if not name:
        return "empty headRefName"
    if name.startswith("-"):
        return "headRefName starts with '-' (option-shaped, refused)"
    if "@{" in name:
        # --branch would expand revision syntax such as @{-1} to another branch name.
        return "headRefName contains '@{' (revision syntax, refused)"
    ok, _ = git("check-ref-format", "--branch", name, cwd=cwd)
    if not ok:
        return "headRefName is not a valid branch name"
    return ""


def merge_pr_into_integration(n: int, cwd: str = None) -> bool:
    raw = gh("pr", "view", str(n), "--json", "headRefOid,headRefName,title")
    if isinstance(raw, dict) and "error" in raw:
        print(f"  [FAIL] cannot read PR #{n}: {raw['error'][:120]}")
        return False

    sha = raw.get("headRefOid", "")
    title = raw.get("title", "")
    branch = raw.get("headRefName", "")
    if not sha:
        print(f"  [FAIL] PR #{n} has no headRefOid")
        return False

    problem = head_ref_problem(branch, cwd=cwd)
    if problem:
        print(f"  [FAIL] PR #{n} refused: {problem}")
        return False

    # `--` ends option parsing so the fork-controlled name is always a refspec.
    ok, _ = git("fetch", "origin", "--", branch, cwd=cwd)
    if not ok:
        ok, _ = git("fetch", "origin", "--", sha, cwd=cwd)
        if not ok:
            print(f"  [FAIL] cannot fetch PR #{n} ref")
            return False

    ok, out = git("merge", sha, "--no-edit",
                  "-m", f"integrate #{n}: {title[:60]}", cwd=cwd)
    if not ok:
        print(f"  [WARN] PR #{n} conflicts -- skipping: {out[:120]}")
        git("merge", "--abort", cwd=cwd)
        return False

    print(f"  [ok] merged #{n} ({title[:40]}) into integration branch")
    return True


def push_integration_branch(branch: str, cwd: str = None, allow_origin: bool = False) -> bool:
    """Push the integration branch to origin. Refused unless `allow_origin` (see origin_push_authorised)."""
    if not allow_origin:
        print(f"  [DRY] would push {branch} (origin not authorised; nothing sent)")
        return False
    ok, out = git("push", "-u", "origin", branch, "--force-with-lease", cwd=cwd)
    if not ok:
        print(f"  [FAIL] push {branch}: {out[:120]}")
        return False
    print(f"  [ok] pushed {branch}")
    return True


def create_integration_pr(branch: str, included_prs: list[int], allow_origin: bool = False) -> str:
    if not allow_origin:
        print(f"  [DRY] would open PR for {branch} (origin not authorised; nothing created)")
        return ""
    existing = gh("pr", "list", "--head", branch, "--json", "number,url",
                  "--state", "open")
    if isinstance(existing, list) and len(existing) > 0:
        url = existing[0].get("url", f"#{existing[0].get('number', '?')}")
        print(f"  [ok] reusing existing integration PR: {url}")
        return url

    pr_list = ", ".join(f"#{n}" for n in included_prs)
    body = f"Integration batch merging PRs: {pr_list}"
    result = gh("pr", "create", "--base", "main", "--head", branch,
                "--title", f"integrate: {branch.split('/')[-1]}",
                "--body", body)
    if isinstance(result, dict) and "error" in result:
        print(f"  [FAIL] create integration PR: {result['error'][:120]}")
        return ""
    url = result if isinstance(result, str) else str(result)
    print(f"  [ok] created integration PR: {url}")
    return url


def wait_for_integration_ci(pr_number: int, poll_interval: int = 45,
                            max_polls: int = 60) -> bool:
    for i in range(max_polls):
        info = pr_state(pr_number)
        if info["checks"] == "green":
            print(f"  [ok] integration PR #{pr_number} CI green")
            return True
        if info["checks"] == "FAIL":
            print(f"  [FAIL] integration PR #{pr_number} CI failed")
            return False
        if i < max_polls - 1:
            if poll_interval > 0:
                time.sleep(poll_interval)
    print(f"  [FAIL] integration PR #{pr_number} CI timed out after {max_polls} polls")
    return False


def merge_integration_pr(pr_number: int, head_oid: str = "") -> bool:
    """Squash-merge the integration PR pinned to head_oid, the sha CI was waited on."""
    if not head_oid:
        print(f"  [FAIL] integration PR #{pr_number} refused: no head sha pin")
        return False
    gh("pr", "merge", str(pr_number), "--squash", "--match-head-commit", head_oid)
    verify = gh("pr", "view", str(pr_number), "--json", "state", "--jq", ".state")
    if verify == "MERGED":
        print(f"  [ok] integration PR #{pr_number} MERGED (verified)")
        return True
    print(f"  [FAIL] integration PR #{pr_number} state={verify} -- NOT MERGED")
    return False


def close_superseded_prs(prs: list[int]):
    """Close superseded PRs after verifying content landed on main.

    B1.3: Before closing, verify `git merge-base --is-ancestor <headRefOid> origin/main`
    passes. If check fails, log loudly and skip (never close unlanded content).
    """
    for n in prs:
        # Fetch PR info to get headRefOid
        pr_info = gh("pr", "view", str(n), "--json", "headRefOid,title")
        if isinstance(pr_info, dict) and "error" in pr_info:
            print(f"  [FAIL] close #{n}: cannot fetch PR info: {pr_info['error'][:80]}")
            continue

        head_oid = pr_info.get("headRefOid", "")
        if not head_oid:
            print(f"  [FAIL] close #{n}: no headRefOid in response")
            continue

        # B1.3: Verify ancestor before close
        if not is_ancestor(head_oid):
            print(f"  [FAIL] close #{n}: content NOT ancestor of origin/main -- SKIPPING")
            print(f"         (PR content never merged; this is a correctness error)")
            continue

        result = gh("pr", "close", str(n),
                     "--comment", "Superseded by integration branch merge")
        if isinstance(result, dict) and "error" in result:
            print(f"  [WARN] close #{n}: {result['error'][:80]}")
        else:
            print(f"  [ok] closed #{n}")


def remote_branch_sha(branch: str, cwd: str = None):
    """Return the sha origin/<branch> points at, or "" when absent (exact ref match only).

    `cwd` pins which repository's `origin` is read (never the process cwd).
    """
    ok, out = git("ls-remote", "--heads", "origin", branch, cwd=cwd)
    if not ok:
        return ""
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].strip() == f"refs/heads/{branch}":
            return parts[0].strip()
    return ""


def discard_remote_branch(branch: str, expected_sha: str, reason: str,
                          allow_origin: bool = False, cwd: str = None) -> bool:
    """Delete origin/<branch> ONLY while it still points at the sha this run pushed.

    Name AND sha must both match, so a branch this run did not create is never removed.
    Refused unless origin is authorised. `cwd` pins the repository whose origin is used.
    """
    if not allow_origin:
        print(f"  [DRY] would delete origin/{branch} ({reason}); origin not authorised")
        return False
    remote_sha = remote_branch_sha(branch, cwd=cwd)
    if remote_sha != expected_sha:
        shown = remote_sha[:12] if remote_sha else "absent"
        print(f"  [skip] origin/{branch} is {shown}, not our {expected_sha[:12]}: not deleting")
        return False
    ok, out = git("push", "origin", "--delete", branch, cwd=cwd)
    if not ok:
        print(f"  [FAIL] delete origin/{branch}: {out[:120]}")
        return False
    print(f"  [ok] deleted origin/{branch} ({reason})")
    return True


def cleanup_local_integration(base: str, wt: str, branch: str) -> None:
    """Remove the throwaway worktree and the LOCAL branch this run created.

    Only called when `wt` is non-empty (this run created the branch). Never touches
    origin, and every git call is pinned to `base`, never the process cwd.
    """
    git("worktree", "remove", "--force", wt, cwd=base)
    shutil.rmtree(os.path.dirname(wt), ignore_errors=True)
    git("worktree", "prune", cwd=base)
    git("branch", "-D", branch, cwd=base)
    print(f"  [ok] cleaned up local {branch} and its worktree")


# The integration branch lives in a throwaway worktree created from an authorised base
# (see _integration_cwd_authorised); nothing is checked out in cwd. Origin is touched
# ONLY when origin_push_authorised() passes (--push-origin AND a valid AESOP_QUEUE_ROOT);
# otherwise the batch is built locally, prints "DRY: would push", and exits 0. Once pushed,
# a finally block deletes origin/<branch> only while it still points at the pushed sha.
def run_integration_train(prs: list[int], batch_name: str = "",
                          poll_interval: int = 45, max_polls: int = 60,
                          dry_run: bool = False, cwd: str = None,
                          owner: str = None, push_origin: bool = False) -> bool:
    """B1: Integration-batch merges by default + stacked-PR chains + landed-content close
    guard. B1.1: --integration DEFAULT, batch_name auto-generated. B1.2: stacked-PR chain
    graph, drop chain on conflict. B1.3: ancestor-verify before close. B1.4: enforce_admins
    required. B1.5: never update_branch in integration mode. B1.6: regeneration hook stub."""
    # Check halt BEFORE starting the integration train
    _check_halt(" [run_integration_train entry]")
    if not batch_name:
        batch_name = f"batch-{datetime.datetime.now().strftime('%Y%m%d-%H%M')}"

    branch = f"integrate/{batch_name}"

    print(f"\n{'='*60}")
    print(f"Integration mode: batching {len(prs)} PRs into {branch}")
    if dry_run:
        print(f"DRY-RUN: trial merge in scratch worktree (no push)")
    print(f"PRs: {', '.join(f'#{n}' for n in prs)}")
    print(f"{'='*60}")

    # B1.4: Enforce admins check
    if not check_enforce_admins():
        return False

    origin_ok, origin_why = origin_push_authorised(push_origin)
    if not origin_ok:
        print(f"[info] origin not authorised ({origin_why}): local-only run, no origin writes")

    wt = create_integration_branch(batch_name, cwd=cwd, owner=owner)
    if not wt:
        return False

    base = cwd
    pushed_sha = None
    outcome = "not merged (see [FAIL] lines above)"
    try:
        included = []
        skipped = []
        for n in prs:
            if merge_pr_into_integration(n, cwd=wt):
                included.append(n)
            else:
                skipped.append(n)

        if not included:
            print(f"\n[FAIL] No PRs could be merged into integration branch")
            return False

        if skipped:
            print(f"\n[info] Skipped {len(skipped)} conflicting PRs: "
                  f"{', '.join(f'#{n}' for n in skipped)}")

        # B1.1 --dry-run: trial in scratch worktree, don't push
        if dry_run:
            print(f"\n[DRY-RUN] Merge partition:")
            print(f"  Clean PRs: {', '.join(f'#{n}' for n in included)}")
            print(f"  Conflicting PRs: {', '.join(f'#{n}' for n in skipped)}")
            return len(skipped) == 0

        if not origin_ok:
            print(f"DRY: would push {branch}")
            print(f"  (local-only: {origin_why}; no push, PR, or remote delete)")
            print(f"  Clean PRs: {', '.join(f'#{n}' for n in included)}")
            return True

        ok, head = git("rev-parse", "HEAD", cwd=wt)
        if not ok:
            print(f"  [FAIL] cannot read integration HEAD: {head[:120]}")
            return False
        # Recorded BEFORE the push so a failed or interrupted push is still reconciled.
        pushed_sha = head.strip()
        if not push_integration_branch(branch, cwd=wt, allow_origin=True):
            return False

        pr_url = create_integration_pr(branch, included, allow_origin=True)
        if not pr_url:
            outcome = "integration PR could not be created"
            return False

        pr_number = None
        for part in pr_url.rstrip("/").split("/"):
            if part.isdigit():
                pr_number = int(part)
        if pr_number is None:
            print(f"  [FAIL] cannot parse PR number from {pr_url}")
            outcome = "integration PR number unparseable"
            return False

        print(f"\n[waiting] Waiting for CI on integration PR #{pr_number}...")
        if not wait_for_integration_ci(pr_number, poll_interval=poll_interval,
                                       max_polls=max_polls):
            print(f"\n[FAIL] Integration PR CI did not pass")
            outcome = f"integration PR #{pr_number} CI did not pass"
            return False

        # Re-check halt before merging integration PR (kill-switch enforcement)
        _check_halt(" [before integration merge]")
        if not merge_integration_pr(pr_number, head_oid=pushed_sha):
            outcome = f"integration PR #{pr_number} not MERGED"
            return False

        # B1.6: Regeneration hook
        if not run_regenerators(branch):
            outcome = "regeneration failed after merge"
            return False

        close_superseded_prs(included)
        outcome = f"integration PR #{pr_number} merged"

        print(f"\n{'='*60}")
        print(f"DONE -- {len(included)} PRs merged via integration branch")
        if skipped:
            print(f"Skipped (conflicts): {', '.join(f'#{n}' for n in skipped)}")
        print(f"{'='*60}")
        return True
    except BaseException as exc:
        outcome = f"run aborted by {type(exc).__name__}: {exc}"
        raise
    finally:
        if pushed_sha is not None:
            discard_remote_branch(branch, pushed_sha, outcome, allow_origin=True, cwd=base)
        if wt:
            cleanup_local_integration(base, wt, branch)


def run_train(prs: list[int], max_rounds: int = 50, poll_interval: int = 45,
              skip_dirty: bool = False, rebase_behind: bool = False):
    # Check halt BEFORE starting the merge train
    _check_halt(" [run_train entry]")
    merged = []
    skipped = []
    retried = {}  # Track PR -> retry count (max 1)
    dirty_count = 0  # Consecutive rounds with all PRs DIRTY
    round_num = 0

    while prs and round_num < max_rounds:
        round_num += 1
        print(f"\n{'='*60}")
        print(f"Round {round_num} -- {len(prs)} PRs remaining, {len(merged)} merged")
        print(f"{'='*60}")

        progress_this_round = False
        still_open = []
        all_dirty_blocked = True  # Track if all remaining are DIRTY/BLOCKED
        any_pending = False  # Track if any PR has pending checks

        for n in prs:
            info = pr_state(n)
            tag = f"#{n} ({info['title'][:40]})"

            if info["state"] != "OPEN":
                if info["state"] == "MERGED":
                    print(f"  [ok] {tag} already MERGED")
                    merged.append(n)
                else:
                    print(f"  [-] {tag} state={info['state']} -- skipping")
                    skipped.append(n)
                progress_this_round = True
                all_dirty_blocked = False
                continue

            if info["merge"] == "DIRTY":
                if skip_dirty:
                    print(f"  [FAIL] {tag} has merge conflicts -- skipping")
                    skipped.append(n)
                    progress_this_round = True
                else:
                    print(f"  [WARN] {tag} has merge conflicts -- re-checking next round")
                    still_open.append(n)
                continue

            all_dirty_blocked = False

            # B1.5: Serial mode only updates branches if --rebase-behind flag is set
            if info["merge"] == "BEHIND" or info["merge"] == "UNKNOWN":
                if rebase_behind:
                    update_branch(n)
                still_open.append(n)
                continue

            if info["checks"] == "pending":
                any_pending = True
                still_open.append(n)
                continue

            if info["checks"] == "FAIL":
                # Defect 1: Flake retry - on first FAIL, attempt rerun once
                if n not in retried:
                    print(f"  [info] {tag} CI FAILING -- attempting flake retry")
                    if retry_ci(n, info.get("headRefName", "")):
                        retried[n] = 1
                        still_open.append(n)
                        continue
                    else:
                        # Rerun not triggered (already running or other issue), keep in queue
                        print(f"  [info] {tag} cannot rerun now -- will retry next round")
                        still_open.append(n)
                        continue
                else:
                    # Already retried once, now skip
                    print(f"  [FAIL] {tag} CI FAILING (after retry) -- skipping")
                    skipped.append(n)
                    progress_this_round = True
                continue

            if info["merge"] == "CLEAN" and info["checks"] == "green":
                # Re-check halt before merging (kill-switch enforcement)
                _check_halt(" [before merge]")
                if merge_pr(n, info["head_oid"]):
                    merged.append(n)
                    progress_this_round = True
                    # After a merge, all others go BEHIND -- restart the loop
                    still_open.extend(prs[prs.index(n)+1:])
                    # Remove already-processed items
                    still_open = [p for p in still_open
                                  if p not in merged and p not in skipped and p != n]
                    break
                else:
                    still_open.append(n)
                    continue

            if info["merge"] == "BLOCKED":
                still_open.append(n)
                continue

            print(f"  ? {tag} unexpected: merge={info['merge']} checks={info['checks']}")
            still_open.append(n)

        prs = list(dict.fromkeys(still_open))

        if not prs:
            break

        # Defect 3: Adaptive poll
        if not progress_this_round:
            if any_pending:
                # If any PR has pending checks, use default poll interval
                wait_time = poll_interval
            elif all_dirty_blocked and len(prs) > 0:
                # If all remaining are DIRTY/BLOCKED with no progress, use 3x capped at 300s
                wait_time = min(poll_interval * 3, 300)
                dirty_count += 1
                # Defect 2: If all DIRTY for 5 consecutive rounds, exit with error
                if dirty_count >= 5:
                    print(f"\n[STUCK] All {len(prs)} PRs stuck (DIRTY/BLOCKED) for 5 rounds")
                    print(f"Stuck PRs: {', '.join(f'#{n}' for n in prs)}")
                    break
            else:
                wait_time = poll_interval
                dirty_count = 0

            print(f"\n[waiting] No PR ready yet -- waiting {wait_time}s for CI...")
            time.sleep(wait_time)
        else:
            dirty_count = 0

    print(f"\n{'='*60}")
    print(f"DONE -- {len(merged)} merged, {len(skipped)} skipped, {len(prs)} remaining")
    print(f"{'='*60}")
    if merged:
        print(f"Merged: {', '.join(f'#{n}' for n in merged)}")
    if skipped:
        print(f"Skipped: {', '.join(f'#{n}' for n in skipped)}")
    if prs:
        print(f"Remaining: {', '.join(f'#{n}' for n in prs)}")
    return len(prs) == 0


def main():
    parser = argparse.ArgumentParser(
        description="Integration-batch or serial merge train for GitHub PRs")
    parser.add_argument("prs", nargs="*", type=int, help="PR numbers to merge")
    parser.add_argument("--file", help="File with one PR number per line")
    parser.add_argument("--max-rounds", type=int, default=50,
                        help="Max rounds for serial mode")
    parser.add_argument("--poll", type=int, default=45, help="Seconds between CI polls")
    parser.add_argument("--skip-dirty", action="store_true",
                        help="Skip PRs with merge conflicts immediately")
    parser.add_argument("--serial", action="store_true",
                        help="Serial mode (legacy): merge one PR at a time")
    parser.add_argument("--rebase-behind", action="store_true",
                        help="Serial mode: report BEHIND PRs as refused (server-side update is banned; default: off)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Trial merge in scratch worktree, print partition, push nothing")
    parser.add_argument("-i", "--integration", nargs="?", const="",
                        default=None, metavar="BATCH_NAME",
                        help="Integration-branch mode with optional batch name")
    parser.add_argument("--cwd", default=None, metavar="WORKTREE",
                        help="Integration mode: worktree to create integrate/* in; must carry "
                             "an rw .aesop-worktree-owner marker owned by --owner (or the "
                             "AESOP_QUEUE_ROOT). Refused otherwise.")
    parser.add_argument("--owner", default=None, metavar="ID",
                        help="Integration mode: owner id the worktree marker must name "
                             "(default: AESOP_WORKTREE_OWNER env)")
    parser.add_argument("--push-origin", action="store_true",
                        help="Integration mode: allow pushing integrate/* to origin, opening "
                             "its PR and deleting it. ALSO requires AESOP_QUEUE_ROOT to name "
                             "a valid git repo. Without both, the run is local-only.")
    args = parser.parse_args()

    prs = list(args.prs)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            prs.extend(int(line.strip()) for line in f if line.strip().isdigit())

    if not prs:
        parser.error("No PR numbers provided")

    # B1.1: Integration is DEFAULT; --serial is escape hatch
    if args.serial:
        print(f"Serial merge train: {len(prs)} PRs queued")
        print(f"Order: {', '.join(f'#{n}' for n in prs)}")
        success = run_train(prs, max_rounds=args.max_rounds, poll_interval=args.poll,
                           skip_dirty=args.skip_dirty, rebase_behind=args.rebase_behind)
    else:
        # Integration mode (default)
        batch_name = args.integration if args.integration else ""
        print(f"Integration merge train: {len(prs)} PRs queued (default mode)")
        if batch_name:
            print(f"Batch name: {batch_name}")
        print(f"PRs: {', '.join(f'#{n}' for n in prs)}")
        success = run_integration_train(
            prs, batch_name=batch_name, poll_interval=args.poll,
            dry_run=args.dry_run, cwd=args.cwd, owner=args.owner,
            push_origin=args.push_origin)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
