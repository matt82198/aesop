#!/usr/bin/env python3
"""tests.multibox_sim -- simulated-multibox harness (multibox Increment 6).

Not a test suite: the *fixture layer* that lets `tests/test_multibox_integration.py`
and `tools/verify_multibox.py` run a fleet of instances against `FsClaimLog`
without a network, without real NFS/SMB, and without a single real sleep.

Why a simulator at all
----------------------
`FsClaimLog`'s safety argument rests on one physical assumption: a written record
becomes visible in a peer's directory listing within a bounded time D, and the
settle window exceeds D. A local tmpdir has D == 0, so every existing test of the
backend exercises the *easy* case and proves nothing about the assumption. Real
NFS/SMB differs from a tmpdir in exactly three observable ways, and all three are
observable at the **directory-listing boundary**:

* **delayed** visibility -- :class:`DelayedShareView`
* **partial / partitioned** visibility -- :class:`PartitionedShareView`
* **torn** (half-published) records -- :class:`TornWriteShareView`

So the simulator patches exactly one function, ``os.listdir`` as seen by
``state_store.fs_claim_log``, and leaves the product code untouched. Writes always
pass straight through: on a real share a writer's own write is not what lags, the
*peer's read* is.

Determinism (no wall clock anywhere)
------------------------------------
Every instance runs on a :class:`SimClock` that advances only when the injected
``sleep`` is called. A record's write time is therefore recoverable from its
filename, whose second field is the writer's ``epoch_ms`` -- so visibility is a
pure function of virtual time and needs neither ``time.time()`` nor a real delay.
Nothing in this module calls ``time.sleep`` except the deliberately-real
``settle=0.05`` smoke path in the integration suite.

The deterministic parameter box
-------------------------------
Threads give *physical* concurrency; virtual time gives the *logical* outcome. The
two agree only while no record can be virtually visible before it physically
exists. :func:`run_claim_round` guarantees that by (a) serializing the request
writes in virtual-time order and (b) holding every instance at a rendezvous inside
its settle window, so all requests exist on disk before any fold runs.

One record type escapes that ordering: the tombstone a *losing* claimant writes
after its fold. It is physically written while peers may still be folding, so the
round is deterministic only while no peer's view could include it. A tombstone
written at ``start_j + settle`` is visible to instance ``i`` iff
``start_j + delay <= start_i`` -- so keeping ``max start spread < min delay``
excludes it by construction. :func:`random_round` samples inside that box, and
:func:`round_is_deterministic` re-runs a round to prove the box holds empirically
rather than by assertion.

Stdlib only: dataclasses, hashlib, json, os, random, subprocess, sys, threading, uuid.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from state_store import fs_claim_log as _fs_claim_log  # noqa: E402
from state_store.claim_backend import ClaimConflict  # noqa: E402
from state_store.fs_claim_log import FsClaimLog, _sanitize_for_filename  # noqa: E402
from state_store.paths import canonical_claim_path  # noqa: E402

#: Hard ceiling on any rendezvous wait. A simulated round has no real work in it,
#: so anything approaching this is a deadlock, not slowness -- fail loudly rather
#: than let CI hang.
GATE_TIMEOUT_SECONDS = 30.0

#: Wall-clock ceiling for one driver subprocess.
DRIVER_TIMEOUT_SECONDS = 60.0

#: Exit code the driver uses to mimic SIGKILL (no release, no tombstone, no atexit).
KILLED_EXIT_CODE = 137

_TWO_64 = float(1 << 64)


# ---------------------------------------------------------------------------
# Virtual time
# ---------------------------------------------------------------------------

class SimClock:
    """A clock that moves only when someone waits.

    Passed to ``FsClaimLog`` as both ``clock`` and (via :meth:`sleeper`) ``sleep``,
    so the settle window costs virtual seconds and zero wall seconds. Resolution is
    deliberately quantized to 1ms -- the resolution of the ``epoch_ms`` field the
    fold's sort key actually compares -- so two distinct virtual times can never
    collide in a record.
    """

    def __init__(self, start: float = 0.0):
        """Create a clock reading ``start`` epoch-seconds."""
        self.t = quantize_ms(start)

    def __call__(self) -> float:
        """Current virtual time, in epoch seconds."""
        return self.t

    def advance(self, seconds: float) -> float:
        """Move the clock forward. Never backwards."""
        self.t = quantize_ms(self.t + max(0.0, float(seconds)))
        return self.t

    def sleeper(self, before: Optional[Callable[[float], None]] = None,
                after: Optional[Callable[[float], None]] = None) -> Callable[[float], None]:
        """Return a ``sleep``-shaped callable that advances virtual time instead.

        Args:
            before: called with the duration before the clock moves. The settle
                window is the one point inside ``claim()`` where the record is
                already published and the fold has not started, which makes it the
                natural place to hang a rendezvous.
            after: called with the duration once the clock has moved.
        """
        def _sleep(duration: float) -> None:
            if before is not None:
                before(duration)
            self.advance(duration)
            if after is not None:
                after(duration)
        return _sleep


def quantize_ms(seconds: float) -> float:
    """Round a virtual time down to whole milliseconds.

    ``FsClaimLog`` stamps ``epoch_ms = int(clock() * 1000)``, so sub-millisecond
    virtual times are invisible to the sort key. Quantizing here keeps the
    simulator's notion of "distinct instants" identical to the product's.
    """
    return int(round(float(seconds) * 1000.0)) / 1000.0


# ---------------------------------------------------------------------------
# Record names carry their own write time
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RecordName:
    """The three fields ``FsClaimLog`` encodes into a record filename.

    Attributes:
        lamport: the record's logical clock.
        epoch_ms: the writer's wall clock in ms -- the simulator's whole basis for
            deciding visibility without reading a real clock or a file mtime.
        owner_token: the sanitized instance id (``:`` is illegal in a Windows
            filename, so the record body holds the true id and the name holds this).
        name: the filename itself.
    """

    lamport: int
    epoch_ms: int
    owner_token: str
    name: str

    @property
    def written_at(self) -> float:
        """Writer's virtual time, in epoch seconds."""
        return self.epoch_ms / 1000.0


def parse_record_name(name: str) -> Optional[RecordName]:
    """Decode ``<lamport>-<epoch_ms>-<instance>-<uuid>.json``, or None.

    Returns None for anything that is not a published record -- ``.json.tmp``
    scratch files, foreign files -- which the caller then passes through
    unfiltered. A name we cannot decode is never hidden: hiding is the
    simulator's way of modelling lag, and inventing lag for an unknown file would
    be inventing a failure the real share does not have.
    """
    if not name.endswith(".json"):
        return None
    parts = name[: -len(".json")].split("-")
    if len(parts) < 3:
        return None
    try:
        lamport = int(parts[0])
        epoch_ms = int(parts[1])
    except ValueError:
        return None
    return RecordName(lamport, epoch_ms, parts[2], name)


# ---------------------------------------------------------------------------
# Share views: the three ways a network share differs from a tmpdir
# ---------------------------------------------------------------------------

class ShareView:
    """Base view: a pass-through ``listdir``.

    A view is the ONLY thing the simulator substitutes. Writes, reads of file
    bodies, fsyncs and renames all go to the real filesystem, so the durability
    machinery of Inc 4b runs for real in every simulated run.
    """

    def listdir(self, path) -> list:
        """Return the directory entries this instance can currently see."""
        return os.listdir(path)


class DelayedShareView(ShareView):
    """Hide records younger than ``delay``: the ordinary NFS/SMB attribute cache.

    This is the failure mode the settle window exists for. A peer's record is
    physically present and fsynced, and this instance's listing simply does not
    show it yet.

    The instance's OWN records are never hidden -- a writer always sees its own
    write -- which is what makes the hazard asymmetric and therefore dangerous:
    an instance can be certain it is alone while a peer is certain of the same.

    Jitter is derived from a BLAKE2b hash of ``(seed, viewer, record name)``, so it
    is per-(reader, record), stable across repeated reads, and identical on every
    re-run of the same seed. No ``random`` state, no wall clock.
    """

    def __init__(self, viewer: str, clock: Callable[[], float], delay: float,
                 jitter: float = 0.0, seed: int = 0, inner: Optional[ShareView] = None):
        """Create a per-instance delayed view.

        Args:
            viewer: this instance's id; its own records bypass the delay.
            clock: the viewer's clock, in the same domain as record ``epoch_ms``.
            delay: minimum visibility lag, in seconds.
            jitter: extra lag, in ``[0, jitter)``, sampled per record. The
                effective worst case is therefore ``delay + jitter``, which is what
                a caller must keep at or below the settle window.
            seed: makes the jitter reproducible.
            inner: view to filter (defaults to the real filesystem).
        """
        self.viewer = viewer
        self.owner_token = _sanitize_for_filename(viewer)
        self.clock = clock
        self.delay = float(delay)
        self.jitter = float(jitter)
        self.seed = int(seed)
        self.inner = inner or ShareView()

    def effective_delay(self, name: str) -> float:
        """Visibility lag this viewer experiences for one specific record."""
        if self.jitter <= 0.0:
            return self.delay
        digest = hashlib.blake2b(
            ("%d|%s|%s" % (self.seed, self.viewer, name)).encode("utf-8"),
            digest_size=8,
        ).digest()
        return self.delay + self.jitter * (int.from_bytes(digest, "big") / _TWO_64)

    def listdir(self, path) -> list:
        """Entries whose age has cleared this viewer's lag."""
        now = float(self.clock())
        visible = []
        for name in self.inner.listdir(path):
            info = parse_record_name(name)
            if info is None or info.owner_token == self.owner_token:
                visible.append(name)
                continue
            if now - info.written_at >= self.effective_delay(name):
                visible.append(name)
        return visible


class PartitionedShareView(ShareView):
    """Hide one instance's records from its peers for a window, then reveal them all.

    Models an SMB session drop and reconnect: while the partition holds, records do
    not trickle across the boundary at all; when it heals, the entire backlog
    becomes visible in one listing. The claim log's convergence property has to
    survive that step change, not just a smooth lag.

    Symmetric on purpose. A partitioned box cannot see its peers either, which is
    exactly the state that produces a stale primary in Inc 5.
    """

    def __init__(self, viewer: str, clock: Callable[[], float], isolated: str,
                 start: float, end: float, inner: Optional[ShareView] = None):
        """Create a partitioned view.

        Args:
            viewer: this instance's id.
            clock: the viewer's clock.
            isolated: the instance cut off from the rest of the fleet.
            start: virtual time the partition opens (inclusive).
            end: virtual time it heals (exclusive).
            inner: view to filter.
        """
        self.viewer = viewer
        self.isolated_token = _sanitize_for_filename(isolated)
        self.viewer_isolated = _sanitize_for_filename(viewer) == self.isolated_token
        self.clock = clock
        self.start = float(start)
        self.end = float(end)
        self.inner = inner or ShareView()

    def partitioned_now(self) -> bool:
        """True while the split is open."""
        return self.start <= float(self.clock()) < self.end

    def listdir(self, path) -> list:
        """Entries on this side of the split (everything, once healed)."""
        if not self.partitioned_now():
            return self.inner.listdir(path)
        visible = []
        for name in self.inner.listdir(path):
            info = parse_record_name(name)
            if info is None:
                visible.append(name)
                continue
            if (info.owner_token == self.isolated_token) == self.viewer_isolated:
                visible.append(name)
        return visible


class TornWriteShareView(ShareView):
    """Publish a truncated record part-way through the run.

    A half-flushed `.json` is the one filesystem accident `FsClaimLog` cannot
    reason its way out of: the bytes it needs are simply gone, so it folds the file
    into a live claim by an unknown holder covering every path (``FS_UNKNOWN``) and
    grants nothing. This view injects exactly that, at a chosen virtual instant, so
    the fail-closed branch is exercised by a real file rather than a hand-built
    record dict.

    The injected file is named with the ordinary scheme, so it is subject to the
    same visibility lag as any other record when this view wraps a delayed one.
    """

    def __init__(self, claims_dir, clock: Callable[[], float], inject_at: float = 0.0,
                 lamport: int = 1, epoch_ms: Optional[int] = None,
                 owner: str = "torn-writer", inner: Optional[ShareView] = None):
        """Create a torn-write view.

        Args:
            claims_dir: directory the truncated record is injected into.
            clock: the viewer's clock.
            inject_at: virtual time at which the truncated record appears.
            lamport: lamport field of the injected name.
            epoch_ms: ``epoch_ms`` field of the injected name; defaults to
                ``inject_at``.
            owner: instance token baked into the injected name.
            inner: view to filter.
        """
        self.claims_dir = Path(claims_dir)
        self.clock = clock
        self.inject_at = float(inject_at)
        self.lamport = int(lamport)
        self.epoch_ms = int(inject_at * 1000) if epoch_ms is None else int(epoch_ms)
        self.owner = owner
        self.inner = inner or ShareView()
        self.injected_name = "%012d-%d-%s-%s.json" % (
            self.lamport, self.epoch_ms, _sanitize_for_filename(owner),
            "00000000-0000-4000-8000-000000000000",
        )

    def inject(self) -> str:
        """Write the truncated record (idempotent). Returns its filename."""
        self.claims_dir.mkdir(parents=True, exist_ok=True)
        target = self.claims_dir / self.injected_name
        if not target.exists():
            # A real torn write is a prefix of the intended bytes, not garbage.
            whole = json.dumps({
                "v": 1, "kind": "claim_requested", "paths": ["src/torn.py"],
                "instance_id": self.owner, "epoch": 1, "lamport": self.lamport,
                "epoch_ms": self.epoch_ms, "ttl": 300.0,
                "uuid": "torn", "lease_id": "torn",
            }, separators=(",", ":"), sort_keys=True)
            target.write_text(whole[: max(1, len(whole) // 2)], encoding="utf-8")
        return self.injected_name

    def listdir(self, path) -> list:
        """Entries, with the truncated record present once ``inject_at`` has passed."""
        if float(self.clock()) >= self.inject_at:
            self.inject()
        return self.inner.listdir(path)


# ---------------------------------------------------------------------------
# Installing a view under the product's listing call
# ---------------------------------------------------------------------------

class _OsShim:
    """``os`` with one method replaced, everything else delegated verbatim."""

    def __init__(self, fabric: "ShareFabric"):
        self._fabric = fabric

    def listdir(self, path):
        """Route the claim log's only directory listing through the fabric."""
        return self._fabric.listdir(path)

    def __getattr__(self, name):
        return getattr(os, name)


class ShareFabric:
    """Process-wide patch of the claim log's listing boundary, per-thread routed.

    ``state_store.fs_claim_log`` holds a single module-level ``os``, but a
    simulated fleet runs several instances in one process, each needing its own
    view. The fabric is installed once and dispatches to whichever view the
    *calling thread* bound, falling back to the real filesystem for unbound
    threads -- so an in-process fleet and a lone subprocess driver use identical
    machinery.
    """

    def __init__(self):
        """Create an uninstalled fabric."""
        self._local = threading.local()
        self._saved = None
        self._depth = 0

    def bind(self, view: Optional[ShareView]) -> None:
        """Attach ``view`` to the current thread."""
        self._local.view = view

    def unbind(self) -> None:
        """Detach this thread's view; it reverts to the real filesystem."""
        self._local.view = None

    def listdir(self, path):
        """Dispatch to the calling thread's view."""
        view = getattr(self._local, "view", None)
        if view is None:
            return os.listdir(path)
        return view.listdir(path)

    def __enter__(self) -> "ShareFabric":
        """Install the shim over ``fs_claim_log.os`` (re-entrant)."""
        if self._depth == 0:
            self._saved = _fs_claim_log.os
            _fs_claim_log.os = _OsShim(self)
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        """Restore the real ``os``. Always runs, even on an assertion failure."""
        self._depth -= 1
        if self._depth == 0:
            _fs_claim_log.os = self._saved
            self._saved = None
        return False


# ---------------------------------------------------------------------------
# One round of concurrent claims
# ---------------------------------------------------------------------------

@dataclass
class InstanceSpec:
    """What one simulated instance does in a round.

    Attributes:
        instance_id: its identity, as recorded in every record it writes.
        paths: the paths it tries to claim, all-or-nothing.
        start: virtual time at which it issues the request.
        delay: visibility lag it experiences reading peers.
        jitter: extra per-record lag, in ``[0, jitter)``.
        ttl: lease lifetime it asks for.
    """

    instance_id: str
    paths: list
    start: float = 0.0
    delay: float = 0.0
    jitter: float = 0.0
    ttl: float = 300.0


@dataclass
class ClaimOutcome:
    """What actually happened to one instance's claim.

    Attributes:
        instance_id: the claimant.
        granted: True iff ``claim()`` returned a lease.
        lease_id: the granted lease, else None.
        paths: canonical form of the paths it asked for.
        conflict_with: the instance (or ``<unknown>``) that beat it, else None.
        error: repr of any non-conflict failure.
    """

    instance_id: str
    granted: bool = False
    lease_id: Optional[str] = None
    paths: list = field(default_factory=list)
    conflict_with: Optional[str] = None
    error: Optional[str] = None


class _RoundGate:
    """Serialize writes in virtual-time order, then release all folds together.

    Two barriers in one object because they are two halves of the same guarantee:
    a round is deterministic only if (a) records appear on disk in the order their
    virtual timestamps claim, and (b) nobody folds until every request is on disk.

    Every wait is bounded. A simulated round contains no real work, so a wait that
    reaches the timeout is a deadlock and must surface as a failure, never a hang.
    """

    def __init__(self, parties: int):
        """Create a gate for ``parties`` instances."""
        self._cond = threading.Condition()
        self._turn = 0
        self._parties = parties
        self._arrived = 0

    def wait_turn(self, position: int) -> None:
        """Block until every earlier instance has published its request."""
        with self._cond:
            while self._turn < position:
                if not self._cond.wait(timeout=GATE_TIMEOUT_SECONDS):
                    raise TimeoutError("write gate stalled at position %d" % position)

    def finish_write(self, position: int) -> None:
        """Hand the write turn to the next instance."""
        with self._cond:
            if self._turn <= position:
                self._turn = position + 1
            self._cond.notify_all()

    def arrive(self) -> None:
        """Rendezvous: return only once every live instance has published."""
        with self._cond:
            self._arrived += 1
            self._cond.notify_all()
            while self._arrived < self._parties:
                if not self._cond.wait(timeout=GATE_TIMEOUT_SECONDS):
                    raise TimeoutError("settle rendezvous stalled")

    def abandon(self, position: int) -> None:
        """Drop out before publishing: pass the turn on and shrink the rendezvous."""
        with self._cond:
            if self._turn <= position:
                self._turn = position + 1
            self._parties -= 1
            self._cond.notify_all()


def default_view_factory(spec: InstanceSpec, clock: SimClock, seed: int) -> ShareView:
    """The plain delayed view, which is what a healthy share looks like."""
    return DelayedShareView(
        spec.instance_id, clock, delay=spec.delay, jitter=spec.jitter, seed=seed,
    )


def run_claim_round(
    claims_dir,
    specs: list,
    settle: float,
    max_skew: float = 0.0,
    seed: int = 0,
    view_factory: Optional[Callable] = None,
    case_policy: str = "insensitive",
    fabric: Optional[ShareFabric] = None,
) -> list:
    """Run one concurrent claim round and report what each instance got.

    Instances run in real threads (so the product's real locking, fsyncing and
    renaming all execute) but their *decisions* are governed entirely by virtual
    time, so the result is a pure function of ``specs``, ``settle`` and ``seed``.

    Args:
        claims_dir: shared claim-log directory.
        specs: one :class:`InstanceSpec` per instance.
        settle: settle window every instance uses. Must be > 0: it is the only
            point inside ``claim()`` where the rendezvous can be hung.
        max_skew: clock-skew bound handed to every backend.
        seed: seeds the per-record visibility jitter.
        view_factory: ``(spec, clock, seed) -> ShareView``; defaults to
            :func:`default_view_factory`.
        case_policy: canonicalization policy; multibox forces "insensitive".
        fabric: an already-installed :class:`ShareFabric`, when the caller wants
            one fabric across several rounds.

    Returns:
        list of :class:`ClaimOutcome`, ordered as ``specs`` was.

    Raises:
        ValueError: ``settle`` is not positive.
    """
    def action(backend, spec, outcome):
        # Deduplicated exactly as FsClaimLog._canon_all does: a single instance
        # asking for "src/b.py" and "src\\b.py" is asking once.
        canonical: list = []
        for path in spec.paths:
            key = canonical_claim_path(path, case_policy=case_policy)
            if key not in canonical:
                canonical.append(key)
        outcome.paths = canonical
        outcome.lease_id = backend.claim(spec.paths, spec.instance_id, spec.ttl)
        outcome.granted = True

    return _run_round(
        claims_dir, specs, settle, action,
        max_skew=max_skew, seed=seed, view_factory=view_factory,
        case_policy=case_policy, fabric=fabric,
    )


def run_election_round(
    claims_dir,
    specs: list,
    settle: float,
    max_skew: float = 0.0,
    seed: int = 0,
    view_factory: Optional[Callable] = None,
    fabric: Optional[ShareFabric] = None,
    ttl_seconds: float = 60.0,
) -> list:
    """Race several instances for the primary lock through the same machinery.

    ``elect_primary_state`` takes the primary lock through the ORDINARY claim
    protocol, so an election is a claim round with a reserved path -- which is the
    whole design claim of Inc 5 and is why this shares :func:`_run_round` rather
    than reimplementing the concurrency.

    Args:
        claims_dir: shared claim-log directory.
        specs: one :class:`InstanceSpec` per challenger (``paths`` unused).
        settle: settle window.
        max_skew: clock-skew bound.
        seed: seeds visibility jitter.
        view_factory: ``(spec, clock, seed) -> ShareView``.
        fabric: an already-installed fabric.
        ttl_seconds: lifetime of the primary lock.

    Returns:
        list of :class:`ClaimOutcome`; ``lease_id`` carries the observed primary
        and ``paths`` carries ``["generation:<N>"]`` for the fold's fence.
    """
    from state_store import failover as failover_mod

    def action(backend, spec, outcome):
        state = failover_mod.elect_primary_state(
            backend, instance_id=spec.instance_id, ttl_seconds=ttl_seconds,
        )
        outcome.lease_id = state.instance_id
        outcome.paths = ["generation:%d" % state.generation]
        outcome.granted = state.instance_id == spec.instance_id

    return _run_round(
        claims_dir, specs, settle, action,
        max_skew=max_skew, seed=seed, view_factory=view_factory,
        case_policy="insensitive", fabric=fabric,
    )


def _run_round(
    claims_dir,
    specs: list,
    settle: float,
    action: Callable,
    max_skew: float = 0.0,
    seed: int = 0,
    view_factory: Optional[Callable] = None,
    case_policy: str = "insensitive",
    fabric: Optional[ShareFabric] = None,
) -> list:
    """Shared body of :func:`run_claim_round` and :func:`run_election_round`.

    Instances run in real threads -- so the product's real fsyncing, renaming and
    directory scanning all execute -- but their *decisions* are governed entirely
    by virtual time, making the result a pure function of the inputs.

    Args:
        claims_dir: shared claim-log directory.
        specs: one :class:`InstanceSpec` per instance.
        settle: settle window; must be > 0, since it is the only point inside
            ``claim()`` where the rendezvous can be hung.
        action: ``(backend, spec, outcome) -> None``, run under the gate.
        max_skew: clock-skew bound handed to every backend.
        seed: seeds the per-record visibility jitter.
        view_factory: ``(spec, clock, seed) -> ShareView``.
        case_policy: canonicalization policy.
        fabric: an already-installed :class:`ShareFabric`.

    Returns:
        list of :class:`ClaimOutcome`, ordered as ``specs`` was.

    Raises:
        ValueError: ``settle`` is not positive.
        TimeoutError: an instance failed to finish inside the gate budget.
    """
    if settle <= 0:
        raise ValueError("a simulated round needs settle > 0 to hang the rendezvous")

    view_factory = view_factory or default_view_factory
    order = sorted(range(len(specs)), key=lambda i: (specs[i].start, specs[i].instance_id))
    position_of = {index: position for position, index in enumerate(order)}
    gate = _RoundGate(len(specs))
    outcomes = [ClaimOutcome(spec.instance_id) for spec in specs]
    owned_fabric = fabric or ShareFabric()

    def body(index: int) -> None:
        spec = specs[index]
        position = position_of[index]
        outcome = outcomes[index]
        published = False
        clock = SimClock(spec.start)

        def before_settle(_duration: float) -> None:
            nonlocal published
            published = True
            gate.finish_write(position)

        def after_settle(_duration: float) -> None:
            gate.arrive()

        backend = FsClaimLog(
            str(claims_dir),
            clock=clock,
            sleep=clock.sleeper(before=before_settle, after=after_settle),
            settle_seconds=settle,
            max_skew_seconds=max_skew,
            case_policy=case_policy,
        )
        try:
            gate.wait_turn(position)
            owned_fabric.bind(view_factory(spec, clock, seed))
            action(backend, spec, outcome)
        except ClaimConflict as exc:
            outcome.conflict_with = exc.conflicting_instance
        except Exception as exc:  # fail-closed: any error is "no grant"
            outcome.error = repr(exc)
        finally:
            if not published:
                gate.abandon(position)
            owned_fabric.unbind()

    with owned_fabric:
        threads = [
            threading.Thread(target=body, args=(index,), name="sim-%d" % index)
            for index in range(len(specs))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=GATE_TIMEOUT_SECONDS * 2)
        stuck = [t.name for t in threads if t.is_alive()]
    if stuck:
        raise TimeoutError("simulated instances did not finish: %s" % stuck)
    return outcomes


def observe_fold(claims_dir, view: Optional[ShareView], now: float,
                 max_skew: float = 0.0, fabric: Optional[ShareFabric] = None) -> dict:
    """Fold the shared log exactly as ``view``'s owner would see it at ``now``.

    The read-side counterpart of a round: convergence means every instance's
    ``observe_fold`` agrees once the share has settled, and disagrees while it has
    not. Read-only -- writes nothing, so it can be called between rounds freely.

    Args:
        claims_dir: shared claim-log directory.
        view: the instance's view, or None for the undecorated ground truth.
        now: virtual time to fold at.
        max_skew: clock-skew bound.
        fabric: an already-installed fabric; one is created if omitted.

    Returns:
        ``{canonical_path: instance_id}`` as that instance would compute it.
    """
    owned_fabric = fabric or ShareFabric()
    with owned_fabric:
        owned_fabric.bind(view)
        try:
            backend = FsClaimLog(str(claims_dir), clock=lambda: now,
                                 settle_seconds=0.0, max_skew_seconds=max_skew)
            return _fs_claim_log.fold_fs_claims(
                backend._read_records(), now=now, max_skew=max_skew,
            )
        finally:
            owned_fabric.unbind()


def find_double_grants(outcomes: list) -> list:
    """Paths granted to more than one instance in the same round.

    THE property. An empty list is the whole point of the settle window; a
    non-empty one is a split-brain that would let two boxes edit one file.

    Returns:
        list of ``(path, [instance_id, ...])``, sorted, one entry per violated path.
    """
    by_path: dict = {}
    for outcome in outcomes:
        if not outcome.granted:
            continue
        for path in outcome.paths:
            by_path.setdefault(path, []).append(outcome.instance_id)
    return sorted(
        (path, sorted(holders))
        for path, holders in by_path.items()
        if len(holders) > 1
    )


# ---------------------------------------------------------------------------
# Seeded randomized rounds
# ---------------------------------------------------------------------------

#: Path pool with deliberate case variation: multibox canonicalizes
#: case-insensitively, so "SRC/A.PY" and "src/a.py" MUST collide.
PATH_POOL = [
    "src/a.py", "src/b.py", "src/c.py", "docs/README.md",
    "SRC/A.PY", "src\\b.py", "./src/c.py",
]


def random_round(rng: random.Random, settle: float = 1.0) -> list:
    """Generate one seeded round inside the deterministic parameter box.

    The box (see module docstring) is ``max start spread < min delay <= settle``.
    Its lower edge matters as much as its upper: with ``delay <= settle - spread``
    every instance sees every peer and the round is trivially safe, so the
    generator samples ``delay`` in the upper half, where at least one instance is
    genuinely blind to a peer and the deterministic sort key is the ONLY thing
    preventing a double grant.

    Args:
        rng: seeded ``random.Random``.
        settle: settle window the round will use.

    Returns:
        list of :class:`InstanceSpec`, 2-5 instances with overlapping path sets.
    """
    count = rng.randint(2, 5)
    delay = quantize_ms(rng.uniform(0.55 * settle, 0.95 * settle))
    jitter = quantize_ms(rng.uniform(0.0, min(delay, settle - delay) * 0.5))
    # Spread must exceed settle-delay (so somebody is blind) and stay under the
    # smallest delay in play (so no post-fold tombstone can enter any view).
    low = settle - delay
    high = delay - jitter
    spread = quantize_ms(rng.uniform(low, high)) if high > low else quantize_ms(low)

    specs = []
    for index in range(count):
        offset = quantize_ms(spread * index / max(1, count - 1))
        size = rng.randint(1, 2)
        paths = rng.sample(PATH_POOL, size)
        specs.append(InstanceSpec(
            instance_id="box%d:%d:sim" % (index, 1000 + index),
            paths=paths,
            start=offset,
            delay=delay,
            jitter=jitter,
            ttl=float(rng.choice([60.0, 300.0])),
        ))
    return specs


def sweep_no_double_grant(tmp_factory: Callable[[int], str], runs: int = 200,
                          settle: float = 1.0, base_seed: int = 0) -> dict:
    """Run ``runs`` seeded rounds with ``delay <= settle`` and count violations.

    Args:
        tmp_factory: ``(run_index) -> claims_dir`` for a fresh directory per run.
        runs: number of seeded rounds.
        settle: settle window (virtual seconds; costs no wall time).
        base_seed: first seed; run ``i`` uses ``base_seed + i``.

    Returns:
        ``{runs, grants, contended_runs, violations}`` where ``violations`` lists
        ``(seed, [(path, holders), ...])`` for every round that double-granted.
    """
    violations = []
    grants = 0
    contended = 0
    for index in range(runs):
        seed = base_seed + index
        rng = random.Random(seed)
        specs = random_round(rng, settle=settle)
        outcomes = run_claim_round(
            tmp_factory(index), specs, settle=settle, seed=seed,
        )
        grants += sum(1 for o in outcomes if o.granted)
        if any(o.conflict_with for o in outcomes):
            contended += 1
        bad = find_double_grants(outcomes)
        if bad:
            violations.append((seed, bad))
    return {
        "runs": runs, "grants": grants,
        "contended_runs": contended, "violations": violations,
    }


def blind_pair(settle: float, delay: float) -> list:
    """Two instances 1ms apart, each reading the share ``delay`` seconds behind.

    The minimal shape of the hazard, used for both halves of the falsifiability
    pair: with ``delay <= settle`` the later instance still sees the earlier one
    and yields; with ``delay > settle`` neither sees the other and both grant.
    """
    return [
        InstanceSpec("boxA:1:sim", ["src/shared.py"], start=0.0, delay=delay),
        InstanceSpec("boxB:2:sim", ["src/shared.py"], start=0.001, delay=delay),
    ]


def round_is_deterministic(claims_dir_a, claims_dir_b, specs: list, settle: float,
                           seed: int) -> bool:
    """Re-run a round on a fresh directory and check the outcome is identical.

    Proves the parameter box empirically. Threads make the round physically
    non-deterministic; if the box holds, the *decisions* are still a pure function
    of the seed.
    """
    def shape(outcomes):
        return [(o.instance_id, o.granted, tuple(o.paths), o.conflict_with is not None)
                for o in outcomes]

    first = run_claim_round(claims_dir_a, specs, settle=settle, seed=seed)
    second = run_claim_round(claims_dir_b, specs, settle=settle, seed=seed)
    return shape(first) == shape(second)


# ---------------------------------------------------------------------------
# Real separate processes
# ---------------------------------------------------------------------------

def run_driver(spec: dict, timeout: float = DRIVER_TIMEOUT_SECONDS):
    """Run one instance as a separate OS process.

    In-process threads prove the protocol; a subprocess proves there is no hidden
    shared state making it work -- no shared lamport counter, no shared clock, no
    shared Python object graph. The two together are the argument.

    Follows tools/subprocess_guard.py G6: list argv, explicit ``cwd``, never
    ``shell=True``, always a timeout, always an explicit encoding (G10).

    Args:
        spec: driver spec dict (see :func:`_driver_main`). Written to a temp file
            beside the claims dir and passed by path.
        timeout: wall-clock ceiling.

    Returns:
        ``(returncode, result_dict_or_None)``.
    """
    spec_path = Path(spec["claims_dir"]).parent / ("spec-%s.json" % uuid.uuid4().hex[:8])
    out_path = spec_path.with_name(spec_path.stem + "-out.json")
    spec = dict(spec, out=str(out_path))
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--spec", str(spec_path)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )
    result = None
    if out_path.exists():
        try:
            result = json.loads(out_path.read_text(encoding="utf-8"))
        except ValueError:
            result = None
    if result is None:
        result = {"stdout": completed.stdout, "stderr": completed.stderr}
    return completed.returncode, result


def _driver_main(spec: dict) -> int:
    """Body of one driver process: claim (or elect), record, optionally die.

    ``kill: true`` exits via ``os._exit(137)`` -- no atexit hooks, no ``release()``,
    no tombstone, no final heartbeat. That is the only faithful way to leave the
    exact wreckage a ``SIGKILL``ed box leaves behind, and it is what assertions 3
    and 4 then have to recover from.
    """
    from state_store import failover as failover_mod

    clock = SimClock(float(spec.get("start", 0.0)))
    settle = float(spec.get("settle", 0.0))
    backend = FsClaimLog(
        spec["claims_dir"],
        clock=clock,
        sleep=clock.sleeper(),
        settle_seconds=settle,
        max_skew_seconds=float(spec.get("max_skew", 0.0)),
        case_policy=spec.get("case_policy", "insensitive"),
        epoch=int(spec.get("epoch", 1)),
    )
    result = {"instance_id": spec["instance_id"], "mode": spec.get("mode", "claim"),
              "granted": False, "lease_id": None, "error": None,
              "primary": None, "generation": 0, "pid": os.getpid()}
    try:
        if spec.get("mode") == "elect":
            state = failover_mod.elect_primary_state(
                backend, instance_id=spec["instance_id"],
                epoch=int(spec.get("epoch", 1)),
                ttl_seconds=float(spec.get("ttl", 60.0)),
            )
            result["primary"] = state.instance_id
            result["generation"] = state.generation
            result["granted"] = state.instance_id == spec["instance_id"]
        else:
            result["lease_id"] = backend.claim(
                spec["paths"], spec["instance_id"], float(spec.get("ttl", 300.0)),
            )
            result["granted"] = True
    except ClaimConflict as exc:
        result["error"] = "ClaimConflict:%s" % exc.conflicting_instance
    except Exception as exc:
        result["error"] = repr(exc)

    out = Path(spec["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(result, handle)
        handle.flush()
        os.fsync(handle.fileno())

    if spec.get("kill"):
        os._exit(KILLED_EXIT_CODE)
    # A refused claim is a legitimate outcome, not a driver failure: the parent
    # reads the verdict from the result file, so the exit code only reports
    # whether the driver itself ran.
    return 0


def main(argv=None) -> int:
    """CLI entry point for the driver subprocess.

    Returns:
        Process exit code (0 normally; ``_driver_main`` may exit 137 for a kill).
    """
    parser = argparse.ArgumentParser(
        description="Simulated-multibox instance driver (multibox Inc 6).",
    )
    parser.add_argument("--spec", required=True,
                        help="path to a JSON driver spec written by run_driver()")
    args = parser.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    return _driver_main(spec)


if __name__ == "__main__":
    sys.exit(main())
