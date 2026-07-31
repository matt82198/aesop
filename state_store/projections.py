"""state_store.projections — fold tracker events into current state.

``project_tracker`` reconstructs the tracker.json shape
(``{"version": 1, "items": [...]}``) from the ``tracker`` event stream:

  - ``item_created``  : payload is the full item dict; establishes the item
                        (kept in first-seen order).
  - ``item_updated``  : payload is ``{"id": <id>, ...partial fields}``; merges
                        the given fields onto the existing item.
  - ``item_archived`` : payload is ``{"id": <id>}`` (+ optional
                        ``completed_at``); sets ``status`` to ``"archived"``.

Unknown ids on update/archive and unknown event types are ignored, keeping the
fold tolerant of partial/legacy streams.

Snapshots enable O(n) tail-replay (instead of replaying full log each time):
  - save_snapshot(store, stream, event_version, projection): persist materialized state
  - project_tracker_with_snapshot(store, stream, events): load snapshot, fold tail events
"""
from __future__ import annotations

TRACKER_VERSION = 1


def _fold_events(events: list, order: list | None = None, items: dict | None = None) -> tuple:
    """Fold events into (order, items) state.

    Helper used by both project_tracker and project_tracker_with_snapshot to avoid
    duplicating the folding logic. Mutates order and items in place.

    Args:
        events: list of event dicts
        order: existing order list (item ids in first-seen order); mutated in place
        items: existing items dict (id -> item dict); mutated in place

    Returns:
        (order, items) tuple with accumulated state
    """
    if order is None:
        order = []
    if items is None:
        items = {}

    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload") or {}
        if etype == "item_created":
            iid = payload.get("id")
            if iid is None:
                continue
            if iid not in items:
                order.append(iid)
            items[iid] = dict(payload)
        elif etype == "item_updated":
            iid = payload.get("id")
            if iid in items:
                merged = dict(items[iid])
                for key, value in payload.items():
                    if key != "id":
                        merged[key] = value
                items[iid] = merged
        elif etype == "item_archived":
            iid = payload.get("id")
            if iid in items:
                merged = dict(items[iid])
                merged["status"] = "archived"
                if "completed_at" in payload:
                    merged["completed_at"] = payload["completed_at"]
                items[iid] = merged
        # any other event type is ignored
    return (order, items)


def project_tracker(events: list) -> dict:
    """Return the current tracker state projected from ``events``.

    This is the original full-replay projection. For production use with snapshots,
    prefer project_tracker_with_snapshot() which enables tail-replay.
    """
    order, items = _fold_events(events)
    return {"version": TRACKER_VERSION, "items": [items[i] for i in order]}


def project_tracker_with_snapshot(store, stream: str, events: list) -> dict:
    """Project tracker state using snapshots for O(n) tail-replay instead of O(n²) full replay.

    If a valid snapshot exists for the stream, starts from the snapshot state and folds
    only events after the snapshot's version. If snapshot is missing or corrupt, falls
    back to full replay from event 1 (graceful degradation).

    Args:
        store: EventStore instance with read_snapshot() method
        stream: stream name (e.g., "tracker")
        events: list of all events for the stream (from store.read(stream))

    Returns:
        dict: {"version": 1, "items": [...]} projection, identical to project_tracker()
    """
    snapshot = store.read_snapshot(stream)

    if snapshot is not None:
        # Snapshot exists and is valid; start from it and fold tail events
        snapshot_version, projection, _ = snapshot
        order = [item["id"] for item in projection.get("items", [])]
        items = {item["id"]: dict(item) for item in projection.get("items", [])}

        # Fold only events after the snapshot
        tail_events = [ev for ev in events if ev.get("version", 0) > snapshot_version]
        order, items = _fold_events(tail_events, order, items)
    else:
        # No valid snapshot; do full replay
        order, items = _fold_events(events)

    return {"version": TRACKER_VERSION, "items": [items[i] for i in order]}


def save_snapshot(store, stream: str, event_version: int, projection: dict) -> None:
    """Persist a materialized projection snapshot.

    Args:
        store: EventStore instance with save_snapshot() method
        stream: stream name (e.g., "tracker")
        event_version: the per-stream event version this snapshot was computed through
        projection: the materialized state dict (e.g. full tracker projection)
    """
    store.save_snapshot(stream, event_version, projection)


AGENT_LIFECYCLE_VERSION = 1


def project_agent_lifecycle(events: list) -> dict:
    """Project current agent lifecycle state from agent_* events.

    Folds agent lifecycle events into a map of agent_id -> {state, transitions, last_activity}.

    Event types:
    - agent_dispatched: payload {"agent_id", "timestamp"}; marks dispatch start
    - agent_working: payload {"agent_id", "timestamp"}; marks work in progress
    - agent_done: payload {"agent_id", "timestamp"}; marks completion
    - agent_stalled: payload {"agent_id", "timestamp"}; marks stall/error

    Returns a dict: {"version": 1, "agents": [{"id", "state", "transitions", "last_activity"}, ...]}
    where transitions is a list of {state, at (ISO 8601 timestamp)} in chronological order.
    """
    agents = {}

    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload") or {}

        if etype in ("agent_dispatched", "agent_working", "agent_done", "agent_stalled"):
            agent_id = payload.get("agent_id")
            timestamp = payload.get("timestamp")

            if agent_id is None:
                continue

            # Initialize agent if first time seeing it
            if agent_id not in agents:
                agents[agent_id] = {
                    "id": agent_id,
                    "state": None,
                    "transitions": [],
                    "last_activity": None,
                }

            # Map event type to state
            state_map = {
                "agent_dispatched": "dispatch",
                "agent_working": "working",
                "agent_done": "done",
                "agent_stalled": "stalled",
            }
            new_state = state_map.get(etype)

            # Only append transition if state actually changed
            if new_state and new_state != agents[agent_id]["state"]:
                agents[agent_id]["state"] = new_state
                if timestamp:
                    agents[agent_id]["transitions"].append({
                        "state": new_state,
                        "at": timestamp,
                    })
                    agents[agent_id]["last_activity"] = timestamp

    # Return as versioned list (ordered by agent_id for determinism)
    agent_list = sorted(agents.values(), key=lambda a: a["id"])
    return {"version": AGENT_LIFECYCLE_VERSION, "agents": agent_list}


def project_orchestrator_status(events: list) -> dict:
    """Project current orchestrator status from orchestrator_status events.

    Folds `phase_changed`, `activity_changed`, `status_cleared`, and historical
    `meta`/`phase_set` events into the orchestrator-status.json shape:
    {"id", "role", "activity", "phase", "updated_at"}.

    Event types:
    - `phase_changed`: payload {"phase": str, "actor": str} (from Inc 2+)
    - `activity_changed`: payload {"activity": str, "actor": str} (from Inc 2+)
    - `status_cleared`: payload {} (from Inc 2+)
    - `meta`/`phase_set`: payload {"phase": str} (historical, from reconcile.py --resolve)

    The view is byte-compatible with the current orchestrator-status.json shape.
    Unknown event types and missing payloads are ignored (graceful degradation).
    """
    status = {
        "id": "main",
        "role": "orchestrator",
        "activity": None,
        "phase": None,
        "updated_at": None,
    }

    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload") or {}

        if etype == "phase_changed":
            phase = payload.get("phase")
            if phase:
                status["phase"] = phase
                # Update timestamp only if it's not already set or if this is newer
                if "timestamp" in payload and payload["timestamp"]:
                    status["updated_at"] = payload["timestamp"]

        elif etype == "activity_changed":
            activity = payload.get("activity")
            if activity is not None:
                status["activity"] = activity
                if "timestamp" in payload and payload["timestamp"]:
                    status["updated_at"] = payload["timestamp"]

        elif etype == "status_cleared":
            # Clear all fields except id/role
            status["activity"] = None
            status["phase"] = None
            status["updated_at"] = None

        elif etype == "meta" or etype == "phase_set":
            # Historical events from reconcile.py --resolve; fold forward
            phase = payload.get("phase")
            if phase:
                status["phase"] = phase

    return status
