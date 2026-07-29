#!/usr/bin/env python3
"""Tracker zombie-resurrection prevention gate.

Maintains an append-only lane journal (state/tracker-journal.jsonl) to enforce
the ZOMBIE RULE: items that reach a terminal lane (done/rejected) may NEVER
re-enter an active lane (ranked/proposed/in-progress/accepted).

Usage:
  tracker_guard.py [--seed | --enforce | --check]
  tracker_guard.py --help

Modes:
  --seed
    Bootstrap the journal from current tracker state. Safe to run multiple times.
    Creates state/tracker-journal.jsonl if missing, records all items' current lanes.

  --check (default)
    Check for zombie resurrections against the journal. Exits 0 if clean,
    1 if violations detected (fail-closed). Appends normal transitions to journal.

  --enforce
    Revert any zombie items to their last terminal lane. Appends revert entries
    to journal. Exits 0 after fixing. Use after --check detects zombies.

Environment:
  AESOP_STATE_ROOT: Directory containing tracker.json and tracker-journal.jsonl
                    Defaults to ./state

Exit codes:
  0: Success or clean check (no zombies)
  1: Zombies detected (CHECK mode) or error (missing args, unknown flags, malformed tracker)
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Import common utilities
sys.path.insert(0, str(Path(__file__).parent))
import common


def get_journal_path():
    """Return path to tracker-journal.jsonl."""
    return common.get_state_dir() / "tracker-journal.jsonl"


def get_tracker_path():
    """Return path to tracker.json."""
    return common.get_state_dir() / "tracker.json"


def read_tracker():
    """Read tracker.json. Returns None if missing."""
    tracker_path = get_tracker_path()
    if not tracker_path.exists():
        return None
    try:
        return json.loads(tracker_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError) as e:
        print(f"ERROR: Could not read tracker.json: {e}", file=sys.stderr)
        return None


def write_tracker(tracker_data):
    """Write tracker.json."""
    tracker_path = get_tracker_path()
    tracker_path.write_text(json.dumps(tracker_data, indent=2), encoding="utf-8")


def read_journal():
    """Read all journal entries. Returns list of dicts."""
    journal_path = get_journal_path()
    if not journal_path.exists():
        return []
    entries = []
    try:
        for line in journal_path.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                entries.append(json.loads(line))
    except (json.JSONDecodeError, IOError) as e:
        print(f"ERROR: Could not read journal: {e}", file=sys.stderr)
        return []
    return entries


def append_journal_entry(entry):
    """Append a single entry to journal."""
    journal_path = get_journal_path()
    with open(journal_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def rotate_journal_if_needed():
    """Rotate journal to archive if it exceeds 5000 lines."""
    journal_path = get_journal_path()
    if not journal_path.exists():
        return

    lines = journal_path.read_text(encoding="utf-8").strip().split("\n")
    if len(lines) <= 5000:
        return

    # Rotate: keep last 2500 lines, move old ones to .archive
    archive_path = journal_path.with_stem(journal_path.stem + ".archive")
    old_content = "\n".join(lines[:-2500])
    new_content = "\n".join(lines[-2500:])

    if archive_path.exists():
        archive_path.write_text(archive_path.read_text() + "\n" + old_content + "\n",
                                encoding="utf-8")
    else:
        archive_path.write_text(old_content + "\n", encoding="utf-8")

    journal_path.write_text(new_content + "\n", encoding="utf-8")


def get_item_lane_history(journal, item_id):
    """Get the complete lane history for an item from journal.

    Returns a list of lane names in chronological order, or empty if no history.
    """
    history = []
    for entry in journal:
        if entry.get("id") == item_id:
            if "to" in entry and entry["to"] is not None:
                history.append(entry["to"])
    return history


def is_zombie(item_id, current_lane, journal):
    """Check if an item is a zombie (resurrected from terminal lane).

    A zombie is an item that:
    1. Has a lane history containing 'done' or 'rejected'
    2. Is currently in an active lane (not done/rejected)

    Returns True if zombie, False otherwise.
    """
    history = get_item_lane_history(journal, item_id)
    if not history:
        # No history = not a zombie
        return False

    # Check if item ever reached a terminal lane
    has_terminal = "done" in history or "rejected" in history

    # Active lanes are: ranked, proposed, in-progress, accepted
    active_lanes = {"ranked", "proposed", "in-progress", "accepted"}
    is_currently_active = current_lane in active_lanes

    # Zombie if: has terminal history AND currently active
    return has_terminal and is_currently_active


def find_last_terminal_lane(item_id, journal):
    """Find the last terminal lane (done or rejected) in an item's history.

    Returns the lane name, or None if no terminal lane in history.
    """
    history = get_item_lane_history(journal, item_id)
    terminal_lanes = {"done", "rejected"}

    # Search backwards for the last terminal lane
    for lane in reversed(history):
        if lane in terminal_lanes:
            return lane
    return None


def cmd_seed(args):
    """Bootstrap journal from current tracker state."""
    tracker = read_tracker()
    if tracker is None:
        print("INFO: tracker.json not found, nothing to seed")
        return 0

    items = tracker.get("items", [])
    if not items:
        print("INFO: tracker has no items, journal empty")
        return 0

    # Read existing journal to avoid re-seeding
    journal = read_journal()
    existing_ids = {e.get("id") for e in journal if "id" in e}

    # Seed entries for new items only
    count = 0
    for item in items:
        item_id = item.get("id")
        lane = item.get("lane")

        # Skip malformed items
        if not item_id or lane is None:
            print(f"WARN: skipping malformed item: {item}")
            continue

        # Skip already seeded items
        if item_id in existing_ids:
            continue

        entry = {
            "ts": datetime.utcnow().isoformat(),
            "id": item_id,
            "from": None,
            "to": lane,
        }
        append_journal_entry(entry)
        count += 1

    if count > 0:
        print(f"INFO: seeded {count} items to journal")
        rotate_journal_if_needed()

    return 0


def cmd_check(args):
    """Check for zombie resurrections (default mode)."""
    tracker = read_tracker()
    if tracker is None:
        print("INFO: tracker.json not found, nothing to check")
        return 0

    journal = read_journal()
    items = tracker.get("items", [])

    # Build current state: id -> lane
    current_lanes = {}
    for item in items:
        item_id = item.get("id")
        lane = item.get("lane")

        # Skip malformed items
        if not item_id or lane is None:
            print(f"WARN: skipping malformed item: {item}")
            continue

        current_lanes[item_id] = lane

    # Detect zombies
    zombies = []
    for item_id, current_lane in current_lanes.items():
        if is_zombie(item_id, current_lane, journal):
            history = get_item_lane_history(journal, item_id)
            zombies.append({
                "id": item_id,
                "current_lane": current_lane,
                "history": history,
            })

    if zombies:
        print(f"ERROR: {len(zombies)} zombie item(s) detected:")
        for z in zombies:
            print(f"  {z['id']}: {z['history']} -> {z['current_lane']}")
        return 1

    # No zombies: log normal transitions to journal
    last_known = {}
    for entry in journal:
        if "id" in entry and "to" in entry:
            last_known[entry["id"]] = entry["to"]

    for item_id, current_lane in current_lanes.items():
        last_lane = last_known.get(item_id)
        if last_lane != current_lane:
            # Normal transition: log it
            entry = {
                "ts": datetime.utcnow().isoformat(),
                "id": item_id,
                "from": last_lane,
                "to": current_lane,
            }
            append_journal_entry(entry)

    rotate_journal_if_needed()
    return 0


def cmd_enforce(args):
    """Revert zombies to their last terminal lane."""
    tracker = read_tracker()
    if tracker is None:
        print("INFO: tracker.json not found, nothing to enforce")
        return 0

    journal = read_journal()
    items = tracker.get("items", [])

    # Build current state
    current_lanes = {}
    for item in items:
        item_id = item.get("id")
        lane = item.get("lane")

        if not item_id or lane is None:
            print(f"WARN: skipping malformed item: {item}")
            continue

        current_lanes[item_id] = lane

    # Find and revert zombies
    reverted = []
    for item in items:
        item_id = item.get("id")
        current_lane = item.get("lane")

        if not item_id or current_lane is None:
            continue

        if is_zombie(item_id, current_lane, journal):
            terminal_lane = find_last_terminal_lane(item_id, journal)
            if terminal_lane:
                item["lane"] = terminal_lane
                reverted.append({
                    "id": item_id,
                    "from": current_lane,
                    "to": terminal_lane,
                })
                # Log revert to journal
                entry = {
                    "ts": datetime.utcnow().isoformat(),
                    "id": item_id,
                    "from": current_lane,
                    "to": terminal_lane,
                    "type": "reverted",
                }
                append_journal_entry(entry)

    # Write updated tracker
    if reverted:
        write_tracker(tracker)
        print(f"INFO: reverted {len(reverted)} zombie item(s):")
        for r in reverted:
            print(f"  {r['id']}: {r['from']} -> {r['to']}")
        rotate_journal_if_needed()

    return 0


def print_help():
    """Print usage information."""
    print(__doc__)


def main(argv=None):
    """Main entry point."""
    if argv is None:
        argv = sys.argv[1:]

    # Parse flags
    mode = "check"  # default mode
    for arg in argv:
        if arg in ("--help", "-h"):
            print_help()
            return 0
        elif arg == "--seed":
            mode = "seed"
        elif arg == "--check":
            mode = "check"
        elif arg == "--enforce":
            mode = "enforce"
        elif arg.startswith("--"):
            print(f"ERROR: unknown flag: {arg}", file=sys.stderr)
            return 1

    # Run the appropriate command
    if mode == "seed":
        return cmd_seed(argv)
    elif mode == "check":
        return cmd_check(argv)
    elif mode == "enforce":
        return cmd_enforce(argv)
    else:
        print(f"ERROR: unknown mode: {mode}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
