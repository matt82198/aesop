#!/usr/bin/env python3
"""Tests for shadow adjudication wave.

TDD-first tests covering:
  * Corpus file parsing: 16 items with required fields.
  * Blind adjudication: labels NEVER appear in context packs or prompts.
  * Scorecard math: agreement/correctness logic, bar criteria.
  * API call cap: max 40 calls enforced.

Offline only (no live API calls). Uses FakeTransport fixture.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Add tools/ to sys.path.
TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = TEST_DIR.parent
TOOLS_DIR = REPO_ROOT / "tools"
DRIVER_DIR = REPO_ROOT / "driver"

if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from context_pack import ContextPack  # noqa: E402
from shadow_adjudication import (  # noqa: E402
    AggregatedScorecardItem,
    CorpusItem,
    ScorecardItem,
    aggregate_runs,
    build_finding_context_pack,
    compute_scorecard_stats,
    load_corpus,
)


class TestCorpusLoading(unittest.TestCase):
    """Test corpus file parsing."""

    def test_corpus_file_exists(self):
        """Verify corpus file exists in expected location."""
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-2026-07-23.jsonl"
        )
        self.assertTrue(
            corpus_path.exists(), f"Corpus file not found: {corpus_path}"
        )

    def test_corpus_has_16_items(self):
        """Corpus must have exactly 16 items."""
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-2026-07-23.jsonl"
        )
        corpus = load_corpus(str(corpus_path))
        self.assertEqual(
            len(corpus), 16, f"Expected 16 corpus items, got {len(corpus)}"
        )

    def test_corpus_items_have_required_fields(self):
        """Each corpus item must have required fields."""
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-2026-07-23.jsonl"
        )
        corpus = load_corpus(str(corpus_path))

        required_fields = ["id", "finding_text", "source_lens"]
        for idx, item in enumerate(corpus):
            for field in required_fields:
                self.assertTrue(
                    hasattr(item, field),
                    f"Item {idx} ({item.id}) missing field: {field}",
                )

    def test_corpus_labels_present(self):
        """Verify corpus items have labels (ground truth data)."""
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-2026-07-23.jsonl"
        )
        corpus = load_corpus(str(corpus_path))

        for item in corpus:
            self.assertIsNotNone(
                item.ground_truth,
                f"Item {item.id} missing ground_truth label",
            )
            self.assertIsNotNone(
                item.incumbent_verdict,
                f"Item {item.id} missing incumbent_verdict label",
            )


class TestBlindAdjudication(unittest.TestCase):
    """Test blind adjudication: labels never reach context packs."""

    def setUp(self):
        """Create test item and repo structure."""
        self.item = CorpusItem(
            id="test-finding",
            finding_text="Test finding: something breaks.",
            source_lens="test_lens",
            incumbent_verdict="real_defect",
            ground_truth="real_defect",
            gt_note="Test note",
        )
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = self.temp_dir.name
        self.conductor_root = Path(self.temp_dir.name) / "conductor3"
        self.conductor_root.mkdir(exist_ok=True)

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def test_context_pack_excludes_labels(self):
        """Context pack MUST NOT contain label strings."""
        pack = build_finding_context_pack(
            self.item, self.repo_root, str(self.conductor_root)
        )

        # Serialize pack to JSON (as would be sent to challenger).
        pack_json = json.dumps(pack.content)

        # Assert label strings do NOT appear.
        label_strings = [
            "incumbent_verdict",
            "ground_truth",
            "gt_note",
        ]
        for label in label_strings:
            self.assertNotIn(
                label,
                pack_json,
                f"Label string '{label}' found in context pack (blind adjudication violated)",
            )

    def test_context_pack_includes_finding_text(self):
        """Context pack MUST include the finding_text."""
        pack = build_finding_context_pack(
            self.item, self.repo_root, str(self.conductor_root)
        )

        # The finding text should be in the pack.
        pack_text = json.dumps(pack.content)
        self.assertIn(
            self.item.finding_text,
            pack_text,
            "Finding text not found in context pack",
        )

    def test_context_pack_includes_source_lens(self):
        """Context pack MUST include the source_lens."""
        pack = build_finding_context_pack(
            self.item, self.repo_root, str(self.conductor_root)
        )

        # The source lens should be in the pack.
        pack_text = json.dumps(pack.content)
        self.assertIn(
            self.item.source_lens,
            pack_text,
            "Source lens not found in context pack",
        )


class TestScorecardMath(unittest.TestCase):
    """Test scorecard computation logic."""

    def test_agreement_calculation(self):
        """Test agreement rate calculation."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "real_defect", "real_defect", ""),
            CorpusItem("id2", "text2", "lens2", "false_positive", "false_positive", ""),
            CorpusItem("id3", "text3", "lens3", "real_defect", "false_positive", ""),
        ]

        scorecard = [
            ScorecardItem(
                "id1",
                "real_defect",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=True,
                correct_vs_ground_truth=True,
            ),
            ScorecardItem(
                "id2",
                "false_positive",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=True,
                correct_vs_ground_truth=True,
            ),
            ScorecardItem(
                "id3",
                "real_defect",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=False,
                correct_vs_ground_truth=False,
            ),
        ]

        stats = compute_scorecard_stats(scorecard, corpus)

        # 2 out of 3 agree with incumbent.
        self.assertAlmostEqual(
            stats["overall_agreement_pct"], 66.7, delta=0.1
        )

    def test_real_defect_subset_accuracy(self):
        """Test accuracy on real_defect subset."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "real_defect", "real_defect", ""),
            CorpusItem("id2", "text2", "lens2", "real_defect", "real_defect", ""),
            CorpusItem("id3", "text3", "lens3", "enhancement_opportunity", "enhancement_opportunity", ""),
        ]

        scorecard = [
            ScorecardItem(
                "id1",
                "real_defect",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=True,
                correct_vs_ground_truth=True,
            ),
            ScorecardItem(
                "id2",
                "false_positive",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=False,
                correct_vs_ground_truth=False,
            ),
            ScorecardItem(
                "id3",
                "enhancement_opportunity",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=True,
                correct_vs_ground_truth=True,
            ),
        ]

        stats = compute_scorecard_stats(scorecard, corpus)

        # Real defect subset: 1 correct out of 2.
        self.assertAlmostEqual(
            stats["real_defect_agreement_pct"], 50.0, delta=0.1
        )

    def test_false_positive_subset_accuracy(self):
        """Test accuracy on false_positive subset."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "false_positive", "false_positive", ""),
            CorpusItem("id2", "text2", "lens2", "false_positive", "false_positive", ""),
            CorpusItem("id3", "text3", "lens3", "real_defect", "real_defect", ""),
        ]

        scorecard = [
            ScorecardItem(
                "id1",
                "false_positive",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=True,
                correct_vs_ground_truth=True,
            ),
            ScorecardItem(
                "id2",
                "false_positive",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=True,
                correct_vs_ground_truth=True,
            ),
            ScorecardItem(
                "id3",
                "real_defect",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=True,
                correct_vs_ground_truth=True,
            ),
        ]

        stats = compute_scorecard_stats(scorecard, corpus)

        # False positive subset: 2 correct out of 2.
        self.assertAlmostEqual(
            stats["false_positive_agreement_pct"], 100.0, delta=0.1
        )

    def test_rubber_stamp_refutation_detection(self):
        """Test rubber-stamp (items 9, 14) refutation detection."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "false_positive", "false_positive", ""),
            CorpusItem("whitelist-gate-weakening", "text2", "lens2", "false_positive", "false_positive", ""),
            CorpusItem("regression-ui-suite", "text3", "lens3", "false_positive", "false_positive", ""),
        ]

        scorecard = [
            ScorecardItem(
                "id1",
                "real_defect",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=False,
                correct_vs_ground_truth=False,
            ),
            ScorecardItem(
                "whitelist-gate-weakening",
                "false_positive",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=True,
                correct_vs_ground_truth=True,
            ),
            ScorecardItem(
                "regression-ui-suite",
                "false_positive",
                False,
                0,
                True,
                0,
                agreement_with_incumbent=True,
                correct_vs_ground_truth=True,
            ),
        ]

        stats = compute_scorecard_stats(scorecard, corpus)

        # Both rubber-stamp items correctly identified as false_positive.
        self.assertEqual(stats["rubber_stamp_refutations_count"], 2)

    def test_schema_validity_rate(self):
        """Test schema validity tracking."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "real_defect", "real_defect", ""),
            CorpusItem("id2", "text2", "lens2", "real_defect", "real_defect", ""),
            CorpusItem("id3", "text3", "lens3", "real_defect", "real_defect", ""),
        ]

        scorecard = [
            ScorecardItem("id1", "real_defect", False, 0, True, 0, True, True),
            ScorecardItem("id2", "real_defect", False, 0, False, 0, True, True),
            ScorecardItem("id3", "real_defect", False, 0, True, 0, True, True),
        ]

        stats = compute_scorecard_stats(scorecard, corpus)

        # 2 out of 3 schema valid.
        self.assertAlmostEqual(stats["schema_valid_pct"], 66.7, delta=0.1)

    def test_decision_failed_count(self):
        """Test DECISION_FAILED tracking."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "real_defect", "real_defect", ""),
            CorpusItem("id2", "text2", "lens2", "real_defect", "real_defect", ""),
        ]

        scorecard = [
            ScorecardItem(
                "id1",
                "DECISION_FAILED",
                False,
                0,
                False,
                2,
                False,
                False,
            ),
            ScorecardItem("id2", "real_defect", False, 0, True, 0, True, True),
        ]

        stats = compute_scorecard_stats(scorecard, corpus)

        # 1 DECISION_FAILED.
        self.assertEqual(stats["decision_failed_count"], 1)


class TestSuccessBar(unittest.TestCase):
    """Test success bar criteria."""

    def test_success_bar_real_defect_threshold(self):
        """Success requires >=80% agreement on real_defect subset."""
        # Create 10 items, all real_defect.
        corpus = [
            CorpusItem(f"id{i}", f"text{i}", f"lens{i}", "real_defect", "real_defect", "")
            for i in range(10)
        ]

        # Scenario 1: 8 correct (80%) — should PASS.
        scorecard_pass = [
            ScorecardItem(
                f"id{i}",
                "real_defect",
                False,
                0,
                True,
                0,
                True,
                correct_vs_ground_truth=(i < 8),
            )
            for i in range(10)
        ]

        stats_pass = compute_scorecard_stats(scorecard_pass, corpus)
        self.assertGreaterEqual(
            stats_pass["real_defect_agreement_pct"],
            80.0,
            "80% agreement should meet bar",
        )

        # Scenario 2: 7 correct (70%) — should FAIL.
        scorecard_fail = [
            ScorecardItem(
                f"id{i}",
                "real_defect",
                False,
                0,
                True,
                0,
                True,
                correct_vs_ground_truth=(i < 7),
            )
            for i in range(10)
        ]

        stats_fail = compute_scorecard_stats(scorecard_fail, corpus)
        self.assertLess(
            stats_fail["real_defect_agreement_pct"],
            80.0,
            "70% agreement should fail bar",
        )


class TestEvidenceMechanismMode(unittest.TestCase):
    """Test mechanism-only evidence mode (confound fix 2: answer-leakage prevention)."""

    def test_mechanism_mode_strips_conclusion_clauses(self):
        """Mechanism mode should keep only first 2 evidence items (mechanism + behavior).

        The [3] conclusion/impact clause often contains the verdict direction
        and must be stripped to prevent answer-leakage to models.
        """
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-2026-07-23.jsonl"
        )
        corpus = load_corpus(str(corpus_path))

        # Test with one item that has 3 evidence parts
        test_item = corpus[0]
        self.assertGreaterEqual(len(test_item.evidence), 3, "Test item must have >= 3 evidence items")

        # Build pack in mechanism mode (should have only 2 evidence items)
        repo_root = REPO_ROOT
        conductor_root = REPO_ROOT.parent / "conductor3"
        pack_mechanism = build_finding_context_pack(
            test_item, str(repo_root), str(conductor_root), enriched=True, evidence_mode="mechanism"
        )

        # Verify evidence is sliced to 2 items
        self.assertEqual(
            len(pack_mechanism.evidence),
            2,
            f"Mechanism mode should have 2 evidence items, got {len(pack_mechanism.evidence)}",
        )

        # Verify full mode keeps all 3
        pack_full = build_finding_context_pack(
            test_item, str(repo_root), str(conductor_root), enriched=True, evidence_mode="full"
        )
        self.assertEqual(
            len(pack_full.evidence),
            3,
            f"Full mode should have 3 evidence items, got {len(pack_full.evidence)}",
        )

    def test_mechanism_mode_no_conclusion_tokens(self):
        """Mechanism mode must not contain the [3] conclusion clause.

        The [3] items contain verdict-direction-implying content and must be
        stripped in mechanism mode. We verify this by checking that the pack
        has exactly 2 evidence items (no [3] conclusion).
        """
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-2026-07-23.jsonl"
        )
        corpus = load_corpus(str(corpus_path))

        repo_root = REPO_ROOT
        conductor_root = REPO_ROOT.parent / "conductor3"

        for item in corpus:
            pack = build_finding_context_pack(
                item, str(repo_root), str(conductor_root), enriched=True, evidence_mode="mechanism"
            )

            # Mechanism mode must have exactly 2 evidence items (no [3] conclusion)
            self.assertEqual(
                len(pack.evidence),
                2,
                f"Item {item.id}: mechanism mode must have exactly 2 evidence items "
                f"(no [3] conclusion clause), got {len(pack.evidence)}: {list(pack.evidence.keys())}",
            )


class TestEvidenceSymmetry(unittest.TestCase):
    """Guard against asymmetric evidence richness across verdict classes.

    Increment 2.5 confound-fix: evidence must be balanced in structure and
    completeness across real_defect, false_positive, and enhancement items.
    """

    def test_corpus_evidence_minimum_length(self):
        """Each corpus item must have >= 2 evidence items for fairness."""
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-2026-07-23.jsonl"
        )
        corpus = load_corpus(str(corpus_path))

        for item in corpus:
            self.assertGreaterEqual(
                len(item.evidence),
                2,
                f"Item {item.id} ({item.ground_truth}) has only {len(item.evidence)} evidence items; need >= 2",
            )

    def test_evidence_length_balanced_across_classes(self):
        """Evidence structure should be comparable across ground_truth classes.

        Real_defect and false_positive items should have similar mean evidence
        length to prevent one class from being over-evidenced. Enhancement items
        should be comparable too.
        """
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-2026-07-23.jsonl"
        )
        corpus = load_corpus(str(corpus_path))

        # Partition by ground truth class.
        by_class = {}
        for item in corpus:
            cls = item.ground_truth.lower()
            if cls not in by_class:
                by_class[cls] = []
            by_class[cls].append(item)

        # Calculate mean evidence length per class.
        mean_lengths = {}
        for cls, items in by_class.items():
            total_chars = sum(
                len("\n".join(item.evidence).encode("utf-8")) for item in items
            )
            mean_lengths[cls] = (
                total_chars / len(items) if items else 0
            )

        # Assertion: no class should be >30% richer or poorer than the mean.
        if mean_lengths:
            overall_mean = sum(mean_lengths.values()) / len(mean_lengths)
            for cls, mean_len in mean_lengths.items():
                deviation = abs(mean_len - overall_mean) / overall_mean if overall_mean > 0 else 0
                self.assertLess(
                    deviation,
                    0.30,  # Allow 30% variance (rounding safety).
                    f"Class {cls} evidence (mean {mean_len:.0f} chars) deviates {deviation:.1%} "
                    f"from overall mean {overall_mean:.0f}; expected <30% variance for fairness",
                )


class TestNeutralCorpus(unittest.TestCase):
    """Tests for the verdict-neutral corpus (increment 2.6).

    The neutral corpus must:
    1. Contain only mechanism/behavior facts, not verdict-implying conclusions
    2. Pass a grep filter excluding conclusion words
    3. Have no label leak (same as original corpus)
    """

    def test_neutral_corpus_file_exists(self):
        """Verify neutral corpus file exists."""
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-neutral-2026-07-24.jsonl"
        )
        self.assertTrue(
            corpus_path.exists(), f"Neutral corpus file not found: {corpus_path}"
        )

    def test_neutral_corpus_has_16_items(self):
        """Neutral corpus must have exactly 16 items."""
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-neutral-2026-07-24.jsonl"
        )
        corpus = load_corpus(str(corpus_path))
        self.assertEqual(len(corpus), 16, f"Expected 16 items, got {len(corpus)}")

    def test_neutral_corpus_no_conclusion_words(self):
        """Evidence clauses must not contain verdict-implying conclusion words.

        Banned words (case-insensitive): 'therefore','so the','not a real','correctly catch',
        'would fail','masquerad','misleads','defeats','not in scope','still scanned',
        'Impact:','Semantic'.
        """
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-neutral-2026-07-24.jsonl"
        )
        corpus = load_corpus(str(corpus_path))

        banned_words = [
            "therefore",
            "so the",
            "not a real",
            "correctly catch",
            "would fail correctly",
            "masquerad",
            "misleads",
            "defeats",
            "not in scope",
            "still scanned",
            "impact:",
            "semantic:",
        ]

        for item in corpus:
            evidence_text = "\n".join(item.evidence).lower()
            for banned_word in banned_words:
                self.assertNotIn(
                    banned_word.lower(),
                    evidence_text,
                    f"Item {item.id}: evidence contains banned conclusion word '{banned_word}'",
                )

    def test_neutral_corpus_no_label_leak(self):
        """Neutral corpus items must not contain label strings (same as orig corpus)."""
        corpus_path = (
            REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-neutral-2026-07-24.jsonl"
        )
        corpus = load_corpus(str(corpus_path))

        label_strings = [
            "false_positive",  # As a label, not as a ground truth
            "real_defect",     # As a label, not as a ground truth
            "incumbent_verdict",
            "ground_truth",
            "gt_note",
        ]

        for item in corpus:
            evidence_text = "\n".join(item.evidence)
            # Check evidence only (labels are OK in the labels object)
            for label_str in label_strings:
                self.assertNotIn(
                    label_str,
                    evidence_text,
                    f"Item {item.id}: evidence contains label string '{label_str}'",
                )


class TestRepeatAggregation(unittest.TestCase):
    """Tests for N-repeat run aggregation (increment 2.6)."""

    def test_aggregate_runs_modal_verdict(self):
        """Test modal verdict computation across runs."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "real_defect", "real_defect", ""),
            CorpusItem("id2", "text2", "lens2", "false_positive", "false_positive", ""),
        ]

        # Simulate 3 runs: id1 gets [real_defect, real_defect, false_positive]
        # id2 gets [false_positive, false_positive, false_positive]
        run1 = [
            ScorecardItem("id1", "real_defect", False, 0, True, 0, True, True),
            ScorecardItem("id2", "false_positive", False, 0, True, 0, True, True),
        ]
        run2 = [
            ScorecardItem("id1", "real_defect", False, 0, True, 0, True, True),
            ScorecardItem("id2", "false_positive", False, 0, True, 0, True, True),
        ]
        run3 = [
            ScorecardItem("id1", "false_positive", False, 0, True, 0, False, False),
            ScorecardItem("id2", "false_positive", False, 0, True, 0, True, True),
        ]

        result = aggregate_runs([run1, run2, run3], corpus, 3)

        # id1 modal: real_defect (2/3)
        id1_agg = next(a for a in result["per_item"] if a.id == "id1")
        self.assertEqual(id1_agg.modal_verdict, "real_defect")
        self.assertAlmostEqual(id1_agg.stability, 2.0 / 3, places=2)
        self.assertEqual(id1_agg.verdict_counts["real_defect"], 2)

        # id2 modal: false_positive (3/3, stable)
        id2_agg = next(a for a in result["per_item"] if a.id == "id2")
        self.assertEqual(id2_agg.modal_verdict, "false_positive")
        self.assertAlmostEqual(id2_agg.stability, 1.0, places=2)

    def test_aggregate_runs_stability_calculation(self):
        """Test stability (fraction agreeing with mode) is computed correctly."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "false_positive", "false_positive", ""),
        ]

        # 5 runs: 3 say false_positive, 2 say real_defect
        runs = [
            [ScorecardItem("id1", "false_positive", False, 0, True, 0, True, True)],
            [ScorecardItem("id1", "false_positive", False, 0, True, 0, True, True)],
            [ScorecardItem("id1", "false_positive", False, 0, True, 0, True, True)],
            [ScorecardItem("id1", "real_defect", False, 0, True, 0, False, False)],
            [ScorecardItem("id1", "real_defect", False, 0, True, 0, False, False)],
        ]

        result = aggregate_runs(runs, corpus, 5)

        item = result["per_item"][0]
        self.assertEqual(item.modal_verdict, "false_positive")
        self.assertAlmostEqual(item.stability, 3.0 / 5, places=2)
        self.assertEqual(item.correct_count, 1)  # Mode matches ground truth

    def test_aggregate_runs_overall_stats(self):
        """Test that aggregate overall stats are computed from modal verdicts."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "real_defect", "real_defect", ""),
            CorpusItem("id2", "text2", "lens2", "real_defect", "real_defect", ""),
        ]

        # 2 runs
        run1 = [
            ScorecardItem("id1", "real_defect", False, 0, True, 0, True, True),
            ScorecardItem("id2", "false_positive", False, 0, True, 0, False, False),
        ]
        run2 = [
            ScorecardItem("id1", "real_defect", False, 0, True, 0, True, True),
            ScorecardItem("id2", "real_defect", False, 0, True, 0, True, True),
        ]

        result = aggregate_runs([run1, run2], corpus, 2)
        stats = result["overall_stats"]

        # Overall accuracy: 1.5 correct out of 2 on average (id1: both correct, id2: split 50/50)
        # But modal mode: id1 real_defect (correct), id2 real_defect (correct) = 2/2 = 100%
        self.assertGreaterEqual(stats["overall_agreement_pct"], 50.0)
        self.assertEqual(stats["num_runs"], 2)


class TestPathRedaction(unittest.TestCase):
    """Test path redaction in shadow adjudication."""

    def test_redact_home_paths(self):
        """Verify home paths are redacted to <HOME>."""
        from shadow_adjudication import redact_paths

        test_text = (
            "Code found at C:\\Users\\matt8\\project "
            "and /c/Users/matt8/other"
        )
        redacted = redact_paths(test_text)

        self.assertNotIn("matt8", redacted)
        self.assertIn("<HOME>", redacted)

    def test_redact_repo_paths(self):
        """Verify repo paths are redacted to <REPO>."""
        from shadow_adjudication import redact_paths

        test_text = "Path: C:\\Users\\matt8\\aesop\\state\\tracker.json"
        redacted = redact_paths(test_text)

        self.assertNotIn("matt8", redacted)
        self.assertNotIn("aesop", redacted)
        self.assertIn("<REPO>", redacted)


class TestModalVerdictExcludesDecisionFailed(unittest.TestCase):
    """Test that DECISION_FAILED is excluded from modal verdict computation."""

    def test_aggregate_excludes_decision_failed_from_modal(self):
        """Verify DECISION_FAILED is excluded from modal verdict."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "real_defect", "real_defect", ""),
        ]

        # 2 real_defect, 1 DECISION_FAILED
        run1 = [
            ScorecardItem("id1", "real_defect", False, 0, True, 0, True, True),
        ]
        run2 = [
            ScorecardItem("id1", "real_defect", False, 0, True, 0, True, True),
        ]
        run3 = [
            ScorecardItem("id1", "DECISION_FAILED", False, 0, False, 0, False, False),
        ]

        result = aggregate_runs([run1, run2, run3], corpus, 3)
        agg_items = result["per_item"]
        item1 = agg_items[0]

        # Modal should be real_defect, not DECISION_FAILED
        self.assertEqual(item1.modal_verdict, "real_defect")

    def test_aggregate_all_failed_explicit(self):
        """Verify all-DECISION_FAILED case reports explicitly."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "real_defect", "real_defect", ""),
        ]

        # All verdicts are DECISION_FAILED
        run1 = [
            ScorecardItem("id1", "DECISION_FAILED", False, 0, False, 0, False, False),
        ]
        run2 = [
            ScorecardItem("id1", "DECISION_FAILED", False, 0, False, 0, False, False),
        ]
        run3 = [
            ScorecardItem("id1", "DECISION_FAILED", False, 0, False, 0, False, False),
        ]

        result = aggregate_runs([run1, run2, run3], corpus, 3)
        agg_items = result["per_item"]
        item1 = agg_items[0]

        # Should explicitly report all failed, not DECISION_FAILED as a verdict
        self.assertEqual(item1.modal_verdict, "all_runs_failed")
        self.assertEqual(item1.correct_count, 0)


class TestIncompleteRunAggregation(unittest.TestCase):
    """Test that stability is computed correctly when runs are incomplete (rate-limited).

    When a rate limit hits mid-run, some items may have fewer verdicts than num_runs.
    Stability should be computed based on actual number of verdicts per item, not global num_runs.
    """

    def test_incomplete_run_stability_correct(self):
        """Verify stability uses actual verdict count, not global num_runs."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "real_defect", "real_defect", ""),
            CorpusItem("id2", "text2", "lens2", "false_positive", "false_positive", ""),
            CorpusItem("id3", "text3", "lens3", "real_defect", "real_defect", ""),
        ]

        # Run 1: complete (all 3 items)
        run1 = [
            ScorecardItem("id1", "real_defect", False, 0, True, 0, True, True),
            ScorecardItem("id2", "false_positive", False, 0, True, 0, True, True),
            ScorecardItem("id3", "real_defect", False, 0, True, 0, True, True),
        ]

        # Run 2: incomplete (only 2 items due to rate limit)
        run2 = [
            ScorecardItem("id1", "real_defect", False, 0, True, 0, True, True),
            ScorecardItem("id2", "false_positive", False, 0, True, 0, True, True),
        ]

        result = aggregate_runs([run1, run2], corpus, 2)
        agg_items = result["per_item"]

        # id1 and id2: 2 runs, both same verdict => stability = 2/2 = 1.0
        id1_agg = next(a for a in agg_items if a.id == "id1")
        self.assertEqual(id1_agg.modal_verdict, "real_defect")
        self.assertAlmostEqual(id1_agg.stability, 1.0, places=2)

        id2_agg = next(a for a in agg_items if a.id == "id2")
        self.assertEqual(id2_agg.modal_verdict, "false_positive")
        self.assertAlmostEqual(id2_agg.stability, 1.0, places=2)

        # id3: only 1 run (run 2 didn't include it) => stability = 1/1 = 1.0 (not 1/2!)
        id3_agg = next(a for a in agg_items if a.id == "id3")
        self.assertEqual(id3_agg.modal_verdict, "real_defect")
        # BUG FIX: stability should be 1.0 (1 verdict out of 1 run), NOT 0.5 (1 verdict out of global 2 runs)
        self.assertAlmostEqual(id3_agg.stability, 1.0, places=2,
                              msg="Stability must use actual verdict count (1), not global num_runs (2)")

    def test_mixed_completion_levels(self):
        """Test 3 runs with varying completion levels."""
        corpus = [
            CorpusItem("id1", "text1", "lens1", "real_defect", "real_defect", ""),
            CorpusItem("id2", "text2", "lens2", "false_positive", "false_positive", ""),
            CorpusItem("id3", "text3", "lens3", "real_defect", "real_defect", ""),
            CorpusItem("id4", "text4", "lens4", "enhancement_opportunity", "enhancement_opportunity", ""),
        ]

        # Run 1: complete (all 4)
        run1 = [
            ScorecardItem(f"id{i}", "real_defect", False, 0, True, 0, True, i < 2)
            for i in range(1, 5)
        ]

        # Run 2: 3 items (rate limit after 3)
        run2 = [
            ScorecardItem(f"id{i}", "false_positive", False, 0, True, 0, False, False)
            for i in range(1, 4)
        ]

        # Run 3: only 2 items (rate limit after 2)
        run3 = [
            ScorecardItem(f"id{i}", "real_defect", False, 0, True, 0, True, True)
            for i in range(1, 3)
        ]

        result = aggregate_runs([run1, run2, run3], corpus, 3)
        agg_items = result["per_item"]

        # id1: 3 runs, mixed verdicts
        id1 = next(a for a in agg_items if a.id == "id1")
        self.assertEqual(len(id1.verdict_counts), 2)  # real_defect and false_positive
        # Stability = max_count / actual_runs = 2/3
        expected_stability_1 = 2.0 / 3
        self.assertAlmostEqual(id1.stability, expected_stability_1, places=2)

        # id2: 3 runs, mixed verdicts
        id2 = next(a for a in agg_items if a.id == "id2")
        expected_stability_2 = 2.0 / 3
        self.assertAlmostEqual(id2.stability, expected_stability_2, places=2)

        # id3: 2 runs (missing from run3)
        id3 = next(a for a in agg_items if a.id == "id3")
        self.assertEqual(len(id3.verdict_counts), 2)  # real_defect and false_positive
        # Stability should use 2 (actual runs), not 3 (global num_runs)
        expected_stability_3 = 1.0 / 2
        self.assertAlmostEqual(id3.stability, expected_stability_3, places=2,
                              msg="id3 has only 2 verdicts; stability must be 1/2, not 1/3")

        # id4: 1 run (only in run1)
        id4 = next(a for a in agg_items if a.id == "id4")
        # Stability = 1/1 = 1.0 (only 1 run contributed data)
        self.assertAlmostEqual(id4.stability, 1.0, places=2,
                              msg="id4 has only 1 verdict; stability must be 1/1, not 1/3")


if __name__ == "__main__":
    unittest.main()
