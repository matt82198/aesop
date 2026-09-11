"""Tests for state_store.fs_claim_log -- shared-filesystem lease-by-append (multibox Inc 4a).

Four layers, in TDD order:

1. TestFoldFsClaims        -- the PURE fold table. No filesystem, no clock, no sleeps.
                              This is the entire correctness surface of the design.
2. TestFsClaimLog          -- FsClaimLog on a tmpdir with an injectable clock and settle=0.
3. FsClaimLogContractTests -- the Inc 2 ClaimBackend contract suite, imported UNMODIFIED
                              and re-parametrized onto FsClaimLog.
4. TestFsClaimLogSplitBrain -- the four 47c967b split-brain regressions replayed through
                              FsClaimLog with case_policy="insensitive".

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
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from state_store.claim_backend import ClaimBackend, ClaimConflict  # noqa: E402
from state_store.fs_claim_log import (  # noqa: E402
    FS_UNKNOWN_HOLDER,
    FS_UNKNOWN_PATH,
    FsClaimLog,
    fold_fs_claims,
)

try:  # `unittest discover -s tests` puts tests/ on sys.path as a top-level dir
    from test_claim_backend import ClaimBackendContractTests
except ImportError:  # `python -m unittest tests.test_fs_claim_log` from the repo root
    from tests.test_claim_backend import ClaimBackendContractTests


# ---------------------------------------------------------------------------
# Record builders for the pure fold table
# ---------------------------------------------------------------------------

def _req(
    paths,
    instance_id,
    lamport,
    epoch_ms,
    ttl=60.0,
    uuid_="a" * 8,
    lease_id=None,
    epoch=1,
):
    """Build a claim_requested record dict."""
    rec = {
        "v": 1,
        "kind": "claim_requested",
        "paths": list(paths),
        "instance_id": instance_id,
        "epoch": epoch,
        "lamport": lamport,
        "epoch_ms": epoch_ms,
        "uuid": uuid_,
        "lease_id": lease_id if lease_id is not None else f"lease-{uuid_}",
    }
    if ttl is not None:
        rec["ttl"] = ttl
    return rec


def _rel(lease_id, instance_id, lamport, epoch_ms, uuid_="z" * 8):
    """Build a claim_released (tombstone) record dict."""
    return {
        "v": 1,
        "kind": "claim_released",
        "paths": [],
        "instance_id": instance_id,
        "epoch": 1,
        "lamport": lamport,
        "epoch_ms": epoch_ms,
        "uuid": uuid_,
        "lease_id": lease_id,
    }


def _hb(lease_id, instance_id, lamport, epoch_ms, ttl=60.0, uuid_="h" * 8):
    """Build a heartbeat (renew) record dict."""
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


def _corrupt(mtime_epoch_ms, ttl=60.0):
    """Build the sentinel a corrupt/truncated record file folds to."""
    return {"__corrupt__": True, "epoch_ms": mtime_epoch_ms, "ttl": ttl}


# ---------------------------------------------------------------------------
# 1. Pure fold table -- no FS, no clock, no sleeps
# ---------------------------------------------------------------------------

class TestFoldFsClaims(unittest.TestCase):
    """fold_fs_claims is a pure function over a list of dicts."""

    def test_empty_records_yields_no_holders(self):
        self.assertEqual(fold_fs_claims([], now=1000.0, max_skew=0.0), {})

    def test_single_claim_grants(self):
        recs = [_req(["a/b.txt"], "inst-1", lamport=1, epoch_ms=1_000_000)]
        self.assertEqual(
            fold_fs_claims(recs, now=1000.0, max_skew=0.0), {"a/b.txt": "inst-1"}
        )

    def test_lowest_lamport_wins(self):
        recs = [
            _req(["p"], "inst-2", lamport=9, epoch_ms=1_000_000, uuid_="b" * 8),
            _req(["p"], "inst-1", lamport=1, epoch_ms=9_000_000, uuid_="c" * 8),
        ]
        # lamport dominates epoch_ms in the sort key
        self.assertEqual(fold_fs_claims(recs, now=1000.0, max_skew=0.0)["p"], "inst-1")

    def test_epoch_ms_breaks_lamport_tie(self):
        recs = [
            _req(["p"], "inst-2", lamport=5, epoch_ms=2_000_000, uuid_="b" * 8),
            _req(["p"], "inst-1", lamport=5, epoch_ms=1_000_000, uuid_="c" * 8),
        ]
        self.assertEqual(fold_fs_claims(recs, now=1000.0, max_skew=0.0)["p"], "inst-1")

    def test_instance_id_breaks_epoch_ms_tie(self):
        recs = [
            _req(["p"], "inst-b", lamport=5, epoch_ms=1_000_000, uuid_="1" * 8),
            _req(["p"], "inst-a", lamport=5, epoch_ms=1_000_000, uuid_="2" * 8),
        ]
        self.assertEqual(fold_fs_claims(recs, now=1000.0, max_skew=0.0)["p"], "inst-a")

    def test_uuid_breaks_instance_id_tie(self):
        # Same instance_id racing itself (two processes, same durable id): uuid decides.
        recs = [
            _req(["p"], "inst-a", lamport=5, epoch_ms=1_000, uuid_="bbbb", lease_id="L2"),
            _req(["p"], "inst-a", lamport=5, epoch_ms=1_000, uuid_="aaaa", lease_id="L1"),
        ]
        detail = fold_fs_claims(recs, now=1.0, max_skew=0.0, detail=True)
        self.assertEqual(detail["p"], ("inst-a", "L1"))

    def test_tombstone_releases(self):
        recs = [
            _req(["p"], "inst-1", lamport=1, epoch_ms=1_000, lease_id="L1"),
            _rel("L1", "inst-1", lamport=2, epoch_ms=2_000),
        ]
        self.assertEqual(fold_fs_claims(recs, now=1.0, max_skew=0.0), {})

    def test_tombstone_for_other_lease_does_not_release_ours(self):
        recs = [
            _req(["p"], "inst-1", lamport=1, epoch_ms=1_000, lease_id="L1"),
            _rel("L2", "inst-2", lamport=2, epoch_ms=2_000),
        ]
        self.assertEqual(fold_fs_claims(recs, now=1.0, max_skew=0.0), {"p": "inst-1"})

    def test_tombstone_of_winner_hands_path_to_runner_up(self):
        recs = [
            _req(["p"], "inst-1", lamport=1, epoch_ms=1_000, lease_id="L1", uuid_="a" * 8),
            _req(["p"], "inst-2", lamport=2, epoch_ms=2_000, lease_id="L2", uuid_="b" * 8),
            _rel("L1", "inst-1", lamport=3, epoch_ms=3_000),
        ]
        self.assertEqual(fold_fs_claims(recs, now=1.0, max_skew=0.0), {"p": "inst-2"})

    def test_expired_claim_is_reclaimable(self):
        # claimed at epoch_ms=1_000_000 (=1000.0s) with ttl 60 -> dead after 1060.0
        recs = [_req(["p"], "inst-1", lamport=1, epoch_ms=1_000_000, ttl=60.0)]
        self.assertEqual(fold_fs_claims(recs, now=1059.0, max_skew=0.0), {"p": "inst-1"})
        self.assertEqual(fold_fs_claims(recs, now=1061.0, max_skew=0.0), {})

    def test_expired_winner_yields_to_live_runner_up(self):
        recs = [
            _req(["p"], "inst-1", lamport=1, epoch_ms=1_000_000, ttl=10.0, uuid_="a" * 8),
            _req(["p"], "inst-2", lamport=2, epoch_ms=1_050_000, ttl=60.0, uuid_="b" * 8),
        ]
        self.assertEqual(fold_fs_claims(recs, now=1005.0, max_skew=0.0)["p"], "inst-1")
        self.assertEqual(fold_fs_claims(recs, now=1060.0, max_skew=0.0)["p"], "inst-2")

    def test_legacy_record_without_ttl_never_expires(self):
        recs = [_req(["p"], "inst-1", lamport=1, epoch_ms=1_000, ttl=None)]
        self.assertEqual(fold_fs_claims(recs, now=10.0 ** 12, max_skew=0.0)["p"], "inst-1")

    def test_max_skew_only_lengthens_a_lease(self):
        # Dead at 1060.0 with no skew; a 5s skew bound keeps it live until 1065.0.
        recs = [_req(["p"], "inst-1", lamport=1, epoch_ms=1_000_000, ttl=60.0)]
        self.assertEqual(fold_fs_claims(recs, now=1061.0, max_skew=0.0), {})
        self.assertEqual(fold_fs_claims(recs, now=1061.0, max_skew=5.0)["p"], "inst-1")
        self.assertEqual(fold_fs_claims(recs, now=1066.0, max_skew=5.0), {})

    def test_max_skew_never_shortens_a_lease(self):
        recs = [_req(["p"], "inst-1", lamport=1, epoch_ms=1_000_000, ttl=60.0)]
        live_no_skew = fold_fs_claims(recs, now=1059.0, max_skew=0.0)
        live_skew = fold_fs_claims(recs, now=1059.0, max_skew=30.0)
        self.assertEqual(live_no_skew, live_skew)

    def test_heartbeat_extends_the_lease(self):
        recs = [
            _req(["p"], "inst-1", lamport=1, epoch_ms=1_000_000, ttl=60.0, lease_id="L1"),
            _hb("L1", "inst-1", lamport=2, epoch_ms=1_050_000, ttl=60.0),
        ]
        # Original deadline 1060.0; heartbeat at 1050.0 pushes it to 1110.0
        self.assertEqual(fold_fs_claims(recs, now=1100.0, max_skew=0.0)["p"], "inst-1")
        self.assertEqual(fold_fs_claims(recs, now=1120.0, max_skew=0.0), {})

    def test_heartbeat_does_not_change_the_sort_key(self):
        # inst-1 wins on lamport 1; its heartbeat at lamport 9 must not demote it.
        recs = [
            _req(["p"], "inst-1", lamport=1, epoch_ms=1_000, lease_id="L1", uuid_="a" * 8),
            _req(["p"], "inst-2", lamport=2, epoch_ms=2_000, lease_id="L2", uuid_="b" * 8),
            _hb("L1", "inst-1", lamport=9, epoch_ms=9_000),
        ]
        self.assertEqual(fold_fs_claims(recs, now=1.0, max_skew=0.0)["p"], "inst-1")

    def test_orphan_heartbeat_grants_nothing(self):
        recs = [_hb("L-missing", "inst-1", lamport=1, epoch_ms=1_000)]
        self.assertEqual(fold_fs_claims(recs, now=1.0, max_skew=0.0), {})

    def test_heartbeat_cannot_resurrect_a_tombstoned_lease(self):
        recs = [
            _req(["p"], "inst-1", lamport=1, epoch_ms=1_000, lease_id="L1"),
            _rel("L1", "inst-1", lamport=2, epoch_ms=2_000),
            _hb("L1", "inst-1", lamport=3, epoch_ms=3_000),
        ]
        self.assertEqual(fold_fs_claims(recs, now=1.0, max_skew=0.0), {})

    def test_multiple_paths_in_one_record(self):
        recs = [_req(["p1", "p2"], "inst-1", lamport=1, epoch_ms=1_000)]
        self.assertEqual(
            fold_fs_claims(recs, now=1.0, max_skew=0.0),
            {"p1": "inst-1", "p2": "inst-1"},
        )

    def test_disjoint_paths_have_independent_winners(self):
        recs = [
            _req(["p1"], "inst-1", lamport=1, epoch_ms=1_000, uuid_="a" * 8),
            _req(["p2"], "inst-2", lamport=2, epoch_ms=2_000, uuid_="b" * 8),
        ]
        self.assertEqual(
            fold_fs_claims(recs, now=1.0, max_skew=0.0),
            {"p1": "inst-1", "p2": "inst-2"},
        )

    def test_corrupt_record_blocks_everything(self):
        """A corrupt record is a LIVE claim by an unknown holder -- fail-closed."""
        recs = [_corrupt(mtime_epoch_ms=1_000_000, ttl=60.0)]
        folded = fold_fs_claims(recs, now=1010.0, max_skew=0.0)
        self.assertEqual(folded.get(FS_UNKNOWN_PATH), FS_UNKNOWN_HOLDER)

    def test_corrupt_record_expires_at_mtime_plus_ttl(self):
        recs = [_corrupt(mtime_epoch_ms=1_000_000, ttl=60.0)]
        self.assertIn(FS_UNKNOWN_PATH, fold_fs_claims(recs, now=1059.0, max_skew=0.0))
        self.assertNotIn(FS_UNKNOWN_PATH, fold_fs_claims(recs, now=1061.0, max_skew=0.0))

    def test_corrupt_record_respects_max_skew(self):
        recs = [_corrupt(mtime_epoch_ms=1_000_000, ttl=60.0)]
        self.assertIn(FS_UNKNOWN_PATH, fold_fs_claims(recs, now=1061.0, max_skew=5.0))

    def test_corrupt_record_coexists_with_good_records(self):
        recs = [
            _req(["p"], "inst-1", lamport=1, epoch_ms=1_000_000, ttl=60.0),
            _corrupt(mtime_epoch_ms=1_000_000, ttl=60.0),
        ]
        folded = fold_fs_claims(recs, now=1010.0, max_skew=0.0)
        self.assertEqual(folded["p"], "inst-1")
        self.assertEqual(folded[FS_UNKNOWN_PATH], FS_UNKNOWN_HOLDER)

    def test_malformed_record_missing_fields_is_ignored_not_crashed(self):
        recs = [{"kind": "claim_requested"}, {}, {"kind": "nonsense", "paths": ["p"]}]
        self.assertEqual(fold_fs_claims(recs, now=1.0, max_skew=0.0), {})

    def test_fold_is_pure_and_order_independent(self):
        a = _req(["p"], "inst-1", lamport=1, epoch_ms=1_000, uuid_="a" * 8)
        b = _req(["p"], "inst-2", lamport=2, epoch_ms=2_000, uuid_="b" * 8)
        forward = fold_fs_claims([a, b], now=1.0, max_skew=0.0)
        reverse = fold_fs_claims([b, a], now=1.0, max_skew=0.0)
        self.assertEqual(forward, reverse)
        # inputs untouched
        self.assertEqual(a["instance_id"], "inst-1")
        self.assertEqual(b["instance_id"], "inst-2")


# ---------------------------------------------------------------------------
# 2. FsClaimLog on a tmpdir, injectable clock, settle=0
# ---------------------------------------------------------------------------

class _Clock:
    """Deterministic, injectable clock."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestFsClaimLog(unittest.TestCase):

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

    def _record_files(self):
        return sorted(p.name for p in Path(self.claims_dir).glob("*.json"))

    def _load(self, name):
        with open(Path(self.claims_dir) / name, encoding="utf-8") as fh:
            return json.load(fh)

    def test_claim_writes_exactly_one_record(self):
        self.log.claim(["a/b.txt"], "inst-1", ttl_seconds=60)
        self.assertEqual(len(self._record_files()), 1)

    def test_record_filename_is_unique_by_construction(self):
        """<lamport>-<epoch_ms>-<instance_id>-<uuid4>.json, no FS locking primitive."""
        self.log.claim(["a"], "host:123:abc", ttl_seconds=60)
        names = self._record_files()
        self.assertEqual(len(names), 1)
        stem = names[0][: -len(".json")]
        parts = stem.split("-")
        self.assertGreaterEqual(len(parts), 4)
        self.assertTrue(parts[0].isdigit(), stem)   # lamport
        self.assertTrue(parts[1].isdigit(), stem)   # epoch_ms
        # instance_id is sanitized for the filename; the real value lives in the JSON
        self.assertNotIn(":", names[0])
        self.assertEqual(self._load(names[0])["instance_id"], "host:123:abc")

    def test_two_claims_never_collide_on_a_filename(self):
        self.log.claim(["p1"], "inst-1", ttl_seconds=60)
        self.log.claim(["p2"], "inst-1", ttl_seconds=60)
        names = self._record_files()
        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names)), 2)

    def test_record_shape(self):
        self.log.claim(["a/B.txt"], "inst-1", ttl_seconds=60)
        rec = self._load(self._record_files()[0])
        for key in ("v", "kind", "paths", "instance_id", "epoch", "lamport",
                    "epoch_ms", "ttl", "uuid", "lease_id"):
            self.assertIn(key, rec)
        self.assertEqual(rec["kind"], "claim_requested")
        # canonical_claim_path(case_policy="insensitive") is applied
        self.assertEqual(rec["paths"], ["a/b.txt"])

    def test_holder_reflects_the_fold(self):
        self.assertIsNone(self.log.holder(["p"]))
        self.log.claim(["p"], "inst-1", ttl_seconds=60)
        self.assertEqual(self.log.holder(["p"]), "inst-1")

    def test_conflict_raises_and_self_tombstones_the_loser(self):
        self.log.claim(["p"], "inst-1", ttl_seconds=60)
        before = len(self._record_files())

        with self.assertRaises(ClaimConflict) as ctx:
            self.log.claim(["p"], "inst-2", ttl_seconds=60)
        self.assertEqual(ctx.exception.conflicting_instance, "inst-1")

        # loser wrote its request AND its own tombstone (fail-closed retract)
        names = self._record_files()
        self.assertEqual(len(names), before + 2)
        kinds = [self._load(n)["kind"] for n in names]
        self.assertEqual(kinds.count("claim_released"), 1)

        # and the loser's request is NOT a phantom holder afterwards
        self.assertEqual(self.log.holder(["p"]), "inst-1")

    def test_release_frees_the_path(self):
        lease_id = self.log.claim(["p"], "inst-1", ttl_seconds=60)
        self.log.release(lease_id, "inst-1")
        self.assertIsNone(self.log.holder(["p"]))
        # someone else may now claim it
        self.log.claim(["p"], "inst-2", ttl_seconds=60)
        self.assertEqual(self.log.holder(["p"]), "inst-2")

    def test_renew_appends_and_never_mutates(self):
        lease_id = self.log.claim(["p"], "inst-1", ttl_seconds=60)
        original_name = self._record_files()[0]
        original_bytes = (Path(self.claims_dir) / original_name).read_bytes()

        self.clock.advance(30)
        self.log.renew(lease_id, "inst-1", ttl_seconds=60)

        names = self._record_files()
        self.assertEqual(len(names), 2)
        self.assertEqual(
            (Path(self.claims_dir) / original_name).read_bytes(), original_bytes
        )
        appended = [n for n in names if n != original_name][0]
        self.assertEqual(self._load(appended)["kind"], "heartbeat")

    def test_renew_extends_the_deadline(self):
        lease_id = self.log.claim(["p"], "inst-1", ttl_seconds=60)
        self.clock.advance(50)
        self.log.renew(lease_id, "inst-1", ttl_seconds=60)
        self.clock.advance(50)  # t = +100, past the ORIGINAL deadline of +60
        self.assertEqual(self.log.holder(["p"]), "inst-1")

    def test_expired_lease_is_stealable(self):
        self.log.claim(["p"], "inst-1", ttl_seconds=60)
        self.clock.advance(61)
        self.assertIsNone(self.log.holder(["p"]))
        self.log.claim(["p"], "inst-2", ttl_seconds=60)
        self.assertEqual(self.log.holder(["p"]), "inst-2")

    def test_renew_expired_lease_raises(self):
        lease_id = self.log.claim(["p"], "inst-1", ttl_seconds=30)
        self.clock.advance(40)
        with self.assertRaises(ValueError) as ctx:
            self.log.renew(lease_id, "inst-1", ttl_seconds=60)
        self.assertIn("expired", str(ctx.exception).lower())

    def test_renew_released_lease_raises(self):
        lease_id = self.log.claim(["p"], "inst-1", ttl_seconds=60)
        self.log.release(lease_id, "inst-1")
        with self.assertRaises(ValueError) as ctx:
            self.log.renew(lease_id, "inst-1", ttl_seconds=60)
        self.assertIn("released", str(ctx.exception).lower())

    def test_renew_unknown_lease_raises(self):
        with self.assertRaises(ValueError):
            self.log.renew("no-such-lease", "inst-1", ttl_seconds=60)

    def test_release_unknown_lease_raises(self):
        with self.assertRaises(ValueError):
            self.log.release("no-such-lease", "inst-1")

    def test_lamport_is_monotonic_across_records(self):
        self.log.claim(["p1"], "inst-1", ttl_seconds=60)
        self.log.claim(["p2"], "inst-1", ttl_seconds=60)
        self.log.claim(["p3"], "inst-1", ttl_seconds=60)
        lamports = [self._load(n)["lamport"] for n in self._record_files()]
        self.assertEqual(sorted(lamports), lamports)
        self.assertEqual(len(set(lamports)), 3)

    def test_lamport_advances_past_a_peers_record(self):
        """A second instance's log view adopts the peer's lamport high-water mark."""
        peer = FsClaimLog(
            self.claims_dir, clock=self.clock, settle_seconds=0.0, max_skew_seconds=0.0
        )
        self.log.claim(["p1"], "inst-1", ttl_seconds=60)
        mine = self._load(self._record_files()[0])["lamport"]
        peer.claim(["p2"], "inst-2", ttl_seconds=60)
        theirs = max(self._load(n)["lamport"] for n in self._record_files())
        self.assertGreater(theirs, mine)
        peer.close()

    def test_corrupt_record_blocks_new_claims(self):
        """Fail-closed: an unparseable record is a live claim by an unknown holder."""
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        torn = Path(self.claims_dir) / "000000000001-1000000-inst9-deadbeef.json"
        torn.write_text('{"kind": "claim_requ', encoding="utf-8")

        with self.assertRaises(ClaimConflict) as ctx:
            self.log.claim(["anything"], "inst-1", ttl_seconds=60)
        self.assertEqual(ctx.exception.conflicting_instance, FS_UNKNOWN_HOLDER)

    def test_corrupt_record_stops_blocking_after_its_ttl(self):
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        torn = Path(self.claims_dir) / "000000000001-1000000-inst9-deadbeef.json"
        torn.write_text("not json at all", encoding="utf-8")
        # mtime is real wall-clock; drive the injected clock far past mtime + ttl
        self.clock.t = os.path.getmtime(torn) + 10_000.0
        lease_id = self.log.claim(["anything"], "inst-1", ttl_seconds=60)
        self.assertIsNotNone(lease_id)

    def test_empty_record_file_is_treated_as_corrupt(self):
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        (Path(self.claims_dir) / "000000000001-1000000-x-y.json").write_text(
            "", encoding="utf-8"
        )
        with self.assertRaises(ClaimConflict):
            self.log.claim(["anything"], "inst-1", ttl_seconds=60)

    def test_non_json_files_in_the_dir_are_ignored(self):
        Path(self.claims_dir).mkdir(parents=True, exist_ok=True)
        (Path(self.claims_dir) / "README.txt").write_text("hello", encoding="utf-8")
        lease_id = self.log.claim(["p"], "inst-1", ttl_seconds=60)
        self.assertIsNotNone(lease_id)

    def test_missing_directory_is_created_lazily(self):
        self.assertFalse(Path(self.claims_dir).exists())
        self.assertIsNone(self.log.holder(["p"]))
        self.log.claim(["p"], "inst-1", ttl_seconds=60)
        self.assertTrue(Path(self.claims_dir).is_dir())

    def test_settle_window_is_observed_before_revalidation(self):
        """claim() sleeps `settle` between the append and the revalidating list."""
        calls = []
        log = FsClaimLog(
            self.claims_dir,
            clock=self.clock,
            settle_seconds=5.0,
            max_skew_seconds=0.0,
            sleep=calls.append,
        )
        log.claim(["p"], "inst-1", ttl_seconds=60)
        self.assertEqual(calls, [5.0])
        log.close()

    def test_no_fs_locking_primitives_are_used(self):
        """The design forbids flock / O_EXCL / link tricks -- assert on the source."""
        src = Path(_REPO_ROOT / "state_store" / "fs_claim_log.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("flock", "O_EXCL", "os.link(", "lockf", "msvcrt"):
            self.assertNotIn(forbidden, src, f"forbidden FS primitive: {forbidden}")

    def test_paths_are_canonicalized_case_insensitively(self):
        self.log.claim(["Dir/File.TXT"], "inst-1", ttl_seconds=60)
        self.assertEqual(self.log.holder(["dir/file.txt"]), "inst-1")

    def test_holder_of_empty_path_list_is_none(self):
        self.assertIsNone(self.log.holder([]))


# ---------------------------------------------------------------------------
# 3. The Inc 2 ClaimBackend contract suite, re-parametrized onto FsClaimLog
# ---------------------------------------------------------------------------

class FsClaimLogContractTests(ClaimBackendContractTests):
    """Run the UNMODIFIED Inc 2 contract suite against FsClaimLog."""

    def backend_factory(self, db_path: str) -> ClaimBackend:
        claims_dir = str(Path(db_path).parent / "fs-claims")
        return FsClaimLog(claims_dir, settle_seconds=0.0, max_skew_seconds=0.0)


# The base class is concrete (it tests LocalLeaseBackend) and is already run by
# tests/test_claim_backend.py; drop the imported name so unittest does not
# re-collect it here.
del ClaimBackendContractTests


# ---------------------------------------------------------------------------
# 4. The four 47c967b split-brain regressions, replayed through FsClaimLog
# ---------------------------------------------------------------------------

class TestFsClaimLogSplitBrain(unittest.TestCase):
    """47c967b regression contract, host-independent via case_policy='insensitive'."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.clock = _Clock(1000.0)
        self.log = FsClaimLog(
            str(Path(self.temp_dir.name) / "claims"),
            clock=self.clock,
            settle_seconds=0.0,
            max_skew_seconds=0.0,
            case_policy="insensitive",
        )

    def tearDown(self):
        self.log.close()
        self.temp_dir.cleanup()

    def test_47c967b_separator_split_brain_rejected(self):
        """'dir/f' and 'dir\\f' are the same file on EVERY box."""
        self.log.claim(["dir/file.txt"], "inst-1", ttl_seconds=60)
        with self.assertRaises(ClaimConflict) as ctx:
            self.log.claim(["dir\\file.txt"], "inst-2", ttl_seconds=60)
        self.assertEqual(ctx.exception.conflicting_instance, "inst-1")

    def test_47c967b_case_split_brain_rejected(self):
        """'README.md' and 'README.MD' collide under the insensitive policy."""
        self.log.claim(["README.md"], "inst-1", ttl_seconds=60)
        with self.assertRaises(ClaimConflict) as ctx:
            self.log.claim(["README.MD"], "inst-2", ttl_seconds=60)
        self.assertEqual(ctx.exception.conflicting_instance, "inst-1")

    def test_47c967b_renew_on_expired_lease_raises(self):
        lease_id = self.log.claim(["file1.txt"], "inst-1", ttl_seconds=30)
        self.clock.advance(40)
        with self.assertRaises(ValueError) as ctx:
            self.log.renew(lease_id, "inst-1", ttl_seconds=60)
        self.assertIn("expired", str(ctx.exception).lower())

    def test_47c967b_renew_on_released_lease_raises(self):
        lease_id = self.log.claim(["file1.txt"], "inst-1", ttl_seconds=60)
        self.log.release(lease_id, "inst-1")
        with self.assertRaises(ValueError) as ctx:
            self.log.renew(lease_id, "inst-1", ttl_seconds=60)
        self.assertIn("released", str(ctx.exception).lower())

    def test_heterogeneous_boxes_agree_on_the_canonical_key(self):
        """The claim record stores the host-independent canonical form."""
        self.log.claim(["Dir\\SUB/File.TXT"], "inst-1", ttl_seconds=60)
        rec_path = next(Path(self.log.claims_dir).glob("*.json"))
        with open(rec_path, encoding="utf-8") as fh:
            rec = json.load(fh)
        self.assertEqual(rec["paths"], ["dir/sub/file.txt"])


if __name__ == "__main__":
    unittest.main()
