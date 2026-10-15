"""F-04 red/green: wave_loop file claims must share the multi_dispatch keyspace.

wave_loop claims items by slug in the `claims` stream. multi_dispatch claims
by file path: the advisory path (multibox off) writes the `instances` stream,
the atomic path (multibox on) writes the LeaseStore `leases` table. A file
wave_loop has claimed must be refused to multi_dispatch on whichever keyspace
is active, and wave_loop must honour multibox.enabled when choosing it.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO / "driver"), str(REPO / "tools"), str(REPO / "state_store"), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wave_loop  # noqa: E402
from state_store import StateAPI  # noqa: E402
from state_store.claim_backend import ClaimConflict  # noqa: E402
from tools import multi_dispatch  # noqa: E402
from tools.multibox_config import build_backend  # noqa: E402


def _ctx(state_dir):
    return wave_loop._ClaimContext(
        state_dir=state_dir,
        instance_id="wave-test-%d" % os.getpid(),
        ttl=60,
    )


class TestClaimKeyspaceAdvisoryPath(unittest.TestCase):
    """multibox.enabled=false (the shipped default)."""

    def test_wave_loop_claim_is_visible_to_multi_dispatch_advisory_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(tmp)
            try:
                self.assertTrue(ctx.acquire("item-A", ["a.py"]))
                store = StateAPI(str(Path(tmp) / "state.db"))
                try:
                    conflict = multi_dispatch.check_conflict(store, ["a.py"], "other-instance")
                finally:
                    store.close()
            finally:
                ctx.release_all()
        self.assertIsNotNone(
            conflict,
            "multi_dispatch advisory check did not see the wave_loop claim on a.py",
        )

    def test_release_frees_the_advisory_keyspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(tmp)
            self.assertTrue(ctx.acquire("item-A", ["a.py"]))
            ctx.release_all()
            store = StateAPI(str(Path(tmp) / "state.db"))
            try:
                conflict = multi_dispatch.check_conflict(store, ["a.py"], "other-instance")
            finally:
                store.close()
        self.assertIsNone(conflict)


class TestClaimKeyspaceAtomicPath(unittest.TestCase):
    """multibox.enabled=true: the atomic ClaimBackend must refuse the file."""

    def test_wave_loop_claim_refuses_multibox_backend_claim_on_same_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"AESOP_MULTIBOX_ENABLED": "1"}
        ):
            db = str(Path(tmp) / "state.db")
            ctx = _ctx(tmp)
            backend = None
            try:
                self.assertTrue(ctx.acquire("item-A", ["a.py"]))
                backend = build_backend(db, None, repo_root=str(REPO))
                self.assertIsNotNone(backend)
                with self.assertRaises(ClaimConflict):
                    backend.claim(["a.py"], "other-instance", ttl_seconds=60)
            finally:
                ctx.release_all()
                if backend is not None:
                    backend.close()


class _UnreadableClaimsStore:
    """Claims store whose read path fails (locked/corrupt instances DB)."""

    db_path = "unreadable-claims-test.db"

    def get(self, stream):
        raise OSError("claims store unreadable (test)")

    def read(self, stream):
        raise OSError("claims store unreadable (test)")


class TestClaimReadFailClosed(unittest.TestCase):
    """A claims-store read error must never read as 'no conflict'."""

    def test_advisory_check_reports_unknown_not_clean_on_read_error(self):
        ip = wave_loop.instance_projection
        self.assertIsNotNone(ip, "instance_projection must import for the advisory path")
        with self.assertRaises(ip.ClaimStateUnknown):
            wave_loop._advisory_file_conflict(_UnreadableClaimsStore(), ["a.py"], "me")

    def test_acquire_does_not_grant_claim_when_claim_state_unknown(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"AESOP_MULTIBOX_ENABLED": "0"}
        ):
            ctx = _ctx(tmp)
            try:
                with mock.patch.object(ctx, "_event_store", return_value=_UnreadableClaimsStore()):
                    try:
                        granted = ctx.acquire("item-B", ["a.py"])
                    except Exception:
                        granted = None  # refused by raising: fail-closed
                self.assertIsNot(granted, True, "claim granted on unknown claim state")
            finally:
                ctx.release_all()


if __name__ == "__main__":
    unittest.main()
