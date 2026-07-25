#!/usr/bin/env python3
"""HS-2 block-gate hardening: verified defects on the LIVE ship-decision path.

Covers (TDD for the block-gate hardening round):
  1. CONFIDENCE CONTRACT: final_catch.schema.json REQUIRES confidence, so the
     decision prompt must say REQUIRED (not "optional") -- a well-behaved seat
     that omitted confidence had its BLOCK verdict silently dropped
     (validation fail -> retries -> DECISION_FAILED -> item ships).
  2. BLOCKED LANE: a blocked item gets a TERMINAL tracker state ("blocked"),
     appears in Report.blocked (list of {slug, reason}), the orchestrator
     seat summary is persisted as Report.orchestrator_gate, success is not
     silently True, and a second scheduler run does NOT re-select/rebuild it.
  3. GATE VISIBILITY: an all-failing seat is flagged loudly
     (orchestrator_gate.status == "degraded") while ship semantics stay
     crash-only (the item still ships).
  4. QUARANTINE: on a block verdict the item's written files are restored to
     their pre-build state (tracked -> git checkout; untracked -> deleted);
     outside a git worktree quarantine conservatively skips (no deletion).
  5. SEAT SPEND: orchestrator-seat tokens are metered (backend
     get_tokens_spent), surfaced in orchestrator_review/orchestrator_gate,
     and included in the post-decision cost-ceiling check.
  6. F6: DECISION_FAILED dicts carry evidence as an ARRAY of >=1 strings.

All offline: no API key, no network. stdlib-only (unittest), ASCII-only,
Windows + Linux safe.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
TOOLS_DIR = REPO / "tools"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from agent_driver import CommandResult  # noqa: E402
from context_pack import ContextPack  # noqa: E402
from orchestrator_backend import (  # noqa: E402
    FakeOrchestratorBackend,
    OpenAICompatibleOrchestratorBackend,
)
from orchestrator_driver import OrchestratorDriver  # noqa: E402
import wave_loop  # noqa: E402
from wave_loop import run_wave  # noqa: E402
import wave_scheduler as ws  # noqa: E402

# Reuse the offline ship-capable fake worker driver from the HS-2 swap proof.
if str(REPO / "tests") not in sys.path:
    sys.path.insert(0, str(REPO / "tests"))
from test_hs2_swap_proof import (  # noqa: E402
    ShipCapableFakeDriver,
    _one_item_manifest,
    _write_tracker,
)

_KEY_ENV = "OPENAI" + "_" + "API" + "_" + "KEY"

_MERGE_DECISION = {
    "verdict": "merge",
    "evidence": ["test exit code 0; no red flags"],
    "confidence": 0.95,
}
_BLOCK_DECISION = {
    "verdict": "block",
    "evidence": ["refusing to ship: spot check flagged"],
    "confidence": 0.9,
    "hold_reason": "spot_check_flagged",
}
# The defect scenario: a well-behaved seat that blocks but omits confidence
# (the schema requires it; the OLD prompt said it was optional).
_BLOCK_NO_CONFIDENCE = {
    "verdict": "block",
    "evidence": ["refusing to ship: spot check flagged"],
}


def _fresh_pack():
    return ContextPack(
        decision_type="final_catch",
        sources_requested=(),
        evidence={"item": json.dumps({"slug": "x"})},
    )


# ========================================================================
# 1. Confidence contract: prompt must match the schema's required set
# ========================================================================

class TestConfidencePromptContract(unittest.TestCase):
    def test_final_catch_prompt_declares_confidence_required(self):
        """final_catch.schema.json requires confidence -> the prompt must say
        REQUIRED, never 'optional' (the contradiction inverted BLOCK->SHIP)."""
        backend = FakeOrchestratorBackend([_MERGE_DECISION])
        orch = OrchestratorDriver(backend, schema_dir=str(DRIVER_DIR))
        orch.decide("final_catch", _fresh_pack())
        prompt = backend.received_prompts[0]
        self.assertNotIn("confidence: optional", prompt)
        self.assertIn("confidence: REQUIRED", prompt)
        self.assertIn("0.0-1.0", prompt)

    def test_prompt_without_schema_keeps_confidence_optional(self):
        """Decision types whose schema does not require confidence keep the
        optional phrasing (no false demands)."""
        backend = FakeOrchestratorBackend(
            [{"verdict": "ok", "evidence": ["e"]}]
        )
        orch = OrchestratorDriver(backend)  # no schema_dir
        orch.decide("custom_type", _fresh_pack())
        prompt = backend.received_prompts[0]
        self.assertIn("confidence: optional", prompt)
        self.assertNotIn("confidence: REQUIRED", prompt)

    def test_block_with_confidence_validates_and_blocks(self):
        """A schema-conformant block (with confidence) validates: the verdict
        survives as 'block', schema-validated."""
        backend = FakeOrchestratorBackend([_BLOCK_DECISION])
        orch = OrchestratorDriver(backend, schema_dir=str(DRIVER_DIR))
        result = orch.decide("final_catch", _fresh_pack())
        self.assertEqual(result["verdict"], "block")
        self.assertTrue(result["schema_validated"])

    def test_block_without_confidence_fails_validation_documented(self):
        """DOCUMENTED crash-only semantics: omitting a required field still
        fails validation -> DECISION_FAILED (which ships). The prompt fix
        above is what makes well-behaved seats include confidence."""
        backend = FakeOrchestratorBackend([_BLOCK_NO_CONFIDENCE] * 3)
        orch = OrchestratorDriver(backend, schema_dir=str(DRIVER_DIR))
        result = orch.decide("final_catch", _fresh_pack())
        self.assertEqual(result["verdict"], "DECISION_FAILED")


# ========================================================================
# 2. Blocked lane: terminal tracker state + Report observability + no loop
# ========================================================================

class TestBlockedLaneScheduler(unittest.TestCase):
    def _run(self, td, backend):
        tracker_path = _write_tracker(td)
        report = ws.run_wave_scheduler(
            tracker_path=tracker_path,
            max_items=1,
            dry_run=False,
            driver=ShipCapableFakeDriver(),
            state_dir=Path(td) / "state",
            orchestrator_backend=backend,
        )
        tracker_after = json.loads(
            Path(tracker_path).read_text(encoding="utf-8")
        )
        return report, tracker_path, tracker_after

    def test_block_marks_tracker_blocked_and_reports(self):
        with tempfile.TemporaryDirectory() as td:
            report, _, tracker = self._run(
                td, FakeOrchestratorBackend([_BLOCK_DECISION])
            )
        # (a) TERMINAL tracker state: blocked, never left todo.
        self.assertEqual(tracker["items"][0]["status"], "blocked")
        # (b) Report.blocked lane with slug + reason.
        self.assertIn("blocked", report)
        self.assertEqual(len(report["blocked"]), 1)
        self.assertEqual(report["blocked"][0]["slug"], "swap-item")
        self.assertTrue(report["blocked"][0]["reason"])
        # Seat summary persisted into the Report.
        self.assertIn("orchestrator_gate", report)
        gate = report["orchestrator_gate"]
        self.assertEqual(gate["verdict_counts"]["block"], 1)
        self.assertEqual(gate["decisions"], 1)
        # success reflects the block honestly (not silently True).
        self.assertFalse(report["success"])
        # Nothing shipped.
        self.assertEqual(report["items_shipped"], [])

    def test_blocked_item_not_reselected_on_second_run(self):
        with tempfile.TemporaryDirectory() as td:
            report1, tracker_path, tracker = self._run(
                td, FakeOrchestratorBackend([_BLOCK_DECISION])
            )
            self.assertEqual(tracker["items"][0]["status"], "blocked")
            # Second run: fresh seat; the blocked item must NOT be re-selected
            # (no rebuild spend, no unbounded block loop).
            backend2 = FakeOrchestratorBackend([_BLOCK_DECISION])
            driver2 = ShipCapableFakeDriver()
            report2 = ws.run_wave_scheduler(
                tracker_path=tracker_path,
                max_items=1,
                dry_run=False,
                driver=driver2,
                state_dir=Path(td) / "state",
                orchestrator_backend=backend2,
            )
        self.assertEqual(report2["items_selected"], [])
        self.assertEqual(driver2.dispatch_count, 0)
        self.assertEqual(backend2.call_count, 0)

    def test_merge_path_reports_empty_blocked_lane(self):
        with tempfile.TemporaryDirectory() as td:
            report, _, tracker = self._run(
                td, FakeOrchestratorBackend([_MERGE_DECISION])
            )
        self.assertEqual(report["blocked"], [])
        self.assertEqual(
            report["orchestrator_gate"]["verdict_counts"]["merge"], 1
        )
        self.assertEqual(tracker["items"][0]["status"], "in_progress")
        self.assertTrue(report["success"])

    def test_default_path_has_no_gate_keys(self):
        """No-op invariant: without a live seat the Report gains NO new keys."""
        with tempfile.TemporaryDirectory() as td:
            report, _, _ = self._run(td, None)
        self.assertNotIn("blocked", report)
        self.assertNotIn("orchestrator_gate", report)


# ========================================================================
# 3. Gate visibility: an all-failing seat is flagged, never invisible
# ========================================================================

class TestGateDegradedSignal(unittest.TestCase):
    def test_all_failing_seat_flags_degraded(self):
        with tempfile.TemporaryDirectory() as td:
            tracker_path = _write_tracker(td)
            report = ws.run_wave_scheduler(
                tracker_path=tracker_path,
                max_items=1,
                dry_run=False,
                driver=ShipCapableFakeDriver(),
                state_dir=Path(td) / "state",
                # Exhausted canned responses -> every decide_call raises
                # -> DECISION_FAILED on every decision.
                orchestrator_backend=FakeOrchestratorBackend([]),
            )
        gate = report["orchestrator_gate"]
        self.assertEqual(gate["status"], "degraded")
        self.assertEqual(gate["decisions"], 1)
        self.assertEqual(gate["verdict_counts"]["decision_failed"], 1)
        self.assertEqual(gate["decision_failed"], ["swap-item"])
        # Crash-only ship semantics preserved: the item still ships.
        shipped_slugs = [i["slug"] for i in report["items_shipped"]]
        self.assertIn("swap-item", shipped_slugs)

    def test_gate_with_no_decisions_not_degraded(self):
        """A wave with zero verified items makes zero decisions: that is
        'no_decisions', not 'degraded' (no false alarms)."""
        with tempfile.TemporaryDirectory() as td:
            tracker_path = _write_tracker(td)
            report = ws.run_wave_scheduler(
                tracker_path=tracker_path,
                max_items=1,
                dry_run=False,
                driver=ShipCapableFakeDriver(test_exit=1),
                state_dir=Path(td) / "state",
                orchestrator_backend=FakeOrchestratorBackend([]),
            )
        gate = report["orchestrator_gate"]
        self.assertEqual(gate["decisions"], 0)
        self.assertEqual(gate["status"], "no_decisions")


# ========================================================================
# 4. Quarantine: a block reverts the item's written files
# ========================================================================

class RealGitBlockDriver(ShipCapableFakeDriver):
    """Fake worker whose git commands REALLY run (subprocess) so the
    quarantine path can be proven against a real temp repo."""

    def run_command(self, command, cwd=None, shell=None):
        if command.strip().startswith("git"):
            completed = subprocess.run(
                command,
                cwd=cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return CommandResult(
                exit_code=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        return CommandResult(exit_code=self.test_exit, stdout="TEST")


def _init_temp_git_repo(td):
    """git init a temp repo with one committed tracked file. Identity is
    passed inline per-commit (-c): never touches user/global git config."""
    subprocess.run(
        "git init -q", cwd=td, shell=True, check=True, timeout=60
    )
    tracked = Path(td) / "mod_hs2.py"
    tracked.write_text("ORIGINAL CONTENT\n", encoding="utf-8")
    subprocess.run(
        "git add mod_hs2.py", cwd=td, shell=True, check=True, timeout=60
    )
    subprocess.run(
        "git -c user.name=aesop-test -c user.email=t@example.invalid "
        "commit -q -m baseline",
        cwd=td,
        shell=True,
        check=True,
        timeout=60,
    )
    return tracked


class TestBlockQuarantine(unittest.TestCase):
    def test_block_restores_tracked_and_deletes_untracked(self):
        with tempfile.TemporaryDirectory() as td:
            tracked = _init_temp_git_repo(td)
            manifest = {
                "wave_id": "hs2-quarantine",
                "items": [
                    {
                        "slug": "swap-item",
                        "ownsFiles": ["mod_hs2.py", "new_untracked.py"],
                        "prompt": "Rewrite mod_hs2.py; add new_untracked.py.",
                        "testCmd": "run-item-test",
                        "workDir": td,
                    }
                ],
            }
            result = run_wave(
                RealGitBlockDriver(),
                manifest,
                state_dir=None,
                git=None,
                orchestrator_backend=FakeOrchestratorBackend(
                    [_BLOCK_DECISION]
                ),
            )
            item = result["built"][0]
            self.assertEqual(item["final_catch"], "block")
            # Tracked file restored to its pre-build content.
            self.assertEqual(
                tracked.read_text(encoding="utf-8"), "ORIGINAL CONTENT\n"
            )
            # Untracked file (did not exist pre-build) removed.
            self.assertFalse((Path(td) / "new_untracked.py").exists())
            # Tree left clean: git sees no modifications.
            status = subprocess.run(
                "git status --porcelain",
                cwd=td,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(status.stdout.strip(), "")
            # Honest per-item quarantine record.
            q = item["quarantine"]
            self.assertIn("mod_hs2.py", q["restored"])
            self.assertIn("new_untracked.py", q["deleted"])
            self.assertEqual(q["errors"], [])

    def test_quarantine_skips_conservatively_outside_git(self):
        """Not a git worktree -> quarantine must NOT guess (no deletions);
        it records an honest skip instead."""
        with tempfile.TemporaryDirectory() as td:
            # ShipCapableFakeDriver fakes git answers ('OK', not 'true'),
            # so the worktree probe fails the strict check.
            result = run_wave(
                ShipCapableFakeDriver(),
                _one_item_manifest(td),
                state_dir=None,
                git=None,
                orchestrator_backend=FakeOrchestratorBackend(
                    [_BLOCK_DECISION]
                ),
            )
            item = result["built"][0]
            self.assertEqual(item["final_catch"], "block")
            q = item["quarantine"]
            self.assertEqual(q["skipped_reason"], "not_a_git_worktree")
            # The written file is untouched (conservative no-op).
            self.assertTrue((Path(td) / "mod_hs2.py").exists())


# ========================================================================
# 5. Seat spend metering + post-decision ceiling check
# ========================================================================

class _RecordingCeiling:
    """Stands in for tools/cost_ceiling: records check() calls; exceeds at
    a configurable threshold."""

    def __init__(self, threshold=None):
        self.threshold = threshold
        self.calls = []

    def check(self, spent=None, trip=True, state_dir=None, **kwargs):
        self.calls.append(spent)
        exceeded = (
            self.threshold is not None
            and spent is not None
            and spent >= self.threshold
        )
        return {
            "period": "wave",
            "ceiling": self.threshold,
            "spent": spent,
            "exceeded": exceeded,
            "tripped": False,
            "reason": None,
        }


class TestSeatSpendMetering(unittest.TestCase):
    def test_fake_backend_seat_tokens_surface_in_review(self):
        backend = FakeOrchestratorBackend(
            [_MERGE_DECISION], tokens_per_call=42
        )
        with tempfile.TemporaryDirectory() as td:
            result = run_wave(
                ShipCapableFakeDriver(),
                _one_item_manifest(td),
                state_dir=None,
                git=None,
                orchestrator_backend=backend,
            )
        self.assertEqual(backend.get_tokens_spent(), 42)
        self.assertEqual(
            result["orchestrator_review"]["seat_tokens_spent"], 42
        )

    def test_openai_backend_accumulates_usage_tokens(self):
        def fake_transport(payload, timeout_s=None, base_url=None):
            return {
                "choices": [
                    {"message": {"content": json.dumps(_MERGE_DECISION)}}
                ],
                "usage": {"total_tokens": 123},
            }

        with mock.patch.dict(os.environ, {_KEY_ENV: "test-" + "dummy"}):
            backend = OpenAICompatibleOrchestratorBackend(
                transport=fake_transport
            )
            backend.decide_call("prompt")
            backend.decide_call("prompt")
        self.assertEqual(backend.get_tokens_spent(), 246)

    def test_seat_spend_included_in_post_decision_ceiling_check(self):
        """Driver spend alone stays under the ceiling; driver + seat spend
        exceeds it -> the wave aborts BEFORE ship with the new reason."""
        fake_ceiling = _RecordingCeiling(threshold=200)
        backend = FakeOrchestratorBackend(
            [_MERGE_DECISION], tokens_per_call=150
        )
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(wave_loop, "cost_ceiling", fake_ceiling):
                result = run_wave(
                    ShipCapableFakeDriver(),  # spends 100 tokens on dispatch
                    _one_item_manifest(td),
                    state_dir=td,
                    git={"expectTopLevel": str(REPO)},
                    orchestrator_backend=backend,
                )
        self.assertTrue(result["aborted"])
        self.assertEqual(
            result["abort_reason"], "cost_ceiling_exceeded_after_decisions"
        )
        self.assertIsNone(result.get("shipped"))
        # The post-decision check saw driver (100) + seat (150) spend.
        self.assertIn(250, fake_ceiling.calls)

    def _count_ceiling_checks(self, backend):
        fake_ceiling = _RecordingCeiling(threshold=None)
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(wave_loop, "cost_ceiling", fake_ceiling):
                run_wave(
                    ShipCapableFakeDriver(),
                    _one_item_manifest(td),
                    state_dir=td,
                    git=None,
                    orchestrator_backend=backend,
                )
        return len(fake_ceiling.calls)

    def test_no_seat_means_no_extra_ceiling_check(self):
        """No-op invariant: without a live seat the post-Phase-6 ceiling
        check does not run -- the live-seat arm makes exactly ONE more
        check than the identical no-seat arm (call pattern otherwise
        byte-identical to pre-HS-2)."""
        none_calls = self._count_ceiling_checks(None)
        live_calls = self._count_ceiling_checks(
            FakeOrchestratorBackend([_MERGE_DECISION])
        )
        self.assertEqual(live_calls, none_calls + 1)


# ========================================================================
# 6. F6: DECISION_FAILED evidence is an array (driver's own contract)
# ========================================================================

class TestDecisionFailedEvidenceArray(unittest.TestCase):
    def _assert_evidence_array(self, result):
        self.assertEqual(result["verdict"], "DECISION_FAILED")
        self.assertIsInstance(result["evidence"], list)
        self.assertGreaterEqual(len(result["evidence"]), 1)
        for entry in result["evidence"]:
            self.assertIsInstance(entry, str)
            self.assertTrue(entry)

    def test_backend_error_evidence_is_array(self):
        orch = OrchestratorDriver(FakeOrchestratorBackend([]))  # always raises
        self._assert_evidence_array(orch.decide("final_catch", _fresh_pack()))

    def test_malformed_json_evidence_is_array(self):
        orch = OrchestratorDriver(
            FakeOrchestratorBackend(["not json"] * 3)
        )
        self._assert_evidence_array(orch.decide("final_catch", _fresh_pack()))

    def test_invalid_structure_evidence_is_array(self):
        orch = OrchestratorDriver(
            FakeOrchestratorBackend([{"verdict": "ok"}] * 3)  # no evidence
        )
        self._assert_evidence_array(orch.decide("final_catch", _fresh_pack()))


if __name__ == "__main__":
    unittest.main()
