#!/usr/bin/env python3
"""
Heartbeat registry for fleet-wide liveness monitoring.
INDEX: Single-instance loop liveness registry

Usage:
  heartbeat.py beat <name> [<status>] [--state-dir DIR] [--brain]
  heartbeat.py check [--max-age 300] [--state-dir DIR]

Modes:
  beat: Write epoch + optional status word to .heartbeats/<name>
    --state-dir: Heartbeats directory (default: AESOP_STATE_ROOT/heartbeats or ./state/heartbeats)
    --brain: Write to ~/.claude/.heartbeats/ (overrides --state-dir)
  check: List all .heartbeats/* files and report "<name> ALIVE|STALE age:<n>s"
    Exit 0 if all alive, 1 if any stale. Always prints output.
"""

import sys
import os
import time
from pathlib import Path
import argparse

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir


def beat(name, status=None, state_dir=None, brain=False):
    """Write epoch (+ optional status) to .heartbeats/<name>."""
    if brain:
        target_dir = Path.home() / ".claude" / ".heartbeats"
        location = "brain"
    else:
        if state_dir is None:
            state_dir = get_state_dir()
        target_dir = state_dir / "heartbeats"
        location = "state"

    target_dir.mkdir(parents=True, exist_ok=True)
    hb_file = target_dir / name
    now_epoch = str(int(time.time()))
    content = now_epoch + "\n"
    if status:
        content += status + "\n"
    hb_file.write_text(content)
    print(f"beat: {name} written to {location}")


def check(max_age=300, state_dir=None):
    """Check all heartbeat files and report ALIVE/STALE."""
    now = time.time()
    all_alive = True
    results = []

    if state_dir is None:
        state_dir = get_state_dir()

    # Collect state dir registry files
    state_hb_dir = state_dir / "heartbeats"
    if state_hb_dir.exists():
        for hb_file in sorted(state_hb_dir.iterdir()):
            if hb_file.is_file():
                age = _get_age(hb_file, now)
                status = "ALIVE" if age <= max_age else "STALE"
                if status == "STALE":
                    all_alive = False
                results.append((f"state/{hb_file.name}", status, age))

    # Collect brain registry files
    brain_hb_dir = Path.home() / ".claude" / ".heartbeats"
    if brain_hb_dir.exists():
        for hb_file in sorted(brain_hb_dir.iterdir()):
            if hb_file.is_file():
                age = _get_age(hb_file, now)
                status = "ALIVE" if age <= max_age else "STALE"
                if status == "STALE":
                    all_alive = False
                results.append((f"brain/{hb_file.name}", status, age))

    # Print results
    for name, status, age in results:
        print(f"{name} {status} age:{age}s")

    sys.exit(0 if all_alive else 1)


def _get_age(hb_file, now):
    """Extract epoch from first line and return age in seconds."""
    try:
        content = hb_file.read_text().strip().split('\n')
        epoch_str = content[0].strip()
        epoch = int(epoch_str)
        age = int(now - epoch)
        return max(0, age)
    except (ValueError, IndexError):
        return 99999


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="cmd")

    beat_parser = subparsers.add_parser("beat", help="Write heartbeat")
    beat_parser.add_argument("name", help="Heartbeat name")
    beat_parser.add_argument("status", nargs="?", default=None, help="Optional status word")
    beat_parser.add_argument("--state-dir", default=None, help="State directory (default: AESOP_STATE_ROOT or ./state)")
    beat_parser.add_argument("--brain", action="store_true", help="Write to brain .heartbeats/ (overrides --state-dir)")

    check_parser = subparsers.add_parser("check", help="Check heartbeat status")
    check_parser.add_argument("--max-age", type=int, default=300, help="Max age in seconds (default 300)")
    check_parser.add_argument("--state-dir", default=None, help="State directory (default: AESOP_STATE_ROOT or ./state)")

    args = parser.parse_args()

    if args.cmd == "beat":
        state_dir = Path(args.state_dir) if args.state_dir else None
        beat(args.name, args.status, state_dir=state_dir, brain=args.brain)
    elif args.cmd == "check":
        state_dir = Path(args.state_dir) if args.state_dir else None
        check(args.max_age, state_dir=state_dir)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
