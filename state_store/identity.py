"""state_store.identity — stable per-instance orchestrator identity with epoch fencing.

Implements durable instance identity: hostname:pid:nonce (ephemeral) or stable_id:epoch
(persisted for multibox coordination).

- Ephemeral form (get_instance_id): hostname:pid:nonce; cached within process.
- Durable form (get_identity_with_epoch): (stable_id, epoch) persisted to $AESOP_STATE_ROOT/instance-id.
  Epoch is a monotonic boot counter; ACQUIRING the identity increments and persists it
  (atomic read-modify-write), so each process/boot gets a strictly higher fencing token
  than every prior boot on that state root. Within one process the value is cached, so
  repeated calls return the same epoch. Used for claim fencing in multibox.

Fail-open on IDENTITY creation: missing/unwritable id file on FRESH box falls back to ephemeral.
Fail-closed on corruption: if id file EXISTED but is corrupt (torn write, missing keys, or a
non-integer epoch), raises IdentityCorruptionError.
Fail-closed on persist failure: if the bumped epoch cannot be written, raises EpochPersistError
(a subclass of IdentityCorruptionError) rather than handing back a reused epoch.
The distinction preserves epoch monotonicity: a stale crashed instance cannot claim epoch=1 after a restart.

Stdlib only: socket, os, random, string, json, pathlib.
"""
from __future__ import annotations

import json
import os
import random
import socket
import string
from pathlib import Path
from typing import Optional


class IdentityCorruptionError(Exception):
    """Raised when persisted identity file is corrupt and recovery is impossible."""

    pass


class EpochPersistError(IdentityCorruptionError):
    """Raised when a bumped epoch cannot be durably persisted.

    Subclasses IdentityCorruptionError so existing fail-closed handlers keep working.
    Fail-closed on purpose: returning the un-bumped epoch would hand a restarting
    instance the SAME fencing token its pre-crash self may still be using.
    """

    pass


# Cached instance_id (ephemeral form), computed once per process
_INSTANCE_ID_CACHE: str | None = None

# Cached persistent identity (stable_id, epoch), computed once per process per state root
_IDENTITY_CACHE: dict[str, tuple[str, int]] = {}


def get_instance_id() -> str:
    """Return a stable per-instance orchestrator id (ephemeral form).

    Computed once per process and cached. The id is the form:
        hostname:pid:nonce

    Where nonce is a random 6-character alphanumeric string, used to
    differentiate multiple orchestrators on the same box.

    Returns:
        A stable string identifier for this orchestrator instance (ephemeral form).
    """
    global _INSTANCE_ID_CACHE
    if _INSTANCE_ID_CACHE is None:
        _INSTANCE_ID_CACHE = _derive_instance_id()
    return _INSTANCE_ID_CACHE


def get_identity_with_epoch(state_root: Optional[str] = None) -> tuple[str, int]:
    """Return durable instance identity with monotonic epoch counter.

    Persists identity to $state_root/instance-id as JSON: {stable_id, epoch}.
    Stable ID is derived from hostname and a unique nonce, persisted across restarts.
    Epoch is a monotonic boot counter; each simulated restart should increment it.

    Fail-closed on corruption: if id file existed but is corrupt (torn write), raises
    IdentityCorruptionError. This preserves epoch monotonicity for fencing (Inc 4+).
    Fail-open on CREATION: missing state_root or unwritable directory falls back to
    ephemeral form for fresh boxes.

    Args:
        state_root: Base directory for persisted identity file. Defaults to AESOP_STATE_ROOT
                   env var or ./state.

    Returns:
        Tuple of (stable_id, epoch) where epoch is >= 1.

    Raises:
        IdentityCorruptionError: If id file existed but is corrupt (torn/partial JSON).
                                 This is fail-closed to prevent epoch resets on restart.
    """
    if state_root is None:
        state_root = os.environ.get("AESOP_STATE_ROOT", "./state")

    # Return cached value if available for this state root
    if state_root in _IDENTITY_CACHE:
        return _IDENTITY_CACHE[state_root]

    try:
        stable_id, epoch = _init_identity_file(state_root)
        _IDENTITY_CACHE[state_root] = (stable_id, epoch)
        return stable_id, epoch
    except IdentityCorruptionError:
        # Fail-closed: corrupt file when prior file existed is a hard error.
        # Caller must handle (e.g., manual recovery, abort startup).
        raise
    except (OSError, IOError):
        # Fail-open: unwritable directory on FRESH box (no prior file) falls back
        # to ephemeral form. This allows solo mode to boot even if state root is
        # unwritable (e.g., permissions, filesystem error on a fresh box).
        ephemeral_id = _derive_instance_id()
        _IDENTITY_CACHE[state_root] = (ephemeral_id, 1)
        return ephemeral_id, 1


def _write_identity_atomic(id_path: Path, data: dict) -> None:
    """Persist the identity file atomically (temp file in the same dir + os.replace).

    A torn write here is what `_init_identity_file` fails closed on at the next boot,
    so the write itself must never be able to leave a partial file behind.

    Raises:
        OSError: if the temp write, flush, or replace fails.
    """
    tmp_path = id_path.with_name(id_path.name + f".tmp.{os.getpid()}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, id_path)
    except BaseException:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _init_identity_file(state_root: str) -> tuple[str, int]:
    """Initialize or load persisted identity, INCREMENTING the epoch on acquisition.

    Epoch is a monotonic boot counter used as the multibox fencing token. Acquiring an
    identity is what advances it — before deep-scan B3 nothing in the repo ever
    incremented it, so `get_identity_with_epoch` returned 1 forever and a post-crash
    instance was indistinguishable from its pre-crash self.

    Distinguishes fresh box (no prior file) from crash recovery (corrupt file):
    - If file doesn't exist: creates it with epoch=1 (fresh box)
    - If file exists but is corrupt: raises IdentityCorruptionError (fail-closed)
    - If file exists and is valid: atomically read-modify-writes epoch -> epoch + 1
      and returns the NEW epoch

    Args:
        state_root: Directory for persisted identity file.

    Returns:
        Tuple of (stable_id, epoch), where epoch is the freshly acquired value.

    Raises:
        IdentityCorruptionError: If file exists but is corrupt (JSON parse error, missing
                                 keys, non-integer epoch). Preserves epoch monotonicity.
        EpochPersistError: If the bumped epoch cannot be durably written. Fail-closed:
                           reusing an epoch would defeat fencing.
        OSError: If directory creation or FRESH-box creation fails (caller falls back to
                 ephemeral only for the fresh-box case).
    """
    id_path = Path(state_root) / "instance-id"
    Path(state_root).mkdir(parents=True, exist_ok=True)

    if id_path.exists():
        # File EXISTS: load it (strict fail-closed on corruption)
        try:
            with open(id_path, encoding="utf-8") as f:
                content = f.read()
            if not content or not content.strip():
                # Empty or whitespace-only file: torn write during persist
                raise IdentityCorruptionError(
                    f"Identity file {id_path} is empty; corrupted during prior write. "
                    "Restart refused to preserve epoch monotonicity. "
                    f"Manual recovery: remove {id_path} and restart."
                )
            data = json.loads(content)
            stable_id = data.get("stable_id")
            epoch = data.get("epoch")
            if not stable_id or epoch is None:
                raise IdentityCorruptionError(
                    f"Identity file {id_path} missing required keys (stable_id, epoch). "
                    "Corrupted or incompatible format. "
                    f"Manual recovery: remove {id_path} and restart."
                )
        except json.JSONDecodeError as e:
            # JSON parse error: torn/partial write during prior persist
            raise IdentityCorruptionError(
                f"Identity file {id_path} has invalid JSON: {e}. "
                "Corrupted during prior write. "
                f"Manual recovery: remove {id_path} and restart."
            )

        # An epoch we cannot increment monotonically is corrupt, not merely odd.
        # bool is an int subclass, so exclude it explicitly.
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise IdentityCorruptionError(
                f"Identity file {id_path} has non-integer epoch {epoch!r}; "
                "epoch monotonicity cannot be preserved. "
                f"Manual recovery: remove {id_path} and restart."
            )
        if epoch < 1:
            raise IdentityCorruptionError(
                f"Identity file {id_path} has epoch {epoch} < 1; "
                f"Manual recovery: remove {id_path} and restart."
            )

        # ACQUISITION: bump the fencing token and persist BEFORE returning it, so a
        # crash right after this point can never hand out the same epoch twice.
        new_epoch = epoch + 1
        try:
            _write_identity_atomic(id_path, {"stable_id": stable_id, "epoch": new_epoch})
        except OSError as e:
            raise EpochPersistError(
                f"Could not persist incremented epoch {new_epoch} to {id_path}: {e}. "
                "Refusing to start: reusing epoch "
                f"{epoch} would let this instance masquerade as its pre-crash self."
            )
        return stable_id, new_epoch
    else:
        # File DOES NOT EXIST: fresh box, create new identity with epoch=1
        stable_id = _derive_stable_id()
        epoch = 1
        data = {"stable_id": stable_id, "epoch": epoch}
        # Write may fail (unwritable directory), but we let OSError propagate
        # to caller which catches it and falls back to ephemeral (fail-open for fresh boxes)
        _write_identity_atomic(id_path, data)
        return stable_id, epoch


def _derive_instance_id() -> str:
    """Derive ephemeral instance_id from hostname, pid, and random nonce."""
    hostname = socket.gethostname()
    pid = os.getpid()
    # Random 6-char alphanumeric nonce for entropy across processes
    nonce = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{hostname}:{pid}:{nonce}"


def _derive_stable_id() -> str:
    """Derive stable identity from hostname and unique nonce (persisted component)."""
    hostname = socket.gethostname()
    # 12-char nonce for stable ID to reduce collision risk
    nonce = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{hostname}:{nonce}"


def release_own_stale(state_root: str, stable_id: str, prior_epochs: list[int]) -> bool:
    """Release (reclaim) own claims from prior epochs on startup. NOT YET IMPLEMENTED.

    Intended behaviour (Inc 5, when lease-backend coordination is wired): immediately
    reclaim claims still held under a restarted instance's prior epochs, so a restart
    does not have to wait out the full lease TTL.

    Deep-scan B3: this previously returned an unconditional ``True`` while doing nothing.
    That is worse than absent — a caller that trusted the return value believed prior
    claims had been reclaimed and would proceed to write files its pre-crash self still
    holds a live lease on. It now fails loudly instead. Callers that merely want
    best-effort reclamation should catch NotImplementedError and fall back to waiting
    out the TTL, which is the behaviour the stub was silently providing anyway.

    Args:
        state_root: Base directory for state.
        stable_id: Stable identity (from get_identity_with_epoch).
        prior_epochs: List of epoch numbers to reclaim.

    Returns:
        Never returns; declared bool for the eventual Inc 5 signature.

    Raises:
        NotImplementedError: always, until lease-backend coordination lands.
    """
    raise NotImplementedError(
        "release_own_stale is not implemented: prior-epoch claim reclamation requires "
        "the lease-backend coordination landing in multibox Inc 5. Until then, prior "
        "claims are reclaimed by TTL expiry. This raises rather than returning True so "
        "no caller can believe reclamation happened when it did not."
    )
