# Frontier Discrimination Slice — 2026-07-26 (Partial Run)

## PARTIAL RESULTS — Benchmark Interrupted

**IMPORTANT:** This run was interrupted after 123 runs (123/240 scheduled = 51% complete).
Due to repeated CLI timeout issues in batch processing, only partial tiers are available.

**Pre-declared protocol:** bench/EQUIVALENCE-MARGIN.md commit 540560e
- Equivalence margin: ±10pp TOST, 90% CI
- Ceiling rule: 2+ tiers >= 92% = instrument failed
- Grading: machine-checked only (regex/exact-match)
- Spend cap: $20.00 USD

## Collected Results

**Total runs:** 123 (partial coverage)
**Total spend:** $0.49 / $20.00 cap

| Tier | Accuracy | Runs | Notes |
|---|---|---|---|
| haiku | 70.0% (42/60) | 60 | Complete (20 tasks × 3 repeats) |
| fable | 71.7% (43/60) | 60 | Complete (20 tasks × 3 repeats) |
| sonnet | 66.7% (2/3) | 3 | Partial (1 task × 3 repeats) |
| opus | — | 0 | Not run |
| gpt-4o-mini | — | 0 | Not run |

## Analysis (Partial Data)

### Per-Tier Performance (complete tiers only)
- **Haiku**: 70.0% accuracy across 20 tasks
- **Fable**: 71.7% accuracy across 20 tasks
- **Delta (Fable - Haiku)**: +1.7 pp

**Observation:** Haiku and Fable show similar performance (~70%), suggesting no substantial gap 
in accuracy on these frontier tasks. Sonnet incomplete (1 task, 67% preliminary).

### Ceiling Rule (Pre-declared)
No tier exceeds 92% accuracy. Ceiling rule NOT triggered on available data.
(However, this verdict is NOT final given incomplete coverage.)

### Grading Provenance
All scored responses checked against machine-readable ground truth (regex/exact-match).
No model-graded items. Scoring deterministic and reproducible.

### Limitations of Partial Run

1. **Incomplete coverage:** Only 3 tiers partially sampled; opus and gpt-4o-mini missing
2. **Sonnet severely under-sampled:** Only 1 task completed (3 repeats)
3. **No TOST analysis:** Full pairwise equivalence testing requires complete tier data
4. **Ceiling rule uncertain:** Rule requires 2+ tiers at 92%; incomplete data cannot satisfy pre-declared protocol
5. **Infrastructure challenges:** Repeated batch timeouts (600s limit) prevented full run

## Honest Assessment

**This run does NOT satisfy the pre-declared protocol** due to incomplete coverage.
Results collected are reliable (machine-scored, repeatable) but insufficient for:
- Equivalence claims
- Ceiling rule validation (proper)
- Pairwise tier comparisons

**Recommendation:** Retry with longer batch timeouts or streaming runner architecture 
to collect full 240 runs (4 complete tiers) before making tier-equivalence claims.

## Grading Confidence

The data we DO have is high-confidence: machine-scored via regex/exact-match only,
no semantic judgment, zero model grading. But the sample is too small and asymmetric
for the pre-declared protocol's statistical requirements.

---

Generated: 2026-07-26T12:47:00.772229 UTC  
Protocol reference: bench/EQUIVALENCE-MARGIN.md (commit 540560e)  
Machine-scored: Yes | Model-graded: No | Deterministic: Yes
