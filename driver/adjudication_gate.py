#!/usr/bin/env python3
"""Two-tier adjudication gate: challenger + incumbent escalation (swap increment 3).

CONSERVATIVE design: a cheaper challenger model handles adjudication decisions,
but escalates every doubtful call to the incumbent (frontier model) for safety.

Flow:
  1. Call challenger.decide()
  2. If verdict=='DECISION_FAILED', escalate (source: escalated-failed)
  3. If verdict=='undetermined', escalate (source: escalated-undetermined)
  4. If confidence < threshold, escalate (source: escalated-lowconf)
  5. If decision_type not in allowed_decision_types, escalate (source: escalated-disallowed-type)
  6. Otherwise, with probability spot_check_frac, escalate for audit (source: escalated-spotcheck)
  7. Else accept challenger verdict as final

SAFETY INVARIANT:
  The gate's output verdict on any given item is EITHER the challenger's confident
  non-undetermined verdict OR the incumbent's resolvable verdict (not DECISION_FAILED/undetermined).
  It NEVER emits an undetermined/DECISION_FAILED/low-confidence challenger verdict as final.

DEFENSIVE CORRECTNESS (incumbent validation):
  When escalating to the incumbent, the gate validates the incumbent's returned verdict.
  If the incumbent ALSO returned DECISION_FAILED or undetermined (both failed to decide),
  the gate marks this as explicitly unresolved (escalation_unresolved=True) rather than
  silently presenting the incumbent's failure as a confident verdict. The gate passes
  through the incumbent's verdict (it is the ground truth) but flags it as unresolved.
  The gate is only as safe as its incumbent — if both fail, the gate surfaces an
  explicit unresolved terminal, never a fabricated verdict.

DETERMINISM:
  Spot-check decisions are deterministic (seeded) so tests are reproducible.
  The caller can supply a fixed seed or a per-call nonce.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class AdjudicationGate:
    """Two-tier gate: challenger decides, incumbent escalates on doubt.

    Attributes:
        challenger: An OrchestratorDriver instance (cheap backend).
        incumbent_fn: A callable(decision_type, context_pack, schema) -> dict
                     that returns the incumbent's verdict (frontier model).
        escalate_on_undetermined: If True (default), escalate undetermined verdicts
                                 to incumbent. If False (not recommended), accept
                                 undetermined as final (violates safety invariant).
        escalate_confidence_below: Float threshold (0.0-1.0, default 0.70).
                                  If challenger's confidence < threshold, escalate.
        spot_check_frac: Float probability (0.0-1.0, default 0.10) of escalating
                        a confident challenger verdict for audit sampling.
        allowed_decision_types: List of decision types the challenger may decide
                               without escalation (default: all). If empty, all
                               types are allowed. Narrow this to exclude narrative
                               mechanisms the ladder showed weaker models struggle with.
    """

    challenger: Any  # OrchestratorDriver
    incumbent_fn: Callable[[str, Any, Optional[Dict[str, Any]]], Dict[str, Any]]
    escalate_on_undetermined: bool = True
    escalate_confidence_below: float = 0.70
    spot_check_frac: float = 0.10
    allowed_decision_types: List[str] = field(default_factory=list)

    def adjudicate(
        self,
        decision_type: str,
        context_pack: Any,  # ContextPack
        schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Adjudicate using challenger, escalate on doubt, return safe verdict.

        Args:
            decision_type: Name of the decision class (e.g., 'rank_backlog').
            context_pack: ContextPack with file-brain snapshot.
            schema: Optional JSON schema for decision validation.

        Returns:
            Dict with keys:
              - verdict: The final verdict (from challenger or incumbent).
              - evidence: Reasoning for the verdict.
              - confidence: Confidence score (from challenger or incumbent).
              - source: Where the verdict came from:
                  * 'challenger' = challenger confident, allowed, not spot-checked
                  * 'escalated-undetermined' = challenger returned undetermined
                  * 'escalated-lowconf' = challenger confidence below threshold
                  * 'escalated-failed' = challenger returned DECISION_FAILED
                  * 'escalated-disallowed-type' = decision_type not in allowed set
                  * 'escalated-spotcheck' = challenger confident but sampled for audit
              - challenger_verdict: The raw challenger output (always retained for audit).
              - incumbent_verdict: Present only if escalated (the incumbent's verdict).

        SAFETY INVARIANT (enforced by construction):
            The returned verdict is EITHER:
              (a) The challenger's confident (confidence >= threshold) non-undetermined
                  verdict for an allowed decision type (not spot-checked), OR
              (b) The incumbent's verdict (escalated for safety).
            It is NEVER an undetermined/DECISION_FAILED/low-confidence challenger
            verdict as final. Tests assert this holds for every case.
        """
        # Call challenger.
        challenger_result = self.challenger.decide(decision_type, context_pack, schema)

        # Extract confidence from challenger (default to 0.0 if absent).
        # Fail-closed coercions: bool (True == 1 would fake full confidence)
        # and NaN (every comparison is False, silently bypassing the low-conf
        # escalation) are NOT valid confidence values.
        challenger_confidence = challenger_result.get("confidence", 0.0)
        if (
            isinstance(challenger_confidence, bool)
            or not isinstance(challenger_confidence, (int, float))
            or challenger_confidence != challenger_confidence  # NaN
        ):
            challenger_confidence = 0.0

        # Extract verdict (DECISION_FAILED means failure). Normalize for the
        # reserved-terminal checks: case variants ("decision_failed",
        # "UNDETERMINED") and non-string verdicts must not slip past the
        # escalation rules (fail-closed: non-string == failure).
        challenger_verdict = challenger_result.get("verdict")
        if isinstance(challenger_verdict, str):
            _verdict_norm = challenger_verdict.strip().upper()
        else:
            _verdict_norm = "DECISION_FAILED"

        # Rule 1: Challenger failed.
        if _verdict_norm == "DECISION_FAILED":
            incumbent_result = self.incumbent_fn(decision_type, context_pack, schema)
            result_dict = {
                "verdict": incumbent_result.get("verdict"),
                "evidence": incumbent_result.get("evidence", []),
                "confidence": incumbent_result.get("confidence", 0.0),
                "source": "escalated-failed",
                "challenger_verdict": challenger_result,
                "incumbent_verdict": incumbent_result,
            }
            # Defensive correctness: mark as unresolved if incumbent also failed.
            if self._is_verdict_unresolved(incumbent_result):
                result_dict["escalation_unresolved"] = True
            return result_dict

        # Rule 2: Challenger returned undetermined.
        if self.escalate_on_undetermined and _verdict_norm == "UNDETERMINED":
            incumbent_result = self.incumbent_fn(decision_type, context_pack, schema)
            result_dict = {
                "verdict": incumbent_result.get("verdict"),
                "evidence": incumbent_result.get("evidence", []),
                "confidence": incumbent_result.get("confidence", 0.0),
                "source": "escalated-undetermined",
                "challenger_verdict": challenger_result,
                "incumbent_verdict": incumbent_result,
            }
            # Defensive correctness: mark as unresolved if incumbent also failed.
            if self._is_verdict_unresolved(incumbent_result):
                result_dict["escalation_unresolved"] = True
            return result_dict

        # Rule 3: Challenger confidence below threshold.
        if challenger_confidence < self.escalate_confidence_below:
            incumbent_result = self.incumbent_fn(decision_type, context_pack, schema)
            result_dict = {
                "verdict": incumbent_result.get("verdict"),
                "evidence": incumbent_result.get("evidence", []),
                "confidence": incumbent_result.get("confidence", 0.0),
                "source": "escalated-lowconf",
                "challenger_verdict": challenger_result,
                "incumbent_verdict": incumbent_result,
            }
            # Defensive correctness: mark as unresolved if incumbent also failed.
            if self._is_verdict_unresolved(incumbent_result):
                result_dict["escalation_unresolved"] = True
            return result_dict

        # Rule 4: Decision type not allowed (if allowed list is non-empty).
        if (
            self.allowed_decision_types
            and decision_type not in self.allowed_decision_types
        ):
            incumbent_result = self.incumbent_fn(decision_type, context_pack, schema)
            result_dict = {
                "verdict": incumbent_result.get("verdict"),
                "evidence": incumbent_result.get("evidence", []),
                "confidence": incumbent_result.get("confidence", 0.0),
                "source": "escalated-disallowed-type",
                "challenger_verdict": challenger_result,
                "incumbent_verdict": incumbent_result,
            }
            # Defensive correctness: mark as unresolved if incumbent also failed.
            if self._is_verdict_unresolved(incumbent_result):
                result_dict["escalation_unresolved"] = True
            return result_dict

        # Rule 5: Spot-check sample (deterministic, not random).
        if self._should_spot_check(decision_type, context_pack):
            incumbent_result = self.incumbent_fn(decision_type, context_pack, schema)
            result_dict = {
                "verdict": incumbent_result.get("verdict"),
                "evidence": incumbent_result.get("evidence", []),
                "confidence": incumbent_result.get("confidence", 0.0),
                "source": "escalated-spotcheck",
                "challenger_verdict": challenger_result,
                "incumbent_verdict": incumbent_result,
            }
            # Defensive correctness: mark as unresolved if incumbent also failed.
            if self._is_verdict_unresolved(incumbent_result):
                result_dict["escalation_unresolved"] = True
            return result_dict

        # Accept challenger verdict.
        return {
            "verdict": challenger_verdict,
            "evidence": challenger_result.get("evidence", []),
            "confidence": challenger_confidence,
            "source": "challenger",
            "challenger_verdict": challenger_result,
        }

    def _is_verdict_unresolved(self, result: Dict[str, Any]) -> bool:
        """Check if a verdict result is unresolved.

        Unresolved = DECISION_FAILED, undetermined (any case variant), or a
        missing/None/non-string verdict (a malformed incumbent result without
        a 'verdict' key must never be presented as a confident final verdict).

        Args:
            result: A verdict dict with a 'verdict' key.

        Returns:
            True if the verdict is DECISION_FAILED, undetermined, or absent.
        """
        verdict = result.get("verdict")
        if not isinstance(verdict, str):
            # Missing/None/non-string verdict is unresolved (fail-closed);
            # never present it as a resolved incumbent verdict.
            return True
        return verdict.strip().upper() in ("DECISION_FAILED", "UNDETERMINED")

    def _should_spot_check(self, decision_type: str, context_pack: Any) -> bool:
        """Deterministically decide if this call should be spot-checked.

        Spot-check is seeded per ITEM (not per decision_type). Each distinct item
        (context_pack with different content AND different evidence) gets an independent
        draw at the configured fraction. The same item (same content + same evidence)
        always produces the same decision (deterministic, reproducible across runs).
        Different items produce independent decisions, so approximately spot_check_frac
        of all items get sampled.

        FIX (BL2 Finding 1): Fold evidence into the canonical digest. In production,
        content is the SHARED file-brain (identical across all findings in a wave);
        the per-item distinguisher is pack.evidence. Without folding evidence, every
        item in a wave hashes identically → spot-check is 0% or 100% of the WAVE.
        With evidence, spot-check is ~spot_check_frac per ITEM (independent draws).

        Args:
            decision_type: The decision type string.
            context_pack: The context pack, used to build a stable content+evidence digest.

        Returns:
            True if this call should be escalated for audit, False otherwise.
        """
        # Build a stable per-item key from (decision_type + context_pack content + evidence).
        # Extract content digest from the pack (try .content attr, fall back to str).
        canonical = decision_type + "|"
        if hasattr(context_pack, "content") and isinstance(context_pack.content, dict):
            # Sorted JSON of content items for stable serialization.
            try:
                content_text = json.dumps(
                    sorted(context_pack.content.items()), separators=(",", ":")
                )
                canonical += content_text
            except (TypeError, ValueError):
                # If JSON encoding fails, fall back to str.
                canonical += str(context_pack)
        else:
            # No .content attr; use string representation.
            canonical += str(context_pack)

        # FIX (BL2): Fold evidence into the canonical digest so spot-check is per-item.
        canonical += "|"
        if hasattr(context_pack, "evidence"):
            evidence = context_pack.evidence
            if isinstance(evidence, dict):
                # Sorted JSON of evidence for stable serialization.
                try:
                    evidence_text = json.dumps(
                        sorted(evidence.items()), separators=(",", ":")
                    )
                    canonical += evidence_text
                except (TypeError, ValueError):
                    # F10 FIX: If sorting fails (unsortable keys), use repr on the dict itself
                    # (not sorted items). This fallback CANNOT raise from sorted() again.
                    canonical += repr(evidence)
            else:
                # Non-dict evidence: convert to stable string.
                canonical += repr(evidence)
        # If no evidence attr, leave it blank (canonical += "")

        # Hash the canonical key to get a deterministic integer.
        hash_val = int(hashlib.md5(canonical.encode()).hexdigest(), 16)
        # Sample at the configured fraction (0.0-1.0).
        # round() before int(): int(0.29 * 100) == 28 due to float representation,
        # which would silently under-sample the configured fraction.
        return (hash_val % 100) < int(round(self.spot_check_frac * 100))

    def summarize_run(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summarize adjudication run statistics.

        Args:
            results: List of dicts returned by adjudicate() calls.

        Returns:
            Dict with:
              - n: Total number of adjudications.
              - accepted_challenger: Count of verdicts from challenger (source=='challenger').
              - escalated_by_reason: Dict mapping reason strings to counts.
              - spot_check_agreements: Count of escalated-spotcheck where incumbent
                                      agreed with challenger.
              - spot_check_disagreements: Count of escalated-spotcheck where they disagreed.
              - effective_escalation_rate: Fraction of calls that were escalated.
        """
        n = len(results)
        accepted_challenger = 0
        escalated_by_reason = {}
        spot_check_agreements = 0
        spot_check_disagreements = 0

        for result in results:
            source = result.get("source", "unknown")

            if source == "challenger":
                accepted_challenger += 1
            else:
                # Escalated for some reason.
                escalated_by_reason[source] = escalated_by_reason.get(source, 0) + 1

                # Track spot-check agreement.
                if source == "escalated-spotcheck":
                    challenger_verdict = (
                        result.get("challenger_verdict", {}).get("verdict")
                    )
                    incumbent_verdict = (
                        result.get("incumbent_verdict", {}).get("verdict")
                    )
                    if challenger_verdict == incumbent_verdict:
                        spot_check_agreements += 1
                    else:
                        spot_check_disagreements += 1

        escalated_count = n - accepted_challenger
        effective_escalation_rate = (
            escalated_count / n if n > 0 else 0.0
        )

        return {
            "n": n,
            "accepted_challenger": accepted_challenger,
            "escalated_by_reason": escalated_by_reason,
            "spot_check_agreements": spot_check_agreements,
            "spot_check_disagreements": spot_check_disagreements,
            "effective_escalation_rate": effective_escalation_rate,
        }
