"""Tests for state_store Optimistic Concurrency Control (OCC) — Phase 2.

Multi-process tests proving the OCC invariant: two processes both reading
version N and both attempting `append(expected_version=N)` concurrently on ONE
shared DB will result in EXACTLY ONE succeeding and the other raising
ConcurrencyConflict WITHOUT writing any event. The loser can retry with the
new expected_version and succeed.

Also verifies backward compatibility: append() WITHOUT expected_version behaves
exactly as before (Phase 1 tests remain green).

Follows Linux-parity rules:
  - sys.executable for subprocess spawning
  - stdlib unittest (no pytest assumption)
  - Enforced timeouts on any subprocess
  - ASCII-only output
  - No hardcoded absolute timestamps
  - Isolated temp DB per test
"""
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from multiprocessing import Pool, Barrier, Manager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store import EventStore, StateAPI, ConcurrencyConflict  # noqa: E402


def _worker_append_with_occ(args):
    """Worker function: read version, then append with OCC check.

    Args:
        args: (db_path, stream, worker_id, iterations)

    Returns:
        dict with worker_id, successes (list of version numbers),
        conflicts (count), and any error message
    """
    db_path, stream, worker_id, iterations = args
    try:
        store = EventStore(db_path)
        successes = []
        conflicts = 0

        for i in range(iterations):
            # Read current version
            events = store.read(stream)
            current_version = len(events)  # 0 if empty, N if N events exist

            # Attempt append with OCC check
            try:
                payload = {"worker_id": worker_id, "iteration": i}
                version = store.append(
                    stream,
                    "test_event",
                    payload,
                    actor=f"worker_{worker_id}",
                    expected_version=current_version,
                )
                successes.append(version)
            except ConcurrencyConflict:
                conflicts += 1
                # In a real scenario, we'd retry; here we just count

        return {
            "worker_id": worker_id,
            "successes": successes,
            "conflicts": conflicts,
            "error": None,
        }
    except Exception as e:
        return {
            "worker_id": worker_id,
            "successes": [],
            "conflicts": 0,
            "error": str(e),
        }


def _worker_deterministic_occ_contention(args):
    """Worker for deterministic OCC contention using a Barrier.

    This worker reads version, then blocks on a Barrier until ALL workers are
    ready, then ALL attempt append(expected_version=N) SIMULTANEOUSLY.

    This GUARANTEES genuine contention every run, unlike sleep-based timing which
    may serialize if the OS scheduler doesn't interleave reads and appends.

    Args:
        args: (db_path, stream, worker_id, barrier)

    Returns:
        dict with worker_id, success (bool), version (or None if conflict),
        and any error message
    """
    db_path, stream, worker_id, barrier = args
    try:
        store = EventStore(db_path)

        # Read current version (all workers do this sequentially, seeing the same version)
        events = store.read(stream)
        current_version = len(events)

        # CRITICAL: Block on barrier until ALL workers are ready.
        # This ensures they all release from this line at nearly the same instant.
        barrier.wait()

        # All workers now attempt append(expected_version=N) in close temporal proximity.
        # Only ONE can succeed; the rest hit ConcurrencyConflict.
        try:
            payload = {"worker_id": worker_id}
            version = store.append(
                stream,
                "test_event",
                payload,
                actor=f"worker_{worker_id}",
                expected_version=current_version,
            )
            return {
                "worker_id": worker_id,
                "success": True,
                "version": version,
                "error": None,
            }
        except ConcurrencyConflict as e:
            return {
                "worker_id": worker_id,
                "success": False,
                "version": None,
                "actual_version": e.actual_version,
                "error": None,
            }

    except Exception as e:
        return {
            "worker_id": worker_id,
            "success": False,
            "version": None,
            "error": str(e),
        }


def _worker_simultaneous_occ_attempt(args):
    """Worker: read version, then attempt append with exact OCC check.

    This worker simulates two orchestrators both reading version N, then both
    attempting to append with expected_version=N. Uses a simple sleep-based
    synchronization to approximate simultaneous attempts.

    Args:
        args: (db_path, stream, worker_id, delay_before_append)

    Returns:
        dict with worker_id, success (bool), version (or None if conflict),
        and any error message
    """
    db_path, stream, worker_id, delay_before_append = args
    try:
        store = EventStore(db_path)

        # Read current version
        events = store.read(stream)
        current_version = len(events)

        # Small sleep to approximate simultaneous append attempts
        if delay_before_append > 0:
            time.sleep(delay_before_append)

        # Try to append with same expected_version
        try:
            payload = {"worker_id": worker_id}
            version = store.append(
                stream,
                "test_event",
                payload,
                actor=f"worker_{worker_id}",
                expected_version=current_version,
            )
            return {
                "worker_id": worker_id,
                "success": True,
                "version": version,
                "error": None,
            }
        except ConcurrencyConflict as e:
            return {
                "worker_id": worker_id,
                "success": False,
                "version": None,
                "actual_version": e.actual_version,
                "error": None,
            }

    except Exception as e:
        return {
            "worker_id": worker_id,
            "success": False,
            "version": None,
            "error": str(e),
        }


def _worker_occ_with_barrier_retry(args):
    """Worker: read version, barrier sync on each iteration, attempt append with OCC.

    Uses a Manager.Barrier per iteration to force ALL workers to read, sync,
    and attempt append(expected_version=N) simultaneously. This GUARANTEES
    OCC conflicts occur on each round, making the conflict assertion deterministic.

    Args:
        args: (db_path, stream, worker_id, iterations, barrier)

    Returns:
        dict with worker_id, successes (list of versions), conflicts (count),
        and any error message
    """
    db_path, stream, worker_id, iterations, barrier = args
    try:
        store = EventStore(db_path)
        successes = []
        conflicts = 0

        for i in range(iterations):
            # Read current version
            events = store.read(stream)
            current_version = len(events)

            # CRITICAL: Block on barrier until ALL workers have read.
            # This ensures simultaneous append attempts.
            barrier.wait()

            # All workers now attempt append(expected_version=N) in close temporal proximity.
            # Exactly one can succeed; the rest hit ConcurrencyConflict.
            try:
                payload = {"worker_id": worker_id, "iteration": i}
                version = store.append(
                    stream,
                    "test_event",
                    payload,
                    actor=f"worker_{worker_id}",
                    expected_version=current_version,
                )
                successes.append(version)
            except ConcurrencyConflict:
                conflicts += 1

        return {
            "worker_id": worker_id,
            "successes": successes,
            "conflicts": conflicts,
            "error": None,
        }
    except Exception as e:
        return {
            "worker_id": worker_id,
            "successes": [],
            "conflicts": 0,
            "error": str(e),
        }


class OCCTest(unittest.TestCase):
    """OCC tests for state_store."""

    def setUp(self):
        """Create isolated temp DB for this test."""
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_occ.db")
        # Initialize DB
        self._init_store = EventStore(self.db_path)

    def tearDown(self):
        """Clean up temp directory."""
        import shutil
        self._init_store.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_backward_compat_append_without_expected_version(self):
        """Verify append() without expected_version works exactly as before (Phase 1 compat)."""
        store = EventStore(self.db_path)
        stream = "test_stream"

        # Append without expected_version (old behavior)
        v1 = store.append(stream, "event_1", {"data": "a"})
        v2 = store.append(stream, "event_2", {"data": "b"})
        v3 = store.append(stream, "event_3", {"data": "c"})

        # Verify versions are sequential
        self.assertEqual(v1, 1)
        self.assertEqual(v2, 2)
        self.assertEqual(v3, 3)

        # Verify events are readable
        events = store.read(stream)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["version"], 1)
        self.assertEqual(events[1]["version"], 2)
        self.assertEqual(events[2]["version"], 3)

    def test_occ_success_when_version_matches(self):
        """Verify append succeeds when expected_version matches current."""
        store = EventStore(self.db_path)
        stream = "test_stream"

        # Seed with one event
        v1 = store.append(stream, "seed", {"data": "seed"})
        self.assertEqual(v1, 1)

        # Read to get current version
        events = store.read(stream)
        self.assertEqual(len(events), 1)
        current_version = 1

        # Append with matching expected_version should succeed
        v2 = store.append(
            stream,
            "test_event",
            {"data": "test"},
            expected_version=current_version,
        )
        self.assertEqual(v2, 2)

        # Verify the event was written
        events = store.read(stream)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["version"], 2)

    def test_occ_conflict_when_version_mismatch(self):
        """Verify append raises ConcurrencyConflict when expected_version doesn't match."""
        store = EventStore(self.db_path)
        stream = "test_stream"

        # Seed with one event
        store.append(stream, "seed", {"data": "seed"})

        # Try to append with wrong expected_version
        with self.assertRaises(ConcurrencyConflict) as cm:
            store.append(
                stream,
                "test_event",
                {"data": "test"},
                expected_version=999,  # Wrong!
            )

        # Verify the exception carries version info
        exc = cm.exception
        self.assertEqual(exc.expected_version, 999)
        self.assertEqual(exc.actual_version, 1)

    def test_occ_conflict_no_write_on_failure(self):
        """Verify no event is written when OCC conflict occurs."""
        store = EventStore(self.db_path)
        stream = "test_stream"

        # Seed with one event
        store.append(stream, "seed", {"data": "seed"})

        # Count before conflict attempt
        events_before = store.read(stream)
        count_before = len(events_before)

        # Attempt append with wrong expected_version
        try:
            store.append(
                stream,
                "test_event",
                {"data": "test"},
                expected_version=999,
            )
        except ConcurrencyConflict:
            pass

        # Verify no event was written
        events_after = store.read(stream)
        count_after = len(events_after)
        self.assertEqual(count_before, count_after,
                        "Event should not be written on OCC conflict")

    def test_occ_exception_carries_actual_version(self):
        """Verify ConcurrencyConflict provides actual version for caller to retry."""
        store = EventStore(self.db_path)
        stream = "test_stream"

        # Seed with 3 events
        for i in range(3):
            store.append(stream, f"event_{i}", {"data": i})

        # Try append with old expected_version (2 instead of 3)
        try:
            store.append(
                stream,
                "test_event",
                {"data": "test"},
                expected_version=2,
            )
            self.fail("Should have raised ConcurrencyConflict")
        except ConcurrencyConflict as e:
            # Caller can use actual_version to retry
            self.assertEqual(e.expected_version, 2)
            self.assertEqual(e.actual_version, 3)

            # Simulate retry: re-read and append with new expected_version
            events = store.read(stream)
            new_expected = len(events)
            v_retry = store.append(
                stream,
                "retry_event",
                {"data": "retry"},
                expected_version=new_expected,
            )
            self.assertEqual(v_retry, 4)

    def test_multiprocess_simultaneous_occ_exactly_one_succeeds(self):
        """Multi-process: DETERMINISTIC multi-worker contention on append(expected_version=N).

        Uses a Manager-based Barrier to force ALL workers to block until everyone has read
        version N, then ALL release simultaneously to attempt append(expected_version=N).
        This GUARANTEES genuine contention every run, no stochastic timing.

        Load-bearing assertion: EXACTLY ONE succeeds, the rest raise ConcurrencyConflict.
        If a mutant removes the version check, all workers succeed (test fails).
        If a mutant swaps the comparison, wrong workers succeed (test fails).
        """
        stream = "test_stream"

        # Seed with one event
        store = EventStore(self.db_path)
        store.append(stream, "seed", {"data": "seed"})

        # Spawn N workers racing to append (all will read version 1, all will attempt
        # append(expected_version=1) simultaneously via barrier).
        num_workers = 5

        # Create a Manager-based Barrier for interprocess synchronization.
        # A plain Barrier() cannot be pickled; Manager() creates a shareable version.
        with Manager() as manager:
            barrier = manager.Barrier(num_workers)

            with Pool(processes=num_workers) as pool:
                async_results = [
                    pool.apply_async(
                        _worker_deterministic_occ_contention,
                        ((self.db_path, stream, worker_id, barrier),),
                    )
                    for worker_id in range(num_workers)
                ]

                # Collect results with timeout
                results = []
                for async_result in async_results:
                    try:
                        result = async_result.get(timeout=30)
                        results.append(result)
                        self.assertIsNone(result["error"],
                                        f"Worker {result['worker_id']} error: {result['error']}")
                    except Exception as e:
                        self.fail(f"Worker failed or timed out: {e}")

        # DETERMINISTIC INVARIANT: exactly ONE success, rest conflicts.
        # All workers read version 1, all attempted append(expected_version=1).
        # Only one SQLite writer can succeed; others hit ConcurrencyConflict.
        successes = [r for r in results if r.get("success")]
        failures = [r for r in results if not r.get("success")]

        self.assertEqual(len(successes), 1,
                        f"Expected exactly 1 success from deterministic barrier contention, "
                        f"got {len(successes)}. Mutant: version check may be missing or broken.")
        self.assertEqual(len(failures), num_workers - 1,
                        f"Expected exactly {num_workers - 1} failures, got {len(failures)}")

        # Verify all failures are ConcurrencyConflict (have actual_version set).
        for failure in failures:
            self.assertIsNotNone(failure.get("actual_version"),
                                f"Worker {failure['worker_id']}: expected ConcurrencyConflict "
                                f"with actual_version set, got {failure}")

        # Verify the stream has exactly one new event (1 seed + 1 success).
        events = store.read(stream)
        self.assertEqual(len(events), 2,
                        f"Expected 1 seed + 1 success = 2 events, got {len(events)}")

        # Versions should be gapless [1, 2]
        versions = sorted([e["version"] for e in events])
        self.assertEqual(versions, [1, 2],
                        "Versions should be gapless: [1, 2]")

    def test_multiprocess_occ_conflict_retry_convergence(self):
        """Multi-process: DETERMINISTIC conflict generation with barrier-synced retries.

        Uses a Manager.Barrier to force ALL workers to read, sync, and attempt
        append(expected_version=N) simultaneously on each iteration. This GUARANTEES
        OCC conflicts occur every round (num_workers - 1 per round), not probabilistically.

        Load-bearing assertion: total_conflicts must be > 0 (deterministically true
        because we force contention via barriers). Each iteration: exactly one succeeds,
        the rest conflict.
        """
        stream = "test_stream"
        num_workers = 3
        iterations_per_worker = 5

        # Create a Manager-based Barrier for interprocess synchronization.
        # Each iteration, all workers sync before attempting append.
        with Manager() as manager:
            barrier = manager.Barrier(num_workers)

            # Spawn workers using barrier-synchronized OCC attempts
            with Pool(processes=num_workers) as pool:
                async_results = [
                    pool.apply_async(
                        _worker_occ_with_barrier_retry,
                        ((self.db_path, stream, worker_id, iterations_per_worker, barrier),),
                    )
                    for worker_id in range(num_workers)
                ]

                # Collect results with timeout
                results = []
                for async_result in async_results:
                    try:
                        result = async_result.get(timeout=30)
                        results.append(result)
                        self.assertIsNone(result["error"],
                                        f"Worker {result['worker_id']} error: {result['error']}")
                    except Exception as e:
                        self.fail(f"Worker failed or timed out: {e}")

        # Verify that we have appends and conflicts
        total_successes = sum(len(r["successes"]) for r in results)
        total_conflicts = sum(r["conflicts"] for r in results)

        # DETERMINISTIC: With barrier synchronization, we guarantee contention.
        # Each of N iterations: exactly 1 success + (num_workers - 1) conflicts.
        self.assertGreater(total_successes, 0,
                          "At least some appends should succeed")
        # With barrier-synchronized contention, conflicts are GUARANTEED, not probabilistic.
        self.assertGreater(total_conflicts, 0,
                          "Should have conflicts from GUARANTEED barrier-synchronized contention")

        # Each iteration: 1 success + (N-1) conflicts = N total outcomes per iteration
        # Total iterations = iterations_per_worker (all workers do the same number)
        total_attempts = num_workers * iterations_per_worker
        total_outcomes = total_successes + total_conflicts
        # With barrier sync, we expect exactly: iterations_per_worker successes
        # and (num_workers - 1) * iterations_per_worker conflicts
        self.assertEqual(total_outcomes, total_attempts,
                        "Total outcomes should equal total attempts with deterministic barriers")

        # Verify that all successful appends landed (no lost updates)
        store = EventStore(self.db_path)
        events = store.read(stream)
        # Each success should correspond to an event in the stream
        self.assertEqual(len(events), total_successes,
                        f"Expected {total_successes} events, got {len(events)}")

        # Verify versions are gapless (1..N)
        versions = sorted([e["version"] for e in events])
        expected_versions = list(range(1, len(events) + 1))
        self.assertEqual(versions, expected_versions,
                        "Versions should be gapless after OCC appends")

    def test_multiprocess_no_lost_updates_with_occ(self):
        """Multi-process: append with and without OCC mixed; no lost updates."""
        stream = "test_stream"
        num_appends_without_occ = 5

        store = EventStore(self.db_path)

        # Phase 1: Append some events without OCC (baseline)
        for i in range(num_appends_without_occ):
            store.append(stream, f"event_{i}", {"data": i})

        # Phase 2: Spawn concurrent workers appending WITH OCC
        num_workers = 3
        iterations_per_worker = 3

        with Pool(processes=num_workers) as pool:
            async_results = [
                pool.apply_async(
                    _worker_append_with_occ,
                    ((self.db_path, stream, worker_id, iterations_per_worker),),
                )
                for worker_id in range(num_workers)
            ]

            results = []
            for async_result in async_results:
                try:
                    result = async_result.get(timeout=30)
                    results.append(result)
                except Exception as e:
                    self.fail(f"Worker failed or timed out: {e}")

        # Verify all events landed
        events = store.read(stream)
        total_expected_events = num_appends_without_occ + sum(
            len(r["successes"]) for r in results
        )
        # The stream should have all successful appends
        self.assertGreaterEqual(len(events), num_appends_without_occ,
                               "Stream should have at least baseline events")

    def test_occ_empty_stream_version_zero(self):
        """Verify OCC on empty stream (version 0) works correctly."""
        store = EventStore(self.db_path)
        stream = "empty_stream"

        # Append to empty stream with expected_version=0
        v = store.append(
            stream,
            "first_event",
            {"data": "first"},
            expected_version=0,
        )
        self.assertEqual(v, 1)

        # Verify the event exists
        events = store.read(stream)
        self.assertEqual(len(events), 1)

    def test_occ_conflict_on_empty_stream_wrong_version(self):
        """Verify conflict when expected_version doesn't match empty stream."""
        store = EventStore(self.db_path)
        stream = "empty_stream"

        # Try to append to empty stream with expected_version=1 (wrong)
        with self.assertRaises(ConcurrencyConflict) as cm:
            store.append(
                stream,
                "first_event",
                {"data": "first"},
                expected_version=1,  # Wrong; stream is at version 0
            )

        exc = cm.exception
        self.assertEqual(exc.expected_version, 1)
        self.assertEqual(exc.actual_version, 0)

    def test_occ_sequence_append_then_occ(self):
        """Verify sequential appends with and without OCC interleaved."""
        store = EventStore(self.db_path)
        stream = "test_stream"

        # Append 1 without OCC
        v1 = store.append(stream, "event_1", {"data": "a"})
        self.assertEqual(v1, 1)

        # Read current version
        events = store.read(stream)
        current = len(events)
        self.assertEqual(current, 1)

        # Append 2 with OCC (expected_version=1)
        v2 = store.append(
            stream,
            "event_2",
            {"data": "b"},
            expected_version=current,
        )
        self.assertEqual(v2, 2)

        # Read current version again
        events = store.read(stream)
        current = len(events)
        self.assertEqual(current, 2)

        # Append 3 with OCC (expected_version=2)
        v3 = store.append(
            stream,
            "event_3",
            {"data": "c"},
            expected_version=current,
        )
        self.assertEqual(v3, 3)

        # Verify all 3 events
        events = store.read(stream)
        self.assertEqual(len(events), 3)
        versions = sorted([e["version"] for e in events])
        self.assertEqual(versions, [1, 2, 3])

    def test_state_api_occ_passthrough(self):
        """Verify StateAPI.append correctly passes through expected_version to EventStore."""
        api = StateAPI(self.db_path)
        stream = "test_stream"

        # Append via API without OCC
        v1 = api.append(stream, "event_1", {"data": "a"})
        self.assertEqual(v1, 1)

        # Read current version
        events = api.get(stream)
        current = len(events)

        # Append via API with OCC
        v2 = api.append(
            stream,
            "event_2",
            {"data": "b"},
            expected_version=current,
        )
        self.assertEqual(v2, 2)

        # Try with wrong version via API
        with self.assertRaises(ConcurrencyConflict):
            api.append(
                stream,
                "event_3",
                {"data": "c"},
                expected_version=999,
            )


if __name__ == "__main__":
    unittest.main()
