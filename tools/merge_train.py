#!/usr/bin/env python3
"""Merge train -- serial or integration-branch batch merge for GitHub PRs.
INDEX: Serial or integration-branch merge train: serial mode processes PRs one-at-a-time (update-branch, wait for CI, merge, verify MERGED); integration mode (`-i [BATCH_NAME]`) batches PRs into a local `integrate/<name>` branch, runs CI once, squash-merges, closes superseded PRs. Check classification is fail-closed via the `GREEN_CONCLUSIONS` allow-list (`SUCCESS`/`NEUTRAL`/`SKIPPED` only) and `check_outcome()`, which reads `conclusion` on CheckRun entries and `state` on legacy StatusContext entries — CANCELLED/TIMED_OUT/ACTION_REQUIRED/unknown are never green. Keep it an allow-list: a deny-list lets COMPLETED-but-not-FAILURE outcomes fall through to a merge

Serial mode (default): update-branch, wait for CI, merge one at a time.
Integration mode (--integration): batch PRs into a single integration branch,
test once, merge to main, close superseded PRs.

Usage:
    python tools/merge_train.py 492 493 494          # serial mode
    python tools/merge_train.py --file pr-list.txt   # one number per line
    python tools/merge_train.py --skip-dirty 492 493  # skip DIRTY immediately
    python tools/merge_train.py -i 492 493 494       # integration-branch mode
    python tools/merge_train.py --integration my-batch 492 493  # named batch
"""
import argparse
import functools
import io
import json
import subprocess
import sys
import time

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
             "state,mergeStateStatus,statusCheckRollup,title,headRefName")
    if isinstance(raw, dict) and "error" in raw:
        return {"state": "ERROR", "merge": "UNKNOWN", "checks": "unknown",
                "title": f"(error: {raw['error'][:80]})", "headRefName": ""}
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
    }


def update_branch(n: int) -> bool:
    result = gh("pr", "update-branch", str(n))
    if isinstance(result, dict) and "error" in result:
        print(f"  [WARN] update-branch #{n} failed: {result['error'][:120]}")
        return False
    print(f"  [ok] #{n} updated to latest main")
    return True


def merge_pr(n: int) -> bool:
    result = gh("pr", "merge", str(n), "--squash")
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
# Integration-branch mode
# ---------------------------------------------------------------------------

def git(*args: str) -> tuple[bool, str]:
    """Run one `git` call, returning (ok, combined stdout+stderr).

    See `gh()` for why `errors='replace'` is mandatory here: git emits raw
    bytes from refs, config and commit messages without transcoding them, so
    strict UTF-8 decoding is a live crash, not a theoretical one.
    """
    cmd = ["git"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=120)
    out = (result.stdout.strip() + "\n" + result.stderr.strip()).strip()
    return (result.returncode == 0, out)


def create_integration_branch(batch_name: str) -> bool:
    branch = f"integrate/{batch_name}"
    ok, out = git("fetch", "origin", "main")
    if not ok:
        print(f"  [FAIL] fetch origin main: {out[:120]}")
        return False
    ok, out = git("checkout", "-B", branch, "origin/main")
    if not ok:
        print(f"  [FAIL] checkout {branch}: {out[:120]}")
        return False
    print(f"  [ok] created integration branch {branch}")
    return True


def merge_pr_into_integration(n: int) -> bool:
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

    ok, _ = git("fetch", "origin", f"{branch}")
    if not ok:
        ok, _ = git("fetch", "origin", sha)
        if not ok:
            print(f"  [FAIL] cannot fetch PR #{n} ref")
            return False

    ok, out = git("merge", sha, "--no-edit",
                  "-m", f"integrate #{n}: {title[:60]}")
    if not ok:
        print(f"  [WARN] PR #{n} conflicts -- skipping: {out[:120]}")
        git("merge", "--abort")
        return False

    print(f"  [ok] merged #{n} ({title[:40]}) into integration branch")
    return True


def push_integration_branch(branch: str) -> bool:
    ok, out = git("push", "-u", "origin", branch, "--force-with-lease")
    if not ok:
        print(f"  [FAIL] push {branch}: {out[:120]}")
        return False
    print(f"  [ok] pushed {branch}")
    return True


def create_integration_pr(branch: str, included_prs: list[int]) -> str:
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


def merge_integration_pr(pr_number: int) -> bool:
    gh("pr", "merge", str(pr_number), "--squash")
    verify = gh("pr", "view", str(pr_number), "--json", "state", "--jq", ".state")
    if verify == "MERGED":
        print(f"  [ok] integration PR #{pr_number} MERGED (verified)")
        return True
    print(f"  [FAIL] integration PR #{pr_number} state={verify} -- NOT MERGED")
    return False


def close_superseded_prs(prs: list[int]):
    for n in prs:
        result = gh("pr", "close", str(n),
                     "--comment", "Superseded by integration branch merge")
        if isinstance(result, dict) and "error" in result:
            print(f"  [WARN] close #{n}: {result['error'][:80]}")
        else:
            print(f"  [ok] closed #{n}")


def cleanup_integration_branch(branch: str):
    git("checkout", "main")
    git("branch", "-D", branch)
    git("push", "origin", "--delete", branch)
    print(f"  [ok] cleaned up {branch}")


def run_integration_train(prs: list[int], batch_name: str = "batch-wave",
                          poll_interval: int = 45, max_polls: int = 60) -> bool:
    branch = f"integrate/{batch_name}"

    print(f"\nIntegration mode: batching {len(prs)} PRs into {branch}")
    print(f"PRs: {', '.join(f'#{n}' for n in prs)}")

    if not create_integration_branch(batch_name):
        return False

    included = []
    skipped = []
    for n in prs:
        if merge_pr_into_integration(n):
            included.append(n)
        else:
            skipped.append(n)

    if not included:
        print(f"\n[FAIL] No PRs could be merged into integration branch")
        cleanup_integration_branch(branch)
        return False

    if skipped:
        print(f"\n[info] Skipped {len(skipped)} conflicting PRs: "
              f"{', '.join(f'#{n}' for n in skipped)}")

    if not push_integration_branch(branch):
        cleanup_integration_branch(branch)
        return False

    pr_url = create_integration_pr(branch, included)
    if not pr_url:
        cleanup_integration_branch(branch)
        return False

    pr_number = None
    for part in pr_url.rstrip("/").split("/"):
        if part.isdigit():
            pr_number = int(part)
    if pr_number is None:
        print(f"  [FAIL] cannot parse PR number from {pr_url}")
        cleanup_integration_branch(branch)
        return False

    print(f"\n[waiting] Waiting for CI on integration PR #{pr_number}...")
    if not wait_for_integration_ci(pr_number, poll_interval=poll_interval,
                                   max_polls=max_polls):
        print(f"\n[FAIL] Integration PR CI did not pass")
        return False

    if not merge_integration_pr(pr_number):
        return False

    close_superseded_prs(included)
    cleanup_integration_branch(branch)

    print(f"\n{'='*60}")
    print(f"DONE -- {len(included)} PRs merged via integration branch")
    if skipped:
        print(f"Skipped (conflicts): {', '.join(f'#{n}' for n in skipped)}")
    print(f"{'='*60}")
    return True


def run_train(prs: list[int], max_rounds: int = 50, poll_interval: int = 45,
              skip_dirty: bool = False):
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

            if info["merge"] == "BEHIND" or info["merge"] == "UNKNOWN":
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
                if merge_pr(n):
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
        description="Serial or integration-branch merge train for GitHub PRs")
    parser.add_argument("prs", nargs="*", type=int, help="PR numbers to merge")
    parser.add_argument("--file", help="File with one PR number per line")
    parser.add_argument("--max-rounds", type=int, default=50)
    parser.add_argument("--poll", type=int, default=45, help="Seconds between CI polls")
    parser.add_argument("--skip-dirty", action="store_true",
                        help="Skip PRs with merge conflicts immediately (old behavior)")
    parser.add_argument("-i", "--integration", nargs="?", const="batch-wave",
                        default=None, metavar="BATCH_NAME",
                        help="Integration-branch mode: batch PRs into a single "
                             "integration branch (default name: batch-wave)")
    args = parser.parse_args()

    prs = list(args.prs)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            prs.extend(int(line.strip()) for line in f if line.strip().isdigit())

    if not prs:
        parser.error("No PR numbers provided")

    if args.integration is not None:
        print(f"Integration merge train: {len(prs)} PRs queued")
        print(f"Batch name: {args.integration}")
        print(f"PRs: {', '.join(f'#{n}' for n in prs)}")
        success = run_integration_train(
            prs, batch_name=args.integration, poll_interval=args.poll)
    else:
        print(f"Merge train: {len(prs)} PRs queued")
        print(f"Order: {', '.join(f'#{n}' for n in prs)}")
        success = run_train(prs, max_rounds=args.max_rounds, poll_interval=args.poll,
                           skip_dirty=args.skip_dirty)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
