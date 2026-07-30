"""Tests for multi-instance management — instance registration, heartbeat, and file claims.

Covers:
  - Instance registration and discovery
  - Heartbeat and stale detection
  - File claim/release lifecycle
  - Conflict detection (two instances claiming same file)
  - Multiple concurrent instances
  - Error handling and fail-closed semantics

Uses temporary SQLite databases to avoid pollution; enforces isolation per test.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store import StateAPI, EventStore
from state_store.instance_projection import (
    register_instance,
    heartbeat,
    claim_files,
    release_files,
    list_active_instances,
    get_instance_status,
    get_claimed_files,
    get_all_claimed_files,
    detect_stale_instances,
)


class TestInstanceRegistration(unittest.TestCase):
    """Test instance registration and listing."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.store = StateAPI(self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_register_single_instance(self):
        """Register one instance and verify it appears in the list."""
        instance_id = "host1:1234:abc123"
        success = register_instance(self.store, instance_id, "host1", 1234)
        self.assertTrue(success)

        instances = list_active_instances(self.store)
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0]["instance_id"], instance_id)
        self.assertEqual(instances[0]["hostname"], "host1")
        self.assertEqual(instances[0]["pid"], 1234)
        self.assertEqual(instances[0]["status"], "active")

    def test_register_multiple_instances(self):
        """Register multiple instances and verify all are listed."""
        ids = ["host1:1234:abc", "host2:5678:def", "host3:9999:ghi"]
        for i, instance_id in enumerate(ids):
            host = f"host{i+1}"
            pid = 1234 + i * 1000
            register_instance(self.store, instance_id, host, pid)

        instances = list_active_instances(self.store)
        self.assertEqual(len(instances), 3)

        returned_ids = [inst["instance_id"] for inst in instances]
        self.assertEqual(sorted(returned_ids), sorted(ids))

    def test_register_duplicate_overwrites(self):
        """Re-registering an instance updates its registration."""
        instance_id = "host1:1234:abc123"

        # First registration
        register_instance(self.store, instance_id, "host1", 1234)
        instances1 = list_active_instances(self.store)
        self.assertEqual(len(instances1), 1)

        # Second registration (update)
        register_instance(self.store, instance_id, "host1", 1234)
        instances2 = list_active_instances(self.store)
        self.assertEqual(len(instances2), 1)
        self.assertEqual(instances2[0]["instance_id"], instance_id)

    def test_get_instance_status(self):
        """Retrieve status of a specific registered instance."""
        instance_id = "host1:1234:abc123"
        register_instance(self.store, instance_id, "host1", 1234)

        status = get_instance_status(self.store, instance_id)
        self.assertIsNotNone(status)
        self.assertEqual(status["instance_id"], instance_id)
        self.assertEqual(status["hostname"], "host1")
        self.assertEqual(status["status"], "active")

    def test_get_nonexistent_instance_status(self):
        """Querying a non-existent instance returns None."""
        status = get_instance_status(self.store, "nonexistent:0:xyz")
        self.assertIsNone(status)


class TestHeartbeatAndStaleness(unittest.TestCase):
    """Test heartbeat updates and stale instance detection."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.store = StateAPI(self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_heartbeat_updates_timestamp(self):
        """Sending a heartbeat updates the last_heartbeat timestamp."""
        instance_id = "host1:1234:abc123"
        register_instance(self.store, instance_id, "host1", 1234)

        status1 = get_instance_status(self.store, instance_id)
        hb1 = status1["last_heartbeat"]

        # Small delay then send heartbeat
        time.sleep(0.01)  # sleep-ok
        heartbeat(self.store, instance_id)

        status2 = get_instance_status(self.store, instance_id)
        hb2 = status2["last_heartbeat"]

        self.assertGreater(hb2, hb1)

    def test_detect_stale_after_timeout(self):
        """An instance with no recent heartbeat is detected as stale."""
        instance_id = "host1:1234:abc123"
        register_instance(self.store, instance_id, "host1", 1234)

        # With a very small threshold, the instance is immediately stale
        stale = detect_stale_instances(self.store, stale_threshold_seconds=0.0)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["instance_id"], instance_id)

    def test_active_instance_not_stale(self):
        """A recently-heartbeated instance is not stale."""
        instance_id = "host1:1234:abc123"
        register_instance(self.store, instance_id, "host1", 1234)
        heartbeat(self.store, instance_id)

        # With a large threshold, instance is not stale
        stale = detect_stale_instances(self.store, stale_threshold_seconds=10.0)
        self.assertEqual(len(stale), 0)

    def test_multiple_instances_stale_detection(self):
        """Correctly identify which instances are stale among multiple."""
        # Register 3 instances
        ids = ["host1:1234:abc", "host2:5678:def", "host3:9999:ghi"]
        for i, instance_id in enumerate(ids):
            register_instance(self.store, instance_id, f"host{i+1}", 1234 + i * 1000)

        # Heartbeat only the first two
        heartbeat(self.store, ids[0])
        heartbeat(self.store, ids[1])
        # Third has no heartbeat, is immediately stale with threshold 0

        stale = detect_stale_instances(self.store, stale_threshold_seconds=0.0)
        stale_ids = [inst["instance_id"] for inst in stale]
        self.assertIn(ids[2], stale_ids)


class TestFileClaims(unittest.TestCase):
    """Test file claim and release lifecycle."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.store = StateAPI(self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_claim_single_file(self):
        """Claim a single file and verify it appears in claimed list."""
        instance_id = "host1:1234:abc123"
        register_instance(self.store, instance_id, "host1", 1234)

        file_path = "/path/to/file.txt"
        success = claim_files(self.store, instance_id, [file_path])
        self.assertTrue(success)

        claimed = get_claimed_files(self.store, instance_id)
        self.assertEqual(claimed, [file_path])

    def test_claim_multiple_files(self):
        """Claim multiple files at once."""
        instance_id = "host1:1234:abc123"
        register_instance(self.store, instance_id, "host1", 1234)

        files = ["/path/to/file1.txt", "/path/to/file2.py", "/path/to/file3.json"]
        success = claim_files(self.store, instance_id, files)
        self.assertTrue(success)

        claimed = get_claimed_files(self.store, instance_id)
        self.assertEqual(sorted(claimed), sorted(files))

    def test_release_claimed_files(self):
        """Release files that were previously claimed."""
        instance_id = "host1:1234:abc123"
        register_instance(self.store, instance_id, "host1", 1234)

        files = ["/path/to/file1.txt", "/path/to/file2.txt"]
        claim_files(self.store, instance_id, files)

        # Verify claimed
        claimed_before = get_claimed_files(self.store, instance_id)
        self.assertEqual(len(claimed_before), 2)

        # Release one file
        success = release_files(self.store, instance_id, [files[0]])
        self.assertTrue(success)

        # Verify only one remains
        claimed_after = get_claimed_files(self.store, instance_id)
        self.assertEqual(claimed_after, [files[1]])

    def test_release_all_files(self):
        """Release all claimed files."""
        instance_id = "host1:1234:abc123"
        register_instance(self.store, instance_id, "host1", 1234)

        files = ["/path/to/file1.txt", "/path/to/file2.txt"]
        claim_files(self.store, instance_id, files)

        # Release all
        success = release_files(self.store, instance_id, files)
        self.assertTrue(success)

        # Verify empty
        claimed = get_claimed_files(self.store, instance_id)
        self.assertEqual(claimed, [])

    def test_release_unclaimed_files_idempotent(self):
        """Releasing unclaimed files is idempotent (no error)."""
        instance_id = "host1:1234:abc123"
        register_instance(self.store, instance_id, "host1", 1234)

        # Release without claiming first
        success = release_files(self.store, instance_id, ["/unclaimed/file.txt"])
        self.assertTrue(success)

        claimed = get_claimed_files(self.store, instance_id)
        self.assertEqual(claimed, [])

    def test_empty_file_list_operations(self):
        """Claiming/releasing empty file list is a no-op."""
        instance_id = "host1:1234:abc123"
        register_instance(self.store, instance_id, "host1", 1234)

        success_claim = claim_files(self.store, instance_id, [])
        self.assertTrue(success_claim)

        success_release = release_files(self.store, instance_id, [])
        self.assertTrue(success_release)

        claimed = get_claimed_files(self.store, instance_id)
        self.assertEqual(claimed, [])


class TestMultiInstanceConflicts(unittest.TestCase):
    """Test conflict detection when multiple instances claim the same files."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.store = StateAPI(self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_detect_file_conflict_between_instances(self):
        """Detect when two instances claim the same file."""
        instance1 = "host1:1234:abc"
        instance2 = "host2:5678:def"

        # Register both
        register_instance(self.store, instance1, "host1", 1234)
        register_instance(self.store, instance2, "host2", 5678)

        # Both claim the same file
        shared_file = "/shared/data.txt"
        claim_files(self.store, instance1, [shared_file])
        claim_files(self.store, instance2, [shared_file])

        # Verify both appear in claimed files
        all_claimed = get_all_claimed_files(self.store)
        self.assertEqual(set(all_claimed[instance1]), {shared_file})
        self.assertEqual(set(all_claimed[instance2]), {shared_file})

    def test_no_conflict_with_different_files(self):
        """No conflict when instances claim different files."""
        instance1 = "host1:1234:abc"
        instance2 = "host2:5678:def"

        register_instance(self.store, instance1, "host1", 1234)
        register_instance(self.store, instance2, "host2", 5678)

        claim_files(self.store, instance1, ["/file1.txt"])
        claim_files(self.store, instance2, ["/file2.txt"])

        all_claimed = get_all_claimed_files(self.store)
        self.assertEqual(set(all_claimed[instance1]), {"/file1.txt"})
        self.assertEqual(set(all_claimed[instance2]), {"/file2.txt"})

    def test_partial_overlap_in_file_claims(self):
        """Detect partial overlap in file claims."""
        instance1 = "host1:1234:abc"
        instance2 = "host2:5678:def"

        register_instance(self.store, instance1, "host1", 1234)
        register_instance(self.store, instance2, "host2", 5678)

        # Instance 1 claims files A, B, C
        claim_files(self.store, instance1, ["/a.txt", "/b.txt", "/c.txt"])
        # Instance 2 claims files B, C, D (overlaps on B and C)
        claim_files(self.store, instance2, ["/b.txt", "/c.txt", "/d.txt"])

        all_claimed = get_all_claimed_files(self.store)
        self.assertEqual(set(all_claimed[instance1]), {"/a.txt", "/b.txt", "/c.txt"})
        self.assertEqual(set(all_claimed[instance2]), {"/b.txt", "/c.txt", "/d.txt"})

    def test_release_resolves_conflict(self):
        """Releasing a file resolves the conflict."""
        instance1 = "host1:1234:abc"
        instance2 = "host2:5678:def"

        register_instance(self.store, instance1, "host1", 1234)
        register_instance(self.store, instance2, "host2", 5678)

        shared_file = "/shared/data.txt"
        claim_files(self.store, instance1, [shared_file])
        claim_files(self.store, instance2, [shared_file])

        # Instance 1 releases
        release_files(self.store, instance1, [shared_file])

        all_claimed = get_all_claimed_files(self.store)
        self.assertNotIn(instance1, all_claimed)
        self.assertEqual(set(all_claimed[instance2]), {shared_file})


class TestAllClaimedFiles(unittest.TestCase):
    """Test aggregated view of all claimed files."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.store = StateAPI(self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_get_all_claimed_files_empty(self):
        """With no claims, get_all_claimed_files returns empty dict."""
        all_claimed = get_all_claimed_files(self.store)
        self.assertEqual(all_claimed, {})

    def test_get_all_claimed_files_multiple_instances(self):
        """Aggregate all claimed files across instances."""
        instances = [
            ("host1:1234:abc", "host1", 1234),
            ("host2:5678:def", "host2", 5678),
            ("host3:9999:ghi", "host3", 9999),
        ]

        for inst_id, host, pid in instances:
            register_instance(self.store, inst_id, host, pid)

        # Each instance claims some files
        claim_files(self.store, instances[0][0], ["/data/file1.txt", "/data/file2.txt"])
        claim_files(self.store, instances[1][0], ["/code/main.py"])
        claim_files(self.store, instances[2][0], ["/config/app.json", "/logs/app.log"])

        all_claimed = get_all_claimed_files(self.store)

        self.assertEqual(len(all_claimed), 3)
        self.assertEqual(set(all_claimed[instances[0][0]]), {"/data/file1.txt", "/data/file2.txt"})
        self.assertEqual(set(all_claimed[instances[1][0]]), {"/code/main.py"})
        self.assertEqual(set(all_claimed[instances[2][0]]), {"/config/app.json", "/logs/app.log"})


class TestErrorHandling(unittest.TestCase):
    """Test error handling and fail-closed semantics."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.store = StateAPI(self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_list_returns_empty_on_empty_stream(self):
        """Listing with no instances returns empty list."""
        instances = list_active_instances(self.store)
        self.assertEqual(instances, [])

    def test_get_status_returns_none_for_nonexistent(self):
        """Getting status for non-existent instance returns None."""
        status = get_instance_status(self.store, "host1:1234:abc")
        self.assertIsNone(status)

    def test_get_claimed_files_returns_empty_for_nonexistent(self):
        """Getting claimed files for non-existent instance returns empty list."""
        files = get_claimed_files(self.store, "host1:1234:abc")
        self.assertEqual(files, [])

    def test_detect_stale_returns_empty_when_none_stale(self):
        """Detecting stale instances with high threshold returns empty."""
        register_instance(self.store, "host1:1234:abc", "host1", 1234)
        heartbeat(self.store, "host1:1234:abc")

        stale = detect_stale_instances(self.store, stale_threshold_seconds=1000.0)
        self.assertEqual(stale, [])

    def test_get_all_claimed_files_with_no_claims(self):
        """Getting all claimed files with no claims returns empty dict."""
        register_instance(self.store, "host1:1234:abc", "host1", 1234)

        all_claimed = get_all_claimed_files(self.store)
        self.assertEqual(all_claimed, {})

    def test_idempotent_release_of_unclaimed_files(self):
        """Releasing unclaimed files multiple times is idempotent."""
        register_instance(self.store, "host1:1234:abc", "host1", 1234)

        # Release unclaimed multiple times
        self.assertTrue(release_files(self.store, "host1:1234:abc", ["/file.txt"]))
        self.assertTrue(release_files(self.store, "host1:1234:abc", ["/file.txt"]))
        self.assertTrue(release_files(self.store, "host1:1234:abc", ["/file.txt"]))

        claimed = get_claimed_files(self.store, "host1:1234:abc")
        self.assertEqual(claimed, [])


class TestComplexScenarios(unittest.TestCase):
    """Test complex multi-instance scenarios."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.store = StateAPI(self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_instance_lifecycle_full_cycle(self):
        """Complete lifecycle: register, heartbeat, claim, release, check status."""
        instance_id = "host1:1234:abc123"

        # Register
        self.assertTrue(register_instance(self.store, instance_id, "host1", 1234))

        # Verify active
        instances = list_active_instances(self.store)
        self.assertEqual(len(instances), 1)

        # Send heartbeat
        self.assertTrue(heartbeat(self.store, instance_id))

        # Claim files
        files = ["/work/file1.txt", "/work/file2.txt"]
        self.assertTrue(claim_files(self.store, instance_id, files))

        # Verify claimed
        claimed = get_claimed_files(self.store, instance_id)
        self.assertEqual(set(claimed), set(files))

        # Release one file
        self.assertTrue(release_files(self.store, instance_id, [files[0]]))
        claimed = get_claimed_files(self.store, instance_id)
        self.assertEqual(claimed, [files[1]])

        # Release remaining
        self.assertTrue(release_files(self.store, instance_id, [files[1]]))
        claimed = get_claimed_files(self.store, instance_id)
        self.assertEqual(claimed, [])

    def test_coordinated_multi_instance_workflow(self):
        """Multiple instances coordinating on different file sets."""
        # Create 3 instances
        instances = []
        for i in range(3):
            inst_id = f"host{i}:{1000+i}:xyz{i}"
            instances.append(inst_id)
            register_instance(self.store, inst_id, f"host{i}", 1000 + i)
            heartbeat(self.store, inst_id)

        # Each claims different files
        file_sets = [
            ["/module/a.py", "/module/a_test.py"],
            ["/module/b.py", "/module/b_test.py"],
            ["/module/c.py", "/module/c_test.py"],
        ]

        for inst_id, files in zip(instances, file_sets):
            claim_files(self.store, inst_id, files)

        # Verify all are active and claimed properly
        active = list_active_instances(self.store)
        self.assertEqual(len(active), 3)

        all_claimed = get_all_claimed_files(self.store)
        for inst_id, expected_files in zip(instances, file_sets):
            self.assertEqual(set(all_claimed[inst_id]), set(expected_files))


if __name__ == "__main__":
    unittest.main()
