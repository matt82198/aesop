"""state_store.fs_claim_log — shared-filesystem lease-by-append claim backend.

Multibox Increment 4a. Carries the coordination claim log — and ONLY the claim log —
onto a shared filesystem (NFS/SMB). The event store stays on each instance's local
disk: SQLite WAL requires a shared-memory index that is only coherent between
processes on the same host, so `state.db` must never live on the share.

Design (the whole point of this module)
---------------------------------------
Every record is written to a filename that is unique **by construction**:

    <lamport>-<epoch_ms>-<instance_id>-<uuid4>.json

No two writers ever contend for the same name, so this backend needs **no
filesystem atomicity or mutual-exclusion primitive at all** — no advisory file
locks, no exclusive-create, no hardlink tricks. Mutual exclusion is decided by a
deterministic fold over the directory listing, exactly as
``coordination.fold_claims`` decides it over the event stream.

The only property the shared filesystem must supply is that a written+fsynced file
becomes visible in a peer's directory listing within a bounded time D. Because
D > 0, a naive "write, list, I'm lowest, go" double-grants under concurrent
claims, so ``claim()`` runs the settle-window protocol:

    1. write the ``claim_requested`` record and fsync it
    2. wait ``settle_seconds`` (must exceed the measured p99 visibility delay)
    3. re-list the directory and fold
    4. grant iff our own record is the winner for EVERY requested path;
       otherwise write our own tombstone and fail closed

Records
-------
``{v, kind, paths[], instance_id, epoch, lamport, epoch_ms, ttl, uuid, lease_id}``
with ``kind`` one of ``claim_requested`` | ``claim_released`` | ``heartbeat``.
Records are immutable: ``renew()`` APPENDS a heartbeat, it never rewrites a file.

Invariants inherited from ``coordination.fold_claims``
-----------------------------------------------------
* lowest sort key ``(lamport, epoch_ms, instance_id, uuid)`` wins — a deterministic
  total order that needs no clock synchronisation for *ordering*
* TTL is enforced AT FOLD TIME, so a crashed holder's claim becomes reclaimable
* a tombstone releases the lease it names
* legacy records carrying no ``ttl`` never expire (backward compatible)
* any read/append failure means no grant (fail-closed)

Clock skew only ever LENGTHENS a lease: the fold treats a lease as live until
``epoch_ms/1000 + ttl + max_skew``. A skewed peer clock can stall throughput; it
can never produce a double grant.

Corrupt records fail CLOSED
---------------------------
``store.py`` rightly skips a corrupt event payload on read. A claim log cannot
afford that: a truncated record might be somebody's live claim on the very path we
are about to take. An unparseable record is therefore folded into a live claim by
an UNKNOWN holder, covering every path, until ``mtime + default_ttl + max_skew``.

Reachable only when ``multibox.transport == "shared-fs"`` (wired in Inc 7).

Stdlib only: json, os, time, uuid, pathlib.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from state_store.claim_backend import ClaimBackend, ClaimConflict
from state_store.paths import canonical_claim_path

# Record schema version.
RECORD_VERSION = 1

KIND_CLAIM_REQUESTED = "claim_requested"
KIND_CLAIM_RELEASED = "claim_released"
KIND_HEARTBEAT = "heartbeat"

#: Reserved fold key: an unparseable record covers paths we cannot know, so it is
#: folded onto this wildcard key and blocks every grant while it is live.
FS_UNKNOWN_PATH = "*"
#: Reserved holder id for the same case.
FS_UNKNOWN_HOLDER = "<unknown>"

#: TTL assumed for a corrupt record, whose real ttl is unreadable.
DEFAULT_TTL_SECONDS = 300.0
#: Default settle window; must exceed the measured p99 directory-visibility delay.
DEFAULT_SETTLE_SECONDS = 5.0


# ---------------------------------------------------------------------------
# The pure fold — no filesystem, no clock, no sleeps
# ---------------------------------------------------------------------------

def _as_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sort_key(rec: dict):
    """Deterministic total order: (lamport, epoch_ms, instance_id, uuid)."""
    return (
        _as_int(rec.get("lamport")),
        _as_int(rec.get("epoch_ms")),
        str(rec.get("instance_id") or ""),
        str(rec.get("uuid") or ""),
    )


def _deadline(rec: dict, max_skew: float):
    """Absolute expiry time of a record, or None when it never expires.

    ``max_skew`` is added, never subtracted: a bounded clock disagreement between
    boxes may only make a lease look LONGER than its writer intended.
    """
    ttl = _as_float(rec.get("ttl"))
    if ttl is None:
        return None  # legacy record: never expires
    epoch_ms = _as_float(rec.get("epoch_ms"))
    if epoch_ms is None:
        return None
    return (epoch_ms / 1000.0) + ttl + float(max_skew)


def fold_fs_claims(
    records: list,
    now: Optional[float] = None,
    max_skew: float = 0.0,
    detail: bool = False,
) -> dict:
    """Fold claim-log records into the set of currently held paths.

    PURE function over a list of dicts: it performs no I/O, reads no clock and
    never sleeps. This is the entire correctness surface of the shared-filesystem
    backend, which is why it is separated from ``FsClaimLog``.

    Args:
        records: record dicts, in any order. A record that failed to parse is
            represented as ``{"__corrupt__": True, "epoch_ms": <mtime_ms>, "ttl": t}``.
        now: reference time (epoch seconds) for TTL expiry. Defaults to time.time().
        max_skew: bound on cross-box clock disagreement, in seconds. Only ever
            lengthens a lease.
        detail: when True, map each path to ``(instance_id, lease_id)`` instead of
            just ``instance_id``.

    Returns:
        ``{canonical_path: instance_id}``, or ``{canonical_path: (instance_id,
        lease_id)}`` when ``detail`` is set. The reserved key ``FS_UNKNOWN_PATH``
        appears iff a live corrupt record is blocking every path (fail-closed).
    """
    if now is None:
        now = time.time()
    now = float(now)

    requests: list[dict] = []
    tombstoned: set = set()
    heartbeats: dict = {}
    unknown_blocker = False

    for rec in records:
        if not isinstance(rec, dict):
            continue

        if rec.get("__corrupt__"):
            # Unparseable: assume it is somebody's live claim on an unknown path.
            deadline = _deadline(
                {"ttl": rec.get("ttl", DEFAULT_TTL_SECONDS),
                 "epoch_ms": rec.get("epoch_ms")},
                max_skew,
            )
            if deadline is None or now <= deadline:
                unknown_blocker = True
            continue

        kind = rec.get("kind")
        lease_id = rec.get("lease_id")

        if kind == KIND_CLAIM_REQUESTED:
            paths = rec.get("paths")
            instance_id = rec.get("instance_id")
            if not isinstance(paths, list) or not paths or not instance_id:
                continue
            requests.append(rec)
        elif kind == KIND_CLAIM_RELEASED:
            if lease_id is not None:
                tombstoned.add(lease_id)
        elif kind == KIND_HEARTBEAT:
            if lease_id is None:
                continue
            deadline = _deadline(rec, max_skew)
            if deadline is None:
                heartbeats[lease_id] = None
            elif lease_id not in heartbeats:
                heartbeats[lease_id] = deadline
            elif heartbeats[lease_id] is not None:
                heartbeats[lease_id] = max(heartbeats[lease_id], deadline)

    # Live requests: not tombstoned, not past their (heartbeat-extended) deadline.
    live: list[dict] = []
    for rec in requests:
        lease_id = rec.get("lease_id")
        if lease_id in tombstoned:
            continue
        deadline = _deadline(rec, max_skew)
        if lease_id in heartbeats:
            hb_deadline = heartbeats[lease_id]
            if hb_deadline is None or deadline is None:
                deadline = None
            else:
                deadline = max(deadline, hb_deadline)
        if deadline is not None and now > deadline:
            continue
        live.append(rec)

    # Lowest sort key wins each path.
    winners: dict = {}
    for rec in sorted(live, key=_sort_key):
        key = _sort_key(rec)
        for path in rec.get("paths", []):
            if not isinstance(path, str):
                continue
            existing = winners.get(path)
            if existing is None or key < existing[0]:
                winners[path] = (key, rec.get("instance_id"), rec.get("lease_id"))

    if detail:
        result = {p: (v[1], v[2]) for p, v in winners.items()}
        if unknown_blocker:
            result[FS_UNKNOWN_PATH] = (FS_UNKNOWN_HOLDER, FS_UNKNOWN_HOLDER)
        return result

    result = {p: v[1] for p, v in winners.items()}
    if unknown_blocker:
        result[FS_UNKNOWN_PATH] = FS_UNKNOWN_HOLDER
    return result


# ---------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------

def _sanitize_for_filename(value: str) -> str:
    """Reduce an instance_id to filename-safe characters.

    ``hostname:pid:nonce`` contains ':', which is illegal in a Windows filename.
    The sanitized form appears ONLY in the filename; the fold always reads the
    true instance_id out of the record body.
    """
    return "".join(ch if ch.isalnum() else "_" for ch in str(value)) or "anon"


class FsClaimLog(ClaimBackend):
    """ClaimBackend over an append-only directory of immutable JSON records.

    Implements the Inc 2 ``ClaimBackend`` protocol, so the Inc 2 contract suite
    (``tests/test_claim_backend.py``) runs against it unmodified.
    """

    def __init__(
        self,
        claims_dir: str,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        settle_seconds: float = DEFAULT_SETTLE_SECONDS,
        max_skew_seconds: float = 0.0,
        case_policy: str = "insensitive",
        repo_root: Optional[str] = None,
        epoch: int = 1,
        default_ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ):
        """Create a claim log rooted at ``claims_dir``.

        Args:
            claims_dir: directory on the shared filesystem holding the records.
                Created lazily on first append; never required to pre-exist.
            clock: callable returning epoch seconds (default ``time.time``).
                Injectable so tests need no sleeps.
            sleep: callable consuming a duration in seconds (default ``time.sleep``).
                Injectable so the settle window is observable in tests.
            settle_seconds: visibility settle window; must exceed the measured p99
                cross-box directory-visibility delay (Inc 0 measures it, Inc 7 gates on it).
            max_skew_seconds: bound on cross-box clock disagreement. Only lengthens leases.
            case_policy: passed to ``canonical_claim_path``. Multibox forces
                "insensitive": over-colliding costs throughput, under-colliding
                costs correctness.
            repo_root: when given, claim keys become repo-relative.
            epoch: this instance's fencing epoch (Inc 3 identity); recorded for Inc 5.
            default_ttl_seconds: TTL assumed for a corrupt record whose real ttl
                cannot be read.
        """
        self.claims_dir = str(claims_dir)
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self.settle_seconds = float(settle_seconds)
        self.max_skew_seconds = float(max_skew_seconds)
        self.case_policy = case_policy
        self.repo_root = repo_root
        self.epoch = int(epoch)
        self.default_ttl_seconds = float(default_ttl_seconds)
        self._lamport = 0

    # -- internals ---------------------------------------------------------

    def _canon(self, path: str) -> str:
        return canonical_claim_path(
            path, repo_root=self.repo_root, case_policy=self.case_policy
        )

    def _canon_all(self, paths) -> list:
        """Canonicalize preserving order, dropping duplicates."""
        out: list = []
        for p in paths:
            c = self._canon(p)
            if c not in out:
                out.append(c)
        return out

    def _read_records(self) -> list:
        """Re-list the directory and parse every record.

        The listing is re-read on every call by design: on a network share the
        caller needs the freshest view the mount will give it (the required mount
        options are documented by the Inc 0 preflight). A record that will not
        parse is turned into the corrupt sentinel rather than skipped.
        """
        directory = Path(self.claims_dir)
        if not directory.is_dir():
            return []

        records: list = []
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            # Fail-closed: an unreadable directory must not read as "nothing held".
            return [{"__corrupt__": True,
                     "epoch_ms": self._clock() * 1000.0,
                     "ttl": self.default_ttl_seconds}]

        for name in names:
            if not name.endswith(".json"):
                continue
            fpath = directory / name
            try:
                with open(fpath, encoding="utf-8") as handle:
                    rec = json.load(handle)
                if not isinstance(rec, dict):
                    raise ValueError("record is not an object")
                records.append(rec)
            except Exception:
                try:
                    mtime = os.path.getmtime(fpath)
                except OSError:
                    mtime = self._clock()
                records.append({
                    "__corrupt__": True,
                    "epoch_ms": mtime * 1000.0,
                    "ttl": self.default_ttl_seconds,
                    "file": name,
                })
        return records

    def _next_lamport(self, records: Optional[list] = None) -> int:
        if records is None:
            records = self._read_records()
        highest = self._lamport
        for rec in records:
            highest = max(highest, _as_int(rec.get("lamport")))
        return highest + 1

    def _append(self, kind: str, paths: list, instance_id: str,
                ttl_seconds: float, lease_id: str) -> dict:
        """Write one immutable record under a name unique by construction."""
        directory = Path(self.claims_dir)
        directory.mkdir(parents=True, exist_ok=True)

        lamport = self._next_lamport()
        epoch_ms = int(self._clock() * 1000)
        record_uuid = str(uuid.uuid4())
        record = {
            "v": RECORD_VERSION,
            "kind": kind,
            "paths": list(paths),
            "instance_id": instance_id,
            "epoch": self.epoch,
            "lamport": lamport,
            "epoch_ms": epoch_ms,
            "ttl": float(ttl_seconds),
            "uuid": record_uuid,
            "lease_id": lease_id,
        }
        name = (
            f"{lamport:012d}-{epoch_ms}-"
            f"{_sanitize_for_filename(instance_id)}-{record_uuid}.json"
        )
        # No exclusive-create and no advisory locking: the name cannot collide.
        with open(directory / name, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        self._lamport = lamport
        return record

    def _find_request(self, records: list, lease_id: str) -> Optional[dict]:
        for rec in records:
            if (rec.get("kind") == KIND_CLAIM_REQUESTED
                    and rec.get("lease_id") == lease_id):
                return rec
        return None

    def _is_tombstoned(self, records: list, lease_id: str) -> bool:
        return any(
            rec.get("kind") == KIND_CLAIM_RELEASED and rec.get("lease_id") == lease_id
            for rec in records
        )

    # -- ClaimBackend protocol --------------------------------------------

    def claim(self, paths: list, instance_id: str, ttl_seconds: float) -> str:
        """Claim paths via the settle-window protocol; fail closed on any loss.

        Writes a ``claim_requested`` record, waits out the visibility settle
        window, re-lists and folds, and grants only if this exact record is the
        winner for every requested path. On loss it writes its OWN tombstone
        before raising, so a losing request never strands a phantom holder for a
        full TTL (the RS5 F2 retract invariant, carried over from
        ``coordination.try_claim``).

        Raises:
            ClaimConflict: some path is held by another instance, or a corrupt
                record makes holdership unknowable.
        """
        canonical = self._canon_all(paths)
        if not canonical:
            raise ValueError("claim() requires at least one path")

        lease_id = str(uuid.uuid4())
        self._append(KIND_CLAIM_REQUESTED, canonical, instance_id,
                     ttl_seconds, lease_id)

        # Step 2 of the protocol: let the share's directory view settle.
        if self.settle_seconds > 0:
            self._sleep(self.settle_seconds)

        # Step 3: force a fresh listing and fold it.
        folded = fold_fs_claims(
            self._read_records(),
            now=self._clock(),
            max_skew=self.max_skew_seconds,
            detail=True,
        )

        conflicting_instance = None
        conflicting_paths: list = []

        if FS_UNKNOWN_PATH in folded:
            # A corrupt record might be a live claim on any of these paths.
            conflicting_instance = FS_UNKNOWN_HOLDER
            conflicting_paths = list(canonical)
        else:
            for path in canonical:
                winner = folded.get(path)
                if winner is None or winner[1] != lease_id:
                    conflicting_paths.append(path)
                    if conflicting_instance is None:
                        conflicting_instance = (
                            winner[0] if winner else FS_UNKNOWN_HOLDER
                        )

        if conflicting_instance is not None:
            # Retract our own request, then fail closed regardless of the retract.
            try:
                self._append(KIND_CLAIM_RELEASED, [], instance_id,
                             ttl_seconds, lease_id)
            except Exception:
                pass
            raise ClaimConflict(conflicting_instance, conflicting_paths)

        return lease_id

    def renew(self, lease_id: str, instance_id: str, ttl_seconds: float) -> None:
        """Extend a live lease by APPENDING a heartbeat; never mutates a record.

        Raises:
            ValueError: lease unknown, held by another instance, already
                released, or already expired.
        """
        records = self._read_records()
        request = self._find_request(records, lease_id)
        if request is None:
            raise ValueError(f"Lease {lease_id} not found")

        holder = request.get("instance_id")
        if holder != instance_id:
            raise ValueError(
                f"Cannot renew lease held by {holder} from {instance_id}"
            )

        if self._is_tombstoned(records, lease_id):
            raise ValueError(f"Cannot renew released lease {lease_id}")

        live = fold_fs_claims(
            [r for r in records if not r.get("__corrupt__")],
            now=self._clock(),
            max_skew=self.max_skew_seconds,
            detail=True,
        )
        held = any(v[1] == lease_id for v in live.values())
        if not held:
            raise ValueError(f"Cannot renew expired lease {lease_id}")

        self._append(KIND_HEARTBEAT, [], instance_id, ttl_seconds, lease_id)

    def release(self, lease_id: str, instance_id: str) -> None:
        """Tombstone a lease, freeing its paths.

        Raises:
            ValueError: lease unknown, or held by another instance.
        """
        records = self._read_records()
        request = self._find_request(records, lease_id)
        if request is None:
            raise ValueError(f"Lease {lease_id} not found")

        holder = request.get("instance_id")
        if holder != instance_id:
            raise ValueError(
                f"Cannot release lease held by {holder} from {instance_id}"
            )

        self._append(KIND_CLAIM_RELEASED, [], instance_id,
                     _as_float(request.get("ttl")) or self.default_ttl_seconds,
                     lease_id)

    def holder(self, paths: list) -> Optional[str]:
        """Return the instance holding ALL given paths, else None.

        Fail-closed: while a corrupt record is live, holdership is unknowable and
        this returns ``FS_UNKNOWN_HOLDER`` rather than a reassuring None.
        """
        if not paths:
            return None

        canonical = self._canon_all(paths)
        folded = fold_fs_claims(
            self._read_records(),
            now=self._clock(),
            max_skew=self.max_skew_seconds,
        )
        if FS_UNKNOWN_PATH in folded:
            return FS_UNKNOWN_HOLDER

        holders = {folded.get(p) for p in canonical}
        if len(holders) == 1 and None not in holders:
            return holders.pop()
        return None

    def close(self) -> None:
        """No resources are held open; present for ClaimBackend parity."""
        return None
