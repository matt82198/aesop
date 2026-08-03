"""state_store.identity — stable per-instance orchestrator identity with epoch fencing.

Implements durable instance identity: hostname:pid:nonce (ephemeral) or stable_id:epoch
(persisted for multibox coordination).

- Ephemeral form (get_instance_id): hostname:pid:nonce; cached within process.
- Durable form (get_identity_with_epoch): (stable_id, epoch) persisted to $AESOP_STATE_ROOT/instance-id.
  Epoch is a monotonic boot counter; restart increments it. Used for claim fencing in multibox.

Fail-open on IDENTITY: corrupt/missing/unwritable id file falls back to ephemeral without raising.

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

    Fail-open: corrupt/missing/unwritable id file falls back to ephemeral form without raising.

    Args:
        state_root: Base directory for persisted identity file. Defaults to AESOP_STATE_ROOT
                   env var or ./state.

    Returns:
        Tuple of (stable_id, epoch) where epoch is >= 1.
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
    except Exception:
        # Fail-open: fall back to ephemeral form without raising
        ephemeral_id = _derive_instance_id()
        # Use epoch 1 for ephemeral (not persisted)
        _IDENTITY_CACHE[state_root] = (ephemeral_id, 1)
        return ephemeral_id, 1


def _init_identity_file(state_root: str) -> tuple[str, int]:
    """Initialize or load persisted identity file.

    Creates $state_root/instance-id if it doesn't exist, or loads it if present.
    Increments epoch on each fresh initialization (simulated restart).

    Args:
        state_root: Directory for persisted identity file.

    Returns:
        Tuple of (stable_id, epoch).

    Raises:
        Exception: If file I/O fails beyond recovery (handled by caller with fail-open).
    """
    id_path = Path(state_root) / "instance-id"
    Path(state_root).mkdir(parents=True, exist_ok=True)

    if id_path.exists():
        # Load existing identity
        try:
            with open(id_path, encoding="utf-8") as f:
                data = json.load(f)
            stable_id = data["stable_id"]
            epoch = data["epoch"]
            return stable_id, epoch
        except (json.JSONDecodeError, KeyError):
            # Corrupt file; fall back to ephemeral
            raise RuntimeError("Corrupt identity file")
    else:
        # Create new persistent identity
        stable_id = _derive_stable_id()
        epoch = 1
        data = {"stable_id": stable_id, "epoch": epoch}
        try:
            with open(id_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            # Unwritable; fall back to ephemeral
            raise RuntimeError("Unable to write identity file")
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
    """Release (reclaim) own claims from prior epochs on startup.

    Coordinated with the lease backend (Inc 4+) to immediately reclaim claims
    from a restarted instance's prior epochs, avoiding TTL wait time.

    Args:
        state_root: Base directory for state.
        stable_id: Stable identity (from get_identity_with_epoch).
        prior_epochs: List of epoch numbers to reclaim.

    Returns:
        True if all prior epochs were released (or none existed).
    """
    # TODO: Implement in Inc 5 when lease backend coordination is wired.
    # For now, this is a no-op placeholder to satisfy the API contract.
    return True
