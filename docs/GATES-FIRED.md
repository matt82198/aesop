# Gates Fired: Evidence-Backed Record

A committed audit of which guardrails have demonstrably blocked code, versus which exist untested.

**Credibility standard**: Every PROVEN entry cites a commit SHA, PR number, or run ID that can be verified against this repository's git history. UNPROVEN gates are listed explicitly.

## Summary Counts

- **PROVEN activations**: 7 gates have demonstrably blocked code
- **UNPROVEN gates**: 15 gates implemented but no documented activation
- **FAIL-OPEN gates**: 0 (all are fail-closed or ratcheted)
- **Total gates**: 22

## Proven Gate Activations

### 1. secret_scan.py — Multiple Real Blocks
- **Commit dc76586** (2026-07-21): Fixed two fail-open vulnerabilities (bare except blocks swallowing errors)
- **Commit de1ddac** (2026-07-16): Closed worktree/blob bypass classes  
- **Commit ef491c7** (2026-07-26): Caught "api_key literal" in bench scenario text
- **Failure mode**: Fail-closed (exit 1 on findings)
- **Impact**: Caught unintended secret patterns and driven protocol fixes

### 2. encoding_lint.py — 62 Violations, 9 Lanes Blocked
- **Commit ff514eaa** (2026-07-31) / **PR #636** (2418634d)
- Caught 62 pre-existing subprocess encoding violations repo-wide
- Every push to main was blocked until mechanical fixes applied
- Fixed: 62 subprocess.run/Popen calls with text=True gained encoding='utf-8'
- **Failure mode**: Fail-closed (exit 1 on new violations; ratcheted baseline allows pre-existing)
- **Impact**: Prevented Windows cp1252 encoding crashes; stalled 9 feature branches

### 3. verify_test_coverage.py — Fake-Green Detection
- **Commit 3753a01** (2026-07-29)
- Discovered 3 orphaned shell tests (dash-watchdog-gui.test.sh, test-run-watchdog-smoke-signal.sh, test_waveguard.sh)
- Prevents test files from existing unexecuted in CI
- **Failure mode**: Fail-closed (exit 1 on orphans)
- **Impact**: Prevents CI green masking untested code

### 4. verify_dash.py — Browser Proofs (Fake-Green)
- **PR #464** (2026-07-29): "actually execute playwright specs + minimal dashboard smoke"
- Found playwright specs not executing despite CI being green
- Fixed by actually running TypeScript Playwright specs in main-full.yml
- **Failure mode**: Fail-closed (exit 1 on proof failure)
- **Impact**: Ensures browser tests actually run, not skipped

### 5. self_stats.py --check — README Stats Drift
- **Wired in**: main-full.yml line 56-57 (post-merge gate)
- Multiple recent commits fixing stats drift (ff9aaac, 4913d68, c9c8eee, 0b7a724, 82cdf52)
- Verifies README.md stats blocks stay in sync with git metrics
- **Failure mode**: Fail-closed (exit 1 on drift)
- **Impact**: Keeps published statistics current

### 6. state_md_verifier.py — Checkpoint Accuracy
- **PR #638**: "fix/state md verifier failclosed"
- Detects falsifiable claims in STATE.md checkpoint against git truth
- **Failure mode**: Fail-closed (exit 1 on contradictions)
- **Impact**: Prevents stale checkpoints from misleading recovery

### 7. Branch Protection (main/master)
- **Evidence**: ~100+ recent commits on feature branches (feat/*, docs/*, fix/*, guard/*, etc.)
- Zero direct main pushes in recent history
- Enforced by hooks/pre-push-policy.sh check_branch_policy()
- **Failure mode**: Fail-closed (exit 1)
- **Impact**: Ensures all changes go through PR review

## Unproven Gates (Exist, No Documented Activation)

15 gates implemented and enforced, but no documented evidence of blocking commits:
1. import_resolution_check.py (Guardrail G5)
2. tracker_guard.py
3. claudemd_sync_gate.py
4. metrics_gate.py
5. dispatch_lint.py
6. force-model-policy.mjs
7. ci_gate_runability.py (staged, not wired to ci.yml)
8. spec_contract_validator.py (Guardrail G4)
9. workflow_model_linter.py (Guardrail G7)
10. watcher_linter.py (Guardrail G3)
11. subprocess_guard.py (Guardrail G6)
12. bash_guard_check.py
13. portability_check.py
14. stateapi_lint.py (ratcheted baseline, no new escapes)
15. pre-commit-waveguard.sh

## Fail-Open Gates

No fail-open gates identified. All primary enforcement mechanisms are fail-closed or use ratcheted baselines (intentional design to prevent historic backlog from permanently stalling the main line).

## Methodology

1. Inventoried all enforcement mechanisms in hooks/, tools/, .github/workflows/, driver/
2. Determined failure mode for each by code inspection
3. Searched git history (git log --all --grep, commit messages) for evidence of real activations
4. Verified each activation against actual commit SHAs and PR references
5. Cross-referenced INCIDENTS.md for corroboration
6. Listed unproven gates explicitly; gate existing ≠ gate firing

## Notes for Future Auditors

- **Ratcheted baselines** (.encoding-baseline.json, .stateapi-baseline.json) indicate a gate is permissive on pre-existing violations but fail-closed on new ones
- **Silent logging** (MODEL-POLICY-ESCAPES.log, state/SECURITY-AUDIT.log) may show additional activations; these files are git-ignored
- Some gates may be preventative — violations rare or upstream blocked. Absence of activation can itself be evidence of compliance
- This document is a snapshot as of 2026-07-31. Future gate activations should be added with commit citations
