#!/usr/bin/env python3
"""Tests for seated_shadow_adjudication.py (increment 4a redo).

Verifies that the seated shadow tool:
1. Routes through real OrchestratorDriver.decide() seam
2. Persists full reasoning in verdicts
3. Builds real context packs with file brain + evidence
4. Never leaks labels into context packs
5. Computes stability correctly over N runs
6. Handles item 9 flip logic
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, Optional

# Add tools/ to path for import
REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
DRIVER_DIR = REPO_ROOT / "driver"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

# Import the tool
import seated_shadow_adjudication as seated  # noqa: E402

# Import driver modules
from orchestrator_driver import OrchestratorDriver  # noqa: E402
from orchestrator_backend import FakeOrchestratorBackend  # noqa: E402
from context_pack import build_context_pack, ContextPack  # noqa: E402


class TestSeatedContextPack(unittest.TestCase):
    """Test real context pack building with file brain + evidence."""

    def setUp(self):
        """Create temp directories for test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name) / "repo"
        self.conductor_root = Path(self.temp_dir.name) / "conductor"
        self.repo_root.mkdir()
        self.conductor_root.mkdir()

        # Create STATE.md
        (self.repo_root / "STATE.md").write_text("PHASE: test\n", encoding="utf-8")

        # Create state dir and tracker.json
        state_dir = self.repo_root / "state"
        state_dir.mkdir()
        (state_dir / "tracker.json").write_text(
            json.dumps({"items": []}), encoding="utf-8"
        )

    def tearDown(self):
        """Clean up temp directories."""
        self.temp_dir.cleanup()

    def test_seated_pack_includes_evidence(self):
        """Verify seated context pack includes evidence dict."""
        item = seated.CorpusItem(
            id="test-item",
            finding_text="Test finding",
            source_lens="test_lens",
            incumbent_verdict="real_defect",
            ground_truth="real_defect",
            gt_note="test",
            evidence=["Evidence 1", "Evidence 2", "Evidence 3"],
        )

        pack = seated.build_seated_context_pack(
            item, str(self.repo_root), str(self.conductor_root)
        )

        # Verify evidence is in the pack (includes finding + 3 evidence items).
        self.assertIn("evidence", pack.__dict__)
        self.assertEqual(len(pack.evidence), 4)  # 1 finding + 3 evidence
        self.assertIn("finding", pack.evidence)
        self.assertEqual(pack.evidence.get("evidence_0"), "Evidence 1")
        self.assertEqual(pack.evidence.get("evidence_1"), "Evidence 2")
        self.assertEqual(pack.evidence.get("evidence_2"), "Evidence 3")

    def test_labels_never_leak(self):
        """Verify labels never appear in context pack content."""
        item = seated.CorpusItem(
            id="test-item",
            finding_text="Test finding",
            source_lens="test_lens",
            incumbent_verdict="real_defect",
            ground_truth="real_defect",
            gt_note="test note with sensitive data",
            evidence=["Evidence"],
        )

        # This should not raise an error; the assertion is inside the function.
        pack = seated.build_seated_context_pack(
            item, str(self.repo_root), str(self.conductor_root)
        )

        # Double-check: labels should not be in content.
        pack_text = json.dumps(pack.content)
        self.assertNotIn("incumbent_verdict", pack_text)
        self.assertNotIn("ground_truth", pack_text)
        self.assertNotIn("gt_note", pack_text)


class TestSeatedAdjudication(unittest.TestCase):
    """Test seated adjudication through OrchestratorDriver."""

    def setUp(self):
        """Setup driver with fake backend."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name) / "repo"
        self.conductor_root = Path(self.temp_dir.name) / "conductor"
        self.repo_root.mkdir()
        self.conductor_root.mkdir()

        # Create minimal state files.
        (self.repo_root / "STATE.md").write_text("PHASE: test\n", encoding="utf-8")
        state_dir = self.repo_root / "state"
        state_dir.mkdir()
        (state_dir / "tracker.json").write_text(
            json.dumps({"items": []}), encoding="utf-8"
        )

        # Create fake backend with canned verdicts (must have 'verdict' and 'evidence' keys).
        canned_responses = [
            {
                "verdict": "false_positive",
                "evidence": ["This is the reasoning for verdict 1"],
                "confidence": 0.95,
            },
            {
                "verdict": "false_positive",
                "evidence": ["This is the reasoning for verdict 2"],
                "confidence": 0.90,
            },
            {
                "verdict": "real_defect",
                "evidence": ["This is the reasoning for verdict 3"],
                "confidence": 0.85,
            },
        ]
        self.backend = FakeOrchestratorBackend(canned_responses=canned_responses)
        self.driver = OrchestratorDriver(self.backend, max_retries=2)

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_adjudicate_persists_reasoning(self):
        """Verify adjudication persists full reasoning text."""
        item = seated.CorpusItem(
            id="test-item",
            finding_text="Test finding",
            source_lens="test_lens",
            incumbent_verdict="unknown",
            ground_truth="false_positive",
            gt_note="test",
            evidence=["Evidence"],
        )

        result = seated.adjudicate_one_finding(
            self.driver, item, str(self.repo_root), str(self.conductor_root), None, 1
        )

        # Verify result has reasoning.
        self.assertIsNotNone(result.challenger_reasoning)
        self.assertIn("reasoning", result.challenger_reasoning.lower())
        self.assertEqual(result.challenger_classification, "false_positive")

    def test_schema_valid_set(self):
        """Verify schema_valid is set in results."""
        item = seated.CorpusItem(
            id="test-item",
            finding_text="Test finding",
            source_lens="test_lens",
            incumbent_verdict="unknown",
            ground_truth="false_positive",
            gt_note="test",
            evidence=["Evidence"],
        )

        result = seated.adjudicate_one_finding(
            self.driver, item, str(self.repo_root), str(self.conductor_root), None, 1
        )

        # For fake backend (no schema), schema_valid will be False by default.
        # In real operation with schema, it will be True.
        self.assertIsInstance(result.schema_valid, bool)


class TestSeatedAggregation(unittest.TestCase):
    """Test aggregation of seated verdicts across runs."""

    def test_modal_verdict_computation(self):
        """Verify modal verdict is computed correctly."""
        corpus = [
            seated.CorpusItem(
                id="item1",
                finding_text="Finding 1",
                source_lens="lens1",
                incumbent_verdict="real_defect",
                ground_truth="real_defect",
                gt_note="",
                evidence=[],
            ),
            seated.CorpusItem(
                id="item2",
                finding_text="Finding 2",
                source_lens="lens2",
                incumbent_verdict="false_positive",
                ground_truth="false_positive",
                gt_note="",
                evidence=[],
            ),
        ]

        verdicts = [
            # Item 1: 2/3 real_defect (modal)
            seated.SeatedVerdictItem(
                id="item1",
                run_num=1,
                challenger_classification="real_defect",
                challenger_reasoning="reason1",
                schema_valid=True,
                retries_used=0,
                confidence=0.9,
            ),
            seated.SeatedVerdictItem(
                id="item1",
                run_num=2,
                challenger_classification="real_defect",
                challenger_reasoning="reason2",
                schema_valid=True,
                retries_used=0,
                confidence=0.85,
            ),
            seated.SeatedVerdictItem(
                id="item1",
                run_num=3,
                challenger_classification="false_positive",
                challenger_reasoning="reason3",
                schema_valid=True,
                retries_used=0,
                confidence=0.7,
            ),
            # Item 2: 3/3 false_positive (modal)
            seated.SeatedVerdictItem(
                id="item2",
                run_num=1,
                challenger_classification="false_positive",
                challenger_reasoning="reason4",
                schema_valid=True,
                retries_used=0,
                confidence=0.95,
            ),
            seated.SeatedVerdictItem(
                id="item2",
                run_num=2,
                challenger_classification="false_positive",
                challenger_reasoning="reason5",
                schema_valid=True,
                retries_used=0,
                confidence=0.92,
            ),
            seated.SeatedVerdictItem(
                id="item2",
                run_num=3,
                challenger_classification="false_positive",
                challenger_reasoning="reason6",
                schema_valid=True,
                retries_used=0,
                confidence=0.88,
            ),
        ]

        aggregated = seated.aggregate_seated_results(verdicts, corpus, 3)

        # Check item 1.
        item1_agg = next(a for a in aggregated["per_item"] if a.id == "item1")
        self.assertEqual(item1_agg.modal_verdict, "real_defect")
        self.assertAlmostEqual(item1_agg.stability, 2 / 3)

        # Check item 2.
        item2_agg = next(a for a in aggregated["per_item"] if a.id == "item2")
        self.assertEqual(item2_agg.modal_verdict, "false_positive")
        self.assertAlmostEqual(item2_agg.stability, 1.0)

    def test_held_real_defects(self):
        """Verify held real defects are counted correctly."""
        corpus = [
            seated.CorpusItem(
                id="real1",
                finding_text="Real defect 1",
                source_lens="lens",
                incumbent_verdict="real_defect",
                ground_truth="real_defect",
                gt_note="",
                evidence=[],
            ),
            seated.CorpusItem(
                id="real2",
                finding_text="Real defect 2",
                source_lens="lens",
                incumbent_verdict="real_defect",
                ground_truth="real_defect",
                gt_note="",
                evidence=[],
            ),
            seated.CorpusItem(
                id="fp1",
                finding_text="False positive",
                source_lens="lens",
                incumbent_verdict="false_positive",
                ground_truth="false_positive",
                gt_note="",
                evidence=[],
            ),
        ]

        verdicts = [
            # real1: flipped to false_positive
            seated.SeatedVerdictItem(
                id="real1",
                run_num=1,
                challenger_classification="false_positive",
                challenger_reasoning="reason",
                schema_valid=True,
                retries_used=0,
                confidence=0.8,
            ),
            # real2: held as real_defect
            seated.SeatedVerdictItem(
                id="real2",
                run_num=1,
                challenger_classification="real_defect",
                challenger_reasoning="reason",
                schema_valid=True,
                retries_used=0,
                confidence=0.9,
            ),
            # fp1: correct
            seated.SeatedVerdictItem(
                id="fp1",
                run_num=1,
                challenger_classification="false_positive",
                challenger_reasoning="reason",
                schema_valid=True,
                retries_used=0,
                confidence=0.95,
            ),
        ]

        aggregated = seated.aggregate_seated_results(verdicts, corpus, 1)

        # Only real2 should be counted as held.
        self.assertEqual(aggregated["held_real_defects"], 1)
        self.assertEqual(aggregated["total_real_defects"], 2)

    def test_item9_flip_detection(self):
        """Verify item 9 flip is detected correctly."""
        corpus = [
            seated.CorpusItem(
                id="whitelist-gate-weakening",
                finding_text="Finding",
                source_lens="lens",
                incumbent_verdict="false_positive",
                ground_truth="false_positive",
                gt_note="",
                evidence=[],
            ),
        ]

        # Item 9 flips to false_positive (correct).
        verdicts = [
            seated.SeatedVerdictItem(
                id="whitelist-gate-weakening",
                run_num=1,
                challenger_classification="false_positive",
                challenger_reasoning="It does flip.",
                schema_valid=True,
                retries_used=0,
                confidence=0.9,
            ),
            seated.SeatedVerdictItem(
                id="whitelist-gate-weakening",
                run_num=2,
                challenger_classification="false_positive",
                challenger_reasoning="It flips again.",
                schema_valid=True,
                retries_used=0,
                confidence=0.92,
            ),
            seated.SeatedVerdictItem(
                id="whitelist-gate-weakening",
                run_num=3,
                challenger_classification="false_positive",
                challenger_reasoning="And again.",
                schema_valid=True,
                retries_used=0,
                confidence=0.88,
            ),
        ]

        aggregated = seated.aggregate_seated_results(verdicts, corpus, 3)
        item9 = aggregated["item_9_analysis"]

        self.assertTrue(item9["flips_to_false_positive"])
        self.assertEqual(item9["modal_verdict"], "false_positive")
        self.assertAlmostEqual(item9["stability"], 1.0)


class TestSeatedCorpusLoading(unittest.TestCase):
    """Test corpus loading."""

    def test_load_corpus_from_jsonl(self):
        """Verify corpus loads correctly from JSONL."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            corpus_file = f.name
            # Write test corpus.
            f.write(
                json.dumps(
                    {
                        "id": "item1",
                        "finding_text": "Finding 1",
                        "source_lens": "lens1",
                        "labels": {
                            "incumbent_verdict": "real_defect",
                            "ground_truth": "real_defect",
                            "gt_note": "note",
                        },
                        "evidence": ["Evidence 1"],
                    }
                )
            )
            f.write("\n")
            f.write(
                json.dumps(
                    {
                        "id": "item2",
                        "finding_text": "Finding 2",
                        "source_lens": "lens2",
                        "labels": {
                            "incumbent_verdict": "false_positive",
                            "ground_truth": "false_positive",
                            "gt_note": "note2",
                        },
                        "evidence": ["Evidence 2a", "Evidence 2b"],
                    }
                )
            )
            f.write("\n")

        try:
            corpus = seated.load_corpus(corpus_file)
            self.assertEqual(len(corpus), 2)
            self.assertEqual(corpus[0].id, "item1")
            self.assertEqual(corpus[0].ground_truth, "real_defect")
            self.assertEqual(corpus[1].id, "item2")
            self.assertEqual(len(corpus[1].evidence), 2)
        finally:
            Path(corpus_file).unlink()


class TestPathRedaction(unittest.TestCase):
    """Test path redaction helper function."""

    def test_redact_home_paths(self):
        """Verify home paths are redacted to <HOME>."""
        test_text = (
            "Code found at C:\\Users\\matt8\\project "
            "and /c/Users/matt8/other"
        )
        redacted = seated.redact_paths(test_text)

        self.assertNotIn("matt8", redacted)
        self.assertIn("<HOME>", redacted)

    def test_redact_repo_paths(self):
        """Verify repo paths are redacted to <REPO>."""
        test_text = "Path: C:\\Users\\matt8\\aesop\\state\\tracker.json"
        redacted = seated.redact_paths(test_text)

        self.assertNotIn("matt8", redacted)
        self.assertNotIn("aesop", redacted)
        self.assertIn("<REPO>", redacted)

    def test_redaction_preserves_non_path_content(self):
        """Verify non-path content is not redacted."""
        test_text = "This is normal text about matt8 variables"
        redacted = seated.redact_paths(test_text)

        # Non-path references to matt8 should remain
        self.assertIn("matt8", redacted)

    def test_redaction_in_json_reasoning(self):
        """Verify path redaction works in JSON reasoning strings."""
        reasoning = (
            "['Evidence from C:\\\\Users\\\\matt8\\\\aesop\\\\file.py', "
            "'More text with /c/Users/matt8/test.sh']"
        )
        redacted = seated.redact_paths(reasoning)

        self.assertNotIn("matt8", redacted)
        self.assertIn("<REPO>", redacted)

    def test_redaction_multi_escaped_backslash_forms(self):
        """Repr/JSON re-escaped paths (2x/4x/8x backslashes) are redacted.

        Round-2 adversarial finding: reasoning strings embed repr-of-list text
        whose paths carry doubled/quadrupled backslash separators; the previous
        1-2 backslash pattern missed them, leaking machine paths into committed
        bench/results files.
        """
        for depth in (1, 2, 4, 8):
            sep = "\\" * depth
            text = "path " + "C:" + sep + "Users" + sep + "matt8" + sep + "aesop" + sep + "state"
            redacted = seated.redact_paths(text)
            self.assertNotIn("matt8", redacted, f"escape depth {depth}")
            self.assertIn("<REPO>", redacted, f"escape depth {depth}")

    def test_redaction_forward_slash_windows_form(self):
        """Windows paths written with forward slashes are redacted."""
        text = "found C:/Users/matt8/aesop/tools/x.py and C:/Users/matt8/other"
        redacted = seated.redact_paths(text)
        self.assertNotIn("matt8", redacted)


class TestModalVerdictExcludesDecisionFailed(unittest.TestCase):
    """Test that DECISION_FAILED is excluded from modal verdict computation."""

    def test_modal_excludes_decision_failed(self):
        """Verify DECISION_FAILED verdicts are excluded from modal."""
        corpus = [
            seated.CorpusItem(
                id="item1",
                finding_text="Finding 1",
                source_lens="lens1",
                incumbent_verdict="real_defect",
                ground_truth="real_defect",
                gt_note="",
                evidence=[],
            ),
        ]

        # 2 real_defect, 1 DECISION_FAILED
        verdicts = [
            seated.SeatedVerdictItem(
                id="item1",
                run_num=1,
                challenger_classification="real_defect",
                challenger_reasoning="reason1",
                schema_valid=True,
                retries_used=0,
                confidence=0.9,
            ),
            seated.SeatedVerdictItem(
                id="item1",
                run_num=2,
                challenger_classification="real_defect",
                challenger_reasoning="reason2",
                schema_valid=True,
                retries_used=0,
                confidence=0.85,
            ),
            seated.SeatedVerdictItem(
                id="item1",
                run_num=3,
                challenger_classification="DECISION_FAILED",
                challenger_reasoning="Failed to decide",
                schema_valid=False,
                retries_used=2,
                confidence=0.0,
            ),
        ]

        aggregated = seated.aggregate_seated_results(verdicts, corpus, 3)
        item1_agg = aggregated["per_item"][0]

        # Modal should be real_defect, not DECISION_FAILED
        self.assertEqual(item1_agg.modal_verdict, "real_defect")
        self.assertAlmostEqual(item1_agg.stability, 2 / 3)

    def test_modal_all_failed_explicit(self):
        """Verify all-DECISION_FAILED case reports explicitly."""
        corpus = [
            seated.CorpusItem(
                id="item1",
                finding_text="Finding 1",
                source_lens="lens1",
                incumbent_verdict="real_defect",
                ground_truth="real_defect",
                gt_note="",
                evidence=[],
            ),
        ]

        # All verdicts are DECISION_FAILED
        verdicts = [
            seated.SeatedVerdictItem(
                id="item1",
                run_num=1,
                challenger_classification="DECISION_FAILED",
                challenger_reasoning="Failed to decide",
                schema_valid=False,
                retries_used=2,
                confidence=0.0,
            ),
            seated.SeatedVerdictItem(
                id="item1",
                run_num=2,
                challenger_classification="DECISION_FAILED",
                challenger_reasoning="Failed to decide",
                schema_valid=False,
                retries_used=2,
                confidence=0.0,
            ),
            seated.SeatedVerdictItem(
                id="item1",
                run_num=3,
                challenger_classification="DECISION_FAILED",
                challenger_reasoning="Failed to decide",
                schema_valid=False,
                retries_used=2,
                confidence=0.0,
            ),
        ]

        aggregated = seated.aggregate_seated_results(verdicts, corpus, 3)
        item1_agg = aggregated["per_item"][0]

        # Should explicitly report all failed, not DECISION_FAILED as a verdict
        self.assertEqual(item1_agg.modal_verdict, "all_runs_failed")
        self.assertEqual(item1_agg.stability, 0.0)


if __name__ == "__main__":
    unittest.main()
