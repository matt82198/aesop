"""Durability, clock skew and GC for state_store.fs_claim_log (multibox Inc 4b).

Inc 4a shipped the pure fold and the settle-window protocol. Inc 4b hardens the
three things 4a deliberately left open, each tested here in its own layer:

1. TestDurableAppend   -- a record becomes visible to a peer ONLY once its bytes
                          are durable. Asserted as a CALL ORDER over a spying
                          ``os`` shim: fsync(file) strictly before replace(),
                          and the parent-directory sync strictly after it.
                          Nothing here asserts that a real fsync did anything --
                          on tmpfs it cannot, which is exactly why the proof is
                          the ordering and not the effect.
2. TestClockSkewMatrix -- the four-cell skew matrix. ``max_skew`` is only ever
                          ADDED, so a bounded clock disagreement can stall
                          throughput but can NEVER expire a live lease early.
                          The past-bound cell is the falsifiability case: it
                          proves the bound is load-bearing rather than
                          decorative, and shows the writer-side guard is what
                          keeps that configuration unreachable.
3. TestCompactGc       -- ``compact(retain_seconds)`` never deletes a live or an
                          unprovable record, is idempotent, and never changes
                          the answer the fold gives.

Hermetic: tempdirs only, no cwd pollution, no network, no real sleeps, and an
injected clock everywhere.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from state_store import fs_claim_log as fcl  # noqa: E402
from state_store.fs_claim_log import (  # noqa: E402
    FS_UNKNOWN_PATH,
    ClockSkewError,
    FsClaimLog,
    fold_fs_claims,
)


class _Clock:
    """Deterministic, injectable clock (same shape as the Inc 4a helper)."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


# ---------------------------------------------------------------------------
# 1. Durability -- fsync/replace call order
# ---------------------------------------------------------------------------

#: os functions whose invocation order is the durability contract.
_WATCHED = ("fsync", "replace", "open", "close", "remove")

#: Sentinel fd handed back by the spy when it fakes a successful directory open.
_FAKE_DIR_FD = 987654321


class _OsSpy:
    """Recording proxy over the real ``os`` module.

    Every attribute not in ``_WATCHED`` is delegated untouched, so the module
    under test keeps working (``listdir``, ``path``, ``O_RDONLY``, ...). The
    watched calls are appended to ``calls`` in invocation order.

    ``dir_open`` decides how a directory open behaves, which is the ONLY
    platform difference in the durability path:

    * ``"posix"``  -- succeeds, returns a sentinel fd; fsync/close on that fd are
      no-ops. Lets the full POSIX sequence be exercised deterministically on any
      host, including Windows.
    * ``"refuse"`` -- raises PermissionError, exactly as Windows does when asked
      to open a directory as a file.
    """

    def __init__(self, real, dir_open="posix"):
        self._real = real
        self._dir_open = dir_open
        self.calls = []

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if name not in _WATCHED:
            return attr

        def wrapper(*args, **kwargs):
            self.calls.append(name)
            if name == "open" and args and os.path.isdir(str(args[0])):
                if self._dir_open == "refuse":
                    raise PermissionError(13, "Permission denied", str(args[0]))
                return _FAKE_DIR_FD
            if name in ("fsync", "close") and args and args[0] == _FAKE_DIR_FD:
                return None
            return attr(*args, **kwargs)

        return wrapper


class TestDurableAppend(unittest.TestCase):
    """A record must be durable BEFORE its name is published to peers."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.claims_dir = str(Path(self.temp_dir.name) / "claims")
        self.clock = _Clock(1000.0)
        self.log = FsClaimLog(
            self.claims_dir,
            clock=self.clock,
            settle_seconds=0.0,
            max_skew_seconds=0.0,
        )

    def tearDown(self):
        self.log.close()
        self.temp_dir.cleanup()

    def _claim_with_spy(self, dir_open="posix"):
        spy = _OsSpy(os, dir_open=dir_open)
        real_os = fcl.os
        fcl.os = spy
        try:
            lease_id = self.log.claim(["a/b.txt"], "inst-1", ttl_seconds=60)
        finally:
            fcl.os = real_os
        return spy, lease_id

    def test_fsync_of_file_precedes_replace(self):
        """The load-bearing ordering: bytes durable, THEN the name appears."""
        spy, _ = self._claim_with_spy()
        self.assertIn("fsync", spy.calls)
        self.assertIn("replace", spy.calls)
        self.assertLess(
            spy.calls.index("fsync"),
            spy.calls.index("replace"),
            "a peer could see a record name whose bytes are not yet durable",
        )

    def test_parent_dir_sync_follows_replace(self):
        """The directory ENTRY is synced after the entry exists, not before."""
        spy, _ = self._claim_with_spy()
        replace_at = spy.calls.index("replace")
        self.assertIn("open", spy.calls[replace_at:])
        self.assertNotIn(
            "open", spy.calls[:replace_at],
            "the parent-dir sync must not run before os.replace",
        )

    def test_full_posix_call_order(self):
        """Exact sequence on a platform that permits directory fsync."""
        spy, _ = self._claim_with_spy(dir_open="posix")
        self.assertEqual(
            spy.calls, ["fsync", "replace", "open", "fsync", "close"]
        )

    def test_record_still_written_when_platform_refuses_dir_fsync(self):
        """Windows refuses to open a directory; the append must still succeed."""
        spy, lease_id = self._claim_with_spy(dir_open="refuse")
        self.assertEqual(spy.calls, ["fsync", "replace", "open"])
        self.assertEqual(self.log.holder(["a/b.txt"]), "inst-1")
        self.assertTrue(lease_id)

    def test_no_temp_file_survives_a_completed_append(self):
        self.log.claim(["a/b.txt"], "inst-1", ttl_seconds=60)
        names = sorted(p.name for p in Path(self.claims_dir).iterdir())
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith(".json"))

    def test_temp_name_lives_in_the_same_directory_as_its_final_name(self):
        """os.replace is only atomic within one filesystem."""
        seen = {}
        real_replace = os.replace

        def spy_replace(src, dst):
            seen["src"] = Path(src)
            seen["dst"] = Path(dst)
            return real_replace(src, dst)

        fcl.os = _OsSpy(os)
        fcl.os.replace = spy_replace  # type: ignore[attr-defined]
        try:
            self.log.claim(["a/b.txt"], "inst-1", ttl_seconds=60)
        finally:
            fcl.os = os
        self.assertEqual(seen["src"].parent, seen["dst"].parent)
        self.assertEqual(seen["src"].parent, Path(self.claims_dir))

    def test_dir_sync_actually_succeeds_on_this_host(self):
        """Not a mock: the real platform branch must work, not merely not crash.

        POSIX takes the os.open/fsync path; Windows falls through to the ctypes
        FlushFileBuffers fallback. Either way the host must report success, which
        is what keeps the fallback from being a silent no-op forever.
        """
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        self.assertTrue(fcl._fsync_dir(self.claims_dir))

    def test_dir_sync_never_raises_on_a_missing_directory(self):
        missing = str(Path(self.temp_dir.name) / "no-such-dir")
        self.assertFalse(fcl._fsync_dir(missing))

    def test_temp_name_uses_the_documented_suffix(self):
        seen = {}
        real_replace = os.replace

        def spy_replace(src, dst):
            seen["src"] = str(src)
            return real_replace(src, dst)

        spy = _OsSpy(os)
        spy.replace = spy_replace  # type: ignore[attr-defined]
        fcl.os = spy
        try:
            self.log.claim(["a/b.txt"], "inst-1", ttl_seconds=60)
        finally:
            fcl.os = os
        self.assertTrue(seen["src"].endswith(fcl.TEMP_SUFFIX))
        self.assertFalse(seen["src"].endswith(".json"))

    def test_partial_temp_file_is_invisible_to_the_fold(self):
        """A crash mid-write leaves a temp file that must NOT block claims.

        The temp name is not a published record: no grant was ever made from it,
        so ignoring it is the pre-write state, not a fail-open. Contrast a
        truncated ``.json``, which WAS published and therefore blocks.
        """
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        (Path(self.claims_dir) / "000000000001-1000000-inst9-abc.json.tmp").write_text(
            '{"kind": "claim_requ', encoding="utf-8"
        )
        folded = fold_fs_claims(
            self.log._read_records(), now=self.clock(), max_skew=0.0
        )
        self.assertNotIn(FS_UNKNOWN_PATH, folded)
        self.assertEqual(self.log.claim(["a/b.txt"], "inst-1", ttl_seconds=60) != "", True)

    def test_truncated_published_record_still_blocks(self):
        """Regression guard: the temp-file rule must not weaken 4a fail-closed."""
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        (Path(self.claims_dir) / "000000000001-1000000-inst9-abc.json").write_text(
            '{"kind": "claim_requ', encoding="utf-8"
        )
        folded = fold_fs_claims(
            self.log._read_records(), now=self.clock(), max_skew=0.0
        )
        self.assertIn(FS_UNKNOWN_PATH, folded)


# ---------------------------------------------------------------------------
# 2. Clock skew -- the four-cell matrix
# ---------------------------------------------------------------------------

def _peer_request(paths, instance_id, peer_now, ttl, lamport=1):
    """A record written by a peer whose wall clock reads ``peer_now``."""
    return {
        "v": 1,
        "kind": "claim_requested",
        "paths": list(paths),
        "instance_id": instance_id,
        "epoch": 1,
        "lamport": lamport,
        "epoch_ms": int(peer_now * 1000),
        "ttl": float(ttl),
        "uuid": "u" * 8,
        "lease_id": f"lease-{instance_id}",
    }


class TestClockSkewMatrix(unittest.TestCase):
    """Skew LENGTHENS a lease; it must never shorten one.

    Setup for every cell: a peer claims at OUR time ``T0`` with ``ttl``, but its
    clock is offset by ``skew`` (peer_clock = our_clock + skew), so the record
    carries ``epoch_ms = (T0 + skew) * 1000``. The fold computes the deadline as
    ``epoch_ms/1000 + ttl + max_skew``; the TRUE local deadline is ``T0 + ttl``.
    Early expiry therefore happens iff ``skew + max_skew < 0`` -- i.e. only when
    the peer is behind by MORE than the configured bound.
    """

    T0 = 1000.0
    TTL = 60.0
    MAX_SKEW = 10.0

    def _deadline_seen_by_us(self, skew):
        rec = _peer_request(["f.txt"], "peer", self.T0 + skew, self.TTL)
        # Probe the fold at the true deadline: still held => no early expiry.
        true_deadline = self.T0 + self.TTL
        at_true = fold_fs_claims([rec], now=true_deadline, max_skew=self.MAX_SKEW)
        return rec, at_true, true_deadline

    def test_cell_peer_ahead_within_bound_no_early_expiry(self):
        _, at_true, _ = self._deadline_seen_by_us(skew=+self.MAX_SKEW / 2)
        self.assertEqual(at_true.get("f.txt"), "peer")

    def test_cell_peer_behind_within_bound_no_early_expiry(self):
        _, at_true, _ = self._deadline_seen_by_us(skew=-self.MAX_SKEW / 2)
        self.assertEqual(at_true.get("f.txt"), "peer")

    def test_cell_peer_behind_exactly_at_bound_no_early_expiry(self):
        """The boundary is inclusive: computed deadline == true deadline."""
        rec, at_true, true_deadline = self._deadline_seen_by_us(skew=-self.MAX_SKEW)
        self.assertEqual(at_true.get("f.txt"), "peer")
        computed = rec["epoch_ms"] / 1000.0 + self.TTL + self.MAX_SKEW
        self.assertAlmostEqual(computed, true_deadline, places=6)

    def test_cell_peer_behind_past_bound_is_the_falsifiable_case(self):
        """Past the bound the lease DOES expire early -- the bound is load-bearing.

        This is deliberately asserted rather than hidden: it is what makes
        ``max_skew`` a real precondition (measured by Inc 0, gated by Inc 7)
        instead of decoration.
        """
        skew = -(self.MAX_SKEW + 5.0)
        _, at_true, _ = self._deadline_seen_by_us(skew=skew)
        self.assertEqual(at_true, {})

    def test_skew_only_ever_lengthens_across_the_whole_matrix(self):
        """For every |skew| <= max_skew, computed deadline >= true deadline."""
        true_deadline = self.T0 + self.TTL
        for skew in (-self.MAX_SKEW, -1.0, 0.0, 1.0, self.MAX_SKEW):
            rec = _peer_request(["f.txt"], "peer", self.T0 + skew, self.TTL)
            computed = rec["epoch_ms"] / 1000.0 + self.TTL + self.MAX_SKEW
            self.assertGreaterEqual(
                computed + 1e-9, true_deadline,
                f"skew={skew} shortened a lease",
            )

    def test_zero_skew_config_still_never_shortens_an_unskewed_lease(self):
        rec = _peer_request(["f.txt"], "peer", self.T0, self.TTL)
        folded = fold_fs_claims([rec], now=self.T0 + self.TTL, max_skew=0.0)
        self.assertEqual(folded.get("f.txt"), "peer")


class TestWriterSideSkewGuard(unittest.TestCase):
    """The writer-side half of the bound (the fold owns the reader-side half)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.claims_dir = str(Path(self.temp_dir.name) / "claims")
        self.clock = _Clock(1000.0)
        self.log = FsClaimLog(
            self.claims_dir,
            clock=self.clock,
            settle_seconds=0.0,
            max_skew_seconds=10.0,
        )

    def tearDown(self):
        self.log.close()
        self.temp_dir.cleanup()

    def _plant_peer(self, peer_now, ttl=60.0, name="000000000001-x-peer-u.json"):
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        rec = _peer_request(["other.txt"], "peer", peer_now, ttl)
        with open(Path(self.claims_dir) / name, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)

    def test_peer_ahead_within_bound_is_accepted(self):
        self._plant_peer(self.clock() + 5.0)
        lease_id = self.log.claim(["mine.txt"], "inst-1", ttl_seconds=60)
        self.assertTrue(lease_id)

    def test_peer_ahead_exactly_at_bound_is_accepted(self):
        self._plant_peer(self.clock() + 10.0)
        lease_id = self.log.claim(["mine.txt"], "inst-1", ttl_seconds=60)
        self.assertTrue(lease_id)

    def test_peer_ahead_past_bound_fails_closed(self):
        """An unbounded disagreement makes the TTL bound meaningless: no grant."""
        self._plant_peer(self.clock() + 60.0)
        with self.assertRaises(ClockSkewError):
            self.log.claim(["mine.txt"], "inst-1", ttl_seconds=60)

    def test_skew_refusal_grants_nothing(self):
        self._plant_peer(self.clock() + 60.0)
        with self.assertRaises(ClockSkewError):
            self.log.claim(["mine.txt"], "inst-1", ttl_seconds=60)
        self.assertIsNone(self.log.holder(["mine.txt"]))

    def test_our_own_record_never_trips_the_guard(self):
        """_writer_epoch_ms may deliberately clamp OUR stamp forward.

        That is the backwards-clock guard doing its job, not evidence of skew,
        so our own records are exempt from the peer check.
        """
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        ours = _peer_request(["mine-old.txt"], "inst-1", self.clock() + 900.0, 60.0)
        with open(Path(self.claims_dir) / "000000000001-x-inst1-u.json",
                  "w", encoding="utf-8") as fh:
            json.dump(ours, fh)
        self.assertTrue(self.log.claim(["mine.txt"], "inst-1", ttl_seconds=60))
        # ...but the identical record from a PEER does trip it.
        peer = _peer_request(["theirs.txt"], "peer", self.clock() + 900.0, 60.0)
        with open(Path(self.claims_dir) / "000000000002-x-peer-u.json",
                  "w", encoding="utf-8") as fh:
            json.dump(peer, fh)
        with self.assertRaises(ClockSkewError):
            self.log.claim(["mine2.txt"], "inst-1", ttl_seconds=60)

    def test_writer_epoch_ms_never_regresses(self):
        """A backwards clock jump must not make our new lease look older.

        Clamping upward can only LENGTHEN a lease, which is the safe direction.
        """
        self.log.claim(["a.txt"], "inst-1", ttl_seconds=60)
        first = self._epoch_ms_values()[0]
        self.clock.advance(-500.0)  # NTP step backwards
        self.log.claim(["b.txt"], "inst-1", ttl_seconds=60)
        values = self._epoch_ms_values()
        self.assertEqual(len(values), 2)
        self.assertGreaterEqual(values[1], first)

    def _epoch_ms_values(self):
        out = []
        for path in sorted(Path(self.claims_dir).glob("*.json")):
            with open(path, encoding="utf-8") as fh:
                out.append(json.load(fh)["epoch_ms"])
        return out


# ---------------------------------------------------------------------------
# 3. GC -- compact(retain_seconds)
# ---------------------------------------------------------------------------

class TestCompactGc(unittest.TestCase):
    """compact() may only remove what it can PROVE is dead past the full bound."""

    TTL = 60.0
    MAX_SKEW = 10.0
    SETTLE = 5.0

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.claims_dir = str(Path(self.temp_dir.name) / "claims")
        self.clock = _Clock(1000.0)
        self.log = FsClaimLog(
            self.claims_dir,
            clock=self.clock,
            sleep=lambda _s: None,  # settle is configured but never really slept
            settle_seconds=self.SETTLE,
            max_skew_seconds=self.MAX_SKEW,
        )

    def tearDown(self):
        self.log.close()
        self.temp_dir.cleanup()

    def _files(self):
        return sorted(p.name for p in Path(self.claims_dir).glob("*.json"))

    def _kinds(self):
        kinds = []
        for name in self._files():
            with open(Path(self.claims_dir) / name, encoding="utf-8") as fh:
                kinds.append(json.load(fh).get("kind"))
        return sorted(kinds)

    # -- never delete live -------------------------------------------------

    def test_never_deletes_a_live_lease(self):
        self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        before = self._files()
        self.assertEqual(self.log.compact(), 0)
        self.assertEqual(self._files(), before)
        self.assertEqual(self.log.holder(["a.txt"]), "inst-1")

    def test_never_deletes_a_lease_still_live_only_because_of_skew(self):
        """The skewed-clock case: within ttl but only under the +max_skew bound."""
        self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        # Past the naive ttl, still inside ttl + max_skew.
        self.clock.advance(self.TTL + (self.MAX_SKEW / 2))
        self.assertEqual(self.log.compact(), 0)
        self.assertEqual(self.log.holder(["a.txt"]), "inst-1")

    def test_never_deletes_within_the_settle_margin(self):
        """Expired, but a peer may still be mid-fold on a listing that saw it."""
        self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        self.clock.advance(self.TTL + self.MAX_SKEW + (self.SETTLE / 2))
        self.assertEqual(self.log.compact(), 0)
        self.assertEqual(len(self._files()), 1)

    def test_never_deletes_a_renewed_lease_whose_request_alone_looks_expired(self):
        """Deleting the request would shrink a heartbeat-extended live lease."""
        lease_id = self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        self.clock.advance(self.TTL / 2)
        self.log.renew(lease_id, "inst-1", ttl_seconds=self.TTL)
        # Land between the request's bound (T0+70) and the heartbeat's (T0+100):
        # the request alone is collectable, the lease as a whole is not.
        self.clock.advance(50.0)
        # Request looks long dead; the heartbeat still holds the lease live.
        self.assertEqual(self.log.holder(["a.txt"]), "inst-1")
        self.assertEqual(self.log.compact(), 0)
        self.assertEqual(self.log.holder(["a.txt"]), "inst-1")

    def test_never_deletes_an_unprovable_ttl_less_legacy_record(self):
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        legacy = {
            "v": 1, "kind": "claim_requested", "paths": ["legacy.txt"],
            "instance_id": "old", "epoch": 1, "lamport": 1,
            "epoch_ms": 1000, "uuid": "l" * 8, "lease_id": "legacy-lease",
        }
        with open(Path(self.claims_dir) / "000000000001-1000-old-l.json",
                  "w", encoding="utf-8") as fh:
            json.dump(legacy, fh)
        self.clock.advance(10_000_000.0)
        self.assertEqual(self.log.compact(), 0)
        self.assertEqual(len(self._files()), 1)

    # -- does delete what it can prove -------------------------------------

    def test_deletes_a_long_expired_lease_past_the_full_bound(self):
        self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        self.clock.advance(self.TTL + self.MAX_SKEW + self.SETTLE + 1.0)
        self.assertEqual(self.log.compact(), 1)
        self.assertEqual(self._files(), [])

    def test_retain_seconds_extends_the_bound(self):
        self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        self.clock.advance(self.TTL + self.MAX_SKEW + self.SETTLE + 1.0)
        self.assertEqual(self.log.compact(retain_seconds=3600.0), 0)
        self.clock.advance(3600.0)
        self.assertEqual(self.log.compact(retain_seconds=3600.0), 1)

    def test_a_tombstoned_lease_is_still_held_to_the_full_bound(self):
        """A tombstone frees the path at FOLD time, but not at GC time.

        Collection waits for the same provable ``ttl + max_skew + settle`` bound
        as everything else, so no deletion ever depends on reasoning about how
        fresh a peer's directory listing is.
        """
        lease_id = self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        self.log.release(lease_id, "inst-1")
        self.assertEqual(len(self._files()), 2)
        self.assertIsNone(self.log.holder(["a.txt"]))   # already free to claim
        self.clock.advance(self.MAX_SKEW + self.SETTLE + 1.0)
        self.assertEqual(self.log.compact(), 0)         # but not yet collectable
        self.assertEqual(len(self._files()), 2)

    def test_deletes_a_tombstoned_lease_as_a_group(self):
        lease_id = self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        self.log.release(lease_id, "inst-1")
        self.assertEqual(len(self._files()), 2)
        self.clock.advance(self.TTL + self.MAX_SKEW + self.SETTLE + 1.0)
        self.assertEqual(self.log.compact(), 2)
        self.assertEqual(self._files(), [])

    def test_never_leaves_a_request_without_its_tombstone(self):
        """Deleting a tombstone alone would RESURRECT a released claim."""
        lease_id = self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        self.log.release(lease_id, "inst-1")
        for advance in (0.0, 1.0, self.MAX_SKEW, self.SETTLE, 600.0):
            self.clock.advance(advance)
            self.log.compact()
            kinds = self._kinds()
            if "claim_requested" in kinds:
                self.assertIn("claim_released", kinds,
                              "request survived its own tombstone")
            self.assertIsNone(self.log.holder(["a.txt"]))

    def test_deletes_all_records_of_a_lease_together(self):
        lease_id = self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        self.log.renew(lease_id, "inst-1", ttl_seconds=self.TTL)
        self.log.release(lease_id, "inst-1")
        self.assertEqual(len(self._files()), 3)
        self.clock.advance(self.TTL + self.MAX_SKEW + self.SETTLE + 1.0)
        self.assertEqual(self.log.compact(), 3)
        self.assertEqual(self._files(), [])

    # -- corrupt records ---------------------------------------------------

    def _plant_corrupt(self, name="000000000009-1-corrupt-z.json"):
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        target = Path(self.claims_dir) / name
        target.write_text('{"kind": "claim_requ', encoding="utf-8")
        return target

    def test_never_deletes_a_corrupt_record_inside_the_bound(self):
        target = self._plant_corrupt()
        os.utime(target, (self.clock(), self.clock()))
        self.assertEqual(self.log.compact(), 0)
        self.assertTrue(target.exists())
        folded = fold_fs_claims(self.log._read_records(), now=self.clock(),
                                max_skew=self.MAX_SKEW)
        self.assertIn(FS_UNKNOWN_PATH, folded)

    def test_deletes_a_corrupt_record_only_when_mtime_proves_expiry(self):
        """mtime is the ONLY evidence available; it must clear the full bound."""
        target = self._plant_corrupt()
        mtime = self.clock()
        os.utime(target, (mtime, mtime))
        default_ttl = self.log.default_ttl_seconds
        # Just inside the bound: still blocking, still kept.
        self.clock.t = mtime + default_ttl + self.MAX_SKEW + self.SETTLE - 1.0
        self.assertEqual(self.log.compact(), 0)
        self.assertTrue(target.exists())
        # Past the bound: provably dead, collectable.
        self.clock.t = mtime + default_ttl + self.MAX_SKEW + self.SETTLE + 1.0
        self.assertEqual(self.log.compact(), 1)
        self.assertFalse(target.exists())

    # -- general properties ------------------------------------------------

    def test_compact_is_idempotent(self):
        self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        self.clock.advance(self.TTL + self.MAX_SKEW + self.SETTLE + 1.0)
        self.assertEqual(self.log.compact(), 1)
        self.assertEqual(self.log.compact(), 0)
        self.assertEqual(self.log.compact(), 0)

    def test_compact_on_an_empty_or_missing_dir_is_a_noop(self):
        self.assertEqual(self.log.compact(), 0)
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        self.assertEqual(self.log.compact(), 0)

    def test_compact_never_changes_the_answer_of_the_fold(self):
        """The strongest GC property: collection is invisible to correctness."""
        live = self.log.claim(["live.txt"], "inst-1", ttl_seconds=10_000.0)
        self.log.claim(["short.txt"], "inst-2", ttl_seconds=self.TTL)
        dead = self.log.claim(["gone.txt"], "inst-3", ttl_seconds=self.TTL)
        self.log.release(dead, "inst-3")
        self.clock.advance(self.TTL + self.MAX_SKEW + self.SETTLE + 1.0)

        before = fold_fs_claims(self.log._read_records(), now=self.clock(),
                                max_skew=self.MAX_SKEW)
        self.log.compact()
        after = fold_fs_claims(self.log._read_records(), now=self.clock(),
                               max_skew=self.MAX_SKEW)
        self.assertEqual(before, after)
        self.assertEqual(after, {"live.txt": "inst-1"})
        self.assertTrue(live)

    def test_a_live_lease_survives_compaction_and_can_still_be_renewed(self):
        lease_id = self.log.claim(["live.txt"], "inst-1", ttl_seconds=10_000.0)
        self.log.claim(["dead.txt"], "inst-2", ttl_seconds=self.TTL)
        self.clock.advance(self.TTL + self.MAX_SKEW + self.SETTLE + 1.0)
        self.assertEqual(self.log.compact(), 1)
        self.log.renew(lease_id, "inst-1", ttl_seconds=10_000.0)
        self.assertEqual(self.log.holder(["live.txt"]), "inst-1")

    def test_compact_never_changes_the_fold_over_randomized_histories(self):
        """Seeded property sweep of the never-delete-live invariant.

        The table-driven cases above each pin one hazard; this sweeps 200
        randomized claim/renew/release/advance histories across the whole
        settle x max_skew x ttl x retain space and asserts the single property
        that subsumes them: compaction is invisible to the fold. Seeded, so a
        failure is exactly reproducible.
        """
        rng = random.Random(20260802)
        for trial in range(200):
            with tempfile.TemporaryDirectory() as td:
                now = [1000.0]
                log = FsClaimLog(
                    str(Path(td) / "claims"),
                    clock=lambda: now[0],
                    sleep=lambda _s: None,
                    settle_seconds=rng.choice([0.0, 1.0, 5.0]),
                    max_skew_seconds=rng.choice([0.0, 2.0, 10.0]),
                )
                leases = []
                for i in range(rng.randint(1, 4)):
                    try:
                        leases.append((
                            log.claim([f"p{i}.txt"], f"inst-{i}",
                                      ttl_seconds=rng.choice([10.0, 60.0, 300.0])),
                            f"inst-{i}",
                        ))
                    except Exception:
                        pass
                    now[0] += rng.uniform(0.0, 40.0)
                for lease_id, inst in leases:
                    if rng.random() < 0.3:
                        try:
                            log.renew(lease_id, inst, ttl_seconds=60.0)
                        except Exception:
                            pass
                    if rng.random() < 0.3:
                        try:
                            log.release(lease_id, inst)
                        except Exception:
                            pass
                    now[0] += rng.uniform(0.0, 30.0)

                skew = log.max_skew_seconds
                before = fold_fs_claims(log._read_records(), now=now[0],
                                        max_skew=skew)
                log.compact(retain_seconds=rng.choice([0.0, 5.0]))
                after = fold_fs_claims(log._read_records(), now=now[0],
                                       max_skew=skew)
                self.assertEqual(before, after, f"compaction changed the fold "
                                                f"on trial {trial}")

    def test_compact_tolerates_a_concurrently_removed_file(self):
        """Two instances may compact at once; a lost race is not an error."""
        self.log.claim(["a.txt"], "inst-1", ttl_seconds=self.TTL)
        self.clock.advance(self.TTL + self.MAX_SKEW + self.SETTLE + 1.0)
        victim = Path(self.claims_dir) / self._files()[0]
        real_remove = os.remove

        def racing_remove(path):
            real_remove(path)          # our peer got there first
            return real_remove(path)   # -> FileNotFoundError

        spy = _OsSpy(os)
        spy.remove = racing_remove  # type: ignore[attr-defined]
        fcl.os = spy
        try:
            deleted = self.log.compact()
        finally:
            fcl.os = os
        self.assertEqual(deleted, 0)
        self.assertFalse(victim.exists())


if __name__ == "__main__":
    unittest.main()
