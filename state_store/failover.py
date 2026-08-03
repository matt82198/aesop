"""state_store.failover -- stale-instance detection, primary election, generation fencing.

Multibox Increment 5. Answers one question the claim log alone cannot: *which* of
3-5 peers is currently driving, and how a peer that was partitioned away is stopped
from resuming when it comes back.

Three pieces, in dependency order.

1. Tier-S heartbeats (:class:`HeartbeatDir`)
--------------------------------------------
Tier L (single box) keeps appending ``instance_heartbeat`` events. Tier S cannot:
5 boxes beating every 10s would append ~43k records a day to a log whose every
read is a full directory listing. So a Tier-S heartbeat is a single file per
``(instance_id, epoch)``, **atomically REPLACED** rather than appended --
``instances/<instance_id>.<epoch>.hb`` -- so the directory stays bounded by fleet
size forever. ``os.replace`` gives the same publish-atomically guarantee the claim
log relies on, and the same fsync ordering (Inc 4b).

``detect_stale_instances`` in ``instance_projection`` gains an optional
transport-aware ``source``; threshold semantics and the 300s default are unchanged.

2. Primary election (:func:`elect_primary`)
-------------------------------------------
The primary is simply *the holder of one reserved resource*,
``orchestrator_lock``, taken through the **same claim protocol as any file** --
same settle window, same deterministic sort key, same TTL-expiry-at-fold. There is
no separate election algorithm, no consensus, no quorum: the shared log is the
arbiter. A primary that stops renewing has its lock expire at fold time and any
live instance may take it.

3. Fencing generations (:class:`FencingToken`)
----------------------------------------------
TTL takeover alone is not safe. The classic failure is a primary that was merely
*partitioned*, not dead: its lock expires, a successor takes over, and then the old
primary returns still believing it is in charge. Both would drive.

So every takeover bumps a **generation**, and every coordination write carries
``(instance_id, epoch, generation)``. A write whose generation is below the fold's
current generation is REJECTED. Monotonic generation is the fence; the old primary's
writes are refused forever, without any of the peers having to agree on anything.

The generation is carried **inside the claim record** as a second claimed path,
``orchestrator_lock/gen/<NNNNNNNNNNNN>`` (see :func:`generation_token`). That is not
a trick to smuggle a field in -- it is load-bearing: because the token is itself a
claimed path, two instances racing to take generation N collide on the *token* as
well as on the lock, so a generation can never be occupied twice.

Purity
------
:func:`fold_primary` is a **pure function over a list of record dicts** -- no
filesystem, no clock, no sleeps -- exactly like ``fold_fs_claims``, which it reuses
for liveness rather than reimplementing. That is the whole correctness surface and
the entire TDD lever; everything below it is plumbing.

Reachable only when ``multibox.enabled`` (wired in Inc 7). Tier L is untouched.

Stdlib only: json, os, time, dataclasses, pathlib.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from state_store.claim_backend import ClaimConflict
from state_store.fs_claim_log import (
    FS_UNKNOWN_HOLDER,
    FS_UNKNOWN_PATH,
    KIND_CLAIM_REQUESTED,
    _fsync_dir,
    _sanitize_for_filename,
    fold_fs_claims,
)

#: The reserved resource whose holder IS the primary. Claimed through the ordinary
#: claim protocol; nothing about it is special-cased inside the backend.
RESERVED_PRIMARY_RESOURCE = "orchestrator_lock"

#: Prefix of the per-generation companion path (see module docstring).
GENERATION_PREFIX = RESERVED_PRIMARY_RESOURCE + "/gen/"
#: Zero-padded width, so the token sorts lexically in numeric order too.
_GENERATION_WIDTH = 12

#: Default lifetime of the primary lock. Deliberately shorter than the 300s claim
#: TTL: a dead primary should be replaced in seconds, while a dead worker's file
#: claims may safely wait out a longer lease.
DEFAULT_PRIMARY_TTL_SECONDS = 60.0

#: Suffix of a Tier-S heartbeat file.
HEARTBEAT_SUFFIX = ".hb"
#: In-progress name for the atomic replace. Deliberately NOT ``.hb`` so a half
#: written beat is invisible to readers.
HEARTBEAT_TEMP_SUFFIX = ".hb.tmp"

#: Unchanged from ``instance_projection``: 300s.
DEFAULT_STALE_THRESHOLD_SECONDS = 300.0


class FencedWriteError(RuntimeError):
    """A write was refused because its generation is below the current one.

    Raised by :func:`assert_fenced`. This is the split-brain guard: the returning
    old primary is told, definitively, that it is no longer in charge. It is a
    programming-visible refusal rather than a silent drop so the caller cannot
    mistake a fenced write for a successful one.
    """

    def __init__(self, instance_id, token_generation, current_generation):
        self.instance_id = instance_id
        self.token_generation = int(token_generation)
        self.current_generation = int(current_generation)
        super().__init__(
            "write from %r fenced out: generation %d < current generation %d"
            % (instance_id, self.token_generation, self.current_generation)
        )


class UnsupportedBackendError(TypeError):
    """The backend cannot expose its raw records, so no generation can be read.

    Fail-closed on purpose. A backend whose log cannot be folded cannot prove who
    the primary is, and guessing "nobody" would hand the lock to a second driver.
    """


# ---------------------------------------------------------------------------
# Generation tokens
# ---------------------------------------------------------------------------

def generation_token(generation: int) -> str:
    """Return the companion claim path that CARRIES generation ``generation``.

    Claimed alongside ``orchestrator_lock`` so that the generation lives in the
    claim record itself, and so two instances racing for the same generation
    conflict on the token as well as on the lock.
    """
    return "%s%0*d" % (GENERATION_PREFIX, _GENERATION_WIDTH, int(generation))


def generation_of_paths(paths) -> Optional[int]:
    """Extract the generation carried by a claim record's ``paths``, or None."""
    if not isinstance(paths, list):
        return None
    for path in paths:
        if not isinstance(path, str) or not path.startswith(GENERATION_PREFIX):
            continue
        try:
            return int(path[len(GENERATION_PREFIX):])
        except ValueError:
            return None
    return None


def _as_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# The pure fold
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PrimaryState:
    """Who is driving, at which generation, and whether that is knowable.

    Attributes:
        instance_id: the live holder of the lock; ``None`` when the lock is
            vacant, ``FS_UNKNOWN_HOLDER`` when a corrupt record makes holdership
            unknowable (fail-closed).
        generation: the FENCE -- the highest generation ever recorded in this log,
            live or dead. Never decreases while the records survive, which is
            precisely what stops a returning old primary.
        holder_generation: the generation the live holder claimed at; 0 when vacant.
        epoch: the holder's Inc 3 boot epoch, or None.
        lease_id: the holder's lease, needed to renew or release the lock.
        unknown: True iff a live corrupt record blocks the answer.
    """

    instance_id: Optional[str]
    generation: int
    holder_generation: int
    epoch: Optional[int]
    lease_id: Optional[str]
    unknown: bool = False

    @property
    def vacant(self) -> bool:
        """True iff no live instance holds the lock and the log is readable."""
        return self.instance_id is None and not self.unknown

    @property
    def fenced(self) -> bool:
        """True iff the live holder is already behind the fence.

        Transient at worst -- two lock records cannot both be live for long,
        because they conflict on the lock path -- but worth surfacing rather than
        hiding, since a holder in this state has already lost the right to write.
        """
        return self.instance_id is not None and self.holder_generation < self.generation

    def token(self) -> Optional["FencingToken"]:
        """The fencing token this holder must stamp on every coordination write."""
        if self.instance_id is None:
            return None
        return FencingToken(
            instance_id=self.instance_id,
            epoch=_as_int(self.epoch, 1),
            generation=self.holder_generation,
        )


def fold_primary(
    records: list,
    now: Optional[float] = None,
    max_skew: float = 0.0,
) -> PrimaryState:
    """Fold claim-log records into the current primary and generation.

    PURE: no filesystem, no clock, no sleeps. Liveness is delegated to
    ``fold_fs_claims`` rather than reimplemented, so every invariant already proven
    there -- lowest sort key wins, TTL enforced at fold time, tombstone releases,
    ``max_skew`` only lengthens, corrupt records fail closed -- applies to the
    primary lock unchanged, for free.

    The generation returned is the maximum over ALL lock claim requests present,
    **including expired and tombstoned ones**. That is deliberate: a dead record is
    still evidence that a generation was once issued, and forgetting it would let
    the fence go backwards.

    Args:
        records: record dicts in any order (corrupt sentinels included).
        now: reference time in epoch seconds; defaults to ``time.time()``.
        max_skew: bound on cross-box clock disagreement, in seconds.

    Returns:
        PrimaryState.
    """
    folded = fold_fs_claims(records, now=now, max_skew=max_skew, detail=True)
    unknown = FS_UNKNOWN_PATH in folded

    generation = 0
    by_lease: dict = {}
    for rec in records:
        if not isinstance(rec, dict) or rec.get("__corrupt__"):
            continue
        if rec.get("kind") != KIND_CLAIM_REQUESTED:
            continue
        paths = rec.get("paths")
        if not isinstance(paths, list) or RESERVED_PRIMARY_RESOURCE not in paths:
            continue
        claimed = generation_of_paths(paths)
        if claimed is None:
            # A lock record with no generation token predates fencing. It still
            # counts as a holder, but it can never outrank a fenced generation.
            claimed = 0
        generation = max(generation, claimed)
        by_lease[rec.get("lease_id")] = (
            claimed, rec.get("instance_id"), _as_int(rec.get("epoch"), 1),
        )

    if unknown:
        # Holdership is unknowable; report the fence we can still prove.
        return PrimaryState(
            instance_id=FS_UNKNOWN_HOLDER,
            generation=generation,
            holder_generation=generation,
            epoch=None,
            lease_id=None,
            unknown=True,
        )

    winner = folded.get(RESERVED_PRIMARY_RESOURCE)
    if winner is None:
        return PrimaryState(None, generation, 0, None, None, False)

    holder_id, lease_id = winner
    claimed, rec_instance, rec_epoch = by_lease.get(lease_id, (0, holder_id, 1))
    return PrimaryState(
        instance_id=rec_instance or holder_id,
        generation=generation,
        holder_generation=claimed,
        epoch=rec_epoch,
        lease_id=lease_id,
        unknown=False,
    )


# ---------------------------------------------------------------------------
# Fencing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FencingToken:
    """The ``(instance_id, epoch, generation)`` stamp every coordination write carries.

    ``epoch`` (Inc 3) fences a RESTARTED process against its own prior incarnation;
    ``generation`` fences a PARTITIONED primary against its successor. They are
    different failure modes and both are needed.
    """

    instance_id: str
    epoch: int
    generation: int

    def as_dict(self) -> dict:
        """Serializable form, for stamping onto a coordination payload."""
        return {
            "instance_id": self.instance_id,
            "epoch": int(self.epoch),
            "generation": int(self.generation),
        }


def assert_fenced(token: FencingToken, current_generation: int) -> None:
    """Raise unless ``token`` is still entitled to write.

    THE fence. One comparison, and it is load-bearing: without it a returning old
    primary's writes are indistinguishable from the successor's and land normally
    (``tests/test_failover.py`` proves exactly that by writing through the unguarded
    path and watching it succeed).

    Equal generations pass: the current primary writes at its own generation.

    Raises:
        FencedWriteError: ``token.generation < current_generation``.
    """
    if token is None:
        raise FencedWriteError(None, -1, current_generation)
    if int(token.generation) < int(current_generation):
        raise FencedWriteError(
            token.instance_id, token.generation, current_generation
        )


def fenced_write(
    token: FencingToken,
    current_generation: int,
    write: Callable[..., Any],
    *args,
    **kwargs,
):
    """Perform ``write`` only if ``token`` clears the fence.

    Fail-closed: the guard runs BEFORE the write, so a fenced caller produces no
    partial effect at all.

    Returns:
        Whatever ``write`` returns.

    Raises:
        FencedWriteError: the token is behind the current generation.
    """
    assert_fenced(token, current_generation)
    return write(*args, **kwargs)


def fenced_backend_write(
    backend,
    token: FencingToken,
    write: Callable[..., Any],
    *args,
    now: Optional[float] = None,
    max_skew: Optional[float] = None,
    **kwargs,
):
    """:func:`fenced_write` with the current generation read live from the log.

    The generation is re-derived from the shared log on every call rather than
    cached, because the whole point is that a partitioned primary learns of its
    replacement only by reading what its peers wrote.
    """
    state = observe_primary(backend, now=now, max_skew=max_skew)
    return fenced_write(token, state.generation, write, *args, **kwargs)


# ---------------------------------------------------------------------------
# Election over a ClaimBackend
# ---------------------------------------------------------------------------

def backend_records(backend) -> list:
    """Read the raw claim-log records behind ``backend``.

    Prefers a public ``read_records()``; falls back to ``FsClaimLog._read_records``.
    A backend offering neither cannot be folded for a generation, so this refuses
    rather than assuming an empty log -- assuming "no records" would read as "no
    primary" and immediately hand the lock to a second driver.

    Raises:
        UnsupportedBackendError: no records surface available.
    """
    for name in ("read_records", "_read_records"):
        reader = getattr(backend, name, None)
        if callable(reader):
            return reader()
    raise UnsupportedBackendError(
        "backend %r exposes no record listing; failover cannot fold a generation"
        % type(backend).__name__
    )


def _backend_now(backend, now: Optional[float]) -> float:
    if now is not None:
        return float(now)
    clock = getattr(backend, "_clock", None)
    if callable(clock):
        return float(clock())
    return time.time()


def _backend_skew(backend, max_skew: Optional[float]) -> float:
    if max_skew is not None:
        return float(max_skew)
    return float(getattr(backend, "max_skew_seconds", 0.0) or 0.0)


def observe_primary(
    backend,
    now: Optional[float] = None,
    max_skew: Optional[float] = None,
) -> PrimaryState:
    """Read-only: who holds the lock right now. Never writes, never takes over."""
    return fold_primary(
        backend_records(backend),
        now=_backend_now(backend, now),
        max_skew=_backend_skew(backend, max_skew),
    )


def elect_primary_state(
    backend,
    now: Optional[float] = None,
    instance_id: Optional[str] = None,
    epoch: int = 1,
    ttl_seconds: float = DEFAULT_PRIMARY_TTL_SECONDS,
    max_skew: Optional[float] = None,
) -> PrimaryState:
    """Confirm the sitting primary, or take over if the lock has lapsed.

    Never pre-empts a live holder -- takeover happens only once the lock's TTL has
    expired at fold time, which is the same reclamation rule every file claim
    already obeys.

    A takeover claims ``[orchestrator_lock, generation_token(current + 1)]`` in ONE
    call, so the generation bump goes through the same settle window and the same
    all-or-nothing grant as any other claim. Losing that race is not an error: the
    log is re-folded and the actual winner is reported, so N simultaneous
    challengers converge on one answer without any of them retrying.

    Args:
        backend: a ClaimBackend exposing its records (see :func:`backend_records`).
        now: reference time; defaults to the backend's clock.
        instance_id: the challenger. ``None`` means observe only.
        epoch: the challenger's Inc 3 boot epoch, recorded for fencing.
        ttl_seconds: lifetime of the primary lock.
        max_skew: clock-skew bound; defaults to the backend's.

    Returns:
        PrimaryState after the attempt.
    """
    now = _backend_now(backend, now)
    max_skew = _backend_skew(backend, max_skew)

    state = fold_primary(backend_records(backend), now=now, max_skew=max_skew)
    if state.instance_id is not None or instance_id is None:
        # Somebody live holds it (or holdership is unknowable), or we were only
        # asked to look. Either way: no write.
        return state

    target = generation_token(state.generation + 1)
    try:
        backend.claim(
            [RESERVED_PRIMARY_RESOURCE, target], instance_id, ttl_seconds
        )
    except ClaimConflict:
        pass  # a peer beat us to it; the re-fold below reports the real winner
    return fold_primary(backend_records(backend), now=now, max_skew=max_skew)


def elect_primary(
    backend,
    now: Optional[float] = None,
    instance_id: Optional[str] = None,
    epoch: int = 1,
    ttl_seconds: float = DEFAULT_PRIMARY_TTL_SECONDS,
    max_skew: Optional[float] = None,
) -> tuple:
    """``(instance_id, generation)`` of the primary after election.

    Thin tuple view of :func:`elect_primary_state`; use that when the lease_id is
    needed (to renew or release the lock).
    """
    state = elect_primary_state(
        backend, now=now, instance_id=instance_id, epoch=epoch,
        ttl_seconds=ttl_seconds, max_skew=max_skew,
    )
    if state.instance_id is None:
        return (None, state.generation)
    return (state.instance_id, state.holder_generation)


# ---------------------------------------------------------------------------
# Tier-S heartbeats: replaced, never appended
# ---------------------------------------------------------------------------

class HeartbeatDir:
    """One atomically-replaced file per ``(instance_id, epoch)``.

    Tier L appends an ``instance_heartbeat`` event per beat, which is fine for one
    box. Tier S would turn that into an unbounded log that every peer re-reads in
    full; 5 boxes at 10s is ~43k records a day. Replacing a fixed file instead
    keeps the directory bounded by fleet size forever, and ``os.replace`` publishes
    it atomically so a reader never sees a half-written beat.

    Freshness is read from the record body, not the file mtime -- except when the
    body will not parse, where mtime is the only evidence left. That case is
    treated as ALIVE, not stale: mistakenly declaring a live peer dead would let
    its claims be reclaimed underneath it, so unreadable evidence must fail toward
    "still running".

    ``instance_id`` contains ``:``, illegal in a Windows filename, so the filename
    carries a sanitized form and the true id is always read from the body -- the
    same split ``FsClaimLog`` uses for record names.
    """

    def __init__(self, heartbeats_dir: str, clock: Optional[Callable[[], float]] = None):
        """Create a heartbeat directory view.

        Args:
            heartbeats_dir: directory on the shared filesystem. Created lazily.
            clock: callable returning epoch seconds; injectable so tests need no sleeps.
        """
        self.heartbeats_dir = str(heartbeats_dir)
        self._clock = clock or time.time

    def _name(self, instance_id: str, epoch: int) -> str:
        return "%s.%d%s" % (
            _sanitize_for_filename(instance_id), int(epoch), HEARTBEAT_SUFFIX
        )

    def beat(
        self,
        instance_id: str,
        epoch: int = 1,
        hostname: str = "",
        pid: int = 0,
    ) -> dict:
        """Publish this instance's liveness, REPLACING its previous beat.

        Durability order matches Inc 4b: write temp -> flush -> fsync bytes ->
        ``os.replace`` to publish the name atomically -> fsync the directory entry.

        Returns:
            The record that was written.
        """
        directory = Path(self.heartbeats_dir)
        directory.mkdir(parents=True, exist_ok=True)

        record = {
            "v": 1,
            "instance_id": instance_id,
            "epoch": int(epoch),
            "hostname": hostname,
            "pid": int(pid),
            "heartbeat_at": float(self._clock()),
        }
        name = self._name(instance_id, epoch)
        final_path = directory / name
        temp_path = directory / (
            name[: -len(HEARTBEAT_SUFFIX)] + HEARTBEAT_TEMP_SUFFIX
        )
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(final_path))
        _fsync_dir(directory)
        return record

    def read_heartbeats(self) -> list:
        """Every instance's latest beat.

        An unreadable directory returns ``[]`` -- "we know of no instances", which
        reports nothing stale and therefore reclaims nothing. An I/O error must
        never read as "everyone is dead".
        """
        directory = Path(self.heartbeats_dir)
        if not directory.is_dir():
            return []
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return []

        records: list = []
        for name in names:
            if not name.endswith(HEARTBEAT_SUFFIX):
                continue  # a ``.hb.tmp`` was never published
            fpath = directory / name
            try:
                with open(fpath, encoding="utf-8") as handle:
                    rec = json.load(handle)
                if not isinstance(rec, dict) or not rec.get("instance_id"):
                    raise ValueError("heartbeat is not a record")
                rec.setdefault("epoch", 1)
                rec.setdefault("heartbeat_at", os.path.getmtime(fpath))
                records.append(rec)
            except Exception:
                # Unreadable body: fall back to mtime and treat as ALIVE.
                try:
                    mtime = os.path.getmtime(fpath)
                except OSError:
                    continue  # vanished under us
                records.append({
                    "v": 1,
                    "instance_id": name[: -len(HEARTBEAT_SUFFIX)],
                    "epoch": 0,
                    "heartbeat_at": mtime,
                    "corrupt": True,
                })
        return records

    def __call__(self) -> list:
        """Callable form, so the object can be passed straight as a stale ``source``."""
        return self.read_heartbeats()

    def forget(self, instance_id: str, epoch: int = 1) -> bool:
        """Remove one instance's beat on clean shutdown. Idempotent."""
        try:
            os.remove(str(Path(self.heartbeats_dir) / self._name(instance_id, epoch)))
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Observability surface (consumed by fleet_multibox_summary in Inc 7)
# ---------------------------------------------------------------------------

def multibox_staleness_summary(
    backend=None,
    heartbeat_source=None,
    now: Optional[float] = None,
    stale_threshold_seconds: float = DEFAULT_STALE_THRESHOLD_SECONDS,
    max_skew: Optional[float] = None,
) -> dict:
    """One JSON-shaped dict describing who is alive, who is primary, and the fence.

    Inc 5 does not CHANGE stale-claim reclamation -- that stays fold-TTL behaviour
    in ``fold_fs_claims``. It makes staleness *observable*, so the MCP
    ``fleet_multibox_summary`` tool can render it. This function only exposes data;
    it never writes and never reclaims.

    Every section degrades independently: an unreadable backend yields
    ``primary: None`` with ``degraded`` naming what failed, rather than an
    exception that blanks the whole dashboard.

    Args:
        backend: ClaimBackend to fold for the primary and held paths; optional.
        heartbeat_source: callable returning heartbeat records, or an object with
            ``read_heartbeats()`` (e.g. :class:`HeartbeatDir`); optional.
        now: reference time; defaults to the backend's clock or wall clock.
        stale_threshold_seconds: unchanged 300s semantics.
        max_skew: clock-skew bound; defaults to the backend's.

    Returns:
        ``{now, stale_threshold_seconds, primary, generation, instances,
        stale_instances, held_paths, unknown_holder, degraded}``.
    """
    now = _backend_now(backend, now) if backend is not None else (
        float(now) if now is not None else time.time()
    )
    degraded: list = []

    summary: dict = {
        "now": now,
        "stale_threshold_seconds": float(stale_threshold_seconds),
        "primary": None,
        "generation": 0,
        "instances": [],
        "stale_instances": [],
        "held_paths": {},
        "unknown_holder": False,
        "degraded": degraded,
    }

    if backend is not None:
        try:
            skew = _backend_skew(backend, max_skew)
            records = backend_records(backend)
            state = fold_primary(records, now=now, max_skew=skew)
            summary["generation"] = state.generation
            summary["unknown_holder"] = state.unknown
            if state.instance_id is not None:
                summary["primary"] = {
                    "instance_id": state.instance_id,
                    "generation": state.holder_generation,
                    "epoch": state.epoch,
                    "lease_id": state.lease_id,
                    "fenced": state.fenced,
                }
            folded = fold_fs_claims(records, now=now, max_skew=skew)
            summary["held_paths"] = {
                path: holder for path, holder in folded.items()
                if path != RESERVED_PRIMARY_RESOURCE
                and not path.startswith(GENERATION_PREFIX)
            }
        except Exception as exc:
            degraded.append("backend: %s" % type(exc).__name__)

    if heartbeat_source is not None:
        try:
            records = _read_heartbeat_source(heartbeat_source)
            instances = []
            for rec in _latest_beat_per_instance(records):
                age = now - float(rec.get("heartbeat_at", now))
                instances.append({
                    "instance_id": rec.get("instance_id"),
                    "epoch": _as_int(rec.get("epoch"), 1),
                    "last_heartbeat": float(rec.get("heartbeat_at", now)),
                    "age_seconds": age,
                    "stale": age > float(stale_threshold_seconds),
                })
            instances.sort(key=lambda item: item["last_heartbeat"])
            summary["instances"] = instances
            summary["stale_instances"] = [
                item["instance_id"] for item in instances if item["stale"]
            ]
        except Exception as exc:
            degraded.append("heartbeats: %s" % type(exc).__name__)

    return summary


def _read_heartbeat_source(source) -> list:
    """Accept a callable, or anything exposing ``read_heartbeats()``, or a list."""
    if isinstance(source, list):
        return source
    reader = getattr(source, "read_heartbeats", None)
    if callable(reader):
        return reader()
    if callable(source):
        return source()
    raise UnsupportedBackendError(
        "heartbeat source %r is neither callable nor a reader" % type(source).__name__
    )


def _latest_beat_per_instance(records) -> list:
    """Keep the newest beat per instance_id (an instance may have several epochs)."""
    latest: dict = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        instance_id = rec.get("instance_id")
        if not instance_id:
            continue
        beat_at = rec.get("heartbeat_at")
        try:
            beat_at = float(beat_at)
        except (TypeError, ValueError):
            continue
        current = latest.get(instance_id)
        if current is None or beat_at > float(current.get("heartbeat_at", 0.0)):
            latest[instance_id] = rec
    return list(latest.values())
