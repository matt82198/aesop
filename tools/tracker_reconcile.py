#!/usr/bin/env python3
"""Tracker zombie detection and reconciliation tool.
INDEX: Tracker zombie reconciliation tool (detects shipped-but-open items)

Scans the tracker for zombies — items marked open/in_progress whose linked PRs
have already merged or whose item ID / title keywords appear in merged commits.
Reports findings and optionally auto-closes them via state_store events.

Usage:
  tracker_reconcile.py [--fix] [--json] [--root DIR]
  tracker_reconcile.py --help

Flags:
  --fix     Auto-close detected zombies (append item_updated event via StateAPI)
  --json    Machine-readable JSON output
  --root    Override repo root directory (default: auto-detect from script location)

Exit codes:
  0: Clean (no zombies) or zombies fixed (--fix)
  1: Zombies found (without --fix)
  2: Error (missing deps, unknown flags)
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap imports
sys.path.insert(0, str(Path(__file__).parent))
import common

repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

ACTIVE_STATUSES = {"ranked", "open", "in_progress", "proposed", "accepted"}


def _load_tracker(state_dir):
    """Load tracker data via ReadAPI. Returns None if no data."""
    from state_store.read_api import ReadAPI
    api = ReadAPI(state_dir)
    snap = api.read_tracker_snapshot()
    return snap if snap else None


def _extract_pr_numbers(text):
    """Extract PR numbers from text fields. Returns list of str."""
    if not text:
        return []
    nums = set()
    for m in re.finditer(r"#(\d+)", text):
        nums.add(m.group(1))
    for m in re.finditer(r"PR\s+(\d+)", text, re.IGNORECASE):
        nums.add(m.group(1))
    return sorted(nums)


def _check_pr_merged(pr_number):
    """Check if a PR is MERGED via gh CLI. Returns True/False."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "state", "--jq", ".state"],
            capture_output=True, text=True, encoding='utf-8', timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip() == "MERGED"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _check_git_evidence(item_id, title, repo_dir):
    """Search git log for commits mentioning item_id or title keywords.

    Returns the matching commit summary line, or None.
    """
    queries = [item_id]
    # Add title keywords (3+ chars, skip common words)
    if title:
        skip = {"the", "and", "for", "with", "from", "into", "this", "that", "add", "fix"}
        words = [w for w in re.findall(r"[a-zA-Z0-9_-]{3,}", title) if w.lower() not in skip]
        if words:
            queries.append(" ".join(words[:3]))

    for query in queries:
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--all", "-1", "--grep", query],
                capture_output=True, text=True, encoding='utf-8', timeout=10,
                cwd=str(repo_dir),
            )
            line = result.stdout.strip()
            if result.returncode == 0 and line:
                return line
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    return None


def _close_zombie(item, state_dir, evidence):
    """Close a zombie by appending an item_updated event via StateAPI."""
    from state_store.api import StateAPI
    db_path = str(Path(state_dir) / common.STATE_DB_FILENAME)
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "id": item["id"],
        "status": "done",
        "completed_at": now,
        "notes": (item.get("notes") or "") + f" | RECONCILED: {evidence}",
    }
    try:
        api = StateAPI(db_path)
        api.append("tracker", "item_updated", payload, actor="tracker_reconcile")
        api.close()
    except Exception as exc:
        print(f"WARN: event append failed for {item['id']}: {exc}", file=sys.stderr)


def reconcile(state_dir, repo_dir, fix=False):
    """Run reconciliation. Returns (zombies, genuinely_open) lists of dicts."""
    tracker = _load_tracker(state_dir)
    if tracker is None:
        return [], []

    items = tracker.get("items", [])
    zombies = []
    genuinely_open = []

    for item in items:
        status = item.get("status", "")
        if status not in ACTIVE_STATUSES:
            continue

        item_id = item.get("id", "")
        title = item.get("title", "")
        notes = item.get("notes", "")
        pr_link = item.get("pr_link", "")

        # Collect PR numbers from all text fields
        pr_nums = _extract_pr_numbers(pr_link) or _extract_pr_numbers(notes) or _extract_pr_numbers(title)

        evidence = None

        # Check PRs first
        for pr in pr_nums:
            if _check_pr_merged(pr):
                evidence = f"PR #{pr} merged"
                break

        # Fall back to git log search
        if not evidence:
            commit_line = _check_git_evidence(item_id, title, repo_dir)
            if commit_line:
                evidence = f"commit found: {commit_line[:60]}"

        if evidence:
            entry = {"id": item_id, "title": title, "status": status, "evidence": evidence}
            zombies.append(entry)
            if fix:
                _close_zombie(item, state_dir, evidence)
        else:
            genuinely_open.append({"id": item_id, "title": title, "status": status})

    return zombies, genuinely_open


def main(argv=None):
    """CLI entry point."""
    if argv is None:
        argv = sys.argv[1:]

    fix = False
    json_out = False
    root = None

    for arg in argv:
        if arg in ("--help", "-h"):
            print(__doc__)
            return 0
        elif arg == "--fix":
            fix = True
        elif arg == "--json":
            json_out = True
        elif arg.startswith("--root"):
            # Handle --root DIR or --root=DIR
            if "=" in arg:
                root = arg.split("=", 1)[1]
            else:
                idx = argv.index(arg)
                if idx + 1 < len(argv):
                    root = argv[idx + 1]
                else:
                    print("ERROR: --root requires a directory argument", file=sys.stderr)
                    return 2
        elif arg.startswith("--") and arg != root:
            print(f"ERROR: unknown flag: {arg}", file=sys.stderr)
            return 2

    repo_dir = Path(root) if root else repo_root
    state_dir = os.environ.get("AESOP_STATE_ROOT") or str(repo_dir / "state")

    zombies, genuinely_open = reconcile(state_dir, repo_dir, fix=fix)

    if json_out:
        result = {
            "zombies": zombies,
            "genuinely_open": genuinely_open,
            "fixed": fix and len(zombies) > 0,
        }
        print(json.dumps(result, indent=2))
    else:
        if not zombies and not genuinely_open:
            print("No tracker data or no active items.")
            return 0
        if zombies:
            verb = "FIXED" if fix else "ZOMBIE"
            for z in zombies:
                print(f"{verb} {z['id']}: {z['evidence']} (was {z['status']})")
        if genuinely_open:
            print(f"\nGenuinely open: {len(genuinely_open)}")
            for o in genuinely_open:
                print(f"  OPEN {o['id']}: {o['title']} ({o['status']})")
        print(f"\nSummary: {len(zombies)} zombie(s), {len(genuinely_open)} open")

    if zombies and not fix:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
