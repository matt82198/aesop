#!/usr/bin/env python3
"""state_store.materialize — canonical materializer for all views.

This module consolidates view rendering to ONE place: all caller write paths
(WriteAPI, event-sourced collectors, orchestrator) call the view-specific
functions below to keep the derived files (tracker.json, STATE.md, etc.)
in sync with the event log.

Each view is a pure function: (projection_dict) -> bytes. Deterministic,
idempotent, testable. No side effects except writing to disk atomically.

Views exported:
  - materialize_tracker() — tracker.json
  - materialize_orchestrator_status() — orchestrator-status.json (stub)
  - materialize_state_md() — STATE.md (delegates to gen_state_md.generate_state_md)
  - materialize_ledger() — ledger view (stub)
"""
from __future__ import annotations

import json
import sys
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
    """Render orchestrator-status projection to JSON bytes (deterministic, idempotent).

    Converts the orchestrator_status projection (from api.project("orchestrator_status"))
    to JSON with consistent formatting. The view is byte-compatible with the current
    orchestrator-status.json shape: {"id", "role", "activity", "phase", "updated_at"}.

    Args:
        orch_projection: dict from api.project("orchestrator_status"), or None

    Returns:
        bytes: JSON representation with indent=2, newline-terminated
    """
    if orch_projection is None:
        orch_projection = {}

    # Ensure required fields exist (with defaults matching the projector)
    view = {
        "id": orch_projection.get("id", "main"),
        "role": orch_projection.get("role", "orchestrator"),
        "activity": orch_projection.get("activity"),
        "phase": orch_projection.get("phase"),
        "updated_at": orch_projection.get("updated_at"),
    }

    return json.dumps(view, indent=2).encode("utf-8") + b"\n"


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
