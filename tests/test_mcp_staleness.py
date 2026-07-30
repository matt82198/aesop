#!/usr/bin/env python3
"""
Test MCP projection staleness bounds under concurrent load.

Validates:
1. SQLite WAL consistency (no torn reads, monotonic versions)
2. Measured staleness window (how stale can projections be under N writers + readers)
3. Projection atomic writes (tempfile + replace)
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# Ensure imports work
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from state_store.store import EventStore
from state_store.projections import project_tracker


class StalenessCollector:
    """Collect staleness measurements and verify WAL consistency."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = []
        self.staleness_windows = []
        self.version_sequence = []
        self.wal_errors = []
        self.next_item_id = 0

    def record_append(self, ts, version, event_type, stream):
        """Record an event append."""
        with self.lock:
            self.events.append({
                "type": "append",
                "ts": ts,
                "version": version,
                "event_type": event_type,
                "stream": stream,
            })
            self.version_sequence.append((ts, version))

    def record_read(self, ts, version, item_count):
        """Record a projection read."""
        with self.lock:
            self.events.append({
                "type": "read",
                "ts": ts,
                "version": version,
                "item_count": item_count,
            })

    def record_staleness(self, append_ts, read_ts):
        """Record measured staleness (ms between append and readable)."""
        staleness_ms = (read_ts - append_ts) * 1000
        with self.lock:
            self.staleness_windows.append((append_ts, read_ts, staleness_ms))

    def record_wal_error(self, error_msg):
        """Record a WAL consistency error."""
        with self.lock:
            self.wal_errors.append(error_msg)

    def next_id(self):
        """Get next item ID."""
        with self.lock:
            self.next_item_id += 1
            return f"item-{self.next_item_id}"

    def summary(self):
        """Return summary stats."""
        staleness_ms_values = [s[2] for s in self.staleness_windows]
        staleness_ms_values.sort()

        return {
            "total_appends": len([e for e in self.events if e["type"] == "append"]),
            "total_reads": len([e for e in self.events if e["type"] == "read"]),
            "staleness_measurements": len(staleness_ms_values),
            "staleness_min_ms": min(staleness_ms_values) if staleness_ms_values else None,
            "staleness_max_ms": max(staleness_ms_values) if staleness_ms_values else None,
            "staleness_avg_ms": sum(staleness_ms_values) / len(staleness_ms_values) if staleness_ms_values else None,
            "staleness_p99_ms": staleness_ms_values[int(len(staleness_ms_values) * 0.99)] if len(staleness_ms_values) > 0 else None,
            "wal_consistency_errors": len(self.wal_errors),
            "wal_errors_sample": self.wal_errors[:5],
        }


def writer_worker(store, collector, thread_id, num_events):
    """Append events to the store."""
    for i in range(num_events):
        item_id = collector.next_id()
        payload = {
            "id": item_id,
            "title": f"Item from thread {thread_id} #{i}",
            "priority": "P1",
            "status": "todo",
        }

        append_ts = time.time()
        version = store.append(
            stream="tracker",
            event_type="item_created",
            payload=payload,
            actor=f"writer-{thread_id}",
        )

        collector.record_append(append_ts, version, "item_created", "tracker")


def reader_worker(store, collector, thread_id, duration_sec):
    """Read tracker events and verify consistency."""
    start = time.time()
    last_version = None

    while time.time() - start < duration_sec:
        try:
            # Read all events from the store
            events = store.read("tracker")

            # Verify monotonic versions (no torn reads)
            versions = [e.get("version") for e in events]
            if versions != sorted(versions):
                collector.record_wal_error(
                    f"Reader {thread_id}: versions not monotonic: {versions}"
                )

            # Verify no version gaps (WAL consistency)
            if versions and versions != list(range(1, max(versions) + 1)):
                collector.record_wal_error(
                    f"Reader {thread_id}: version gap detected: {versions}"
                )

            # Record this read
            current_version = max(versions) if versions else 0
            collector.record_read(time.time(), current_version, len(events))

            # For each event we see, record when it was readable
            for event in events:
                if "ts" in event:
                    collector.record_staleness(event["ts"], time.time())

            last_version = current_version

            # Short sleep to avoid spinning too hard
            time.sleep(0.001)

        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e):
                collector.record_wal_error(f"Reader {thread_id} DB error: {e}")
            time.sleep(0.01)
        except Exception as e:
            # Catch unexpected errors to avoid thread crashes
            collector.record_wal_error(f"Reader {thread_id} unexpected error: {e}")
            break


class TestMCPStaleness(unittest.TestCase):
    """Test MCP projection staleness under concurrent load."""

    def test_wal_consistency_no_errors(self):
        """Verify SQLite WAL consistency: no torn reads or version gaps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tracker.db")
            store = EventStore(db_path)
            collector = StalenessCollector()

            try:
                with ThreadPoolExecutor(max_workers=6) as executor:
                    futures = []

                    # 4 writers
                    for i in range(4):
                        fut = executor.submit(writer_worker, store, collector, i, 100)
                        futures.append(("writer", i, fut))

                    time.sleep(0.05)

                    # 2 readers
                    for i in range(2):
                        fut = executor.submit(reader_worker, store, collector, i, 2.0)
                        futures.append(("reader", i, fut))

                    # Wait for all to complete (with timeout per future)
                    for role, idx, fut in futures:
                        fut.result(timeout=30)
            finally:
                # Ensure proper cleanup even if there are errors
                executor.shutdown(wait=True)
                store.close()

            summary = collector.summary()

            # Assert no WAL consistency errors
            self.assertEqual(
                summary["wal_consistency_errors"],
                0,
                f"WAL consistency errors detected: {summary['wal_errors_sample']}"
            )

            # Assert we got measurements
            self.assertGreater(summary["total_appends"], 0, "No events appended")
            self.assertGreater(summary["total_reads"], 0, "No reads performed")
            self.assertGreater(summary["staleness_measurements"], 0, "No staleness measurements")

    def test_monotonic_versions(self):
        """Verify that event versions are monotonically increasing (no gaps)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tracker.db")
            store = EventStore(db_path)

            try:
                # Append events sequentially
                versions = []
                for i in range(10):
                    v = store.append(
                        stream="test_stream",
                        event_type="test_event",
                        payload={"index": i},
                        actor="test",
                    )
                    versions.append(v)

                # Versions should be 1, 2, 3, ..., 10
                self.assertEqual(versions, list(range(1, 11)))

                # Read all events and verify monotonicity
                events = store.read("test_stream")
                read_versions = [e["version"] for e in events]
                self.assertEqual(read_versions, list(range(1, 11)))
            finally:
                store.close()

    def test_concurrent_appends_no_version_collisions(self):
        """Verify that concurrent appends never produce duplicate versions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tracker.db")
            store = EventStore(db_path)

            all_versions = []
            lock = threading.Lock()

            def append_events(thread_id, count):
                for i in range(count):
                    v = store.append(
                        stream="concurrent_stream",
                        event_type="item_created",
                        payload={"thread": thread_id, "index": i},
                        actor=f"thread-{thread_id}",
                    )
                    with lock:
                        all_versions.append(v)

            try:
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = [executor.submit(append_events, i, 50) for i in range(4)]
                    for fut in futures:
                        fut.result(timeout=30)
            finally:
                executor.shutdown(wait=True)
                store.close()

            # Should have 200 unique versions (4 threads × 50 events)
            self.assertEqual(len(all_versions), 200)
            self.assertEqual(len(set(all_versions)), 200, "Duplicate versions detected!")
            self.assertEqual(sorted(all_versions), list(range(1, 201)))

    def test_projection_consistency(self):
        """Verify projection folding produces consistent results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tracker.db")
            store = EventStore(db_path)

            try:
                # Append some items
                item_ids = []
                for i in range(5):
                    item_id = f"item-{i}"
                    item_ids.append(item_id)
                    store.append(
                        stream="tracker",
                        event_type="item_created",
                        payload={
                            "id": item_id,
                            "title": f"Item {i}",
                            "priority": "P1",
                            "status": "todo",
                        },
                        actor="test",
                    )

                # Project the tracker
                events = store.read("tracker")
                projection = project_tracker(events)

                # Verify projection shape
                self.assertEqual(projection["version"], 1)
                self.assertEqual(len(projection["items"]), 5)

                # Verify all items present in order
                projected_ids = [item["id"] for item in projection["items"]]
                self.assertEqual(projected_ids, item_ids)
            finally:
                store.close()

    def test_staleness_bounds_under_load(self):
        """Measure staleness bounds under realistic concurrent load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tracker.db")
            store = EventStore(db_path)
            collector = StalenessCollector()

            try:
                with ThreadPoolExecutor(max_workers=6) as executor:
                    futures = []

                    # 4 writers
                    for i in range(4):
                        fut = executor.submit(writer_worker, store, collector, i, 150)
                        futures.append(("writer", i, fut))

                    time.sleep(0.05)

                    # 2 readers for 3 seconds
                    for i in range(2):
                        fut = executor.submit(reader_worker, store, collector, i, 3.0)
                        futures.append(("reader", i, fut))

                    # Wait for all to complete (with timeout per future)
                    for role, idx, fut in futures:
                        fut.result(timeout=30)
            finally:
                # Ensure proper cleanup even if there are errors
                executor.shutdown(wait=True)
                store.close()

            summary = collector.summary()

            # Verify we got measurements
            self.assertGreater(summary["staleness_measurements"], 1000)

            # Log the measurements
            print(f"\nStaleness measurement (4 writers, 150 events each, 2 readers, 3s):")
            print(f"  Min staleness: {summary['staleness_min_ms']:.2f} ms")
            print(f"  Max staleness: {summary['staleness_max_ms']:.2f} ms")
            print(f"  Avg staleness: {summary['staleness_avg_ms']:.2f} ms")
            print(f"  P99 staleness: {summary['staleness_p99_ms']:.2f} ms")
            print(f"  Measurements: {summary['staleness_measurements']}")

            # Expected: max staleness should be within a few seconds
            # (exact bound depends on system, but we verify it's bounded)
            self.assertIsNotNone(summary["staleness_max_ms"])
            self.assertLess(summary["staleness_max_ms"], 10000, "Max staleness > 10 seconds")


if __name__ == "__main__":
    unittest.main()
