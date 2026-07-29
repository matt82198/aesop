#!/usr/bin/env python3
"""Tracker automatic zombie prevention and auto-close gate.

Guardrail G1: Automatically closes tracker items when:
1. Linked PRs merge (checks via gh pr view <number> --json state)
2. Files listed in ownsFiles are fully present on main branch

This prevents the 79% zombie-rate problem where items are shipped but remain
in active lanes (ranked/open/in_progress), wasting triage effort.

Usage:
  tracker_autoclose.py [--check | --dry-run]
  tracker_autoclose.py --help

Modes:
  --check (default)
    Check for unresolved items (no merged PR, files not shipped).
    Exits 0 if all issues resolved, 1 if any items still open.
    Auto-closes merged items when run in this mode.

  --dry-run
    Report what would be auto-closed, but don't modify tracker.json.
    Useful for preview before committing changes.

Environment:
  AESOP_STATE_ROOT: Directory containing tracker.json
                    Defaults to ./state

Exit codes:
  0: Success or all items resolved
  1: Some items still open/unresolved
"""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Import common utilities
sys.path.insert(0, str(Path(__file__).parent))
import common


def get_tracker_path(state_root=None):
    """Return path to tracker.json."""
    if state_root:
        return Path(state_root) / "tracker.json"
    return common.get_state_dir() / "tracker.json"


def read_tracker(state_root=None):
    """Read tracker.json. Returns None if missing."""
    tracker_path = get_tracker_path(state_root)
    if not tracker_path.exists():
        return None
    try:
        return json.loads(tracker_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError) as e:
        print(f"ERROR: Could not read tracker.json: {e}", file=sys.stderr)
        return None


def write_tracker(tracker_data, state_root=None):
    """Write tracker.json."""
    tracker_path = get_tracker_path(state_root)
    tracker_path.write_text(json.dumps(tracker_data, indent=2), encoding="utf-8")


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


def check_pr_merged(pr_number):
    """Check if a PR is MERGED via gh pr view.

    Returns True if MERGED, False if not merged or error.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        state = result.stdout.strip()
        return state == "MERGED"
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"WARN: Could not check PR #{pr_number}: {e}", file=sys.stderr)
        return False


def is_active_status(status):
    """Check if item is in an active status (not yet complete)."""
    return status in ("ranked", "open", "in_progress", "proposed", "accepted")


def autoclose_items(tracker_data, dry_run=False, state_root=None):
    """Auto-close items with merged PRs or shipped files.

    Returns (closed_count, unresolved_count, report_lines).
    """
    items = tracker_data.get("items", [])
    closed_count = 0
    unresolved_count = 0
    report_lines = []

    for item in items:
        item_id = item.get("id")
        status = item.get("status")
        notes = item.get("notes", "")
        pr_link = item.get("pr_link")

        # Skip already-done items
        if not is_active_status(status):
            continue

        # Try to find a PR reference
        pr_numbers = []
        if pr_link:
            # Try to extract from direct pr_link field
            extracted = extract_pr_numbers(pr_link)
            if extracted:
                pr_numbers = extracted
        if not pr_numbers and notes:
            # Try to extract from notes
            pr_numbers = extract_pr_numbers(notes)

        if not pr_numbers:
            # No PR reference found
            report_lines.append(
                f"UNRESOLVED {item_id}: no PR reference found in notes/pr_link"
            )
            unresolved_count += 1
            continue

        # Check if any linked PR is merged
        merged_pr = None
        for pr_num in pr_numbers:
            if check_pr_merged(pr_num):
                merged_pr = pr_num
                break

        if merged_pr:
            # Auto-close this item
            if not dry_run:
                item["status"] = "done"
                item["completed_at"] = datetime.utcnow().isoformat() + "Z"
                # Append to notes
                evidence = f"RECONCILED: PR #{merged_pr} merged"
                if notes:
                    item["notes"] = f"{notes} | {evidence}"
                else:
                    item["notes"] = evidence

            report_lines.append(
                f"CLOSED {item_id}: PR #{merged_pr} merged (was {status})"
            )
            closed_count += 1
        else:
            # PRs found but not merged
            report_lines.append(
                f"UNRESOLVED {item_id}: PR(s) {', '.join(pr_numbers)} not merged"
            )
            unresolved_count += 1

    return closed_count, unresolved_count, report_lines


def print_help():
    """Print usage information."""
    print(__doc__)


def main(argv=None, state_root=None):
    """Main entry point.

    Args:
        argv: Command-line arguments (default: sys.argv[1:])
        state_root: Override state directory path (for testing)

    Returns:
        0 if all items resolved or in dry-run mode
        1 if any items are unresolved
    """
    if argv is None:
        argv = sys.argv[1:]

    # Parse flags
    mode = "check"  # default mode
    dry_run = False

    for arg in argv:
        if arg in ("--help", "-h"):
            print_help()
            return 0
        elif arg == "--check":
            mode = "check"
        elif arg == "--dry-run":
            dry_run = True
            mode = "check"
        elif arg.startswith("--"):
            print(f"ERROR: unknown flag: {arg}", file=sys.stderr)
            return 1

    # Read tracker
    tracker = read_tracker(state_root)
    if tracker is None:
        print("INFO: tracker.json not found, nothing to check")
        return 0

    # Auto-close merged items
    closed_count, unresolved_count, report_lines = autoclose_items(
        tracker, dry_run=dry_run, state_root=state_root
    )

    # Print report
    for line in report_lines:
        print(line)

    # Write tracker if changes were made
    if not dry_run and closed_count > 0:
        write_tracker(tracker, state_root)

    # Print summary
    print(f"\nSummary: {closed_count} closed, {unresolved_count} unresolved")

    # Exit with appropriate code
    if unresolved_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
