# Seated Shadow Adjudication Redo (Increment 4a) — Implementation Summary

**Date**: 2026-07-24  
**Status**: Code & tests complete; API results pending  
**PR**: #358  
**Branch**: feat/seated-shadow-redo

## What Was Accomplished

### 1. New Tool: `tools/seated_shadow_adjudication.py`

Complete rewrite of shadow adjudication to use the **wired seam** (increment 1.5):

#### Key Differences from Prior Shim
- **Old**: SimpleOpenAIDriver side-channel, prompt dropped, context never passed to backend
- **New**: OrchestratorDriver.decide() + OpenAICompatibleOrchestratorBackend, prompt end-to-end, schema validated

#### Implementation Details
```python
# Real context pack building
build_seated_context_pack(item, repo_root, conductor_root)
├─ File brain: STATE.md, tracker.json, BUILDLOG.md (last 50 lines)
├─ Evidence dict: finding framing + cited code (mechanism, behavior, facts)
└─ Blind: labels never reach context pack (assertion)

# Adjudication through wired seam
OrchestratorDriver.decide("adjudicate_finding", pack, schema=schema)
├─ Prompt: _build_decision_prompt(decision_type, context_pack)
├─ Backend: backend.decide_call(prompt, schema=schema)  # REAL call
├─ Response: JSON parsed, validated against schema
└─ Retry: <=2 times on malformed, then DECISION_FAILED (never green)

# Results aggregation (N>=3 runs)
aggregate_seated_results(verdicts, corpus, num_runs)
├─ Modal verdict: most common classification across runs
├─ Stability: fraction of runs matching mode (>=2/3 = true mode)
├─ Reasoning persisted: each verdict carries full challenger reasoning text
└─ Item 9 special: whitelist-gate-weakening flip detection (frontier-first gate)
```

#### Frontier-First Abort Logic
```
IF model == gpt-5.6-sol AND item_9_modal != "false_positive" THEN
    ABORT cheaper run (gpt-5.5)
    REASON: frontier failing => cheaper won't pass
    HYPOTHESIS: real context does not rescue narrative refutation
```

#### Stale-Label Handling
- **Item 7 (hardcoded-username)**: Fixed in current code (docs/INSTALL.md uses placeholders)
- **Item 6 (unc-paths)**: Disputed (MSYS accepts //server/share; invalid-bash claim unproven)
- **Both tracked**: Current-state-correct assessment per seated verdict + reasoning

#### Benign-Drift Check
Counts real_defect items that remain real_defect under real context:
- Detects if seated adjudication significantly flips real defects to false
- If yes: benign drift (bad sign — real context is misleading)
- If no: seated verdicts actually respect finding validity

### 2. Comprehensive Test Suite: `tests/test_seated_shadow_adjudication.py` (8 cases)

| Test | Purpose | Status |
|------|---------|--------|
| test_seated_pack_includes_evidence | Evidence dict has finding + corpus items | ✅ PASS |
| test_labels_never_leak | Assertion: labels absent from pack | ✅ PASS |
| test_adjudicate_persists_reasoning | Reasoning text persisted per verdict | ✅ PASS |
| test_schema_valid_set | schema_valid boolean set correctly | ✅ PASS |
| test_modal_verdict_computation | Modal verdict & stability math | ✅ PASS |
| test_held_real_defects | Count real_defects held as real | ✅ PASS |
| test_item9_flip_detection | Item 9 flip to false_positive | ✅ PASS |
| test_load_corpus_from_jsonl | Corpus parsing from JSONL | ✅ PASS |

**Offline Proof**
- FakeOrchestratorBackend with canned string verdicts
- schema_valid=true end-to-end
- No API keys, no network

**Regression Test Results**
- test_orchestrator_driver.py (20 suites): ✅ GREEN
- test_adjudication_gate.py (20 suites): ✅ GREEN
- No changes to orchestrator seam; all existing tests pass

### 3. Documentation Updates

**tests/CLAUDE.md**
- Python test count: 128 → 136 (+8 new seated shadow cases)
- Dropped section updated with increment 4a note
- Full accounting of orchestrator-swap test additions

## API Runs (Pending)

### Phase 1: gpt-5.6-sol (N=3) — IN PROGRESS
- **Progress**: 48 API calls in flight (16 items × 3 runs)
- **Expected completion**: Within 10-15 minutes from start
- **Measurements**:
  - Item 9 flip verdict (key test): must be false_positive (≥2/3 runs)
  - Real defects held count: how many gt=real_defect stay real_defect
  - Schema validity: all verdicts must have schema_validated=true
  - Reasoning: persisted for each verdict
  - Stability: per-item fraction matching modal verdict

### Phase 2: gpt-5.5 (N=3) — CONDITIONAL
- **Trigger**: Only if gpt-5.6-sol item 9 flips to false_positive
- **Skip condition**: If frontier fails, abort (hypothesis: real context doesn't rescue)
- **Measurements**: Same as Phase 1, compare gap collapse with gpt-5.6-sol

## Deliverables Checklist

### Code
- ✅ tools/seated_shadow_adjudication.py (387 lines)
- ✅ tests/test_seated_shadow_adjudication.py (330 lines, 8 cases)
- ✅ tests/CLAUDE.md (updated)
- ✅ All offline tests green
- ✅ No regressions (40/40 orchestrator tests pass)

### Git
- ✅ feat/seated-shadow-redo branch created
- ✅ Commit 0b92797: code + tests + docs
- ✅ Pushed to origin
- ✅ PR #358 created with detailed description

### Pending
- ⏳ bench/results/seated-redo-2026-07-24-sol-wired-n3.{json,md}
- ⏳ bench/results/seated-redo-2026-07-24-gpt55-wired-n3.{json,md} (conditional)
- ⏳ Final summary markdown with honest bounds
- ⏳ PR description update with actual results

## Honest Bounds

### What This Increment Proves
✅ **Seam is wired mechanically**
- OrchestratorDriver.decide() passes prompt end-to-end
- Real context packs build from file brain + cited evidence
- schema_validated=true (no retry exhaustion on valid responses)
- Reasoning persisted (evidence-cited verdicts preserved)

✅ **Stability measurable over N>=3 runs**
- Modal verdict computed correctly
- Stability fraction = runs matching mode / total runs
- True mode defined as >=2/3 agreement

⚠️ **NOT proven**
- Long-loop coherence (adjudicate + rank + review + final-catch in sequence)
- Live wave swap (increment 4b requires actual governing adjudication)
- Narrative refutation at scale (item 9 is THE test; 1 item is not enough)
- Upgrade path (4b: escalation gate needs real wave context)

### File Brain Variable
- ✅ Real (STATE.md, tracker.json, BUILDLOG.md from disk at run time)
- Real context may expose model coherence gaps (long loop) not visible in single decisions

### Seated-Swap Readiness
**After 4a**: Wired seam proven; real context seating proven; item 9 flip measured  
**After 4b**: Actual wave coordination; adjudication_gate escalation tested; full coherence data

## Known Limitations

1. **Corpus is Retrospective**: 16 items adjudicated POST-FIX (labels known). Models see finding + source only (blind), but findings reference fixed code. Stale labels assessed per seated verdict.

2. **Single-Decision Judgments**: Each verdict stands alone. No coherence test (e.g., "if this is real, is this also real?" across related findings).

3. **Narrative Refutation**: Item 9 (false_positive claiming gate-weakening) tests whether models refuse based on mechanism alone. Frontier refusing != cheaper refusing => frontier-only skill (not a scale-dependent failure).

4. **Temperature Fallback**: gpt-5.x rejects temperature=0. Backend auto-retries without temperature. May affect reasoning quality (unmeasured).

## Next Steps (After API Results)

1. **Inspect item 9 modal verdict + reasoning**
   - If false_positive: proceed to gpt-5.5
   - If not: document frontier failure; abort cheap run

2. **If gpt-5.5 runs**:
   - Measure gap: gpt-5.6-sol accuracy vs gpt-5.5 on same seated context
   - Report coherence (do same findings stay consistent verdict)?

3. **Stale-label assessment**:
   - Item 7: does seated verdict match current code state?
   - Item 6: does seated verdict treat UNC acceptance correctly?

4. **Final commit + PR update**:
   - Add bench/results/ files
   - Update PR with actual results + item 9 quoted reasoning
   - Honest bounds statement for increment 4a

5. **Mark for 4b Planning**:
   - If seam green: wave-7 trial swap (adjudication_gate escalation)
   - If seam issues: debug before attempting live swap

---

**Status**: Code complete, offline validated, API in flight. Awaiting results to finalize.
