"""tools.multi_dispatch — Multi-instance-aware dispatch wrapper.
INDEX: Multi-instance-aware dispatch wrapper; resolves the backend through `multibox_config.build_backend()` (so the hard preflight gate runs before any claim), atomic ClaimBackend.claim() when multibox.enabled=True, legacy advisory check_conflict+claim_files when off; `--config` names aesop.config.json and an unreadable one is fail-closed exit 1 (silently falling back to the advisory path is the exact "flag looked on, coordination was off" failure the gate exists to prevent); exit 1 on ClaimConflict or preflight refusal, no record written

Coordinates dispatch work across multiple instances by:
  1. Checking if files are already claimed by other instances (legacy) or
     atomically claiming files via ClaimBackend (when multibox.enabled=True)
  2. Executing the dispatch
  3. Releasing files when complete

Ensures no two instances work on the same files simultaneously (fail-closed:
if we cannot claim files or release them, we do not proceed).

The multibox block is resolved by tools.multibox_config.build_backend
(precedence: env > aesop.config.json > default).

When multibox.enabled=False (default): uses legacy advisory claim_files path
(instance_projection), which has TOCTOU window but maintains backward
compatibility. No multibox module is imported and no probe runs.

When multibox.enabled=True: the HARD startup preflight runs first and refuses
to proceed unless the event-store DB is on local storage and the share's
measured visibility delay and clock skew are inside the configured bounds.
Only then does dispatch use atomic ClaimBackend.claim() to fix the TOCTOU race.
A refused preflight exits 1 with the documented mount remedies -- fail-closed.

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
from state_store.claim_backend import ClaimConflict
from state_store.identity import get_instance_id
from tools.multibox_config import (
    MultiboxConfigError,
    MultiboxPreflightRefused,
    build_backend,
)
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
        "--config",
        help="Path to aesop.config.json (for multibox.enabled flag)",
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

    # Load config for the multibox block. An explicitly named config that
    # cannot be read is fail-closed: silently continuing on the advisory path
    # would be exactly the "flag looked on, coordination was off" failure the
    # hard gate exists to prevent.
    config = {}
    if args.config:
        try:
            import json

            with open(args.config, encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print(f"error: failed to load config {args.config}: {e}",
                  file=sys.stderr)
            return 1

    # build_backend parses the multibox block (env > config > default) and, when
    # multibox is enabled, runs the HARD startup preflight before handing back a
    # backend. At the shipped default it returns None without importing or
    # touching anything, so the legacy advisory path below is byte-for-byte
    # unchanged.
    try:
        backend = build_backend(args.db, config, repo_root=str(ROOT))
    except (MultiboxConfigError, MultiboxPreflightRefused) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    lease_id = None

    try:
        # Atomic claim if multibox enabled, else advisory claim
        if backend is not None:
            # Multibox enabled: use atomic ClaimBackend.claim()
            try:
                lease_id = backend.claim(args.files, instance_id, ttl_seconds=300)
                print(f"claimed {len(args.files)} files (atomic)", file=sys.stderr)
            except ClaimConflict as e:
                print(
                    f"error: files are claimed by {e.conflicting_instance}",
                    file=sys.stderr,
                )
                return 1
        else:
            # Multibox disabled (default): use legacy advisory path
            # Check for conflicts (advisory, has TOCTOU window)
            conflicting_instance = check_conflict(store, args.files, instance_id)
            if conflicting_instance:
                print(
                    f"error: files are claimed by {conflicting_instance}",
                    file=sys.stderr,
                )
                return 1

            # Claim files (advisory)
            if not claim_files(store, instance_id, args.files):
                print(f"error: failed to claim files", file=sys.stderr)
                return 1

            print(f"claimed {len(args.files)} files (advisory)", file=sys.stderr)

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
        if backend is not None and lease_id is not None:
            # Atomic release
            try:
                backend.release(lease_id, instance_id)
                print(f"released {len(args.files)} files (atomic)", file=sys.stderr)
            except Exception as e:
                print(f"error: failed to release files: {e}", file=sys.stderr)
                return 1
        else:
            # Advisory release
            if not release_files(store, instance_id, args.files):
                print(f"error: failed to release files", file=sys.stderr)
                return 1

            print(f"released {len(args.files)} files (advisory)", file=sys.stderr)

        if not dispatch_success:
            return 2

        return 0

    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            if backend is not None:
                backend.close()
        except Exception:
            pass
        try:
            store.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
