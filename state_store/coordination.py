"""state_store.coordination — lease-by-append claim for exclusive resource access.

Implements DB-native CLAIM (lease-via-append) on top of the existing event log:
to claim resource R, append a claim_requested event to the 'claims' stream;
the winner is the lowest-version append for that resource key; readers fold
the stream to see who holds it.

Fail-CLOSED by construction: if you cannot append or read, you do NOT hold
the claim and must not dispatch. TTL + claim_released events handle crashed
holders (analogous to lock.mjs PID-liveness staleness, but expressed as events).

No risky changes to store.py or api.py — uses only existing append/read primitives.

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
        # Fail-closed: any exception means we don't hold the claim
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
    Returns None if the resource is not claimed or has been released.

    Args:
        store: StateAPI or EventStore instance (must have get() method)
        resource: the resource identifier to query

    Returns:
        The instance_id of the current holder, or None if unclaimed.
    """
    try:
        events = _read_claim_events(store)
        claims = fold_claims(events)
        return claims.get(resource)
    except Exception:
        # Fail-closed: on any error, we cannot determine the holder
        return None
