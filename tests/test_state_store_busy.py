#!/usr/bin/env python3
"""Busy-window contract for state_store.store.EventStore writes.

Contract under test:
1. A write that cannot get the SQLite write lock within the store's busy
   window raises ``StoreBusyError`` (a typed subclass of
   ``sqlite3.OperationalError``), not a raw lock error after an unbounded
   retry chain.
2. A write whose lock is released inside the busy window succeeds.
3. Six concurrent writer threads sharing one store never surface a lock error
   from the store, even with a tiny busy window (the in-process writers must
   serialize rather than race for the SQLite write lock).
"""
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from state_store.store import EventStore, StoreBusyError


def _hold_write_lock(db_path, held, release):
    """Hold the SQLite write lock from a second connection until ``release`` is set."""
    holder = sqlite3.connect(db_path, isolation_level=None, timeout=5.0)
    try:
        holder.execute("BEGIN IMMEDIATE")
        held.set()
        release.wait(timeout=30)
        holder.execute("ROLLBACK")
    finally:
        holder.close()


class TestStoreBusyContract(unittest.TestCase):
    """Writes against a locked database honour the busy window."""

    def test_blocked_write_raises_typed_busy_error_within_bound(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "busy.db")
            store = EventStore(db_path, busy_timeout_ms=100)
            held = threading.Event()
            release = threading.Event()
            holder = threading.Thread(target=_hold_write_lock, args=(db_path, held, release))
            holder.start()
            try:
                self.assertTrue(held.wait(timeout=5))
                started = time.monotonic()
                with self.assertRaises(StoreBusyError) as ctx:
                    store.append("tracker", "item_created", {"id": "x"})
                elapsed = time.monotonic() - started
            finally:
                release.set()
                holder.join(timeout=10)
                store.close()

            # Bounded: a few busy windows plus backoff, never the 30 s future timeout.
            self.assertLess(elapsed, 5.0)
            # Typed error stays catchable as the sqlite3 base class for old callers.
            self.assertIsInstance(ctx.exception, sqlite3.OperationalError)
            self.assertIn("database is locked", str(ctx.exception))

    def test_write_succeeds_when_lock_released_inside_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "busy.db")
            store = EventStore(db_path, busy_timeout_ms=2000)
            held = threading.Event()
            release = threading.Event()
            holder = threading.Thread(target=_hold_write_lock, args=(db_path, held, release))
            holder.start()
            try:
                self.assertTrue(held.wait(timeout=5))
                timer = threading.Timer(0.2, release.set)
                timer.start()
                version = store.append("tracker", "item_created", {"id": "x"})
                timer.cancel()
            finally:
                release.set()
                holder.join(timeout=10)
                store.close()

            self.assertEqual(version, 1)

    def test_six_writers_tiny_window_never_surface_lock_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "busy.db")
            store = EventStore(db_path, busy_timeout_ms=1)
            per_writer = 100
            errors = []

            def writer(idx):
                for n in range(per_writer):
                    try:
                        store.append("tracker", "item_created", {"w": idx, "n": n},
                                     actor=f"writer-{idx}")
                    except Exception as exc:  # collected, asserted below
                        errors.append(repr(exc))
                        return

            try:
                with ThreadPoolExecutor(max_workers=6) as executor:
                    futures = [executor.submit(writer, i) for i in range(6)]
                    for fut in futures:
                        fut.result(timeout=30)
                events = store.read("tracker")
            finally:
                store.close()

            self.assertEqual(errors, [])
            self.assertEqual(len(events), 6 * per_writer)
            self.assertEqual([e["version"] for e in events], list(range(1, 6 * per_writer + 1)))


if __name__ == "__main__":
    unittest.main()
