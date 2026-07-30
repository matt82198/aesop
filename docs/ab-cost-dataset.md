# A/B Cost Dataset: Dispatch Topology & Tiered-Cognition A/B Tests

**Purpose:** Raw data backing Aesop's "4x cost reduction" claim — the measured cost differences between flat Haiku-first dispatch (status quo) vs. hierarchical Sonnet-specialist dispatch and tiered cognition architectures.

**Date:** 2026-07-14 · **Runs:** Workflow `wf_c7fe6017-4b2` + tiered-cognition fixture  
**Full details:** Available in `conductor3/plans/ab-dispatch-topology-results.md` and `conductor3/COST-LOG.md` (private conductor3 archive)

---

## Executive Summary

Two independent A/B measurements on 2026-07-14 both demonstrate that adding an intermediate Sonnet tier to coordinate Haiku executors **increases cost 4–4.3×** while producing **identical quality** (100% test pass, zero repair rounds).

- **Dispatch Topology A/B:** Hierarchical (Sonnet planners → Haiku fleet) = **1.75× raw / 4.33× weighted cost** vs. flat (blind Haiku per module). Same 20/20 test pass rate, zero repairs needed.
- **Tiered-Cognition A/B:** Sonnet cognition stage → Haiku execution = **2.15× raw / 4.60× weighted cost** vs. end-to-end Haiku. Same 6/6 suite pass rate, zero repairs needed.

The claim "4x cost reduction" inverts this: the status quo (flat Haiku-first) is **4x cheaper than** the rejected alternatives, not a 4x improvement over them.

---

## Methodology: NO-UNVERIFIED-METRICS

All token counts are **measured from `budget.spent()` snapshots** at homogeneous-model barriers, attributed to the agent/model that crossed them. No backfilled estimates; no unverified telemetry claims.

### Cost Model

- **Haiku:** 1.0 weight unit
- **Sonnet:** 3.0 weight units (input and output alike)
- **Opus:** 5.0 weight units

Reported figures are **output tokens only** (where `budget.spent()` deltas are measured), labeled as such.

### Fixture Integrity Gates

Both arms used identical, sanity-verified fixtures:

1. **Pre-arm verification:** Fixture tested green against a reference solution (then discarded), confirmed red on stubs.
2. **Setup-token exclusion:** Fixture authoring tokens are a separate line item, excluded from both arm measurements.
3. **Abort on failure:** If sanity gates failed, the workflow aborted rather than emit invalid numbers.

---

## A/B Test 1: Dispatch Topology (2026-07-14)

**Fixture:** `eventpipe` — 6 interdependent modules, 20-test integrating suite with a deliberate cross-module ambiguity (priority sort direction and tie-break rule).

**Design:** 
- **ARM A (flat, status quo):** orchestrator spawns one blind Haiku per module, then integration + final-catch verify
- **ARM B (hierarchical):** orchestrator → 2 Sonnet specialists (each reads shared contract, spawns its own Haiku fleet, reviews output) → integration

### Results

| Metric | ARM A (flat) | ARM B (hierarchical) | Ratio (B/A) |
|--------|---:|---:|---:|
| **Raw output tokens** | 16,803 | 29,416 | **1.75×** |
| **Weighted tokens** (H=1, S=3) | 16,803 | 72,686 | **4.33×** |
| — Sonnet output | 0 | 21,635 | — |
| — Haiku output | 16,803 | 7,781 | — |
| **Quality (pytest)** | 20/20 green | 20/20 green | **tie** |
| **Repair rounds** | 0 | 0 | **tie** |
| **Wall time** | — | 7m24s | — |

### What Happened

- **ARM A (flat) did not drift:** Every blind Haiku independently implemented the priority rule correctly. Zero repair rounds needed. The `order` executor wrote the correct sort, `dispatch` correctly avoided re-sorting on it.
- **ARM B's Sonnet reviewers did nothing:** Both reviewers reported "all files already compliant, no edits made." The coordination layer had no actual drift to prevent.
- **Cost moved, not disappeared:** ARM B's Haiku executors were cheaper (7,781 vs 16,803 output tokens) because the Sonnet planners pre-chewed the work into mechanical orders. But that cognition moved to the 3× tier — total weighted cost rose 4.33×.

### Why This Arm Design Matters

The 6-module fixture was constructed so hierarchy *should* win if it wins at all: a deliberately-inverted priority rule that creates cross-module coupling. Assigning priority-coupled modules to a single Sonnet specialist guaranteed internal agreement. But the flat arm's blind Haikus solved it anyway without coordination overhead — the regime where flat should lose has now gone untested twice.

---

## A/B Test 2: Tiered-Cognition (2026-07-14, earlier)

**Fixture:** 3 small Haiku-solvable tasks (LRU-cache implementation, bug-fix, refactor).

**Design:**
- **ARM A (end-to-end Haiku):** One Haiku agent per task, full cognition + implementation
- **ARM B (tiered):** Sonnet planning stage (WORK-ORDER) → Haiku execution (EXEC-BRIEF)

### Results

| Task | A raw | B raw | Raw B/A | A weighted | B weighted | Weighted B/A | A green | B green | Repairs |
|------|---:|---:|---:|---:|---:|---:|---|---|---|
| T1 LRU-cache | 36,923 | 83,328 | 2.26× | 36,923 | 176,396 | 4.78× | YES | YES | 0 |
| T2 3-bug fix | 41,522 | 83,779 | 2.02× | 41,522 | 179,651 | 4.33× | YES | YES | 0 |
| T3 refactor | 39,009 | 85,998 | 2.20× | 39,009 | 183,988 | 4.72× | YES | YES | 0 |
| **TOTAL** | **117,454** | **253,105** | **2.15×** | **117,454** | **540,035** | **4.60×** | **3/3** | **3/3** | **0** |

### What This Shows

Both arms achieved 100% test pass rate and zero repair loops on identical Haiku-solvable tasks. The Sonnet planning tier relocates cognition upward without reducing it, inflating cost 4.60× without quality gain.

---

## Honest Caveats (What This Does NOT Measure)

### Same Fidelity Ceiling as Before

To be a fair A/B fixture, both tests required:
1. A complete, unambiguous contract (so no rework is needed)
2. Haiku-solvable tasks (no solo-Haiku failure threshold)

This ensures clean comparison, but it also means **hierarchy's claimed payoff has never been tested:** the regime where solo-Haiku failure rate > 0 and a Sonnet coordinator could buy back quality gains.

### Limited Sample Size

- Dispatch Topology: **n=1 fixture** (eventpipe), **n=1 run per arm**
- Tiered-Cognition: **n=3 tasks**, **n=1 run per task**, **output tokens only**

These are proof-of-concept measurements, not a large-scale benchmark. They prove the cost penalty exists and is real; they do not bound its magnitude across all possible workloads.

### What Genuinely Remains Untested

- **Hard-task scenarios** (where solo-Haiku failure rate > 0 and coordination has real work to do)
- **Incomplete contracts** (where cross-module consensus must be invented, not enforced)
- **Input token costs** (only output tokens measured here; both arms likely differed on input token usage)
- **Model tier interaction** (what if you use Sonnet for the executor layer instead of Haiku? Not tested.)

---

## The Real Lever: Workflow Encapsulation

Both A/B tests reached the same decisive insight:

> **To close a wave in one turn, you do not need a Sonnet mid-tier. You need workflow-encapsulation of the cheap flat Haiku fan-out.**

ARM A and ARM B both completed in a single Workflow call = one orchestrator turn. The user's premise (that flat branch-per-feature requires many orchestrator turns) is true of the *live interactive system* but is **not inherent to flat dispatch.** Wrapping the flat fan-out + integration + repair in a workflow achieves one-turn execution at 4.3× lower weighted cost than hierarchy.

---

## Recommendations (Status)

1. **Adopt workflow-encapsulated flat dispatch** as the lever for one-turn wave execution. ✓ *Implemented as part of wave-15 machinery.*
2. **Do not adopt hierarchical Sonnet-specialist dispatch** for routine wave work on cost grounds. ✓ *Policy encoded in dispatch-model.md and CARDINAL-RULES.*
3. **Only revisit hierarchy with a hard-task benchmark** (measured solo-Haiku failure rate > 0) where quality is the deciding metric, A/B'd against "improve the contract/spec" as the cheaper control. — *Candidate for future benchmarking cycle.*

---

## Verification & Source

**Raw measurement artifacts** (private, conductor3 archive):
- `conductor3/plans/ab-dispatch-topology-results.md` — full dispatch-topology A/B writeup with journal
- `conductor3/plans/tiered-cognition-ab-results.md` — (referenced in COST-LOG.md)
- `conductor3/COST-LOG.md` — cost analysis log, cycle 2026-07-14 entries
- `conductor3/plans/ab-topology-workflow.mjs` — harness implementation (Workflow orchestration, agent dispatch, token telemetry)

**Published references:**
- `docs/DISPATCH-MODEL.md` — Aesop's dispatch policy (flat, Haiku-first, branched)
- `docs/CARDINAL-RULES.md` — Subagents always Haiku (cost lever)
- `docs/CONCEPTS.md` — Hierarchy section; "workflow-encapsulation" as the alternative to mid-tier

**Verification checklist for reviewers:**
- [ ] Fixture sanity gates passed (20/20 green on reference, 19/20 red on stubs)
- [ ] Token counts match `budget.spent()` snapshots at homogeneous-model barriers
- [ ] Both arms used identical fixture code (seeded from same stubs)
- [ ] Quality metrics (pytest pass/fail, repair rounds) verified independently
- [ ] Setup tokens excluded from both arms
- [ ] Weighted cost model (H=1, S=3) applied consistently

---

## License & Citation

This dataset is part of Aesop (MIT license). If you cite these numbers in external venues (press, research, hiring), please:
1. Cite both the raw *and* weighted cost figures (not just one)
2. State the caveats (n=1 fixture, Haiku-solvable only, output-tokens-only)
3. Link back to this document as the canonical source
4. Note that "4x cost reduction" is a *comparison against rejected alternatives*, not an absolute efficiency claim
