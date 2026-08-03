#!/usr/bin/env python3
r"""
Append uniform BUILDLOG entries with optional git HEAD reference.
INDEX: Uniform BUILDLOG.md appender (writes via state_store WriteAPI: entry also lands as buildlog_entry event)

Usage:
    python buildlog.py "<message>" [--state-dir DIR] [--head] [--repo-path PATH]

Purpose:
    Ensures consistent BUILDLOG.md formatting across agents: one line per entry,
    timestamped, with optional git HEAD reference. Prevents hand-formatting variance.

Args:
    message: Entry text to append (positional, required).
    --state-dir: Path to state directory for BUILDLOG.md (default: AESOP_STATE_ROOT or ./state).
    --head: Include git HEAD (short-hash subject) from specified repo or current directory.
    --repo-path: Path to git repo for HEAD extraction (default: current working directory).

Output:
    Prints the exact line appended to stderr; exit 0 on success; non-zero (fail-closed) on write failure.

Behavior:
    - Appends ONE line formatted: ### [YYYY-MM-DD HH:MM] <message>
    - With --head, appends: | HEAD: <short-hash> <subject>
    - If repo not found or not a git repo, omits HEAD and notes "(no-repo)".
    - Creates BUILDLOG.md with append-only header if missing.
    - Never overwrites; always returns the line appended.
    - Writes through state_store.write_api.WriteAPI (unified write path):
      each append also lands as a buildlog_entry event in the event store.
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir

from state_store.write_api import WriteAPI, WriteConflict

# Historical BUILDLOG header this tool has always written (byte-compatible)
BUILDLOG_HEADER = "# Build Log (append-only)\n"


def get_git_head(repo_path):
    """Get short hash and subject from git HEAD.

    Args:
        repo_path: Path to git repository.

    Returns:
        Tuple of (short_hash, subject) or (None, None) if not a git repo.
    """
    try:
        if not Path(repo_path / ".git").exists():
            return None, None

        result = subprocess.run(
            ["git", "log", "-1", "--pretty=%h %s"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(None, 1)
            if len(parts) == 2:
                return parts[0], parts[1]
            elif len(parts) == 1:
                return parts[0], "(no subject)"
    except Exception:
        pass

    return None, None


def build_entry_line(message, short_hash=None, subject=None):
    """Format BUILDLOG entry line.

    Args:
        message: Entry message.
        short_hash: Git short hash (optional).
        subject: Git subject (optional).

    Returns:
        Formatted entry line.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"### [{timestamp}] {message}"

    if short_hash and subject:
        line += f" | HEAD: {short_hash} {subject}"
    elif short_hash:
        line += f" | HEAD: {short_hash} (no subject)"

    return line


def append_entry(state_dir, line):
    """Append line to BUILDLOG.md via the WriteAPI facade (unified write path).

    The facade writes the file atomically (with OCC + file locking) and mirrors
    the entry as a buildlog_entry event in the event store, so markdown and
    SQLite state can never drift.

    Args:
        state_dir: Path to the state directory containing BUILDLOG.md.
        line: Entry line to append.

    Raises:
        WriteConflict: If the facade detects a concurrent modification or
                       the atomic write fails (fail-closed).
    """
    api = WriteAPI(state_dir)
    api.ensure_buildlog_exists(header=BUILDLOG_HEADER)
    api.append_buildlog(line, actor="buildlog-tool")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Append uniform BUILDLOG entries with optional git HEAD reference.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("message", help="Entry message to append")
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Path to state directory (default: AESOP_STATE_ROOT or ./state)",
    )
    parser.add_argument(
        "--head",
        action="store_true",
        help="Include git HEAD (short-hash subject) reference",
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Path to git repo for HEAD extraction (default: current working directory)",
    )

    args = parser.parse_args()

    # Resolve state directory
    if args.state_dir:
        state_dir = Path(args.state_dir)
    else:
        state_dir = get_state_dir()

    # Derive repo path and get git HEAD if requested
    short_hash = None
    subject = None
    repo_note = ""

    if args.head:
        repo_path = Path(args.repo_path) if args.repo_path else Path.cwd()
        short_hash, subject = get_git_head(repo_path)

        if not short_hash:
            repo_note = " (no-repo)"

    # Build and format entry
    line = build_entry_line(args.message, short_hash, subject)

    # Append to BUILDLOG.md via the WriteAPI facade (fail-closed on write failure)
    try:
        append_entry(state_dir, line)
    except (WriteConflict, OSError) as e:
        print(f"ERROR: failed to append BUILDLOG entry: {e}", file=sys.stderr)
        sys.exit(1)

    # Print the exact line appended (with repo note if --head requested and failed)
    output_line = line + repo_note if repo_note else line
    print(output_line, file=sys.stderr)


if __name__ == "__main__":
    main()
