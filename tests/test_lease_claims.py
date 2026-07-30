"""Tests for state_store.lease_claims — multi-instance file-scope leasing."""

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add state_store to path for import
sys.path.insert(0, str(Path(__file__).parent.parent))

from state_store.lease_claims import LeaseStore, LeaseConflict


class TestLeaseStore(unittest.TestCase):
    """Unit tests for LeaseStore multi-instance file-scope leasing."""

    def setUp(self):
        """Create temporary database for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_leases.db"
        self.store = LeaseStore(str(self.db_path))

    def tearDown(self):
        """Clean up temporary database."""
        self.store.close()
        self.temp_dir.cleanup()

    def test_happy_path_claim_renew_release(self):
        """Happy path: claim paths, renew, and release."""
        now = 1000.0
        lease_id = self.store.claim(
            paths=["file1.txt", "file2.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )
        self.assertIsNotNone(lease_id)
        self.assertIsInstance(lease_id, str)
        self.assertTrue(len(lease_id) > 0)

        # Check the lease exists
        holder = self.store.get_holder(["file1.txt"], clock=lambda: now)
        self.assertEqual(holder, "instance-1")

        # Renew extends TTL
        now = 1030.0
        self.store.renew(
            lease_id=lease_id,
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )
        # Lease should still be valid after renewal
        holder = self.store.get_holder(["file1.txt"], clock=lambda: now)
        self.assertEqual(holder, "instance-1")

        # Release the lease
        self.store.release(
            lease_id=lease_id,
            instance_id="instance-1",
            clock=lambda: now
        )
        # After release, no holder
        holder = self.store.get_holder(["file1.txt"], clock=lambda: now)
        self.assertIsNone(holder)

    def test_conflict_on_overlapping_path(self):
        """Claim fails when path is already held by another instance."""
        now = 1000.0
        # Instance 1 claims file1.txt
        lease_id_1 = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )
        self.assertIsNotNone(lease_id_1)

        # Instance 2 tries to claim same path -> conflict
        with self.assertRaises(LeaseConflict) as ctx:
            self.store.claim(
                paths=["file1.txt"],
                instance_id="instance-2",
                ttl_seconds=60.0,
                clock=lambda: now
            )
        # Verify conflict info
        self.assertEqual(ctx.exception.conflicting_instance, "instance-1")
        self.assertIn("file1.txt", ctx.exception.conflicting_paths)

    def test_non_overlap_concurrent_claims(self):
        """Multiple instances can claim non-overlapping paths."""
        now = 1000.0
        lease_id_1 = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )
        self.assertIsNotNone(lease_id_1)

        # Instance 2 claims different path -> succeeds
        lease_id_2 = self.store.claim(
            paths=["file2.txt"],
            instance_id="instance-2",
            ttl_seconds=60.0,
            clock=lambda: now
        )
        self.assertIsNotNone(lease_id_2)
        self.assertNotEqual(lease_id_1, lease_id_2)

        # Verify both hold their paths
        self.assertEqual(self.store.get_holder(["file1.txt"], clock=lambda: now), "instance-1")
        self.assertEqual(self.store.get_holder(["file2.txt"], clock=lambda: now), "instance-2")

    def test_renew_extends_ttl(self):
        """Renew extends TTL; lease expires only if not renewed."""
        now = 1000.0
        lease_id = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )

        # At now + 50s, renew for another 60s
        now = 1050.0
        self.store.renew(
            lease_id=lease_id,
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )

        # At now + 70s (original would expire at 1060, but renewed to 1110)
        now = 1070.0
        holder = self.store.get_holder(["file1.txt"], clock=lambda: now)
        self.assertEqual(holder, "instance-1")

        # At now + 120s (past renewal deadline)
        now = 1120.0
        holder = self.store.get_holder(["file1.txt"], clock=lambda: now)
        self.assertIsNone(holder)

    def test_release_frees_lease(self):
        """After release, path is claimable by others."""
        now = 1000.0
        lease_id = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )

        # Release the lease
        self.store.release(
            lease_id=lease_id,
            instance_id="instance-1",
            clock=lambda: now
        )

        # Instance 2 can now claim it
        lease_id_2 = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-2",
            ttl_seconds=60.0,
            clock=lambda: now
        )
        self.assertIsNotNone(lease_id_2)
        self.assertEqual(self.store.get_holder(["file1.txt"], clock=lambda: now), "instance-2")

    def test_expired_lease_stolen(self):
        """After expiry, another instance can claim the path (steal on expiry)."""
        now = 1000.0
        lease_id = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )

        # Lease is held by instance-1
        holder = self.store.get_holder(["file1.txt"], clock=lambda: now)
        self.assertEqual(holder, "instance-1")

        # Time advances past expiry (now 1100 > 1000 + 60)
        now = 1100.0
        holder = self.store.get_holder(["file1.txt"], clock=lambda: now)
        self.assertIsNone(holder)

        # Instance 2 can now claim it (steal on expiry)
        lease_id_2 = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-2",
            ttl_seconds=60.0,
            clock=lambda: now
        )
        self.assertIsNotNone(lease_id_2)
        self.assertEqual(self.store.get_holder(["file1.txt"], clock=lambda: now), "instance-2")

    def test_wrong_instance_renew_rejected(self):
        """Renew fails if caller is not the lease holder."""
        now = 1000.0
        lease_id = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )

        # Instance 2 tries to renew a lease held by instance-1
        with self.assertRaises(ValueError) as ctx:
            self.store.renew(
                lease_id=lease_id,
                instance_id="instance-2",
                ttl_seconds=60.0,
                clock=lambda: now
            )
        self.assertIn("instance-1", str(ctx.exception))

    def test_wrong_instance_release_rejected(self):
        """Release fails if caller is not the lease holder."""
        now = 1000.0
        lease_id = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )

        # Instance 2 tries to release a lease held by instance-1
        with self.assertRaises(ValueError) as ctx:
            self.store.release(
                lease_id=lease_id,
                instance_id="instance-2",
                clock=lambda: now
            )
        self.assertIn("instance-1", str(ctx.exception))

    def test_claim_multiple_paths_atomically(self):
        """Claiming multiple paths is atomic: either all or none."""
        now = 1000.0
        # Instance 1 claims file1.txt
        lease_id_1 = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )

        # Instance 2 tries to claim both file1.txt (held) and file2.txt
        with self.assertRaises(LeaseConflict):
            self.store.claim(
                paths=["file1.txt", "file2.txt"],
                instance_id="instance-2",
                ttl_seconds=60.0,
                clock=lambda: now
            )

        # Verify file2.txt is NOT claimed by instance-2 (atomic failure)
        holder = self.store.get_holder(["file2.txt"], clock=lambda: now)
        self.assertIsNone(holder)

    def test_default_clock_uses_time_time(self):
        """When clock parameter not provided, uses time.time()."""
        # Claim without explicit clock
        lease_id = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0
        )
        self.assertIsNotNone(lease_id)

        # Should be claimable (not expired)
        holder = self.store.get_holder(["file1.txt"])
        self.assertEqual(holder, "instance-1")


class TestLeaseStoreIntegration(unittest.TestCase):
    """Integration tests for realistic multi-instance scenarios."""

    def setUp(self):
        """Create temporary database for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_leases.db"
        self.store = LeaseStore(str(self.db_path))

    def tearDown(self):
        """Clean up temporary database."""
        self.store.close()
        self.temp_dir.cleanup()

    def test_instance_crash_recovery_via_expiry(self):
        """Crashed instance's lease becomes claimable after TTL expiry."""
        now = 1000.0
        # Instance 1 claims critical files
        lease_id = self.store.claim(
            paths=["state.db", "config.json"],
            instance_id="instance-1",
            ttl_seconds=30.0,
            clock=lambda: now
        )

        # Instance 1 crashes, but its lease is still valid at now
        holder = self.store.get_holder(["state.db"], clock=lambda: now)
        self.assertEqual(holder, "instance-1")

        # 25 seconds later, lease still valid
        now = 1025.0
        holder = self.store.get_holder(["state.db"], clock=lambda: now)
        self.assertEqual(holder, "instance-1")

        # 35 seconds later, lease expired and reclaimable
        now = 1035.0
        holder = self.store.get_holder(["state.db"], clock=lambda: now)
        self.assertIsNone(holder)

        # Instance 2 can now claim the files
        lease_id_2 = self.store.claim(
            paths=["state.db", "config.json"],
            instance_id="instance-2",
            ttl_seconds=30.0,
            clock=lambda: now
        )
        self.assertIsNotNone(lease_id_2)
        self.assertEqual(self.store.get_holder(["state.db"], clock=lambda: now), "instance-2")

    def test_multiple_leases_per_instance(self):
        """One instance can hold multiple independent leases."""
        now = 1000.0
        # Instance 1 claims file1.txt
        lease_id_1 = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )

        # Instance 1 claims file2.txt in a separate lease
        lease_id_2 = self.store.claim(
            paths=["file2.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )

        self.assertNotEqual(lease_id_1, lease_id_2)
        self.assertEqual(self.store.get_holder(["file1.txt"], clock=lambda: now), "instance-1")
        self.assertEqual(self.store.get_holder(["file2.txt"], clock=lambda: now), "instance-1")

        # Release only lease 1
        self.store.release(lease_id_1, "instance-1", clock=lambda: now)
        self.assertIsNone(self.store.get_holder(["file1.txt"], clock=lambda: now))
        self.assertEqual(self.store.get_holder(["file2.txt"], clock=lambda: now), "instance-1")


if __name__ == "__main__":
    unittest.main()
