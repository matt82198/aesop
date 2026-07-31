#!/usr/bin/env python3
"""
Defect escape telemetry — Haiku code quality measurement.

Turns 'is Haiku producing good code' from assertion into a measured number.

Given --repo <path> --since <ISO date>, computes:
  (a) fix-forward rate: count commits matching /fix-forward|hotfix|fix(ci)|repair/i
      vs total feature commits in the window
  (b) first-try-estimate: per-merge, whether a 'fix-forward' commit followed
      the feature commit before the merge (proxy for not-first-try-green)

Output: JSON with {window, feature_commits, fixforward_commits, fixforward_rate, first_try_estimate}

Deterministic, stdlib+subprocess(git) only, no network, Windows-safe.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from subprocess_common import git


def run_git(args, repo_path, check=True):
    """
    Run git command in repo, return stdout.

    Args:
        args: list of git command args (e.g., ["log", "--oneline"])
        repo_path: path to git repo
        check: raise on non-zero exit if True

    Returns:
        stdout as string
    """
    try:
        result = git(args, cwd=str(repo_path), check=check, timeout=60)
        return result.stdout
    except subprocess.CalledProcessError as e:
        if check:
            raise RuntimeError(
                f"git command failed: {' '.join(args)}\n"
                f"stderr: {e.stderr}"
            )
        return ""
    except subprocess.TimeoutExpired:
        if check:
            raise RuntimeError(
                f"git command timed out: {' '.join(args)}"
            )
        return ""


def get_commits_since(repo_path, since_date):
    """
    Get all commits since a date using git log.

    Args:
        repo_path: path to git repo
        since_date: ISO format date string (e.g., "2026-07-01")

    Returns:
        list of commit hashes
    """
    output = run_git(
        [
            "log",
            "--all",
            f"--since={since_date}",
            "--format=%H",
        ],
        repo_path,
    )
    commits = [c.strip() for c in output.strip().split("\n") if c.strip()]
    return commits


def get_commit_subject(repo_path, commit_hash):
    """Get commit subject line."""
    output = run_git(
        ["log", "-1", "--format=%s", commit_hash],
        repo_path,
    )
    return output.strip()


def is_fixforward_commit(subject):
    """
    Check if commit subject matches fix-forward pattern.

    Matches: fix-forward, hotfix, fix(ci), repair (case-insensitive)
    """
    import re

    patterns = [
        r"fix-forward",
        r"hotfix",
        r"fix\s*\(\s*ci\s*\)",
        r"repair",
    ]
    combined = "|".join(f"({p})" for p in patterns)
    return bool(re.search(combined, subject, re.IGNORECASE))


def get_commit_parents(repo_path, commit_hash):
    """Get parent commit hashes for a commit."""
    output = run_git(
        ["log", "-1", "--format=%P", commit_hash],
        repo_path,
    )
    parents = output.strip().split() if output.strip() else []
    return parents


def compute_first_try_estimate(repo_path, commits_window):
    """
    Compute first-try-estimate: fraction of fix-forward commits that
    followed a feature commit before the merge (proxy for not-first-try-green).

    Simplified approach: for commits in the window, check if a fix-forward
    commit appears after its preceding feature commit (naive linear scan).

    Args:
        repo_path: path to git repo
        commits_window: list of commit hashes in window (reverse chronological)

    Returns:
        float 0.0-1.0 or None if no opportunities in window
    """
    if not commits_window or len(commits_window) < 2:
        return None

    # Build subject map for all commits in window
    commit_subjects = {}
    for commit in commits_window:
        try:
            subject = get_commit_subject(repo_path, commit)
            commit_subjects[commit] = subject
        except Exception:
            pass

    # Linear scan: count instances where a fix-forward follows a feature
    # (commits come in reverse chronological, so "follows" means appears later in list)
    feature_commits = [c for c in commits_window if c in commit_subjects
                       and not is_fixforward_commit(commit_subjects[c])]

    if not feature_commits:
        return None

    followed_count = 0
    for i, feature_commit in enumerate(feature_commits):
        # Check if any fix-forward appears in next few commits
        # (simple heuristic: next 3 commits after this feature)
        feature_idx = commits_window.index(feature_commit)
        for j in range(feature_idx + 1, min(feature_idx + 4, len(commits_window))):
            next_commit = commits_window[j]
            if next_commit in commit_subjects:
                if is_fixforward_commit(commit_subjects[next_commit]):
                    followed_count += 1
                    break

    return followed_count / len(feature_commits) if feature_commits else None


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Defect escape telemetry — Haiku code quality measurement"
    )
    parser.add_argument("--repo", required=True, help="Path to git repository")
    parser.add_argument(
        "--since", required=True, help="ISO format date (e.g., 2026-07-01)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output JSON (default: human-readable)"
    )

    args = parser.parse_args()

    repo_path = Path(args.repo)
    if not repo_path.exists():
        print(f"Error: repo not found: {repo_path}", file=sys.stderr)
        sys.exit(1)

    # Validate date format
    try:
        datetime.fromisoformat(args.since)
    except ValueError:
        print(f"Error: invalid date format (use ISO): {args.since}", file=sys.stderr)
        sys.exit(1)

    # Get all commits in window
    try:
        all_commits = get_commits_since(repo_path, args.since)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Count feature vs fix-forward
    feature_commits = []
    fixforward_commits = []

    for commit in all_commits:
        subject = get_commit_subject(repo_path, commit)
        if is_fixforward_commit(subject):
            fixforward_commits.append(commit)
        else:
            feature_commits.append(commit)

    # Compute rates
    total_feature = len(feature_commits)
    total_fixforward = len(fixforward_commits)
    fixforward_rate = (
        total_fixforward / total_feature if total_feature > 0 else 0.0
    )

    # Compute first-try estimate
    first_try_estimate = compute_first_try_estimate(repo_path, all_commits)

    result = {
        "window": {
            "since": args.since,
            "total_commits": len(all_commits),
        },
        "feature_commits": total_feature,
        "fixforward_commits": total_fixforward,
        "fixforward_rate": round(fixforward_rate, 4),
        "first_try_estimate": (
            round(first_try_estimate, 4) if first_try_estimate is not None else None
        ),
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Defect Escape Telemetry Report")
        print(f"  Window: since {args.since}")
        print(f"  Total commits: {len(all_commits)}")
        print(f"  Feature commits: {total_feature}")
        print(f"  Fix-forward commits: {total_fixforward}")
        print(f"  Fix-forward rate: {fixforward_rate:.2%}")
        if first_try_estimate is not None:
            print(f"  First-try estimate: {first_try_estimate:.2%}")
        else:
            print(f"  First-try estimate: N/A (no merges in window)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
