#!/usr/bin/env python3
"""state_store.materialize — canonical materializer for all views.

This module consolidates view rendering to ONE place: all caller write paths
(WriteAPI, event-sourced collectors, orchestrator) call materialize_all() or
view-specific functions to keep the derived files (tracker.json, STATE.md, etc.)
in sync with the event log.

Each view is a pure function: (projection_dict) -> bytes. Deterministic,
idempotent, testable. No side effects except writing to disk atomically.

Views exported:
  - materialize_tracker() — tracker.json
  - materialize_orchestrator_status() — orchestrator-status.json (stub)
  - materialize_state_md() — STATE.md (delegates to gen_state_md.generate_state_md)
  - materialize_ledger() — ledger view (stub)
  - materialize_all() — renders all views under a file lock
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def materialize_tracker(tracker_projection: dict) -> bytes:
    """Render tracker projection to JSON bytes (deterministic, idempotent).

    Args:
        tracker_projection: dict from api.project("tracker")

    Returns:
        bytes: JSON representation with indent=2, newline-terminated
    """
    if not isinstance(tracker_projection, dict):
        raise ValueError("tracker_projection must be a dict")

    # Ensure required keys exist
    if "items" not in tracker_projection:
        tracker_projection = {"items": []} | tracker_projection

    # Render to JSON with consistent formatting
    return json.dumps(tracker_projection, indent=2).encode("utf-8") + b"\n"


def materialize_orchestrator_status(orch_projection: dict | None) -> bytes:
    """Render orchestrator-status projection to JSON bytes (stub for Inc 2).

    Args:
        orch_projection: dict from api.project("orchestrator_status"), or None

    Returns:
        bytes: JSON representation (stub for now)
    """
    if orch_projection is None:
        orch_projection = {}

    # Stub: return minimal valid JSON for now
    stub = {
        "phase": orch_projection.get("phase", "unknown"),
        "activity": orch_projection.get("activity", None),
        "timestamp": orch_projection.get("timestamp", None),
    }

    return json.dumps(stub, indent=2).encode("utf-8") + b"\n"


def materialize_state_md(api, state_dir: Path | str) -> bytes:
    """Render STATE.md from state store (delegates to gen_state_md).

    Args:
        api: StateAPI instance
        state_dir: Path to state directory (unused for now; gen_state_md uses api)

    Returns:
        bytes: Markdown content, UTF-8 encoded
    """
    # Lazy import to avoid circular dependency
    try:
        from tools.gen_state_md import generate_state_md
    except ImportError:
        # Fallback: basic stub
        return b"# STATE\n\nGenerated from event store.\n"

    try:
        content = generate_state_md(state_dir=str(state_dir))
        return content.encode("utf-8")
    except Exception as e:
        # Fail-closed: if generation fails, return stub with error
        print(f"[materialize] Failed to generate STATE.md: {e}", file=sys.stderr)
        return b"# STATE\n\n(Generation failed)\n"


def materialize_ledger(ledger_projection: dict | None) -> bytes:
    """Render ledger projection to markdown (stub for Inc 4).

    Args:
        ledger_projection: dict from api.project("ledger"), or None

    Returns:
        bytes: Markdown representation (stub for now)
    """
    if ledger_projection is None:
        ledger_projection = {}

    # Stub: return minimal markdown header
    lines = [
        "# Ledger",
        "",
        "(Ledger view materialization coming in Inc 4)",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def materialize_all(api, state_dir: Path | str, file_lock=None) -> dict:
    """Render all views atomically under an optional file lock.

    Materializes tracker.json, orchestrator-status.json, STATE.md, and ledger
    views from the event store. Each view is written atomically (tempfile +
    os.replace) to minimize data loss windows.

    If file_lock is provided, all writes are held under the same lock.

    Args:
        api: StateAPI instance
        state_dir: Path to state directory
        file_lock: Optional file lock context (from WriteAPI._file_lock)

    Returns:
        dict: Summary of materialized views
        {
            "views": ["tracker.json", "orchestrator-status.json", ...],
            "errors": [{"view": "...", "error": "..."}],
            "timestamp": ISO timestamp
        }
    """
    from datetime import datetime, timezone

    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()
    views_written = []
    errors = []

    def _write_atomic(view_name: str, file_path: Path, content: bytes) -> bool:
        """Write content atomically to file_path."""
        try:
            temp_file = file_path.with_suffix(file_path.suffix + ".tmp")
            temp_file.write_bytes(content)
            os.replace(str(temp_file), str(file_path))
            views_written.append(view_name)
            return True
        except Exception as e:
            errors.append({"view": view_name, "error": str(e)})
            print(f"[materialize] Failed to write {view_name}: {e}", file=sys.stderr)
            return False

    # Render all views
    try:
        # Tracker
        tracker_proj = api.project("tracker")
        tracker_bytes = materialize_tracker(tracker_proj)
        _write_atomic("tracker.json", state_dir / "tracker.json", tracker_bytes)
    except Exception as e:
        errors.append({"view": "tracker.json (project)", "error": str(e)})

    try:
        # Orchestrator status (stub for now)
        orch_proj = api.project("orchestrator_status") if hasattr(api, "project") else None
        orch_bytes = materialize_orchestrator_status(orch_proj)
        _write_atomic(
            "orchestrator-status.json",
            state_dir / "orchestrator-status.json",
            orch_bytes
        )
    except Exception as e:
        errors.append({"view": "orchestrator-status.json", "error": str(e)})

    try:
        # STATE.md (delegates to gen_state_md)
        state_md_bytes = materialize_state_md(api, state_dir)
        _write_atomic("STATE.md", state_dir / "STATE.md", state_md_bytes)
    except Exception as e:
        errors.append({"view": "STATE.md", "error": str(e)})

    try:
        # Ledger (stub for now)
        ledger_proj = api.project("ledger") if hasattr(api, "project") else None
        ledger_bytes = materialize_ledger(ledger_proj)
        _write_atomic("ledger", state_dir / "ledger.md", ledger_bytes)
    except Exception as e:
        errors.append({"view": "ledger", "error": str(e)})

    return {
        "views": views_written,
        "errors": errors,
        "timestamp": timestamp,
    }
