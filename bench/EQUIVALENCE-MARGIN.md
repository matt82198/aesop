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

## Amendment 1 — N=60 expansion (committed 2026-07-26, before v2 results)

This amendment pre-commits 40 additional frontier discrimination tasks (ft21–ft60) alongside the original 20 (ft01–ft20), expanding the discrimination slice from N=20 to N=60.

**Scope and authorship:**
- **40 new tasks (ft21–ft60)** authored after the N=20 v1 results were known, without per-model tuning.
- New tasks follow the same format and difficulty philosophy as ft01–ft20: multi-step reasoning, defect detection, semantic equivalence, config validation, etc.
- All 60 tasks now have machine-checkable ground-truth patterns (regex or exact-match) with exemplar and counter-example validation.
- Test file `tests/test_frontier_slice_n60.py` verifies all 60 patterns mechanically: exemplars MUST match, counter-examples MUST NOT match (credibility gate).

**New protocol for N=60 run (v2):**
- Same tiers under test (Opus-5, Fable-5, Sonnet-5, Haiku-4.5, + OpenAI seam).
- Same repeated-run structure: 3 repeats per task × 60 tasks.
- Same margin protocol: equivalence claim only if difference ≤10pp AND TOST CI within ±10pp.
- **Updated ceiling rule: if two or more tiers land at or above 85% (lowered from 92% to reflect larger N), the instrument discriminates poorly — we say so explicitly.**
- Grading: machine-checked only (patterns pre-committed and tested). No silent model grading.
- Parallel execution: concurrent task runs do NOT affect scoring; per-run transport recorded for reproducibility.
- Spend cap for v2 run: US$30 (N=60 vs. N=20, × 5 tiers × 3 repeats = 900 API calls).

**Data and bias:**
- **Honesty statement:** ft21–ft60 authored after N=20 results known; we state explicitly: "no per-model tuning" and will verify post-hoc via per-tier per-task performance audits.
- Pre-committed: all task definitions, ground-truth patterns, exemplars, counter-examples.
- Pattern-validity test (tests/test_frontier_slice_n60.py) runs in CI; no new results until test passes CLEAN.

**Disposition of ft09 (0% in N=20):**
- Task: `ft09_refactoring_correctness_semantic` — simple list comprehension equivalence.
- Result: All five tiers scored 0% on this straightforward task despite correct exemplar/pattern.
- Analysis: Ground-truth pattern and exemplar are valid; the task is genuinely difficult for the weaker FakeTransport mock but expected to score higher on real models. No pattern fix needed; task retained as-is. Expect higher accuracy on real v2 run.

**Record audit trail:**
- Commit hash of this amendment is the binding timestamp.
- All 60 task definitions (bench/tasks_frontier.jsonl) and ground truth (bench/ground_truth_frontier.jsonl) committed before any v2 results.
- Test suite run gate enforced in CI (tests/test_frontier_slice_n60.py must pass).
- Post-v2: audit report will include per-tier breakdown, per-task score distributions, and equivalence decision per the margin protocol.
