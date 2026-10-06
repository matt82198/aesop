"""state_store.instance_projection — Instance lifecycle and coordination projection.

Tracks instance registration, heartbeat, and file claim state for multi-instance
orchestration. Uses the same SQLite WAL + events pattern as the core state_store.

Event types:
  - instance_registered: instance startup {instance_id, hostname, pid, registered_at}
  - instance_heartbeat: periodic liveness pulse {instance_id, heartbeat_at}
  - instance_failed: instance failure {instance_id, failed_at, reason}
  - file_claim_requested: instance wants to work on files {instance_id, file_paths, claimed_at}
  - file_claim_released: instance done with files {instance_id, file_paths, released_at}

Projection tables:
  - instances: current instance state (id, hostname, pid, registered_at, last_heartbeat, status)
  - file_claims: who is working on which files

Uses the existing StateAPI + EventStore pattern; interoperable with other projections.
"""
from __future__ import annotations

import time
from typing import Any

# Retry configuration (matches store.py patterns)
_MAX_DB_LOCK_RETRIES = 3
_DB_LOCK_RETRY_BASE_DELAY = 0.05


def register_instance(store, instance_id: str, hostname: str, pid: int) -> bool:
    """Register a new instance or update existing registration.

    Appends an instance_registered event to the 'instances' stream, marking the
    instance as active. If the instance is already registered, this updates its
    registration timestamp and status.

    Args:
        store: StateAPI or EventStore instance
        instance_id: unique identifier for this instance (hostname:pid:nonce)
        hostname: hostname of the machine running this instance
        pid: process ID of the orchestrator

    Returns:
        bool: True on success, False on error
    """
    try:
        store.append(
            "instances",
            "instance_registered",
            {
                "instance_id": instance_id,
                "hostname": hostname,
                "pid": pid,
                "registered_at": time.time(),
            },
            actor=instance_id,
        )
        return True
    except Exception:
        return False


def heartbeat(store, instance_id: str) -> bool:
    """Record a heartbeat for an instance to signal it is still alive.

    Appends an instance_heartbeat event, updating the last_heartbeat timestamp.
    Fail-closed: if the heartbeat append fails, returns False.

    Args:
        store: StateAPI or EventStore instance
        instance_id: unique identifier for this instance

    Returns:
        bool: True if heartbeat recorded, False on error
    """
    try:
        store.append(
            "instances",
            "instance_heartbeat",
            {
                "instance_id": instance_id,
                "heartbeat_at": time.time(),
            },
            actor=instance_id,
        )
        return True
    except Exception:
        return False


def claim_files(store, instance_id: str, file_paths: list[str]) -> bool:
    """Claim ownership of a set of files for this instance to work on.

    ADVISORY ONLY: appends a file_claim_requested event without atomicity.
    This function is for projection/dashboard tracking, NOT mutual exclusion.
    For atomic coordinated claiming, use state_store.claim_backend.ClaimBackend
    (Inc 2 multibox fix; enabled via multibox.enabled config flag).

    Other instances should skip files already claimed. Fail-closed: if claim
    fails, returns False.

    Args:
        store: StateAPI or EventStore instance
        instance_id: unique identifier for this instance
        file_paths: list of file paths being claimed (absolute paths)

    Returns:
        bool: True if claim recorded, False on error
    """
    if not file_paths:
        return True
    try:
        store.append(
            "instances",
            "file_claim_requested",
            {
                "instance_id": instance_id,
                "file_paths": file_paths,
                "claimed_at": time.time(),
            },
            actor=instance_id,
        )
        return True
    except Exception:
        return False


def release_files(store, instance_id: str, file_paths: list[str]) -> bool:
    """Release ownership of files after work completes.

    Appends a file_claim_released event. Idempotent: releasing unclaimed files
    is a no-op.

    Args:
        store: StateAPI or EventStore instance
        instance_id: unique identifier for this instance
        file_paths: list of file paths being released

    Returns:
        bool: True if release recorded, False on error
    """
    if not file_paths:
        return True
    try:
        store.append(
            "instances",
            "file_claim_released",
            {
                "instance_id": instance_id,
                "file_paths": file_paths,
                "released_at": time.time(),
            },
            actor=instance_id,
        )
        return True
    except Exception:
        return False


def list_active_instances(
    store, stale_threshold_seconds: float = 300.0
) -> list[dict[str, Any]]:
    """List all currently active instances (not stale, not failed).

    An instance is considered active if:
      - It has been registered
      - Its last heartbeat is more recent than stale_threshold_seconds ago
      - It has not been marked as failed

    Args:
        store: StateAPI or EventStore instance
        stale_threshold_seconds: consider heartbeat older than this as stale (default 300s)

    Returns:
        List of dicts: {instance_id, hostname, pid, registered_at, last_heartbeat, status}
    """
    try:
        events = _read_instances_events(store)
        return _project_active_instances(events, stale_threshold_seconds)
    except Exception:
        return []


def get_instance_status(
    store, instance_id: str, stale_threshold_seconds: float = 300.0
) -> dict[str, Any] | None:
    """Get current status of a specific instance.

    Returns instance metadata including last heartbeat time and active status.

    Args:
        store: StateAPI or EventStore instance
        instance_id: unique identifier for the instance
        stale_threshold_seconds: consider heartbeat older than this as stale

    Returns:
        dict with instance metadata if found, None if not registered
    """
    try:
        events = _read_instances_events(store)
        instances = _project_active_instances(events, stale_threshold_seconds)
        for inst in instances:
            if inst["instance_id"] == instance_id:
                return inst
        # Check if instance exists but is stale/failed
        all_instances = _project_all_instances(events)
        for inst in all_instances:
            if inst["instance_id"] == instance_id:
                return inst
        return None
    except Exception:
        return None


def get_claimed_files(store, instance_id: str) -> list[str]:
    """Get list of files currently claimed by an instance.

    Returns paths for files this instance has claimed and not yet released.

    Args:
        store: StateAPI or EventStore instance
        instance_id: unique identifier for the instance

    Returns:
        List of file paths (absolute paths)
    """
    try:
        events = _read_instances_events(store)
        return _project_claimed_files(events, instance_id)
    except Exception:
        return []


def get_all_claimed_files(store) -> dict[str, list[str]]:
    """Get all file claims across all active instances.

    Returns a map of instance_id -> list of claimed file paths.

    Args:
        store: StateAPI or EventStore instance

    Returns:
        dict mapping instance_id to list of claimed file paths
    """
    try:
        events = _read_instances_events(store)
        return _project_all_claimed_files(events)
    except Exception:
        return {}


def detect_stale_instances(
    store,
    stale_threshold_seconds: float = 300.0,
    source: Any = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Detect instances that have not sent a heartbeat recently.

    An instance is stale if:
      - It was registered but has not sent a heartbeat
      - Its last heartbeat is older than stale_threshold_seconds

    Stale instances can have their claims forcibly released by other instances
    (e.g., after a crash).

    TRANSPORT-AWARE SOURCE (multibox Inc 5). Tier L reads heartbeats out of the
    ``instances`` event stream, one appended event per beat. Tier S cannot afford
    that -- 3-5 boxes beating every 10s would flood a log that every peer re-reads
    in full -- so it publishes one atomically REPLACED file per instance
    (``state_store.failover.HeartbeatDir``) and passes it here as ``source``.

    Only the SOURCE of the heartbeat changes. The threshold semantics and the 300s
    default are identical on both transports, and so is the shape of what comes
    back, so callers and the reclamation path need no transport awareness at all.

    Args:
        store: StateAPI or EventStore instance. Ignored (and may be None) when
            ``source`` is given.
        stale_threshold_seconds: threshold in seconds (default 300s = 5min)
        source: optional Tier-S heartbeat source -- a list of heartbeat records, a
            callable returning them, or any object exposing ``read_heartbeats()``.
            When None (the default) the event stream is used, unchanged.
        now: reference time in epoch seconds; defaults to ``time.time()``. Present
            so both paths are testable without sleeping.

    Returns:
        List of stale instance dicts, oldest heartbeat first. Fail-closed: any
        error yields ``[]`` (nothing reported stale, so nothing is reclaimed on
        the strength of evidence we could not read).
    """
    try:
        if source is not None:
            return _detect_stale_from_heartbeats(
                source, stale_threshold_seconds, now
            )
        events = _read_instances_events(store)
        return _detect_stale_instances(events, stale_threshold_seconds, now)
    except Exception:
        return []


def _detect_stale_from_heartbeats(
    source: Any, stale_threshold_seconds: float, now: float | None
) -> list[dict[str, Any]]:
    """Fold Tier-S heartbeat records into the same stale-instance shape.

    The record readers live in ``failover`` because they belong to the transport,
    not to this projection; the import is local so this low-level module does not
    depend on the failover layer at import time.
    """
    from state_store.failover import (  # local: avoids inverting the layering
        _latest_beat_per_instance,
        _read_heartbeat_source,
    )

    reference = time.time() if now is None else float(now)
    records = _read_heartbeat_source(source)

    stale: list[dict[str, Any]] = []
    for rec in _latest_beat_per_instance(records):
        last_heartbeat = float(rec.get("heartbeat_at", reference))
        if (reference - last_heartbeat) <= stale_threshold_seconds:
            continue
        try:
            epoch = int(rec.get("epoch", 1))
        except (TypeError, ValueError):
            epoch = 1
        stale.append({
            "instance_id": rec.get("instance_id"),
            "epoch": epoch,
            "registered_at": float(rec.get("registered_at", last_heartbeat)),
            "last_heartbeat": last_heartbeat,
            "status": "active",
        })

    return sorted(stale, key=lambda x: x["last_heartbeat"])


# ---------------------------------------------------------------------------
# Private projection helpers
# ---------------------------------------------------------------------------


def _read_instances_events(store) -> list[dict]:
    """Read all events from the instances stream.

    Handles both StateAPI (.get) and raw EventStore (.read) interfaces.
    """
    getter = getattr(store, "get", None)
    if callable(getter):
        return getter("instances")
    return store.read("instances")


def _project_active_instances(
    events: list[dict], stale_threshold_seconds: float
) -> list[dict[str, Any]]:
    """Fold instances events into active (non-stale, non-failed) instance state."""
    now = time.time()
    state = {}  # instance_id -> {hostname, pid, registered_at, last_heartbeat, status, failed_at}

    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload") or {}
        instance_id = payload.get("instance_id")

        if not instance_id:
            continue

        if etype == "instance_registered":
            if instance_id not in state:
                state[instance_id] = {
                    "instance_id": instance_id,
                    "hostname": payload.get("hostname", ""),
                    "pid": payload.get("pid", 0),
                    "registered_at": payload.get("registered_at", now),
                    "last_heartbeat": payload.get("registered_at", now),
                    "status": "active",
                    "failed_at": None,
                }
            else:
                # Update registration
                state[instance_id].update(
                    {
                        "registered_at": payload.get("registered_at", now),
                        "status": "active",
                        "failed_at": None,
                    }
                )

        elif etype == "instance_heartbeat":
            if instance_id in state:
                state[instance_id]["last_heartbeat"] = payload.get("heartbeat_at", now)

        elif etype == "instance_failed":
            if instance_id in state:
                state[instance_id]["status"] = "failed"
                state[instance_id]["failed_at"] = payload.get("failed_at", now)

    # Filter to active instances (not failed, not stale)
    active = []
    for inst in state.values():
        if inst["status"] == "failed":
            continue
        # Check if stale
        if (now - inst["last_heartbeat"]) > stale_threshold_seconds:
            continue
        active.append(inst)

    return sorted(active, key=lambda x: x["registered_at"])


def _project_all_instances(events: list[dict]) -> list[dict[str, Any]]:
    """Fold instances events into all instance state (including stale/failed)."""
    now = time.time()
    state = {}

    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload") or {}
        instance_id = payload.get("instance_id")

        if not instance_id:
            continue

        if etype == "instance_registered":
            if instance_id not in state:
                state[instance_id] = {
                    "instance_id": instance_id,
                    "hostname": payload.get("hostname", ""),
                    "pid": payload.get("pid", 0),
                    "registered_at": payload.get("registered_at", now),
                    "last_heartbeat": payload.get("registered_at", now),
                    "status": "active",
                    "failed_at": None,
                }
            else:
                state[instance_id].update(
                    {
                        "registered_at": payload.get("registered_at", now),
                        "status": "active",
                        "failed_at": None,
                    }
                )

        elif etype == "instance_heartbeat":
            if instance_id in state:
                state[instance_id]["last_heartbeat"] = payload.get("heartbeat_at", now)

        elif etype == "instance_failed":
            if instance_id in state:
                state[instance_id]["status"] = "failed"
                state[instance_id]["failed_at"] = payload.get("failed_at", now)

    return sorted(state.values(), key=lambda x: x["registered_at"])


def _detect_stale_instances(
    events: list[dict], stale_threshold_seconds: float, now: float | None = None
) -> list[dict[str, Any]]:
    """Detect instances whose last heartbeat is older than threshold (Tier L)."""
    now = time.time() if now is None else float(now)
    state = {}

    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload") or {}
        instance_id = payload.get("instance_id")

        if not instance_id:
            continue

        if etype == "instance_registered":
            if instance_id not in state:
                state[instance_id] = {
                    "instance_id": instance_id,
                    "registered_at": payload.get("registered_at", now),
                    "last_heartbeat": payload.get("registered_at", now),
                    "status": "active",
                }

        elif etype == "instance_heartbeat":
            if instance_id in state:
                state[instance_id]["last_heartbeat"] = payload.get("heartbeat_at", now)

        elif etype == "instance_failed":
            if instance_id in state:
                state[instance_id]["status"] = "failed"

    stale = []
    for inst in state.values():
        if inst["status"] == "failed":
            continue
        if (now - inst["last_heartbeat"]) > stale_threshold_seconds:
            stale.append(inst)

    return sorted(stale, key=lambda x: x["last_heartbeat"])


def _project_claimed_files(events: list[dict], instance_id: str) -> list[str]:
    """Get files currently claimed by a specific instance (not yet released)."""
    claimed = set()

    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload") or {}
        if payload.get("instance_id") != instance_id:
            continue

        if etype == "file_claim_requested":
            paths = payload.get("file_paths") or []
            claimed.update(paths)

        elif etype == "file_claim_released":
            paths = payload.get("file_paths") or []
            for p in paths:
                claimed.discard(p)

    return sorted(claimed)


def _project_all_claimed_files(events: list[dict]) -> dict[str, list[str]]:
    """Get all claimed files across all instances."""
    claimed_by_instance = {}

    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload") or {}
        instance_id = payload.get("instance_id")

        if not instance_id:
            continue

        if instance_id not in claimed_by_instance:
            claimed_by_instance[instance_id] = set()

        if etype == "file_claim_requested":
            paths = payload.get("file_paths") or []
            claimed_by_instance[instance_id].update(paths)

        elif etype == "file_claim_released":
            paths = payload.get("file_paths") or []
            for p in paths:
                claimed_by_instance[instance_id].discard(p)

    # Convert sets to sorted lists, remove empty entries
    result = {}
    for instance_id, paths in claimed_by_instance.items():
        if paths:
            result[instance_id] = sorted(paths)

    return result
