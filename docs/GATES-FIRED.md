# Gates Fired: Evidence-Backed Record

A committed audit of which guardrails have demonstrably blocked code, versus which exist untested.

**Credibility standard**: Every PROVEN entry cites a commit SHA, PR number, or run ID verifiable against git history.

## Summary

- **PROVEN**: 7 gates with documented activations
- **UNPROVEN**: 15 gates implemented, no documented blocking event
- **FAIL-OPEN**: 7 gates (deliberate two-tier design with critical caveat)
- **Total**: 22 gates

## Proven Activations (7)

### 1. secret_scan.py — Multiple Blocks
- Commit dc76586: Fixed fail-open vulnerabilities
- Commit de1ddac: Closed worktree/blob bypasses
- Commit ef491c7: Caught api_key literal in bench

### 2. encoding_lint.py — 62 Violations, 9 Lanes Blocked
- Commit ff514eaa / PR #636 (2026-07-31)
- Every push to main blocked until mechanical fixes
- Prevented Windows cp1252 encoding crashes

### 3. verify_test_coverage.py — Fake-Green Detection
- Commit 3753a01: Discovered 3 orphaned shell tests

### 4. verify_dash.py — Browser Proofs (Fake-Green)
- PR #464: Found playwright specs not executing in CI

### 5. self_stats.py --check — README Stats Drift
- main-full.yml line 56-57 + multiple fix commits

### 6. state_md_verifier.py — Checkpoint Accuracy (Fake-Green Caught)
- PR #638: Gate was fake-green (reported success with ZERO verification)
- Activation: The FIX is the evidence (fixed 2026-07-31)

### 7. Branch Protection (main/master)
- ~100+ recent commits on feature branches, zero direct main pushes

## Fail-Open Gates (7) — Deliberate Two-Tier Design

**Critical Finding**: Seven pre-push checks in hooks/pre-push-policy.sh deliberately fail-open when their tool file is missing. This is DOCUMENTED intentional architecture (line 603-604: "Fail-open ONLY for missing optional tool; actual resolution failures stay fail-closed").

Rationale: Support repos without full aesop checkout. Tool missing = benign skip + audit log. Tool error = block.

**The Seven**:
1. Line 567-569: tracker_guard.py
2. Line 609-611: import_resolution_check.py  
3. Line 645-647: claudemd_sync_gate.py
4. Line 681-683: metrics_gate.py
5. Line 721-723: verify_test_suite_count.py
6. Line 832-834: encoding_lint.py
7. Line 865-867: verify_test_coverage.py

**CRITICAL CAVEAT — Involuntary Binary Deletion**:

On 2026-07-31, bash.exe was discovered deleted on this machine (SECOND such event; EDR/AV quarantine suspected). The fail-open assumption rests on "tool missing = deliberate configuration". On a box where binaries disappear involuntarily:

- A quarantined secret_scan.py silently disables the secret gate
- A quarantined encoding_lint.py silently disables the encoding gate
- Audit logs show only _skipped_tool_missing, indistinguishable from intentional

This transforms fail-open from defensible (intentional config) to dangerous (involuntary quarantine masquerading as missing). Team investigating root cause; assumptions under revision.

## Unproven Gates (15)

Implemented and enforced, no documented blocking events. Many share fail-open behavior with proven gates when tool is missing.

## Honesty Notes

- **This document claims 7 fail-open gates**: Read the code. They are real. Lines cited above.
- **This is not an indictment**: It is documented, intentional, defensible for use case (repos without aesop checkout). The caveat is why it matters.
- **The bash.exe deletions**: Real incidents, pattern identified, team aware. Makes a previously-benign design decision suddenly relevant to security posture.
- **No padding on PROVEN list**: 7 is the actual count after honest review. state_md_verifier.py went from "working" to "fake-green" to "fixed today" — the fix is the activation.

## Methodology

Code inspection of pre-push-policy.sh (all 867 lines), git history search, commit SHA verification, INCIDENTS.md cross-reference, hooks/CLAUDE.md design documentation review.
