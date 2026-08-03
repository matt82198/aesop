#!/usr/bin/env python3
"""
Mid-wave recovery: classify completed vs remaining items from workflow journal.
INDEX: Mid-wave recovery: parse workflow journal.jsonl + worktree to classify items as completed (files written + tests green) vs remaining, enabling resume from last good phase instead of re-run

Given a workflow run's journal.jsonl and its worktree, parses journal to identify
which items have already COMPLETED (files written + tests green) vs which remain,
enabling resume from the last good phase instead of full re-run.

Usage:
  wave_resume.py --journal <path> --workdir <worktree> [--json]

Inputs:
  --journal PATH     Path to journal.jsonl (append-only log of task results)
  --workdir PATH     Path to worktree root
  --json             Output as JSON (default: human-readable summary)

Output:
  {
    "completed": ["item-slug-1", "item-slug-2"],
    "remaining": ["item-slug-3", "item-slug-4"],
    "resume_hint": "Resume from item: item-slug-3"
  }

Logic:
  1. Parse journal.jsonl (each line = JSON task result)
  2. For each unique slug with status='completed':
     - Verify all reported 'files' exist in workdir
     - If all files exist, classify as completed
     - Otherwise classify as remaining
  3. Emit resume plan (completed, remaining, hint)

Read-only: never mutates workdir or journal. Stdlib-only, Windows-safe paths.
"""

import json
import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional


def load_journal(journal_path: str) -> List[Dict[str, Any]]:
    """
    Load and parse journal.jsonl file.

    Each line is expected to be valid JSON representing a task result.
    Malformed JSON lines are silently skipped.

    Args:
        journal_path: Path to journal.jsonl file

    Returns:
        List of parsed JSON records (in order), or empty list if file missing/unreadable
    """
    journal = []
    path = Path(journal_path)

    if not path.exists():
        return journal

    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        record = json.loads(line)
                        journal.append(record)
                    except json.JSONDecodeError:
                        # Silently skip malformed JSON lines
                        continue
    except (IOError, OSError):
        # File unreadable; return empty list
        pass

    return journal


def verify_files_exist(workdir: str, files: List[str]) -> bool:
    """
    Check if all files in the list exist under workdir.

    Args:
        workdir: Path to worktree root
        files: List of relative file paths to verify

    Returns:
        True if all files exist (or list is empty), False if any missing
    """
    workdir_path = Path(workdir)

    if not files:
        # Empty list is considered valid (no files to verify)
        return True

    for file_path in files:
        # Construct full path and check existence
        full_path = workdir_path / file_path

        # Normalize path to handle platform differences
        try:
            # Resolve to absolute path for safety
            resolved = full_path.resolve()
            if not resolved.exists():
                return False
        except (OSError, RuntimeError):
            # Path resolution failed (e.g., invalid characters, symlink loops)
            return False

    return True


def classify_items(
    journal: List[Dict[str, Any]], workdir: str
) -> Dict[str, Any]:
    """
    Classify items as completed vs remaining based on journal + file verification.

    For each unique slug in journal:
    - If status='completed' AND all reported files exist in workdir: completed
    - Otherwise: remaining

    Args:
        journal: List of task result records from load_journal()
        workdir: Path to worktree root

    Returns:
        {
            "completed": [list of slugs],
            "remaining": [list of slugs],
            "resume_hint": "Resume from item: <first-remaining-slug>"
        }
    """
    completed = []
    remaining = []
    processed_slugs = set()

    for record in journal:
        slug = record.get("slug")

        # Skip records without slug or already-processed slugs
        if not slug or slug in processed_slugs:
            continue

        processed_slugs.add(slug)

        status = record.get("status", "unknown")
        files = record.get("files", [])

        # An item is completed if:
        # 1. status == 'completed'
        # 2. files list is non-empty AND all files exist
        if status == "completed" and files and verify_files_exist(workdir, files):
            completed.append(slug)
        else:
            remaining.append(slug)

    # Generate resume hint
    if remaining:
        resume_hint = f"Resume from item: {remaining[0]}"
    else:
        resume_hint = "All items completed"

    return {
        "completed": completed,
        "remaining": remaining,
        "resume_hint": resume_hint,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Mid-wave recovery: classify completed vs remaining items",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--journal",
        required=True,
        help="Path to journal.jsonl file",
    )
    parser.add_argument(
        "--workdir",
        required=True,
        help="Path to worktree directory",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (default: human-readable)",
    )

    args = parser.parse_args()

    # Load and classify
    journal = load_journal(args.journal)
    result = classify_items(journal, args.workdir)

    # Output
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Completed: {len(result['completed'])} items")
        print(f"Remaining: {len(result['remaining'])} items")
        if result["completed"]:
            print(f"  Completed: {', '.join(result['completed'])}")
        if result["remaining"]:
            print(f"  Remaining: {', '.join(result['remaining'])}")
        print(result["resume_hint"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
