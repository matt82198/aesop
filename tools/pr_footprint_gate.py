#!/usr/bin/env python3
"""
Guard against PR footprint explosion: detects oversized or fixture-polluted changes.

INDEX: pr_footprint_gate: fails PRs with >100 files or >5000 deletions unless labeled `big-change`, or PRs with fixture-pattern commits (test fixtures committed into lane worktrees); pass on chore(stats)-only commits

This gate prevents lane PRs from auto-merging massive changesets that indicate:
  1. Test fixtures accidentally committed into a lane worktree (detectable by commit subjects)
  2. Oversized refactors or bulk imports without explicit review (files > 100 or deletions > 5000)

Commit subject patterns indicating fixture pollution (case-sensitive):
  - "Initial" / "Initial commit"
  - "fixture" / "base" / "seed" / "baseline" / "change"
  - "lane a adds ..." / "lane a appends ..." / "lane b adds ..." / "lane b appends ..."

These patterns are the signature of test fixtures auto-committed by lane worktrees that
accidentally included test data in their changeset.

CLI:
  --base SHA              Merge base (git sha)
  --head SHA              PR head (git sha)
  --labels JSON           Array of PR label objects: [{"name": "label1"}, ...]
                          If "big-change" is present, bypass size limits

Exit: 0=pass, 1=fail (oversized or fixture-polluted), 2=error
"""

import sys
import json
import re
import subprocess


FIXTURE_PATTERNS = [
    r'^Initial$',
    r'^Initial commit$',
    r'^fixture',
    r'^base$',
    r'^seed$',
    r'^baseline$',
    r'^change$',
    r'^lane [ab] (adds|appends).*$',
]
FIXTURE_REGEX = re.compile('|'.join(f'({p})' for p in FIXTURE_PATTERNS))

FILE_LIMIT = 100
DELETION_LIMIT = 5000


def get_commits(base_sha, head_sha, repo_cwd=None):
    """Get list of commit subjects in base..head range."""
    cmd = ['git', 'rev-list', '--pretty=%s', f'{base_sha}..{head_sha}']
    result = subprocess.run(
        cmd,
        cwd=repo_cwd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )
    if result.returncode != 0:
        raise RuntimeError(f"git rev-list failed: {result.stderr}")

    lines = result.stdout.strip().split('\n')
    # Format is: commit <sha>\n<subject>, repeat
    subjects = []
    for line in lines:
        if not line.startswith('commit '):
            subjects.append(line)
    return subjects


def get_diff_stats(base_sha, head_sha, repo_cwd=None):
    """Get file count and deletion count via git diff --numstat."""
    cmd = ['git', 'diff', '--numstat', f'{base_sha}...{head_sha}']
    result = subprocess.run(
        cmd,
        cwd=repo_cwd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff --numstat failed: {result.stderr}")

    files = set()
    total_deletions = 0

    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) < 3:
            continue

        _, deletions_str, filepath = parts[0], parts[1], parts[2]
        try:
            deletions = int(deletions_str) if deletions_str != '-' else 0
        except ValueError:
            continue

        files.add(filepath)
        total_deletions += deletions

    return len(files), total_deletions


def has_big_change_label(labels_json):
    """Check if 'big-change' label is present."""
    if not labels_json:
        return False
    try:
        labels = json.loads(labels_json)
        if not isinstance(labels, list):
            return False
        for label in labels:
            if isinstance(label, dict) and label.get('name') == 'big-change':
                return True
    except (json.JSONDecodeError, TypeError):
        pass
    return False


def is_chore_stats_only(subjects):
    """Check if all commits are chore(stats) commits."""
    if not subjects:
        return False
    for subject in subjects:
        if not subject.startswith('chore(stats):'):
            return False
    return True


def has_fixture_commit(subjects):
    """Check if any commit subject matches fixture patterns."""
    for subject in subjects:
        if FIXTURE_REGEX.match(subject):
            return True
    return False


def main():
    """Main gate logic."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='Base commit SHA')
    parser.add_argument('--head', required=True, help='Head commit SHA')
    parser.add_argument('--labels', help='PR labels as JSON array')
    args = parser.parse_args()

    try:
        commits = get_commits(args.base, args.head)
        files_changed, deletions = get_diff_stats(args.base, args.head)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # Check for fixture pollution first (always fail, no bypass)
    if has_fixture_commit(commits):
        print("FAIL: PR contains fixture-pattern commit (detected test fixture pollution)")
        return 1

    # Check if labeled for big changes
    if has_big_change_label(args.labels):
        print(f"PASS: PR labeled 'big-change' (bypass size limits) — {files_changed} files, {deletions} deletions")
        return 0

    # Check if chore(stats) only (auto-bypass)
    if is_chore_stats_only(commits):
        print(f"PASS: chore(stats)-only PR (auto-bypass) — {files_changed} files, {deletions} deletions")
        return 0

    # Check size limits
    if files_changed > FILE_LIMIT:
        print(f"FAIL: {files_changed} files > {FILE_LIMIT} limit (use big-change label for review)")
        return 1

    if deletions > DELETION_LIMIT:
        print(f"FAIL: {deletions} deletions > {DELETION_LIMIT} limit (use big-change label for review)")
        return 1

    print(f"PASS: {files_changed} files, {deletions} deletions (within limits)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
