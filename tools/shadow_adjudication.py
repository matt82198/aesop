#!/usr/bin/env python3
"""Shadow adjudication wave runner.

Replays a corpus of ground-truth-labeled adjudication decisions through
the OrchestratorDriver seam using a configurable challenger backend
(OpenAI-compatible; --model selects the ladder rung). Zero behavior change
to any live system. Scorecards record the API-reported served model id as
evidence of which brain actually answered.

Blind adjudication: labels are NEVER included in challenger context packs.

CLI: python tools/shadow_adjudication.py --corpus <path> [--offline | --live]
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add driver/ to sys.path.
REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO_ROOT / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from context_pack import build_context_pack, ContextPack  # noqa: E402
from orchestrator_driver import OrchestratorDriver  # noqa: E402
from orchestrator_backend import (  # noqa: E402
    FakeOrchestratorBackend,
    HarnessOrchestratorBackend,
    OpenAICompatibleOrchestratorBackend,
)
from openai_transport import default_openai_transport  # noqa: E402

# Fallback challenger model when neither --model nor seats.orchestrator names one.
DEFAULT_CHALLENGER_MODEL = "gpt-4o-mini"


def build_live_backend(cli_model=None, config_path=None):
    """Build the live orchestrator backend from the seats config (HS-1).

    Resolution:
      - seats.orchestrator (openai-compatible) in aesop.config.json supplies
        model/base_url/api_key_env/is_local for the seat.
      - CLI --model, when given, OVERRIDES the seat's model (seat knobs stay).
      - No configured seat (or harness/claude seat) -> legacy hosted-OpenAI
        default with cli_model or DEFAULT_CHALLENGER_MODEL (behavior
        unchanged for installs without a seats block).

    Offline-safe: no API key is read here.
    """
    from backend_config import (  # noqa: E402 (driver/ on sys.path above)
        build_orchestrator_backend,
        load_backend_config,
    )

    config = load_backend_config(config_path)
    backend = build_orchestrator_backend(config)
    if isinstance(backend, HarnessOrchestratorBackend):
        return OpenAICompatibleOrchestratorBackend(
            model=cli_model or DEFAULT_CHALLENGER_MODEL,
            transport=default_openai_transport,
        )
    if cli_model:
        backend.model = cli_model
    return backend


# ============================================================================
# Path redaction helper (privacy leak prevention)
# ============================================================================


def redact_paths(text: str) -> str:
    """Redact absolute machine paths from text.

    Replaces:
    - C:\\Users\\matt8\\aesop → <REPO> (handles single or double backslashes)
    - /c/Users/matt8/aesop → <REPO>
    - C:\\Users\\matt8 → <HOME> (handles single or double backslashes)
    - /c/Users/matt8 → <HOME>
    - Users/matt8 → <HOME>
    - Users\\matt8 → <HOME>

    Non-path occurrences of 'matt8' are preserved.

    Separators match ANY run of backslashes or forward slashes so single-,
    double-, and repr/JSON re-escaped forms (\\, \\\\, \\\\\\\\ ...) are all
    caught. Reasoning strings often embed repr-of-list text whose paths carry
    2x/4x-escaped separators; a 1-2 backslash pattern misses those.
    """
    # First redact repo-specific paths (longer pattern, must be first)
    # Windows: C:\Users\matt8\aesop at any escape depth, or C:/Users/... form
    text = re.sub(
        r"C:[\\/]+Users[\\/]+matt8[\\/]+aesop",
        "<REPO>",
        text,
        flags=re.IGNORECASE,
    )
    # POSIX: /c/Users/matt8/aesop
    text = re.sub(
        r"/c/Users/matt8/aesop",
        "<REPO>",
        text,
        flags=re.IGNORECASE,
    )

    # Then redact home paths (shorter pattern)
    # Windows: C:\Users\matt8 at any escape depth, or C:/Users/matt8
    text = re.sub(
        r"C:[\\/]+Users[\\/]+matt8",
        "<HOME>",
        text,
        flags=re.IGNORECASE,
    )
    # POSIX: /c/Users/matt8
    text = re.sub(
        r"/c/Users/matt8",
        "<HOME>",
        text,
        flags=re.IGNORECASE,
    )
    # Also handle Users\matt8 / Users\\matt8 / Users/matt8 at any escape depth
    text = re.sub(
        r"Users[\\/]+matt8",
        "<HOME>",
        text,
        flags=re.IGNORECASE,
    )

    return text


def redact_data_structure(obj: Any) -> Any:
    """Recursively redact paths from all string values in a data structure.

    Handles dicts, lists, and strings. Returns a new object with paths redacted.
    """
    if isinstance(obj, str):
        return redact_paths(obj)
    elif isinstance(obj, dict):
        return {k: redact_data_structure(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [redact_data_structure(item) for item in obj]
    else:
        return obj


# ============================================================================
# Enhanced backend wrapper to track served models and tokens
# ============================================================================


class ShadowAdjudicationBackend:
    """Wrapper around OrchestratorBackend to track served models and tokens.

    Decorates the real backend to record which model the API reports as having
    served each decision (evidence of which brain answered).
    """

    def __init__(
        self,
        backend: "OrchestratorBackend",
        model: str = "gpt-4o-mini",
    ):
        self.backend = backend
        self.model = model
        self.tokens_spent = 0
        self.served_models: List[str] = []

    def decide_call(self, prompt: str, *, schema=None) -> str:
        """Call the backend and record telemetry."""
        response_text = self.backend.decide_call(prompt, schema=schema)

        # Try to extract served model from the response if it's JSON.
        try:
            response_json = json.loads(response_text)
            if isinstance(response_json, dict) and "model" in response_json:
                served = response_json.get("model")
                if served:
                    self.served_models.append(str(served))
        except (json.JSONDecodeError, ValueError):
            pass

        return response_text


# ============================================================================
# Corpus and Scorecard
# ============================================================================


@dataclass
class CorpusItem:
    """One adjudication item from the corpus."""

    id: str
    finding_text: str
    source_lens: str
    incumbent_verdict: str
    ground_truth: str
    gt_note: str
    evidence: list = None

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = []


@dataclass
class ScorecardItem:
    """Scorecard entry for one adjudication."""

    id: str
    challenger_classification: str
    challenger_actionable: bool
    evidence_count: int
    schema_valid: bool
    retries_used: int
    agreement_with_incumbent: bool
    correct_vs_ground_truth: bool
    challenger_confidence: float = 0.0
    challenger_evidence: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class AggregatedScorecardItem:
    """Per-item results aggregated across multiple runs."""

    id: str
    ground_truth: str
    verdict_counts: Dict[str, int] = field(default_factory=dict)  # verdict -> count across runs
    stability: float = 0.0  # fraction of runs agreeing with mode
    modal_verdict: str = "undetermined"  # most common verdict across runs
    correct_count: int = 0  # runs where modal_verdict == ground_truth
    num_runs: int = 1


def load_corpus(corpus_path: str) -> List[CorpusItem]:
    """Load corpus from jsonl file (now with optional evidence field)."""
    items = []
    with open(corpus_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                labels = obj.get("labels", {})
                item = CorpusItem(
                    id=obj["id"],
                    finding_text=obj["finding_text"],
                    source_lens=obj["source_lens"],
                    incumbent_verdict=labels.get("incumbent_verdict", "unknown"),
                    ground_truth=labels.get("ground_truth", "unknown"),
                    gt_note=labels.get("gt_note", ""),
                    evidence=obj.get("evidence", []),
                )
                items.append(item)
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error parsing corpus line {line_num}: {e}", file=sys.stderr)
                raise
    return items


def build_finding_context_pack(
    item: CorpusItem, repo_root: str, conductor_root: str, enriched: bool = False, evidence_mode: str = "full"
) -> ContextPack:
    """Build a context pack for adjudication (BLIND: no labels).

    Args:
        item: Corpus item with finding_text and source_lens.
        repo_root: Repo root path.
        conductor_root: Conductor root path.
        enriched: If True, include evidence from the corpus item (increment 2.5).
        evidence_mode: How much evidence to surface when enriched=True:
          - 'full': all 3 parts (mechanism + behavior + impact/conclusion)
          - 'mechanism': first 2 parts only (mechanism + behavior, no conclusions)
          - Mechanism mode prevents answer-leakage from [3] impact clauses.

    Returns:
        ContextPack with finding_text + source framing (no labels).
        If enriched, also includes evidence section (sliced per evidence_mode).
    """
    # Build sources dict: finding text + source lens.
    # This is the BLIND framing the challenger sees.
    sources = {
        "brief:finding": None,  # Will be constructed inline.
    }

    # Construct the brief as finding_text + source framing.
    finding_brief = f"""FINDING: {item.finding_text}

Source lens: {item.source_lens}

Please adjudicate this finding. Classify it as one of:
- real_defect: actionable code/process defect needing a fix
- false_positive: not actually a defect (e.g., misunderstood, refuted by evidence)
- enhancement_opportunity: nice-to-have, not blocking
- undetermined: insufficient information to classify

Provide evidence supporting your classification."""

    # Override the brief source with the constructed text.
    # We'll manually pass this to the OrchestratorDriver.
    pack = ContextPack(
        decision_type="adjudicate_finding",
        sources_requested=("finding",),
        content={"finding": finding_brief},
        total_size_bytes=len(finding_brief.encode("utf-8")),
    )
    pack.manifest.append(
        {
            "source": "finding",
            "included": True,
            "truncated": False,
            "truncation_reason": None,
            "size_bytes": len(finding_brief.encode("utf-8")),
        }
    )

    # Add evidence if enriched mode is enabled (increment 2.5).
    if enriched and item.evidence:
        # Slice evidence based on mode: mechanism mode drops the [3] conclusion clause
        # to prevent answer-leakage (confound fix).
        evidence_list = item.evidence
        if evidence_mode == "mechanism":
            # Mechanism mode: keep only first 2 items (mechanism + behavior)
            evidence_list = item.evidence[:2]
        elif evidence_mode != "full":
            raise ValueError(f"Unknown evidence_mode: {evidence_mode}")

        # Convert evidence list to a dict for build_context_pack
        evidence_dict = {}
        for idx, evidence_item in enumerate(evidence_list):
            evidence_dict[f"evidence_{idx}"] = evidence_item

        # Rebuild pack with evidence
        pack_with_evidence = build_context_pack(
            decision_type="adjudicate_finding",
            sources=sources,
            repo_root=repo_root,
            conductor_root=conductor_root,
            evidence=evidence_dict,
        )
        return pack_with_evidence

    return pack


def adjudicate_one_finding(
    driver: OrchestratorDriver,
    item: CorpusItem,
    repo_root: str,
    conductor_root: str,
    schema: Optional[Dict[str, Any]],
    enriched: bool = False,
    evidence_mode: str = "full",
) -> ScorecardItem:
    """Run one adjudication decision through the driver."""
    try:
        # Build context pack (BLIND; optionally enriched with evidence).
        pack = build_finding_context_pack(item, repo_root, conductor_root, enriched=enriched, evidence_mode=evidence_mode)

        # Verify labels never reach the pack (unit test for blind adjudication).
        pack_text = json.dumps(pack.content)
        for label_key in ["incumbent_verdict", "ground_truth", "gt_note"]:
            if label_key in pack_text:
                print(
                    f"ERROR: Label '{label_key}' found in context pack for {item.id}",
                    file=sys.stderr,
                )
                raise ValueError(f"Blind adjudication violated: {label_key} in pack")

        # Call the driver (context pack is now passed through OrchestratorDriver.decide()).
        decision = driver.decide("adjudicate_finding", pack, schema=schema)

        # Parse decision.
        verdict_obj = decision.get("verdict", {})
        if isinstance(verdict_obj, dict):
            classification = verdict_obj.get("classification", "undetermined")
            actionable = verdict_obj.get("actionable", False)
        else:
            classification = str(verdict_obj)
            actionable = False

        evidence = decision.get("evidence", [])
        evidence_count = len(evidence) if isinstance(evidence, list) else 0

        retries = decision.get("retry_count", 0)
        schema_valid = decision.get("schema_validated", False)
        confidence = decision.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)):
            confidence = 0.0

        # If decision failed, log it for debugging
        if "DECISION_FAILED" in str(decision.get("verdict", "")):
            print(
                f"    [FAILED] {item.id}: {decision.get('evidence', 'no error message')}",
                file=sys.stderr,
            )

        # Map incumbent verdict to schema enum.
        incumbent_enum = item.incumbent_verdict.lower()
        challenger_enum = classification.lower()
        agreement = incumbent_enum == challenger_enum

        # Correct vs ground truth.
        ground_truth_enum = item.ground_truth.lower()
        correct = challenger_enum == ground_truth_enum

        return ScorecardItem(
            id=item.id,
            challenger_classification=classification,
            challenger_actionable=actionable,
            evidence_count=evidence_count,
            schema_valid=schema_valid,
            retries_used=retries,
            agreement_with_incumbent=agreement,
            correct_vs_ground_truth=correct,
            challenger_confidence=confidence,
            challenger_evidence=evidence if isinstance(evidence, list) else [],
        )

    except Exception as e:
        print(f"Error adjudicating {item.id}: {e}", file=sys.stderr)
        return ScorecardItem(
            id=item.id,
            challenger_classification="DECISION_FAILED",
            challenger_actionable=False,
            evidence_count=0,
            schema_valid=False,
            retries_used=0,
            agreement_with_incumbent=False,
            correct_vs_ground_truth=False,
        )


def compute_scorecard_stats(
    scorecard: List[ScorecardItem], corpus: List[CorpusItem]
) -> Dict[str, Any]:
    """Compute aggregate statistics."""
    if not scorecard:
        return {}

    # Agreement rates.
    total_agreement = sum(1 for s in scorecard if s.agreement_with_incumbent)
    overall_agreement_pct = (total_agreement / len(scorecard)) * 100 if scorecard else 0

    # Real defect subset (ground_truth == real_defect).
    real_defect_items = [
        (c, s)
        for c, s in zip(corpus, scorecard)
        if c.ground_truth.lower() == "real_defect"
    ]
    real_defect_agreement = sum(1 for _, s in real_defect_items if s.correct_vs_ground_truth)
    real_defect_pct = (
        (real_defect_agreement / len(real_defect_items)) * 100
        if real_defect_items
        else 0
    )

    # False positive subset (ground_truth == false_positive).
    false_positive_items = [
        (c, s)
        for c, s in zip(corpus, scorecard)
        if c.ground_truth.lower() == "false_positive"
    ]
    false_positive_agreement = sum(
        1 for _, s in false_positive_items if s.correct_vs_ground_truth
    )
    false_positive_pct = (
        (false_positive_agreement / len(false_positive_items)) * 100
        if false_positive_items
        else 0
    )

    # Rubber-stamp test: items 9 (whitelist-gate-weakening) and 14 (regression-ui-suite).
    rubber_stamp_items = [s for s in scorecard if s.id in ["whitelist-gate-weakening", "regression-ui-suite"]]
    rubber_stamp_refutations = sum(
        1 for s in rubber_stamp_items if s.challenger_classification == "false_positive"
    )

    # Schema validity rate.
    schema_valid_count = sum(1 for s in scorecard if s.schema_valid)
    schema_valid_pct = (schema_valid_count / len(scorecard)) * 100 if scorecard else 0

    # DECISION_FAILED count.
    decision_failed_count = sum(
        1 for s in scorecard if s.challenger_classification == "DECISION_FAILED"
    )

    return {
        "overall_agreement_pct": round(overall_agreement_pct, 1),
        "real_defect_agreement_pct": round(real_defect_pct, 1),
        "false_positive_agreement_pct": round(false_positive_pct, 1),
        "rubber_stamp_refutations_count": rubber_stamp_refutations,
        "schema_valid_pct": round(schema_valid_pct, 1),
        "decision_failed_count": decision_failed_count,
        "total_items": len(scorecard),
    }


def aggregate_runs(
    all_run_scorecards: List[List[ScorecardItem]], corpus: List[CorpusItem], num_runs: int
) -> Dict[str, Any]:
    """Aggregate scorecard results across multiple runs.

    Args:
        all_run_scorecards: List of scorecards, one per run
        corpus: The corpus items
        num_runs: Number of runs (global maximum; some items may have fewer)

    Returns:
        Dict with:
        - per_item: List[AggregatedScorecardItem] with stability data
        - overall_stats: Aggregated metrics across all runs
    """
    # Group verdicts by item id across runs
    item_verdicts = {}  # id -> List[verdict]
    for corpus_item in corpus:
        item_verdicts[corpus_item.id] = []

    for run_scorecard in all_run_scorecards:
        for scorecard_item in run_scorecard:
            if scorecard_item.id in item_verdicts:
                item_verdicts[scorecard_item.id].append(
                    scorecard_item.challenger_classification
                )

    # Compute per-item aggregations
    aggregated_items = []
    for corpus_item in corpus:
        verdicts = item_verdicts.get(corpus_item.id, [])

        # Count verdicts
        verdict_counts = {}
        for v in verdicts:
            verdict_counts[v] = verdict_counts.get(v, 0) + 1

        # Modal verdict (most frequent, excluding DECISION_FAILED)
        valid_verdicts = {
            v: count for v, count in verdict_counts.items() if v != "DECISION_FAILED"
        }

        # Use actual number of verdicts for this item (handles incomplete runs)
        actual_runs_for_item = len(verdicts)

        if valid_verdicts:
            modal_verdict = max(valid_verdicts, key=valid_verdicts.get)
            modal_count = valid_verdicts[modal_verdict]
            stability = modal_count / actual_runs_for_item if actual_runs_for_item > 0 else 0.0
            correct_count = 1 if modal_verdict.lower() == corpus_item.ground_truth.lower() else 0
        elif verdict_counts:
            # All verdicts were DECISION_FAILED
            modal_verdict = "all_runs_failed"
            modal_count = 0
            stability = 0.0
            correct_count = 0
        else:
            modal_verdict = "undetermined"
            modal_count = 0
            stability = 0.0
            correct_count = 0

        agg_item = AggregatedScorecardItem(
            id=corpus_item.id,
            ground_truth=corpus_item.ground_truth,
            verdict_counts=verdict_counts,
            stability=stability,
            modal_verdict=modal_verdict,
            correct_count=correct_count,
            num_runs=num_runs,
        )
        aggregated_items.append(agg_item)

    # Compute overall stats across all runs
    all_scores_flat = []
    for run_scorecard in all_run_scorecards:
        all_scores_flat.extend(run_scorecard)

    # Create a single-run scorecard view for stats computation
    # (treat aggregated modal verdicts as the scorecard)
    synthetic_scorecard = []
    for agg in aggregated_items:
        synthetic_item = ScorecardItem(
            id=agg.id,
            challenger_classification=agg.modal_verdict,
            challenger_actionable=False,
            evidence_count=0,
            schema_valid=True,
            retries_used=0,
            agreement_with_incumbent=agg.modal_verdict.lower() == corpus[
                next(i for i, c in enumerate(corpus) if c.id == agg.id)
            ].incumbent_verdict.lower(),
            correct_vs_ground_truth=agg.correct_count == 1,
        )
        synthetic_scorecard.append(synthetic_item)

    overall_stats = compute_scorecard_stats(synthetic_scorecard, corpus)
    overall_stats["num_runs"] = num_runs

    return {
        "per_item": aggregated_items,
        "overall_stats": overall_stats,
    }


def write_scorecard_json(
    scorecard: List[ScorecardItem],
    stats: Dict[str, Any],
    output_path: str,
) -> None:
    """Write full scorecard as JSON."""
    items = [
        {
            "id": s.id,
            "challenger_classification": s.challenger_classification,
            "challenger_actionable": s.challenger_actionable,
            "evidence_count": s.evidence_count,
            "schema_valid": s.schema_valid,
            "retries_used": s.retries_used,
            "agreement_with_incumbent": s.agreement_with_incumbent,
            "correct_vs_ground_truth": s.correct_vs_ground_truth,
            "challenger_confidence": s.challenger_confidence,
        }
        for s in scorecard
    ]

    data = {"statistics": stats, "items": items}

    # Redact paths before writing
    data = redact_data_structure(data)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)

    print(f"Wrote scorecard JSON: {output_path}")


def write_scorecard_md(
    scorecard: List[ScorecardItem],
    corpus: List[CorpusItem],
    stats: Dict[str, Any],
    output_path: str,
    model: str = "gpt-4o-mini",
    aggregated_data: Optional[Dict[str, Any]] = None,
) -> None:
    """Write human-readable scorecard as Markdown.

    Args:
        aggregated_data: If provided, includes stability table for repeated runs (increment 2.6)
    """
    num_runs = aggregated_data["overall_stats"].get("num_runs", 1) if aggregated_data else 1
    lines = [
        "# Shadow Adjudication Wave — Scorecard Report",
        "",
        "**Date**: 2026-07-24",
        f"**Challenger Model**: {model} (OpenAI-compatible)",
        f"**Corpus Size**: 16 items",
        f"**Runs**: {num_runs} (increment 2.6: verdict-neutral corpus)",
        "",
        "## Aggregate Statistics",
        "",
        f"- **Overall Agreement (vs incumbent)**: {stats.get('overall_agreement_pct', 0):.1f}%",
        f"- **Real Defect Subset Agreement**: {stats.get('real_defect_agreement_pct', 0):.1f}%",
        f"- **False Positive Subset Agreement**: {stats.get('false_positive_agreement_pct', 0):.1f}%",
        f"- **Rubber-Stamp Refutations** (items 9, 14 correctly classified as false_positive): {stats.get('rubber_stamp_refutations_count', 0)}/2",
        f"- **Schema Validity Rate**: {stats.get('schema_valid_pct', 0):.1f}%",
        f"- **DECISION_FAILED Count**: {stats.get('decision_failed_count', 0)}",
        "",
    ]

    # Add stability table for N>1 runs (items 9 and 13)
    if aggregated_data and num_runs > 1:
        lines.extend([
            "## Narrative-Refusal Stability (Items 9, 13)",
            "",
            "**Item 9 (whitelist-gate-weakening, gt=false_positive)**:",
            "",
        ])
        item_9 = next((i for i in aggregated_data["per_item"] if i.id == "whitelist-gate-weakening"), None)
        if item_9:
            lines.append(f"| Verdict | Runs | Stability |")
            lines.append(f"|---|---|---|")
            for verdict, count in sorted(item_9.verdict_counts.items()):
                stability_pct = (count / num_runs) * 100
                lines.append(f"| {verdict} | {count}/{num_runs} | {stability_pct:.0f}% |")
            lines.append("")
            lines.append(f"**Modal verdict**: {item_9.modal_verdict} ({item_9.verdict_counts.get(item_9.modal_verdict, 0)}/{num_runs} runs)")
            lines.append("")

        lines.append("**Item 13 (fixreview-backtick-test, gt=false_positive)**:")
        lines.append("")
        item_13 = next((i for i in aggregated_data["per_item"] if i.id == "fixreview-backtick-test"), None)
        if item_13:
            lines.append(f"| Verdict | Runs | Stability |")
            lines.append(f"|---|---|---|")
            for verdict, count in sorted(item_13.verdict_counts.items()):
                stability_pct = (count / num_runs) * 100
                lines.append(f"| {verdict} | {count}/{num_runs} | {stability_pct:.0f}% |")
            lines.append("")
            lines.append(f"**Modal verdict**: {item_13.modal_verdict} ({item_13.verdict_counts.get(item_13.modal_verdict, 0)}/{num_runs} runs)")
            lines.append("")

    lines.extend([
        "## Success Bar Results",
        "",
    ])

    # Check success criteria.
    real_defect_pct = stats.get("real_defect_agreement_pct", 0)
    rubber_stamp = stats.get("rubber_stamp_refutations_count", 0)
    schema_pct = stats.get("schema_valid_pct", 0)

    lines.append(
        f"- >=80% agreement on gt=real_defect items: "
        f"**{'PASS' if real_defect_pct >= 80 else 'FAIL'}** ({real_defect_pct:.1f}%)"
    )
    lines.append(
        f"- >=1 of items {{9, 14}} classified false_positive: "
        f"**{'PASS' if rubber_stamp >= 1 else 'FAIL'}** ({rubber_stamp}/2)"
    )
    lines.append(
        f"- >=90% schema-valid without retry exhaustion: "
        f"**{'PASS' if schema_pct >= 90 else 'FAIL'}** ({schema_pct:.1f}%)"
    )

    lines.extend(
        [
            "",
            "## Item-by-Item Results",
            "",
            "| ID | Challenger | Ground Truth | Correct | Confidence | Schema Valid |",
            "|---|---|---|---|---|---|",
        ]
    )

    for corp, score in zip(corpus, scorecard):
        correct = "✓" if score.correct_vs_ground_truth else "✗"
        schema_ok = "✓" if score.schema_valid else "✗"
        lines.append(
            f"| {score.id} | {score.challenger_classification} | {corp.ground_truth} | {correct} | {score.challenger_confidence:.2f} | {schema_ok} |"
        )

    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- **Corpus Size**: N=16 (single replay, not statistically comprehensive)",
            "- **Blind but Authored**: Challenger sees only finding_text + source_lens, but corpus was authored by the incumbent (potential selection bias)",
            "- **Single Run**: No repeated trials; variance not measured",
            "- **Real-World Drift**: Actual adjudication may differ on live production findings",
        ]
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Wrote scorecard markdown: {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Shadow adjudication wave: replay corpus through OrchestratorDriver"
    )
    parser.add_argument(
        "--corpus",
        required=True,
        help="Path to corpus jsonl file",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run offline with FakeTransport (for testing)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run live with real OpenAI API (requires OPENAI_API_KEY env var)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Challenger model id for the ladder; OVERRIDES the configured "
            "seats.orchestrator model (default: seat model, else gpt-4o-mini)"
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to aesop.config.json for seats.orchestrator (default: ./aesop.config.json)",
    )
    parser.add_argument(
        "--enriched",
        action="store_true",
        default=False,
        help="Use enriched context packs with evidence (increment 2.5; default: off for reproducibility)",
    )
    parser.add_argument(
        "--evidence-mode",
        choices=["full", "mechanism"],
        default="full",
        help="How much evidence to surface when --enriched is used: 'full' (all 3 parts, may leak answers), 'mechanism' (first 2 parts only, no conclusions)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of times to repeat the entire corpus (increment 2.6: N>=5 for stability; default: 1)",
    )
    parser.add_argument(
        "--out-tag",
        default=None,
        help="Results filename tag (default: derived from --model and --repeat); rung files never overwrite each other",
    )

    args = parser.parse_args()

    # Validate corpus path.
    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"Error: corpus file not found: {corpus_path}", file=sys.stderr)
        sys.exit(1)

    # Load corpus.
    try:
        corpus = load_corpus(str(corpus_path))
        print(f"Loaded {len(corpus)} corpus items")
    except Exception as e:
        print(f"Error loading corpus: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate item count.
    if len(corpus) != 16:
        print(f"Error: corpus must have exactly 16 items, got {len(corpus)}", file=sys.stderr)
        sys.exit(1)

    # Set up driver and backend.
    repo_root = REPO_ROOT
    conductor_root = Path.home() / "conductor3"

    # Load schema.
    schema_path = DRIVER_DIR / "decisions" / "adjudicate_finding.schema.json"
    schema = None
    if schema_path.exists():
        try:
            with open(schema_path, encoding="utf-8") as f:
                schema = json.load(f)
        except Exception as e:
            print(f"Warning: failed to load schema: {e}", file=sys.stderr)

    if args.offline:
        print("Running in OFFLINE mode (FakeOrchestratorBackend)")
        if args.model is None:
            args.model = DEFAULT_CHALLENGER_MODEL
        # Offline mode: use canned responses for testing.
        backend = FakeOrchestratorBackend(
            canned_responses=[
                {
                    "verdict": "real_defect",
                    "evidence": "Test response",
                    "confidence": 0.95,
                }
                for _ in range(16)
            ]
        )
    elif args.live:
        print("Running in LIVE mode (OpenAI API)")
        # HS-1: orchestrator seat from config; CLI --model overrides.
        try:
            real_backend = build_live_backend(
                cli_model=args.model, config_path=args.config
            )
        except (TypeError, ValueError) as e:
            print(f"Error: invalid aesop.config.json: {e}", file=sys.stderr)
            sys.exit(1)
        args.model = real_backend.model
        # Check for the seat's API key at runtime (local seats need none).
        key_env = getattr(real_backend, "api_key_env", "OPENAI_API_KEY")
        if not getattr(real_backend, "is_local", False) and not os.environ.get(key_env):
            print(
                f"Error: {key_env} environment variable not set",
                file=sys.stderr,
            )
            sys.exit(1)
        backend = ShadowAdjudicationBackend(real_backend, model=args.model)
    else:
        print("Error: specify --offline or --live", file=sys.stderr)
        sys.exit(1)

    # Create OrchestratorDriver with the new backend.
    driver = OrchestratorDriver(backend, schema_dir=str(DRIVER_DIR), max_retries=2)

    # Run adjudications (with --repeat support).
    all_run_scorecards = []
    api_call_count = 0
    max_calls_per_run = 16  # 16 items per corpus
    max_calls_total = args.repeat * max_calls_per_run  # N*16 for N runs

    print(f"Running {args.repeat} iteration(s) of the corpus (max {max_calls_total} API calls)")

    for run_num in range(args.repeat):
        print(f"\n--- Run {run_num + 1}/{args.repeat} ---")
        run_scorecard = []

        for item in corpus:
            if api_call_count >= max_calls_total:
                print(
                    f"Reached max API calls ({max_calls_total}). Stopping adjudication.",
                    file=sys.stderr,
                )
                break

            result = adjudicate_one_finding(
                driver, item, str(repo_root), str(conductor_root), schema, enriched=args.enriched, evidence_mode=args.evidence_mode
            )
            run_scorecard.append(result)
            api_call_count += 1

            print(f"  [{api_call_count:2d}] {item.id}: {result.challenger_classification}")

        all_run_scorecards.append(run_scorecard)

    # Use the last run's scorecard for backward compatibility stats
    scorecard = all_run_scorecards[-1] if all_run_scorecards else []

    # Compute stats for the last run.
    stats = compute_scorecard_stats(scorecard, corpus[: len(scorecard)])
    stats["challenger_model_requested"] = args.model
    stats["challenger_model"] = args.model
    # Served-model receipts: what the API says actually answered each call.
    served = sorted(set(getattr(backend, "served_models", [])))
    stats["served_models"] = served
    # Check for temperature fallback (on real backend).
    temperature_omitted = False
    if hasattr(backend, "backend") and hasattr(backend.backend, "omit_temperature"):
        temperature_omitted = bool(backend.backend.omit_temperature)
    stats["temperature_omitted"] = temperature_omitted
    print(f"\nServed models (API-reported): {served or 'NONE RECORDED (offline mode?)'}")
    if temperature_omitted:
        print("PARITY NOTE: model rejected temperature=0; ran at API default (recorded in scorecard)")

    # Aggregate across runs if N > 1 (increment 2.6)
    aggregated_data = None
    if args.repeat > 1:
        aggregated_data = aggregate_runs(all_run_scorecards, corpus, args.repeat)
        print(f"\nAggregated {args.repeat} runs:")
        print(f"  Per-item modal verdicts computed")
        print(f"  Stability (mode agreement): per-item")

    # Write outputs.
    results_dir = REPO_ROOT / "bench" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Per-rung output files: clean naming scheme
    # Format: shadow-adjudication-neutral-<date>-<model>[_suffix].json
    if args.out_tag:
        # Custom tag: use as-is (user provides full model tag)
        model_tag = args.out_tag
    else:
        # Auto-generate model tag from model id (clean)
        model_clean = args.model.replace("/", "_").lower()
        model_tag = model_clean

    # Optional suffixes (use underscores for clarity)
    repeat_suffix = f"_repeat{args.repeat}" if args.repeat > 1 else ""
    enriched_suffix = "_enriched" if args.enriched else ""
    full_tag = model_tag + repeat_suffix + enriched_suffix

    # Date: use 2026-07-24 for repeated runs (increment 2.6), else 2026-07-23
    date_tag = "2026-07-24" if args.repeat > 1 else "2026-07-23"

    json_path = results_dir / f"shadow-adjudication-neutral-{date_tag}-{full_tag}.json"
    md_path = results_dir / f"shadow-adjudication-neutral-{date_tag}-{full_tag}.md"

    write_scorecard_json(scorecard, stats, str(json_path))
    write_scorecard_md(scorecard, corpus[: len(scorecard)], stats, str(md_path), model=args.model, aggregated_data=aggregated_data)

    # Print summary.
    print("")
    print("=" * 60)
    print("SHADOW ADJUDICATION WAVE — SUMMARY")
    print("=" * 60)
    print(f"Overall Agreement: {stats.get('overall_agreement_pct', 0):.1f}%")
    print(
        f"Real Defect Accuracy: {stats.get('real_defect_agreement_pct', 0):.1f}%"
    )
    print(
        f"False Positive Accuracy: {stats.get('false_positive_agreement_pct', 0):.1f}%"
    )
    print(
        f"Rubber-Stamp Refutations: {stats.get('rubber_stamp_refutations_count', 0)}/2"
    )
    print(f"Schema Validity: {stats.get('schema_valid_pct', 0):.1f}%")
    print(f"DECISION_FAILED: {stats.get('decision_failed_count', 0)}")
    if args.repeat > 1:
        print(f"Runs completed: {args.repeat}")
    print("=" * 60)

    # Get tokens spent (if available from wrapped backend).
    tokens = None
    if hasattr(backend, "tokens_spent"):
        tokens = backend.tokens_spent
    if tokens:
        print(f"Total tokens spent: {tokens}")

    print("")
    print(f"Results written to:")
    print(f"  {json_path}")
    print(f"  {md_path}")


if __name__ == "__main__":
    main()
