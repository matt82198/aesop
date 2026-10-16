#!/usr/bin/env python3
"""Batch auto-merge: fix conflicts, merge green PRs, loop until done.
INDEX: Batch PR merge tool (fix-by-default: merge main into broken branches + merge green PRs; `--no-fix`/`--loop`/`--dry-run`/`--json`/`--wait`); uses subprocess_common.py for timeouts + encoding; MERGED-state verification gate at lines 101-105; run with `--loop` to continuously merge all green PRs; use merge_train.py for one-shot serial CI-gated queues. `fix_branch()` auto-resolves merge conflicts by taking `--theirs` ONLY over paths listed by `generated_paths.py` -- the single registry of repo-generated files. It imports that module and calls `generated_paths()` at CALL time rather than re-typing the list, because a copy silently drifts the moment a path is registered: this tool held its own two-entry copy while PR #757 was adding `tools/INDEX.md` to the registry, and a batch conflicting on that path would have gone unresolved here. Never `git stash` (the stash stack is shared across worktrees) and never a blanket `git checkout .`; resolution stays path-by-path and registry-bounded, so an unregistered conflicted file still aborts the merge rather than being discarded. Enforced by `tests/test_auto_merge_registry.py`, which injects a sentinel into the registry and asserts this tool acts on it, plus an AST source scan failing any module under `tools/` that re-types the registry as a literal collection

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

Exit codes: 0=all merged, 1=some blocked (scoped mode: 0 only if every named PR is gh-verified MERGED), 2=error
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# Ensure this tool's own directory (tools/) is importable so the shared
# harness resolves regardless of cwd or how the file is loaded
# (the import-gate loads tools by path, without tools/ on sys.path).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Imported as a MODULE rather than `from generated_paths import
# GENERATED_PATHS`: the registry is then read at CALL time, so this tool
# tracks whatever tools/generated_paths.py currently holds instead of
# freezing a copy of it at import.
import generated_paths  # noqa: E402
from subprocess_common import gh, git, json_output

# The secret gate is this checkout's own tools/secret_scan.py (never the PR branch's copy).
SECRET_SCAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'secret_scan.py')
_CLEAN_COUNT_RE = re.compile(r'CLEAN: (\d+) files scanned')


def halt_reason():
    """Return None when the box is not halted, else the active halt's reason. Fails closed.

    Mirrors merge_train.halt_status() and merge_queue._halt_status(): consults the
    box-wide .HALT that `halt.py set` writes and the checkout-scoped one via
    halt.active_halt(). halt.py is imported here, not at module load, so an import
    failure reads as HALTED at call time instead of breaking the whole tool.
    """
    try:
        import halt as halt_module
    except Exception as exc:  # any import failure must halt
        return 'halt module unimportable (%s: %s)' % (type(exc).__name__, exc)
    try:
        active = halt_module.active_halt()
    except Exception as exc:  # any reader failure must halt, never clear
        return 'halt state unreadable (%s: %s)' % (type(exc).__name__, exc)
    if active is None:
        return None
    label, info = active
    reason = (info or {}).get('reason', '(unknown)')
    return '%s [%s]' % (reason, label)


def refuse_if_halted():
    """True, with a stderr line naming the reason, when the box is halted. Callers stop on True."""
    reason = halt_reason()
    if reason is None:
        return False
    print('auto_merge.py: HALTED: %s' % reason, file=sys.stderr)
    return True


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


def pr_merge_state(pr_num):
    """Return (state, mergedAt) for one PR from `gh pr view --json state,mergedAt`.

    Fail closed: any gh failure or unparseable output yields ('UNKNOWN', None),
    so a nonexistent PR or a gh outage can never read as merged.
    """
    r = gh(['pr', 'view', str(pr_num), '--json', 'state,mergedAt'], check=False)
    if r.returncode != 0:
        return 'UNKNOWN', None
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return 'UNKNOWN', None
    if not isinstance(data, dict):
        return 'UNKNOWN', None
    return data.get('state') or 'UNKNOWN', data.get('mergedAt')


def scoped_verdict(pr_numbers):
    """Return [(n, state)] for every requested PR not verified MERGED via GitHub."""
    unverified = []
    for n in dict.fromkeys(pr_numbers):
        state, merged_at = pr_merge_state(n)
        if state != 'MERGED' or not merged_at:
            unverified.append((n, state))
    return unverified


def _run_in(cwd, cmd, timeout=30):
    """Run a list-form command in cwd (no shell, utf-8). subprocess_common.run() takes no cwd."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout, check=False)


def _secret_gate(wt, branch):
    """Scan the fix's committed delta over origin/<branch> before push. Fails closed.

    Scans committed git objects via `secret_scan.py --range`. A non-empty delta that the
    scanner reports as 0 files inspected is NOT a pass.
    """
    base = f'origin/{branch}..HEAD'
    r = git(['diff', '--name-only', '--diff-filter=d', base], cwd=wt)
    if r.returncode != 0:
        return False, 'secret scan: cannot list fix delta'
    changed = [ln for ln in r.stdout.splitlines() if ln.strip()]
    r = _run_in(wt, [sys.executable, SECRET_SCAN, '--range', base, '--repo', wt], timeout=60)
    if r.returncode != 0:
        return False, 'secret scan failed'
    if changed:
        m = _CLEAN_COUNT_RE.search(r.stdout)
        inspected = (m is not None and int(m.group(1)) >= 1) or 'CLEAN: scanned (' in r.stdout
        if not inspected:
            return False, f'secret scan inspected 0 files for {len(changed)} changed'
    return True, 'secret scan clean'


def _fix_in_worktree(branch, wt):
    """Merge origin/main into the detached worktree at wt, fix CLAUDE.md limits, scan, push."""
    r = git(['merge', 'origin/main', '--no-edit'], cwd=wt, check=False)
    if r.returncode != 0:
        # Resolve conflicts ONLY over registered generated files -- paths a
        # committed gate deterministically rewrites, so taking "theirs" and
        # regenerating loses nothing a human authored. Read live from the
        # registry so a path added there (e.g. tools/INDEX.md) is handled
        # here without this list ever being edited again.
        for f in generated_paths.generated_paths():
            git(['checkout', '--theirs', f], cwd=wt, check=False)
            git(['add', f], cwd=wt, check=False)
        r2 = git(['-c', 'core.editor=true', 'merge', '--continue'], cwd=wt, check=False)
        if r2.returncode != 0:
            git(['merge', '--abort'], cwd=wt, check=False)
            return False, 'merge conflict unresolvable'

    # Branch-local generators: they run inside the worktree, so they rewrite only its files.
    _run_in(wt, [sys.executable, os.path.join(wt, 'tools', 'claudemd_lint.py')], timeout=30)
    _run_in(wt, [sys.executable, os.path.join(wt, 'tools', 'gen_tool_index.py'), '--regenerate'],
            timeout=30)

    # Stage only the tracked paths this fix rewrote. Never `add -A`.
    r = git(['diff', '--name-only', '-z'], cwd=wt, check=False)
    touched = [p for p in r.stdout.split('\0') if p]
    if touched:
        git(['add', '--'] + touched, cwd=wt, check=False)
    r = git(['diff', '--cached', '--quiet'], cwd=wt, check=False)
    if r.returncode != 0:
        r = git(['commit', '-m', 'fix: merge main + resolve conflicts'], cwd=wt, check=False)
        if r.returncode != 0:
            return False, 'commit failed'

    ok, detail = _secret_gate(wt, branch)
    if not ok:
        return False, detail

    r = git(['push', 'origin', f'HEAD:refs/heads/{branch}'], cwd=wt, check=False)
    if r.returncode != 0:
        return False, 'push failed'
    return True, 'fixed + pushed, CI re-triggered'


def fix_branch(branch):
    """Merge main into branch, fix CLAUDE.md limits, push. Never touches the caller's checkout.

    All branch work runs in a detached temporary worktree that is removed on exit, so the
    caller's HEAD, index and untracked WIP are never checked out, merged, pulled or staged.
    """
    git(['fetch', 'origin', 'main'], check=False)
    r = git(['fetch', 'origin', branch], check=False)
    if r.returncode != 0:
        return False, f'fetch failed for {branch}'

    scratch = tempfile.mkdtemp(prefix='auto-merge-fix-')
    wt = os.path.join(scratch, 'wt')
    try:
        r = git(['worktree', 'add', '--detach', wt, f'origin/{branch}'], check=False)
        if r.returncode != 0:
            return False, f'worktree add failed for {branch}'
        return _fix_in_worktree(branch, wt)
    finally:
        git(['worktree', 'remove', '--force', wt], check=False)
        git(['worktree', 'prune'], check=False)
        shutil.rmtree(scratch, ignore_errors=True)


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
                        help='Loop: fix -> wait -> merge until done (max 3 rounds)')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--wait', type=int, default=180,
                        help='Seconds to wait for CI between loop rounds (default 180)')
    args = parser.parse_args()

    # Box-wide kill switch first: a halted box makes no GitHub call and writes nothing to origin.
    if refuse_if_halted():
        return 1

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
                    # Re-check before every merge: a halt set mid-run must stop the next merge.
                    if refuse_if_halted():
                        return 1
                    ok, msg = merge_pr(num)
                    round_results.append((num, 'MERGED' if ok else 'FAIL', msg, title))
                    if ok:
                        merged += 1

            elif status in ('red', 'conflict') and (args.fix or args.loop):
                if args.dry_run:
                    round_results.append((num, 'FIX', f'dry-run ({detail})', title))
                    fixed += 1
                else:
                    # fix_branch pushes to origin: re-check the kill switch before writing.
                    if refuse_if_halted():
                        return 1
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
            # Scoped mode is decided by scoped_verdict() after the loop, never here.
            if args.all and not args.json:
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
        return 0 if not remaining else 1

    # Scoped mode: success means every named PR is verified MERGED on GitHub.
    unverified = scoped_verdict(args.pr_numbers)
    for n, state in unverified:
        print(f'auto_merge.py: #{n} is NOT MERGED (state={state})', file=sys.stderr)
    if not unverified and not args.json:
        print('\nAll targeted PRs merged!')
    return 0 if not unverified else 1


if __name__ == '__main__':
    sys.exit(main())
