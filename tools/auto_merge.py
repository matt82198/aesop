#!/usr/bin/env python3
"""Batch auto-merge: fix conflicts, merge green PRs, loop until done.
INDEX: Batch PR merge tool (fix-by-default: merge main into broken branches + merge green PRs; `--no-fix`/`--loop`/`--dry-run`/`--json`/`--wait`); uses subprocess_common.py for timeouts + encoding; MERGED-state verification gate at lines 101-105; run with `--loop` to continuously merge all green PRs; use merge_train.py for one-shot serial CI-gated queues

One command to clear the PR backlog. No serial merge trains.

Modes:
  python tools/auto_merge.py <n> [<n>...]  Merge specific PR(s) by number
  python tools/auto_merge.py --all          Merge all green PRs (board-wide mode)

Options:
  --no-fix         Only merge green PRs, skip fixing broken branches
  --loop           Fix + merge in a loop until done (max 3 rounds)
  --dry-run        Show plan without acting
  --json           JSON output

Usage:
    python tools/auto_merge.py 806           Merge PR #806
    python tools/auto_merge.py 806 807 809   Merge PRs #806, #807, #809
    python tools/auto_merge.py --all         Merge all open green PRs

Exit codes: 0=all merged, 1=some blocked, 2=error
"""
import argparse
import json
import os
import subprocess
import sys
import time

# Ensure this tool's own directory (tools/) is importable so the shared
# harness resolves regardless of cwd or how the file is loaded
# (the import-gate loads tools by path, without tools/ on sys.path).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from subprocess_common import gh, git, json_output, run


def get_open_prs():
    r = gh(['pr', 'list', '--state', 'open', '--limit', '50',
            '--json', 'number,title,headRefName'])
    return json_output(r)


def check_pr_status(pr_num):
    """Returns (status, detail) where status is 'green', 'red', 'pending', 'conflict'."""
    pr = str(pr_num)
    r = gh(['pr', 'view', pr, '--json', 'mergeable', '-q', '.mergeable'],
           check=False)
    mergeable = r.stdout.strip()
    if mergeable == 'CONFLICTING':
        return 'conflict', 'merge conflict with main'

    r = gh(['pr', 'checks', pr, '--json', 'name,bucket,state'], check=False)
    if r.returncode != 0:
        return 'pending', 'checks query failed'
    checks = json.loads(r.stdout)
    if not checks:
        return 'pending', 'no checks'

    has_pending = False
    for c in checks:
        if c.get('bucket') == 'fail':
            return 'red', f"{c['name']}: FAILURE"
        if c.get('bucket') in ('cancel',):
            return 'red', f"{c['name']}: CANCELLED"
        if c.get('bucket') == 'pending':
            has_pending = True
    if has_pending:
        return 'pending', 'checks still running'
    return 'green', 'all checks passed'


def merge_pr(pr_num):
    pr = str(pr_num)
    r = gh(['pr', 'merge', pr, '--merge'], check=False)
    if r.returncode != 0:
        stderr = r.stderr.strip()
        if 'not mergeable' in stderr:
            return False, 'conflict — needs rebase'
        return False, f'merge failed: {stderr}'
    r2 = gh(['pr', 'view', pr, '--json', 'state', '-q', '.state'], check=False)
    state = r2.stdout.strip()
    if state != 'MERGED':
        return False, f'state={state}'
    return True, 'MERGED'


def fix_branch(branch):
    """Merge main into branch, fix test counts + CLAUDE.md limits, push."""
    git(['fetch', 'origin', 'main'], check=False)
    r = git(['fetch', 'origin', branch], check=False)
    if r.returncode != 0:
        return False, f'fetch failed for {branch}'

    git(['checkout', 'main'], check=False)
    r = git(['checkout', branch], check=False)
    if r.returncode != 0:
        return False, f'checkout failed for {branch}'

    r = git(['merge', 'origin/main', '--no-edit'], check=False)
    if r.returncode != 0:
        for f in ['tests/CLAUDE.md', 'tools/CLAUDE.md']:
            git(['checkout', '--theirs', f], check=False)
            git(['add', f], check=False)
        r2 = git(['-c', 'core.editor=true', 'merge', '--continue'], check=False)
        if r2.returncode != 0:
            git(['merge', '--abort'], check=False)
            git(['checkout', 'main'], check=False)
            return False, 'merge conflict unresolvable'

    run([sys.executable, 'tools/verify_test_suite_count.py', '--fix'], check=False, timeout=30)
    run([sys.executable, 'tools/claudemd_lint.py'], check=False, timeout=30)

    git(['add', '-A'], check=False)
    r = git(['diff', '--cached', '--quiet'], check=False)
    if r.returncode != 0:
        git(['commit', '-m', 'fix: merge main + resolve conflicts'], check=False)

    r = run([sys.executable, 'tools/secret_scan.py', '--staged'], check=False, timeout=30)
    if r.returncode != 0:
        git(['checkout', 'main'], check=False)
        return False, 'secret scan failed'

    r = git(['push', 'origin', branch], check=False)
    git(['checkout', 'main'], check=False)
    if r.returncode != 0:
        return False, 'push failed'
    return True, 'fixed + pushed, CI re-triggered'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pr_numbers', nargs='*', type=int,
                        help='PR number(s) to merge (required unless --all is given)')
    parser.add_argument('--all', action='store_true',
                        help='Merge all open green PRs (board-wide mode)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--no-fix', action='store_true',
                        help='Skip fixing non-green branches (default: fix is ON)')
    parser.add_argument('--loop', action='store_true',
                        help='Loop: fix → wait → merge until done (max 3 rounds)')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--wait', type=int, default=180,
                        help='Seconds to wait for CI between loop rounds (default 180)')
    args = parser.parse_args()

    # Fail-closed: require explicit PR number(s) or --all flag
    if not args.pr_numbers and not args.all:
        print('auto_merge.py: error: must specify PR number(s) or use --all flag', file=sys.stderr)
        print('Usage: auto_merge.py <n> [<n>...] or auto_merge.py --all', file=sys.stderr)
        sys.exit(2)

    args.fix = not args.no_fix

    max_rounds = 3 if args.loop else 1
    all_results = []

    for round_num in range(1, max_rounds + 1):
        prs = get_open_prs()
        if not prs:
            print('No open PRs.')
            break

        # Filter PRs based on mode
        if args.all:
            # Board-wide mode: process all open PRs
            target_prs = prs
        else:
            # Scoped mode: only process specified PRs
            target_prs = [pr for pr in prs if pr['number'] in args.pr_numbers]

        target_prs.sort(key=lambda p: p['number'])
        round_results = []
        merged = 0
        fixed = 0

        for pr in target_prs:
            num, title, branch = pr['number'], pr['title'], pr['headRefName']
            status, detail = check_pr_status(num)

            if status == 'green':
                if args.dry_run:
                    round_results.append((num, 'MERGE', 'dry-run', title))
                    merged += 1
                else:
                    ok, msg = merge_pr(num)
                    round_results.append((num, 'MERGED' if ok else 'FAIL', msg, title))
                    if ok:
                        merged += 1
                        git(['pull', 'origin', 'main'], check=False)

            elif status in ('red', 'conflict') and (args.fix or args.loop):
                if args.dry_run:
                    round_results.append((num, 'FIX', f'dry-run ({detail})', title))
                    fixed += 1
                else:
                    ok, msg = fix_branch(branch)
                    round_results.append((num, 'FIXED' if ok else 'FAIL', msg, title))
                    if ok:
                        fixed += 1

            else:
                round_results.append((num, 'SKIP', detail, title))

        if not args.json:
            if max_rounds > 1:
                print(f'\n=== Round {round_num} ===')
            for num, action, detail, title in round_results:
                print(f'  #{num} [{action}] {detail}  — {title}')
            print(f'  Merged: {merged}, Fixed: {fixed}, Remaining: {len(target_prs) - merged}')

        all_results.extend(round_results)

        # For scoped mode, check if all target PRs are done
        remaining = target_prs if not args.all else get_open_prs()
        if not remaining:
            if not args.json:
                print('\nAll targeted PRs merged!')
            break

        if args.loop and round_num < max_rounds and fixed > 0:
            if not args.json:
                print(f'\nWaiting {args.wait}s for CI to re-run...')
            time.sleep(args.wait)

    if args.json:
        print(json.dumps([
            {'pr': r[0], 'action': r[1], 'detail': r[2], 'title': r[3]}
            for r in all_results
        ], indent=2))

    if args.all:
        remaining = get_open_prs()
    else:
        remaining = [pr for pr in get_open_prs() if pr['number'] in args.pr_numbers]
    return 0 if not remaining else 1


if __name__ == '__main__':
    sys.exit(main())
