#!/usr/bin/env python3
"""RS3-W: round-2 wave-engine robustness tests (verified /refinesystem defects).

TDD coverage, all offline (no API key, no network):
  N1  live-seat ceiling re-check must not crash on None worker spend
      (ClaudeCodeDriver.get_tokens_spent() is None BY CONTRACT): the flagship
      "Claude worker + swapped orchestrator seat" wave completes Phase 6 and
      reaches ship without TypeError.
  N3  recovery livelock: a journal-resumed verified item restores its
      filesWritten, SHIPS, and reaches a terminal tracker state exactly once
      (scheduler does not re-select it next wave). A verified item with no
      files still gets a terminal shipped record (no_changes).
  N4  stale-lease release uses the SAME key shape claims use (slug string,
      not (repo, slug) tuple); coordination enforces TTL expiry so a crashed
      instance's claim does not persist forever.
  N5  claim exception fails CLOSED: the item is skipped, never dispatched
      (and never repair-dispatched) without a held claim.
  N6  _quote_arg must not double every backslash on Windows (git add
      pathspec for `src\\util.py` must match).
  N7  no silent item-vanish (executor exception -> recorded failed item);
      green is False when zero items ran; duplicate slugs rejected loudly.
  N10 journal entries are bound to item CONTENT (fingerprint): a new item
      reusing a prior wave's slug is rebuilt, never skipped; journal writes
      are atomic (temp + os.replace); torn entries never corrupt resume.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

# Add driver/, tools/, state_store/ and repo root to path for imports.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
TOOLS_DIR = REPO / "tools"
STATE_STORE_DIR = REPO / "state_store"
for _p in (str(DRIVER_DIR), str(TOOLS_DIR), str(STATE_STORE_DIR), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import agent_driver as ad  # noqa: E402
from agent_driver import (  # noqa: E402
    AgentDriver,
    CommandResult,
    DriverCapabilities,
    WorkerRequest,
    WorkerResult,
    WORKER_DONE,
    WORKER_FAILED,
)
import wave_loop  # noqa: E402
from wave_loop import (  # noqa: E402
    run_wave,
    result_to_report,
    _quote_arg,
    _item_fingerprint,
    _write_journal_entry,
    _journal_key_for_item,
    _load_journal_state,
    _release_stale_leases,
)
from orchestrator_backend import FakeOrchestratorBackend  # noqa: E402
from claude_code_driver import ClaudeCodeDriver  # noqa: E402
import coordination  # noqa: E402
from state_store import store as sstore  # noqa: E402
import wave_scheduler as ws  # noqa: E402

# Module-level tmpdir isolation (hygiene rule: no cwd pollution).
_MODULE_TMP = None
_MODULE_SAVED_CWD = None


def setUpModule():
    global _MODULE_TMP, _MODULE_SAVED_CWD
    _MODULE_SAVED_CWD = os.getcwd()
    _MODULE_TMP = tempfile.mkdtemp(prefix="wave-loop-rs3-tests-")
    os.chdir(_MODULE_TMP)


def tearDownModule():
    global _MODULE_TMP, _MODULE_SAVED_CWD
    if _MODULE_SAVED_CWD:
        os.chdir(_MODULE_SAVED_CWD)
    if _MODULE_TMP:
        shutil.rmtree(_MODULE_TMP, ignore_errors=True)


_MERGE_DECISION = {
    "verdict": "merge",
    "evidence": ["test exit code 0; no red flags"],
    "confidence": 0.95,
}


class NoneMeterFakeBackend(FakeOrchestratorBackend):
    """Orchestrator seat backend whose spend is NOT observable (None).

    Mirrors real backends that do not meter: the HS-2 Fake returned an int
    and hid the N1 TypeError."""

    def get_tokens_spent(self):
        return None


class DispatchingFakeDriver(AgentDriver):
    """Tier-2 fake worker driver: dispatch writes owned files, tests pass."""

    def __init__(self):
        self.dispatch_count = 0
        self.total_tokens = 0
        self._workers = {}

    def probe_capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            name="rs3-fake-driver",
            parallel_dispatch=False,
            worker_filesystem_access=False,
            worker_shell_access=False,
            structured_output=False,
            worktree_isolation=False,
            native_cost_tracking=False,
            native_stall_detection=False,
            tool_use_accuracy=0.92,
            recommended_verification_tier=2,
            available_models=("fake-model",),
            notes="RS3 offline fake driver",
        )

    def dispatch_worker(self, request: WorkerRequest) -> WorkerResult:
        self.dispatch_count += 1
        self.total_tokens += 100
        worker_id = "worker-%d" % self.dispatch_count
        self._workers[worker_id] = {"status": WORKER_DONE}
        workdir = Path(request.workdir) if request.workdir else Path(".")
        files_written = []
        try:
            for f in request.owned_files:
                fpath = workdir / f
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text("# fake dispatch\n")
                files_written.append(f)
        except Exception as exc:
            return WorkerResult(
                worker_id=worker_id,
                status=WORKER_FAILED,
                ok=False,
                error="file write failed: %s" % exc,
            )
        return WorkerResult(
            worker_id=worker_id,
            status=WORKER_DONE,
            ok=True,
            structured={"summary": "done"},
            files_written=tuple(files_written),
            tokens_spent=100,
        )

    def worker_status(self, worker_id):
        if worker_id in self._workers:
            return ad.WorkerStatus(
                worker_id=worker_id, state=self._workers[worker_id]["status"]
            )
        return ad.WorkerStatus(worker_id=worker_id, state=ad.WORKER_UNKNOWN)

    def run_command(self, command: str, cwd=None, shell=None) -> CommandResult:
        return CommandResult(exit_code=0, stdout="OK")

    def resolve_model(self, role: str) -> str:
        return "fake-model"

    def get_tokens_spent(self) -> int:
        return self.total_tokens


class ShipFakeDriver(DispatchingFakeDriver):
    """Fake driver whose git answers let the ship phase complete for a
    configured toplevel WITHOUT running any real git."""

    def __init__(self, toplevel):
        super().__init__()
        self.toplevel = str(toplevel)
        self.commands = []

    def run_command(self, command: str, cwd=None, shell=None) -> CommandResult:
        self.commands.append(command)
        if command.strip() == "git rev-parse --show-toplevel":
            return CommandResult(exit_code=0, stdout=self.toplevel)
        if command.strip() == "git rev-parse HEAD":
            return CommandResult(exit_code=0, stdout="f" * 40)
        return CommandResult(exit_code=0, stdout="OK")


def _seed_verified_journal(state_dir, item, files_written, repo=None):
    """Write a verified journal entry matching `item` (fingerprint-bound)."""
    _write_journal_entry(
        str(state_dir),
        item["slug"],
        "dispatched",
        {
            "verified": True,
            "testExit": 0,
            "fingerprint": _item_fingerprint(item),
            "filesWritten": list(files_written),
        },
        repo=repo,
    )


def _init_git_repo(path):
    """Create a real, isolated git repo with a local identity."""
    for args in (
        ["git", "init", "-q", str(path)],
        ["git", "-C", str(path), "config", "user.email", "rs3@test.local"],
        ["git", "-C", str(path), "config", "user.name", "RS3 Test"],
    ):
        subprocess.run(args, capture_output=True, check=True)


# ========================================================================
# N1: live-seat ceiling re-check must survive None worker spend
# ========================================================================

class TestN1LiveSeatCeilingNoneSpend(unittest.TestCase):
    """Flagship config: ClaudeCodeDriver worker + swapped orchestrator seat."""

    def test_flagship_wave_completes_phase6_and_ships_without_typeerror(self):
        """ClaudeCodeDriver (get_tokens_spent -> None) + seat backend whose
        get_tokens_spent -> None: the wave must complete Phase 6 decisions,
        pass the post-decision ceiling re-check, and reach ship."""
        driver = ClaudeCodeDriver()
        self.assertIsNone(driver.get_tokens_spent())  # the contract N1 hits
        backend = NoneMeterFakeBackend([_MERGE_DECISION] * 3)
        self.assertIsNone(backend.get_tokens_spent())

        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n1-"))
        repo_dir = tmp / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)
        (repo_dir / "x.py").write_text("# verified work\n")
        repo_resolved = str(repo_dir.resolve())

        item = {
            "slug": "n1-item",
            "ownsFiles": ["x.py"],
            "prompt": "p",
            "testCmd": "exit 0",
            "workDir": str(repo_dir),
        }
        _seed_verified_journal(tmp, item, ["x.py"], repo=repo_resolved)

        result = run_wave(
            driver,
            {"items": [item]},
            state_dir=str(tmp),
            git={"expectTopLevel": str(repo_dir)},
            resume_journal=True,
            orchestrator_backend=backend,
        )

        self.assertFalse(result.get("aborted"), result.get("abort_reason"))
        built = result["built"][0]
        self.assertTrue(built["verified"])
        self.assertEqual(built.get("final_catch"), "merge")
        # Ship reached: the verified work is committed (push may fail --
        # no remote -- but the item is still recorded shipped).
        self.assertEqual(result.get("shipped"), ["n1-item"])

    def test_seat_tokens_counted_when_driver_unmetered(self):
        """A metered seat with an unmetered driver still feeds the ceiling
        re-check (best-effort: seat spend only, never a crash)."""
        calls = []

        class RecordingCeiling:
            @staticmethod
            def check(spent=None, trip=True, state_dir=None):
                calls.append(spent)
                return {"exceeded": False}

        driver = ClaudeCodeDriver()
        backend = FakeOrchestratorBackend(
            [_MERGE_DECISION] * 3, tokens_per_call=150
        )
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n1b-"))
        item = {
            "slug": "n1b-item",
            "ownsFiles": ["y.py"],
            "prompt": "p",
            "testCmd": "exit 0",
            "workDir": str(tmp),
        }
        _seed_verified_journal(tmp, item, ["y.py"])
        with mock.patch.object(wave_loop, "cost_ceiling", RecordingCeiling):
            result = run_wave(
                ClaudeCodeDriver(),
                {"items": [item]},
                state_dir=str(tmp),
                resume_journal=True,
                orchestrator_backend=backend,
            )
        self.assertFalse(result.get("aborted"))
        # Post-decision re-check saw the seat's metered spend (150), not a
        # TypeError from None + 150.
        self.assertIn(150, calls)


# ========================================================================
# N5: claim exception fails CLOSED (skip, never dispatch)
# ========================================================================

class TestN5ClaimFailClosed(unittest.TestCase):

    def test_claim_exception_skips_item_without_dispatch(self):
        """try_claim raising (e.g. SQLite lock between racing instances)
        must SKIP the item, exactly as the fail-closed comment states --
        not fall through and dispatch without a claim."""

        class BoomCoordination:
            @staticmethod
            def try_claim(*a, **k):
                raise RuntimeError("database is locked")

            @staticmethod
            def release(*a, **k):
                pass

        driver = DispatchingFakeDriver()
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n5-"))
        manifest = {
            "items": [
                {
                    "slug": "n5-item",
                    "ownsFiles": ["z.py"],
                    "prompt": "p",
                    "testCmd": "run-test",
                    "workDir": str(tmp),
                }
            ]
        }
        with mock.patch.object(wave_loop, "coordination", BoomCoordination):
            result = run_wave(driver, manifest, state_dir=str(tmp))

        built = result["built"][0]
        self.assertFalse(built["verified"])
        self.assertFalse(built["dispatched"])
        self.assertIn("fail-closed skip", built["error"] or "")
        # NEVER dispatched: not in build, and not re-dispatched via repair.
        self.assertEqual(driver.dispatch_count, 0)


# ========================================================================
# N4: stale-lease release key shape + coordination TTL expiry
# ========================================================================

class TestN4StaleLeaseRelease(unittest.TestCase):

    def test_release_stale_leases_matches_claim_key(self):
        """A dead instance's claim (made with the slug STRING) is released
        on resume: _release_stale_leases must use the same key shape."""
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n4-"))
        _write_journal_entry(
            str(tmp),
            "s9",
            "dispatched",
            {"verified": False, "instance_id": "wave-dead-instance"},
            repo=None,
        )
        db = tmp / "state.db"
        es = sstore.EventStore(str(db))
        self.assertTrue(
            coordination.try_claim(
                es, resource="s9", instance_id="wave-dead-instance", ttl=99999
            )
        )

        journal_state = _load_journal_state(str(tmp))
        _release_stale_leases(str(tmp), journal_state)

        es2 = sstore.EventStore(str(db))
        self.assertTrue(
            coordination.try_claim(
                es2, resource="s9", instance_id="wave-new-instance", ttl=60
            ),
            "resume could not claim the resource: stale lease NOT released "
            "(release key did not match the claim key)",
        )

    def test_expired_claim_is_reclaimable(self):
        """A claim past its TTL is ignored: a crashed holder's claim does
        not persist forever."""
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n4b-"))
        es = sstore.EventStore(str(tmp / "state.db"))
        self.assertTrue(
            coordination.try_claim(
                es, resource="r1", instance_id="inst-a", ttl=0.05
            )
        )
        time.sleep(0.2)
        self.assertTrue(
            coordination.try_claim(
                es, resource="r1", instance_id="inst-b", ttl=60
            ),
            "claim past its TTL was still enforced",
        )

    def test_fold_claims_ttl_expiry_with_now(self):
        """fold_claims honors ts + ttl against the reference time."""
        events = [
            {
                "type": "claim_requested",
                "payload": {"resource": "r", "instance_id": "a", "ttl": 10},
                "ts": 1000.0,
                "version": 1,
            }
        ]
        self.assertEqual(
            coordination.fold_claims(events, now=1005.0), {"r": "a"}
        )
        self.assertEqual(coordination.fold_claims(events, now=1011.0), {})

    def test_fold_claims_legacy_events_never_expire(self):
        """Events without ts/ttl (legacy) keep their holder."""
        events = [
            {
                "type": "claim_requested",
                "payload": {"resource": "r", "instance_id": "a"},
                "version": 1,
            }
        ]
        self.assertEqual(
            coordination.fold_claims(events, now=time.time() + 1e9),
            {"r": "a"},
        )


# ========================================================================
# N3: recovery livelock -- resumed verified items ship + terminal tracker
# ========================================================================

class TestN3RecoveryShip(unittest.TestCase):

    def _repo_fixture(self, prefix):
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix=prefix))
        repo_dir = tmp / "repo"
        repo_dir.mkdir()
        return tmp, repo_dir, str(repo_dir.resolve())

    def test_resumed_verified_item_restores_files_and_ships(self):
        """Journal-resumed verified item: filesWritten restored from the
        journal, Phase 7 ships it (previously filesWritten:[] -> no shipped
        record -> tracker stayed todo forever)."""
        tmp, repo_dir, repo_resolved = self._repo_fixture("n3a-")
        driver = ShipFakeDriver(repo_resolved)
        item = {
            "slug": "n3-item",
            "ownsFiles": ["x.py"],
            "prompt": "p",
            "testCmd": "run-test",
            "workDir": str(repo_dir),
        }
        _seed_verified_journal(tmp, item, ["x.py"], repo=repo_resolved)

        result = run_wave(
            driver,
            {"items": [item]},
            state_dir=str(tmp),
            git={"expectTopLevel": str(repo_dir)},
            resume_journal=True,
        )

        built = result["built"][0]
        self.assertTrue(built["verified"])
        self.assertTrue(built.get("skipped_from_journal"))
        self.assertEqual(built["filesWritten"], ["x.py"])
        self.assertEqual(result.get("shipped"), ["n3-item"])
        self.assertEqual(driver.dispatch_count, 0)

    def test_verified_item_with_no_files_gets_terminal_ship_record(self):
        """A verified item with legitimately nothing to add still emits a
        shipped record (honest no-op ship) so the scheduler can mark the
        tracker terminal -- never re-selectable forever."""
        tmp, repo_dir, repo_resolved = self._repo_fixture("n3b-")
        driver = ShipFakeDriver(repo_resolved)
        item = {
            "slug": "n3-nofiles",
            "ownsFiles": ["x.py"],
            "prompt": "p",
            "testCmd": "run-test",
            "workDir": str(repo_dir),
        }
        _seed_verified_journal(tmp, item, [], repo=repo_resolved)

        result = run_wave(
            driver,
            {"items": [item]},
            state_dir=str(tmp),
            git={"expectTopLevel": str(repo_dir)},
            resume_journal=True,
        )

        self.assertEqual(result.get("shipped"), ["n3-nofiles"])
        repo_records = result.get("shipped_repos") or []
        self.assertEqual(len(repo_records), 1)
        self.assertTrue(repo_records[0].get("no_changes"))
        self.assertTrue(result["built"][0].get("ship_no_changes"))

    def test_nothing_to_commit_is_terminal_not_a_failure_loop(self):
        """Commit failing with 'nothing to commit' (work already in HEAD
        from a crashed prior run) is a terminal no-op ship."""
        tmp, repo_dir, repo_resolved = self._repo_fixture("n3c-")

        class AlreadyCommittedDriver(ShipFakeDriver):
            def run_command(self, command, cwd=None, shell=None):
                if command.startswith("git commit"):
                    self.commands.append(command)
                    return CommandResult(
                        exit_code=1,
                        stdout="nothing to commit, working tree clean",
                    )
                return super().run_command(command, cwd=cwd, shell=shell)

        driver = AlreadyCommittedDriver(repo_resolved)
        item = {
            "slug": "n3-precommitted",
            "ownsFiles": ["x.py"],
            "prompt": "p",
            "testCmd": "run-test",
            "workDir": str(repo_dir),
        }
        _seed_verified_journal(tmp, item, ["x.py"], repo=repo_resolved)

        result = run_wave(
            driver,
            {"items": [item]},
            state_dir=str(tmp),
            git={"expectTopLevel": str(repo_dir)},
            resume_journal=True,
        )

        self.assertEqual(result.get("shipped"), ["n3-precommitted"])
        self.assertTrue((result.get("shipped_repos") or [{}])[0].get("no_changes"))

    def test_scheduler_marks_tracker_terminal_and_never_reselects(self):
        """End-to-end N3: a journal-resumed verified item marks the tracker
        (in_progress = terminal for re-selection) on wave 1 and is NOT
        selected on wave 2. The worker is never dispatched."""
        fixture = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n3d-"))
        state_dir = fixture / "state"
        state_dir.mkdir()
        tracker_path = fixture / "tracker.json"
        tracker_item = {
            "id": "1",
            "slug": "rs3-resume",
            "status": "todo",
            "priority": "P1",
            "ownsFiles": ["a.py"],
            "prompt": "Fix",
            "testCmd": "test",
        }
        tracker_path.write_text(json.dumps([tracker_item]))

        # The scheduler ships against its module REPO root.
        sched_repo = str(Path(ws.REPO).resolve())
        driver = ShipFakeDriver(sched_repo)
        _seed_verified_journal(
            state_dir,
            {
                "slug": "rs3-resume",
                "prompt": "Fix",
                "ownsFiles": ["a.py"],
                "testCmd": "test",
            },
            ["a.py"],
            repo=sched_repo,
        )

        report1 = ws.run_wave_scheduler(
            tracker_path=str(tracker_path),
            max_items=5,
            dry_run=False,
            driver=driver,
            state_dir=state_dir,
        )
        self.assertEqual(report1["phase"], "dispatch", report1)
        shipped_slugs = [i["slug"] for i in report1.get("items_shipped") or []]
        self.assertIn("rs3-resume", shipped_slugs)
        self.assertEqual(driver.dispatch_count, 0)

        tracker_after = json.loads(tracker_path.read_text())
        self.assertEqual(tracker_after[0]["status"], "in_progress")

        report2 = ws.run_wave_scheduler(
            tracker_path=str(tracker_path),
            max_items=5,
            dry_run=False,
            driver=driver,
            state_dir=state_dir,
        )
        self.assertEqual(report2["phase"], "intake")
        self.assertEqual(report2["items_selected"], [])
        self.assertEqual(driver.dispatch_count, 0)


# ========================================================================
# N6: Windows arg quoting must not double every backslash
# ========================================================================

class TestN6QuoteArgWindows(unittest.TestCase):

    @unittest.skipUnless(os.name == "nt", "Windows-only quoting semantics")
    def test_plain_backslash_path_not_doubled(self):
        """`src\\util.py` has no quote to escape: backslash stays single."""
        q = _quote_arg("src" + "\\" + "util.py")
        self.assertEqual(q, '"src\\util.py"')
        self.assertNotIn("\\\\", q)

    @unittest.skipUnless(os.name == "nt", "Windows-only quoting semantics")
    def test_backslash_before_quote_still_escaped(self):
        """Backslashes preceding an embedded quote ARE doubled (MSVCRT
        rules) -- the receiver must round-trip the original string."""
        payload = "a\\" + '"' + "b"
        received = self._roundtrip_argv(payload)
        self.assertEqual(received, payload)

    @unittest.skipUnless(os.name == "nt", "Windows-only quoting semantics")
    def test_argv_roundtrip_backslash_path(self):
        received = self._roundtrip_argv("src\\util.py")
        self.assertEqual(received, "src\\util.py")

    @unittest.skipUnless(os.name == "nt", "Windows-only git behavior")
    def test_git_add_stages_backslash_path(self):
        """Execution proof: git add with a quoted backslash pathspec stages
        the file (the doubled form failed with 'pathspec did not match')."""
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n6-"))
        _init_git_repo(tmp)
        sub = tmp / "sub"
        sub.mkdir()
        (sub / "util.py").write_text("# n6\n")
        driver = ClaudeCodeDriver()
        add = driver.run_command(
            "git add " + _quote_arg("sub\\util.py"), cwd=str(tmp)
        )
        self.assertEqual(add.exit_code, 0, add.stderr)
        staged = driver.run_command(
            "git diff --cached --name-only", cwd=str(tmp)
        )
        self.assertIn("sub/util.py", staged.stdout)

    def test_argv_roundtrip_space_path_all_platforms(self):
        received = self._roundtrip_argv("file with space.py")
        self.assertEqual(received, "file with space.py")

    def _roundtrip_argv(self, s):
        """Pass _quote_arg(s) through a real shell to a real argv receiver."""
        script = Path(_MODULE_TMP) / "echo_arg.py"
        script.write_text("import sys\nsys.stdout.write(sys.argv[1])\n")
        cmd = '"%s" "%s" %s' % (sys.executable, script, _quote_arg(s))
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout


# ========================================================================
# N7: no silent vanish, no vacuous green, no duplicate slugs
# ========================================================================

class TestN7HonestAccounting(unittest.TestCase):

    def test_future_exception_recorded_as_failed_item(self):
        """If an item's future raises, the item is recorded FAILED -- it
        must never vanish from both built and failed_items."""
        driver = DispatchingFakeDriver()
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n7a-"))
        item = {
            "slug": "n7-boom",
            "ownsFiles": ["b.py"],
            "prompt": "p",
            "testCmd": "run-test",
            "workDir": str(tmp),
        }
        _seed_verified_journal(tmp, item, ["b.py"])
        # repair_cap 0: isolate the Phase-4 recording (a nonzero cap would
        # let the repair round green the recorded failure, hiding it).
        no_repair_policy = {
            "repair_cap": 0,
            "spot_check_frac": 0.0,
            "require_adversarial_review": False,
        }
        with mock.patch.object(
            wave_loop,
            "_should_skip_from_journal",
            side_effect=RuntimeError("boom in build thread"),
        ), mock.patch.object(
            wave_loop, "verification_policy", return_value=no_repair_policy
        ):
            result = run_wave(
                driver,
                {"items": [item]},
                state_dir=str(tmp),
                resume_journal=True,
            )
        self.assertEqual(len(result["built"]), 1)
        built = result["built"][0]
        self.assertEqual(built["slug"], "n7-boom")
        self.assertFalse(built["verified"])
        self.assertIn("executor exception", built["error"] or "")
        # And the Report is NOT green.
        self.assertFalse(result_to_report(result)["integration"]["green"])

    def test_green_false_when_zero_items_ran(self):
        report = result_to_report(
            {"built": [], "aborted": False, "preflight_ok": True}
        )
        self.assertFalse(report["integration"]["green"])

    def test_duplicate_slugs_rejected_loudly(self):
        driver = DispatchingFakeDriver()
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n7b-"))
        manifest = {
            "items": [
                {
                    "slug": "dup",
                    "ownsFiles": ["a.py"],
                    "prompt": "p1",
                    "testCmd": "t",
                    "workDir": str(tmp),
                },
                {
                    "slug": "dup",
                    "ownsFiles": ["b.py"],
                    "prompt": "p2",
                    "testCmd": "t",
                    "workDir": str(tmp),
                },
            ]
        }
        result = run_wave(driver, manifest)
        self.assertTrue(result["aborted"])
        self.assertEqual(result["abort_reason"], "duplicate_slugs")
        self.assertEqual(result["duplicate_slugs"], ["dup"])
        self.assertEqual(driver.dispatch_count, 0)


# ========================================================================
# N10: journal identity binding + atomic writes
# ========================================================================

class TestN10JournalScoping(unittest.TestCase):

    def test_new_item_reusing_prior_slug_is_rebuilt(self):
        """A NEW tracker item that reuses a prior wave's slug (different
        content) must NOT inherit the stale verified state."""
        driver = DispatchingFakeDriver()
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n10a-"))
        old_item = {
            "slug": "reused",
            "ownsFiles": ["old.py"],
            "prompt": "OLD work",
            "testCmd": "t",
        }
        _seed_verified_journal(tmp, old_item, ["old.py"])

        new_item = {
            "slug": "reused",
            "ownsFiles": ["new.py"],
            "prompt": "COMPLETELY DIFFERENT new work",
            "testCmd": "t",
            "workDir": str(tmp),
        }
        result = run_wave(
            driver, {"items": [new_item]}, state_dir=str(tmp), resume_journal=True
        )
        built = result["built"][0]
        self.assertFalse(built.get("skipped_from_journal", False))
        self.assertTrue(built["dispatched"])
        self.assertEqual(driver.dispatch_count, 1)

    def test_legacy_entry_without_fingerprint_is_rebuilt(self):
        """Fail-closed: an old-format entry (no fingerprint) cannot prove it
        matches the current item -> rebuild, never skip."""
        driver = DispatchingFakeDriver()
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n10b-"))
        _write_journal_entry(
            str(tmp), "legacy", "dispatched", {"verified": True, "testExit": 0}
        )
        item = {
            "slug": "legacy",
            "ownsFiles": ["l.py"],
            "prompt": "p",
            "testCmd": "t",
            "workDir": str(tmp),
        }
        result = run_wave(
            driver, {"items": [item]}, state_dir=str(tmp), resume_journal=True
        )
        self.assertFalse(result["built"][0].get("skipped_from_journal", False))
        self.assertEqual(driver.dispatch_count, 1)

    def test_matching_fingerprint_still_resumes(self):
        """The SAME item resumes as before (trust-but-verify skip)."""
        driver = DispatchingFakeDriver()
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n10c-"))
        item = {
            "slug": "same",
            "ownsFiles": ["s.py"],
            "prompt": "p",
            "testCmd": "t",
            "workDir": str(tmp),
        }
        _seed_verified_journal(tmp, item, ["s.py"])
        result = run_wave(
            driver, {"items": [item]}, state_dir=str(tmp), resume_journal=True
        )
        self.assertTrue(result["built"][0].get("skipped_from_journal"))
        self.assertEqual(driver.dispatch_count, 0)

    def test_torn_journal_entry_is_skipped_and_item_rebuilt(self):
        """A torn (half-written) journal file must not corrupt resume: the
        malformed entry is ignored and the item rebuilds normally."""
        driver = DispatchingFakeDriver()
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n10d-"))
        jdir = tmp / "journal"
        jdir.mkdir(parents=True)
        key = _journal_key_for_item({"slug": "torn"})
        (jdir / (key + ".json")).write_text('{"slug": "torn", "verifi')
        item = {
            "slug": "torn",
            "ownsFiles": ["t.py"],
            "prompt": "p",
            "testCmd": "t",
            "workDir": str(tmp),
        }
        result = run_wave(
            driver, {"items": [item]}, state_dir=str(tmp), resume_journal=True
        )
        self.assertTrue(result["built"][0]["verified"])
        self.assertEqual(driver.dispatch_count, 1)

    def test_journal_write_is_atomic_via_replace(self):
        """The entry lands via os.replace: if replace fails, the previous
        entry is untouched and no temp residue remains (a direct write_text
        would have torn/overwritten the file)."""
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="n10e-"))
        _write_journal_entry(
            str(tmp), "atomic", "dispatched", {"verified": True, "testExit": 0}
        )
        key = _journal_key_for_item({"slug": "atomic"})
        journal_file = tmp / "journal" / (key + ".json")
        before = json.loads(journal_file.read_text())
        self.assertTrue(before["verified"])

        with mock.patch.object(
            wave_loop.os, "replace", side_effect=OSError("disk full")
        ):
            _write_journal_entry(
                str(tmp),
                "atomic",
                "failed",
                {"verified": False, "testExit": 1},
            )

        after = json.loads(journal_file.read_text())
        self.assertEqual(after, before)  # prior entry intact
        self.assertEqual(
            list((tmp / "journal").glob("*.tmp")), []  # no residue
        )


# ========================================================================
# RS5: claim-gate lifecycle -- ttl sized to the work (F1a), fencing before
# repair/ship (F1b), claim held across build -> repair -> ship with
# exactly-once release at the true end (F3).
# ========================================================================

class BlockingRepairDriver(DispatchingFakeDriver):
    """Test always fails -> repair rounds run; the repair dispatch BLOCKS
    until the test releases it (parks the wave inside Phase 5)."""

    def __init__(self):
        super().__init__()
        self.in_repair = threading.Event()
        self.resume = threading.Event()

    def dispatch_worker(self, request):
        if self.dispatch_count >= 1:  # second dispatch = repair round
            self.in_repair.set()
            self.resume.wait(timeout=30)
        return super().dispatch_worker(request)

    def run_command(self, command, cwd=None, shell=None):
        return CommandResult(exit_code=1, stdout="test failed")


class TestRS5ClaimLifecycle(unittest.TestCase):

    def _manifest(self, workdir, slug):
        return {
            "items": [
                {
                    "slug": slug,
                    "ownsFiles": ["a.py"],
                    "prompt": "p",
                    "testCmd": "run-test",
                    "workDir": str(workdir),
                }
            ]
        }

    # ---------------------------------------------------------------- F1a
    def test_claim_ttl_sized_to_driver_command_timeout(self):
        """The ttl passed to try_claim derives from the driver's command
        timeout (generous multiple, sane floor) -- never the 300s default
        that a single real build outlives."""
        captured = []
        original = coordination.try_claim

        def capturing(store, resource=None, instance_id=None, ttl=300.0):
            captured.append(ttl)
            return original(
                store, resource=resource, instance_id=instance_id, ttl=ttl
            )

        driver = DispatchingFakeDriver()
        driver.command_timeout_s = 900.0
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="rs5a-"))
        with mock.patch.object(coordination, "try_claim", capturing):
            run_wave(driver, self._manifest(tmp, "rs5-ttl"), state_dir=str(tmp))

        self.assertEqual(len(captured), 1)
        self.assertGreaterEqual(
            captured[0], 900.0 * wave_loop._CLAIM_TTL_TIMEOUT_MULTIPLE,
            "claim ttl not sized to the driver's command timeout",
        )

    def test_claim_ttl_floor_and_scaling(self):
        """No timeout knob -> floor; a big timeout scales past the floor."""
        self.assertGreaterEqual(
            wave_loop._claim_ttl_for_driver(object()),
            wave_loop._CLAIM_TTL_FLOOR_S,
        )

        class BigTimeout:
            command_timeout_s = 3600.0

        self.assertGreaterEqual(
            wave_loop._claim_ttl_for_driver(BigTimeout()),
            3600.0 * wave_loop._CLAIM_TTL_TIMEOUT_MULTIPLE,
        )

        class BogusTimeout:
            command_timeout_s = "not-a-number"

        self.assertEqual(
            wave_loop._claim_ttl_for_driver(BogusTimeout()),
            wave_loop._CLAIM_TTL_FLOOR_S,
        )

    # ---------------------------------------------------------------- F3
    def test_claim_held_across_repair_released_exactly_once(self):
        """While instance A is mid-REPAIR for a slug, a second instance
        cannot claim it (the old code released in build_item's finally,
        leaving Phase 5 claim-less). At wave end the claim is released
        exactly once and the slug is claimable again."""
        driver = BlockingRepairDriver()
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="rs5b-"))
        manifest = self._manifest(tmp, "rs5-held")
        box = {}

        t = threading.Thread(
            target=lambda: box.update(
                r=run_wave(driver, manifest, state_dir=str(tmp))
            )
        )
        t.start()
        try:
            self.assertTrue(
                driver.in_repair.wait(timeout=30), "wave never reached repair"
            )
            es = sstore.EventStore(str(tmp / "state.db"))
            self.assertFalse(
                coordination.try_claim(
                    es, resource="rs5-held", instance_id="inst-B", ttl=60
                ),
                "second instance claimed a slug whose holder is mid-repair "
                "(concurrent double-dispatch on the same files)",
            )
        finally:
            driver.resume.set()
            t.join(timeout=60)
        self.assertFalse(t.is_alive(), "wave thread did not finish")

        es = sstore.EventStore(str(tmp / "state.db"))
        wave_releases = [
            e
            for e in es.read("claims")
            if e.get("type") == "claim_released"
            and (e.get("payload") or {}).get("resource") == "rs5-held"
            and str(
                (e.get("payload") or {}).get("instance_id", "")
            ).startswith("wave-")
        ]
        self.assertEqual(
            len(wave_releases), 1,
            "claim must be released EXACTLY once at lifecycle end",
        )
        self.assertTrue(
            coordination.try_claim(
                es, resource="rs5-held", instance_id="inst-B", ttl=60
            ),
            "slug not claimable after the wave ended",
        )

    def test_claim_held_through_ship_phase(self):
        """While instance A is inside Phase 7 (ship), a second instance
        cannot claim the slug."""
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="rs5c-"))
        repo_dir = tmp / "repo"
        repo_dir.mkdir()
        repo_resolved = str(repo_dir.resolve())

        class ShipPausingDriver(ShipFakeDriver):
            def __init__(self, toplevel):
                super().__init__(toplevel)
                self.in_ship = threading.Event()
                self.resume = threading.Event()

            def run_command(self, command, cwd=None, shell=None):
                if command.strip() == "git rev-parse --show-toplevel":
                    self.in_ship.set()
                    self.resume.wait(timeout=30)
                return super().run_command(command, cwd=cwd, shell=shell)

        driver = ShipPausingDriver(repo_resolved)
        manifest = self._manifest(repo_dir, "rs5-ship")
        box = {}
        t = threading.Thread(
            target=lambda: box.update(
                r=run_wave(
                    driver,
                    manifest,
                    state_dir=str(tmp),
                    git={"expectTopLevel": str(repo_dir)},
                )
            )
        )
        t.start()
        try:
            self.assertTrue(
                driver.in_ship.wait(timeout=30), "wave never reached ship"
            )
            es = sstore.EventStore(str(tmp / "state.db"))
            self.assertFalse(
                coordination.try_claim(
                    es, resource="rs5-ship", instance_id="inst-B", ttl=60
                ),
                "second instance claimed a slug whose holder is mid-ship",
            )
        finally:
            driver.resume.set()
            t.join(timeout=60)
        self.assertFalse(t.is_alive())
        self.assertEqual(box["r"].get("shipped"), ["rs5-ship"])

    # ---------------------------------------------------------------- F1b
    def test_fence_blocks_ship_when_claim_lost(self):
        """If the claim lapsed mid-build and another instance reclaimed the
        slug, Phase 7 must NOT ship it (double-ship): honest abort record."""
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="rs5d-"))
        repo_dir = tmp / "repo"
        repo_dir.mkdir()
        repo_resolved = str(repo_dir.resolve())
        db_path = str(tmp / "state.db")

        class ClaimStealingDriver(ShipFakeDriver):
            """During the post-build test run (ttl already lapsed), a second
            instance reclaims the slug -- the exact lost-claim window."""

            def __init__(self, toplevel):
                super().__init__(toplevel)
                self.stole = False

            def run_command(self, command, cwd=None, shell=None):
                if command == "run-test" and not self.stole:
                    self.stole = True
                    time.sleep(0.1)  # let the tiny test ttl lapse
                    es = sstore.EventStore(db_path)
                    assert coordination.try_claim(
                        es, resource="rs5-lost", instance_id="inst-B", ttl=600
                    ), "test setup: reclaim after ttl lapse must win"
                return super().run_command(command, cwd=cwd, shell=shell)

        driver = ClaimStealingDriver(repo_resolved)
        with mock.patch.object(
            wave_loop, "_claim_ttl_for_driver", return_value=0.01
        ):
            result = run_wave(
                driver,
                self._manifest(repo_dir, "rs5-lost"),
                state_dir=str(tmp),
                git={"expectTopLevel": str(repo_dir)},
            )

        built = result["built"][0]
        self.assertTrue(built["verified"])  # the test did pass
        self.assertTrue(built.get("claim_lost"), "lost claim not recorded")
        self.assertIn("claim lost", built.get("ship_error") or "")
        self.assertIsNone(result.get("shipped"))
        self.assertFalse(
            any(c.startswith("git add") for c in driver.commands),
            "fenced item still reached git add (double-ship path)",
        )

    def test_fence_blocks_repair_redispatch_when_claim_lost(self):
        """A failed item whose claim was reclaimed must NOT be repair-
        re-dispatched (the reclaimer may be dispatching it right now)."""
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="rs5e-"))
        db_path = str(tmp / "state.db")

        class StealAndFailDriver(DispatchingFakeDriver):
            def __init__(self):
                super().__init__()
                self.stole = False

            def run_command(self, command, cwd=None, shell=None):
                if not self.stole:
                    self.stole = True
                    time.sleep(0.1)
                    es = sstore.EventStore(db_path)
                    coordination.try_claim(
                        es, resource="rs5-rlost", instance_id="inst-B", ttl=600
                    )
                return CommandResult(exit_code=1, stdout="test failed")

        driver = StealAndFailDriver()
        with mock.patch.object(
            wave_loop, "_claim_ttl_for_driver", return_value=0.01
        ):
            result = run_wave(
                driver, self._manifest(tmp, "rs5-rlost"), state_dir=str(tmp)
            )

        built = result["built"][0]
        self.assertFalse(built["verified"])
        self.assertTrue(built.get("claim_lost"))
        self.assertIn("claim lost", built.get("error") or "")
        self.assertEqual(built.get("repairs", 0), 0)
        self.assertEqual(
            driver.dispatch_count, 1,
            "repair re-dispatched an item whose claim was lost",
        )

    # ---------------------------------------------------------------- misc
    def test_release_exactly_once_normal_path(self):
        """A normal verified wave: one claim_requested + one claim_released
        by the wave instance; nothing held afterwards."""
        driver = DispatchingFakeDriver()
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="rs5f-"))
        result = run_wave(
            driver, self._manifest(tmp, "rs5-once"), state_dir=str(tmp)
        )
        self.assertTrue(result["built"][0]["verified"])

        es = sstore.EventStore(str(tmp / "state.db"))
        events = es.read("claims")
        reqs = [e for e in events if e.get("type") == "claim_requested"]
        rels = [e for e in events if e.get("type") == "claim_released"]
        self.assertEqual(len(reqs), 1)
        self.assertEqual(len(rels), 1)
        self.assertIsNone(coordination.current_holder(es, "rs5-once"))

    def test_no_state_dir_means_no_claims(self):
        """Single-instance no-op invariant: without a state_dir the claim
        machinery is never touched (a raising coordination module proves it)."""

        class BoomCoordination:
            @staticmethod
            def try_claim(*args, **kwargs):
                raise AssertionError("claims must not run without state_dir")

            @staticmethod
            def current_holder(*args, **kwargs):
                raise AssertionError("claims must not run without state_dir")

            @staticmethod
            def release(*args, **kwargs):
                raise AssertionError("claims must not run without state_dir")

        driver = DispatchingFakeDriver()
        tmp = Path(tempfile.mkdtemp(dir=_MODULE_TMP, prefix="rs5g-"))
        with mock.patch.object(wave_loop, "coordination", BoomCoordination):
            result = run_wave(driver, self._manifest(tmp, "rs5-noop"))
        self.assertTrue(result["built"][0]["verified"])
        self.assertEqual(driver.dispatch_count, 1)


if __name__ == "__main__":
    unittest.main()
