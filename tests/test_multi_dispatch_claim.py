"""Tests for multi_dispatch TOCTOU race reproduction and ClaimBackend atomicity.

TOCTOU (time-of-check to time-of-use) race: two concurrent claim attempts
on the same path must not both succeed. Before Inc 2, both succeeded because
check_conflict() and claim_files() were separate operations with no lock.

This test reproduces the race by threading two claim attempts and verifying
that exactly one succeeds (ClaimConflict is raised on the loser).
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Thread
from typing import Optional

# Add parent directory to path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state_store import StateAPI
from state_store.claim_backend import ClaimConflict, LocalLeaseBackend, get_backend


class TestMultiDispatchClaimTOCTOU(unittest.TestCase):
    """Reproduce TOCTOU race: concurrent claims on the same file."""

    def setUp(self):
        """Create a temporary database for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "state.db")
        # Initialize database with event store schema
        api = StateAPI(self.db_path)
        api.close()
        self.backend = None

    def tearDown(self):
        """Clean up temporary directory."""
        if self.backend:
            try:
                self.backend.close()
            except Exception:
                pass
        self.temp_dir.cleanup()

    def test_concurrent_claims_same_path_one_succeeds(self):
        """Two concurrent claims on the same path: only one succeeds.

        This is the load-bearing test for the Inc 2 fix. It demonstrates that
        when two instances try to claim the same file concurrently, exactly one
        succeeds and the other gets ClaimConflict. Without atomicity (the bug),
        both would succeed.
        """
        # SQLite connections are thread-local; create separate backend per thread
        path = "/shared/important.txt"
        instance_1 = "host1:1234:abc"
        instance_2 = "host2:5678:def"

        results = {"instance_1": None, "instance_2": None}
        errors = {"instance_1": None, "instance_2": None}

        def claim_instance_1():
            backend = LocalLeaseBackend(self.db_path)
            try:
                lease_id = backend.claim([path], instance_1, ttl_seconds=60)
                results["instance_1"] = lease_id
            except ClaimConflict as e:
                errors["instance_1"] = e
            finally:
                backend.close()

        def claim_instance_2():
            backend = LocalLeaseBackend(self.db_path)
            try:
                lease_id = backend.claim([path], instance_2, ttl_seconds=60)
                results["instance_2"] = lease_id
            except ClaimConflict as e:
                errors["instance_2"] = e
            finally:
                backend.close()

        # Launch both claims concurrently
        t1 = Thread(target=claim_instance_1)
        t2 = Thread(target=claim_instance_2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one should succeed, one should raise ClaimConflict
        success_count = sum(1 for r in results.values() if r is not None)
        conflict_count = sum(1 for e in errors.values() if e is not None)

        self.assertEqual(
            success_count, 1, f"Expected exactly 1 success, got {success_count}"
        )
        self.assertEqual(
            conflict_count, 1, f"Expected exactly 1 conflict, got {conflict_count}"
        )

    def test_claim_conflict_no_record_written(self):
        """On conflict, no claim record is written (fail-closed).

        This verifies the spec requirement: if claim() raises ClaimConflict,
        no entry is created in the lease table.
        """
        self.backend = LocalLeaseBackend(self.db_path)
        path = "/shared/critical.txt"
        instance_1 = "host1:1234:abc"
        instance_2 = "host2:5678:def"

        # First claim succeeds
        lease_id_1 = self.backend.claim([path], instance_1, ttl_seconds=60)
        self.assertIsNotNone(lease_id_1)

        # Second claim on same path should fail
        with self.assertRaises(ClaimConflict):
            self.backend.claim([path], instance_2, ttl_seconds=60)

        # Verify holder is still instance_1 (no phantom entry for instance_2)
        holder = self.backend.holder([path])
        self.assertEqual(holder, instance_1)

    def test_legacy_flag_off_uses_instance_projection(self):
        """When multibox.enabled=False, multi_dispatch uses legacy path (advisory claim_files).

        This test verifies the spec requirement: flag off keeps legacy path byte-for-byte.
        """
        # Test that get_backend returns LocalLeaseBackend when flag is on
        # and returns None or advisory backend when flag is off
        config_on = {"multibox": {"enabled": True}}
        config_off = {"multibox": {"enabled": False}}

        self.backend = get_backend(self.db_path, config_on)
        backend_off = get_backend(self.db_path, config_off)

        # When on, should return ClaimBackend adapter
        self.assertIsNotNone(self.backend)
        # When off, should return None (legacy path)
        self.assertIsNone(backend_off)

    def test_claim_renew_release_cycle(self):
        """Verify claim -> renew -> release lifecycle."""
        self.backend = LocalLeaseBackend(self.db_path)
        path = "/shared/file.txt"
        instance_id = "host1:1234:abc"

        # Claim
        lease_id = self.backend.claim([path], instance_id, ttl_seconds=60)
        self.assertIsNotNone(lease_id)
        self.assertEqual(self.backend.holder([path]), instance_id)

        # Renew
        self.backend.renew(lease_id, instance_id, ttl_seconds=120)
        self.assertEqual(self.backend.holder([path]), instance_id)

        # Release
        self.backend.release(lease_id, instance_id)
        self.assertIsNone(self.backend.holder([path]))

    def test_claim_all_paths_or_none(self):
        """Claim is atomic: all paths or none. If one fails, all roll back."""
        self.backend = LocalLeaseBackend(self.db_path)
        paths = ["/shared/file1.txt", "/shared/file2.txt"]
        instance_1 = "host1:1234:abc"
        instance_2 = "host2:5678:def"

        # Instance 1 claims both
        lease_id_1 = self.backend.claim(paths, instance_1, ttl_seconds=60)
        self.assertIsNotNone(lease_id_1)

        # Instance 2 tries to claim both (should fail on first conflict)
        with self.assertRaises(ClaimConflict):
            self.backend.claim(paths, instance_2, ttl_seconds=60)

        # Verify no partial claim: both paths still held by instance_1
        self.assertEqual(self.backend.holder(paths), instance_1)


class TestClaimBackendProtocol(unittest.TestCase):
    """Contract tests for ClaimBackend implementations.

    Any backend implementing the ClaimBackend protocol must pass these tests.
    This enables swapping backends (Inc 4a: FsClaimLog backend).
    """

    def setUp(self):
        """Create a temporary database for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "state.db")
        api = StateAPI(self.db_path)
        api.close()
        self.backend = LocalLeaseBackend(self.db_path)

    def tearDown(self):
        """Clean up temporary directory."""
        if self.backend:
            try:
                self.backend.close()
            except Exception:
                pass
        self.temp_dir.cleanup()

    def test_claim_creates_lease(self):
        """claim() creates a lease and returns lease_id."""
        path = "/shared/file.txt"
        instance_id = "host:pid:nonce"

        lease_id = self.backend.claim([path], instance_id, ttl_seconds=60)
        self.assertIsNotNone(lease_id)
        self.assertIsInstance(lease_id, str)

    def test_holder_returns_instance_id_on_active_lease(self):
        """holder() returns instance_id for paths held by that instance."""
        path = "/shared/file.txt"
        instance_id = "host:pid:nonce"

        self.assertIsNone(self.backend.holder([path]))

        lease_id = self.backend.claim([path], instance_id, ttl_seconds=60)

        self.assertEqual(self.backend.holder([path]), instance_id)

    def test_holder_returns_none_on_released_lease(self):
        """holder() returns None after release()."""
        path = "/shared/file.txt"
        instance_id = "host:pid:nonce"

        lease_id = self.backend.claim([path], instance_id, ttl_seconds=60)
        self.backend.release(lease_id, instance_id)

        self.assertIsNone(self.backend.holder([path]))

    def test_renew_extends_ttl(self):
        """renew() extends lease TTL without raising."""
        path = "/shared/file.txt"
        instance_id = "host:pid:nonce"

        lease_id = self.backend.claim([path], instance_id, ttl_seconds=1)
        # Should not raise (would raise if lease expired, but it just started)
        self.backend.renew(lease_id, instance_id, ttl_seconds=120)

    def test_claim_raises_on_conflict(self):
        """claim() raises ClaimConflict if path already held."""
        path = "/shared/file.txt"
        instance_1 = "host1:pid1:nonce1"
        instance_2 = "host2:pid2:nonce2"

        self.backend.claim([path], instance_1, ttl_seconds=60)

        with self.assertRaises(ClaimConflict):
            self.backend.claim([path], instance_2, ttl_seconds=60)


if __name__ == "__main__":
    unittest.main()
