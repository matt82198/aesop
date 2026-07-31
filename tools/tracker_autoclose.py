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
    """Extract PR numbers from text with whole-token matching.

    Matches #123 (word boundary required), PR #456, PR 456, pr #789, etc.
    Returns list of (pr_number_int, matched_text) tuples for traceability.

    Uses word boundaries to prevent #1 from matching inside #594.
    """
    if not text:
        return []

    # Match patterns with word boundaries to prevent prefix matching:
    # #123\b ensures #1 doesn't match inside #594
    # (?:PR|pr)\s+#?(\d+)\b for PR XXX pattern
    patterns = [
        (r"#(\d+)\b", "hash"),           # #123 (word boundary)
        (r"(?:PR|pr)\s+#?(\d+)\b", "pr"),  # PR #123 or PR 123
    ]

    pr_refs = []  # List of (number_int, matched_text, pattern_type)
    for pattern, ptype in patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            pr_num_str = match.group(1)
            pr_num_int = int(pr_num_str)
            pr_refs.append((pr_num_int, match.group(0), ptype))

    # Deduplicate by PR number and return sorted
    unique = {}
    for num, text, ptype in pr_refs:
        if num not in unique:
            unique[num] = (num, text, ptype)

    return sorted(unique.values(), key=lambda x: x[0])


def check_pr_merged(pr_number, skip_gh=False):
    """Check if a PR is MERGED via gh pr view and get merge timestamp.

    Returns (is_merged: bool, merged_at: ISO str or None, gh_available: bool, error: str or None)
    """
    if skip_gh:
        return False, None, False, "gh not available or disabled"

    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "state,mergedAt"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False, None, True, None  # gh works, PR just not found/not merged

        try:
            data = json.loads(result.stdout.strip())
            is_merged = data.get("state") == "MERGED"
            merged_at = data.get("mergedAt") if is_merged else None
            return is_merged, merged_at, True, None
        except json.JSONDecodeError:
            return False, None, True, f"JSON parse error: {result.stdout}"

    except FileNotFoundError:
        return False, None, False, "gh not found on PATH"
    except subprocess.TimeoutExpired:
        return False, None, True, "gh timeout"
    except Exception as e:
        return False, None, True, str(e)


def is_causally_valid(item_created_at, pr_merged_at):
    """Check if PR merge timestamp is after item creation (causality guard).

    Returns (is_valid: bool, explanation: str)

    Fail-closed: missing timestamps cannot pass causality check.
    """
    if not item_created_at:
        return False, "item creation time missing (cannot verify causality)"

    if not pr_merged_at:
        return False, "PR merge time unavailable (cannot verify causality)"

    try:
        # Parse ISO format timestamps
        item_time = datetime.fromisoformat(item_created_at.replace('Z', '+00:00'))
        pr_time = datetime.fromisoformat(pr_merged_at.replace('Z', '+00:00'))

        if pr_time > item_time:
            return True, f"merged after creation ({pr_time.date()} > {item_time.date()})"
        else:
            return False, f"merged BEFORE creation ({pr_time.date()} <= {item_time.date()})"
    except (ValueError, AttributeError) as e:
        return False, f"timestamp parse error: {e}"


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

    Evidence-based with whole-token PR matching, causality guard, and directionality checks.
    Returns (classification, evidence_string).
    """
    item_id = item.get("id", "?")
    status = item.get("status")
    notes = item.get("notes", "")
    pr_link = item.get("pr_link", "")
    owns_files = item.get("ownsFiles")
    title = item.get("title", "")
    created_at = item.get("created_at")

    # Skip already-done items
    if not is_active_status(status):
        return "SKIP", "already done/rejected"

    evidence_parts = []

    # Check for merged PR with whole-token matching and causality guard
    pr_refs_all = []

    # Extract from all fields
    for text_field in [pr_link, notes, title]:
        if text_field:
            refs = extract_pr_numbers(text_field)
            pr_refs_all.extend(refs)

    pr_evidence = None
    if pr_refs_all:
        # Deduplicate by PR number
        seen_prs = {}
        for pr_num, matched_text, ptype in pr_refs_all:
            if pr_num not in seen_prs:
                seen_prs[pr_num] = (pr_num, matched_text, ptype)

        for pr_num, matched_text, ptype in sorted(seen_prs.values(), key=lambda x: x[0]):
            is_merged, merged_at, gh_ok, error = check_pr_merged(pr_num, skip_gh=skip_gh)

            if is_merged:
                # Causality guard: PR must merge AFTER item creation
                causally_valid, causality_detail = is_causally_valid(created_at, merged_at)

                if causally_valid:
                    # Evidence string with all details
                    pr_evidence = f"PR #{pr_num} merged ({ptype}-match '{matched_text}', {causality_detail})"
                    evidence_parts.append(pr_evidence)
                    break
                else:
                    # PR merged before item existed — excluded by causality
                    evidence_parts.append(
                        f"PR #{pr_num} excluded by causality ({causality_detail})"
                    )
            elif not gh_ok and not skip_gh:
                # gh not available; report but don't fail
                evidence_parts.append(
                    f"PR #{pr_num} check skipped (gh unavailable: {error})"
                )

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
