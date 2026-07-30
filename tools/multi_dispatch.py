"""tools.multi_dispatch — Multi-instance-aware dispatch wrapper.

Coordinates dispatch work across multiple instances by:
  1. Checking if files are already claimed by other instances
  2. Claiming files for this instance
  3. Executing the dispatch
  4. Releasing files when complete

Ensures no two instances work on the same files simultaneously (fail-closed:
if we cannot claim files or release them, we do not proceed).

Exit codes:
  0 = dispatch succeeded
  1 = error or conflict (files claimed by another instance)
  2 = dispatch command execution failed
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Add parent directory to path for imports
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store import StateAPI
from state_store.identity import get_instance_id
from state_store.instance_projection import (
    claim_files,
    release_files,
    get_all_claimed_files,
)


def check_conflict(store, file_paths: list[str], this_instance_id: str) -> str | None:
    """Check if any files are claimed by other instances.

    Args:
        store: StateAPI instance
        file_paths: list of file paths to check
        this_instance_id: this instance's ID (to exclude from conflict check)

    Returns:
        None if no conflict, otherwise conflicting instance_id
    """
    claimed = get_all_claimed_files(store)
    for other_instance, other_files in claimed.items():
        if other_instance == this_instance_id:
            continue
        # Check for overlap
        overlap = set(file_paths) & set(other_files)
        if overlap:
            return other_instance
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Multi-instance-aware dispatch with file coordination"
    )
    parser.add_argument(
        "--db", required=True, help="Path to state_store SQLite database"
    )
    parser.add_argument(
        "--instance-id",
        help="Instance ID (default: auto-derived from hostname:pid:nonce)",
    )
    parser.add_argument(
        "--files", nargs="+", required=True, help="File paths this dispatch will touch"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Dispatch timeout in seconds (default 300)",
    )
    parser.add_argument(
        "dispatch_cmd", nargs=argparse.REMAINDER, help="Dispatch command to run"
    )

    args = parser.parse_args()

    if not args.dispatch_cmd:
        print("error: no dispatch command provided", file=sys.stderr)
        return 1

    # Derive instance ID if not provided
    instance_id = args.instance_id or get_instance_id()

    try:
        store = StateAPI(args.db)
    except Exception as e:
        print(f"error: failed to open database: {e}", file=sys.stderr)
        return 1

    try:
        # Check for conflicts
        conflicting_instance = check_conflict(store, args.files, instance_id)
        if conflicting_instance:
            print(
                f"error: files are claimed by {conflicting_instance}",
                file=sys.stderr,
            )
            return 1

        # Claim files
        if not claim_files(store, instance_id, args.files):
            print(f"error: failed to claim files", file=sys.stderr)
            return 1

        print(f"claimed {len(args.files)} files", file=sys.stderr)

        try:
            # Execute dispatch command
            result = subprocess.run(
                args.dispatch_cmd,
                timeout=args.timeout,
                capture_output=False,
            )
            dispatch_success = result.returncode == 0
        except subprocess.TimeoutExpired:
            print(f"error: dispatch timed out after {args.timeout}s", file=sys.stderr)
            dispatch_success = False
        except Exception as e:
            print(f"error: dispatch failed: {e}", file=sys.stderr)
            dispatch_success = False

        # Release files (even if dispatch failed)
        if not release_files(store, instance_id, args.files):
            print(f"error: failed to release files", file=sys.stderr)
            return 1

        print(f"released {len(args.files)} files", file=sys.stderr)

        if not dispatch_success:
            return 2

        return 0

    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            store.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
