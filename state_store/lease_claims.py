"""state_store.lease_claims — multi-instance file-scope lease coordination.

Provides atomic file-path leasing for multi-instance Aesop coordination on a single box.
Uses a dedicated SQLite leases table (not event-sourced) for atomic claim/renew/release
operations with deterministic TTL-based expiry. Leases past their deadline are reclaimable
by other instances (steal-on-expiry); expiry check happens at claim time, not via background
threads. Fails closed: any exception in claim/renew/release propagates; no silent failures.

Deterministic time injection (clock parameter) enables testing without sleeps or mocking.

PLATFORM-SPECIFIC PATH NORMALIZATION:
  On Windows (os.name == 'nt'): paths are normalized via os.path.normcase() + os.path.normpath()
    to handle both separator styles (/ vs \\) and case-insensitivity (README.md vs README.MD).
  On Linux/Unix: paths are normalized via os.path.normpath() only, preserving case sensitivity
    (README.md and README.MD are legitimately different files). This respects local filesystem
    semantics while catching separator mismatches (dir/file vs dir/file).
  Original path strings are preserved in error messages for user clarity.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from typing import Callable, Optional

from state_store.paths import canonical_claim_path


def _normalize_path(path: str) -> str:
    """Normalize a file path for comparison, respecting platform-specific semantics.

    DEPRECATED: This is now a thin alias to canonical_claim_path() with case_policy="platform"
    for backward compatibility. New code should use canonical_claim_path() directly.

    On Windows (os.name == 'nt'):
      - Normalize separators (/ and \\ both become the platform separator)
      - Apply case-folding (case-insensitive filesystem)
      Result: 'dir/file.txt' and 'dir\\FILE.TXT' compare equal

    On Linux/Unix (os.name != 'nt'):
      - Normalize separators (standardize to forward slashes)
      - Preserve case (case-sensitive filesystem)
      Result: 'dir/file.txt' and 'dir\\file.txt' compare equal (same canonical form)
      But: 'readme.md' and 'README.MD' remain different

    Args:
        path: the file path to normalize

    Returns:
        Canonical form of the path, normalized for filesystem comparison
    """
    # Delegate to canonical_claim_path with case_policy="platform" for backward compatibility
    return canonical_claim_path(path, case_policy="platform")


class LeaseConflict(Exception):
    """Raised when claim fails due to path already held by another instance."""

    def __init__(self, conflicting_instance: str, conflicting_paths: list[str]):
        self.conflicting_instance = conflicting_instance
        self.conflicting_paths = conflicting_paths
        msg = f"Path conflict with {conflicting_instance}: {conflicting_paths}"
        super().__init__(msg)


class LeaseStore:
    """Atomic file-scope lease store using SQLite with deterministic time injection."""

    def __init__(self, db_path: str):
        """Initialize lease store with SQLite database.

        Args:
            db_path: path to SQLite database file
        """
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create thread-local SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=5.0)
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for concurrent readers
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def _init_schema(self) -> None:
        """Create leases table if not exists."""
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leases (
                lease_id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL,
                paths TEXT NOT NULL,
                claimed_at REAL NOT NULL,
                ttl_seconds REAL NOT NULL,
                released_at REAL,
                UNIQUE(lease_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_instance_id
            ON leases(instance_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_released_at
            ON leases(released_at)
            """
        )
        conn.commit()

    def close(self) -> None:
        """Close database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def claim(
        self,
        paths: list[str],
        instance_id: str,
        ttl_seconds: float,
        clock: Optional[Callable[[], float]] = None,
    ) -> str:
        """Atomically claim a set of file paths.

        All paths must be unclaimed or held by expired leases. If any path is held
        by another instance with an active (non-expired) lease, raises LeaseConflict
        without modifying state. Fails closed: no partial claims.

        Paths are normalized for platform-specific filesystem comparison (separators,
        case-sensitivity). The original path strings are preserved for error messages.

        Args:
            paths: list of file paths to claim
            instance_id: instance identifier requesting the claim
            ttl_seconds: time-to-live in seconds before lease expires
            clock: optional callable returning current time (default: time.time)

        Returns:
            lease_id: unique identifier for this lease

        Raises:
            LeaseConflict: if any path is held by another active instance
            Exception: any SQLite error propagates (fail-closed)
        """
        if clock is None:
            clock = time.time

        now = clock()
        lease_id = str(uuid.uuid4())

        # Normalize paths for storage and conflict checking
        normalized_paths = [_normalize_path(p) for p in paths]

        conn = self._get_conn()

        try:
            # ONE atomic transaction: check conflicts and insert in the same lock
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Check for conflicts inside the transaction (check holds the lock)
                conflict_instance, conflict_paths = self._check_conflicts(
                    normalized_paths, instance_id, now, conn
                )
                if conflict_instance is not None:
                    conn.rollback()
                    raise LeaseConflict(conflict_instance, conflict_paths)

                # All paths are available: atomically insert the lease with normalized paths
                paths_json = json.dumps(sorted(normalized_paths), separators=(",", ":"))
                conn.execute(
                    """
                    INSERT INTO leases
                    (lease_id, instance_id, paths, claimed_at, ttl_seconds, released_at)
                    VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (lease_id, instance_id, paths_json, now, ttl_seconds),
                )
                conn.commit()
            except LeaseConflict:
                # Re-raise LeaseConflict if it was raised (already rolled back above)
                raise
            except Exception:
                conn.rollback()
                raise

            return lease_id

        except LeaseConflict:
            raise
        except Exception:
            # Fail-closed: propagate any error
            raise

    def _check_conflicts(
        self,
        paths: list[str],
        instance_id: str,
        now: float,
        conn: sqlite3.Connection,
    ) -> tuple[Optional[str], list[str]]:
        """Check if any path is held by another active instance.

        IMPORTANT: This method assumes the caller holds a write lock (via BEGIN IMMEDIATE).
        It does NOT manage transactions — caller is responsible for opening and committing.
        This prevents TOCTOU races where the lock is released between check and write.

        Returns (conflicting_instance, conflicting_paths) or (None, []).
        """
        # Collect all non-released leases
        rows = conn.execute(
            """
            SELECT instance_id, paths, claimed_at, ttl_seconds
            FROM leases
            WHERE released_at IS NULL
            """,
        ).fetchall()

        for path in paths:
            for row in rows:
                holder = row["instance_id"]
                claimed_at = row["claimed_at"]
                ttl = row["ttl_seconds"]
                lease_paths = json.loads(row["paths"])

                # Check if path is in this lease and lease is not expired
                if path in lease_paths:
                    if now <= claimed_at + ttl:
                        # Active lease held by another instance
                        if holder != instance_id:
                            return holder, lease_paths
                    # else: lease is expired, continue checking
        return None, []

    def renew(
        self,
        lease_id: str,
        instance_id: str,
        ttl_seconds: float,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """Extend a live lease's TTL.

        Only the instance that holds the lease may renew it, and only if the lease
        is still valid (not expired and not released). Updates the claimed_at timestamp
        and ttl_seconds to extend the deadline.

        Args:
            lease_id: the lease identifier
            instance_id: the instance requesting renewal (must be the holder)
            ttl_seconds: new time-to-live in seconds
            clock: optional callable returning current time (default: time.time)

        Raises:
            ValueError: if lease not found, instance_id does not hold it, lease is expired, or lease is released
            Exception: any SQLite error propagates (fail-closed)
        """
        if clock is None:
            clock = time.time

        now = clock()

        conn = self._get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT instance_id, claimed_at, ttl_seconds, released_at FROM leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()

            if row is None:
                conn.rollback()
                raise ValueError(f"Lease {lease_id} not found")

            holder = row["instance_id"]
            if holder != instance_id:
                conn.rollback()
                raise ValueError(
                    f"Cannot renew lease held by {holder} from {instance_id}"
                )

            # Check if lease has been released
            if row["released_at"] is not None:
                conn.rollback()
                raise ValueError(f"Cannot renew released lease {lease_id}")

            # Check if lease has expired
            claimed_at = row["claimed_at"]
            ttl = row["ttl_seconds"]
            if now > claimed_at + ttl:
                conn.rollback()
                raise ValueError(f"Cannot renew expired lease {lease_id}")

            # Update claimed_at to extend the deadline
            conn.execute(
                """
                UPDATE leases
                SET claimed_at = ?, ttl_seconds = ?
                WHERE lease_id = ?
                """,
                (now, ttl_seconds, lease_id),
            )
            conn.commit()

        except Exception:
            conn.rollback()
            raise

    def release(
        self,
        lease_id: str,
        instance_id: str,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """Release a held lease, making paths claimable by others.

        Only the instance that holds the lease may release it. Sets released_at
        timestamp to mark the lease as inactive.

        Args:
            lease_id: the lease identifier
            instance_id: the instance requesting release (must be the holder)
            clock: optional callable returning current time (default: time.time)

        Raises:
            ValueError: if instance_id does not hold this lease
            Exception: any SQLite error propagates (fail-closed)
        """
        if clock is None:
            clock = time.time

        now = clock()

        conn = self._get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT instance_id FROM leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()

            if row is None:
                conn.rollback()
                raise ValueError(f"Lease {lease_id} not found")

            holder = row["instance_id"]
            if holder != instance_id:
                conn.rollback()
                raise ValueError(
                    f"Cannot release lease held by {holder} from {instance_id}"
                )

            # Mark lease as released
            conn.execute(
                "UPDATE leases SET released_at = ? WHERE lease_id = ?",
                (now, lease_id),
            )
            conn.commit()

        except Exception:
            conn.rollback()
            raise

    def get_holder(
        self,
        paths: list[str],
        clock: Optional[Callable[[], float]] = None,
    ) -> Optional[str]:
        """Return the instance_id currently holding all given paths, or None.

        A path is held if there exists an active (non-released, non-expired)
        lease containing it. Paths are normalized for platform-specific comparison
        (separators, case-sensitivity). If any of the given paths is not held by
        the same instance, returns None.

        Args:
            paths: list of file paths to check
            clock: optional callable returning current time (default: time.time)

        Returns:
            instance_id if all paths are held by the same instance, None otherwise
        """
        if clock is None:
            clock = time.time

        if not paths:
            return None

        # Normalize paths for comparison
        normalized_paths = [_normalize_path(p) for p in paths]
        now = clock()

        conn = self._get_conn()

        # Collect all non-released leases
        rows = conn.execute(
            """
            SELECT lease_id, instance_id, paths, claimed_at, ttl_seconds
            FROM leases
            WHERE released_at IS NULL
            """,
        ).fetchall()

        # Find which instance holds each normalized path
        path_holders = {}
        for path in normalized_paths:
            path_holders[path] = None
            for row in rows:
                holder = row["instance_id"]
                claimed_at = row["claimed_at"]
                ttl = row["ttl_seconds"]
                lease_paths = json.loads(row["paths"])

                # Check if path is in this lease and lease is not expired
                if path in lease_paths and now <= claimed_at + ttl:
                    path_holders[path] = holder
                    break  # Found the holder for this path

        # All paths must be held by the same instance
        unique_holders = set(h for h in path_holders.values() if h is not None)
        if len(unique_holders) == 1 and None not in path_holders.values():
            return unique_holders.pop()
        else:
            # Some path is not held, or paths held by different instances
            return None
