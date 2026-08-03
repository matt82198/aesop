"""tools.instance_manager — Multi-instance orchestration manager.
INDEX: Multi-instance coordination CLI (register/heartbeat/list/claim/release/status); respects AESOP_STATE_ROOT env var for db path; --json flag for JSON output on all subcommands; validates status response is dict (exit 2 on contract violation)

High-level CLI wrapper around state_store.instance_projection for instance
registration, discovery, heartbeat, and file claim coordination.

CLI Usage:
  python tools/instance_manager.py [--db <db_path>] [--json] register <instance_id> <hostname> <pid>
  python tools/instance_manager.py [--db <db_path>] [--json] heartbeat <instance_id>
  python tools/instance_manager.py [--db <db_path>] [--json] list
  python tools/instance_manager.py [--db <db_path>] [--json] claim <instance_id> <file1> <file2> ...
  python tools/instance_manager.py [--db <db_path>] [--json] release <instance_id> <file1> <file2> ...
  python tools/instance_manager.py [--db <db_path>] [--json] status <instance_id>
  python tools/instance_manager.py [--db <db_path>] [--json] claimed-files <instance_id>
  python tools/instance_manager.py [--db <db_path>] [--json] all-claimed
  python tools/instance_manager.py [--db <db_path>] [--json] stale <threshold_seconds>

Environment variables:
  AESOP_STATE_ROOT: Base directory for state files; db defaults to $AESOP_STATE_ROOT/state.db

Exit codes:
  0 = success
  1 = error or operation failed
  2 = contract violation (e.g., malformed status response)
"""
from __future__ import annotations

import argparse
import json
import os
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


def resolve_db_path(cli_db_arg: str | None) -> str:
    """Resolve database path: CLI arg -> AESOP_STATE_ROOT env var -> default.

    Follows core invariant from tools/CLAUDE.md: all state tools fall back to
    AESOP_STATE_ROOT environment variable before defaulting to ./state.

    Args:
        cli_db_arg: --db argument (may be None if not provided)

    Returns:
        Resolved path to SQLite database
    """
    if cli_db_arg:
        return cli_db_arg

    env_root = os.environ.get("AESOP_STATE_ROOT")
    if env_root:
        return os.path.join(env_root, "state.db")

    return "./state/state.db"


def main():
    parser = argparse.ArgumentParser(
        description="Multi-instance orchestration manager"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to state_store SQLite database (default: AESOP_STATE_ROOT/state.db or ./state/state.db)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (applies to all subcommands)",
    )

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

    # Resolve database path
    db_path = resolve_db_path(args.db)

    try:
        store = StateAPI(db_path)
    except Exception as e:
        print(f"error: failed to open database: {e}", file=sys.stderr)
        return 1

    try:
        if args.command == "register":
            success = register_instance(store, args.instance_id, args.hostname, args.pid)
            if not success:
                print(f"error: failed to register instance {args.instance_id}", file=sys.stderr)
                return 1
            if args.json:
                print(json.dumps({"status": "success", "instance_id": args.instance_id}))
            else:
                print(f"registered: {args.instance_id}")
            return 0

        elif args.command == "heartbeat":
            success = heartbeat(store, args.instance_id)
            if not success:
                print(f"error: failed to send heartbeat for {args.instance_id}", file=sys.stderr)
                return 1
            if args.json:
                print(json.dumps({"status": "success", "instance_id": args.instance_id}))
            else:
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
            if args.json:
                print(json.dumps({"status": "success", "instance_id": args.instance_id, "files_claimed": len(args.files)}))
            else:
                print(f"claimed {len(args.files)} files: {args.instance_id}")
            return 0

        elif args.command == "release":
            success = release_files(store, args.instance_id, args.files)
            if not success:
                print(f"error: failed to release files for {args.instance_id}", file=sys.stderr)
                return 1
            if args.json:
                print(json.dumps({"status": "success", "instance_id": args.instance_id, "files_released": len(args.files)}))
            else:
                print(f"released {len(args.files)} files: {args.instance_id}")
            return 0

        elif args.command == "status":
            status = get_instance_status(store, args.instance_id)
            if status is None:
                print(f"error: instance not found: {args.instance_id}", file=sys.stderr)
                return 1
            if not isinstance(status, dict):
                print(f"error: malformed status response (expected dict, got {type(status).__name__})", file=sys.stderr)
                return 2
            if args.json:
                print(json.dumps({"status": status}))
            else:
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
