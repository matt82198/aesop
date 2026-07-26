# Pre-declared equivalence margin — frontier discrimination slice

- Declaration date/time: 2026-07-26 (UTC timestamp of this commit is the binding record).
- **Claim protocol: we claim tier equivalence on the discrimination slice ONLY IF the observed accuracy difference is under 10 percentage points AND the TOST 90% confidence interval for the difference lies entirely within ±10pp.** (Equivalence bounds ±10pp, alpha=0.05 per one-sided test.)
- Anything outside that: we report the gap as-is, including results that hurt.
- Protocol: each tier runs the same slice with **3 repeats per task**; per-task majority scoring; report per-tier score DISTRIBUTIONS (per-task, per-repeat), not just means.
- **Ceiling rule (pre-declared): if two or more tiers land at or above 92%, the instrument failed to discriminate — we say so explicitly and harden the task set before making any equivalence claim.**
- Grading provenance (pre-declared): grading is machine-checked against ground-truth answer patterns committed in the slice definitions; no model-graded scoring in this run. If any task requires judgment-based grading, it is excluded or flagged, never silently model-graded.
- Tiers under test: claude-opus-5 (released 2026-07-24), claude-fable-5, claude-sonnet-5, claude-haiku-4-5-20251001, plus one non-Claude tier (OpenAI seam) for external validity.
- Spend cap for the full run: US$20.
- Statistical honesty notes: N=20 tasks x 3 repeats is SMALL; ruling out gaps much smaller than ~10pp is not possible at this N — that is WHY the margin is 10pp. Larger claims require a bigger slice (tracked as follow-up).
