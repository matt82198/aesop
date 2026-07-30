#!/usr/bin/env python3
"""
Scaffold durable checkpointing directories for project orchestration.

Usage: ensure_state.py --state-dir DIR

Creates STATE.md and BUILDLOG.md templates in the state directory
if they do not already exist. Never overwrites existing files.

Writes go through state_store.write_api.WriteAPI (unified write path):
each scaffold write also lands as an event in the event store, so
markdown and SQLite state can never drift.
"""
# secretscan: allow-pattern-docs

import sys
import os
import argparse
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store.write_api import WriteAPI


STATE_TEMPLATE = """# STATE — authoritative project checkpoint

## Intent
One-line summary of the project's current phase and goal.

## Stack & locked decisions
- Key technology choices and constraints.
- Data model contracts and API signatures.

## Current status
- Phase summary and completion %.
- Major blockers or decisions pending.

## Gotchas
- Known issues, workarounds, environment quirks.

## NEXT STEPS
- Explicit ordered list of what comes next.
- Assigned owners if coordinating multiple agents.
"""

BUILDLOG_HEADER = "# BUILDLOG — append-only progress log"


def ensure_state_files(state_dir):
    """
    Create state directory with STATE.md and BUILDLOG.md if missing.
    Returns list of (filename, status) tuples: ('STATE.md', 'CREATED'), etc.
    """
    state_path = Path(state_dir)
    api = WriteAPI(state_path)  # creates the state directory if missing

    results = []

    # STATE.md (unified write path: file + state_md_written event)
    state_file = state_path / 'STATE.md'
    if state_file.exists():
        results.append(('STATE.md', 'EXISTS'))
    else:
        api.write_state_md(STATE_TEMPLATE, actor='ensure_state')
        results.append(('STATE.md', 'CREATED'))

    # BUILDLOG.md (unified write path: header + created line, byte-compatible
    # with the legacy scaffold; the created line lands as a buildlog_entry event)
    buildlog_file = state_path / 'BUILDLOG.md'
    if buildlog_file.exists():
        results.append(('BUILDLOG.md', 'EXISTS'))
    else:
        timestamp = datetime.datetime.now().isoformat()
        api.ensure_buildlog_exists(header=f'{BUILDLOG_HEADER}\n')
        api.append_buildlog(f'created {timestamp}', actor='ensure_state')
        results.append(('BUILDLOG.md', 'CREATED'))

    return results


def main():
    parser = argparse.ArgumentParser(
        description='Scaffold durable checkpointing directories.'
    )
    parser.add_argument('--state-dir', required=True,
                        help='State directory')

    args = parser.parse_args()

    state_dir = args.state_dir

    results = ensure_state_files(state_dir)

    for filename, status in results:
        print(f'{status} {filename}')


if __name__ == '__main__':
    main()
