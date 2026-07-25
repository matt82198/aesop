#!/usr/bin/env python3
"""HS-2: live orchestrator-seat swap + end-to-end swap-transparency proof.

Proves, all offline (no API key, no network):
  1. NO-OP INVARIANT: with no configured orchestrator seat (or the null
     harness seat), run_wave behaves byte-identically to pre-HS-2: the
     adversarial-review phase stays "deferred", no orchestrator_review key
     appears, no per-item final_catch field appears, no OPENAI key is needed.
  2. LIVE SEAT: with a configured (non-null) orchestrator backend, the wave
     engine routes a real final_catch decision per verified item through
     OrchestratorDriver.decide() -> the configured backend, and the verdict
     has effect: "block" stops the item from shipping; "merge" approves;
     escalate/undetermined/DECISION_FAILED degrade to today's behavior
     (ship to branch, honest record, manual merge downstream).
  3. SWAP TRANSPARENCY (end-to-end): driving the SAME task through the
     public scheduler path with (a) the default harness orchestrator seat
     and (b) a configured non-Claude orchestrator (FakeOrchestratorBackend)
     yields an INVARIANT human interface (Report JSON shape) and state
     layer (tracker + journal structure). The swap changes who decides,
     never the contract.
  4. SCHEDULER WIRING: resolve_orchestrator_backend() maps config ->
     backend (None for absent/harness seats; live backend for
     openai-compatible; key gate only with --execute on hosted seats),
     and run_wave_scheduler passes the backend through to run_wave.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Add driver/ and tools/ to path for imports
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
TOOLS_DIR = REPO / "tools"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

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
from orchestrator_backend import (  # noqa: E402
    FakeOrchestratorBackend,
    HarnessOrchestratorBackend,
    OpenAICompatibleOrchestratorBackend,
)
from wave_loop import run_wave  # noqa: E402
import wave_scheduler as ws  # noqa: E402

# Env var name assembled at runtime (secret-scan hygiene).
_KEY_ENV = "OPENAI" + "_" + "API" + "_" + "KEY"

# A schema-valid approve decision for the final_catch decision type
# (required: verdict, evidence, confidence).
_MERGE_DECISION = {
    "verdict": "merge",
    "evidence": ["test exit code 0; no red flags in verification results"],
    "confidence": 0.95,
}
_BLOCK_DECISION = {
    "verdict": "block",
    "evidence": ["spot check flagged; refusing to ship"],
    "confidence": 0.9,
}
_ESCALATE_DECISION = {
    "verdict": "escalate",
    "evidence": ["ambiguous verification state; needs human review"],
    "confidence": 0.4,
}


class ShipCapableFakeDriver(AgentDriver):
    """Offline fake worker driver whose git answers let the ship phase
    complete against the real repo root WITHOUT running any real git."""

    def __init__(self, test_exit=0):
        self.test_exit = test_exit
        self.total_tokens = 0
        self.dispatch_count = 0
        self._workers = {}

    def probe_capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            name="fake-nonclaude-worker",
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
            notes="Offline ship-capable fake driver (HS-2 swap proof)",
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
                fpath.write_text("# written by fake dispatch\n")
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
        if command.strip() == "git rev-parse --show-toplevel":
            # Answer with the real repo root so the per-repo ship guard passes
            # (no real git is ever executed by this fake).
            return CommandResult(
                exit_code=0, stdout=str(Path(REPO).resolve())
            )
        if command.strip() == "git rev-parse HEAD":
            return CommandResult(exit_code=0, stdout="f" * 40)
        if command.startswith("git"):
            return CommandResult(exit_code=0, stdout="OK")
        # The item's test command.
        return CommandResult(exit_code=self.test_exit, stdout="TEST")

    def resolve_model(self, role: str) -> str:
        return "fake-model"

    def get_tokens_spent(self) -> int:
        return self.total_tokens


def _one_item_manifest(workdir, slug="swap-item"):
    return {
        "wave_id": "hs2-test",
        "items": [
            {
                "slug": slug,
                "ownsFiles": ["mod_hs2.py"],
                "prompt": "Write mod_hs2.py implementing add(a, b).",
                "testCmd": "run-item-test",
                "workDir": str(workdir),
            }
        ],
    }


def _write_tracker(dirpath, slug="swap-item"):
    tracker = Path(dirpath) / "tracker.json"
    tracker.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "hs2-1",
                        "slug": slug,
                        "status": "todo",
                        "priority": "P1",
                        "ownsFiles": ["mod_hs2.py"],
                        "prompt": "Write mod_hs2.py implementing add(a, b).",
                        "testCmd": "run-item-test",
                        "workDir": str(dirpath),
                        "createdAt": "2026-01-01",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return str(tracker)


def _env_without_key():
    """Context manager: os.environ with the OpenAI key env removed."""
    patcher = mock.patch.dict(os.environ)
    env = patcher.start()
    os.environ.pop(_KEY_ENV, None)
    return patcher


# ========================================================================
# 1. NO-OP INVARIANT (hard requirement, mirrors HS-1)
# ========================================================================

class TestNoOpDefaultInvariant(unittest.TestCase):
    """No seats.orchestrator -> byte-identical to pre-HS-2 behavior."""

    def test_run_wave_default_no_orchestrator_artifacts(self):
        """Default run_wave: review deferred, no orchestrator keys anywhere."""
        with tempfile.TemporaryDirectory() as td:
            result = run_wave(
                ShipCapableFakeDriver(),
                _one_item_manifest(td),
                state_dir=None,
                git=None,
            )
        self.assertEqual(result.get("adversarial_review"), "deferred")
        self.assertNotIn("orchestrator_review", result)
        for item in result["built"]:
            self.assertEqual(item.get("adversarial_review"), "deferred")
            self.assertNotIn("final_catch", item)
            self.assertTrue(item["verified"])

    def test_run_wave_null_harness_backend_identical_to_default(self):
        """Explicitly passing the null HarnessOrchestratorBackend is the
        no-op default: never called (its decide_call raises), review stays
        deferred, no orchestrator keys."""
        with tempfile.TemporaryDirectory() as td:
            result = run_wave(
                ShipCapableFakeDriver(),
                _one_item_manifest(td),
                state_dir=None,
                git=None,
                orchestrator_backend=HarnessOrchestratorBackend(),
            )
        self.assertEqual(result.get("adversarial_review"), "deferred")
        self.assertNotIn("orchestrator_review", result)
        for item in result["built"]:
            self.assertEqual(item.get("adversarial_review"), "deferred")
            self.assertNotIn("final_catch", item)
            self.assertTrue(item["verified"])

    def test_default_scheduler_run_needs_no_openai_key(self):
        """Full scheduler execute path with no config: completes with the
        key env var absent (no OpenAI backend constructed anywhere)."""
        patcher = _env_without_key()
        try:
            with tempfile.TemporaryDirectory() as td:
                tracker = _write_tracker(td)
                report = ws.run_wave_scheduler(
                    tracker_path=tracker,
                    max_items=1,
                    dry_run=False,
                    driver=ShipCapableFakeDriver(),
                    state_dir=Path(td) / "state",
                )
        finally:
            patcher.stop()
        self.assertEqual(report["phase"], "dispatch")
        self.assertTrue(report["success"], msg=json.dumps(report, default=str))

    def test_report_shape_unchanged_on_default_path(self):
        """Guard: HS-2 adds NO new keys to the default-path Report JSON."""
        with tempfile.TemporaryDirectory() as td:
            tracker = _write_tracker(td)
            report = ws.run_wave_scheduler(
                tracker_path=tracker,
                max_items=1,
                dry_run=False,
                driver=ShipCapableFakeDriver(),
                state_dir=Path(td) / "state",
            )
        self.assertEqual(
            set(report.keys()),
            {
                "phase",
                "wave_id",
                "items_selected",
                "items_shipped",
                "merged",
                "timestamp",
                "success",
                "sha",
                "tracker_update_attempted",
            },
            msg=json.dumps(report, default=str),
        )


class TestResolveOrchestratorBackend(unittest.TestCase):
    """resolve_orchestrator_backend(): config -> seat backend mapping."""

    def _write_config(self, td, payload):
        p = Path(td) / "aesop.config.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return str(p)

    def test_missing_config_resolves_to_none(self):
        with tempfile.TemporaryDirectory() as td:
            backend, err = ws.resolve_orchestrator_backend(
                config_path=str(Path(td) / "absent.json")
            )
        self.assertIsNone(backend)
        self.assertIsNone(err)

    def test_legacy_flat_block_resolves_to_none(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write_config(
                td, {"backend": "codex", "model": "gpt-4o-mini"}
            )
            backend, err = ws.resolve_orchestrator_backend(config_path=path)
        self.assertIsNone(backend)
        self.assertIsNone(err)

    def test_harness_seat_resolves_to_none(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write_config(
                td, {"seats": {"orchestrator": {"backend": "harness"}}}
            )
            backend, err = ws.resolve_orchestrator_backend(config_path=path)
        self.assertIsNone(backend)
        self.assertIsNone(err)

    def test_openai_seat_resolves_to_live_backend_offline(self):
        """Configured seat builds WITHOUT a key (offline-safe construction)."""
        patcher = _env_without_key()
        try:
            with tempfile.TemporaryDirectory() as td:
                path = self._write_config(
                    td,
                    {
                        "seats": {
                            "orchestrator": {
                                "backend": "openai-compatible",
                                "model": "gpt-4o-mini",
                            }
                        }
                    },
                )
                backend, err = ws.resolve_orchestrator_backend(
                    config_path=path, execute=False
                )
        finally:
            patcher.stop()
        self.assertIsNone(err)
        self.assertIsInstance(backend, OpenAICompatibleOrchestratorBackend)
        self.assertEqual(backend.model, "gpt-4o-mini")

    def test_hosted_seat_execute_requires_key(self):
        patcher = _env_without_key()
        try:
            with tempfile.TemporaryDirectory() as td:
                path = self._write_config(
                    td,
                    {
                        "seats": {
                            "orchestrator": {
                                "backend": "openai-compatible",
                                "model": "gpt-4o-mini",
                            }
                        }
                    },
                )
                backend, err = ws.resolve_orchestrator_backend(
                    config_path=path, execute=True
                )
        finally:
            patcher.stop()
        self.assertIsNone(backend)
        self.assertIn(_KEY_ENV, err or "")

    def test_local_seat_execute_needs_no_key(self):
        patcher = _env_without_key()
        try:
            with tempfile.TemporaryDirectory() as td:
                path = self._write_config(
                    td,
                    {
                        "seats": {
                            "orchestrator": {
                                "backend": "openai-compatible",
                                "model": "llama3",
                                "base_url": "http://localhost:11434/v1",
                                "is_local": True,
                            }
                        }
                    },
                )
                backend, err = ws.resolve_orchestrator_backend(
                    config_path=path, execute=True
                )
        finally:
            patcher.stop()
        self.assertIsNone(err)
        self.assertIsInstance(backend, OpenAICompatibleOrchestratorBackend)

    def test_invalid_config_fails_loud(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "aesop.config.json"
            p.write_text("{not json", encoding="utf-8")
            backend, err = ws.resolve_orchestrator_backend(config_path=str(p))
        self.assertIsNone(backend)
        self.assertIsNotNone(err)


# ========================================================================
# 2. LIVE SEAT: decisions route through the configured backend, with effect
# ========================================================================

class TestOrchestratorSeatLive(unittest.TestCase):
    """A configured orchestrator backend actually decides final_catch."""

    def test_merge_verdict_routes_through_seat_and_approves(self):
        backend = FakeOrchestratorBackend([_MERGE_DECISION])
        with tempfile.TemporaryDirectory() as td:
            result = run_wave(
                ShipCapableFakeDriver(),
                _one_item_manifest(td),
                state_dir=None,
                git=None,
                orchestrator_backend=backend,
            )
        item = result["built"][0]
        self.assertTrue(item["verified"])
        self.assertEqual(item["final_catch"], "merge")
        self.assertEqual(item["adversarial_review"], "approved_by_orchestrator")
        self.assertEqual(result["adversarial_review"], "orchestrator_final_catch")
        self.assertEqual(result["orchestrator_review"]["decisions"], 1)
        self.assertEqual(backend.call_count, 1)
        # The decision genuinely routed through the seat: the prompt names
        # the decision type and the item under review.
        self.assertIn("final_catch", backend.received_prompts[0])
        self.assertIn("swap-item", backend.received_prompts[0])

    def test_block_verdict_stops_ship(self):
        backend = FakeOrchestratorBackend([_BLOCK_DECISION])
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td) / "state"
            result = run_wave(
                ShipCapableFakeDriver(),
                _one_item_manifest(td),
                state_dir=str(state_dir),
                git={"expectTopLevel": str(REPO)},
                orchestrator_backend=backend,
            )
            # Journal must record the block (verified flipped False) so a
            # resume cannot skip-and-ship the blocked item. With git shipping
            # configured the journal key is repo-aware (<repo>--<slug>.json).
            journal_files = [
                f
                for f in (state_dir / "journal").glob("*.json")
                if "swap-item" in f.name
            ]
            self.assertEqual(len(journal_files), 1)
            entry = json.loads(journal_files[0].read_text())
            self.assertFalse(entry["verified"])
            self.assertEqual(entry["phase"], "final_catch_blocked")
        item = result["built"][0]
        self.assertFalse(item["verified"])
        self.assertEqual(item["final_catch"], "block")
        self.assertEqual(item["adversarial_review"], "blocked_by_orchestrator")
        self.assertIn("swap-item", result["orchestrator_review"]["blocked"])
        self.assertIsNone(result.get("shipped"))

    def test_decision_failed_degrades_to_todays_behavior(self):
        """Seat outage/malformed output NEVER fabricates a verdict: the item
        stays test-verified and ships (manual merge downstream), with an
        honest decision_failed record."""
        backend = FakeOrchestratorBackend([])  # exhausted -> raises -> retries
        with tempfile.TemporaryDirectory() as td:
            result = run_wave(
                ShipCapableFakeDriver(),
                _one_item_manifest(td),
                state_dir=None,
                git={"expectTopLevel": str(REPO)},
                orchestrator_backend=backend,
            )
        item = result["built"][0]
        self.assertTrue(item["verified"])
        self.assertEqual(item["final_catch"], "DECISION_FAILED")
        self.assertEqual(item["adversarial_review"], "decision_failed_deferred")
        self.assertIn(
            "swap-item", result["orchestrator_review"]["decision_failed"]
        )
        self.assertEqual(result.get("shipped"), ["swap-item"])

    def test_escalate_ships_with_honest_record(self):
        backend = FakeOrchestratorBackend([_ESCALATE_DECISION])
        with tempfile.TemporaryDirectory() as td:
            result = run_wave(
                ShipCapableFakeDriver(),
                _one_item_manifest(td),
                state_dir=None,
                git={"expectTopLevel": str(REPO)},
                orchestrator_backend=backend,
            )
        item = result["built"][0]
        self.assertTrue(item["verified"])
        self.assertEqual(item["final_catch"], "escalate")
        self.assertEqual(item["adversarial_review"], "escalate")
        self.assertEqual(result.get("shipped"), ["swap-item"])

    def test_unverified_items_never_reach_the_seat(self):
        """final_catch reviews only test-green items; failed items are
        already not shipping and consume no seat decisions."""
        backend = FakeOrchestratorBackend([_MERGE_DECISION])
        with tempfile.TemporaryDirectory() as td:
            result = run_wave(
                ShipCapableFakeDriver(test_exit=1),
                _one_item_manifest(td),
                state_dir=None,
                git=None,
                orchestrator_backend=backend,
            )
        item = result["built"][0]
        self.assertFalse(item["verified"])
        self.assertEqual(item["adversarial_review"], "skipped_not_verified")
        self.assertNotIn("final_catch", item)
        self.assertEqual(backend.call_count, 0)
        self.assertEqual(result["orchestrator_review"]["decisions"], 0)


# ========================================================================
# 3. SWAP TRANSPARENCY: end-to-end shape invariance across the seat swap
# ========================================================================

class TestSwapTransparencyEndToEnd(unittest.TestCase):
    """Same task, public scheduler path, default vs swapped orchestrator:
    the Report JSON shape and the state layer structure are INVARIANT."""

    def _run_arm(self, orchestrator_backend):
        with tempfile.TemporaryDirectory() as td:
            tracker_path = _write_tracker(td)
            state_dir = Path(td) / "state"
            report = ws.run_wave_scheduler(
                tracker_path=tracker_path,
                max_items=1,
                dry_run=False,
                driver=ShipCapableFakeDriver(),
                state_dir=state_dir,
                orchestrator_backend=orchestrator_backend,
            )
            tracker_after = json.loads(
                Path(tracker_path).read_text(encoding="utf-8")
            )
            journal_dir = state_dir / "journal"
            journal = {}
            if journal_dir.exists():
                for f in sorted(journal_dir.glob("*.json")):
                    journal[f.name] = json.loads(f.read_text())
        return report, tracker_after, journal

    def test_report_and_state_shape_invariant_across_swap(self):
        fake_orch = FakeOrchestratorBackend([_MERGE_DECISION])
        report_a, tracker_a, journal_a = self._run_arm(None)
        report_b, tracker_b, journal_b = self._run_arm(fake_orch)

        # The swapped seat REALLY decided (this is not a cosmetic pass-through).
        self.assertEqual(fake_orch.call_count, 1)

        # HUMAN INTERFACE: Report JSON shape is invariant MODULO the two
        # documented seat-observability lanes (block-gate hardening): a live
        # seat adds EXACTLY "blocked" + "orchestrator_gate", nothing else,
        # and the default path never carries them.
        self.assertEqual(
            set(report_a.keys()) | {"blocked", "orchestrator_gate"},
            set(report_b.keys()),
        )
        self.assertNotIn("blocked", report_a)
        self.assertNotIn("orchestrator_gate", report_a)
        self.assertEqual(report_b["blocked"], [])
        self.assertEqual(report_a["phase"], report_b["phase"])
        self.assertEqual(report_a["success"], report_b["success"])
        self.assertEqual(report_a["merged"], report_b["merged"])
        self.assertEqual(
            len(report_a["items_shipped"]), len(report_b["items_shipped"])
        )
        self.assertEqual(len(report_a["items_shipped"]), 1)
        rec_a = report_a["items_shipped"][0]
        rec_b = report_b["items_shipped"][0]
        self.assertEqual(set(rec_a.keys()), set(rec_b.keys()))
        for field in ("slug", "backend", "tier", "verified", "testExit"):
            self.assertEqual(rec_a[field], rec_b[field], msg=field)
        self.assertTrue(rec_a["verified"])

        # STATE LAYER: tracker structure + terminal status invariant.
        self.assertEqual(
            tracker_a["items"][0]["status"], tracker_b["items"][0]["status"]
        )
        self.assertEqual(tracker_a["items"][0]["status"], "in_progress")
        self.assertEqual(
            set(tracker_a["items"][0].keys()), set(tracker_b["items"][0].keys())
        )

        # STATE LAYER: journal file names + entry key sets invariant
        # (timestamps/instance ids differ by value, never by shape).
        self.assertEqual(set(journal_a.keys()), set(journal_b.keys()))
        for name in journal_a:
            self.assertEqual(
                set(journal_a[name].keys()),
                set(journal_b[name].keys()),
                msg=name,
            )
            self.assertEqual(
                journal_a[name]["verified"], journal_b[name]["verified"]
            )

    def test_swapped_block_produces_same_report_shape(self):
        """Even when the swapped seat BLOCKS the item, the Report keeps its
        contract shape (content differs honestly: nothing shipped)."""
        report_a, _, _ = self._run_arm(None)
        report_b, _, _ = self._run_arm(
            FakeOrchestratorBackend([_BLOCK_DECISION])
        )
        # Same top-level contract minus ship-conditional keys (sha only
        # appears when a repo actually shipped) and the two documented
        # seat-observability lanes a live seat adds (block-gate hardening).
        conditional = {"sha", "tracker_update_attempted", "tracker_update_error"}
        seat_lanes = {"blocked", "orchestrator_gate"}
        self.assertEqual(
            set(report_a.keys()) - conditional,
            (set(report_b.keys()) - conditional) - seat_lanes,
        )
        self.assertEqual(report_b["items_shipped"], [])
        # The block is OBSERVABLE: blocked lane carries slug + reason.
        self.assertEqual(
            [b["slug"] for b in report_b["blocked"]], ["swap-item"]
        )
        self.assertEqual(
            report_b["orchestrator_gate"]["verdict_counts"]["block"], 1
        )
        # And honest: a blocked wave is not a silently-successful one.
        self.assertFalse(report_b["success"])


# ========================================================================
# 4. SCHEDULER WIRING
# ========================================================================

class TestSchedulerSeatWiring(unittest.TestCase):
    """run_wave_scheduler passes the orchestrator backend into run_wave."""

    def test_orchestrator_backend_passed_through_to_run_wave(self):
        sentinel = FakeOrchestratorBackend([_MERGE_DECISION])
        captured = {}
        orig = ws.run_wave

        def spy_run_wave(**kwargs):
            captured.update(kwargs)
            return {
                "preflight_ok": True,
                "aborted": False,
                "built": [],
                "shipped": [],
                "shipped_repos": [],
            }

        ws.run_wave = spy_run_wave
        try:
            with tempfile.TemporaryDirectory() as td:
                ws.run_wave_scheduler(
                    tracker_path=_write_tracker(td),
                    max_items=1,
                    dry_run=False,
                    driver=ShipCapableFakeDriver(),
                    state_dir=Path(td) / "state",
                    orchestrator_backend=sentinel,
                )
        finally:
            ws.run_wave = orig
        self.assertIs(captured.get("orchestrator_backend"), sentinel)

    def test_default_passes_none_backend(self):
        captured = {}
        orig = ws.run_wave

        def spy_run_wave(**kwargs):
            captured.update(kwargs)
            return {
                "preflight_ok": True,
                "aborted": False,
                "built": [],
                "shipped": [],
                "shipped_repos": [],
            }

        ws.run_wave = spy_run_wave
        try:
            with tempfile.TemporaryDirectory() as td:
                ws.run_wave_scheduler(
                    tracker_path=_write_tracker(td),
                    max_items=1,
                    dry_run=False,
                    driver=ShipCapableFakeDriver(),
                    state_dir=Path(td) / "state",
                )
        finally:
            ws.run_wave = orig
        self.assertIn("orchestrator_backend", captured)
        self.assertIsNone(captured["orchestrator_backend"])


if __name__ == "__main__":
    unittest.main()
