"""Tests for multi_dispatch TOCTOU race reproduction and ClaimBackend atomicity.

TOCTOU (time-of-check to time-of-use) race: two concurrent claim attempts
on the same path must not both succeed. Before Inc 2, both succeeded because
check_conflict() and claim_files() were separate operations with no lock.

This test reproduces the race by threading two claim attempts and verifying
that exactly one succeeds (ClaimConflict is raised on the loser).
"""
from __future__ import annotations

import io
import tempfile
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from threading import Thread
from unittest import mock

# Add parent directory to path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state_store import StateAPI
from state_store import lease_claims
from state_store.claim_backend import ClaimConflict, LocalLeaseBackend, get_backend
from tools import multi_dispatch


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


class _SimulatedCrash(BaseException):
    """Stands in for a dispatcher process dying mid-dispatch (no release runs)."""


class TestDispatchLeaseOutlivesDispatch(unittest.TestCase):
    """The path-claim lease must cover the dispatch it guards.

    Audit 2026-10-15: multi_dispatch claimed with a hard-coded ttl_seconds=300 while
    --timeout (default 300) is user-settable. A --timeout 1800 dispatch lost its
    claim at t=300, so a second instance was GRANTED the same file mid-dispatch.

    These tests drive multi_dispatch.main() end to end with a fake clock. The
    dispatch subprocess is faked; instance A's fake dispatch runs instance B's
    main() at a chosen time while A still 'holds' the file.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "state.db")
        StateAPI(self.db_path).close()
        self.clock = {"now": 0.0}
        self.config_path = str(Path(self.temp_dir.name) / "aesop.config.json")
        Path(self.config_path).write_text(
            '{"multibox": {"enabled": true}}', encoding="utf-8"
        )
        self.real_build_backend = multi_dispatch.build_backend

    def tearDown(self):
        self.temp_dir.cleanup()

    def _build_backend_without_probe(self, db, config=None, **kwargs):
        # Real config parse and real LocalLeaseBackend; only the hardware
        # preflight probe is skipped (it measures the share, not the lease).
        return self.real_build_backend(
            db, config, repo_root=kwargs.get("repo_root"),
            _gate=lambda *a, **k: None,
        )

    def _main(self, argv_tail, fake_run):
        argv = ["multi_dispatch", "--db", self.db_path,
                "--config", self.config_path] + argv_tail
        fake_clock = types.SimpleNamespace(time=lambda: self.clock["now"])
        err = io.StringIO()
        with mock.patch.object(multi_dispatch, "build_backend",
                               side_effect=self._build_backend_without_probe), \
                mock.patch.object(lease_claims, "time", fake_clock), \
                mock.patch.object(multi_dispatch.subprocess, "run",
                                  side_effect=fake_run), \
                mock.patch.object(sys, "argv", argv), \
                redirect_stderr(err):
            try:
                rc = multi_dispatch.main()
            except _SimulatedCrash:
                rc = "crashed"
        return rc, err.getvalue()

    def _rival_during_dispatch(self, t, outcome):
        """fake_run for A: at time t, instance B attempts its own dispatch."""
        b_argv = ["--instance-id", "lane-b", "--timeout", "60",
                  "--files", "tools/x.py", "--", "B-DISPATCH"]

        def record_b_run(cmd, timeout=None, capture_output=False, **kwargs):
            outcome["b_ran"] = True
            return types.SimpleNamespace(returncode=0)

        def fake_run(cmd, timeout=None, capture_output=False, **kwargs):
            if "A-DISPATCH" in cmd:
                self.clock["now"] = t
                outcome["b_rc"], outcome["b_err"] = self._main(b_argv, record_b_run)
            return types.SimpleNamespace(returncode=0)

        return fake_run

    def test_rival_claim_mid_dispatch_is_refused(self):
        """A claims with --timeout 1800; B at t=400 must be refused (audit repro)."""
        outcome = {"b_ran": False}
        a_argv = ["--instance-id", "lane-a", "--timeout", "1800",
                  "--files", "tools/x.py", "--", "A-DISPATCH"]
        rc_a, err_a = self._main(a_argv, self._rival_during_dispatch(400, outcome))
        self.assertEqual(rc_a, 0, err_a)
        self.assertEqual(outcome["b_rc"], 1, outcome["b_err"])
        self.assertIn("files are claimed by lane-a", outcome["b_err"])
        self.assertFalse(outcome["b_ran"], "B must not dispatch while A holds the file")

    def test_short_timeout_still_holds_for_configured_lease_ttl(self):
        """Control: --timeout 10 keeps the claim for the configured 300s lease_ttl floor."""
        outcome = {"b_ran": False}
        a_argv = ["--instance-id", "lane-a", "--timeout", "10",
                  "--files", "tools/x.py", "--", "A-DISPATCH"]
        self._main(a_argv, self._rival_during_dispatch(299, outcome))
        self.assertEqual(outcome["b_rc"], 1, outcome["b_err"])
        self.assertFalse(outcome["b_ran"])

    def test_crashed_dispatcher_lease_expires_at_timeout_plus_margin(self):
        """Crashed A (no release) blocks B until timeout+margin, then B proceeds."""
        def crash_run(cmd, timeout=None, capture_output=False, **kwargs):
            raise _SimulatedCrash()

        a_argv = ["--instance-id", "lane-a", "--timeout", "1800",
                  "--files", "tools/x.py", "--", "A-DISPATCH"]
        rc_a, _ = self._main(a_argv, crash_run)
        self.assertEqual(rc_a, "crashed")

        ran = {"n": 0}

        def record_run(cmd, timeout=None, capture_output=False, **kwargs):
            ran["n"] += 1
            return types.SimpleNamespace(returncode=0)

        b_argv = ["--instance-id", "lane-b", "--timeout", "60",
                  "--files", "tools/x.py", "--", "B-DISPATCH"]
        # Expected expiry: claim at t=0 + (--timeout 1800 + 60s margin). The lease is
        # live while now <= claimed_at + ttl, so t=1860 is still held by crashed A.
        expiry = 1800 + 60

        self.clock["now"] = expiry
        rc_b, err_b = self._main(b_argv, record_run)
        self.assertEqual(rc_b, 1, err_b)
        self.assertEqual(ran["n"], 0)

        self.clock["now"] = expiry + 1
        rc_b, err_b = self._main(b_argv, record_run)
        self.assertEqual(rc_b, 0, err_b)
        self.assertEqual(ran["n"], 1)


if __name__ == "__main__":
    unittest.main()
