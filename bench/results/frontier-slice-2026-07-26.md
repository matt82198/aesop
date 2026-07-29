# Frontier Discrimination Slice Results — 2026-07-26 (FINAL, 299/300 runs)

Pre-registered protocol: bench/METHODOLOGY.md (committed 540560e, BEFORE any results). Grading: machine-checked ground-truth patterns only; zero model-graded items. Transport: claude CLI for all Claude tiers; OpenAI seam for gpt-4o-mini. One opus run (ft13 rep1) unrecoverable after 4 retries — 59/60.

## Headline

**The slice discriminates (no ceiling), and the ordering is not the marketing order: the commodity external model (gpt-4o-mini, 75.0%) topped the table and Opus 5 (66.1%) came last.** At N=20 tasks x 3 repeats the CIs are wide: no pairwise equivalence can be formally claimed at the pre-declared +/-10pp margin, and by the same token the observed ordering should not be over-read — the honest summary is that on THIS task slice, tier does not predict accuracy. This is the result, published as pre-committed, including the parts that hurt.

## Per-tier accuracy

| Tier | Accuracy | Runs | Est. cost |
|---|---|---|---|
| gpt-4o-mini | 75.0% (45/60) | 60 | $0.02 |
| haiku | 71.7% (43/60) | 60 | $0.29 |
| sonnet | 71.7% (43/60) | 60 | $1.17 |
| fable | 68.3% (41/60) | 60 | $0.08 |
| opus | 66.1% (39/59) | 59 | $2.69 |

## TOST vs the pre-declared +/-10pp margin (90% CI)

| Pair | Delta (pp) | 90% CI | Verdict |
|---|---|---|---|
| gpt-4o-mini vs haiku | +3.3 | [-9.9, +16.6] | equivalence NOT demonstrated (CI exceeds +/-10pp) |
| gpt-4o-mini vs sonnet | +3.3 | [-9.9, +16.6] | equivalence NOT demonstrated (CI exceeds +/-10pp) |
| gpt-4o-mini vs fable | +6.7 | [-6.8, +20.2] | equivalence NOT demonstrated (CI exceeds +/-10pp) |
| gpt-4o-mini vs opus | +8.9 | [-4.8, +22.6] | equivalence NOT demonstrated (CI exceeds +/-10pp) |
| haiku vs sonnet | +0.0 | [-13.5, +13.5] | equivalence NOT demonstrated (CI exceeds +/-10pp) |
| haiku vs fable | +3.3 | [-10.4, +17.1] | equivalence NOT demonstrated (CI exceeds +/-10pp) |
| haiku vs opus | +5.6 | [-8.4, +19.5] | equivalence NOT demonstrated (CI exceeds +/-10pp) |
| sonnet vs fable | +3.3 | [-10.4, +17.1] | equivalence NOT demonstrated (CI exceeds +/-10pp) |
| sonnet vs opus | +5.6 | [-8.4, +19.5] | equivalence NOT demonstrated (CI exceeds +/-10pp) |
| fable vs opus | +2.2 | [-11.9, +16.4] | equivalence NOT demonstrated (CI exceeds +/-10pp) |

Interpretation: with n=60 per tier the CI half-width is ~13-14pp, wider than the +/-10pp margin — so equivalence cannot be demonstrated for ANY pair at this N, exactly as the pre-registration warned ("ruling out gaps much smaller than ~10pp is not possible at this N"). Equally, no gap is established: every CI also spans zero except where noted above.

## Ceiling verdict

NOT TRIPPED — highest tiers at 75.0% and 71.7%, well under the 92% rule. Unlike judgment-v3 (see bench/results/2026-07-26-judgment-v3-ceiling-addendum.md), this instrument discriminates.

## Distribution notes (honesty)

- ft09 scored 0% across ALL five tiers — a candidate defective item (ground-truth or prompt), flagged for review; excluding it shifts all tiers up ~3pp uniformly and does not change any conclusion.
- Several items show non-monotonic tier behavior (e.g. items where opus fails and sonnet passes 3/3) — consistent with format-sensitivity in pattern grading; per-item data in the JSON supports auditing every one.
- Spend: $4.25 of the $20 cap.

## Limitations

- N=20 tasks, 3 repeats: small; wide CIs; single-operator task curation.
- One incomplete opus run (59/60) — recorded, not imputed.
- gpt-4o-mini ran on a different transport (OpenAI seam vs claude CLI) — same prompts and grading, but transport is a potential confound worth noting.
- This slice measures these 20 task shapes, nothing broader.
