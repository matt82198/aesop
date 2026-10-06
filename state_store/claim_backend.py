"""state_store.claim_backend — Atomic claim backend protocol.

Defines ClaimBackend protocol for atomic file-path claiming across instances,
and LocalLeaseBackend adapter over LeaseStore for single-box coordination.

ClaimBackend is an abstraction layer enabling multi-dispatch to use atomic
claims (fixing TOCTOU race) instead of advisory append-based claims, while
keeping the legacy path unchanged when the multibox flag is off.

Increment 2 of the multibox plan: atomic dispatch claims seam.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from state_store.lease_claims import LeaseConflict as LeaseStoreConflict
from state_store.lease_claims import LeaseStore


class ClaimConflict(Exception):
    """Raised when claim() fails due to path already held by another instance."""

    def __init__(self, conflicting_instance: str, conflicting_paths: list[str]):
        self.conflicting_instance = conflicting_instance
        self.conflicting_paths = conflicting_paths
        msg = f"Path conflict with {conflicting_instance}: {conflicting_paths}"
        super().__init__(msg)


class ClaimBackend(ABC):
    """Protocol for atomic file-path claiming across instances.

    Any implementation must provide atomic claim/renew/release/holder operations.
    This is the interface boundary for swapping backends (Inc 4a: FsClaimLog).
    """

    @abstractmethod
    def claim(
        self, paths: list[str], instance_id: str, ttl_seconds: float
    ) -> str:
        """Atomically claim a set of file paths.

        Args:
            paths: list of file paths to claim
            instance_id: instance identifier requesting the claim
            ttl_seconds: time-to-live in seconds before lease expires

        Returns:
            lease_id: unique identifier for this lease

        Raises:
            ClaimConflict: if any path is held by another active instance
            Exception: any error (fail-closed)
        """
        ...

    @abstractmethod
    def renew(self, lease_id: str, instance_id: str, ttl_seconds: float) -> None:
        """Extend a live lease's TTL.

        Args:
            lease_id: the lease identifier
            instance_id: the instance requesting renewal (must be the holder)
            ttl_seconds: new time-to-live in seconds

        Raises:
            ValueError: if lease not found or instance_id does not hold it
            Exception: any error (fail-closed)
        """
        ...

    @abstractmethod
    def release(self, lease_id: str, instance_id: str) -> None:
        """Release a held lease, making paths claimable by others.

        Args:
            lease_id: the lease identifier
            instance_id: the instance requesting release (must be the holder)

        Raises:
            ValueError: if instance_id does not hold this lease
            Exception: any error (fail-closed)
        """
        ...

    @abstractmethod
    def holder(self, paths: list[str]) -> Optional[str]:
        """Return the instance_id currently holding all given paths, or None.

        Args:
            paths: list of file paths to check

        Returns:
            instance_id if all paths are held by the same instance, None otherwise
        """
        ...


class LocalLeaseBackend(ClaimBackend):
    """Adapter: ClaimBackend protocol over LeaseStore (single-box coordination).

    Implements the ClaimBackend protocol using LeaseStore's atomic claim/renew/release
    primitives. Atomicity is inherited from LeaseStore's BEGIN IMMEDIATE transaction.

    Used when multibox.enabled=True; leverages the existing LeaseStore which is
    already proven to be atomic (6311288b fixed TOCTOU at this level).
    """

    def __init__(self, db_path: str, config: Optional[dict] = None):
        """Initialize with a LeaseStore.

        Args:
            db_path: path to SQLite database file
            config: optional aesop config dict. ``multibox.case_policy`` selects the
                    claim-path case policy; omitting it uses the host-independent
                    default (see state_store.lease_claims).
        """
        self._store = LeaseStore(db_path, config=config)

    def claim(
        self, paths: list[str], instance_id: str, ttl_seconds: float
    ) -> str:
        """Atomically claim paths using LeaseStore.

        Converts LeaseConflict to ClaimConflict for protocol consistency.
        """
        try:
            lease_id = self._store.claim(paths, instance_id, ttl_seconds)
            return lease_id
        except LeaseStoreConflict as e:
            # Convert LeaseStore's LeaseConflict to ClaimBackend's ClaimConflict
            raise ClaimConflict(e.conflicting_instance, e.conflicting_paths)

    def renew(self, lease_id: str, instance_id: str, ttl_seconds: float) -> None:
        """Extend lease TTL via LeaseStore."""
        self._store.renew(lease_id, instance_id, ttl_seconds)

    def release(self, lease_id: str, instance_id: str) -> None:
        """Release lease via LeaseStore."""
        self._store.release(lease_id, instance_id)

    def holder(self, paths: list[str]) -> Optional[str]:
        """Query current holder via LeaseStore."""
        return self._store.get_holder(paths)

    def close(self) -> None:
        """Close the underlying LeaseStore connection."""
        self._store.close()


def get_backend(db_path: str, config: Optional[dict] = None) -> Optional[ClaimBackend]:
    """Get a ClaimBackend instance based on config.

    Args:
        db_path: path to SQLite database file
        config: configuration dict with multibox.enabled key
                If None or multibox.enabled=False, returns None (advisory path)
                If multibox.enabled=True, returns LocalLeaseBackend (atomic path)

    Returns:
        ClaimBackend instance if multibox.enabled=True, None otherwise
    """
    if config is None:
        config = {}

    multibox_config = config.get("multibox", {})
    enabled = multibox_config.get("enabled", False)

    if enabled:
        # Pass config through so multibox.case_policy reaches the claim keyspace.
        return LocalLeaseBackend(db_path, config=config)
    else:
        return None
