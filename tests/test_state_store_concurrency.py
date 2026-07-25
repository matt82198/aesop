"""Tests for state_store multi-process concurrency — the core proof.

Spawns 2+ concurrent PROCESSES (not threads — processes are the risk level)
against ONE shared SQLite DB to prove:
  (a) No lost update / no corruption: all appends land, versions are gapless,
      payloads round-trip
  (b) Exactly one claim winner per resource under contention
  (c) Fail-closed on error: append failure does not falsely grant a claim

Uses multiprocessing (not threads) to test cross-process safety, which is
where the CI flake and real team-scale concurrency issues live.

Follows Linux-parity rules:
  - sys.executable for subprocess spawning
  - stdlib unittest (no pytest assumption)
  - Enforced timeouts on any subprocess
  - ASCII-only output
  - No hardcoded absolute timestamps
  - Isolated temp DB per test
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store import EventStore, StateAPI  # noqa: E402
from state_store.coordination import fold_claims, try_claim, release, current_holder  # noqa: E402
from state_store.identity import get_instance_id  # noqa: E402


def _worker_append_events(args):
    """Worker function for multiprocessing: append K events to a shared stream.

    Args:
        args: (db_path, stream, worker_id, num_events)

    Returns:
        dict with worker_id, appended versions, and any error message
    """
    db_path, stream, worker_id, num_events = args
    try:
        store = EventStore(db_path)
        versions = []
        for i in range(num_events):
            payload = {
                "worker_id": worker_id,
                "event_index": i,
                "timestamp": time.time(),
            }
            version = store.append(stream, "test_event", payload, actor=f"worker_{worker_id}")
            versions.append(version)
        return {"worker_id": worker_id, "versions": versions, "error": None}
    except Exception as e:
        return {"worker_id": worker_id, "versions": [], "error": str(e)}


def _worker_try_claim(args):
    """Worker function for multiprocessing: attempt to claim a resource.

    Args:
        args: (db_path, resource, instance_id)

    Returns:
        dict with instance_id, claim_success, and any error message
    """
    db_path, resource, instance_id = args
    try:
        store = StateAPI(db_path)
        success = try_claim(store, resource, instance_id)
        return {"instance_id": instance_id, "success": success, "error": None}
    except Exception as e:
        return {"instance_id": instance_id, "success": False, "error": str(e)}


class MultiProcessConcurrencyTest(unittest.TestCase):
    """Multi-process concurrency tests for state_store."""

    def setUp(self):
        """Create isolated temp DB for this test."""
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_concurrency.db")
        # Initialize DB
        EventStore(self.db_path)

    def tearDown(self):
        """Clean up temp directory."""
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_no_lost_update_across_processes(self):
        """Spawn N processes appending to shared stream; assert all land gaplessly."""
        num_workers = 4
        events_per_worker = 50
        stream = "test_stream"

        results = []
        with Pool(processes=num_workers) as pool:
            # Spawn all workers
            async_results = [
                pool.apply_async(_worker_append_events, ((self.db_path, stream, worker_id, events_per_worker),))
                for worker_id in range(num_workers)
            ]
            # Collect results with timeout
            for async_result in async_results:
                try:
                    result = async_result.get(timeout=30)
                    results.append(result)
                except Exception as e:
                    self.fail(f"Worker failed or timed out: {e}")

        # Verify all workers succeeded
        for result in results:
            self.assertIsNone(result["error"], f"Worker {result['worker_id']} error: {result['error']}")

        # Verify all events landed
        store = EventStore(self.db_path)
        events = store.read(stream)
        total_appended = num_workers * events_per_worker
        self.assertEqual(len(events), total_appended, f"Expected {total_appended} events, got {len(events)}")

        # Verify versions are gapless (1..total_appended)
        versions = sorted([e["version"] for e in events])
        expected_versions = list(range(1, total_appended + 1))
        self.assertEqual(versions, expected_versions, "Versions are not gapless or have duplicates")

        # Verify payloads round-trip (spot check)
        for event in events[:10]:  # Check first 10
            payload = event["payload"]
            self.assertIn("worker_id", payload)
            self.assertIn("event_index", payload)
            self.assertIn("timestamp", payload)

    def test_exactly_one_claim_winner(self):
        """Spawn N processes claiming same resource; assert exactly one wins."""
        num_claimants = 4
        resource = "shared_resource_1"

        # Create instances with unique ids
        instance_ids = [f"orchestrator_{i}" for i in range(num_claimants)]

        results = []
        with Pool(processes=num_claimants) as pool:
            async_results = [
                pool.apply_async(
                    _worker_try_claim,
                    ((self.db_path, resource, instance_id),),
                )
                for instance_id in instance_ids
            ]
            for async_result in async_results:
                try:
                    result = async_result.get(timeout=30)
                    results.append(result)
                except Exception as e:
                    self.fail(f"Claimant failed or timed out: {e}")

        # Exactly one should have success=True
        winners = [r for r in results if r["success"]]
        self.assertEqual(len(winners), 1, f"Expected 1 winner, got {len(winners)}")

        # Verify the winner is deterministic via fold_claims
        store = StateAPI(self.db_path)
        events = store.get("claims")
        claims = fold_claims(events)
        holder = claims.get(resource)

        # The holder should be the single winner
        self.assertEqual(holder, winners[0]["instance_id"])

        # All other claimants should agree on who won
        for result in results:
            if result["success"]:
                self.assertEqual(result["instance_id"], holder)

    def test_fail_closed_on_claim_error(self):
        """Verify try_claim returns False when append fails, never True."""
        resource = "test_resource"
        instance_id = "orchestrator_test"

        # Create a mock store that fails on append
        class FailingStore:
            def append(self, *args, **kwargs):
                raise sqlite3.OperationalError("database is locked")

            def get(self, stream):
                return []

        store = FailingStore()
        result = try_claim(store, resource, instance_id)

        # Must return False on any error
        self.assertFalse(result, "try_claim should fail-closed (return False) on append error")

    def test_release_idempotent(self):
        """Verify release is idempotent (can release same resource multiple times)."""
        resource = "test_resource_release"
        instance_id = "orchestrator_release_test"

        store = StateAPI(self.db_path)

        # First, claim the resource
        try_claim(store, resource, instance_id)

        # Release once
        release(store, resource, instance_id)

        # Verify released
        holder_after_first = current_holder(store, resource)
        self.assertNotEqual(holder_after_first, instance_id)

        # Release again (should be idempotent)
        release(store, resource, instance_id)

        # Verify still released
        holder_after_second = current_holder(store, resource)
        self.assertNotEqual(holder_after_second, instance_id)

    def test_current_holder_query(self):
        """Verify current_holder returns the correct holder or None."""
        store = StateAPI(self.db_path)

        resource = "holder_query_test"
        instance_1 = "orchestrator_1"
        instance_2 = "orchestrator_2"

        # Initially unclaimed
        self.assertIsNone(current_holder(store, resource))

        # Try claim with instance_1 (will win since it's first)
        success_1 = try_claim(store, resource, instance_1)
        self.assertTrue(success_1)

        # Current holder should be instance_1
        self.assertEqual(current_holder(store, resource), instance_1)

        # Try claim with instance_2 (should fail, instance_1 already won)
        success_2 = try_claim(store, resource, instance_2)
        self.assertFalse(success_2)

        # Holder should still be instance_1
        self.assertEqual(current_holder(store, resource), instance_1)

        # Release from instance_1
        release(store, resource, instance_1)

        # Now resource should be free (instance_2's losing claim was retracted).
        # The stale-claim resurrection guard ensures only un-retracted claims matter.
        self.assertIsNone(current_holder(store, resource),
                          "Resource should be free; instance_2's losing claim was retracted")

        # Now instance_2 can reclaim
        success_2_reclaim = try_claim(store, resource, instance_2)
        self.assertTrue(success_2_reclaim, "instance_2 should win a fresh claim on free resource")
        self.assertEqual(current_holder(store, resource), instance_2)

        # Release from instance_2
        release(store, resource, instance_2)

        # Now should be truly unclaimed
        self.assertIsNone(current_holder(store, resource))

    def test_multiple_disjoint_resources(self):
        """Verify each resource has independent claims."""
        store = StateAPI(self.db_path)

        resource_1 = "resource_1"
        resource_2 = "resource_2"
        instance_1 = "orchestrator_1"
        instance_2 = "orchestrator_2"

        # instance_1 claims resource_1
        success_1a = try_claim(store, resource_1, instance_1)
        self.assertTrue(success_1a)

        # instance_2 claims resource_2
        success_2b = try_claim(store, resource_2, instance_2)
        self.assertTrue(success_2b)

        # Verify each holds their resource
        self.assertEqual(current_holder(store, resource_1), instance_1)
        self.assertEqual(current_holder(store, resource_2), instance_2)

        # instance_1 tries to claim resource_2 (should fail)
        success_1b = try_claim(store, resource_2, instance_1)
        self.assertFalse(success_1b)

        # Claims should be unchanged
        self.assertEqual(current_holder(store, resource_1), instance_1)
        self.assertEqual(current_holder(store, resource_2), instance_2)

    def test_same_instance_retry_interleaved(self):
        """Test BUG 1 fix: same-instance retry with interleaved claim from another instance.

        Scenario:
          - A claims resource (wins, has version 1)
          - B claims resource (loses, has version 2)
          - A claims resource again/retries (version 3)

        Expected:
          - A's first claim (version 1) remains active; later claims from A are ignored
          - Minimum version per instance determines the winner
          - A remains sole holder, B is never holder
        """
        store = StateAPI(self.db_path)
        resource = "retry_test_resource"
        instance_a = "orchestrator_a"
        instance_b = "orchestrator_b"

        # A claims (version 1, will win)
        success_a1 = try_claim(store, resource, instance_a)
        self.assertTrue(success_a1, "A should win first claim")

        # B claims (version 2, will lose)
        success_b = try_claim(store, resource, instance_b)
        self.assertFalse(success_b, "B should lose (A already claimed)")

        # A claims again/retries (version 3, but A's minimum is still 1)
        success_a2 = try_claim(store, resource, instance_a)
        self.assertTrue(success_a2, "A should still hold via minimum version")

        # Verify A is sole holder
        holder = current_holder(store, resource)
        self.assertEqual(holder, instance_a, "A should remain sole holder")

        # Verify the fold correctly identifies A's minimum version
        events = store.get("claims")
        claims = fold_claims(events)
        self.assertEqual(claims.get(resource), instance_a)

    def test_release_and_reclaim_correctness(self):
        """Test proper holder tracking through release and reclaim cycles.

        Scenario:
          - A claims (wins)
          - B claims (loses and retracts)
          - A releases
          - Resource is free (no stale-resurrection of B's claim)
          - A reclaims and holds again

        Expected:
          - At each step, holder is correct
          - B never becomes holder spuriously (its claim was retracted)
          - After A's release, resource is free
          - After A's reclaim, A is again holder
        """
        store = StateAPI(self.db_path)
        resource = "reclaim_test_resource"
        instance_a = "orchestrator_a"
        instance_b = "orchestrator_b"

        # A claims (wins)
        success_a1 = try_claim(store, resource, instance_a)
        self.assertTrue(success_a1)
        self.assertEqual(current_holder(store, resource), instance_a)

        # B claims (loses and retracts its claim)
        success_b1 = try_claim(store, resource, instance_b)
        self.assertFalse(success_b1)
        self.assertEqual(current_holder(store, resource), instance_a,
                         "A should still hold after B's failed claim")

        # A releases
        release(store, resource, instance_a)
        holder_after_release = current_holder(store, resource)
        # After A's release, resource should be free (B's claim was retracted when it lost)
        self.assertIsNone(holder_after_release,
                          "Resource should be free; B's losing claim was retracted")

        # A reclaims
        success_a2 = try_claim(store, resource, instance_a)
        # A's reclaim succeeds (resource is free, A's new claim wins)
        self.assertTrue(success_a2,
                        "A's reclaim should succeed when resource is free")

        # A is again holder
        self.assertEqual(current_holder(store, resource), instance_a)

    def test_stale_claim_resurrection_guard(self):
        """Test BUG 2 fix: losing claim is retracted, preventing resurrection.

        Scenario:
          - A claims (wins, version 1)
          - B tries to claim (loses, version 2)
          - A releases (should leave resource FREE)

        Expected:
          - After A's release, B does NOT become holder
          - Resource is truly free (current_holder returns None)
          - A fresh claim by any instance succeeds
        """
        store = StateAPI(self.db_path)
        resource = "stale_resurrection_test"
        instance_a = "orchestrator_a"
        instance_b = "orchestrator_b"
        instance_c = "orchestrator_c"

        # A claims (version 1, wins)
        success_a = try_claim(store, resource, instance_a)
        self.assertTrue(success_a)
        self.assertEqual(current_holder(store, resource), instance_a)

        # B tries to claim (version 2, loses)
        success_b = try_claim(store, resource, instance_b)
        self.assertFalse(success_b, "B should lose")

        # Verify B's losing claim is retracted (it appended claim_released)
        events = store.get("claims")
        b_released = any(
            ev.get("type") == "claim_released"
            and ev.get("payload", {}).get("instance_id") == instance_b
            for ev in events
        )
        self.assertTrue(b_released, "B's claim should be retracted (claim_released appended)")

        # A releases
        release(store, resource, instance_a)

        # Resource should be FREE (no holder), NOT held by B
        holder_after_a_release = current_holder(store, resource)
        self.assertIsNone(holder_after_a_release,
                          "Resource should be free after A releases; B should NOT resurrect")

        # Fresh claim by C succeeds
        success_c = try_claim(store, resource, instance_c)
        self.assertTrue(success_c, "Fresh claim by C should succeed when resource is free")
        self.assertEqual(current_holder(store, resource), instance_c)

    def test_full_release_enables_fresh_claim(self):
        """Test that after full release, resource can be re-claimed and yields one winner.

        Scenario:
          - A claims and releases
          - Multiple instances attempt to claim
          - Exactly one should win

        Expected:
          - Resource is truly free after full release
          - Fresh claim produces exactly one winner
          - Winner is deterministic (lowest version append)
        """
        store = StateAPI(self.db_path)
        resource = "fresh_claim_test"
        instance_a = "orchestrator_a"

        # A claims and releases
        try_claim(store, resource, instance_a)
        release(store, resource, instance_a)

        # Verify resource is free
        self.assertIsNone(current_holder(store, resource))

        # Multiple fresh claims
        instance_b = "orchestrator_b"
        instance_c = "orchestrator_c"
        instance_d = "orchestrator_d"

        # All claim at roughly the same time (in-process sequential, but versions reflect order)
        success_b = try_claim(store, resource, instance_b)
        success_c = try_claim(store, resource, instance_c)
        success_d = try_claim(store, resource, instance_d)

        # Exactly one should win
        winners = sum([success_b, success_c, success_d])
        self.assertEqual(winners, 1, f"Expected 1 winner from fresh claims, got {winners}")

        # Identify winner and verify it's the one that succeeded
        holder = current_holder(store, resource)
        if success_b:
            self.assertEqual(holder, instance_b)
        elif success_c:
            self.assertEqual(holder, instance_c)
        elif success_d:
            self.assertEqual(holder, instance_d)


class RetractOnErrorTest(unittest.TestCase):
    """RS5 F2: a failed claim attempt must never strand a phantom holder.

    try_claim's outer except previously returned False WITHOUT retracting the
    claim_requested it had already appended (the retract only ran on the
    fold-says-we-lost path). The stranded claim became the winner as soon as
    the true holder released, blocking the resource for a full TTL while the
    'holder' believed it had failed."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_retract.db")
        EventStore(self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_read_failure_does_not_strand_phantom_holder(self):
        """try_claim whose post-append read raises (SQLite lock retries
        exhausted) must retract its own claim before returning False: after
        the true holder releases, the resource is immediately claimable."""
        store = EventStore(self.db_path)
        self.assertTrue(try_claim(store, "item-y", "inst-A", ttl=300))

        class ReadFailsStore:
            """Append succeeds; the re-read raises -> outer except path."""

            def __init__(self, inner):
                self.inner = inner

            def append(self, *args, **kwargs):
                return self.inner.append(*args, **kwargs)

            def read(self, stream):
                raise RuntimeError("database is locked (retries exhausted)")

        self.assertFalse(
            try_claim(ReadFailsStore(store), "item-y", "inst-B", ttl=300),
            "claim attempt with a failing read must fail closed",
        )

        # A finishes cleanly; B's claim must NOT be left as the next holder.
        release(store, "item-y", "inst-A")
        self.assertIsNone(
            current_holder(store, "item-y"),
            "phantom holder stranded: B's un-retracted claim won after A "
            "released (resource dead until B's TTL expires)",
        )
        self.assertTrue(
            try_claim(store, "item-y", "inst-C", ttl=60),
            "resource not claimable after the true holder released",
        )

    def test_append_failure_still_fail_closed_and_holder_unchanged(self):
        """If the initial append itself raises there is nothing to retract:
        fail closed, and the true holder is untouched."""
        store = EventStore(self.db_path)
        self.assertTrue(try_claim(store, "item-z", "inst-A", ttl=300))

        class AppendFailsStore:
            def append(self, *args, **kwargs):
                raise RuntimeError("disk full")

            def read(self, stream):
                return store.read(stream)

        self.assertFalse(
            try_claim(AppendFailsStore(), "item-z", "inst-B", ttl=300)
        )
        self.assertEqual(current_holder(store, "item-z"), "inst-A")


if __name__ == "__main__":
    unittest.main()
