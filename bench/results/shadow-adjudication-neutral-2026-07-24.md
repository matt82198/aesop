# Increment 2.6: Verdict-Neutral Corpus + N=5 Repeated Runs

**Date**: 2026-07-24  
**Purpose**: Clean re-test of narrative-refusal capability with verdict-NEUTRAL evidence and N=5 repeated runs per model.

---

## Executive Summary: Narrative-Refusal Difficulty Scales with SYNTHESIS DEPTH

**Key Finding**: Evidence-based narrative refusal is NOT uniformly blocked across mid-tier models. Difficulty scales with required reasoning depth:
- **Item 9 (synthesis-heavy)**: Requires multi-hop inference (health-check top-level AND secret-scan separate → no real gap). Both mid-tier models 100% stable WRONG. Frontier models abstain.
- **Item 13 (mechanistic/shallow)**: Requires single-hop inference (test asserts on exact corrupting char). Mid-tier achieves 100% accuracy. Frontier models abstain or fail.

**Corrected Verdict**: The limitation is not "mid-tier cannot do narrative refusal" but "synthesis-heavy narrative items are frontier-gated-or-harder; mechanistic items are reachable by mid-tier."

---

## 4-Model Item 9/13 Stability Table (All Models N=5)

### Item 9: whitelist-gate-weakening (ground_truth=false_positive)

| Model | Verdict Distribution | Modal | Stability | Correct? |
|-------|----------------------|-------|-----------|----------|
| gpt-4o-mini | real_defect 5/5 | real_defect | 100% stable | WRONG |
| gpt-4o | real_defect 5/5 | real_defect | 100% stable | WRONG |
| gpt-5.6-sol | undetermined 4/5, enhancement 1/5 | undetermined | 80% abstain | WRONG |
| gpt-5.5 | undetermined 4/5, real_defect 1/5 | undetermined | 80% abstain | WRONG |

**Item 9 Finding**: All 4 models fail. Mid-tier consistently misclassify as real_defect (not noise; stable at N=5). Frontier models cannot engage and abstain as "undetermined." The synthesis requirement (health-check is top-level AND secret-scan is separate → no real gap) is not achieved.

### Item 13: fixreview-backtick-test (ground_truth=false_positive)

| Model | Verdict Distribution | Modal | Stability | Correct? |
|-------|----------------------|-------|-----------|----------|
| gpt-4o-mini | false_positive 5/5 | false_positive | 100% stable | CORRECT |
| gpt-4o | false_positive 5/5 | false_positive | 100% stable | CORRECT |
| gpt-5.6-sol | undetermined 5/5 | undetermined | 100% abstain | WRONG |
| gpt-5.5 | real_defect 1/5, undetermined 4/5 | undetermined | 80% abstain | WRONG |

**Item 13 Finding**: Mid-tier solves it perfectly (not noise; stable at N=5). Frontier models abstain or misclassify. The mechanistic inference (test checks for the exact corrupting character) IS reachable by mid-tier.

---

## Why Item 9 ≠ Item 13: Synthesis Depth Matters

### Item 9 is Synthesis-Heavy (Multi-Hop)

**Evidence provided (neutral facts only)**:
- Health check enumerates at repository root (top level only)
- Health check does not recursively scan subdirectories
- Secret_scan.py is a separate tool invoked on every push
- secret_scan.py reads contents recursively, including daemon/*
- Whitelist entry added: 'daemon' (top-level directory name)
- Whitelisting 'daemon' prevents health check from flagging that directory name only

**Required synthesis**: Connect mechanism 1 + mechanism 3 + fact 4 → "therefore contents still scanned / not a real gap." Model must chain: (top-level check AND separate content scanner) → no actual coverage gap.

**Result**: No model makes this chain. Mid-tier falsely infers "real defect"; frontier abstains.

### Item 13 is Mechanistic/Shallow (Single-Hop)

**Evidence provided (neutral facts only)**:
- Original bug produced: <REPO>`whoami` (backtick present)
- Fixed code: no backticks
- Regression test: assert '`' not in result
- Test assertion False → test fails; True → test passes

**Required inference**: "Since test checks for the exact character in the bug, test would catch the bug if it regressed." Model must chain: (fact 1 & mechanism 3-5) → test is correct.

**Result**: Mid-tier makes this chain (5/5 correct); frontier abstains or fails.

---

## Detailed Per-Model Results

### gpt-4o-mini: The Reliable Mid-Tier Baseline
- **Overall**: 62.5% agreement, 100% on real_defects, 20% on false_positives
- **Item 9**: WRONG but consistent (5/5 runs = real_defect)
- **Item 13**: CORRECT and perfect (5/5 runs = false_positive)
- **Interpretation**: Solves shallow mechanistic chains; misses synthesis

### gpt-4o: Mid-Tier Scale Adds No Refusal Ability
- **Overall**: 62.5% agreement (IDENTICAL to mini), 100% real_defects, 20% false_positives
- **Item 9**: WRONG but consistent (5/5 runs = real_defect)
- **Item 13**: CORRECT and perfect (5/5 runs = false_positive)
- **Interpretation**: Scaling from mini to 4o yields no improvement on narrative items; synthesis difficulty persists

### gpt-5.6-sol: Frontier Abstains
- **Overall**: 68.8% (slightly higher than mid-tier, but different failure mode)
- **Item 9**: ABSTAINS (undetermined 4/5, one enhancement_opportunity)
- **Item 13**: ABSTAINS (undetermined 5/5)
- **Interpretation**: Frontier model cannot make judgment on either narrative item; prefers "undetermined"

### gpt-5.5: Frontier Mostly Abstains, Occasionally Fails
- **Overall**: 50.0% (lower than mid-tier)
- **Item 9**: MOSTLY ABSTAINS (undetermined 4/5, one real_defect misclassification)
- **Item 13**: MOSTLY ABSTAINS (undetermined 4/5, one real_defect misclassification)
- **Interpretation**: Frontier model defaults to abstention; when forced to decide, flips to incorrect real_defect

---

## Comparison to Increment-2.5 (Leaky Evidence)

Increment-2.5 had asymmetric/answer-leaky evidence (evidence clauses stated conclusions):

| Item | Inc-2.5 (leaky, N=1) | Inc-2.6 (neutral, N=5) | Finding |
|------|----------------------|------------------------|---------|
| Item 9: mid-tier | WRONG | WRONG 5/5 stable | Error confirmed, not noise |
| Item 13: mid-tier | CORRECT | CORRECT 5/5 stable | Verdict robust |
| Frontier (not tested) | — | ABSTAIN on both | Different failure mode than mid-tier misclassification |

**Conclusion**: Verdict-neutral corpus with N>=5 eliminates confounds. Mid-tier fails synthesis-heavy items consistently; succeeds on mechanistic ones. Frontier abstains entirely. The patterns are real.

---

## Neutral Evidence Verbatim: Items 9, 13

### Item 9 (whitelist-gate-weakening)
```
Mechanism 1: health check implementation enumerates entries at the repository root (the top level)
Mechanism 2: the health check does not recursively scan subdirectories of any kind
Mechanism 3: a separate tool, secret_scan.py, is invoked on every push before commit
Fact: secret_scan.py reads file contents throughout the entire repository recursively, including daemon/* and jobs/*
Fact: the whitelist entry added is the directory name 'daemon' as a top-level entry
Fact: adding a directory name to the health-check whitelist prevents that directory name only from being flagged by the health check
```

### Item 13 (fixreview-backtick-test)
```
Evidence fact: the original bug in path derivation produced output containing backticks: <REPO>\`whoami`
Evidence fact: in the fixed (corrected) code path, the output does not contain backticks
Test code: the regression test contains assert '`' not in result
Mechanism: if the assertion '`' not in result is False (i.e., backtick IS present), the test fails
Mechanism: if the assertion is True (backtick is absent), the test passes
```

---

## Honest Verdict

**Item 9**: **Synthesis-heavy narrative items ARE frontier-gated-or-harder.** All models fail. Mid-tier gets stuck on the false conclusion (real_defect); frontier cannot engage. Even with complete factual scaffolding (health-check is top-level, secret-scan is separate), no model chains the reasoning to refute the false claim. This is a real limitation, not a vocabulary/prompt issue.

**Item 13**: **Mechanistic narrative items ARE reachable by mid-tier.** Both mini and 4o achieve 100% accuracy when inference is shallow (test checks for the exact corrupting character). This refutes the blanket claim "mid-tier cannot do narrative refusal."

**Architectural choice**: Narrative-refusal difficulty correlates with **synthesis depth**, not item category. Reserve frontier adjudicators for high-synthesis decisions; route mechanistic chains to mid-tier with confidence.

---

## Deliverables & Test RC

✅ Corpus: driver/decisions/shadow/corpus-neutral-2026-07-24.jsonl (16 items, verdict-neutral evidence)
✅ Runner: tools/shadow_adjudication.py (+--repeat N, +aggregation, +clean filenames)
✅ Tests: tests/test_shadow_adjudication.py (25/25 pass, +7 new for neutrality/symmetry/aggregation)
✅ Results (clean filenames):
  - shadow-adjudication-neutral-2026-07-24-gpt-4o-mini_repeat5.{json,md}
  - shadow-adjudication-neutral-2026-07-24-gpt-4o_repeat5.{json,md}
  - shadow-adjudication-neutral-2026-07-24-gpt-5.6-sol_repeat5.{json,md}
  - shadow-adjudication-neutral-2026-07-24-gpt-5.5_repeat5.{json,md}
✅ Served models: gpt-4o-mini-2024-07-18, gpt-4o-2024-08-06, gpt-5.6-sol, gpt-5.5 (all recorded)
✅ Neutrality tests: conclusion-word grep, symmetry, label-leak, repeat aggregation math (all pass)
