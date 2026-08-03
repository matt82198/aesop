#!/usr/bin/env python3
"""Merge-pipeline contention telemetry baseline.

Per merged PR via gh api: time-to-merge wall, CI attempts, fix rounds, update-branch
amplification, contended-file flag, merge route, red rate.

Derived metrics: CI-runs/merged-PR, fix-rounds/PR, contended-touch rate, median TTM,
red rate; appends state/ledger/merge-telemetry.jsonl idempotent on pr_number.

INDEX: merge_telemetry.py — D0 baseline telemetry: CI attempts/fix rounds/contended-touch per PR; CLI --since/--until/--append/--json; exit 0=success/2=fatal (gh unavailable)

Usage:
    python tools/merge_telemetry.py --since 2026-08-01 --append
    python tools/merge_telemetry.py --since 2026-08-01 --until 2026-08-02 --json
    python tools/merge_telemetry.py --since 2026-08-01 --append --json

CLI:
    --since DATE        ISO date (e.g. 2026-08-01) for search start
    --until DATE        ISO date for search end (default today)
    --append            Append to state/ledger/merge-telemetry.jsonl (idempotent)
    --json              Output machine-readable JSON on stdout
    --help              Print this message

Exit codes:
    0 = success with results
    1 = findings/errors found
    2 = fatal error (gh unavailable, auth failed, etc.)
"""

import subprocess
import json
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from statistics import median

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir


def gh(*args: str) -> dict | str | list:
    """Execute gh command and return parsed JSON or raw output.

    Exit 2 on auth failure or unavailable gh.
    """
    cmd = ["gh"] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=120
        )
    except FileNotFoundError:
        print("Error: 'gh' command not found; is GitHub CLI installed?", file=sys.stderr)
        return {"error": "gh unavailable", "rc": 2}
    except subprocess.TimeoutExpired:
        print("Error: 'gh' command timed out", file=sys.stderr)
        return {"error": "gh timeout", "rc": 2}

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Check for authentication errors
        if "authentication failed" in stderr.lower() or "not authenticated" in stderr.lower():
            print(f"Error: GitHub authentication failed", file=sys.stderr)
            return {"error": "auth failed", "rc": 2}
        return {"error": stderr, "rc": result.returncode}

    out = result.stdout.strip()
    if not out:
        return {"error": "empty output", "rc": 1}

    try:
        return json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return out


def get_merged_prs(since_date: str, until_date: str = None) -> list:
    """Fetch merged PRs from GitHub since given date.

    Uses --search with is:merged and createdDate range to find PRs merged in window.
    Returns list of PR objects with: number, title, createdAt, mergedAt, headRefOid.
    Exit 2 if gh fails.
    """
    until = until_date or datetime.now().strftime('%Y-%m-%d')

    # Search for merged PRs in date range
    search_query = f"is:merged created:{since_date}..{until}"
    result = gh("pr", "list", "--search", search_query, "--json",
                "number,title,createdAt,mergedAt,headRefOid,baseRefName,headRefName")

    if isinstance(result, dict) and "error" in result:
        if result.get("rc") == 2:
            return None  # Auth/fatal error; caller will exit 2
        # Fall back to empty list on query error
        return []

    if not isinstance(result, list):
        return []

    return result


def get_pr_runs(pr_number: int, head_ref: str) -> list:
    """Fetch all CI runs for a PR's head branch.

    Use --branch filter to get runs on the head ref.
    Returns list of run objects with: databaseId, name, createdAt, updatedAt, status, conclusion, headSha.
    """
    # gh run list queries runs on the repo; filter by branch via --branch flag
    # Note: --commit is silently ignored per the memo, so use --branch instead
    result = gh("run", "list", "--branch", head_ref, "--limit", "100",
                "--json", "databaseId,name,createdAt,updatedAt,status,conclusion,headSha")

    if isinstance(result, dict) and "error" in result:
        return []

    if not isinstance(result, list):
        return []

    return result


def get_pr_details(pr_number: int) -> dict:
    """Fetch detailed PR info: state, mergeStateStatus, commits.

    Returns PR object with state, headRefOid, baseRefOid, and commits.
    """
    result = gh("pr", "view", str(pr_number), "--json",
                "state,title,createdAt,mergedAt,mergeCommit,headRefOid,baseRefOid,commits")

    if isinstance(result, dict) and "error" in result:
        return {}

    return result


def get_merge_commits_on_head(pr_head_oid: str, pr_base_oid: str) -> int:
    """Count merge-from-main commits (merge commit ancestors on head not on base).

    Uses git rev-list to find commits on head that contain 'Merge' in subject line
    and are not reachable from base.

    Returns count of merge commits (update-branch amplification).
    """
    try:
        # Get all commits on head that are not on base
        cmd = ["git", "rev-list", f"{pr_base_oid}..{pr_head_oid}"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=10,
            cwd=Path.cwd()
        )

        if result.returncode != 0:
            return 0

        commits = result.stdout.strip().split('\n')
        if not commits or commits == ['']:
            return 0

        # Count commits with 'Merge' in subject (merge-from-main pattern)
        merge_count = 0
        for commit_sha in commits:
            if not commit_sha.strip():
                continue

            # Get commit subject
            cmd_subj = ["git", "log", "-1", "--format=%s", commit_sha]
            subj_result = subprocess.run(
                cmd_subj,
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=5,
                cwd=Path.cwd()
            )

            if subj_result.returncode == 0:
                subject = subj_result.stdout.strip()
                if "Merge" in subject.lower():
                    merge_count += 1

        return merge_count
    except (subprocess.TimeoutExpired, Exception):
        return 0


def check_contended_files(pr_number: int, pr_head_oid: str) -> bool:
    """Check if PR touched any contended files.

    Contended: tests/CLAUDE.md, tools/CLAUDE.md, .stateapi-baseline.json, README.md, RELEASE-NOTES.md
    """
    contended = [
        "tests/CLAUDE.md",
        "tools/CLAUDE.md",
        ".stateapi-baseline.json",
        "README.md",
        "RELEASE-NOTES.md"
    ]

    result = gh("pr", "view", str(pr_number), "--json", "files")

    if isinstance(result, dict) and "error" in result:
        return False

    files = result.get("files", [])
    for f in files:
        if f.get("path") in contended:
            return True

    return False


def derive_fix_rounds(runs: list) -> int:
    """Derive fix rounds from CI runs.

    A fix round is a distinct head SHA that receives a run after the first run.
    Returns count of fix rounds (distinct SHAs after first).
    """
    if not runs or len(runs) == 0:
        return 0

    # Get distinct head SHAs from runs (skip first)
    shas_ordered = []
    for run in runs:
        sha = run.get("headSha")
        if sha and (not shas_ordered or sha != shas_ordered[-1]):
            shas_ordered.append(sha)

    # Fix rounds = distinct SHAs minus 1 (the initial)
    return max(0, len(shas_ordered) - 1)


def compute_time_to_merge(created_at: str, merged_at: str) -> float:
    """Compute time-to-merge in seconds from ISO timestamps."""
    try:
        created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        merged = datetime.fromisoformat(merged_at.replace('Z', '+00:00'))
        delta = (merged - created).total_seconds()
        return max(0, delta)
    except (ValueError, AttributeError):
        return 0.0


def get_red_rate_for_pr(runs: list) -> bool:
    """Check if any run for this PR had a FAILURE conclusion."""
    for run in runs:
        if run.get("conclusion") == "failure":
            return True
    return False


def telemetry_for_pr(pr: dict) -> dict | None:
    """Compute telemetry row for one merged PR.

    Returns dict with: pr_number, time_to_merge_sec, ci_attempts, fix_rounds,
    update_branch_amplification, contended_file, merge_route, red_flag.

    Returns None if PR details unavailable.
    """
    pr_number = pr.get("number")
    created_at = pr.get("createdAt", "")
    merged_at = pr.get("mergedAt", "")
    head_ref = pr.get("headRefName", "")
    head_oid = pr.get("headRefOid", "")
    base_oid = pr.get("baseRefOid", "")

    if not pr_number or not merged_at:
        return None

    # Time to merge
    ttm = compute_time_to_merge(created_at, merged_at)

    # Fetch runs for this PR's head branch
    runs = get_pr_runs(pr_number, head_ref) if head_ref else []

    # CI attempts (number of runs)
    ci_attempts = len(runs)

    # Fix rounds
    fix_rounds = derive_fix_rounds(runs)

    # Update-branch amplification (merge commits on head)
    uba = get_merge_commits_on_head(head_oid, base_oid) if head_oid and base_oid else 0

    # Contended file flag
    contended = check_contended_files(pr_number, head_oid)

    # Merge route (infer from uba: >0 = update-branch, 0 = rebase/integration)
    merge_route = "serial" if uba > 0 else "integration"

    # Red flag (any run failed)
    red_flag = get_red_rate_for_pr(runs)

    return {
        "pr_number": pr_number,
        "title": pr.get("title", ""),
        "created_at": created_at,
        "merged_at": merged_at,
        "time_to_merge_sec": ttm,
        "ci_attempts": ci_attempts,
        "fix_rounds": fix_rounds,
        "update_branch_amplification": uba,
        "contended_file": contended,
        "merge_route": merge_route,
        "red_flag": red_flag,
    }


def load_ledger(ledger_path: Path) -> dict:
    """Load existing ledger as dict keyed by pr_number.

    Each line is a JSON object. Idempotent on pr_number means skip if already present.
    """
    ledger_entries = {}
    if not ledger_path.exists():
        return ledger_entries

    try:
        lines = ledger_path.read_text(encoding='utf-8').strip().split('\n')
        for line in lines:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and "pr_number" in obj:
                    ledger_entries[obj["pr_number"]] = obj
            except json.JSONDecodeError:
                pass  # Skip malformed lines
    except (IOError, OSError):
        pass  # Ledger doesn't exist yet

    return ledger_entries


def append_to_ledger(ledger_path: Path, rows: list):
    """Append rows to JSONL ledger, idempotent on pr_number."""
    # Load existing
    existing = load_ledger(ledger_path)

    # Filter out rows already present
    new_rows = [r for r in rows if r.get("pr_number") not in existing]

    if not new_rows:
        return  # Nothing new

    # Ensure parent directory
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    # Append new rows
    with open(ledger_path, 'a', encoding='utf-8') as f:
        for row in new_rows:
            f.write(json.dumps(row) + '\n')


def compute_derived_metrics(rows: list) -> dict:
    """Compute headline metrics from rows.

    Returns: {
        "ci_runs_per_merged_pr": float,
        "fix_rounds_per_pr": float,
        "contended_touch_rate": float (0-1),
        "median_time_to_merge_sec": float,
        "red_rate": float (0-1),
        "pr_count": int,
    }
    """
    if not rows:
        return {
            "ci_runs_per_merged_pr": 0,
            "fix_rounds_per_pr": 0,
            "contended_touch_rate": 0.0,
            "median_time_to_merge_sec": 0,
            "red_rate": 0.0,
            "pr_count": 0,
        }

    ttms = [r.get("time_to_merge_sec", 0) for r in rows if r.get("time_to_merge_sec")]
    ci_attempts = [r.get("ci_attempts", 0) for r in rows]
    fix_rounds = [r.get("fix_rounds", 0) for r in rows]
    contended = [r.get("contended_file", False) for r in rows]
    red = [r.get("red_flag", False) for r in rows]

    return {
        "ci_runs_per_merged_pr": sum(ci_attempts) / len(rows) if rows else 0,
        "fix_rounds_per_pr": sum(fix_rounds) / len(rows) if rows else 0,
        "contended_touch_rate": sum(contended) / len(rows) if rows else 0.0,
        "median_time_to_merge_sec": median(ttms) if ttms else 0,
        "red_rate": sum(red) / len(rows) if rows else 0.0,
        "pr_count": len(rows),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Merge-pipeline contention telemetry baseline",
        add_help=False
    )
    parser.add_argument("--since", required=True, help="ISO date (e.g. 2026-08-01)")
    parser.add_argument("--until", help="ISO date for end (default today)")
    parser.add_argument("--append", action="store_true", help="Append to ledger")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--help", action="store_true", help="Show help")

    try:
        args = parser.parse_args()
    except SystemExit:
        print(__doc__)
        sys.exit(2)

    if args.help:
        print(__doc__)
        sys.exit(0)

    # Fetch merged PRs
    prs = get_merged_prs(args.since, args.until)

    if prs is None:
        # Auth/fatal error
        print("Error: Failed to fetch PRs (authentication or gh unavailable)", file=sys.stderr)
        sys.exit(2)

    if not prs:
        if args.json:
            print(json.dumps({"error": "no prs found", "count": 0}, indent=2))
        else:
            print("No merged PRs found in date range")
        sys.exit(0)

    # Compute telemetry for each PR
    rows = []
    for pr in prs:
        row = telemetry_for_pr(pr)
        if row:
            rows.append(row)

    if not rows:
        print("Error: No valid PR data", file=sys.stderr)
        sys.exit(2)

    # Append to ledger if requested
    if args.append:
        ledger_path = get_state_dir() / "ledger" / "merge-telemetry.jsonl"
        append_to_ledger(ledger_path, rows)
        print(f"Appended {len(rows)} PR(s) to {ledger_path}", file=sys.stderr)

    # Compute derived metrics
    metrics = compute_derived_metrics(rows)

    if args.json:
        output = {
            "rows": rows,
            "metrics": metrics,
        }
        print(json.dumps(output, indent=2))
    else:
        # Text output
        print(f"\n=== Merge Telemetry Report ===")
        print(f"Period: {args.since} to {args.until or 'today'}")
        print(f"Merged PRs: {len(rows)}")
        print(f"\nMetrics:")
        print(f"  CI runs per merged PR:      {metrics['ci_runs_per_merged_pr']:.2f}")
        print(f"  Fix rounds per PR:          {metrics['fix_rounds_per_pr']:.2f}")
        print(f"  Contended-file touch rate:  {metrics['contended_touch_rate']*100:.1f}%")
        print(f"  Median time-to-merge:       {metrics['median_time_to_merge_sec']:.0f}s")
        print(f"  Red rate (CI failures):     {metrics['red_rate']*100:.1f}%")

    sys.exit(0)


if __name__ == "__main__":
    main()
