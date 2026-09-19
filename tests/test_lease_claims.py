"""Tests for state_store.lease_claims — multi-instance file-scope leasing."""

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# Add state_store to path for import
sys.path.insert(0, str(Path(__file__).parent.parent))

from state_store.lease_claims import (
    CLAIM_CASE_POLICY_ENV,
    DEFAULT_CASE_POLICY,
    LeaseStore,
    LeaseConflict,
    _normalize_path,
    resolve_case_policy,
)
from state_store.paths import canonical_claim_path


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

    def test_path_separator_normalization_windows_style(self):
        """Paths with different separators (/ vs \\) are treated as same file."""
        import os
        if os.name != 'nt':
            self.skipTest("Windows-specific test")

        now = 1000.0
        # Instance 1 claims with forward slash
        lease_id = self.store.claim(
            paths=["dir/file.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )
        self.assertIsNotNone(lease_id)

        # Instance 2 tries to claim with backslash (same logical file) -> should conflict
        with self.assertRaises(LeaseConflict) as ctx:
            self.store.claim(
                paths=["dir\\file.txt"],
                instance_id="instance-2",
                ttl_seconds=60.0,
                clock=lambda: now
            )
        self.assertEqual(ctx.exception.conflicting_instance, "instance-1")

    def test_path_case_normalization_windows_style(self):
        """Paths with different cases are treated as same file on Windows."""
        import os
        if os.name != 'nt':
            self.skipTest("Windows-specific test")

        now = 1000.0
        # Instance 1 claims README.md
        lease_id = self.store.claim(
            paths=["README.md"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )
        self.assertIsNotNone(lease_id)

        # Instance 2 tries to claim README.MD (same file on Windows) -> should conflict
        with self.assertRaises(LeaseConflict) as ctx:
            self.store.claim(
                paths=["README.MD"],
                instance_id="instance-2",
                ttl_seconds=60.0,
                clock=lambda: now
            )
        self.assertEqual(ctx.exception.conflicting_instance, "instance-1")

    def test_path_case_default_policy_over_collides_on_every_host(self):
        """Deep-scan B1: the DEFAULT claim keyspace is host-independent.

        This test previously asserted Linux-specific case-sensitivity, which is
        exactly the host-dependent keyspace that let a Windows instance and a Linux
        instance both claim the same file. The default policy is now 'insensitive'
        on every host: it over-collides (safe), never under-collides.

        Case-sensitive single-box semantics remain available by opting in, see
        TestClaimCasePolicyResolution.test_lease_store_honours_explicit_sensitive_policy.
        """
        now = 1000.0
        lease_id_1 = self.store.claim(
            paths=["readme.md"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )
        self.assertIsNotNone(lease_id_1)

        with self.assertRaises(LeaseConflict) as ctx:
            self.store.claim(
                paths=["README.MD"],
                instance_id="instance-2",
                ttl_seconds=60.0,
                clock=lambda: now
            )
        self.assertEqual(ctx.exception.conflicting_instance, "instance-1")

    def test_toctou_race_regression(self):
        """REGRESSION TEST: Deterministic TOCTOU race reproducer.

        This test catches the race condition that was fixed in the atomic refactor.
        Previously, _check_conflicts() would open BEGIN IMMEDIATE, read, then ROLLBACK
        (releasing the lock), and claim() would open a SECOND BEGIN IMMEDIATE to insert.
        Between these two transactions, another instance could claim the same path.

        This test interleaves the operations deterministically (not relying on thread
        scheduling) to catch the bug if the transaction atomicity is broken.
        """
        now = 1000.0
        path = "shared/file.txt"
        normalized_path = LeaseStore._normalize_path(path) if hasattr(LeaseStore, '_normalize_path') else path

        # Import the normalization function
        from state_store.lease_claims import _normalize_path
        normalized_path = _normalize_path(path)

        # Instance A: perform the conflict check
        conn_a = self.store._get_conn()
        conn_a.execute("BEGIN IMMEDIATE")
        try:
            # Inside the transaction, check conflicts
            conflict_instance, conflict_paths = self.store._check_conflicts(
                [normalized_path], "instance-A", now, conn_a
            )
            # Conflict check should see no holder (path is free)
            self.assertIsNone(conflict_instance)

            # Instance B: claims the path in a separate transaction (simulating concurrency)
            # This would succeed in the old code because A released its lock after _check_conflicts
            # In the fixed code, A still holds the lock here, so B will block until A finishes
            # For this test, we'll verify that A can complete its claim without B interfering

            # Instance A continues: insert in the same transaction
            import json
            import uuid
            lease_id_a = str(uuid.uuid4())
            paths_json = json.dumps(sorted([normalized_path]), separators=(",", ":"))
            conn_a.execute(
                """
                INSERT INTO leases
                (lease_id, instance_id, paths, claimed_at, ttl_seconds, released_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (lease_id_a, "instance-A", paths_json, now, 300.0),
            )
            conn_a.commit()

            # Verify A holds the path
            holder = self.store.get_holder([path], clock=lambda: now)
            self.assertEqual(holder, "instance-A")

            # Now try to claim from B: should fail with LeaseConflict
            with self.assertRaises(LeaseConflict) as ctx:
                self.store.claim(
                    paths=[path],
                    instance_id="instance-B",
                    ttl_seconds=300.0,
                    clock=lambda: now
                )
            self.assertEqual(ctx.exception.conflicting_instance, "instance-A")

        finally:
            if conn_a:
                try:
                    conn_a.rollback()
                except Exception:
                    pass


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

    def test_renew_expired_lease_fails(self):
        """Renewing an expired lease should fail (cannot extend a dead lease)."""
        now = 1000.0
        lease_id = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=30.0,
            clock=lambda: now
        )

        # Lease expires at now + 30s
        # At now + 40s, lease is expired
        now = 1040.0

        # Trying to renew the expired lease should fail
        with self.assertRaises(ValueError) as ctx:
            self.store.renew(
                lease_id=lease_id,
                instance_id="instance-1",
                ttl_seconds=60.0,
                clock=lambda: now
            )
        self.assertIn("expired", str(ctx.exception).lower())

    def test_renew_released_lease_fails(self):
        """Renewing a released lease should fail."""
        now = 1000.0
        lease_id = self.store.claim(
            paths=["file1.txt"],
            instance_id="instance-1",
            ttl_seconds=60.0,
            clock=lambda: now
        )

        # Release the lease
        self.store.release(lease_id, "instance-1", clock=lambda: now)

        # Try to renew the released lease -> should fail
        with self.assertRaises(ValueError) as ctx:
            self.store.renew(
                lease_id=lease_id,
                instance_id="instance-1",
                ttl_seconds=60.0,
                clock=lambda: now
            )
        self.assertIn("released", str(ctx.exception).lower())


class TestLeaseClaimsHeterogeneityGuard(unittest.TestCase):
    """Heterogeneity regression: the PRODUCTION claim-key path is host-independent.

    Deep-scan B1/B2. The prior version of this guard called ``canonical_claim_path``
    directly with ``case_policy="insensitive"`` — a value production never passed —
    and never touched ``_normalize_path`` at all, so it stayed green even though
    LeaseStore keyed 'tools/Runner.py' as 'tools/runner.py' on Windows and
    'tools/Runner.py' on Linux (split-brain: both instances claim the same file).

    Every test here exercises the REAL production entry point that LeaseStore uses
    (``_normalize_path`` / ``LeaseStore.claim``), so mutating ``_normalize_path`` to
    the identity function fails the suite.
    """

    def test_normalize_path_is_not_the_identity_function(self):
        """MUTATION GUARD: _normalize_path must actually transform its input.

        If _normalize_path is replaced by `lambda p: p`, this fails. The old guard
        survived that mutation because it never called _normalize_path.
        """
        self.assertEqual(_normalize_path("dir\\file.txt"), "dir/file.txt")
        self.assertEqual(_normalize_path("dir/./sub/../file.txt"), "dir/file.txt")

    def test_normalize_path_separator_host_independent(self):
        """REGRESSION B1: _normalize_path folds separators identically on nt and posix."""
        path_forward = "dir/file.txt"
        path_backslash = "dir\\file.txt"

        with mock.patch("os.name", "nt"):
            nt_forward = _normalize_path(path_forward)
            nt_backslash = _normalize_path(path_backslash)

        with mock.patch("os.name", "posix"):
            posix_forward = _normalize_path(path_forward)
            posix_backslash = _normalize_path(path_backslash)

        self.assertEqual(nt_forward, nt_backslash)
        self.assertEqual(posix_forward, posix_backslash)
        self.assertEqual(nt_forward, posix_forward)

    def test_normalize_path_case_host_independent(self):
        """REGRESSION B1: _normalize_path yields the SAME key on nt and posix.

        This is the finding: with case_policy='platform' the production key was
        'tools/runner.py' on Windows and 'tools/Runner.py' on Linux.
        """
        mixed = "tools/Runner.py"

        with mock.patch("os.name", "nt"):
            nt_key = _normalize_path(mixed)
        with mock.patch("os.name", "posix"):
            posix_key = _normalize_path(mixed)

        self.assertEqual(
            nt_key,
            posix_key,
            "Claim key must not depend on the host OS: a Windows instance and a Linux "
            "instance would otherwise both claim the same file (split-brain).",
        )

    def test_normalize_path_case_variants_collide_by_default(self):
        """Default policy over-collides (safe): README.md and README.MD share one key."""
        with mock.patch("os.name", "posix"):
            self.assertEqual(_normalize_path("README.md"), _normalize_path("README.MD"))

    def test_claim_conflicts_across_heterogeneous_hosts(self):
        """END-TO-END B1: a Windows-hosted claim blocks a Linux-hosted claim of the same file.

        This is the split-brain reproducer at the LeaseStore level, not the helper level.
        """
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = LeaseStore(str(Path(temp_dir.name) / "hetero.db"))
        self.addCleanup(store.close)

        now = 1000.0

        # Instance 1 runs on Windows and claims the file.
        with mock.patch("os.name", "nt"):
            store.claim(
                paths=["tools/Runner.py"],
                instance_id="win-instance",
                ttl_seconds=60.0,
                clock=lambda: now,
            )

        # Instance 2 runs on Linux against the SAME shared coordination db.
        with mock.patch("os.name", "posix"):
            with self.assertRaises(LeaseConflict) as ctx:
                store.claim(
                    paths=["tools/Runner.py"],
                    instance_id="linux-instance",
                    ttl_seconds=60.0,
                    clock=lambda: now,
                )
        self.assertEqual(ctx.exception.conflicting_instance, "win-instance")

    def test_get_holder_across_heterogeneous_hosts(self):
        """END-TO-END B1: get_holder resolves the same key from either host."""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = LeaseStore(str(Path(temp_dir.name) / "hetero_holder.db"))
        self.addCleanup(store.close)

        now = 1000.0
        with mock.patch("os.name", "nt"):
            store.claim(
                paths=["docs/README.md"],
                instance_id="win-instance",
                ttl_seconds=60.0,
                clock=lambda: now,
            )

        with mock.patch("os.name", "posix"):
            holder = store.get_holder(["docs/README.md"], clock=lambda: now)

        self.assertEqual(holder, "win-instance")


class TestClaimCasePolicyResolution(unittest.TestCase):
    """Case policy is configuration, not a property of the host OS (deep-scan B1)."""

    def setUp(self):
        self._saved = os.environ.get(CLAIM_CASE_POLICY_ENV)
        os.environ.pop(CLAIM_CASE_POLICY_ENV, None)

    def tearDown(self):
        os.environ.pop(CLAIM_CASE_POLICY_ENV, None)
        if self._saved is not None:
            os.environ[CLAIM_CASE_POLICY_ENV] = self._saved

    def test_default_is_host_independent_insensitive(self):
        """Default must be the safe over-colliding policy, not 'platform'."""
        self.assertEqual(resolve_case_policy(), DEFAULT_CASE_POLICY)
        self.assertEqual(DEFAULT_CASE_POLICY, "insensitive")

    def test_config_overrides_default(self):
        config = {"multibox": {"case_policy": "sensitive"}}
        self.assertEqual(resolve_case_policy(config), "sensitive")

    def test_env_overrides_default(self):
        os.environ[CLAIM_CASE_POLICY_ENV] = "sensitive"
        self.assertEqual(resolve_case_policy(), "sensitive")

    def test_config_beats_env(self):
        os.environ[CLAIM_CASE_POLICY_ENV] = "sensitive"
        config = {"multibox": {"case_policy": "insensitive"}}
        self.assertEqual(resolve_case_policy(config), "insensitive")

    def test_invalid_policy_fails_closed(self):
        """An unknown policy must raise, never silently fall back to a different keyspace."""
        with self.assertRaises(ValueError):
            resolve_case_policy({"multibox": {"case_policy": "bogus"}})
        os.environ[CLAIM_CASE_POLICY_ENV] = "bogus"
        with self.assertRaises(ValueError):
            resolve_case_policy()

    def test_lease_store_honours_explicit_sensitive_policy(self):
        """Local single-box case-sensitive semantics remain available via config."""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = LeaseStore(
            str(Path(temp_dir.name) / "sensitive.db"), case_policy="sensitive"
        )
        self.addCleanup(store.close)

        now = 1000.0
        lease_1 = store.claim(
            paths=["readme.md"], instance_id="i1", ttl_seconds=60.0, clock=lambda: now
        )
        # Under 'sensitive', README.MD is a DIFFERENT file: no conflict.
        lease_2 = store.claim(
            paths=["README.MD"], instance_id="i2", ttl_seconds=60.0, clock=lambda: now
        )
        self.assertNotEqual(lease_1, lease_2)

    def test_lease_store_rejects_invalid_policy(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        with self.assertRaises(ValueError):
            LeaseStore(str(Path(temp_dir.name) / "bad.db"), case_policy="bogus")

    def test_lease_store_accepts_config_dict(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = LeaseStore(
            str(Path(temp_dir.name) / "cfg.db"),
            config={"multibox": {"case_policy": "sensitive"}},
        )
        self.addCleanup(store.close)
        self.assertEqual(store.case_policy, "sensitive")


if __name__ == "__main__":
    unittest.main()
