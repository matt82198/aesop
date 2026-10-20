#!/usr/bin/env python3
"""Tracker zombie-resurrection prevention gate.
INDEX: Append-only lane journal + zombie-resurrection fail-closed gate; prevents items in terminal lanes (done/rejected) from re-entering active lanes (ranked/proposed/in-progress/accepted); modes: --seed (bootstrap journal), --check (detect violations, exit 1 if found, default), --enforce (revert zombies to terminal lane via WriteAPI, event-sourced), --reconcile-journal (backfill missing item_updated events for past direct-patch reverts); CLI: `tracker_guard.py [--seed | --check | --enforce | --reconcile-journal]`; journaled in state/tracker-journal.jsonl with rotation at 5000 lines

Maintains an append-only lane journal (state/tracker-journal.jsonl) to enforce
the ZOMBIE RULE: items that reach a terminal lane (done/rejected) may NEVER
re-enter an active lane (ranked/proposed/in-progress/accepted).

Usage:
  tracker_guard.py [--seed | --enforce | --check | --reconcile-journal]
  tracker_guard.py --adopt-disk-items --reason "<text>" [--ids a,b] [--dry-run]
  tracker_guard.py --help

  --adopt-disk-items
    Explicit, provenance-stamped adoption of tracker items present in tracker.json
    but absent from the event log (the disk-only items that make the OCC guard
    refuse every render). Delegates to WriteAPI.tracker_adopt_disk_items. --reason
    is required for any non-empty adoption; --ids must cover every disk-only item
    (partial adoption is refused); --dry-run plans and verifies without writing.
    Prints the plan/result as JSON. Exit 0 on success, 2 on any refusal.

Modes:
  --seed
    Bootstrap the journal from current tracker state. Safe to run multiple times.
    Creates state/tracker-journal.jsonl if missing, records all items' current lanes.

  --check (default)
    Check for zombie resurrections against the journal. Exits 0 if clean,
    1 if violations detected (fail-closed). Appends normal transitions to journal.

  --enforce
    Revert any zombie items to their last terminal lane. Closes items THROUGH
    the sanctioned writer (state_store.write_api.WriteAPI.tracker_update_item),
    which appends an item_updated event before re-rendering tracker.json, so the
    event log and the projection never disagree. Appends revert entries to the
    journal. Exits 0 after fixing. Use after --check detects zombies.

  --reconcile-journal
    One-time migration for history produced by the OLD (pre-event-sourced)
    --enforce, which patched tracker.json directly without appending an
    item_updated event. For every item the journal records as reverted to a
    terminal lane, backfills the missing item_updated event into the event log
    IF the event-store-only projection does not already reflect that lane.
    Idempotent: a second run appends zero new events.

Environment:
  AESOP_STATE_ROOT: Directory containing tracker.json and tracker-journal.jsonl
                    Defaults to ./state

Exit codes:
  0: Success or clean check (no zombies)
  1: Zombies detected (CHECK mode) or error (missing args, unknown flags, malformed tracker)
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Import common utilities
sys.path.insert(0, str(Path(__file__).parent))
import common

# Ensure state_store is importable (sys.path fix for bootstrapping)
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from state_store.read_api import ReadAPI
from state_store.write_api import WriteAPI
from state_store.store import EventStore
from state_store.projections import project_tracker


def get_journal_path():
    """Return path to tracker-journal.jsonl."""
    return common.get_state_dir() / "tracker-journal.jsonl"


class GuardDataError(Exception):
    """tracker.json or the journal is present but unreadable/malformed.

    Fail closed: a corrupt input must never be treated as "nothing to check"
    (which would let zombie items pass the gate). main() maps this to exit 1.
    """


def read_tracker():
    """Read tracker.json via the state_store facade. Returns None if the file is absent.

    Uses state_store.read_api.ReadAPI for the tracker data.
    Respects AESOP_STATE_ROOT env var.

    Raises GuardDataError when tracker.json exists but is not a JSON object
    with a list-valued "items" (if present). ReadAPI swallows parse errors and
    returns {}, so validity is probed here, before trusting its result.
    """
    state_dir = common.get_state_dir()
    tracker_file = state_dir / "tracker.json"
    if not tracker_file.exists():
        return None

    try:
        raw = json.loads(tracker_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise GuardDataError(f"tracker.json is malformed or unreadable ({tracker_file}): {e}")
    if not isinstance(raw, dict):
        raise GuardDataError(f"tracker.json top level is not a JSON object ({tracker_file})")
    if "items" in raw and not isinstance(raw["items"], list):
        raise GuardDataError(f"tracker.json 'items' is not a list ({tracker_file})")

    api = ReadAPI(state_dir)
    tracker_data = api.read_tracker_snapshot()

    # A valid but empty object ({}) carries no items: treat as no tracker data.
    if not tracker_data:
        return None
    return tracker_data


def read_journal():
    """Read all journal entries. Returns list of dicts.

    Raises GuardDataError on any unreadable or corrupt line. A partial line
    must not be silently dropped: that would hide lane history and let zombies pass.
    """
    journal_path = get_journal_path()
    if not journal_path.exists():
        return []
    entries = []
    try:
        for line_no, line in enumerate(journal_path.read_text(encoding="utf-8").strip().split("\n"), 1):
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                raise GuardDataError(f"journal line {line_no} is corrupt ({journal_path}): {e}")
            if not isinstance(entry, dict):
                raise GuardDataError(f"journal line {line_no} is not a JSON object ({journal_path})")
            entries.append(entry)
    except OSError as e:
        raise GuardDataError(f"journal is unreadable ({journal_path}): {e}")
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


def _zombie_revert_note(item_id, current_lane, terminal_lane):
    """Build the audit note recorded on the item_updated event for a zombie revert."""
    return f"tracker_guard: zombie {item_id} reverted {current_lane} -> {terminal_lane}"


def cmd_enforce(args):
    """Revert zombies to their last terminal lane.

    Closes items THROUGH the sanctioned writer (WriteAPI.tracker_update_item),
    which appends an item_updated event to the event log BEFORE re-rendering
    tracker.json. This is load-bearing: tracker.json is re-rendered FROM the
    event log on every WriteAPI call anywhere in the system (any mutation
    re-projects the whole tracker), so a lane change that only patches
    tracker.json directly is correct only until the next unrelated write --
    at which point the un-journaled item silently resurrects to whatever lane
    the event log (still) says it's in. See --reconcile-journal for backfilling
    history already lost to that bug.
    """
    tracker = read_tracker()
    if tracker is None:
        print("INFO: tracker.json not found, nothing to enforce")
        return 0

    journal = read_journal()
    items = tracker.get("items", [])

    # Find zombies (read-only pass; no mutation yet)
    to_revert = []
    for item in items:
        item_id = item.get("id")
        current_lane = item.get("lane")

        if not item_id or current_lane is None:
            print(f"WARN: skipping malformed item: {item}")
            continue

        if is_zombie(item_id, current_lane, journal):
            terminal_lane = find_last_terminal_lane(item_id, journal)
            if terminal_lane:
                to_revert.append((item_id, current_lane, terminal_lane))

    reverted = []
    if to_revert:
        write_api = WriteAPI(common.get_state_dir())
        try:
            for item_id, current_lane, terminal_lane in to_revert:
                try:
                    write_api.tracker_update_item(
                        item_id,
                        {
                            "lane": terminal_lane,
                            "notes": _zombie_revert_note(item_id, current_lane, terminal_lane),
                            "source": "tracker_guard",
                        },
                        actor="tracker_guard",
                    )
                except Exception as e:
                    print(f"ERROR: failed to revert {item_id} via WriteAPI: {e}", file=sys.stderr)
                    continue

                reverted.append({
                    "id": item_id,
                    "from": current_lane,
                    "to": terminal_lane,
                })
                # Log revert to journal (independent audit trail; the event log
                # above is the source of truth, this journal is what --check uses)
                entry = {
                    "ts": datetime.utcnow().isoformat(),
                    "id": item_id,
                    "from": current_lane,
                    "to": terminal_lane,
                    "type": "reverted",
                }
                append_journal_entry(entry)
        finally:
            write_api.close()

    if reverted:
        print(f"INFO: reverted {len(reverted)} zombie item(s):")
        for r in reverted:
            print(f"  {r['id']}: {r['from']} -> {r['to']}")
        rotate_journal_if_needed()

    return 0


def cmd_reconcile_journal(args):
    """Backfill item_updated events for reverts the OLD direct-patch --enforce lost.

    The journal (state/tracker-journal.jsonl) is an independent append-only audit
    trail that recorded every revert the pre-fix --enforce performed, even though
    those reverts never made it into the event log. For each item the journal says
    was reverted to a terminal lane, check whether the event-store-ONLY projection
    (ignoring tracker.json on disk) already reflects that lane; if not, append the
    missing item_updated event via WriteAPI so the event log catches up.

    Idempotent: once the event log agrees with the journal for an item, re-running
    this appends zero further events for it (safe to run repeatedly / in CI).
    """
    journal = read_journal()
    reverts = [e for e in journal if e.get("type") == "reverted" and e.get("id") and e.get("to")]

    if not reverts:
        print("INFO: no journaled reverts to reconcile")
        return 0

    # Keep only the most recent journaled revert per item (its intended current lane).
    last_revert = {}
    for entry in reverts:
        last_revert[entry["id"]] = entry["to"]

    state_dir = common.get_state_dir()
    db_path = state_dir / common.STATE_DB_FILENAME

    def _event_store_lanes():
        """Read the event-store-ONLY tracker projection (ignores tracker.json)."""
        if not db_path.exists():
            return {}
        store = EventStore(str(db_path))
        try:
            events = store.read("tracker")
        finally:
            store.close()
        projection = project_tracker(events)
        return {item["id"]: item.get("lane") for item in projection.get("items", [])}

    event_lanes = _event_store_lanes()

    reconciled = []
    write_api = WriteAPI(state_dir)
    try:
        for item_id, terminal_lane in last_revert.items():
            if item_id not in event_lanes:
                # Never created via the event log (legacy/manual item) -- nothing
                # safe to backfill against. Skip rather than invent an item_created.
                continue
            if event_lanes[item_id] == terminal_lane:
                # Event log already agrees with the journal: no drift, no-op.
                continue
            try:
                write_api.tracker_update_item(
                    item_id,
                    {
                        "lane": terminal_lane,
                        "notes": f"tracker_guard: reconcile-journal backfill -> {terminal_lane}",
                        "source": "tracker_guard",
                    },
                    actor="tracker_guard",
                )
            except Exception as e:
                print(f"ERROR: failed to reconcile {item_id}: {e}", file=sys.stderr)
                continue

            reconciled.append({"id": item_id, "to": terminal_lane})
            # Refresh baseline so later iterations in this same run see this write.
            event_lanes = _event_store_lanes()
    finally:
        write_api.close()

    if reconciled:
        print(f"INFO: reconciled {len(reconciled)} item(s) into the event log:")
        for r in reconciled:
            print(f"  {r['id']}: backfilled lane={r['to']}")
    else:
        print("INFO: event log already matches journal (0 new events)")

    return 0


def cmd_adopt_disk_items(argv):
    """Explicitly adopt disk-only tracker items into the event log (WriteAPI).

    Plan/result is printed as JSON on stdout. Exit 0 on success (including a
    dry run), 2 on any refusal (missing --reason, ids not disk-only, partial
    adoption, verification mismatch, unreadable tracker.json).
    """
    import argparse

    parser = argparse.ArgumentParser(prog="tracker_guard.py --adopt-disk-items", add_help=False)
    parser.add_argument("--adopt-disk-items", action="store_true")
    parser.add_argument("--reason", default=None)
    parser.add_argument("--ids", default=None, help="comma-separated ids (default: all disk-only)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    ids = None
    if args.ids is not None:
        ids = [part.strip() for part in args.ids.split(",") if part.strip()]

    write_api = WriteAPI(common.get_state_dir())
    try:
        result = write_api.tracker_adopt_disk_items(
            actor="tracker_guard",
            reason=args.reason,
            ids=ids,
            dry_run=args.dry_run,
        )
    except Exception as e:
        print(json.dumps({"ok": False, "dry_run": args.dry_run, "error": str(e),
                          "adopted_ids": []}, indent=2, sort_keys=True))
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    finally:
        write_api.close()

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def print_help():
    """Print usage information."""
    print(__doc__)


def main(argv=None):
    """Main entry point."""
    if argv is None:
        argv = sys.argv[1:]

    if "--adopt-disk-items" in argv and "--help" not in argv and "-h" not in argv:
        return cmd_adopt_disk_items(argv)

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
        elif arg == "--reconcile-journal":
            mode = "reconcile-journal"
        elif arg.startswith("--"):
            print(f"ERROR: unknown flag: {arg}", file=sys.stderr)
            return 1

    # Run the appropriate command. Malformed tracker/journal data fails closed.
    commands = {
        "seed": cmd_seed,
        "check": cmd_check,
        "enforce": cmd_enforce,
        "reconcile-journal": cmd_reconcile_journal,
    }
    if mode not in commands:
        print(f"ERROR: unknown mode: {mode}", file=sys.stderr)
        return 1
    try:
        return commands[mode](argv)
    except GuardDataError as e:
        print(f"ERROR: {e}. Refusing to pass (fail-closed); repair or restore the file.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
