"""state_store.store — SQLite-backed, append-only, concurrency-safe event log.

Durable substrate for aesop's event-sourced state layer (the DB-source-of-truth
design; git becomes a rendered export, not the coordination layer). Backend is
SQLite in WAL mode so many readers and serialized writers share one file; the
same interface is designed to allow a backend swap behind
``state_store.api.StateAPI`` without touching call sites.

Connection reuse: each thread gets a lazily-created, cached connection via
``threading.local()``, avoiding per-call connect/close overhead while remaining
safe for multi-threaded callers (each thread has its own connection). Callers
sharing an EventStore across processes still get per-process isolation because
``threading.local()`` is per-thread-per-process.

Stdlib only (sqlite3, json, time, threading) per aesop's no-external-deps
invariant.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import threading
import time

# Retry configuration for database lock contention (especially under parallel CI shards)
_MAX_DB_LOCK_RETRIES = 3
_DB_LOCK_RETRY_BASE_DELAY = 0.05  # 50ms base, exponential backoff


class ConcurrencyConflict(Exception):
    """Raised when an optimistic concurrency control check fails on append.

    Signifies that the event stream's current version does not match the
    expected version provided to append(), indicating that another writer
    has extended the stream since the caller last read it. No event is
    written when this exception is raised (fail-closed).

    Attributes:
        expected_version: The version the caller expected
        actual_version: The version found in the database
    """

    def __init__(self, expected_version: int, actual_version: int):
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Concurrency conflict: expected version {expected_version}, "
            f"but stream is at version {actual_version}"
        )


def _retry_on_db_lock(func, max_retries=_MAX_DB_LOCK_RETRIES, base_delay=_DB_LOCK_RETRY_BASE_DELAY):
    """Retry a callable up to max_retries times if it hits 'database is locked' error.

    Used for defense-in-depth when multiple EventStore instances or CI shards
    briefly contend on WAL locks. Implements exponential backoff.

    Args:
        func: A callable that may raise sqlite3.OperationalError('database is locked').
        max_retries: Number of attempts (default 3).
        base_delay: Base delay in seconds for exponential backoff (default 0.05s).

    Returns:
        The return value of func().

    Raises:
        sqlite3.OperationalError: If all retries are exhausted or non-lock error occurs.
    """
    for attempt in range(max_retries):
        try:
            return func()
        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e):
                raise
            if attempt == max_retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))  # exponential: 0.05s, 0.1s, 0.2s


class EventStore:
    """Append-only event log stored at ``db_path``.

    Safe under concurrent appends from multiple threads AND multiple EventStore
    instances on the same file. Uses thread-local connection pooling: each
    thread gets its own cached SQLite connection with ``PRAGMA busy_timeout``
    and WAL mode set once, reused across calls. Per-stream version assignment
    happens inside a ``BEGIN IMMEDIATE`` transaction, so the
    read-max-version-then-insert is atomic and two writers can never collide or
    duplicate a version.

    Implements defense-in-depth retry logic for 'database is locked' errors that
    can occur under heavy parallel contention (e.g., CI shards).

    Call ``close()`` when the store is no longer needed to release the current
    thread's cached connection. Connections for other threads are cleaned up
    when those threads exit (Python's threading.local semantics).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()

        def _init_db():
            conn = self._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts      REAL    NOT NULL,
                    actor   TEXT    NOT NULL,
                    stream  TEXT    NOT NULL,
                    type    TEXT    NOT NULL,
                    payload TEXT    NOT NULL,
                    version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_stream_version "
                "ON events(stream, version)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts           REAL    NOT NULL,
                    stream       TEXT    NOT NULL,
                    event_version INTEGER NOT NULL,
                    projection   TEXT    NOT NULL,
                    checksum     TEXT    NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_snapshots_stream_version "
                "ON snapshots(stream, event_version)"
            )
            conn.commit()

        _retry_on_db_lock(_init_db)

    def _get_conn(self) -> sqlite3.Connection:
        """Return a cached, per-thread SQLite connection.

        Lazily creates a connection on first call per thread, sets WAL mode and
        busy_timeout once, then reuses it for all subsequent calls on that thread.
        Thread-local storage ensures no cross-thread sharing of connections.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close the current thread's cached connection, if any.

        Safe to call multiple times. After close(), the next operation on this
        thread will lazily open a fresh connection.
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def append(
        self,
        stream: str,
        event_type: str,
        payload: dict,
        actor: str = "system",
        expected_version: int | None = None,
    ) -> int:
        """Append one event to ``stream``; return its new per-stream version (1-based).

        Supports optimistic concurrency control via the optional ``expected_version``
        parameter. If provided, the append succeeds ONLY if the stream's current
        maximum version equals ``expected_version``. If the versions do not match,
        raises ConcurrencyConflict WITHOUT writing any event (fail-closed, atomic).

        The version check and append are both performed under BEGIN IMMEDIATE so
        they are atomic (no TOCTOU window).

        Args:
            stream: The stream name (e.g. "tracker", "claims")
            event_type: The event type (e.g. "claim_requested", "claim_released")
            payload: The event payload dict (will be JSON-serialized)
            actor: The actor performing the append (default "system")
            expected_version: Optional OCC check: if provided, append only if the
                             stream's current max version equals this value.
                             If None (default), OCC is disabled (backward-compatible).

        Returns:
            The new per-stream version assigned to this event (1-based).

        Raises:
            ConcurrencyConflict: If expected_version is provided and the stream's
                               current max version does not match. The exception
                               carries expected_version and actual_version for retry.
                               No event is written when this exception is raised.
        """

        def _do_append():
            conn = self._get_conn()
            try:
                # BEGIN IMMEDIATE takes the write lock up front so the
                # read-max-then-insert below is atomic under contention.
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM events WHERE stream = ?",
                    (stream,),
                ).fetchone()
                current_version = row[0]

                # Perform OCC check (if expected_version provided)
                if expected_version is not None:
                    if current_version != expected_version:
                        # Mismatch: abort transaction and raise
                        conn.rollback()
                        raise ConcurrencyConflict(expected_version, current_version)

                # OCC check passed or not enabled: proceed with append
                version = current_version + 1
                conn.execute(
                    "INSERT INTO events (ts, actor, stream, type, payload, version) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (time.time(), actor, stream, event_type, json.dumps(payload), version),
                )
                conn.commit()
                return version
            except ConcurrencyConflict:
                raise
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

        return _retry_on_db_lock(_do_append)

    def read(self, stream: str) -> list:
        """Return all events for ``stream`` ascending by version (empty if none)."""
        return self._rows("WHERE stream = ? ORDER BY version ASC", (stream,))

    def read_since(self, stream: str, after_version: int) -> list:
        """Return events for ``stream`` with version > ``after_version``.

        Enables tail-replay: given a snapshot at version N, read_since(stream, N)
        returns only the events appended after the snapshot, avoiding a full-stream
        scan. Combined with snapshot read/write, this provides O(tail) reads
        instead of O(total) for long-lived streams like claims.

        Args:
            stream: The stream name (e.g. "tracker", "claims")
            after_version: Return only events with version strictly greater than this.
                          Use 0 to read all events (equivalent to read()).

        Returns:
            List of event dicts, ascending by version.
        """
        return self._rows(
            "WHERE stream = ? AND version > ? ORDER BY version ASC",
            (stream, after_version),
        )

    def read_all(self) -> list:
        """Return all events across all streams ascending by id."""
        return self._rows("ORDER BY id ASC", ())

    def _rows(self, clause: str, params) -> list:
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT id, ts, actor, stream, type, payload, version FROM events " + clause,
            params,
        )
        rows = []
        for r in cur.fetchall():
            try:
                payload = json.loads(r[5])
                rows.append({
                    "id": r[0], "ts": r[1], "actor": r[2], "stream": r[3],
                    "type": r[4], "payload": payload, "version": r[6],
                })
            except json.JSONDecodeError as e:
                # Log corrupt event (stream id + sequence) to stderr and skip
                print(
                    f"WARNING: corrupt JSON payload in stream={r[3]} id={r[0]}: {e}",
                    file=sys.stderr,
                )
        return rows

    def save_snapshot(self, stream: str, event_version: int, projection: dict) -> None:
        """Save a materialized projection snapshot at a specific event version.

        The snapshot enables tail-replay: instead of replaying from event 1,
        project() can resume from this snapshot's version and fold only newer events.

        Args:
            stream: the stream name (e.g. "tracker", "claims")
            event_version: the per-stream event version this snapshot was computed through
            projection: the materialized state dict (e.g. the full tracker projection)
        """
        def _do_save_snapshot():
            conn = self._get_conn()
            try:
                projection_json = json.dumps(projection, separators=(",", ":"), sort_keys=True)
                checksum = hashlib.sha256(projection_json.encode()).hexdigest()
                conn.execute(
                    "INSERT INTO snapshots (ts, stream, event_version, projection, checksum) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (time.time(), stream, event_version, projection_json, checksum),
                )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

        _retry_on_db_lock(_do_save_snapshot)

    def read_snapshot(self, stream: str) -> tuple | None:
        """Read the most recent snapshot for a stream.

        Returns:
            A tuple (event_version, projection_dict, checksum) if a valid snapshot exists,
            or None if no snapshot found.

        On corrupt/unreadable snapshot, logs a warning and returns None (falls back to
        full replay).
        """
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT event_version, projection, checksum FROM snapshots "
            "WHERE stream = ? ORDER BY event_version DESC LIMIT 1",
            (stream,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        event_version, projection_json, checksum = row
        try:
            projection = json.loads(projection_json)
            # Verify checksum
            computed_checksum = hashlib.sha256(projection_json.encode()).hexdigest()
            if computed_checksum != checksum:
                print(
                    f"WARNING: snapshot checksum mismatch for stream={stream} "
                    f"version={event_version}; falling back to full replay",
                    file=sys.stderr,
                )
                return None
            return (event_version, projection, checksum)
        except json.JSONDecodeError as e:
            print(
                f"WARNING: corrupt JSON in snapshot for stream={stream} "
                f"version={event_version}: {e}; falling back to full replay",
                file=sys.stderr,
            )
            return None
