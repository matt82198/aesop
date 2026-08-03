"""Tests for state_store.failover -- stale detection, primary election, fencing (Inc 5).

Six layers, in TDD order:

1. TestGenerationToken     -- the generation carrier. Pure.
2. TestFoldPrimary         -- the PURE election fold over synthetic record lists.
                              No filesystem, no clock, no sleeps. This is the whole
                              correctness surface.
3. TestFencing             -- the fence, including the FALSIFIABILITY cell:
                              the same write lands when unguarded, so the guard --
                              and only the guard -- is what refuses it. Delete
                              assert_fenced and those tests go red.
4. TestElectPrimaryOnFsClaimLog -- tmpdir integration with an injected clock and
                              settle=0: takeover, races, fencing end to end.
5. TestHeartbeatDir        -- Tier-S heartbeats are REPLACED, never appended.
6. TestStaleDetectionTransport / TestMultiboxStalenessSummary -- the transport-aware
                              stale source and the observability surface.

Hermetic: tempdirs only, no cwd pollution, no network, no real sleeps.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from state_store.claim_backend import ClaimConflict  # noqa: E402
from state_store.failover import (  # noqa: E402
    DEFAULT_STALE_THRESHOLD_SECONDS,
    FS_UNKNOWN_HOLDER,
    GENERATION_PREFIX,
    RESERVED_PRIMARY_RESOURCE,
    FencedWriteError,
    FencingToken,
    HeartbeatDir,
    PrimaryState,
    UnsupportedBackendError,
    assert_fenced,
    backend_records,
    elect_primary,
    elect_primary_state,
    fenced_backend_write,
    fenced_write,
    fold_primary,
    generation_of_paths,
    generation_token,
    multibox_staleness_summary,
    observe_primary,
)
from state_store.fs_claim_log import FsClaimLog  # noqa: E402
from state_store.instance_projection import detect_stale_instances  # noqa: E402


class _Clock:
    """Deterministic, injectable clock."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _lock_req(instance_id, generation, lamport, epoch_ms, ttl=60.0,
              uuid_="a" * 8, lease_id=None, epoch=1, extra_paths=()):
    """A claim_requested record for the primary lock at ``generation``."""
    paths = [RESERVED_PRIMARY_RESOURCE, generation_token(generation)]
    paths.extend(extra_paths)
    return {
        "v": 1,
        "kind": "claim_requested",
        "paths": paths,
        "instance_id": instance_id,
        "epoch": epoch,
        "lamport": lamport,
        "epoch_ms": epoch_ms,
        "ttl": ttl,
        "uuid": uuid_,
        "lease_id": lease_id if lease_id is not None else f"lease-{uuid_}",
    }


def _tombstone(lease_id, instance_id, lamport, epoch_ms, uuid_="z" * 8):
    return {
        "v": 1,
        "kind": "claim_released",
        "paths": [],
        "instance_id": instance_id,
        "epoch": 1,
        "lamport": lamport,
        "epoch_ms": epoch_ms,
        "ttl": 60.0,
        "uuid": uuid_,
        "lease_id": lease_id,
    }


def _heartbeat_rec(lease_id, instance_id, lamport, epoch_ms, ttl=60.0,
                   uuid_="h" * 8):
    return {
        "v": 1,
        "kind": "heartbeat",
        "paths": [],
        "instance_id": instance_id,
        "epoch": 1,
        "lamport": lamport,
        "epoch_ms": epoch_ms,
        "ttl": ttl,
        "uuid": uuid_,
        "lease_id": lease_id,
    }


# ---------------------------------------------------------------------------
# 1. The generation carrier
# ---------------------------------------------------------------------------

class TestGenerationToken(unittest.TestCase):

    def test_token_is_a_path_under_the_reserved_resource(self):
        self.assertTrue(generation_token(3).startswith(GENERATION_PREFIX))

    def test_token_is_zero_padded_so_it_sorts_numerically(self):
        self.assertLess(generation_token(9), generation_token(10))

    def test_token_round_trips_through_the_paths_array(self):
        for gen in (0, 1, 7, 42, 999999):
            paths = [RESERVED_PRIMARY_RESOURCE, generation_token(gen)]
            self.assertEqual(generation_of_paths(paths), gen)

    def test_paths_without_a_token_carry_no_generation(self):
        self.assertIsNone(generation_of_paths([RESERVED_PRIMARY_RESOURCE]))

    def test_malformed_token_is_not_silently_read_as_zero(self):
        self.assertIsNone(generation_of_paths([GENERATION_PREFIX + "notanumber"]))

    def test_non_list_paths_are_rejected(self):
        self.assertIsNone(generation_of_paths("orchestrator_lock"))

    def test_token_is_distinct_per_generation(self):
        tokens = {generation_token(g) for g in range(5)}
        self.assertEqual(len(tokens), 5)


# ---------------------------------------------------------------------------
# 2. The PURE election fold
# ---------------------------------------------------------------------------

class TestFoldPrimary(unittest.TestCase):
    """No filesystem, no clock, no sleeps -- synthetic record lists only."""

    def test_empty_log_has_no_primary_and_generation_zero(self):
        state = fold_primary([], now=1000.0)
        self.assertIsNone(state.instance_id)
        self.assertEqual(state.generation, 0)
        self.assertTrue(state.vacant)

    def test_sole_instance_is_elected(self):
        records = [_lock_req("inst-a", 1, lamport=1, epoch_ms=1_000_000)]
        state = fold_primary(records, now=1000.0)
        self.assertEqual(state.instance_id, "inst-a")
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.holder_generation, 1)
        self.assertFalse(state.vacant)

    def test_state_carries_the_lease_so_the_primary_can_renew(self):
        records = [_lock_req("inst-a", 1, 1, 1_000_000, lease_id="L1", epoch=7)]
        state = fold_primary(records, now=1000.0)
        self.assertEqual(state.lease_id, "L1")
        self.assertEqual(state.epoch, 7)

    def test_lapsed_primary_frees_the_lock(self):
        records = [_lock_req("inst-a", 1, 1, 1_000_000, ttl=30.0)]
        self.assertEqual(fold_primary(records, now=1020.0).instance_id, "inst-a")
        self.assertIsNone(fold_primary(records, now=1100.0).instance_id)

    def test_lapsed_primary_still_counts_toward_the_fence(self):
        """A dead record is still proof that a generation was issued."""
        records = [_lock_req("inst-a", 1, 1, 1_000_000, ttl=30.0)]
        state = fold_primary(records, now=1100.0)
        self.assertIsNone(state.instance_id)
        self.assertEqual(state.generation, 1)

    def test_exactly_one_successor_after_a_lapse(self):
        records = [
            _lock_req("inst-a", 1, lamport=1, epoch_ms=1_000_000, ttl=30.0),
            _lock_req("inst-b", 2, lamport=2, epoch_ms=1_100_000, uuid_="b" * 8),
        ]
        state = fold_primary(records, now=1100.0)
        self.assertEqual(state.instance_id, "inst-b")
        self.assertEqual(state.holder_generation, 2)
        self.assertEqual(state.generation, 2)

    def test_three_way_simultaneous_takeover_yields_one_winner(self):
        """All three raced to generation 2; the sort key decides, deterministically."""
        records = [_lock_req("inst-a", 1, lamport=1, epoch_ms=1_000_000, ttl=30.0)]
        challengers = [
            _lock_req("inst-b", 2, lamport=5, epoch_ms=1_100_000,
                      uuid_="b" * 8, lease_id="LB"),
            _lock_req("inst-c", 2, lamport=4, epoch_ms=1_100_000,
                      uuid_="c" * 8, lease_id="LC"),
            _lock_req("inst-d", 2, lamport=6, epoch_ms=1_100_000,
                      uuid_="d" * 8, lease_id="LD"),
        ]
        state = fold_primary(records + challengers, now=1100.0)
        self.assertEqual(state.instance_id, "inst-c")  # lowest lamport
        self.assertEqual(state.generation, 2)

    def test_three_way_winner_is_order_independent(self):
        records = [
            _lock_req("inst-b", 2, 5, 1_100_000, uuid_="b" * 8, lease_id="LB"),
            _lock_req("inst-c", 2, 4, 1_100_000, uuid_="c" * 8, lease_id="LC"),
            _lock_req("inst-d", 2, 6, 1_100_000, uuid_="d" * 8, lease_id="LD"),
        ]
        winners = {
            fold_primary(list(perm), now=1100.0).instance_id
            for perm in (records, records[::-1], [records[1], records[2], records[0]])
        }
        self.assertEqual(winners, {"inst-c"})

    def test_generation_never_decreases_across_a_takeover_sequence(self):
        records = []
        seen = []
        for index, (holder, gen) in enumerate(
            [("inst-a", 1), ("inst-b", 2), ("inst-c", 3), ("inst-a", 4)], start=1
        ):
            records.append(_lock_req(
                holder, gen, lamport=index, epoch_ms=1_000_000 + index * 100_000,
                ttl=30.0, uuid_=chr(ord("a") + index) * 8,
                lease_id=f"L{index}",
            ))
            seen.append(fold_primary(records, now=1000.0 + index * 100).generation)
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(seen, [1, 2, 3, 4])

    def test_generation_survives_the_holder_being_tombstoned(self):
        records = [
            _lock_req("inst-a", 2, 1, 1_000_000, lease_id="L1"),
            _tombstone("L1", "inst-a", 2, 1_000_100),
        ]
        state = fold_primary(records, now=1000.0)
        self.assertIsNone(state.instance_id)
        self.assertEqual(state.generation, 2)

    def test_heartbeat_extends_the_primary_lock(self):
        records = [
            _lock_req("inst-a", 1, 1, 1_000_000, ttl=30.0, lease_id="L1"),
            _heartbeat_rec("L1", "inst-a", 2, 1_020_000, ttl=30.0),
        ]
        self.assertEqual(fold_primary(records, now=1045.0).instance_id, "inst-a")
        self.assertIsNone(fold_primary(records, now=1060.0).instance_id)

    def test_corrupt_record_makes_the_primary_unknowable_fail_closed(self):
        records = [
            _lock_req("inst-a", 1, 1, 1_000_000),
            {"__corrupt__": True, "epoch_ms": 1_000_000, "ttl": 300.0},
        ]
        state = fold_primary(records, now=1000.0)
        self.assertTrue(state.unknown)
        self.assertEqual(state.instance_id, FS_UNKNOWN_HOLDER)
        self.assertFalse(state.vacant)

    def test_expired_corrupt_record_stops_blocking(self):
        records = [{"__corrupt__": True, "epoch_ms": 1_000_000, "ttl": 30.0}]
        self.assertTrue(fold_primary(records, now=1010.0).unknown)
        self.assertFalse(fold_primary(records, now=1100.0).unknown)

    def test_pre_fencing_lock_record_holds_at_generation_zero(self):
        """A lock claim with no token can never outrank a fenced generation."""
        records = [{
            "v": 1, "kind": "claim_requested",
            "paths": [RESERVED_PRIMARY_RESOURCE],
            "instance_id": "legacy", "epoch": 1, "lamport": 1,
            "epoch_ms": 1_000_000, "ttl": 60.0, "uuid": "l" * 8,
            "lease_id": "LL",
        }]
        state = fold_primary(records, now=1000.0)
        self.assertEqual(state.instance_id, "legacy")
        self.assertEqual(state.holder_generation, 0)

    def test_ordinary_file_claims_do_not_elect_a_primary(self):
        records = [{
            "v": 1, "kind": "claim_requested", "paths": ["src/app.py"],
            "instance_id": "inst-a", "epoch": 1, "lamport": 1,
            "epoch_ms": 1_000_000, "ttl": 60.0, "uuid": "f" * 8,
            "lease_id": "LF",
        }]
        state = fold_primary(records, now=1000.0)
        self.assertIsNone(state.instance_id)
        self.assertEqual(state.generation, 0)

    def test_max_skew_only_ever_lengthens_the_lock(self):
        records = [_lock_req("inst-a", 1, 1, 1_000_000, ttl=30.0)]
        self.assertIsNone(fold_primary(records, now=1035.0).instance_id)
        self.assertEqual(
            fold_primary(records, now=1035.0, max_skew=10.0).instance_id, "inst-a"
        )

    def test_fenced_property_flags_a_holder_behind_the_fence(self):
        records = [
            _lock_req("inst-a", 1, lamport=1, epoch_ms=1_000_000, lease_id="LA"),
            _lock_req("inst-b", 5, lamport=9, epoch_ms=1_000_100,
                      uuid_="b" * 8, lease_id="LB"),
        ]
        state = fold_primary(records, now=1000.0)
        self.assertEqual(state.instance_id, "inst-a")  # lower sort key wins the lock
        self.assertEqual(state.generation, 5)
        self.assertTrue(state.fenced)

    def test_state_token_is_the_stamp_for_coordination_writes(self):
        records = [_lock_req("inst-a", 3, 1, 1_000_000, epoch=4)]
        token = fold_primary(records, now=1000.0).token()
        self.assertEqual(
            token.as_dict(),
            {"instance_id": "inst-a", "epoch": 4, "generation": 3},
        )

    def test_vacant_state_has_no_token(self):
        self.assertIsNone(fold_primary([], now=1000.0).token())

    def test_fold_is_pure_and_does_not_mutate_its_input(self):
        records = [_lock_req("inst-a", 1, 1, 1_000_000)]
        before = json.dumps(records, sort_keys=True)
        fold_primary(records, now=1000.0)
        self.assertEqual(json.dumps(records, sort_keys=True), before)

    def test_non_dict_junk_in_the_log_is_ignored(self):
        records = ["nonsense", None, 42, _lock_req("inst-a", 1, 1, 1_000_000)]
        self.assertEqual(fold_primary(records, now=1000.0).instance_id, "inst-a")


# ---------------------------------------------------------------------------
# 3. The fence -- including the falsifiability cell
# ---------------------------------------------------------------------------

class TestFencing(unittest.TestCase):

    def test_current_generation_passes(self):
        assert_fenced(FencingToken("inst-a", 1, 3), 3)

    def test_future_generation_passes(self):
        assert_fenced(FencingToken("inst-a", 1, 4), 3)

    def test_stale_generation_is_rejected(self):
        with self.assertRaises(FencedWriteError) as ctx:
            assert_fenced(FencingToken("inst-a", 1, 2), 3)
        self.assertEqual(ctx.exception.instance_id, "inst-a")
        self.assertEqual(ctx.exception.token_generation, 2)
        self.assertEqual(ctx.exception.current_generation, 3)

    def test_missing_token_is_rejected(self):
        with self.assertRaises(FencedWriteError):
            assert_fenced(None, 1)

    def test_fenced_write_returns_the_writers_value_when_allowed(self):
        self.assertEqual(
            fenced_write(FencingToken("a", 1, 2), 2, lambda x: x * 2, 21), 42
        )

    def test_fenced_write_passes_keyword_arguments_through(self):
        self.assertEqual(
            fenced_write(FencingToken("a", 1, 2), 2, lambda *, k: k, k="ok"), "ok"
        )

    def test_returning_old_primary_is_fenced_after_the_fold_shows_n_plus_1(self):
        """The split-brain case: A was partitioned, B took over, A came back."""
        records = [
            _lock_req("inst-a", 1, lamport=1, epoch_ms=1_000_000, ttl=30.0,
                      lease_id="LA"),
            _lock_req("inst-b", 2, lamport=2, epoch_ms=1_100_000,
                      uuid_="b" * 8, lease_id="LB"),
        ]
        state = fold_primary(records, now=1100.0)
        self.assertEqual(state.instance_id, "inst-b")

        stale_token = FencingToken("inst-a", epoch=1, generation=1)
        with self.assertRaises(FencedWriteError):
            fenced_write(stale_token, state.generation, lambda: "resumed driving")

        # The successor writes at its own generation, unimpeded.
        self.assertEqual(
            fenced_write(state.token(), state.generation, lambda: "ok"), "ok"
        )

    def test_fence_is_load_bearing_the_same_write_lands_unguarded(self):
        """FALSIFIABILITY. Delete assert_fenced's comparison and this goes red.

        The point is not that the stale write fails -- lots of things could make a
        write fail. The point is that this exact write, from this exact caller,
        with this exact payload, SUCCEEDS when it is not routed through the guard.
        So the guard is the only thing standing between an old primary and a
        split-brain, and removing it is immediately visible here.
        """
        accepted = []

        def write():
            accepted.append("stale-write")
            return "accepted"

        stale_token = FencingToken("inst-a", epoch=1, generation=1)
        current_generation = 2

        # (1) Unguarded, the write is accepted. Nothing else rejects it.
        self.assertEqual(write(), "accepted")
        self.assertEqual(accepted, ["stale-write"])

        # (2) Guarded, the SAME write is refused ...
        with self.assertRaises(FencedWriteError):
            fenced_write(stale_token, current_generation, write)

        # (3) ... and left no trace. The fence, not the writer, stopped it.
        self.assertEqual(accepted, ["stale-write"])

    def test_fenced_write_is_checked_before_any_side_effect(self):
        touched = []
        with self.assertRaises(FencedWriteError):
            fenced_write(FencingToken("a", 1, 0), 5, touched.append, "x")
        self.assertEqual(touched, [])

    def test_token_serializes_instance_epoch_and_generation(self):
        self.assertEqual(
            FencingToken("h:1:x", 3, 9).as_dict(),
            {"instance_id": "h:1:x", "epoch": 3, "generation": 9},
        )

    def test_epoch_and_generation_fence_different_failure_modes(self):
        """Same generation, different epoch: a restart is not a takeover."""
        assert_fenced(FencingToken("inst-a", 1, 2), 2)
        assert_fenced(FencingToken("inst-a", 2, 2), 2)


# ---------------------------------------------------------------------------
# 4. Integration over a real FsClaimLog (tmpdir, injected clock, settle=0)
# ---------------------------------------------------------------------------

class _FailoverFixture(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.claims_dir = str(Path(self.temp_dir.name) / "claims")
        self.clock = _Clock(1000.0)
        self.backends = []

    def tearDown(self):
        for backend in self.backends:
            backend.close()
        self.temp_dir.cleanup()

    def make_backend(self, epoch=1):
        backend = FsClaimLog(
            self.claims_dir,
            clock=self.clock,
            settle_seconds=0.0,
            max_skew_seconds=0.0,
            case_policy="insensitive",
            epoch=epoch,
        )
        self.backends.append(backend)
        return backend


class TestElectPrimaryOnFsClaimLog(_FailoverFixture):

    def test_sole_instance_is_elected_at_generation_one(self):
        backend = self.make_backend()
        self.assertEqual(elect_primary(backend, instance_id="inst-a"), ("inst-a", 1))

    def test_reelection_by_the_sitting_primary_does_not_bump_the_generation(self):
        backend = self.make_backend()
        elect_primary(backend, instance_id="inst-a")
        for _ in range(3):
            self.assertEqual(
                elect_primary(backend, instance_id="inst-a"), ("inst-a", 1)
            )

    def test_a_live_primary_is_never_preempted(self):
        a, b = self.make_backend(), self.make_backend()
        elect_primary(a, instance_id="inst-a")
        self.assertEqual(elect_primary(b, instance_id="inst-b"), ("inst-a", 1))

    def test_lapsed_primary_yields_exactly_one_successor(self):
        a, b, c = self.make_backend(), self.make_backend(), self.make_backend()
        elect_primary(a, instance_id="inst-a", ttl_seconds=30.0)
        self.clock.advance(100)
        first = elect_primary(b, instance_id="inst-b", ttl_seconds=30.0)
        second = elect_primary(c, instance_id="inst-c", ttl_seconds=30.0)
        self.assertEqual(first, ("inst-b", 2))
        self.assertEqual(second, ("inst-b", 2))

    def test_generation_never_decreases_over_repeated_takeovers(self):
        seen = []
        for index, name in enumerate(["inst-a", "inst-b", "inst-c", "inst-a"]):
            backend = self.make_backend(epoch=index + 1)
            seen.append(elect_primary(backend, instance_id=name, ttl_seconds=30.0)[1])
            self.clock.advance(100)
        self.assertEqual(seen, [1, 2, 3, 4])

    def test_returning_old_primary_cannot_resume(self):
        a, b = self.make_backend(), self.make_backend()
        a_id, a_gen = elect_primary(a, instance_id="inst-a", ttl_seconds=30.0)
        a_token = FencingToken(a_id, epoch=1, generation=a_gen)

        self.clock.advance(100)  # A is partitioned; its lock lapses
        self.assertEqual(elect_primary(b, instance_id="inst-b", ttl_seconds=30.0),
                         ("inst-b", 2))

        # A returns, still believing it is primary.
        with self.assertRaises(FencedWriteError):
            fenced_backend_write(a, a_token, lambda: "resume")
        # And it cannot take the lock back either: B holds it.
        self.assertEqual(elect_primary(a, instance_id="inst-a"), ("inst-b", 2))

    def test_successor_writes_pass_the_fence(self):
        a, b = self.make_backend(), self.make_backend()
        elect_primary(a, instance_id="inst-a", ttl_seconds=30.0)
        self.clock.advance(100)
        state = elect_primary_state(b, instance_id="inst-b", ttl_seconds=30.0)
        self.assertEqual(
            fenced_backend_write(b, state.token(), lambda: "ok"), "ok"
        )

    def test_losing_a_concurrent_takeover_reports_the_real_winner(self):
        """A peer's record becomes visible only INSIDE our settle window.

        This is the failure the settle window exists for: the peer wrote first
        (lower sort key) but the share had not yet shown us its record when we
        wrote ours. Re-listing after the settle reveals it, we lose the fold,
        self-tombstone, and elect_primary reports the peer -- no retry, no raise.
        """
        a = self.make_backend()
        elect_primary(a, instance_id="inst-a", ttl_seconds=30.0)
        self.clock.advance(100)

        peer_record = _lock_req(
            "inst-peer", 2, lamport=0, epoch_ms=1_100_000, ttl=30.0,
            uuid_="0" * 8, lease_id="LPEER",
        )

        def _peer_becomes_visible(_duration):
            target = Path(self.claims_dir) / "000000000000-1100000-inst_peer-0.json"
            if not target.exists():
                target.write_text(json.dumps(peer_record), encoding="utf-8")

        challenger = FsClaimLog(
            self.claims_dir, clock=self.clock, settle_seconds=0.5,
            sleep=_peer_becomes_visible, max_skew_seconds=0.0,
            case_policy="insensitive",
        )
        self.backends.append(challenger)
        winner, generation = elect_primary(
            challenger, instance_id="inst-late", ttl_seconds=30.0
        )
        self.assertEqual(winner, "inst-peer")
        self.assertEqual(generation, 2)
        # And every instance agrees, because they all fold the same log.
        self.assertEqual(observe_primary(a).instance_id, "inst-peer")

    def test_loser_leaves_no_phantom_lock_holder(self):
        a, b = self.make_backend(), self.make_backend()
        elect_primary(a, instance_id="inst-a")
        elect_primary(b, instance_id="inst-b")
        self.assertEqual(a.holder([RESERVED_PRIMARY_RESOURCE]), "inst-a")

    def test_state_exposes_the_lease_so_the_primary_can_renew_its_lock(self):
        backend = self.make_backend()
        state = elect_primary_state(backend, instance_id="inst-a", ttl_seconds=30.0)
        self.clock.advance(20)
        backend.renew(state.lease_id, "inst-a", ttl_seconds=30.0)
        self.clock.advance(20)
        self.assertEqual(observe_primary(backend).instance_id, "inst-a")

    def test_released_lock_is_immediately_takeable(self):
        a, b = self.make_backend(), self.make_backend()
        state = elect_primary_state(a, instance_id="inst-a")
        a.release(state.lease_id, "inst-a")
        self.assertEqual(elect_primary(b, instance_id="inst-b"), ("inst-b", 2))

    def test_observe_primary_never_writes(self):
        backend = self.make_backend()
        elect_primary(backend, instance_id="inst-a")
        before = sorted(os.listdir(self.claims_dir))
        observe_primary(backend)
        self.assertEqual(sorted(os.listdir(self.claims_dir)), before)

    def test_election_without_an_instance_id_observes_only(self):
        backend = self.make_backend()
        self.assertEqual(elect_primary(backend), (None, 0))
        self.assertFalse(os.path.isdir(self.claims_dir))

    def test_generation_is_carried_in_the_claim_record_on_disk(self):
        backend = self.make_backend()
        elect_primary(backend, instance_id="inst-a")
        paths = []
        for name in sorted(os.listdir(self.claims_dir)):
            with open(Path(self.claims_dir) / name, encoding="utf-8") as handle:
                paths.extend(json.load(handle).get("paths", []))
        self.assertIn(RESERVED_PRIMARY_RESOURCE, paths)
        self.assertIn(generation_token(1), paths)

    def test_two_instances_cannot_occupy_the_same_generation(self):
        """The token is itself a claimed path, so a generation is exclusive too."""
        a, b = self.make_backend(), self.make_backend()
        elect_primary(a, instance_id="inst-a", ttl_seconds=30.0)
        with self.assertRaises(ClaimConflict):
            b.claim(
                [RESERVED_PRIMARY_RESOURCE, generation_token(1)], "inst-b", 30.0
            )

    def test_corrupt_record_blocks_election_fail_closed(self):
        backend = self.make_backend()
        elect_primary(backend, instance_id="inst-a", ttl_seconds=30.0)
        self.clock.advance(100)
        Path(self.claims_dir, "999-truncated.json").write_text(
            '{"kind": "claim_re', encoding="utf-8"
        )
        state = elect_primary_state(backend, instance_id="inst-b")
        self.assertTrue(state.unknown)
        self.assertEqual(state.instance_id, FS_UNKNOWN_HOLDER)

    def test_backend_without_a_record_surface_is_refused(self):
        class _Opaque:
            pass

        with self.assertRaises(UnsupportedBackendError):
            backend_records(_Opaque())

    def test_public_read_records_is_preferred_when_offered(self):
        class _Public:
            def read_records(self):
                return [_lock_req("inst-x", 1, 1, 1_000_000)]

        self.assertEqual(len(backend_records(_Public())), 1)


# ---------------------------------------------------------------------------
# 5. Tier-S heartbeats: replaced, never appended
# ---------------------------------------------------------------------------

class TestHeartbeatDir(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hb_dir = str(Path(self.temp_dir.name) / "instances")
        self.clock = _Clock(1000.0)
        self.beats = HeartbeatDir(self.hb_dir, clock=self.clock)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _files(self):
        return sorted(os.listdir(self.hb_dir))

    def test_beat_creates_one_file_per_instance_and_epoch(self):
        self.beats.beat("host:1:aaa", epoch=1)
        self.beats.beat("host:2:bbb", epoch=1)
        self.assertEqual(len(self._files()), 2)

    def test_repeated_beats_replace_rather_than_append(self):
        """The whole reason Tier S does not reuse the event stream."""
        for _ in range(50):
            self.clock.advance(10)
            self.beats.beat("host:1:aaa", epoch=1)
        self.assertEqual(len(self._files()), 1)
        records = self.beats.read_heartbeats()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["heartbeat_at"], 1500.0)

    def test_filename_shape_is_instance_dot_epoch_dot_hb(self):
        self.beats.beat("host:1:aaa", epoch=7)
        name = self._files()[0]
        self.assertTrue(name.endswith(".7.hb"))

    def test_true_instance_id_lives_in_the_body_not_the_filename(self):
        """':' is illegal in a Windows filename, so the name is sanitized."""
        self.beats.beat("host:1:aaa", epoch=1)
        self.assertNotIn(":", self._files()[0])
        self.assertEqual(
            self.beats.read_heartbeats()[0]["instance_id"], "host:1:aaa"
        )

    def test_distinct_epochs_of_one_instance_get_distinct_files(self):
        self.beats.beat("host:1:aaa", epoch=1)
        self.beats.beat("host:1:aaa", epoch=2)
        self.assertEqual(len(self._files()), 2)

    def test_no_temp_file_survives_a_beat(self):
        self.beats.beat("host:1:aaa", epoch=1)
        self.assertFalse([n for n in self._files() if n.endswith(".tmp")])

    def test_temp_files_are_invisible_to_readers(self):
        self.beats.beat("host:1:aaa", epoch=1)
        Path(self.hb_dir, "host_1_bbb.1.hb.tmp").write_text("{}", encoding="utf-8")
        self.assertEqual(len(self.beats.read_heartbeats()), 1)

    def test_record_carries_hostname_pid_and_epoch(self):
        self.beats.beat("host:1:aaa", epoch=3, hostname="box-1", pid=4242)
        rec = self.beats.read_heartbeats()[0]
        self.assertEqual(rec["hostname"], "box-1")
        self.assertEqual(rec["pid"], 4242)
        self.assertEqual(rec["epoch"], 3)

    def test_missing_directory_reads_as_no_instances(self):
        self.assertEqual(HeartbeatDir(self.hb_dir).read_heartbeats(), [])

    def test_corrupt_beat_is_treated_as_alive_not_stale(self):
        """Declaring a live peer dead would let its claims be reclaimed."""
        Path(self.hb_dir).mkdir(parents=True, exist_ok=True)
        Path(self.hb_dir, "host_9_zzz.1.hb").write_text(
            '{"instance_id": "host', encoding="utf-8"
        )
        records = self.beats.read_heartbeats()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["corrupt"])
        self.assertGreater(records[0]["heartbeat_at"], 0)

    def test_forget_removes_a_beat_and_is_idempotent(self):
        self.beats.beat("host:1:aaa", epoch=1)
        self.assertTrue(self.beats.forget("host:1:aaa", epoch=1))
        self.assertFalse(self.beats.forget("host:1:aaa", epoch=1))
        self.assertEqual(self.beats.read_heartbeats(), [])

    def test_callable_form_reads_heartbeats(self):
        self.beats.beat("host:1:aaa", epoch=1)
        self.assertEqual(self.beats(), self.beats.read_heartbeats())

    def test_beat_returns_the_record_it_wrote(self):
        rec = self.beats.beat("host:1:aaa", epoch=1)
        self.assertEqual(rec["heartbeat_at"], 1000.0)
        self.assertEqual(rec["instance_id"], "host:1:aaa")


# ---------------------------------------------------------------------------
# 6a. The transport-aware stale source
# ---------------------------------------------------------------------------

class _FakeStore:
    """Minimal EventStore stand-in for the Tier-L path."""

    def __init__(self, events):
        self._events = events

    def read(self, _stream):
        return list(self._events)


class TestStaleDetectionTransport(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.clock = _Clock(1000.0)
        self.beats = HeartbeatDir(
            str(Path(self.temp_dir.name) / "instances"), clock=self.clock
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_threshold_is_still_300_seconds(self):
        self.assertEqual(DEFAULT_STALE_THRESHOLD_SECONDS, 300.0)

    def test_event_stream_path_is_unchanged_when_no_source_is_given(self):
        store = _FakeStore([
            {"type": "instance_registered",
             "payload": {"instance_id": "inst-a", "registered_at": 1000.0}},
            {"type": "instance_heartbeat",
             "payload": {"instance_id": "inst-a", "heartbeat_at": 1000.0}},
        ])
        self.assertEqual(detect_stale_instances(store, now=1100.0), [])
        stale = detect_stale_instances(store, now=1400.0)
        self.assertEqual([s["instance_id"] for s in stale], ["inst-a"])

    def test_heartbeat_source_detects_a_stale_instance(self):
        self.beats.beat("inst-a", epoch=1)
        stale = detect_stale_instances(None, source=self.beats, now=1400.0)
        self.assertEqual([s["instance_id"] for s in stale], ["inst-a"])

    def test_heartbeat_source_reports_nothing_while_beats_are_fresh(self):
        self.beats.beat("inst-a", epoch=1)
        self.assertEqual(detect_stale_instances(None, source=self.beats, now=1200.0), [])

    def test_threshold_semantics_are_identical_on_both_transports(self):
        """Strictly greater-than, on the boundary, on both paths."""
        store = _FakeStore([
            {"type": "instance_registered",
             "payload": {"instance_id": "inst-a", "registered_at": 1000.0}},
        ])
        self.beats.beat("inst-a", epoch=1)
        for at_boundary, past_boundary in [(1300.0, 1300.01)]:
            self.assertEqual(detect_stale_instances(store, now=at_boundary), [])
            self.assertEqual(
                detect_stale_instances(None, source=self.beats, now=at_boundary), []
            )
            self.assertEqual(len(detect_stale_instances(store, now=past_boundary)), 1)
            self.assertEqual(
                len(detect_stale_instances(
                    None, source=self.beats, now=past_boundary)), 1
            )

    def test_newest_epoch_wins_when_an_instance_restarted(self):
        self.beats.beat("inst-a", epoch=1)
        self.clock.advance(200)
        self.beats.beat("inst-a", epoch=2)
        self.assertEqual(detect_stale_instances(None, source=self.beats, now=1400.0), [])

    def test_source_accepts_a_plain_record_list(self):
        records = [{"instance_id": "inst-a", "epoch": 1, "heartbeat_at": 1000.0}]
        stale = detect_stale_instances(None, source=records, now=1400.0)
        self.assertEqual([s["instance_id"] for s in stale], ["inst-a"])

    def test_source_accepts_a_bare_callable(self):
        stale = detect_stale_instances(
            None,
            source=lambda: [{"instance_id": "inst-a", "heartbeat_at": 1000.0}],
            now=1400.0,
        )
        self.assertEqual([s["instance_id"] for s in stale], ["inst-a"])

    def test_unreadable_source_reports_nothing_stale_fail_closed(self):
        def _explode():
            raise OSError("share unreachable")

        self.assertEqual(detect_stale_instances(None, source=_explode, now=1400.0), [])

    def test_stale_result_shape_matches_the_event_stream_path(self):
        self.beats.beat("inst-a", epoch=5)
        entry = detect_stale_instances(None, source=self.beats, now=1400.0)[0]
        for key in ("instance_id", "registered_at", "last_heartbeat", "status"):
            self.assertIn(key, entry)
        self.assertEqual(entry["epoch"], 5)

    def test_stale_instances_are_ordered_oldest_heartbeat_first(self):
        self.beats.beat("inst-a", epoch=1)
        self.clock.advance(50)
        self.beats.beat("inst-b", epoch=1)
        stale = detect_stale_instances(None, source=self.beats, now=1600.0)
        self.assertEqual([s["instance_id"] for s in stale], ["inst-a", "inst-b"])

    def test_corrupt_beat_never_makes_an_instance_look_stale_early(self):
        Path(self.beats.heartbeats_dir).mkdir(parents=True, exist_ok=True)
        Path(self.beats.heartbeats_dir, "inst_a.1.hb").write_text(
            "not json", encoding="utf-8"
        )
        beat_at = self.beats.read_heartbeats()[0]["heartbeat_at"]
        self.assertEqual(
            detect_stale_instances(None, source=self.beats, now=beat_at + 10.0), []
        )


# ---------------------------------------------------------------------------
# 6b. The observability surface (fleet_multibox_summary feed)
# ---------------------------------------------------------------------------

class TestMultiboxStalenessSummary(_FailoverFixture):

    def setUp(self):
        super().setUp()
        self.beats = HeartbeatDir(
            str(Path(self.temp_dir.name) / "instances"), clock=self.clock
        )

    def test_summary_has_a_stable_shape(self):
        summary = multibox_staleness_summary(now=1000.0)
        for key in ("now", "stale_threshold_seconds", "primary", "generation",
                    "instances", "stale_instances", "held_paths",
                    "unknown_holder", "degraded"):
            self.assertIn(key, summary)

    def test_summary_reports_the_primary_and_the_fence(self):
        backend = self.make_backend()
        elect_primary(backend, instance_id="inst-a", ttl_seconds=30.0)
        self.clock.advance(100)
        elect_primary(self.make_backend(), instance_id="inst-b", ttl_seconds=30.0)
        summary = multibox_staleness_summary(backend=backend)
        self.assertEqual(summary["primary"]["instance_id"], "inst-b")
        self.assertEqual(summary["primary"]["generation"], 2)
        self.assertEqual(summary["generation"], 2)

    def test_summary_reports_no_primary_when_the_lock_has_lapsed(self):
        backend = self.make_backend()
        elect_primary(backend, instance_id="inst-a", ttl_seconds=30.0)
        self.clock.advance(100)
        summary = multibox_staleness_summary(backend=backend)
        self.assertIsNone(summary["primary"])
        self.assertEqual(summary["generation"], 1)

    def test_summary_hides_the_reserved_lock_from_held_paths(self):
        backend = self.make_backend()
        elect_primary(backend, instance_id="inst-a")
        backend.claim(["src/app.py"], "inst-a", 60.0)
        summary = multibox_staleness_summary(backend=backend)
        self.assertEqual(summary["held_paths"], {"src/app.py": "inst-a"})

    def test_summary_surfaces_stale_instances(self):
        self.beats.beat("inst-a", epoch=1)
        self.clock.advance(400)
        self.beats.beat("inst-b", epoch=1)
        summary = multibox_staleness_summary(
            heartbeat_source=self.beats, now=self.clock()
        )
        self.assertEqual(summary["stale_instances"], ["inst-a"])
        self.assertEqual(len(summary["instances"]), 2)
        self.assertTrue(summary["instances"][0]["stale"])
        self.assertFalse(summary["instances"][1]["stale"])

    def test_summary_surfaces_an_unknowable_holder(self):
        backend = self.make_backend()
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        Path(self.claims_dir, "000-truncated.json").write_text(
            "{oops", encoding="utf-8"
        )
        self.assertTrue(multibox_staleness_summary(backend=backend)["unknown_holder"])

    def test_summary_degrades_per_section_instead_of_raising(self):
        class _Opaque:
            pass

        summary = multibox_staleness_summary(
            backend=_Opaque(), heartbeat_source=self.beats, now=1000.0
        )
        self.assertIsNone(summary["primary"])
        self.assertTrue(any("backend" in d for d in summary["degraded"]))
        self.assertEqual(summary["instances"], [])

    def test_summary_never_writes(self):
        backend = self.make_backend()
        elect_primary(backend, instance_id="inst-a")
        before = sorted(os.listdir(self.claims_dir))
        multibox_staleness_summary(backend=backend, heartbeat_source=self.beats)
        self.assertEqual(sorted(os.listdir(self.claims_dir)), before)

    def test_summary_threshold_default_is_unchanged(self):
        self.assertEqual(
            multibox_staleness_summary(now=1000.0)["stale_threshold_seconds"], 300.0
        )


class TestPrimaryStateDataclass(unittest.TestCase):

    def test_primary_state_is_immutable(self):
        state = PrimaryState("inst-a", 2, 2, 1, "L1")
        with self.assertRaises(Exception):
            state.generation = 5

    def test_unknown_state_is_not_vacant(self):
        state = PrimaryState(FS_UNKNOWN_HOLDER, 1, 1, None, None, unknown=True)
        self.assertFalse(state.vacant)


if __name__ == "__main__":
    unittest.main()
