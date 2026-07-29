#!/usr/bin/env python3
"""Serial merge train — update-branch, wait for CI, merge, verify MERGED.

Handles the strict-up-to-date treadmill: each merge invalidates all others,
so we loop until all PRs are merged or permanently stuck.

Usage:
    python tools/merge_train.py 492 493 494 495 497 498 499 500 501 502 503 504 505 507 508 509
    python tools/merge_train.py --file pr-list.txt   # one number per line
"""
import argparse
import json
import subprocess
import sys
import time


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
             "state,mergeStateStatus,statusCheckRollup,title")
    if isinstance(raw, dict) and "error" in raw:
        return {"state": "ERROR", "merge": "UNKNOWN", "checks": "unknown",
                "title": f"(error: {raw['error'][:80]})"}
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
    }


def update_branch(n: int) -> bool:
    result = gh("pr", "update-branch", str(n))
    if isinstance(result, dict) and "error" in result:
        print(f"  [WARN] update-branch #{n} failed: {result['error'][:120]}")
        return False
    print(f"  [ok] #{n} updated to latest main")
    return True


def merge_pr(n: int) -> bool:
    result = gh("pr", "merge", str(n), "--squash", "--delete-branch")
    if isinstance(result, dict) and "error" in result:
        print(f"  [FAIL] merge #{n} failed: {result['error'][:120]}")
        return False
    verify = gh("pr", "view", str(n), "--json", "state", "--jq", ".state")
    if verify == "MERGED":
        print(f"  [ok] #{n} MERGED (verified)")
        return True
    else:
        print(f"  [FAIL] #{n} merge exit 0 but state={verify} — NOT MERGED")
        return False


def run_train(prs: list[int], max_rounds: int = 50, poll_interval: int = 45):
    merged = []
    skipped = []
    round_num = 0

    while prs and round_num < max_rounds:
        round_num += 1
        print(f"\n{'='*60}")
        print(f"Round {round_num} — {len(prs)} PRs remaining, {len(merged)} merged")
        print(f"{'='*60}")

        progress_this_round = False
        still_open = []

        for n in prs:
            info = pr_state(n)
            tag = f"#{n} ({info['title'][:40]})"

            if info["state"] != "OPEN":
                if info["state"] == "MERGED":
                    print(f"  [ok] {tag} already MERGED")
                    merged.append(n)
                else:
                    print(f"  [-] {tag} state={info['state']} — skipping")
                    skipped.append(n)
                progress_this_round = True
                continue

            if info["merge"] == "DIRTY":
                print(f"  [FAIL] {tag} has merge conflicts — skipping")
                skipped.append(n)
                continue

            if info["merge"] == "BEHIND" or info["merge"] == "UNKNOWN":
                update_branch(n)
                still_open.append(n)
                continue

            if info["checks"] == "pending":
                still_open.append(n)
                continue

            if info["checks"] == "FAIL":
                print(f"  [FAIL] {tag} CI FAILING — skipping")
                skipped.append(n)
                continue

            if info["merge"] == "CLEAN" and info["checks"] == "green":
                if merge_pr(n):
                    merged.append(n)
                    progress_this_round = True
                    # After a merge, all others go BEHIND — restart the loop
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

        if not progress_this_round:
            print(f"\n[waiting] No PR ready yet — waiting {poll_interval}s for CI...")
            time.sleep(poll_interval)

    print(f"\n{'='*60}")
    print(f"DONE — {len(merged)} merged, {len(skipped)} skipped, {len(prs)} remaining")
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
    args = parser.parse_args()

    prs = list(args.prs)
    if args.file:
        with open(args.file) as f:
            prs.extend(int(line.strip()) for line in f if line.strip().isdigit())

    if not prs:
        parser.error("No PR numbers provided")

    print(f"Merge train: {len(prs)} PRs queued")
    print(f"Order: {', '.join(f'#{n}' for n in prs)}")
    success = run_train(prs, max_rounds=args.max_rounds, poll_interval=args.poll)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
