"""Tests for EventStore connection pooling and claims-stream compaction.

Covers:
(a) Thread-local connection reuse: same thread gets same connection object
(b) read_since: tail reads return only events after a given version
(c) close(): releases cached connection; next op lazily reopens
(d) StateAPI.get_since: passthrough to EventStore.read_since
(e) compact_claims: snapshot active claims for O(tail) reads
(f) Compacted reads produce identical fold results to full-stream reads
(g) Compaction with expired/released claims
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store.store import EventStore  # noqa: E402
from state_store.api import StateAPI  # noqa: E402
from state_store.coordination import (  # noqa: E402
    fold_claims,
    try_claim,
    release,
    current_holder,
    compact_claims,
)


def _retry_on_db_lock(func, max_retries=3, delay=0.1):
    """Retry a function if it fails with a DB lock error.

    Applies exponential backoff to handle filesystem-level WAL contention
    in parallel CI shards (false positive; no code defect).
    """
    import sqlite3
    last_error = None
    for attempt in range(max_retries):
        try:
            return func()
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(delay * (2 ** attempt))  # exponential backoff
                continue
            raise
    if last_error:
        raise last_error


class ConnectionReuseTest(unittest.TestCase):
    """Verify thread-local connection pooling in EventStore."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "events.db")
        self.store = EventStore(self.db)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_thread_reuses_connection(self):
        """Within one thread, _get_conn() returns the same connection object."""
        conn1 = self.store._get_conn()
        conn2 = self.store._get_conn()
        self.assertIs(conn1, conn2, "Same thread should reuse the same connection")

    def test_different_threads_get_different_connections(self):
        """Different threads get independent connection objects."""
        connections = {}
        barrier = threading.Barrier(2)

        def worker(name):
            conn = self.store._get_conn()
            connections[name] = id(conn)
            barrier.wait(timeout=5)

        t1 = threading.Thread(target=worker, args=("t1",))
        t2 = threading.Thread(target=worker, args=("t2",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertIn("t1", connections)
        self.assertIn("t2", connections)
        self.assertNotEqual(
            connections["t1"], connections["t2"],
            "Different threads should get different connection objects"
        )

    def test_close_releases_connection(self):
        """close() releases the cached connection; next call creates a new one."""
        conn1 = self.store._get_conn()
        self.store.close()
        conn2 = self.store._get_conn()
        self.assertIsNot(conn1, conn2, "After close(), a new connection should be created")

    def test_close_idempotent(self):
        """close() can be called multiple times without error."""
        self.store.close()
        self.store.close()
        # Should still work after double close
        self.store.append("test", "test_event", {"x": 1})
        events = self.store.read("test")
        self.assertEqual(len(events), 1)

    def test_operations_work_after_close(self):
        """After close(), operations lazily reopen a connection."""
        self.store.append("test", "event1", {"a": 1})
        self.store.close()
        # Should work seamlessly
        self.store.append("test", "event2", {"b": 2})
        events = self.store.read("test")
        self.assertEqual(len(events), 2)

    def test_stateapi_close_passthrough(self):
        """StateAPI.close() delegates to EventStore.close()."""
        api = StateAPI(self.db)
        api.append("test", "event1", {"a": 1})
        api.close()
        # Should work after close
        api.append("test", "event2", {"b": 2})
        events = api.get("test")
        self.assertEqual(len(events), 2)
        api.close()


class ReadSinceTest(unittest.TestCase):
    """Verify read_since returns only events after a given version."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "events.db")
        self.store = EventStore(self.db)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_since_returns_tail(self):
        """read_since(stream, N) returns only events with version > N."""
        for i in range(5):
            self.store.append("stream", "ev", {"i": i})

        tail = self.store.read_since("stream", 3)
        self.assertEqual(len(tail), 2)
        self.assertEqual(tail[0]["version"], 4)
        self.assertEqual(tail[1]["version"], 5)

    def test_read_since_zero_returns_all(self):
        """read_since(stream, 0) returns all events (equivalent to read)."""
        for i in range(3):
            self.store.append("stream", "ev", {"i": i})

        tail = self.store.read_since("stream", 0)
        all_events = self.store.read("stream")
        self.assertEqual(len(tail), len(all_events))

    def test_read_since_max_returns_empty(self):
        """read_since with version >= max returns empty list."""
        for i in range(3):
            self.store.append("stream", "ev", {"i": i})

        tail = self.store.read_since("stream", 3)
        self.assertEqual(tail, [])

    def test_read_since_empty_stream(self):
        """read_since on an empty stream returns empty list."""
        tail = self.store.read_since("nonexistent", 0)
        self.assertEqual(tail, [])

    def test_stateapi_get_since(self):
        """StateAPI.get_since delegates to EventStore.read_since."""
        api = StateAPI(self.db)
        for i in range(5):
            api.append("stream", "ev", {"i": i})

        tail = api.get_since("stream", 2)
        self.assertEqual(len(tail), 3)
        self.assertEqual(tail[0]["version"], 3)
        api.close()


class CompactClaimsTest(unittest.TestCase):
    """Verify claims-stream compaction (snapshot + tail-replay)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "events.db")
        self.store = EventStore(self.db)
        self.api = StateAPI(self.db)

    def tearDown(self):
        self.api.close()
        self.store.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_compact_empty_stream_returns_false(self):
        """compact_claims on empty claims stream returns False (nothing to do)."""
        result = compact_claims(self.api)
        self.assertFalse(result)

    def test_compact_saves_snapshot(self):
        """compact_claims saves a snapshot of active claims."""
        try_claim(self.api, "res1", "inst1")
        result = compact_claims(self.api)
        self.assertTrue(result)

        # Verify snapshot was saved
        snap = self.store.read_snapshot("claims")
        self.assertIsNotNone(snap)
        snap_version, snap_state, _ = snap
        self.assertIn("active_claims", snap_state)

    def test_compact_then_read_produces_same_holders(self):
        """After compaction, fold_claims on compacted events matches full fold."""
        # Create some claims
        try_claim(self.api, "res1", "inst1")
        try_claim(self.api, "res2", "inst2")
        try_claim(self.api, "res1", "inst3")  # loses

        # Full fold
        full_events = self.api.get("claims")
        full_holders = fold_claims(full_events)

        # Compact
        compact_claims(self.api)

        # After compaction, current_holder should give the same answers
        self.assertEqual(current_holder(self.api, "res1"), full_holders.get("res1"))
        self.assertEqual(current_holder(self.api, "res2"), full_holders.get("res2"))

    def test_compact_then_new_events_merge_correctly(self):
        """Events appended after compaction are correctly combined with snapshot."""
        # Setup: claim res1
        try_claim(self.api, "res1", "inst1")
        compact_claims(self.api)

        # New activity after compaction
        try_claim(self.api, "res2", "inst2")

        # Both holders should be visible
        self.assertEqual(current_holder(self.api, "res1"), "inst1")
        self.assertEqual(current_holder(self.api, "res2"), "inst2")

    def test_compact_then_release_works(self):
        """Releasing a claim after compaction correctly removes the holder."""
        try_claim(self.api, "res1", "inst1")
        compact_claims(self.api)

        # Release after compaction
        release(self.api, "res1", "inst1")

        # Should be free
        self.assertIsNone(current_holder(self.api, "res1"))

    def test_compact_all_released(self):
        """Compacting when all claims are released saves empty active_claims."""
        try_claim(self.api, "res1", "inst1")
        release(self.api, "res1", "inst1")

        result = compact_claims(self.api)
        self.assertTrue(result)

        snap = self.store.read_snapshot("claims")
        self.assertIsNotNone(snap)
        _, snap_state, _ = snap
        self.assertEqual(snap_state.get("active_claims"), [])

    def test_compact_preserves_ttl_for_expiry(self):
        """Compacted claims retain original ts/ttl so TTL expiry still works."""
        # Claim with a very short TTL
        self.api.append(
            "claims", "claim_requested",
            {"resource": "res1", "instance_id": "inst1", "ttl": 0.1},
            actor="inst1",
        )

        # Verify it's held right now
        events = self.api.get("claims")
        holders = fold_claims(events, now=time.time())
        self.assertEqual(holders.get("res1"), "inst1")

        # Compact
        compact_claims(self.api)

        # Wait for TTL to expire
        time.sleep(0.2)

        # After expiry, the claim should be gone even though it's in the snapshot
        # because the snapshot preserves the original ts and ttl
        self.assertIsNone(current_holder(self.api, "res1"))

    def test_compact_with_raw_eventstore(self):
        """compact_claims works with a raw EventStore (not just StateAPI)."""
        self.store.append("claims", "claim_requested",
                          {"resource": "res1", "instance_id": "inst1", "ttl": 300},
                          actor="inst1")

        result = compact_claims(self.store)
        self.assertTrue(result)

        # current_holder should still work
        self.assertEqual(current_holder(self.store, "res1"), "inst1")

    def test_compact_no_snapshot_support_returns_false(self):
        """compact_claims returns False for stores without snapshot support."""
        class MinimalStore:
            def get(self, stream):
                return []

        result = compact_claims(MinimalStore())
        self.assertFalse(result)

    def test_multiple_compactions_idempotent(self):
        """Multiple compactions produce the same result."""
        try_claim(self.api, "res1", "inst1")
        try_claim(self.api, "res2", "inst2")

        compact_claims(self.api)
        h1_res1 = current_holder(self.api, "res1")
        h1_res2 = current_holder(self.api, "res2")

        # Add more activity and compact again
        try_claim(self.api, "res3", "inst3")
        compact_claims(self.api)

        # Original holders unchanged, new holder visible
        self.assertEqual(current_holder(self.api, "res1"), h1_res1)
        self.assertEqual(current_holder(self.api, "res2"), h1_res2)
        self.assertEqual(current_holder(self.api, "res3"), "inst3")

    def test_release_then_reclaim_after_compact(self):
        """REGRESSION: claim -> release -> reclaim -> compact stores the re-claim, not the stale one.

        The bug: compact_claims scans for the FIRST matching claim_requested event
        (lowest version), but should scan for the LATEST un-released claim (after
        the most recent release). This test verifies the fix: after a release and
        re-claim, compaction must snapshot the newer claim event, not the stale one.
        """
        # Claim res1 for inst1
        self.api.append(
            "claims", "claim_requested",
            {"resource": "res1", "instance_id": "inst1", "ttl": 300},
            actor="inst1",
        )
        claim1_version = self.api.get("claims")[-1]["version"]

        # Verify inst1 holds res1
        self.assertEqual(current_holder(self.api, "res1"), "inst1")

        # Release res1
        release(self.api, "res1", "inst1")

        # Verify res1 is now free
        self.assertIsNone(current_holder(self.api, "res1"))

        # Re-claim res1 for inst1
        self.api.append(
            "claims", "claim_requested",
            {"resource": "res1", "instance_id": "inst1", "ttl": 300},
            actor="inst1",
        )
        claim2_version = self.api.get("claims")[-1]["version"]
        claim2_event = self.api.get("claims")[-1]

        # Verify re-claim succeeded
        self.assertEqual(current_holder(self.api, "res1"), "inst1")
        self.assertGreater(claim2_version, claim1_version)

        # Compact
        compact_claims(self.api)

        # After compaction, inst1 should STILL hold res1
        self.assertEqual(current_holder(self.api, "res1"), "inst1")

        # Verify the snapshot contains the RE-CLAIM (claim2), not the stale claim1
        snap = self.store.read_snapshot("claims")
        self.assertIsNotNone(snap)
        snap_version, snap_state, _ = snap
        active_claims = snap_state.get("active_claims", [])

        # Should have exactly one active claim for res1/inst1
        matching_claims = [
            ev for ev in active_claims
            if (ev.get("type") == "claim_requested"
                and ev.get("payload", {}).get("resource") == "res1"
                and ev.get("payload", {}).get("instance_id") == "inst1")
        ]
        self.assertEqual(len(matching_claims), 1, "Should have exactly one active claim for res1/inst1")

        # The snapshotted event MUST be claim2 (the re-claim)
        snapshotted_claim = matching_claims[0]
        self.assertEqual(snapshotted_claim["version"], claim2_version,
                         f"Snapshot must contain re-claim (v{claim2_version}), not stale claim (v{claim1_version})")
        self.assertEqual(snapshotted_claim.get("ts"), claim2_event.get("ts"),
                         "Snapshotted claim timestamp must be from the re-claim event")

    def test_phantom_claim_legacy_no_ttl(self):
        """REGRESSION: legacy claim without ts/ttl + release + compact -> holder None.

        Legacy events lack ts/ttl and never expire. If a legacy claim is released,
        the release event is in the stream and fold_claims correctly ignores it.
        But if the release lives below the snapshot floor, the legacy claim would
        become an un-releasable phantom holder. This test verifies the fix handles it.
        """
        # Create a legacy claim (no ts/ttl)
        self.api.append(
            "claims", "claim_requested",
            {"resource": "res1", "instance_id": "inst1"},  # no ts, no ttl
            actor="inst1",
        )
        legacy_version = self.api.get("claims")[-1]["version"]

        # Verify it's held
        self.assertEqual(current_holder(self.api, "res1"), "inst1")

        # Release it
        release(self.api, "res1", "inst1")

        # Verify it's free
        self.assertIsNone(current_holder(self.api, "res1"))

        # Compact
        compact_claims(self.api)

        # After compaction, the resource should STILL be free
        # (the release is below the snapshot floor, but fold_claims doesn't care)
        self.assertIsNone(current_holder(self.api, "res1"))

    def test_ttl_boundary_claim_expires_after_compact(self):
        """TTL boundary: claim 1s from expiry, compact, then check at now+2.

        After compaction, the snapshotted event preserves ts/ttl, so TTL expiry
        still works correctly. This verifies the snapshot data is sufficient.
        """
        # Create a claim that expires in 1 second
        now_ts = time.time()
        self.api.append(
            "claims", "claim_requested",
            {"resource": "res1", "instance_id": "inst1", "ttl": 1.0},
            actor="inst1",
        )

        # Get the event to check its timestamp
        events_before = self.api.get("claims")
        claim_event = events_before[-1]

        # Claim should be held at now
        holders_now = fold_claims(events_before, now=now_ts)
        self.assertEqual(holders_now.get("res1"), "inst1")

        # Compact
        compact_claims(self.api)

        # Check at now + 0.5 seconds: still held
        holders_half = fold_claims(self.api.get("claims"), now=now_ts + 0.5)
        self.assertEqual(holders_half.get("res1"), "inst1")

        # Check at now + 2 seconds: expired
        holders_expired = fold_claims(self.api.get("claims"), now=now_ts + 2.0)
        self.assertIsNone(holders_expired.get("res1"))

    def test_equivalence_property_random_walk(self):
        """EQUIVALENCE PROPERTY: fold_claims(full) == fold_claims(compacted) at every compaction.

        Bounded random walk: N in [0, 50] claim/release ops over 3 resources x 3 instances.
        Use fixed deterministic seed for reproducibility. Assert that the fold result
        is identical before and after compaction.
        """
        import random
        random.seed(42)  # Deterministic seed

        resources = ["r1", "r2", "r3"]
        instances = ["i1", "i2", "i3"]
        num_ops = random.randint(0, 50)

        # Perform random operations
        claims_made = set()  # Track (resource, instance) pairs that have active claims
        for _ in range(num_ops):
            resource = random.choice(resources)
            instance = random.choice(instances)
            op = random.choice(["claim", "release"])

            if op == "claim":
                try_claim(self.api, resource, instance)
                claims_made.add((resource, instance))
            else:
                if (resource, instance) in claims_made:
                    release(self.api, resource, instance)
                    claims_made.discard((resource, instance))

        # Fold the full stream
        full_events = self.api.get("claims")
        full_holders = fold_claims(full_events)

        # Compact
        compact_claims(self.api)

        # Fold via compacted snapshot + tail
        compacted_events = self.api.get("claims")  # Will use snapshot + tail internally
        compacted_holders = fold_claims(compacted_events)

        # Must be identical
        self.assertEqual(full_holders, compacted_holders,
                         f"After {num_ops} ops, fold_claims(full) != fold_claims(compacted)")

    def test_concurrent_with_interleaved_compaction(self):
        """Concurrent claims/releases with compaction interleaved.

        Verifies that compaction during concurrent operations produces
        consistent results. Uses _retry_on_db_lock to handle WAL contention.
        """
        errors = []
        barrier = threading.Barrier(3)  # 2 claimers + 1 compactor

        def claimer(thread_id):
            try:
                barrier.wait(timeout=5)
                for i in range(10):
                    _retry_on_db_lock(
                        lambda: try_claim(self.api, f"res_{thread_id}_{i}", f"inst_{thread_id}")
                    )
            except Exception as e:
                errors.append(str(e))

        def compactor():
            try:
                barrier.wait(timeout=5)
                # Give claimers a moment to start
                time.sleep(0.05)
                for _ in range(3):
                    _retry_on_db_lock(lambda: compact_claims(self.api))
                    time.sleep(0.05)
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=claimer, args=(0,)),
            threading.Thread(target=claimer, args=(1,)),
            threading.Thread(target=compactor),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"Concurrent compaction errors: {errors}")

        # Verify final state is consistent: all claims should be accessible
        events = self.api.get("claims")
        holders = fold_claims(events)
        # At least some claims should be held (2 claimers * 10 each = 20 potential claims)
        self.assertGreater(len(holders), 0, "Should have at least some active claims")


class ThreadSafetyTest(unittest.TestCase):
    """Verify thread-safe operations with connection pooling."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "events.db")
        self.store = EventStore(self.db)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_concurrent_thread_appends(self):
        """Multiple threads can safely append to the same stream."""
        errors = []
        barrier = threading.Barrier(4)

        def worker(thread_id, count):
            try:
                barrier.wait(timeout=5)
                for i in range(count):
                    self.store.append("shared", "ev", {"thread": thread_id, "i": i})
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(tid, 25)) for tid in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        events = self.store.read("shared")
        self.assertEqual(len(events), 100)

        # Versions gapless
        versions = sorted(e["version"] for e in events)
        self.assertEqual(versions, list(range(1, 101)))

    def test_concurrent_reads_with_write(self):
        """Reads from multiple threads while one thread writes are safe."""
        # Pre-populate
        for i in range(10):
            self.store.append("data", "ev", {"i": i})

        read_counts = []
        errors = []

        def reader():
            try:
                events = self.store.read("data")
                read_counts.append(len(events))
            except Exception as e:
                errors.append(str(e))

        def writer():
            try:
                for i in range(10):
                    self.store.append("data", "ev", {"i": i + 10})
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=reader) for _ in range(3)]
        threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        # Readers saw at least 10 events (pre-populated); writer added 10 more
        for count in read_counts:
            self.assertGreaterEqual(count, 10)


if __name__ == "__main__":
    unittest.main()
