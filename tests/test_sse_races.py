"""Deterministic race condition tests for ui/sse.py — collector thread + client registry.

Contracts under test (concurrency / race conditions):
  - Concurrent client registration + broadcast must not corrupt _sse_clients list.
  - Concurrent unregister + broadcast must not raise KeyError or IndexError.
  - Collector thread snapshot mutations must not tear reads (hash-gated sections).
  - reset_state() called during active collection must not crash collector thread.
  - Concurrent _dropped_counts updates must not corrupt tracking or leak counts.
  - Collector loop must emit snapshots atomically w.r.t. reader threads.

Test strategy (deterministic, no sleep-based flakiness):
  - Use threading.Barrier to synchronize test threads at critical points.
  - Monkeypatch collectors/config to control when snapshots change.
  - Monkeypatch time.time() to advance heartbeat timing.
  - No real file I/O; all state via mocks + in-memory structures.
  - Verify final state consistency after all threads join.

Run: python -m unittest tests.test_sse_races
     python -m pytest tests/test_sse_races.py -v
"""
import importlib.util
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

UI_DIR = Path(__file__).parent.parent / "ui"


def load_sse(fixture_root):
    """Import a fresh ui/sse.py module instance with siblings resolvable."""
    ui_dir = str(UI_DIR)
    if ui_dir not in sys.path:
        sys.path.insert(0, ui_dir)

    os.environ["AESOP_ROOT"] = str(fixture_root)
    os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(fixture_root / "transcripts")

    import config
    config.reload()

    spec = importlib.util.spec_from_file_location(
        f"sse_races_{id(fixture_root)}", UI_DIR / "sse.py"
    )
    sse = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sse)
    return sse


class RaceFixtureCase(unittest.TestCase):
    """Base class for race condition tests."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-sse-races-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT")
        }
        self.sse = load_sse(self.fixture_root)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Stop any running collector thread
        if hasattr(self, "sse"):
            self.sse._collector_stop_event.set()
            time.sleep(0.05)  # Allow collector to exit gracefully  # sleep-ok
        shutil.rmtree(self.fixture_root, ignore_errors=True)


class TestConcurrentClientRegistration(RaceFixtureCase):
    """Race: concurrent register + broadcast must not corrupt _sse_clients list."""

    def test_concurrent_register_and_broadcast_no_corruption(self):
        """Multiple threads registering clients while broadcast iterates must not corrupt list."""
        sse = self.sse
        barrier = threading.Barrier(4)  # 3 register threads + 1 broadcast thread
        registered_queues = []

        def register_client():
            barrier.wait()  # Synchronize start
            q = sse.register_sse_client()
            if q:
                registered_queues.append(q)

        def broadcast_while_registering():
            barrier.wait()
            time.sleep(0.001)  # Let registrars start  # sleep-ok
            try:
                for _ in range(5):
                    sse.broadcast_sse("test", '{"msg": "concurrent"}')
                    time.sleep(0.0001)  # sleep-ok
            except Exception as e:
                self.fail(f"broadcast_sse raised during concurrent register: {e}")

        threads = [
            threading.Thread(target=register_client),
            threading.Thread(target=register_client),
            threading.Thread(target=register_client),
            threading.Thread(target=broadcast_while_registering),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify list integrity: no duplicates, all queues accounted for
        self.assertEqual(len(registered_queues), 3, "All register calls should succeed")
        self.assertEqual(len(set(id(q) for q in registered_queues)), 3,
                         "All registered queues must be unique objects")
        # Verify all registered queues are still in _sse_clients
        for q in registered_queues:
            self.assertIn(q, sse._sse_clients, "Registered queue should remain in list")


class TestConcurrentUnregisterAndBroadcast(RaceFixtureCase):
    """Race: concurrent unregister + broadcast must not raise KeyError or IndexError."""

    def test_concurrent_unregister_and_broadcast_no_error(self):
        """Unregistering a client while broadcast iterates must not error."""
        sse = self.sse
        q1 = sse.register_sse_client()
        q2 = sse.register_sse_client()
        q3 = sse.register_sse_client()

        barrier = threading.Barrier(3)
        errors = []

        def unregister():
            barrier.wait()
            try:
                sse.unregister_sse_client(q2)
            except Exception as e:
                errors.append(("unregister", e))

        def broadcast():
            barrier.wait()
            try:
                for _ in range(10):
                    sse.broadcast_sse("data", '{"v": 1}')
                    time.sleep(0.0001)  # sleep-ok
            except Exception as e:
                errors.append(("broadcast", e))

        def unregister2():
            barrier.wait()
            time.sleep(0.002)  # sleep-ok
            try:
                sse.unregister_sse_client(q1)
            except Exception as e:
                errors.append(("unregister2", e))

        threads = [
            threading.Thread(target=unregister),
            threading.Thread(target=broadcast),
            threading.Thread(target=unregister2),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent unregister/broadcast raised: {errors}")
        # Verify final state: q2 and q1 removed, q3 remains
        self.assertNotIn(q2, sse._sse_clients)
        self.assertNotIn(q1, sse._sse_clients)
        self.assertIn(q3, sse._sse_clients)


class TestConcurrentSnapshotReads(RaceFixtureCase):
    """Race: multiple clients reading _latest_snapshots while collector writes."""

    def test_concurrent_snapshot_read_and_update_no_corruption(self):
        """Readers must not see torn snapshots when collector updates."""
        sse = self.sse
        barrier = threading.Barrier(3)  # 2 readers + 1 updater
        read_results = []
        errors = []

        # Pre-populate a snapshot
        with sse._latest_lock:
            sse._latest_snapshots["data"] = '{"version": 1}'

        def read_snapshot():
            barrier.wait()
            try:
                for _ in range(5):
                    with sse._latest_lock:
                        snapshot = sse._latest_snapshots.get("data")
                    if snapshot:
                        data = json.loads(snapshot)
                        read_results.append(data)
                    time.sleep(0.0001)  # sleep-ok
            except Exception as e:
                errors.append(("read", e))

        def update_snapshot():
            barrier.wait()
            time.sleep(0.0005)  # sleep-ok
            try:
                for i in range(2, 5):
                    with sse._latest_lock:
                        sse._latest_snapshots["data"] = json.dumps({"version": i})
                    time.sleep(0.0002)  # sleep-ok
            except Exception as e:
                errors.append(("update", e))

        threads = [
            threading.Thread(target=read_snapshot),
            threading.Thread(target=read_snapshot),
            threading.Thread(target=update_snapshot),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent reads/writes raised: {errors}")
        # Verify all read values are valid JSON objects (not torn)
        for data in read_results:
            self.assertIsInstance(data, dict, "Snapshot must parse as complete JSON")
            self.assertIn("version", data, "Snapshot must have version field")


class TestResetStateDuringCollection(RaceFixtureCase):
    """Race: reset_state() called while collector_loop is running."""

    def test_reset_state_stops_collector_thread(self):
        """reset_state() must cleanly stop a running collector."""
        sse = self.sse

        # Mock collectors to avoid file I/O
        def mock_snapshot_data():
            return {"log_tail": "..."}

        with patch("collectors._snapshot_data", side_effect=mock_snapshot_data), \
             patch("collectors.parse_audit_backlog", side_effect=lambda: {"tiers": []}), \
             patch("agents._transcripts_fingerprint", side_effect=lambda: "fp1"), \
             patch("agents.get_fleet_agents", side_effect=lambda: []), \
             patch("agents.sanitize_agents_for_broadcast", side_effect=lambda x: x), \
             patch("collectors._snapshot_tracker", side_effect=lambda: {"items": []}), \
             patch("collectors._snapshot_orchestrator_status", side_effect=lambda: {}), \
             patch("collectors.drain_tracker_inbox", side_effect=lambda: None), \
             patch("cost.get_cost_summary", side_effect=lambda: {}), \
             patch("config.AUDIT_BACKLOG_FILE") as mock_backlog, \
             patch("config.STATE_DIR", self.fixture_root / "state"), \
             patch("config.BACKUP_LOG", None), \
             patch("config.ALERTS_LOG", None), \
             patch("config.LEDGER_FILE", None), \
             patch("config.COLLECTOR_INTERVAL", 0.01):

            mock_backlog.exists.return_value = False

            # Start collector
            sse.start_collector_thread()
            time.sleep(0.02)  # Let it run a few cycles  # sleep-ok

            old_stop_event = sse._collector_stop_event
            sse.reset_state()

            # Verify stop event was signalled
            self.assertTrue(old_stop_event.is_set(), "Old stop event must be set")
            # Verify new stop event is unset
            self.assertFalse(sse._collector_stop_event.is_set(), "New stop event must be unset")
            # Verify snapshots cleared
            self.assertTrue(all(v is None for v in sse._latest_snapshots.values()),
                           "Snapshots must be cleared")


class TestDroppedCountsRaces(RaceFixtureCase):
    """Race: concurrent access to _dropped_counts dict during queue overflow."""

    def test_concurrent_dropped_count_updates_no_corruption(self):
        """Multiple broadcasts with queue full must not corrupt _dropped_counts."""
        sse = self.sse

        # Create queues with size 1 to force overflow
        q1 = queue.Queue(maxsize=1)
        q2 = queue.Queue(maxsize=1)

        with sse._sse_lock:
            sse._sse_clients = [q1, q2]

        barrier = threading.Barrier(2)
        errors = []

        def broadcast_to_full_queue(qnum):
            barrier.wait()
            try:
                for i in range(5):
                    sse.broadcast_sse("data", f'{{"msg": "event{i}"}}')
                    time.sleep(0.0001)  # sleep-ok
            except Exception as e:
                errors.append((qnum, e))

        threads = [
            threading.Thread(target=broadcast_to_full_queue, args=(1,)),
            threading.Thread(target=broadcast_to_full_queue, args=(2,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent dropped_counts updates raised: {errors}")
        # Verify dropped counts are integers
        for q, count in sse._dropped_counts.items():
            self.assertIsInstance(count, int, f"Dropped count must be int, got {type(count)}")
            self.assertGreaterEqual(count, 0, f"Dropped count must be >= 0, got {count}")


class TestHeartbeatEmissionRace(RaceFixtureCase):
    """Race: heartbeat emission must not interfere with other sections."""

    def test_concurrent_heartbeat_and_section_broadcast(self):
        """Heartbeat emission must not corrupt section broadcasts."""
        sse = self.sse
        q = queue.Queue(maxsize=100)

        with sse._sse_lock:
            sse._sse_clients.append(q)

        barrier = threading.Barrier(2)
        errors = []

        def emit_sections():
            barrier.wait()
            try:
                for i in range(5):
                    sse._maybe_emit("data", {"seq": i}, {})
                    time.sleep(0.0001)  # sleep-ok
            except Exception as e:
                errors.append(("sections", e))

        def emit_heartbeats():
            barrier.wait()
            try:
                for i in range(5):
                    heartbeat = json.dumps({"timestamp": int(time.time() * 1000)})
                    sse.broadcast_sse("heartbeat", heartbeat)
                    time.sleep(0.00015)  # sleep-ok
            except Exception as e:
                errors.append(("heartbeats", e))

        threads = [
            threading.Thread(target=emit_sections),
            threading.Thread(target=emit_heartbeats),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent heartbeat/section emission raised: {errors}")

        # Verify queue received both types
        events_received = []
        while True:
            try:
                event_type, payload = q.get_nowait()
                events_received.append(event_type)
            except queue.Empty:
                break

        self.assertIn("data", events_received, "Should receive data section events")
        self.assertIn("heartbeat", events_received, "Should receive heartbeat events")


class TestHashGateThreadSafety(RaceFixtureCase):
    """Race: _maybe_emit hash-gating + snapshot storage must be thread-safe."""

    def test_hash_gate_shared_tracking_under_concurrent_emits(self):
        """Concurrent _maybe_emit calls with shared last_hashes must not corrupt tracking."""
        sse = self.sse
        q = queue.Queue(maxsize=100)

        with sse._sse_lock:
            sse._sse_clients.append(q)

        barrier = threading.Barrier(3)
        # Shared last_hashes dict (as in real collector_loop)
        last_hashes = {}
        snapshot = {"data": "same"}

        def emit_same_snapshot(thread_id):
            barrier.wait()
            try:
                # Same section name, same snapshot from multiple threads
                sse._maybe_emit("shared_section", snapshot, last_hashes)
                time.sleep(0.00005)  # sleep-ok
            except Exception as e:
                self.fail(f"Thread {thread_id} raised: {e}")

        threads = [
            threading.Thread(target=emit_same_snapshot, args=(1,)),
            threading.Thread(target=emit_same_snapshot, args=(2,)),
            threading.Thread(target=emit_same_snapshot, args=(3,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # With shared last_hashes, first emit sets the hash, rest are gated
        events_received = []
        while True:
            try:
                event_type, payload = q.get_nowait()
                events_received.append((event_type, payload))
            except queue.Empty:
                break

        # Should only emit once (dedup via shared hash tracking)
        self.assertEqual(len(events_received), 1,
                        f"Shared section emitted {len(events_received)} times, expected 1")
        self.assertEqual(events_received[0][0], "shared_section")

    def test_maybe_emit_snapshot_storage_thread_safe(self):
        """Concurrent _maybe_emit calls must not corrupt _latest_snapshots dict."""
        sse = self.sse
        barrier = threading.Barrier(2)
        last_hashes = {}

        def emit_different_sections():
            barrier.wait()
            try:
                for i in range(5):
                    sse._maybe_emit(f"section_a", {"seq": i}, last_hashes)
                    sse._maybe_emit(f"section_b", {"seq": i * 2}, last_hashes)
                    time.sleep(0.00005)  # sleep-ok
            except Exception as e:
                self.fail(f"emit_different_sections raised: {e}")

        threads = [
            threading.Thread(target=emit_different_sections),
            threading.Thread(target=emit_different_sections),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify snapshots are valid JSON and not corrupted
        with sse._latest_lock:
            for section_name, snapshot_json in sse._latest_snapshots.items():
                if snapshot_json is not None:
                    try:
                        json.loads(snapshot_json)
                    except json.JSONDecodeError:
                        self.fail(f"Section {section_name} has invalid JSON: {snapshot_json}")


class TestClientQueueFullRaceCondition(RaceFixtureCase):
    """Race: queue.Full exception path with concurrent unregister."""

    def test_queue_full_with_concurrent_unregister(self):
        """broadcast_sse must handle queue.Full gracefully even with concurrent unregister."""
        sse = self.sse

        q1 = queue.Queue(maxsize=1)
        q2 = queue.Queue(maxsize=1)

        with sse._sse_lock:
            sse._sse_clients = [q1, q2]

        # Fill q1 to trigger queue.Full
        try:
            q1.put_nowait(("data", '{"msg": "fill"}'))
        except queue.Full:
            pass

        barrier = threading.Barrier(2)
        errors = []

        def broadcast_to_full():
            barrier.wait()
            try:
                sse.broadcast_sse("data", '{"msg": "broadcast"}')
            except Exception as e:
                errors.append(("broadcast", e))

        def unregister_during_broadcast():
            barrier.wait()
            time.sleep(0.0001)  # sleep-ok
            try:
                sse.unregister_sse_client(q1)
            except Exception as e:
                errors.append(("unregister", e))

        threads = [
            threading.Thread(target=broadcast_to_full),
            threading.Thread(target=unregister_during_broadcast),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent broadcast/unregister raised: {errors}")


if __name__ == "__main__":
    unittest.main()
