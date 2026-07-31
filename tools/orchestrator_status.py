#!/usr/bin/env python3
"""Orchestrator status CLI tool - atomic status updates via event store (Inc 2+).

Usage:
  python orchestrator_status.py set --activity "dispatching wave-8" --phase audit [--id main --role orchestrator]
  python orchestrator_status.py clear

Delegates to WriteAPI for event appends and atomic view materialization.
Stdout strings remain byte-identical for shell tests and verify_dash.py assertions.
"""
import argparse
import sys
from pathlib import Path

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir

# Ensure state_store module is importable
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


def set_status(activity, phase, id=None, role=None):
    """Set orchestrator status via WriteAPI."""
    from state_store.write_api import WriteAPI

    state_dir = get_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)

    try:
        api = WriteAPI(state_dir)
        api.set_orchestrator_status(
            activity=activity,
            phase=phase,
            id=id or "main",
            role=role or "orchestrator",
            actor="orchestrator_status_cli",
        )
        # Stdout string must be byte-identical for shell tests
        print(f"[OK] Status updated: activity={activity}, phase={phase}")
    except Exception as e:
        print(f"[ERROR] Failed to set status: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            api.close()
        except Exception:
            pass


def clear_status():
    """Clear orchestrator status via WriteAPI."""
    from state_store.write_api import WriteAPI

    state_dir = get_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)

    try:
        api = WriteAPI(state_dir)
        api.clear_orchestrator_status(actor="orchestrator_status_cli")
        # The API renders a cleared projection (all fields None) to stay consistent
        # with the event log. At the CLI level "clear" must mean NO STATUS EXISTS:
        # consumers -- notably the monitor's orchestrator-idle check -- test for the
        # file's presence, and a null-filled file with a fresh mtime reads as live
        # activity. The status_cleared event preserves the audit trail either way.
        (state_dir / "orchestrator-status.json").unlink(missing_ok=True)
        # Stdout string must be byte-identical for shell tests
        print("[OK] Status cleared")
    except Exception as e:
        print(f"[ERROR] Failed to clear status: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            api.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Atomic orchestrator status updates"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # set command
    set_parser = subparsers.add_parser("set", help="Set orchestrator status")
    set_parser.add_argument("--activity", required=True, help="Activity description")
    set_parser.add_argument("--phase", required=True, help="Phase (plan, dispatch, audit, etc)")
    set_parser.add_argument("--id", default="main", help="Orchestrator ID (default: main)")
    set_parser.add_argument("--role", default="orchestrator", help="Orchestrator role (default: orchestrator)")
    
    # clear command
    subparsers.add_parser("clear", help="Clear orchestrator status")
    
    args = parser.parse_args()
    
    if args.command == "set":
        set_status(args.activity, args.phase, args.id, args.role)
    elif args.command == "clear":
        clear_status()


if __name__ == "__main__":
    main()
