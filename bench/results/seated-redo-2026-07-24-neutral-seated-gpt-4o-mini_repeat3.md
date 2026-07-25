# Seated Shadow Adjudication — Increment 4a Redo

**Date**: 2026-07-24
**Challenger Model**: gpt-4o-mini
**Runs**: 3
**Corpus Size**: 16 items
**Seam**: OrchestratorDriver.decide() with OpenAICompatibleOrchestratorBackend (wired seam, increment 1.5)

## Summary

### Item 9 Flip Verdict (Key Test)

**Item**: whitelist-gate-weakening (gt=false_positive)
**Modal verdict**: false_positive
**Stability**: 100.0% (3/3 runs)
**Flips to false_positive**: YES

**Reasoning** (first run):
```
['Mechanism 1: health check implementation enumerates entries at the repository root (the top level)', 'Mechanism 2: the health check does not recursively scan subdirectories of any kind', 'Mechanism 3: a separate tool, secret_scan.py, is invoked on every push before commit', 'Fact: secret_scan.py reads file contents throughout the entire repository recursively, including daemon/* and jobs/*', "Fact: the whitelist entry added is the directory name 'daemon' as a top-level entry", 'Fact: adding a ...
```

### Real Defect Retention

Items with gt=real_defect: 9
Items held as real_defect (modally): 7

### Schema Validity

Valid verdicts: 48/48 (100.0%)

## Per-Item Results

| ID | Ground Truth | Modal Verdict | Stability | Correct |
|---|---|---|---|---|
| vbs-waitforexit | real_defect | real_defect | 100.0% | ✓ |
| dryrun-blocked | real_defect | false_positive | 100.0% | ✗ |
| uninstall-exit0 | real_defect | real_defect | 100.0% | ✓ |
| quote-validation | real_defect | real_defect | 100.0% | ✓ |
| apostrophe-path | real_defect | real_defect | 100.0% | ✓ |
| unc-paths | real_defect | false_positive | 100.0% | ✗ |
| hardcoded-username | real_defect | real_defect | 100.0% | ✓ |
| audit-log-observability | enhancement_opportunity | real_defect | 100.0% | ✗ |
| whitelist-gate-weakening | false_positive | false_positive | 100.0% | ✓ |
| ps1-syntax-gate | enhancement_opportunity | real_defect | 100.0% | ✗ |
| test-hardcoded-path | real_defect | real_defect | 100.0% | ✓ |
| fixreview-parents1 | false_positive | false_positive | 100.0% | ✓ |
| fixreview-backtick-test | false_positive | false_positive | 100.0% | ✓ |
| regression-ui-suite | false_positive | false_positive | 100.0% | ✓ |
| cimergewait-exit0 | real_defect | real_defect | 100.0% | ✓ |
| vbs-syntax-validity | false_positive | false_positive | 100.0% | ✓ |

## Stale-Label Analysis

### Item 7: hardcoded-username
**Finding-time label**: real_defect (docs shipped with path 'Users/<user>')
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
- schema_validated=100.0% (production readiness required ~100%)
- N=3 per model (stability measured)

**NOT tested in this increment**:
- Long-loop coherence (one wave's full decision sequence)
- Live adjudication inside a real wave (increment 4b)
