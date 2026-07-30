#!/usr/bin/env python3
"""Tracker automatic zombie prevention and auto-close gate.

Guardrail G1: Automatically closes tracker items when evidence shows they shipped:
1. Linked PRs merge (checks via gh pr view <number> --json state)
2. Files listed in ownsFiles are fully present on origin/main

Classifies items as:
  - SHIPPED: Merged PR reference OR all ownsFiles present on origin/main
  - OPEN: No merged PR evidence AND missing ownsFiles
  - AMBIGUOUS: Partial evidence (e.g., some files shipped, PR not found)

This prevents the 79% zombie-rate problem where items are shipped but remain
in active lanes (ranked/open/in_progress), wasting triage effort.

Usage:
  tracker_autoclose.py [--check | --apply]
  tracker_autoclose.py [--json]
  tracker_autoclose.py --help

Modes:
  --check (default)
    Check for items eligible for auto-close. DRY RUN — no modifications.
    Exits 0 if no closable items, 1 if closable items found.

  --apply
    Auto-close items with SHIPPED evidence. Records evidence in journal.
    Exits 0 on success, 1 if closable items found.

Output:
  --json    Machine-readable JSON output (default: text report)

Environment:
  AESOP_STATE_ROOT: Directory containing tracker.json, journal.jsonl
                    Defaults to ./state

Exit codes:
  0: Success (check: no closable / apply: applied all)
  1: Closable items found (check) or error (apply)
  2: Error (missing deps, unknown flags)
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Import common utilities
sys.path.insert(0, str(Path(__file__).parent))
import common

# Import the read_api facade
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
from state_store.read_api import ReadAPI


def get_tracker_path(state_root=None):
    """Return path to tracker.json."""
    if state_root:
        return Path(state_root) / "tracker.json"
    return common.get_state_dir() / "tracker.json"


def get_journal_path(state_root=None):
    """Return path to the evidence journal."""
    if state_root:
        return Path(state_root) / "tracker-journal.jsonl"
    return common.get_state_dir() / "tracker-journal.jsonl"


def read_tracker(state_root=None):
    """Read tracker.json using the read_api facade. Returns empty dict if missing."""
    state_dir = state_root or common.get_state_dir()
    api = ReadAPI(state_dir)
    snapshot = api.read_tracker_snapshot()
    # Return None if empty (for backward compatibility with callers checking None)
    return snapshot if snapshot else None


def write_tracker(tracker_data, state_root=None):
    """Write tracker.json."""
    tracker_path = get_tracker_path(state_root)
    tracker_path.write_text(json.dumps(tracker_data, indent=2), encoding="utf-8")


def append_journal(evidence_entry, state_root=None):
    """Append an evidence entry to the journal."""
    journal_path = get_journal_path(state_root)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with open(journal_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(evidence_entry, ensure_ascii=True) + "\n")


def extract_pr_numbers(text):
    """Extract PR numbers from text (e.g., 'PR #123' or 'PR 456' or '#789').

    Returns a list of unique PR numbers as strings.
    """
    if not text:
        return []

    # Match patterns like #123, PR #456, PR 789
    patterns = [
        r"#(\d+)",  # #123
        r"PR\s+#?(\d+)",  # PR #123 or PR 123
        r"pr\s+#?(\d+)",  # pr #123 or pr 123
    ]

    pr_numbers = set()
    for pattern in patterns:
        matches = re.findall(pattern, text)
        pr_numbers.update(matches)

    return sorted(list(pr_numbers))


def check_pr_merged(pr_number, skip_gh=False):
    """Check if a PR is MERGED via gh pr view.

    Returns (is_merged: bool, gh_available: bool, error: str or None)
    """
    if skip_gh:
        return False, False, "gh not available or disabled"

    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False, True, None  # gh works, PR just not merged
        state = result.stdout.strip()
        return state == "MERGED", True, None
    except FileNotFoundError:
        return False, False, "gh not found on PATH"
    except subprocess.TimeoutExpired:
        return False, True, "gh timeout"
    except Exception as e:
        return False, True, str(e)


def check_files_on_main(owns_files, skip_git=False):
    """Check if all ownsFiles exist on origin/main.

    Returns (all_present: bool, git_available: bool, details: dict)
    """
    if not owns_files:
        return None, True, {}  # None = no ownsFiles to check

    if skip_git:
        return None, False, {"error": "git not available"}

    if isinstance(owns_files, str):
        owns_files = [owns_files]

    details = {}
    try:
        for file_path in owns_files:
            try:
                result = subprocess.run(
                    ["git", "cat-file", "-e", f"origin/main:{file_path}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                present = result.returncode == 0
                details[file_path] = present
            except (subprocess.TimeoutExpired, Exception) as e:
                details[file_path] = False
                details[f"{file_path}_error"] = str(e)

        all_present = all(v for k, v in details.items() if not k.endswith("_error"))
        return all_present, True, details

    except FileNotFoundError:
        return None, False, {"error": "git not found on PATH"}
    except Exception as e:
        return None, True, {"error": str(e)}


def is_active_status(status):
    """Check if item is in an active status (not yet complete)."""
    return status in ("ranked", "open", "in_progress", "proposed", "accepted")


def classify_item(item, skip_gh=False, skip_git=False):
    """Classify an item as SHIPPED, OPEN, or AMBIGUOUS.

    Returns (classification, evidence_string).
    """
    item_id = item.get("id", "?")
    status = item.get("status")
    notes = item.get("notes", "")
    pr_link = item.get("pr_link", "")
    owns_files = item.get("ownsFiles")
    title = item.get("title", "")

    # Skip already-done items
    if not is_active_status(status):
        return "SKIP", "already done/rejected"

    evidence_parts = []

    # Check for merged PR
    pr_numbers = extract_pr_numbers(pr_link) or extract_pr_numbers(notes) or extract_pr_numbers(title)

    pr_evidence = None
    if pr_numbers:
        for pr_num in pr_numbers:
            is_merged, gh_ok, error = check_pr_merged(pr_num, skip_gh=skip_gh)
            if is_merged:
                pr_evidence = f"PR #{pr_num} merged"
                evidence_parts.append(pr_evidence)
                break
            elif not gh_ok and not skip_gh:
                # gh not available; report it but don't fail
                evidence_parts.append(f"PR #{pr_num} check skipped (gh unavailable: {error})")

    # Check for files on main
    files_on_main, git_ok, file_details = check_files_on_main(owns_files, skip_git=skip_git)

    if files_on_main is True:
        evidence_parts.append(f"all ownsFiles present on origin/main")
    elif files_on_main is False and git_ok:
        missing = [k for k, v in file_details.items() if not v and not k.endswith("_error")]
        if missing:
            evidence_parts.append(f"files missing: {missing}")

    # Classify
    has_pr_evidence = pr_evidence is not None
    has_file_evidence = files_on_main is True

    if has_pr_evidence or has_file_evidence:
        return "SHIPPED", " | ".join(evidence_parts)
    elif evidence_parts and any("unavailable" in p or "missing" in p for p in evidence_parts):
        return "AMBIGUOUS", " | ".join(evidence_parts)
    else:
        return "OPEN", " | ".join(evidence_parts) if evidence_parts else "no evidence"


def autoclose_items(tracker_data, apply=False, skip_gh=False, skip_git=False, state_root=None):
    """Classify and optionally close items with SHIPPED evidence.

    Returns (shipped_items, open_items, ambiguous_items, report_lines).
    """
    items = tracker_data.get("items", [])
    shipped_items = []
    open_items = []
    ambiguous_items = []
    report_lines = []

    for item in items:
        item_id = item.get("id")
        status = item.get("status")

        classification, evidence = classify_item(
            item, skip_gh=skip_gh, skip_git=skip_git
        )

        if classification == "SKIP":
            continue
        elif classification == "SHIPPED":
            shipped_items.append({"id": item_id, "status": status, "evidence": evidence})
            if apply:
                item["status"] = "done"
                item["completed_at"] = datetime.now(timezone.utc).isoformat()
                # Append to notes
                notes = item.get("notes", "")
                reconcile_note = f"RECONCILED: {evidence}"
                if notes:
                    item["notes"] = f"{notes} | {reconcile_note}"
                else:
                    item["notes"] = reconcile_note

                # Record in journal
                journal_entry = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "id": item_id,
                    "status": status,
                    "evidence": evidence,
                    "closed_at": item["completed_at"],
                }
                append_journal(journal_entry, state_root=state_root)

            report_lines.append(f"SHIPPED {item_id}: {evidence} (was {status})")

        elif classification == "OPEN":
            open_items.append({"id": item_id, "status": status})
            report_lines.append(f"OPEN {item_id}: {evidence}")

        elif classification == "AMBIGUOUS":
            ambiguous_items.append({"id": item_id, "status": status, "evidence": evidence})
            report_lines.append(f"AMBIGUOUS {item_id}: {evidence}")

    return shipped_items, open_items, ambiguous_items, report_lines


def print_help():
    """Print usage information."""
    print(__doc__)


def main(argv=None, state_root=None):
    """Main entry point.

    Args:
        argv: Command-line arguments (default: sys.argv[1:])
        state_root: Override state directory path (for testing)

    Returns:
        0 if success (check: no closable / apply: applied all)
        1 if closable items found (check) or error
        2 if error (missing args, unknown flags)
    """
    if argv is None:
        argv = sys.argv[1:]

    # Parse flags
    mode = "check"  # default mode
    json_output = False
    skip_gh = False
    skip_git = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--help", "-h"):
            print_help()
            return 0
        elif arg == "--check":
            mode = "check"
        elif arg == "--apply":
            mode = "apply"
        elif arg == "--json":
            json_output = True
        elif arg == "--skip-gh":
            skip_gh = True
        elif arg == "--skip-git":
            skip_git = True
        elif arg.startswith("--"):
            print(f"ERROR: unknown flag: {arg}", file=sys.stderr)
            return 2
        i += 1

    # Read tracker
    tracker = read_tracker(state_root)
    if tracker is None:
        result = {"shipped": 0, "open": 0, "ambiguous": 0, "applied": False}
        if json_output:
            print(json.dumps(result, indent=2))
        else:
            print("INFO: tracker.json not found, nothing to check")
        return 0

    # Classify items
    shipped, open_items, ambiguous, report_lines = autoclose_items(
        tracker, apply=(mode == "apply"), skip_gh=skip_gh, skip_git=skip_git, state_root=state_root
    )

    # Print report
    if not json_output:
        for line in report_lines:
            print(line)

    # Write tracker if changes were made
    if mode == "apply" and len(shipped) > 0:
        write_tracker(tracker, state_root)

    # Prepare result
    result = {
        "shipped": len(shipped),
        "open": len(open_items),
        "ambiguous": len(ambiguous),
        "applied": mode == "apply" and len(shipped) > 0,
        "items": {
            "shipped": shipped,
            "open": open_items,
            "ambiguous": ambiguous,
        } if json_output else None,
    }

    if json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nSummary: {len(shipped)} shipped, {len(open_items)} open, {len(ambiguous)} ambiguous")

    # Exit with appropriate code
    if mode == "check":
        return 1 if len(shipped) > 0 else 0
    elif mode == "apply":
        return 0  # apply always succeeds
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())
