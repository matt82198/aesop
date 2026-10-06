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

#: Suffix of the in-progress temp name (Inc 4b). Deliberately NOT ``.json``, so a
#: half-written record is invisible to ``_read_records`` and can never be folded.
TEMP_SUFFIX = ".json.tmp"


class ClockSkewError(RuntimeError):
    """A peer's clock is ahead of ours by more than ``max_skew_seconds``.

    Fail-closed, not a conflict: past the bound the TTL arithmetic no longer
    guarantees "skew only lengthens", so no grant may be issued at all. Inc 0
    measures the real skew and Inc 7 refuses to enable multibox past the bound;
    this is the same precondition re-checked at write time, live.
    """


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

def _fsync_dir(directory) -> bool:
    """Best-effort: make a freshly created directory ENTRY durable.

    On POSIX, fsyncing a file makes its BYTES durable but says nothing about the
    directory entry that names it -- after a crash the record can be durable and
    yet absent from a peer's listing, which for a claim log means a granted lease
    nobody can see. ``os.open(dir, O_RDONLY)`` + ``os.fsync(fd)`` closes that gap.

    Windows refuses to open a directory as a file, so this raises there and we
    fall back to :func:`_flush_dir_windows`. Every failure is swallowed: the
    record is already written and ``os.replace`` already published it atomically,
    so a refused directory sync degrades durability, never correctness.

    Returns:
        True iff a directory-level sync was actually performed.
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except (OSError, AttributeError, ValueError):
        return _flush_dir_windows(directory)
    try:
        os.fsync(fd)
        return True
    except (OSError, AttributeError):
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _flush_dir_windows(directory) -> bool:
    """Windows fallback: FlushFileBuffers on a directory handle, best-effort.

    ``os.replace`` is already atomic on Windows (MoveFileEx with
    REPLACE_EXISTING), so the entry never appears half-formed. This only pushes
    the metadata write out of the cache.

    Two Win32 details this depends on, both verified against a real directory by
    ``test_dir_sync_actually_succeeds_on_this_host`` rather than assumed:

    * FILE_FLAG_BACKUP_SEMANTICS is required to open a directory as a handle at
      all; without it CreateFileW fails outright.
    * FlushFileBuffers needs **GENERIC_WRITE**. With GENERIC_READ the handle
      opens happily and the flush then fails ERROR_ACCESS_DENIED (5) -- i.e. it
      degrades to a silent no-op, which is exactly the failure mode a
      best-effort helper is most likely to hide.

    ``argtypes`` are declared because an undeclared HANDLE return is truncated to
    a C int on 64-bit, producing a garbage handle.

    Still best-effort and silent on failure: a read-only share or a filesystem
    that refuses directory handles costs durability, never correctness.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        generic_write = 0x40000000
        share_all = 0x00000001 | 0x00000002 | 0x00000004
        open_existing = 3
        backup_semantics = 0x02000000
        invalid_handle = ctypes.c_void_p(-1).value

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.CreateFileW(
            str(directory), generic_write, share_all, None,
            open_existing, backup_semantics, None,
        )
        if not handle or handle == invalid_handle:
            return False
        try:
            return bool(kernel32.FlushFileBuffers(handle))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


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
        #: Highest epoch_ms this instance has ever written (Inc 4b writer-side
        #: guard). A backwards clock step must never make a NEW record look
        #: older than one we already published.
        self._last_epoch_ms = 0

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
            # A temp name (Inc 4b: ``<final>.json.tmp``) does not end in
            # ``.json`` and is therefore skipped, NOT treated as corrupt. It was
            # never published as a record, so no grant was ever made from it and
            # ignoring it reproduces the pre-write state exactly. A truncated
            # ``.json``, by contrast, WAS published and still fails closed.
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

    def _writer_epoch_ms(self) -> int:
        """Our wall clock in ms, clamped so it can never regress (Inc 4b).

        An NTP step backwards would otherwise let us publish a record that looks
        OLDER than one we already wrote, shortening our own lease under the
        fold. Clamping upward can only LENGTHEN a lease -- the safe direction,
        consistent with ``max_skew`` being added and never subtracted.
        """
        epoch_ms = int(self._clock() * 1000)
        if epoch_ms < self._last_epoch_ms:
            epoch_ms = self._last_epoch_ms
        self._last_epoch_ms = epoch_ms
        return epoch_ms

    def _assert_peer_skew_within_bound(self, records: list, instance_id) -> None:
        """Refuse to grant while a peer's clock is ahead beyond ``max_skew``.

        The fold's ``+ max_skew`` only makes "skew lengthens, never shortens"
        true while the real disagreement stays inside the bound. Past it, a peer
        that is BEHIND by more than the bound would have its live lease expired
        early by our fold -- a double-grant. That direction is undetectable from a
        record alone (an old-looking timestamp is indistinguishable from an old
        record), but the symmetric AHEAD direction is directly observable: a
        record stamped in our future by more than the bound proves the fleet's
        clocks are outside the configured envelope.

        So we fail closed on the observable half and let Inc 0 preflight /
        Inc 7 startup gating cover the measurement of both halves.

        Our OWN records are skipped: ``_writer_epoch_ms`` may deliberately clamp
        them forward, which is a guard, not a skew.

        Raises:
            ClockSkewError: some peer record is stamped past ``now + max_skew``.
        """
        bound = self._clock() + self.max_skew_seconds
        for rec in records:
            if rec.get("__corrupt__"):
                continue  # unreadable stamp; already fail-closed by the fold
            if rec.get("instance_id") == instance_id:
                continue
            epoch_ms = _as_float(rec.get("epoch_ms"))
            if epoch_ms is None:
                continue
            stamped = epoch_ms / 1000.0
            if stamped > bound:
                raise ClockSkewError(
                    "peer %r clock is ahead by %.3fs, exceeding max_skew_seconds"
                    "=%.3fs; refusing to grant (fail-closed)"
                    % (rec.get("instance_id"), stamped - self._clock(),
                       self.max_skew_seconds)
                )

    def _append(self, kind: str, paths: list, instance_id: str,
                ttl_seconds: float, lease_id: str) -> dict:
        """Write one immutable record under a name unique by construction.

        Durability (Inc 4b): the record is written to a temp name in the SAME
        directory, flushed and ``os.fsync``ed, then ``os.replace``d onto its final
        name, and finally the parent directory is synced. Order matters -- a peer
        must never be able to see a record name whose bytes are not yet durable,
        and a reader must never see a partially written ``.json``.
        """
        directory = Path(self.claims_dir)
        directory.mkdir(parents=True, exist_ok=True)

        lamport = self._next_lamport()
        epoch_ms = self._writer_epoch_ms()
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
        # No exclusive-create and no advisory locking: the name cannot collide,
        # so the temp name cannot collide either.
        final_path = directory / name
        temp_path = directory / (name[: -len(".json")] + TEMP_SUFFIX)
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())          # 1. bytes durable
        os.replace(str(temp_path), str(final_path))   # 2. name published atomically
        _fsync_dir(directory)                  # 3. entry durable/visible
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
            ClockSkewError: a peer's clock is ahead of ours by more than
                ``max_skew_seconds``, so the TTL bound no longer holds (Inc 4b
                writer-side guard). Fail-closed: nothing is written, nothing is
                granted.
        """
        canonical = self._canon_all(paths)
        if not canonical:
            raise ValueError("claim() requires at least one path")

        # Inc 4b: check the clock envelope BEFORE writing anything, so a refusal
        # leaves no record behind at all.
        self._assert_peer_skew_within_bound(self._read_records(), instance_id)

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

    # -- garbage collection (Inc 4b) ---------------------------------------

    def compact(self, retain_seconds: float = 0.0) -> int:
        """Delete records that are PROVABLY dead; keep everything else.

        An append-only directory grows without bound, so it needs a collector --
        but a claim log's collector is safety-critical: deleting the wrong file
        can hand a held path to a second instance. The rule is therefore
        deliberately one-sided. A record is removed only when it can be *proved*
        dead past the full bound

            ttl + max_skew_seconds + settle_seconds + retain_seconds

        and anything unprovable is kept forever. Concretely:

        * **Group-atomic.** All records of a lease (request + heartbeats +
          tombstone) are deleted together or not at all. Deleting a tombstone
          while its request survives would RESURRECT a released claim; deleting a
          request while its heartbeats survive would silently shorten a live
          lease. Deleting the whole group is safe because a peer that sees no
          records concludes exactly what a peer that sees the tombstone
          concludes: the path is free.
        * **Never live.** A lease still winning a path in the current fold is
          never touched, and neither is one whose deadline (request extended by
          its newest heartbeat) has not yet cleared the full bound. The
          ``+ max_skew`` term makes this hold for a skewed peer too.
        * **Never unprovable.** A legacy record carrying no ``ttl`` never
          expires, so no group containing one is ever collectable.
        * **Corrupt records** carry no readable deadline, so the only evidence is
          the file's mtime; they go only once ``mtime + default_ttl + max_skew +
          settle + retain`` has passed -- the same full bound the fold uses to
          stop treating them as a live claim by an unknown holder.
        * **Idempotent** and safe to run concurrently from several instances: a
          file another compactor already removed is not an error.

        Args:
            retain_seconds: extra grace added to the bound. 0 collects as soon as
                the safety bound allows; a larger value keeps a forensic tail.

        Returns:
            Number of record files actually deleted.
        """
        directory = Path(self.claims_dir)
        if not directory.is_dir():
            return 0
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return 0  # unreadable dir: collect nothing (fail-closed)

        now = float(self._clock())
        margin = self.settle_seconds + max(0.0, float(retain_seconds))
        # A record is only a candidate if it was already dead this long ago.
        cutoff = now - margin

        parsed: list = []          # (name, record_or_None, mtime)
        for name in names:
            if not name.endswith(".json"):
                continue           # temp names are never collected here
            fpath = directory / name
            try:
                with open(fpath, encoding="utf-8") as handle:
                    rec = json.load(handle)
                if not isinstance(rec, dict):
                    raise ValueError("record is not an object")
            except Exception:
                rec = None
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue           # vanished under us; nothing to collect
            parsed.append((name, rec, mtime))

        # Leases currently winning a path are off-limits, belt and braces.
        live_leases = {
            v[1] for v in fold_fs_claims(
                self._read_records(), now=now,
                max_skew=self.max_skew_seconds, detail=True,
            ).values()
        }

        groups: dict = {}
        doomed: list = []
        for name, rec, mtime in parsed:
            if rec is None:
                # Corrupt: mtime is the only evidence we have.
                if cutoff > mtime + self.default_ttl_seconds + self.max_skew_seconds:
                    doomed.append(name)
                continue
            lease_id = rec.get("lease_id")
            if lease_id is None:
                continue           # no group to reason about; keep it
            groups.setdefault(lease_id, []).append((name, rec))

        for lease_id, members in groups.items():
            if lease_id in live_leases:
                continue
            kinds = {rec.get("kind") for _n, rec in members}
            if KIND_CLAIM_REQUESTED not in kinds:
                continue           # orphan heartbeat/tombstone: keep it
            deadlines = [_deadline(rec, self.max_skew_seconds)
                         for _n, rec in members]
            if any(d is None for d in deadlines):
                continue           # a ttl-less record: unprovable, keep the group
            if cutoff <= max(deadlines):
                continue           # not yet dead past the full bound
            doomed.extend(name for name, _rec in members)

        deleted = 0
        for name in doomed:
            try:
                os.remove(str(directory / name))
                deleted += 1
            except OSError:
                continue           # already collected by a peer: not an error
        return deleted

    def close(self) -> None:
        """No resources are held open; present for ClaimBackend parity."""
        return None
