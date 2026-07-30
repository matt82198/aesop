"""tools.instance_manager — Multi-instance orchestration manager.

High-level CLI wrapper around state_store.instance_projection for instance
registration, discovery, heartbeat, and file claim coordination.

CLI Usage:
  python tools/instance_manager.py --db <db_path> --register <instance_id> <hostname> <pid>
  python tools/instance_manager.py --db <db_path> --heartbeat <instance_id>
  python tools/instance_manager.py --db <db_path> --list
  python tools/instance_manager.py --db <db_path> --claim <instance_id> <file1> <file2> ...
  python tools/instance_manager.py --db <db_path> --release <instance_id> <file1> <file2> ...
  python tools/instance_manager.py --db <db_path> --status <instance_id>
  python tools/instance_manager.py --db <db_path> --claimed-files <instance_id>
  python tools/instance_manager.py --db <db_path> --all-claimed
  python tools/instance_manager.py --db <db_path> --stale <threshold_seconds>

Exit codes:
  0 = success
  1 = error or operation failed
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add parent directory to path for imports
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store import StateAPI
from state_store.instance_projection import (
    register_instance,
    heartbeat,
    claim_files,
    release_files,
    list_active_instances,
    get_instance_status,
    get_claimed_files,
    get_all_claimed_files,
    detect_stale_instances,
)


def main():
    parser = argparse.ArgumentParser(
        description="Multi-instance orchestration manager"
    )
    parser.add_argument("--db", required=True, help="Path to state_store SQLite database")

    # Commands (mutually exclusive)
    cmd = parser.add_subparsers(dest="command")

    # Register: register --register <instance_id> <hostname> <pid>
    reg_parser = cmd.add_parser("register", help="Register a new instance")
    reg_parser.add_argument("instance_id", help="Unique instance identifier")
    reg_parser.add_argument("hostname", help="Hostname of the instance")
    reg_parser.add_argument("pid", type=int, help="Process ID of the orchestrator")

    # Heartbeat: heartbeat <instance_id>
    hb_parser = cmd.add_parser("heartbeat", help="Send a heartbeat for an instance")
    hb_parser.add_argument("instance_id", help="Unique instance identifier")

    # List: list
    cmd.add_parser("list", help="List all active instances")

    # Claim: claim <instance_id> <file1> <file2> ...
    claim_parser = cmd.add_parser("claim", help="Claim files for an instance")
    claim_parser.add_argument("instance_id", help="Unique instance identifier")
    claim_parser.add_argument("files", nargs="+", help="File paths to claim")

    # Release: release <instance_id> <file1> <file2> ...
    release_parser = cmd.add_parser("release", help="Release files from an instance")
    release_parser.add_argument("instance_id", help="Unique instance identifier")
    release_parser.add_argument("files", nargs="+", help="File paths to release")

    # Status: status <instance_id>
    status_parser = cmd.add_parser("status", help="Get status of an instance")
    status_parser.add_argument("instance_id", help="Unique instance identifier")

    # Claimed files: claimed-files <instance_id>
    claimed_parser = cmd.add_parser(
        "claimed-files", help="Get files claimed by an instance"
    )
    claimed_parser.add_argument("instance_id", help="Unique instance identifier")

    # All claimed: all-claimed
    cmd.add_parser("all-claimed", help="Get all claimed files across all instances")

    # Stale detection: stale [threshold_seconds]
    stale_parser = cmd.add_parser("stale", help="Detect stale instances")
    stale_parser.add_argument(
        "threshold",
        type=float,
        default=300.0,
        nargs="?",
        help="Stale threshold in seconds (default 300)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        store = StateAPI(args.db)
    except Exception as e:
        print(f"error: failed to open database: {e}", file=sys.stderr)
        return 1

    try:
        if args.command == "register":
            success = register_instance(store, args.instance_id, args.hostname, args.pid)
            if not success:
                print(f"error: failed to register instance {args.instance_id}", file=sys.stderr)
                return 1
            print(f"registered: {args.instance_id}")
            return 0

        elif args.command == "heartbeat":
            success = heartbeat(store, args.instance_id)
            if not success:
                print(f"error: failed to send heartbeat for {args.instance_id}", file=sys.stderr)
                return 1
            print(f"heartbeat: {args.instance_id}")
            return 0

        elif args.command == "list":
            instances = list_active_instances(store)
            print(json.dumps(instances, indent=2))
            return 0

        elif args.command == "claim":
            success = claim_files(store, args.instance_id, args.files)
            if not success:
                print(f"error: failed to claim files for {args.instance_id}", file=sys.stderr)
                return 1
            print(f"claimed {len(args.files)} files: {args.instance_id}")
            return 0

        elif args.command == "release":
            success = release_files(store, args.instance_id, args.files)
            if not success:
                print(f"error: failed to release files for {args.instance_id}", file=sys.stderr)
                return 1
            print(f"released {len(args.files)} files: {args.instance_id}")
            return 0

        elif args.command == "status":
            status = get_instance_status(store, args.instance_id)
            if status is None:
                print(f"error: instance not found: {args.instance_id}", file=sys.stderr)
                return 1
            print(json.dumps(status, indent=2))
            return 0

        elif args.command == "claimed-files":
            files = get_claimed_files(store, args.instance_id)
            print(json.dumps(files, indent=2))
            return 0

        elif args.command == "all-claimed":
            claimed = get_all_claimed_files(store)
            print(json.dumps(claimed, indent=2))
            return 0

        elif args.command == "stale":
            threshold = args.threshold
            stale = detect_stale_instances(store, threshold)
            print(json.dumps(stale, indent=2))
            return 0

        else:
            print(f"error: unknown command {args.command}", file=sys.stderr)
            return 1

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
