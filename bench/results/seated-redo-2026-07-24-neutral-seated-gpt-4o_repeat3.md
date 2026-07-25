# Seated Shadow Adjudication — Increment 4a Redo

**Date**: 2026-07-24
**Challenger Model**: gpt-4o
**Runs**: 3
**Corpus Size**: 16 items
**Seam**: OrchestratorDriver.decide() with OpenAICompatibleOrchestratorBackend (wired seam, increment 1.5)

## Summary

### Item 9 Flip Verdict (Key Test)

**Item**: whitelist-gate-weakening (gt=false_positive)
**Modal verdict**: decision_failed
**Stability**: 100.0% (3/3 runs)
**Flips to false_positive**: NO

**Reasoning** (first run):
```
Backend error after 3 attempts: OpenAI API request failed: HTTP Error 429: Too Many Requests [rate_limit_exceeded, Rate limit reached for gpt-4o in organization org-iVSgyrdEWZmXo5YBrpwz8JGH on tokens per min (TPM): Limit 30000, Used 28798, Requested 6427. Please try again in 10.45s. Visit https://platform.openai.com/account/rate-limits to learn more.]...
```

### Real Defect Retention

Items with gt=real_defect: 9
Items held as real_defect (modally): 0

### Schema Validity

Valid verdicts: 5/48 (10.4%)

## Per-Item Results

| ID | Ground Truth | Modal Verdict | Stability | Correct |
|---|---|---|---|---|
| vbs-waitforexit | real_defect | decision_failed | 66.7% | ✗ |
| dryrun-blocked | real_defect | decision_failed | 66.7% | ✗ |
| uninstall-exit0 | real_defect | decision_failed | 66.7% | ✗ |
| quote-validation | real_defect | decision_failed | 66.7% | ✗ |
| apostrophe-path | real_defect | decision_failed | 100.0% | ✗ |
| unc-paths | real_defect | decision_failed | 100.0% | ✗ |
| hardcoded-username | real_defect | decision_failed | 100.0% | ✗ |
| audit-log-observability | enhancement_opportunity | decision_failed | 100.0% | ✗ |
| whitelist-gate-weakening | false_positive | decision_failed | 100.0% | ✗ |
| ps1-syntax-gate | enhancement_opportunity | decision_failed | 66.7% | ✗ |
| test-hardcoded-path | real_defect | decision_failed | 100.0% | ✗ |
| fixreview-parents1 | false_positive | decision_failed | 100.0% | ✗ |
| fixreview-backtick-test | false_positive | decision_failed | 100.0% | ✗ |
| regression-ui-suite | false_positive | decision_failed | 100.0% | ✗ |
| cimergewait-exit0 | real_defect | decision_failed | 100.0% | ✗ |
| vbs-syntax-validity | false_positive | decision_failed | 100.0% | ✗ |

## Stale-Label Analysis

### Item 7: hardcoded-username
**Finding-time label**: real_defect (docs shipped with path 'Users/matt8')
**Current state**: FIXED (docs/INSTALL.md has no hardcoded paths; matt8 hits are npm handle)
**Seated modal verdict**: [see table above]

### Item 6: unc-paths
**Finding-time label**: real_defect (path converter mangles UNC paths)
**Dispute note**: MSYS/Git-Bash accepts //server/share, so invalid-path mechanism unproven
**Seated modal verdict**: [see table above]

## Honest Bounds

This is REAL-CONTEXT seated adjudication through the WIRED seam (increment 1.5):
- File brain is REAL (STATE.md, tracker.json from disk)
- Cited code/evidence is REAL (persisted in corpus + context pack)
- OrchestratorDriver.decide() is REAL (not shim)
- schema_validated=10.4% (production readiness required ~100%)
- N=3 per model (stability measured)

**NOT tested in this increment**:
- Long-loop coherence (one wave's full decision sequence)
- Live adjudication inside a real wave (increment 4b)
