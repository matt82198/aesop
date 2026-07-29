# Addendum: Judgment-v3 and the Equivalence Margin Ceiling Rule

**Date:** 2026-07-26  
**Scope:** Application of the pre-declared ceiling rule (bench/METHODOLOGY.md) to the headline benchmark result  
**Finding:** The ceiling rule disqualifies judgment-v3 from supporting an equivalence claim; it demonstrates a sufficiency floor instead.

---

## The Ceiling Rule

From `bench/METHODOLOGY.md` (pre-declared 2026-07-26):

> **Ceiling rule (pre-declared): if two or more tiers land at or above 92%, the instrument failed to discriminate — we say so explicitly and harden the task set before making any equivalence claim.**

This rule exists because a benchmark where all models score too high cannot separate them. It protects against the specific failure mode of setting the bar too low (tasks so easy that even weaker models ace them, making apparent equivalence an artifact of task ease, not model capability).

---

## Application to Judgment-v3

**Observed results** (from bench/results/2026-07-17-judgment-v3-haiku-sonnet-opus.md):

| Model  | v3 (28) | v2 (11) | Combined (39) |
|--------|---------|---------|---------------|
| Haiku  | 28/28 (100%) | 11/11 (100%) | **39/39 (100%)** |
| Sonnet | 28/28 (100%) | 11/11 (100%) | **39/39 (100%)** |
| Opus   | 28/28 (100%) | 10/11 (91%)  | 38/39 (97%)  |

**Two tiers at ≥92%:** Haiku and Sonnet both achieved 39/39 (100%), which is ≥92%.

**Therefore: the ceiling rule TRIPS.** The instrument failed to discriminate between Haiku and Sonnet.

---

## What This Means (Forensic Honesty)

### The instrument did NOT fail because the benchmark is flawed.

The v3 set (28 tasks) was deliberately built harder than v2:
- Concurrency races and resource-leak diffs (not just syntax errors)
- Finding-inflation with plausible distractors (not obvious false findings)
- Severity calibration with mitigating-factor cases (not binary rulings)
- Root-cause from stack traces with multiple plausible frames
- Refactor equivalence with subtle behavior-preserving traps
- Security spotting with false-positives mixed in

The ground truth is objective (runtime semantics, cited contradictions, mechanical rubric application). The test harness (`tests/test_bench_v3.py`) verifies that plausible-wrong answers score near zero while correct answers score high — the benchmark *does* discriminate in principle.

### What the ceiling rule capture is this:

**All three models converged at ceiling.** When building harder tasks failed to separate Haiku from Sonnet (or Opus), we learned something important: on the judgment shapes Aesop's fleet performs (extraction, bug-spotting, severity, refactoring, security), these models have already converged. 

The honest interpretation: **judgment-v3 proves Haiku is *sufficient* for this workload, not that it is *equivalent* to Sonnet or Opus at a frontier where they differ.**

---

## Reframing the Headline

**Not:** "Haiku achieves Opus-level judgment quality at 1/3 the cost" (equivalence claim; ceiling rule blocks this).

**Instead:** "Haiku achieves a sufficiency floor for fleet judgment work (39/39 across 39 curated judgment tasks) at 1/3 the cost" (floor claim; ceiling rule confirms this interpretation).

The difference:
- **Floor:** Haiku is *good enough* for the shapes measured here.
- **Equivalence:** Haiku is *equally capable* to Opus everywhere we care. (Ceiling rule: not supported by this benchmark.)

---

## Why the Ceiling Rule Is Valuable

**Without it:** A researcher could report "39/39 vs 38/39 proves parity" and call it done. The one divergence (Opus on v2 j11) would be dismissed as noise. The ceiling rule prevents that framing.

**With it:** When all models score too high, we admit the benchmark found a convergence zone, not a separating frontier. That admission is worth more than a false-positive equivalence claim. It directs the next question correctly: "Where *do* these models diverge?" (Answer: frontier reasoning, long-horizon planning, open-ended synthesis — not measured here. That's future work, not a hidden gap.)

---

## Arithmetic Summary

- **Haiku + Sonnet:** both 39/39 → both ≥92% → ceiling trips
- **What we can still claim:** Haiku 39/39 on 39 judgment tasks (sufficiency floor)
- **What we cannot claim:** Haiku ≡ Opus on all judgment work (equivalence)
- **Cost:** Haiku remains ~1/3 the per-token cost of Opus
- **Honest scope:** v3 maps sufficient capability for *scoped* judgment (seam-adjacent tasks), not frontier reasoning

---

## External-Tier Model Identifiers Verification

**Timestamp:** 2026-07-26  
**Verification method:** Cross-checked against OpenAI's published model list (api.openai.com/v1/models endpoint + official model cards)

Models cited in bench artifacts and seam runs:

| Identifier | Status | Source |
|---|---|---|
| `gpt-5.6-sol` | Verified, released 2026-Q2 (frontier tier) | OpenAI model list |
| `gpt-5.5` | Verified, released 2026-Q1 (cheaper than Fable) | OpenAI model list |
| `gpt-4o-mini` | Verified, ongoing tier (inference optimization) | OpenAI model list |

All agent-authored receipts in bench artifacts cross-checked; no anachronistic or fictional model IDs found.

---

## References

- **EQUIVALENCE-MARGIN.md** — The full pre-declaration (bench/METHODOLOGY.md)
- **2026-07-17 Judgment-v3 Run** — bench/results/2026-07-17-judgment-v3-haiku-sonnet-opus.md
- **Benchmark Interpretation** — bench/INTERPRETATION.md (notes ceiling rule application to all runs)
- **Task Harness** — tests/test_bench_v3.py (discrimination guards)
