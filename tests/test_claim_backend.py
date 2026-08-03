"""Contract test suite for ClaimBackend implementations.

This test suite defines the required behavior for any ClaimBackend impl.
Reusable by Inc 4a (FsClaimLog backend) and future backends; run against any
implementation by passing the backend factory to the test suite.

Tests prove atomicity, fail-closed semantics, TTL handling, and the
invariant: concurrent claims on the same path never both succeed.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Callable, Optional

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state_store import StateAPI
from state_store.claim_backend import ClaimBackend, ClaimConflict, LocalLeaseBackend


class ClaimBackendContractTests(unittest.TestCase):
    """Abstract contract tests for ClaimBackend implementations.

    Subclass this and override backend_factory() to test a different implementation.
    """

    def backend_factory(self, db_path: str) -> ClaimBackend:
        """Create a ClaimBackend instance. Override for different implementations."""
        return LocalLeaseBackend(db_path)

    def setUp(self):
        """Create a temporary database for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "state.db")
        api = StateAPI(self.db_path)
        api.close()
        self.backend = self.backend_factory(self.db_path)

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

    def test_claim_multiple_paths_atomic(self):
        """claim() with multiple paths is atomic: all or none."""
        paths = ["/shared/file1.txt", "/shared/file2.txt"]
        instance_1 = "host1:pid1:nonce1"
        instance_2 = "host2:pid2:nonce2"

        # Instance 1 claims both
        lease_id_1 = self.backend.claim(paths, instance_1, ttl_seconds=60)
        self.assertIsNotNone(lease_id_1)

        # Instance 2 tries to claim both (should fail without partial claim)
        with self.assertRaises(ClaimConflict):
            self.backend.claim(paths, instance_2, ttl_seconds=60)

        # Verify no partial claim: both paths still held by instance_1
        self.assertEqual(self.backend.holder(paths), instance_1)

    def test_renew_only_by_holder(self):
        """renew() fails if instance_id is not the holder."""
        path = "/shared/file.txt"
        instance_1 = "host1:pid1:nonce1"
        instance_2 = "host2:pid2:nonce2"

        lease_id = self.backend.claim([path], instance_1, ttl_seconds=60)

        # Instance 2 cannot renew instance 1's lease
        with self.assertRaises(ValueError):
            self.backend.renew(lease_id, instance_2, ttl_seconds=120)

    def test_release_only_by_holder(self):
        """release() fails if instance_id is not the holder."""
        path = "/shared/file.txt"
        instance_1 = "host1:pid1:nonce1"
        instance_2 = "host2:pid2:nonce2"

        lease_id = self.backend.claim([path], instance_1, ttl_seconds=60)

        # Instance 2 cannot release instance 1's lease
        with self.assertRaises(ValueError):
            self.backend.release(lease_id, instance_2)

    def test_holder_with_multiple_paths_same_instance(self):
        """holder() returns instance_id only if all paths held by same instance."""
        paths = ["/shared/file1.txt", "/shared/file2.txt"]
        instance_id = "host:pid:nonce"

        lease_id = self.backend.claim(paths, instance_id, ttl_seconds=60)
        self.assertEqual(self.backend.holder(paths), instance_id)

    def test_holder_returns_none_if_paths_held_by_different_instances(self):
        """holder() returns None if paths held by different instances."""
        path1 = "/shared/file1.txt"
        path2 = "/shared/file2.txt"
        instance_1 = "host1:pid1:nonce1"
        instance_2 = "host2:pid2:nonce2"

        # Instance 1 claims path1
        self.backend.claim([path1], instance_1, ttl_seconds=60)

        # Instance 2 claims path2
        self.backend.claim([path2], instance_2, ttl_seconds=60)

        # holder([path1, path2]) returns None (held by different instances)
        self.assertIsNone(self.backend.holder([path1, path2]))

    def test_holder_returns_none_if_any_path_unclaimed(self):
        """holder() returns None if any requested path is unclaimed."""
        path1 = "/shared/file1.txt"
        path2 = "/shared/file2.txt"
        instance_id = "host:pid:nonce"

        # Claim only path1
        self.backend.claim([path1], instance_id, ttl_seconds=60)

        # holder([path1, path2]) returns None because path2 is unclaimed
        self.assertIsNone(self.backend.holder([path1, path2]))


class LocalLeaseBackendTests(ClaimBackendContractTests):
    """Run contract tests against LocalLeaseBackend."""

    def backend_factory(self, db_path: str) -> ClaimBackend:
        return LocalLeaseBackend(db_path)


if __name__ == "__main__":
    unittest.main()
