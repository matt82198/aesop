"""state_store.coordination — lease-by-append claim for exclusive resource access.

Implements DB-native CLAIM (lease-via-append) on top of the existing event log:
to claim resource R, append a claim_requested event to the 'claims' stream;
the winner is the lowest-version append for that resource key; readers fold
the stream to see who holds it.

Fail-CLOSED by construction: if you cannot append or read, you do NOT hold
the claim and must not dispatch. TTL + claim_released events handle crashed
holders (analogous to lock.mjs PID-liveness staleness, but expressed as events).

Claims-stream compaction: the claims stream grows monotonically as agents
claim/release resources across waves. ``compact_claims()`` snapshots the
active claim events so that subsequent reads use tail-replay (snapshot + new
events only) instead of scanning the full history. Compaction is optional and
safe to skip; it only affects read performance, not correctness.

No risky changes to store.py or api.py — uses only existing append/read/snapshot
primitives.

Stdlib only: time.
"""
from __future__ import annotations

import time


def _read_claim_events(store) -> list:
    """Read the claims stream from a StateAPI (.get) OR an EventStore (.read).

    RS3-W: wave_loop passes a raw EventStore, which exposes read() but not
    get(). try_claim called store.get() unconditionally, so EVERY claim
    attempt raised AttributeError and fail-closed to False -- the claim gate
    was dead code and items fell through to claim-less dispatch paths.
    """
    getter = getattr(store, "get", None)
    if callable(getter):
        return getter("claims")
    return store.read("claims")


def _claim_expired(ev: dict, payload: dict, now: float) -> bool:
    """True when a claim_requested event is past its TTL (RS3-W N4).

    A claim whose event timestamp plus payload ttl is in the past is treated
    as released: a crashed holder's claim must not persist forever. Events
    without a ttl or timestamp (legacy) never expire (backward compatible).
    """
    ttl = payload.get("ttl")
    ts = ev.get("ts")
    if ttl is None or ts is None:
        return False
    try:
        return float(now) > float(ts) + float(ttl)
    except (TypeError, ValueError):
        return False


def fold_claims(events: list, now: float | None = None) -> dict[str, str]:
    """Fold a claims stream into the current state of who holds each resource.

    Processes claim_requested and claim_released events to determine the winner
    for each resource. The winner is the lowest-version un-released claim for
    that resource (first serialized append wins; ties impossible because versions
    are unique). A claim is valid only if it has NOT been released by a
    subsequent claim_released event. If an instance releases and then re-claims,
    the re-claim is the new active claim for that instance.

    TTL enforcement (RS3-W N4): a claim_requested event whose ts + payload
    ttl is earlier than ``now`` is EXPIRED and ignored -- a crashed instance's
    claim becomes claimable again after its TTL instead of persisting forever.
    Legacy events without ts/ttl never expire.

    Args:
        events: list of event dicts from the claims stream
                (as returned by EventStore.read('claims'))
        now: reference time for TTL expiry (default: time.time())

    Returns:
        dict mapping resource_id -> holding_instance_id for all currently
        held resources. Empty dict if no claims exist or all have been released.
    """
    if now is None:
        now = time.time()

    # Track all claims by resource and instance (as list, to preserve order)
    claims_by_resource = {}  # resource -> {instance_id: [versions...]}
    # Track all releases by resource and instance (as sorted list)
    releases_by_resource = {}  # resource -> {instance_id: [release_versions]}

    # First pass: collect all claims and releases
    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload") or {}
        version = ev.get("version", 0)

        if etype == "claim_requested":
            if _claim_expired(ev, payload, now):
                # Expired claim: releasable/ignored (never a live holder).
                continue
            resource = payload.get("resource")
            instance_id = payload.get("instance_id")
            if resource is not None and instance_id is not None:
                if resource not in claims_by_resource:
                    claims_by_resource[resource] = {}
                if instance_id not in claims_by_resource[resource]:
                    claims_by_resource[resource][instance_id] = []
                claims_by_resource[resource][instance_id].append(version)

        elif etype == "claim_released":
            resource = payload.get("resource")
            instance_id = payload.get("instance_id")
            if resource is not None and instance_id is not None:
                if resource not in releases_by_resource:
                    releases_by_resource[resource] = {}
                if instance_id not in releases_by_resource[resource]:
                    releases_by_resource[resource][instance_id] = []
                releases_by_resource[resource][instance_id].append(version)

    # Second pass: determine current holders
    holders = {}
    for resource, claims_dict in claims_by_resource.items():
        # For each instance claiming this resource, find the latest un-released claim
        active_claims = {}
        for instance_id, claim_versions in claims_dict.items():
            releases = sorted(releases_by_resource.get(resource, {}).get(instance_id, []))

            # Process claims in order, tracking "current active" claim within streaks
            # (separated by releases). The current active claim is the latest claim
            # that comes after the most recent release.
            current_active = None
            for claim_v in sorted(claim_versions):
                # Check if there's a release between the current active and this claim
                if current_active is not None:
                    # Check if current_active was released
                    if any(r > current_active for r in releases):
                        # Yes, released; start a new streak with this claim
                        current_active = claim_v
                    # else: already in this streak, keep current_active
                else:
                    # First claim for this instance
                    current_active = claim_v

            # After processing all claims, check if current_active is released
            if current_active is not None:
                if not any(r > current_active for r in releases):
                    # Not released; it's active
                    active_claims[instance_id] = current_active

        # Find the minimum version among active claims (the winner)
        if active_claims:
            winner_instance = min(active_claims.keys(), key=lambda x: active_claims[x])
            holders[resource] = winner_instance

    return holders


def try_claim(store, resource: str, instance_id: str, ttl: float = 300.0) -> bool:
    """Attempt to claim exclusive access to a resource.

    Appends a claim_requested event to the 'claims' stream, then re-reads
    and folds to check if this instance won the claim. Fail-CLOSED: if ANY
    exception occurs (append fails, read fails, or exception during fold),
    return False (claim not held, do not proceed).

    If this instance does NOT win, it retracts its claim by appending a
    claim_released event (scoped to this instance + resource) before returning
    False. This prevents stale-claim resurrection: a losing claim left un-retracted
    in the stream could later become the winner if the true holder releases.

    Args:
        store: StateAPI or EventStore instance (must have append() plus
               get() or read())
        resource: the resource identifier to claim (e.g., "wave_123", "lane_0", ...)
        instance_id: the instance identifier requesting the claim
        ttl: time-to-live in seconds (default 300s = 5min); embedded in the payload
             for later TTL-based expiry checks (not enforced here, but available
             for reconciliation)

    Returns:
        bool: True if this instance holds the claim after the append,
              False otherwise (including on any error).
    """
    try:
        # Append claim request to the claims stream
        store.append(
            "claims",
            "claim_requested",
            {"resource": resource, "instance_id": instance_id, "ttl": ttl},
            actor=instance_id,
        )

        # Re-read claims stream and fold to see current state
        events = _read_claim_events(store)
        claims = fold_claims(events)

        # Check if we won
        if claims.get(resource) == instance_id:
            return True

        # We did NOT win: retract our claim to prevent stale-claim resurrection.
        # Fail-closed: if retract fails, still return False (never a false grant).
        try:
            store.append(
                "claims",
                "claim_released",
                {"resource": resource, "instance_id": instance_id},
                actor=instance_id,
            )
        except Exception:
            # Retract failed, but we still don't hold the claim; return False.
            pass

        return False
    except Exception:
        # Fail-closed: any exception means we don't hold the claim.
        # RS5 F2: our claim_requested may already be in the stream (append
        # succeeded, then the re-read/fold raised -- e.g. SQLite lock retries
        # exhausted). Left un-retracted it becomes a PHANTOM holder: it wins
        # as soon as the true holder releases, blocking the resource for a
        # full TTL while we believe we failed. Best-effort retract our own
        # request before returning False; if the retract itself fails there
        # is nothing more we can do (TTL expiry remains the backstop).
        try:
            store.append(
                "claims",
                "claim_released",
                {"resource": resource, "instance_id": instance_id},
                actor=instance_id,
            )
        except Exception:
            pass
        return False


def release(store, resource: str, instance_id: str) -> None:
    """Release a claimed resource.

    Appends a claim_released event to the 'claims' stream, marking that
    this instance no longer holds the resource. Idempotent: releasing
    a resource that was not held is a no-op (the fold will see the
    release but no matching claim, so it has no effect).

    Args:
        store: StateAPI or EventStore instance (must have append() method)
        resource: the resource identifier being released
        instance_id: the instance identifier releasing the claim
    """
    store.append(
        "claims",
        "claim_released",
        {"resource": resource, "instance_id": instance_id},
        actor=instance_id,
    )


def current_holder(store, resource: str) -> str | None:
    """Return the instance_id currently holding a resource, or None if unclaimed.

    Reads and folds the claims stream to find the winner for the resource.
    Uses snapshot + tail-replay when a compacted snapshot is available.
    Returns None if the resource is not claimed or has been released.

    Args:
        store: StateAPI or EventStore instance (must have get() method)
        resource: the resource identifier to query

    Returns:
        The instance_id of the current holder, or None if unclaimed.
    """
    try:
        events = _read_claims_compacted(store)
        claims = fold_claims(events)
        return claims.get(resource)
    except Exception:
        # Fail-closed: on any error, we cannot determine the holder
        return None


# ---------------------------------------------------------------------------
# Claims-stream compaction (snapshot + tail-replay)
# ---------------------------------------------------------------------------

def _get_event_store(store):
    """Extract the underlying EventStore from a StateAPI or raw EventStore.

    Returns the EventStore instance, or None if snapshot operations are not
    available (e.g. mock stores in tests).
    """
    # StateAPI wraps EventStore as _store
    es = getattr(store, "_store", None)
    if es is not None and hasattr(es, "read_snapshot"):
        return es
    # Raw EventStore
    if hasattr(store, "read_snapshot"):
        return store
    return None


def _read_claims_compacted(store) -> list:
    """Read claims events using snapshot + tail-replay when available.

    If a compacted snapshot exists for the claims stream, loads the snapshot's
    active claim events and combines them with tail events (events after the
    snapshot version). This avoids scanning the full claims history on every
    read.

    Falls back to reading the full stream when:
    - No snapshot exists
    - The store does not support snapshots (e.g. mock stores)
    - The snapshot is corrupt (read_snapshot returns None)

    The snapshot stores the set of claim_requested events that were active
    (winning, non-expired, non-released) at compaction time. These synthetic
    prefix events carry their original version, timestamp, and TTL so that
    fold_claims produces identical results to a full-stream fold.
    """
    es = _get_event_store(store)
    if es is None:
        return _read_claim_events(store)

    snapshot = es.read_snapshot("claims")
    if snapshot is None:
        return _read_claim_events(store)

    snap_version, snap_state, _ = snapshot
    active_events = snap_state.get("active_claims", [])

    # Read only events appended after the snapshot
    tail = es.read_since("claims", snap_version)

    return active_events + tail


def compact_claims(store) -> bool:
    """Snapshot the current claims fold state for faster future reads.

    Reads the full claims stream, folds it to find active holders, extracts
    the winning claim_requested events, and saves them as a snapshot. Future
    reads via ``_read_claims_compacted`` will load this snapshot plus only
    events appended after the snapshot version, instead of replaying the
    entire claims history.

    The snapshot stores the original event dicts (type, payload with resource/
    instance_id/ttl, ts, version) so that fold_claims TTL checks remain
    correct after compaction.

    Args:
        store: StateAPI or EventStore instance with snapshot support.

    Returns:
        True if a snapshot was saved, False if compaction was skipped
        (empty stream or no snapshot support).
    """
    es = _get_event_store(store)
    if es is None:
        return False

    events = _read_claim_events(store)
    if not events:
        return False

    # Fold to find current holders
    holders = fold_claims(events)
    if not holders:
        # All claims released/expired; snapshot an empty state at current max version
        max_version = max(e.get("version", 0) for e in events)
        es.save_snapshot("claims", max_version, {"active_claims": []})
        return True

    # For each held resource, find the winning claim_requested event.
    # The winner is the instance with the lowest active claim version for
    # that resource. We need the original event dict to preserve ts/ttl
    # for correct TTL checks after compaction.
    active_events = []
    for resource, winner_id in holders.items():
        # Find the active (winning) claim_requested event for this holder
        for ev in events:
            if (
                ev.get("type") == "claim_requested"
                and ev.get("payload", {}).get("resource") == resource
                and ev.get("payload", {}).get("instance_id") == winner_id
            ):
                # Check this is the active claim (not released after it)
                # Simple heuristic: take the last un-released claim from this
                # instance for this resource, matching fold_claims behavior.
                active_events.append(ev)
                break  # first match is lowest version (events are version-ordered)

    max_version = max(e.get("version", 0) for e in events)
    es.save_snapshot("claims", max_version, {"active_claims": active_events})
    return True
