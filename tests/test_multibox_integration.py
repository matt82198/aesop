"""Simulated-multibox integration proof (multibox Increment 6).

Everything below this line runs against `state_store/fs_claim_log.py` and
`state_store/failover.py` **exactly as shipped** -- no product code is stubbed,
patched or subclassed. The only substitution is `os.listdir` as seen by the claim
log, which is where a real NFS/SMB share differs from a local tmpdir
(`tests/multibox_sim.py` explains the model).

Six assertions, and the first two are a matched pair:

1. NO DOUBLE-GRANT -- 200 seeded randomized rounds with ``delay <= settle`` never
   grant one path to two instances.
2. FALSIFIABILITY -- the SAME harness, with ``delay > settle``, DOES observe the
   double grant.

(2) is what makes (1) worth reading. A safety property that cannot fail proves
nothing about the mechanism it is supposed to be testing, and "no double grant"
is trivially satisfiable by a harness that never creates contention. Together
they say something falsifiable: the settle window is load-bearing, which in turn
makes Inc 0's measurement of the real visibility delay a genuine precondition
rather than a ceremony.

3. LIVENESS -- a SIGKILLed instance's claims are reclaimable within
   ``ttl + max_skew + settle``, and not before.
4. FAILOVER -- a killed primary yields exactly one successor; the revived old
   primary is fenced.
5. CONVERGENCE -- every instance's fold agrees once the share quiesces, including
   across a partition heal.
6. REAL-FS SMOKE -- the undecorated tmpdir path, real clock, real
   ``settle=0.05`` sleeps, real threads.

Hermetic: tempdirs only, no cwd pollution, no network. The only real sleeping is
assertion 6's 0.05s settle window; everywhere else time is virtual.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from state_store import failover as failover_mod  # noqa: E402
from state_store.claim_backend import ClaimConflict  # noqa: E402
from state_store.fs_claim_log import (  # noqa: E402
    FS_UNKNOWN_HOLDER,
    FS_UNKNOWN_PATH,
    FsClaimLog,
)

try:  # `unittest discover -s tests` puts tests/ on sys.path as a top-level dir
    import multibox_sim as sim
except ImportError:  # `python -m unittest tests.test_multibox_integration`
    from tests import multibox_sim as sim


SETTLE = 1.0
#: Assertion 1's run count. Fixed by the Inc 6 spec; the whole sweep is virtual
#: time, so it costs a few seconds of file I/O and no sleeping at all.
SWEEP_RUNS = 200


class _TempDirCase(unittest.TestCase):
    """Base: a private tempdir per test, removed afterwards. Never touches cwd."""

    def setUp(self):
        """Create the per-test tempdir."""
        self.tmp = tempfile.mkdtemp(prefix="multibox-sim-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def claims_dir(self, name: str = "claims") -> str:
        """Path of a fresh claim-log directory inside this test's tempdir."""
        return os.path.join(self.tmp, name)


# ---------------------------------------------------------------------------
# 1 + 2: the pair that matters
# ---------------------------------------------------------------------------

class TestNoDoubleGrant(_TempDirCase):
    """Assertion 1: the load-bearing safety property, over 200 seeded rounds."""

    def test_no_double_grant_across_seeded_sweep(self):
        """200 randomized rounds with delay <= settle grant no path twice."""
        report = sim.sweep_no_double_grant(
            lambda index: os.path.join(self.tmp, "run%03d" % index, "claims"),
            runs=SWEEP_RUNS, settle=SETTLE,
        )
        self.assertEqual(report["runs"], SWEEP_RUNS)
        self.assertEqual(
            report["violations"], [],
            "double grant under delay <= settle: %r" % (report["violations"][:3],),
        )

    def test_sweep_actually_contends(self):
        """The sweep is not vacuous: most rounds really do fight over a path.

        A "no double grant" pass is worthless if nothing ever contends, so the
        contention rate is asserted directly rather than assumed.
        """
        report = sim.sweep_no_double_grant(
            lambda index: os.path.join(self.tmp, "c%03d" % index, "claims"),
            runs=40, settle=SETTLE, base_seed=9000,
        )
        self.assertGreater(report["contended_runs"], 20)
        self.assertGreater(report["grants"], 40)

    def test_rounds_are_deterministic(self):
        """Same seed, fresh directory, identical decisions.

        Threads make each round physically non-deterministic; if the harness's
        parameter box holds, the decisions remain a pure function of the seed.
        Without this, a green sweep could just be a lucky interleaving.
        """
        import random

        for seed in range(5):
            specs = sim.random_round(random.Random(seed), settle=SETTLE)
            self.assertTrue(
                sim.round_is_deterministic(
                    os.path.join(self.tmp, "d%d-a" % seed),
                    os.path.join(self.tmp, "d%d-b" % seed),
                    specs, settle=SETTLE, seed=seed,
                ),
                "round %d is not reproducible" % seed,
            )


class TestFalsifiability(_TempDirCase):
    """Assertion 2: with delay > settle the harness DOES catch a double grant."""

    def test_delay_beyond_settle_double_grants(self):
        """Two blind instances both grant the same path once delay exceeds settle.

        This is the load-bearing negative control. It proves the settle window is
        the mechanism doing the work in assertion 1 -- not the sort key, not the
        tmpdir, not luck.
        """
        outcomes = sim.run_claim_round(
            self.claims_dir("broken"),
            sim.blind_pair(settle=SETTLE, delay=SETTLE * 3),
            settle=SETTLE, seed=7,
        )
        self.assertTrue(all(o.granted for o in outcomes))
        self.assertEqual(
            sim.find_double_grants(outcomes),
            [("src/shared.py", ["boxA:1:sim", "boxB:2:sim"])],
        )

    def test_same_pair_is_safe_within_settle(self):
        """The identical pair, delay <= settle: exactly one grant, one conflict.

        Same instances, same paths, same seed -- only the visibility delay moves.
        That isolates the settle window as the single variable.
        """
        outcomes = sim.run_claim_round(
            self.claims_dir("healthy"),
            sim.blind_pair(settle=SETTLE, delay=SETTLE * 0.9),
            settle=SETTLE, seed=7,
        )
        self.assertEqual([o.granted for o in outcomes], [True, False])
        self.assertEqual(outcomes[1].conflict_with, "boxA:1:sim")
        self.assertEqual(sim.find_double_grants(outcomes), [])

    def test_falsifiability_holds_across_seeds(self):
        """The negative control is not one lucky seed."""
        caught = 0
        for seed in range(10):
            outcomes = sim.run_claim_round(
                os.path.join(self.tmp, "f%d" % seed),
                sim.blind_pair(settle=SETTLE, delay=SETTLE * 3),
                settle=SETTLE, seed=seed,
            )
            if sim.find_double_grants(outcomes):
                caught += 1
        self.assertEqual(caught, 10)


# ---------------------------------------------------------------------------
# 3: liveness after a real kill
# ---------------------------------------------------------------------------

class TestLivenessAfterKill(_TempDirCase):
    """Assertion 3: a killed instance's claims come back, on schedule."""

    TTL = 60.0
    MAX_SKEW = 2.0
    KILL_AT = 1000.0

    def _kill_a_holder(self, claims_dir):
        """Claim src/orphan.py in a separate process, then SIGKILL-equivalent it."""
        returncode, result = sim.run_driver({
            "mode": "claim", "claims_dir": claims_dir,
            "instance_id": "boxDead:1:sim", "paths": ["src/orphan.py"],
            "ttl": self.TTL, "start": self.KILL_AT, "settle": 0.0,
            "max_skew": self.MAX_SKEW, "kill": True,
        })
        self.assertEqual(returncode, sim.KILLED_EXIT_CODE)
        self.assertTrue(result["granted"], result)
        return result

    def test_killed_instance_leaves_a_live_claim_behind(self):
        """No tombstone, no release: the wreckage a killed box actually leaves."""
        claims_dir = self.claims_dir()
        self._kill_a_holder(claims_dir)
        names = os.listdir(claims_dir)
        self.assertEqual(len(names), 1, names)
        backend = FsClaimLog(claims_dir, clock=lambda: self.KILL_AT + 1,
                             settle_seconds=0.0, max_skew_seconds=self.MAX_SKEW)
        self.assertEqual(backend.holder(["src/orphan.py"]), "boxDead:1:sim")

    def test_not_reclaimable_before_the_bound(self):
        """Inside ttl + max_skew the dead instance's lease still blocks.

        The half that makes the next test meaningful: reclamation that happened
        too early would be a correctness bug wearing a liveness costume.
        """
        claims_dir = self.claims_dir()
        self._kill_a_holder(claims_dir)
        early = self.KILL_AT + self.TTL  # folds at +settle, still under the bound
        outcomes = sim.run_claim_round(
            claims_dir,
            [sim.InstanceSpec("boxNew:2:sim", ["src/orphan.py"],
                              start=early, delay=SETTLE * 0.9)],
            settle=SETTLE, max_skew=self.MAX_SKEW, seed=1,
        )
        self.assertFalse(outcomes[0].granted)
        self.assertEqual(outcomes[0].conflict_with, "boxDead:1:sim")

    def test_reclaimed_within_ttl_plus_skew_plus_settle(self):
        """Past ttl + max_skew + settle a live peer takes the orphaned path."""
        claims_dir = self.claims_dir()
        self._kill_a_holder(claims_dir)
        deadline = self.KILL_AT + self.TTL + self.MAX_SKEW + SETTLE
        outcomes = sim.run_claim_round(
            claims_dir,
            [sim.InstanceSpec("boxNew:2:sim", ["src/orphan.py"],
                              start=deadline, delay=SETTLE * 0.9)],
            settle=SETTLE, max_skew=self.MAX_SKEW, seed=1,
        )
        self.assertTrue(outcomes[0].granted, outcomes[0])
        self.assertEqual(sim.find_double_grants(outcomes), [])


# ---------------------------------------------------------------------------
# 4: failover and fencing
# ---------------------------------------------------------------------------

class TestFailoverAfterKill(_TempDirCase):
    """Assertion 4: exactly one successor, and the revenant is fenced."""

    TTL = 60.0
    ELECT_AT = 1000.0

    def setUp(self):
        """Elect a primary in a separate process and kill it mid-term."""
        super().setUp()
        self.dir = self.claims_dir()
        returncode, result = sim.run_driver({
            "mode": "elect", "claims_dir": self.dir,
            "instance_id": "boxOld:1:sim", "ttl": self.TTL,
            "start": self.ELECT_AT, "settle": 0.0, "epoch": 1, "kill": True,
        })
        self.assertEqual(returncode, sim.KILLED_EXIT_CODE)
        self.assertEqual(result["primary"], "boxOld:1:sim")
        self.assertEqual(result["generation"], 1)
        self.takeover_at = self.ELECT_AT + self.TTL + SETTLE
        self.outcomes = sim.run_election_round(
            self.dir,
            [sim.InstanceSpec("box%d:%d:sim" % (i, i), [],
                              start=self.takeover_at, delay=SETTLE * 0.9)
             for i in (1, 2, 3)],
            settle=SETTLE, ttl_seconds=self.TTL, seed=3,
        )

    def _observe(self, now=None):
        backend = FsClaimLog(self.dir, clock=lambda: now or self.takeover_at + SETTLE,
                             settle_seconds=0.0)
        return failover_mod.observe_primary(backend)

    def test_exactly_one_successor(self):
        """Three simultaneous challengers, one winner, unanimously reported."""
        winners = [o.instance_id for o in self.outcomes if o.granted]
        self.assertEqual(len(winners), 1, self.outcomes)
        observed = {o.lease_id for o in self.outcomes}
        self.assertEqual(observed, set(winners))

    def test_generation_advanced_exactly_once(self):
        """One takeover bumps the fence by one, no matter how many challenged."""
        self.assertEqual({o.paths[0] for o in self.outcomes}, {"generation:2"})
        self.assertEqual(self._observe().generation, 2)

    def test_revived_old_primary_is_fenced(self):
        """The returning generation-1 primary is refused, definitively.

        The partition-not-death case: the old primary never learned it lost the
        lock, so nothing but the fence stops it from driving alongside its
        successor.
        """
        state = self._observe()
        revenant = failover_mod.FencingToken("boxOld:1:sim", epoch=1, generation=1)
        with self.assertRaises(failover_mod.FencedWriteError) as caught:
            failover_mod.assert_fenced(revenant, state.generation)
        self.assertEqual(caught.exception.token_generation, 1)
        self.assertEqual(caught.exception.current_generation, 2)

    def test_successor_may_still_write(self):
        """Fencing refuses the revenant without refusing the live primary."""
        state = self._observe()
        self.assertIsNotNone(state.token())
        self.assertEqual(
            failover_mod.fenced_write(state.token(), state.generation, lambda: "ok"),
            "ok",
        )

    def test_generation_never_decreases(self):
        """The fence survives the successor releasing the lock.

        Generation is folded over ALL lock records, expired and tombstoned
        included, so a released lock cannot rewind the fence and re-admit the
        revenant.
        """
        before = self._observe().generation
        backend = FsClaimLog(self.dir, clock=lambda: self.takeover_at + SETTLE,
                             settle_seconds=0.0)
        state = self._observe()
        backend.release(state.lease_id, state.instance_id)
        after = failover_mod.observe_primary(backend)
        self.assertIsNone(after.instance_id)
        self.assertGreaterEqual(after.generation, before)
        self.assertEqual(after.generation, 2)


# ---------------------------------------------------------------------------
# 5: convergence
# ---------------------------------------------------------------------------

class TestConvergence(_TempDirCase):
    """Assertion 5: every instance folds the same answer once the share settles."""

    def test_folds_agree_at_quiescence(self):
        """After a contended round, all instances converge on the ground truth."""
        claims_dir = self.claims_dir()
        specs = sim.random_round(__import__("random").Random(42), settle=SETTLE)
        sim.run_claim_round(claims_dir, specs, settle=SETTLE, seed=42)

        quiet = max(s.start for s in specs) + SETTLE * 10
        truth = sim.observe_fold(claims_dir, None, quiet)
        for spec in specs:
            view = sim.DelayedShareView(
                spec.instance_id, lambda: quiet,
                delay=spec.delay, jitter=spec.jitter, seed=42,
            )
            self.assertEqual(sim.observe_fold(claims_dir, view, quiet), truth)
        self.assertNotIn(FS_UNKNOWN_PATH, truth)

    def test_partition_diverges_then_heals(self):
        """A partitioned instance disagrees, then agrees the moment it reconnects.

        The SMB-reconnect model: nothing trickles across the split, then the whole
        backlog lands in one listing. Convergence has to survive that step change,
        not just a smooth lag.
        """
        claims_dir = self.claims_dir()
        specs = [
            sim.InstanceSpec("boxA:1:sim", ["src/a.py"], start=0.0, delay=SETTLE * 0.9),
            sim.InstanceSpec("boxB:2:sim", ["src/b.py"], start=0.0, delay=SETTLE * 0.9),
        ]
        sim.run_claim_round(claims_dir, specs, settle=SETTLE, seed=11)

        heal_at = 100.0
        during, after = 50.0, 200.0

        def cut(viewer, now):
            return sim.PartitionedShareView(
                viewer, lambda: now, isolated="boxB:2:sim", start=0.0, end=heal_at,
            )

        split = sim.observe_fold(claims_dir, cut("boxA:1:sim", during), during)
        self.assertEqual(sorted(split), ["src/a.py"])

        truth = sim.observe_fold(claims_dir, None, after)
        self.assertEqual(sorted(truth), ["src/a.py", "src/b.py"])
        for viewer in ("boxA:1:sim", "boxB:2:sim"):
            self.assertEqual(sim.observe_fold(claims_dir, cut(viewer, after), after), truth)


class TestTornWriteFailsClosed(_TempDirCase):
    """A half-published record blocks every grant, rather than being skipped.

    ``store.py`` rightly skips a corrupt event payload on read. A claim log cannot:
    the truncated bytes might be somebody's live claim on the very path we are
    about to take. This is that distinction, exercised through a real truncated
    file rather than a hand-built record dict.
    """

    def test_truncated_record_blocks_the_grant(self):
        """A torn record folds to FS_UNKNOWN and no claim is granted."""
        claims_dir = self.claims_dir()
        clock = sim.SimClock(10.0)
        torn = sim.TornWriteShareView(claims_dir, clock, inject_at=0.0)
        torn.inject()

        fold = sim.observe_fold(claims_dir, None, 10.0)
        self.assertEqual(fold.get(FS_UNKNOWN_PATH), FS_UNKNOWN_HOLDER)

        backend = FsClaimLog(claims_dir, clock=lambda: 10.0, settle_seconds=0.0)
        with self.assertRaises(ClaimConflict) as caught:
            backend.claim(["src/unrelated.py"], "boxA:1:sim", 300.0)
        self.assertEqual(caught.exception.conflicting_instance, FS_UNKNOWN_HOLDER)

    def test_torn_record_appears_only_after_its_instant(self):
        """The view injects at a chosen virtual time, not at construction."""
        claims_dir = self.claims_dir()
        os.makedirs(claims_dir, exist_ok=True)
        clock = sim.SimClock(0.0)
        torn = sim.TornWriteShareView(claims_dir, clock, inject_at=5.0)
        self.assertEqual(torn.listdir(claims_dir), [])
        clock.advance(5.0)
        self.assertIn(torn.injected_name, torn.listdir(claims_dir))


# ---------------------------------------------------------------------------
# 6: the undecorated filesystem
# ---------------------------------------------------------------------------

class TestRealFilesystemSmoke(_TempDirCase):
    """Assertion 6: no simulator at all -- real clock, real sleeps, real threads.

    Every other assertion runs through a substituted ``listdir``. This one does
    not, so it is the check that the simulator has not quietly become the thing
    under test: the same protocol, on a real tmpdir, with a real 0.05s settle
    window, must still grant a contended path exactly once.
    """

    SETTLE = 0.05

    def test_two_real_threads_contend_once(self):
        """Two backends on a real tmpdir, real time: exactly one winner."""
        claims_dir = self.claims_dir()
        results: dict = {}
        barrier = threading.Barrier(2, timeout=30)

        def contend(name):
            backend = FsClaimLog(claims_dir, settle_seconds=self.SETTLE)
            try:
                barrier.wait()
                results[name] = backend.claim(["src/real.py"], name, 300.0)
            except ClaimConflict:
                results[name] = None

        threads = [threading.Thread(target=contend, args=("boxR%d:%d:sim" % (i, i),))
                   for i in (1, 2)]
        started = time.time()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        elapsed = time.time() - started

        self.assertEqual(len(results), 2)
        granted = [name for name, lease in results.items() if lease]
        self.assertEqual(len(granted), 1, results)
        # The settle window really was waited out, and only once.
        self.assertGreaterEqual(elapsed, self.SETTLE)
        self.assertLess(elapsed, 10.0)

    def test_real_settle_round_trip(self):
        """Claim, renew and release on the real filesystem with a real settle."""
        claims_dir = self.claims_dir()
        backend = FsClaimLog(claims_dir, settle_seconds=self.SETTLE)
        lease = backend.claim(["src/solo.py"], "boxR:9:sim", 300.0)
        self.assertEqual(backend.holder(["src/solo.py"]), "boxR:9:sim")
        backend.renew(lease, "boxR:9:sim", 300.0)
        backend.release(lease, "boxR:9:sim")
        self.assertIsNone(backend.holder(["src/solo.py"]))


# ---------------------------------------------------------------------------
# Harness self-checks: the simulator itself must be trustworthy
# ---------------------------------------------------------------------------

class TestHarnessItself(_TempDirCase):
    """The simulator is test infrastructure, so its own claims need proving."""

    def test_record_name_round_trips(self):
        """Visibility is decided from the filename, so decoding must be exact."""
        claims_dir = self.claims_dir()
        backend = FsClaimLog(claims_dir, clock=lambda: 12.345, settle_seconds=0.0)
        backend.claim(["src/x.py"], "boxN:7:sim", 300.0)
        (name,) = os.listdir(claims_dir)
        info = sim.parse_record_name(name)
        self.assertIsNotNone(info)
        self.assertEqual(info.epoch_ms, 12345)
        self.assertEqual(info.written_at, 12.345)
        self.assertEqual(info.owner_token, "boxN_7_sim")

    def test_non_record_names_are_never_hidden(self):
        """Scratch and foreign files pass through: lag is modelled, not invented."""
        view = sim.DelayedShareView("boxA:1:sim", lambda: 0.0, delay=99.0,
                                    inner=_StaticView(["x.json.tmp", "notes.txt"]))
        self.assertEqual(sorted(view.listdir(".")), ["notes.txt", "x.json.tmp"])

    def test_writer_always_sees_its_own_record(self):
        """The asymmetry that makes the hazard real."""
        own = "000000000001-0-boxA_1_sim-%s.json" % ("0" * 8)
        peer = "000000000001-0-boxB_2_sim-%s.json" % ("0" * 8)
        view = sim.DelayedShareView("boxA:1:sim", lambda: 0.0, delay=99.0,
                                    inner=_StaticView([own, peer]))
        self.assertEqual(view.listdir("."), [own])

    def test_jitter_is_seeded_and_stable(self):
        """Same seed, same lag -- reruns of a failing seed reproduce it."""
        names = ["000000000001-0-boxB_2_sim-%s.json" % ("0" * 8)]
        a = sim.DelayedShareView("boxA:1:sim", lambda: 0.0, delay=1.0, jitter=1.0,
                                 seed=5, inner=_StaticView(names))
        b = sim.DelayedShareView("boxA:1:sim", lambda: 0.0, delay=1.0, jitter=1.0,
                                 seed=5, inner=_StaticView(names))
        c = sim.DelayedShareView("boxA:1:sim", lambda: 0.0, delay=1.0, jitter=1.0,
                                 seed=6, inner=_StaticView(names))
        self.assertEqual(a.effective_delay(names[0]), b.effective_delay(names[0]))
        self.assertNotEqual(a.effective_delay(names[0]), c.effective_delay(names[0]))
        self.assertGreaterEqual(a.effective_delay(names[0]), 1.0)
        self.assertLess(a.effective_delay(names[0]), 2.0)

    def test_fabric_restores_the_real_os(self):
        """The listing patch is scoped; a failing assertion cannot leak it."""
        from state_store import fs_claim_log as module
        original = module.os
        fabric = sim.ShareFabric()
        try:
            with fabric:
                self.assertIsNot(module.os, original)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertIs(module.os, original)

    def test_clock_quantizes_to_milliseconds(self):
        """Sub-millisecond virtual time would be invisible to the fold's sort key."""
        clock = sim.SimClock(0.0)
        clock.advance(0.00049)
        self.assertEqual(clock(), 0.0)
        clock.advance(0.0006)
        self.assertEqual(clock(), 0.001)

    def test_round_rejects_zero_settle(self):
        """settle=0 skips the product's sleep, so the rendezvous cannot be hung."""
        with self.assertRaises(ValueError):
            sim.run_claim_round(self.claims_dir(),
                                [sim.InstanceSpec("boxA:1:sim", ["src/a.py"])],
                                settle=0.0)

    def test_random_round_stays_inside_the_box(self):
        """Generated rounds keep max start spread < min delay <= settle."""
        import random

        for seed in range(50):
            specs = sim.random_round(random.Random(seed), settle=SETTLE)
            spread = max(s.start for s in specs) - min(s.start for s in specs)
            worst = max(s.delay + s.jitter for s in specs)
            best = min(s.delay for s in specs)
            self.assertLessEqual(worst, SETTLE)
            self.assertLess(spread, best)
            self.assertGreaterEqual(len(specs), 2)


class _StaticView(sim.ShareView):
    """A view over a fixed name list, for unit-testing the views themselves."""

    def __init__(self, names):
        """Wrap a fixed list of directory entries."""
        self.names = list(names)

    def listdir(self, path):
        """Return the fixed list, ignoring ``path``."""
        return list(self.names)


if __name__ == "__main__":
    unittest.main()
