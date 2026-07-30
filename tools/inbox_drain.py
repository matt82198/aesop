#!/usr/bin/env python3
r"""Drain UI inbox submissions, tracking which have been processed.

Usage:
    python inbox_drain.py pending         # List unprocessed inbox items
    python inbox_drain.py mark <ISO-ts>   # Mark one timestamp as processed
    python inbox_drain.py mark-all        # Mark all pending items as processed

Purpose:
    Makes inbox submissions (written by the aesop dashboard) survive sessions.
    Maintains a processed-marker file that tracks which items have been actioned.
    Enables /power and daemons to pick up queued work that arrived while no
    session was running.

Configuration:
    - Reads aesop.config.json for inbox_path and inbox_seen_path (optional).
    - Env vars override config: AESOP_INBOX_PATH, AESOP_INBOX_SEEN_PATH.
    - Falls back to defaults: state/ui-inbox.md, state/.ui-inbox-seen.

Format (ui-inbox.md):
    - [ISO-TS] item text
    - [ISO-TS] item text

Subcommands:
    pending:    Read inbox, compare against processed-marker, print unprocessed.
                Exit 0. If none exist, print "NO PENDING" and exit 0.
    mark <ts>:  Append ISO timestamp to seen-file. If already marked, noop.
    mark-all:   Append ALL pending timestamps to seen-file in one write.

Robustness:
    - Missing inbox or seen files treated as empty (no crash).
    - Timestamps are exact match (whitespace-preserved).
    - Exit 0 always (never fails on missing files).

Output:
    pending: Each unprocessed item as "[TS] text" (one per line).
    mark/mark-all: Prints "[i/total] marked" summary to stderr.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime


def load_config():
    """Load aesop.config.json if present; return dict."""
    try:
        config_path = Path.cwd() / 'aesop.config.json'
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def resolve_paths(config):
    """Resolve paths with precedence: env var > config > default."""
    aesop_root = Path.cwd()
    state_root = Path(
        os.environ.get(
            'AESOP_STATE_ROOT',
            config.get('state_root', str(aesop_root / 'state'))
        )
    ).expanduser()

    # inbox_path: env AESOP_INBOX_PATH > config inbox_path > default state/ui-inbox.md
    inbox_path = Path(
        os.environ.get(
            'AESOP_INBOX_PATH',
            config.get('inbox_path', str(state_root / 'ui-inbox.md'))
        )
    ).expanduser()

    # inbox_seen_path: env AESOP_INBOX_SEEN_PATH > config inbox_seen_path > default state/.ui-inbox-seen
    inbox_seen_path = Path(
        os.environ.get(
            'AESOP_INBOX_SEEN_PATH',
            config.get('inbox_seen_path', str(state_root / '.ui-inbox-seen'))
        )
    ).expanduser()

    return inbox_path, inbox_seen_path


config = load_config()
INBOX_PATH, SEEN_PATH = resolve_paths(config)


def read_inbox():
    """Read inbox.md and return list of (timestamp, text) tuples.

    Returns:
        List of (ts, text) tuples; empty list if file missing/empty.
    """
    if not INBOX_PATH.exists():
        return []

    items = []
    try:
        # Try UTF-8 first, fall back to latin-1 for extended-ASCII files
        try:
            with open(INBOX_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(INBOX_PATH, "r", encoding="latin-1") as f:
                content = f.read()

        for line in content.splitlines():
            line = line.rstrip("\r\n")
            if line.startswith("- [") and "]" in line:
                # Parse "- [ISO-TS] text"
                end_bracket = line.index("]")
                ts = line[3:end_bracket]  # Extract ISO-TS from - [ISO-TS]
                text = line[end_bracket + 2 :] if end_bracket + 2 < len(line) else ""
                items.append((ts, text))
    except Exception:
        pass

    return items


def read_seen():
    """Read seen-file and return set of marked timestamps.

    Returns:
        Set of already-processed ISO timestamps; empty set if file missing.
    """
    if not SEEN_PATH.exists():
        return set()

    seen = set()
    try:
        try:
            with open(SEEN_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(SEEN_PATH, "r", encoding="latin-1") as f:
                content = f.read()

        for line in content.splitlines():
            ts = line.rstrip("\r\n")
            if ts:
                seen.add(ts)
    except Exception:
        pass

    return seen


def get_pending():
    """Get list of unprocessed items.

    Returns:
        List of (ts, text) tuples that haven't been seen yet.
    """
    inbox = read_inbox()
    seen = read_seen()
    return [(ts, text) for ts, text in inbox if ts not in seen]


def cmd_pending():
    """List unprocessed inbox items. Exit 0."""
    pending = get_pending()
    if not pending:
        print("NO PENDING")
        return

    for ts, text in pending:
        print(f"[{ts}] {text}")


def cmd_mark(timestamp):
    """Mark one timestamp as processed.

    Args:
        timestamp: ISO timestamp string.
    """
    seen = read_seen()
    if timestamp in seen:
        print(f"[1/1] already marked", file=sys.stderr)
        return

    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_PATH, "a", encoding="utf-8") as f:
        f.write(timestamp + "\n")

    print(f"[1/1] marked", file=sys.stderr)


def cmd_mark_all():
    """Mark all pending items as processed in one write.

    Prints [i/total] summary to stderr.
    """
    pending = get_pending()
    if not pending:
        print(f"[0/0] no pending items", file=sys.stderr)
        return

    seen = read_seen()
    to_mark = [ts for ts, _ in pending if ts not in seen]
    if not to_mark:
        print(f"[0/{len(pending)}] no new items", file=sys.stderr)
        return

    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_PATH, "a", encoding="utf-8") as f:
        for ts in to_mark:
            f.write(ts + "\n")

    print(f"[{len(to_mark)}/{len(pending)}] marked all", file=sys.stderr)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Drain UI inbox submissions, tracking processed items.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand")

    # pending subcommand
    subparsers.add_parser("pending", help="List unprocessed inbox items")

    # mark subcommand
    mark_parser = subparsers.add_parser("mark", help="Mark one item as processed")
    mark_parser.add_argument("timestamp", help="ISO timestamp to mark")

    # mark-all subcommand
    subparsers.add_parser("mark-all", help="Mark all pending items as processed")

    args = parser.parse_args()

    if args.command == "pending":
        cmd_pending()
    elif args.command == "mark":
        cmd_mark(args.timestamp)
    elif args.command == "mark-all":
        cmd_mark_all()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
