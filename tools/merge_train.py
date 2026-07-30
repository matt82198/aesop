#!/usr/bin/env python3
"""Serial merge train -- update-branch, wait for CI, merge, verify MERGED.

Handles the strict-up-to-date treadmill: each merge invalidates all others,
so we loop until all PRs are merged or permanently stuck.

Enhancements:
- Flake retry: on first FAIL, rerun CI once via gh run rerun --failed (one retry max per PR)
- DIRTY handling: PRs with conflicts stay in queue, re-checked each round (--skip-dirty restores old behavior)
- Adaptive poll: use 45s if any pending, else 3x poll (min 45s, max 300s) if all stuck DIRTY/BLOCKED

Usage:
    python tools/merge_train.py 492 493 494 495 497 498 499 500 501 502 503 504 505 507 508 509
    python tools/merge_train.py --file pr-list.txt   # one number per line
    python tools/merge_train.py --skip-dirty 492 493  # old behavior: skip DIRTY immediately
"""
import argparse
import functools
import json
import subprocess
import sys
import time

print = functools.partial(print, flush=True)


def gh(*args: str) -> dict | str:
    cmd = ["gh"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "rc": result.returncode}
    out = result.stdout.strip()
    try:
        return json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return out


def pr_state(n: int) -> dict:
    raw = gh("pr", "view", str(n), "--json",
             "state,mergeStateStatus,statusCheckRollup,title,headRefName")
    if isinstance(raw, dict) and "error" in raw:
        return {"state": "ERROR", "merge": "UNKNOWN", "checks": "unknown",
                "title": f"(error: {raw['error'][:80]})", "headRefName": ""}
    checks_list = raw.get("statusCheckRollup") or []
    pending = sum(1 for c in checks_list if c.get("status") != "COMPLETED")
    failing = sum(1 for c in checks_list
                  if c.get("status") == "COMPLETED" and c.get("state") == "FAILURE")
    if pending > 0:
        ci = "pending"
    elif failing > 0:
        ci = "FAIL"
    elif len(checks_list) == 0:
        ci = "none"
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
        if "already running" in err or "workflow is already running" in err:
            # Workflow just started, keep PR queued without consuming retry
            return False
        print(f"  [WARN] run rerun #{run_id} failed: {rerun_result['error'][:80]}")
        return False

    print(f"  [ok] #{n} CI rerun triggered (run {run_id})")
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
    parser = argparse.ArgumentParser(description="Serial merge train for GitHub PRs")
    parser.add_argument("prs", nargs="*", type=int, help="PR numbers to merge")
    parser.add_argument("--file", help="File with one PR number per line")
    parser.add_argument("--max-rounds", type=int, default=50)
    parser.add_argument("--poll", type=int, default=45, help="Seconds between CI polls")
    parser.add_argument("--skip-dirty", action="store_true",
                        help="Skip PRs with merge conflicts immediately (old behavior)")
    args = parser.parse_args()

    prs = list(args.prs)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            prs.extend(int(line.strip()) for line in f if line.strip().isdigit())

    if not prs:
        parser.error("No PR numbers provided")

    print(f"Merge train: {len(prs)} PRs queued")
    print(f"Order: {', '.join(f'#{n}' for n in prs)}")
    success = run_train(prs, max_rounds=args.max_rounds, poll_interval=args.poll,
                       skip_dirty=args.skip_dirty)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
