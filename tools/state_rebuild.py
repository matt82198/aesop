#!/usr/bin/env python3
"""state_rebuild — CLI tool for rebuilding and verifying materialized views.
INDEX: Rebuild and verify materialized state views (tracker.json, STATE.md) from event store; CI gate via --check mode detects drift

Rebuilds state files (tracker.json, STATE.md, etc.) from the event store.
Also provides a --check mode for CI gates to detect drift.

Usage:
  python tools/state_rebuild.py --all [--state-root DIR]
  python tools/state_rebuild.py --view NAME [--state-root DIR]
  python tools/state_rebuild.py --check [--state-root DIR]

Exit codes:
  0 — success (or no drift detected with --check)
  1 — failure (missing DB, corrupt state, write error, or drift with --check)
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Ensure imports work
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from state_store import StateAPI
from state_store.materialize import (
    materialize_tracker,
    materialize_orchestrator_status,
    materialize_state_md,
    materialize_ledger,
)


def main():
    parser = argparse.ArgumentParser(
        description="Rebuild and verify materialized views from event store"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Rebuild all views (tracker.json, STATE.md, etc.)",
    )
    parser.add_argument(
        "--view",
        metavar="NAME",
        help="Rebuild a specific view (tracker, state-md, orch-status, ledger)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Render to memory and diff with disk (for CI gates); exit 1 on drift",
    )
    parser.add_argument(
        "--state-root",
        default=None,
        help="State directory (default: AESOP_STATE_ROOT or ./state)",
    )

    args = parser.parse_args()

    # Resolve state directory
    if args.state_root:
        state_dir = Path(args.state_root)
    else:
        state_dir = Path(os.environ.get("AESOP_STATE_ROOT", "./state"))

    state_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(state_dir / "tracker_events.db")

    # Open the event store
    try:
        api = StateAPI(db_path)
    except Exception as e:
        print(f"[rebuild] Failed to open event store at {db_path}: {e}", file=sys.stderr)
        return 1

    try:
        # Dispatch based on mode
        if args.all:
            return _rebuild_all(api, state_dir)
        elif args.view:
            return _rebuild_view(api, state_dir, args.view)
        elif args.check:
            return _check_drift(api, state_dir)
        else:
            parser.print_help()
            return 1
    finally:
        api.close()


def _rebuild_all(api, state_dir: Path) -> int:
    """Rebuild all views from the event store."""
    views_to_rebuild = [
        ("tracker", _rebuild_tracker),
        ("state-md", _rebuild_state_md),
        ("orch-status", _rebuild_orch_status),
        ("ledger", _rebuild_ledger),
    ]

    success = True
    for view_name, rebuild_func in views_to_rebuild:
        try:
            rebuild_func(api, state_dir)
            print(f"[rebuild] {view_name}: OK", file=sys.stdout)
        except Exception as e:
            print(f"[rebuild] {view_name}: FAILED ({e})", file=sys.stderr)
            success = False

    return 0 if success else 1


def _rebuild_view(api, state_dir: Path, view_name: str) -> int:
    """Rebuild a specific view."""
    views = {
        "tracker": _rebuild_tracker,
        "state-md": _rebuild_state_md,
        "orch-status": _rebuild_orch_status,
        "ledger": _rebuild_ledger,
    }

    if view_name not in views:
        print(f"[rebuild] Unknown view: {view_name}", file=sys.stderr)
        print(f"[rebuild] Available views: {', '.join(views.keys())}", file=sys.stderr)
        return 1

    try:
        views[view_name](api, state_dir)
        print(f"[rebuild] {view_name}: OK", file=sys.stdout)
        return 0
    except Exception as e:
        print(f"[rebuild] {view_name}: FAILED ({e})", file=sys.stderr)
        return 1


def _check_drift(api, state_dir: Path) -> int:
    """Render all views to memory and diff with disk (for CI drift gates)."""
    views_to_check = [
        ("tracker.json", _check_tracker),
        ("STATE.md", _check_state_md),
        ("orchestrator-status.json", _check_orch_status),
        ("ledger.md", _check_ledger),
    ]

    drifts = []
    for view_name, check_func in views_to_check:
        try:
            has_drift = check_func(api, state_dir)
            if has_drift:
                drifts.append(view_name)
                print(f"[rebuild] {view_name}: DRIFT DETECTED", file=sys.stderr)
            else:
                print(f"[rebuild] {view_name}: OK (no drift)", file=sys.stdout)
        except Exception as e:
            print(f"[rebuild] {view_name}: ERROR ({e})", file=sys.stderr)
            drifts.append(view_name)

    if drifts:
        print(
            f"[rebuild] Drift detected in {len(drifts)} view(s): {', '.join(drifts)}",
            file=sys.stderr
        )
        return 1

    print("[rebuild] All views consistent", file=sys.stdout)
    return 0


def _rebuild_tracker(api, state_dir: Path) -> None:
    """Rebuild tracker.json from event store."""
    tracker_file = state_dir / "tracker.json"
    tracker_proj = api.project("tracker")
    content = materialize_tracker(tracker_proj)
    tracker_file.write_bytes(content)


def _rebuild_state_md(api, state_dir: Path) -> None:
    """Rebuild STATE.md from event store."""
    state_file = state_dir / "STATE.md"
    content = materialize_state_md(api, state_dir)
    state_file.write_bytes(content)


def _rebuild_orch_status(api, state_dir: Path) -> None:
    """Rebuild orchestrator-status.json from event store."""
    orch_file = state_dir / "orchestrator-status.json"
    orch_proj = None
    try:
        orch_proj = api.project("orchestrator_status")
    except Exception:
        pass
    content = materialize_orchestrator_status(orch_proj)
    orch_file.write_bytes(content)


def _rebuild_ledger(api, state_dir: Path) -> None:
    """Rebuild ledger view from event store."""
    ledger_file = state_dir / "ledger.md"
    ledger_proj = None
    try:
        ledger_proj = api.project("ledger")
    except Exception:
        pass
    content = materialize_ledger(ledger_proj)
    ledger_file.write_bytes(content)


def _check_tracker(api, state_dir: Path) -> bool:
    """Check tracker.json for drift. Return True if drift detected."""
    tracker_file = state_dir / "tracker.json"
    if not tracker_file.exists():
        # If file doesn't exist, that's a drift (should be materialized)
        return True

    try:
        tracker_proj = api.project("tracker")
        expected = materialize_tracker(tracker_proj)
        actual = tracker_file.read_bytes()
        return expected != actual
    except Exception:
        return True


def _check_state_md(api, state_dir: Path) -> bool:
    """Check STATE.md for drift. Return True if drift detected."""
    state_file = state_dir / "STATE.md"
    if not state_file.exists():
        return True

    try:
        expected = materialize_state_md(api, state_dir)
        actual = state_file.read_bytes()
        return expected != actual
    except Exception:
        return True


def _check_orch_status(api, state_dir: Path) -> bool:
    """Check orchestrator-status.json for drift. Return True if drift detected."""
    orch_file = state_dir / "orchestrator-status.json"
    if not orch_file.exists():
        return True

    try:
        orch_proj = None
        try:
            orch_proj = api.project("orchestrator_status")
        except Exception:
            pass
        expected = materialize_orchestrator_status(orch_proj)
        actual = orch_file.read_bytes()
        return expected != actual
    except Exception:
        return True


def _check_ledger(api, state_dir: Path) -> bool:
    """Check ledger for drift. Return True if drift detected."""
    ledger_file = state_dir / "ledger.md"
    if not ledger_file.exists():
        return True

    try:
        ledger_proj = None
        try:
            ledger_proj = api.project("ledger")
        except Exception:
            pass
        expected = materialize_ledger(ledger_proj)
        actual = ledger_file.read_bytes()
        return expected != actual
    except Exception:
        return True


if __name__ == "__main__":
    sys.exit(main())
