
# Incidents

Operational failures tracked by class: detection, resolution, and source reference.
Rows marked (inferred) were classified from the commit subject because the commit carries no Incident-Class trailer.

**Summary**

- **ci-drift** (1, 1 inferred): CI workflow state out of sync (missing deps, env setup, tools)
- **conflict** (21, 21 inferred): Merge/rebase conflict, module shadowing, unintended override
- **doc-invented** (2, 2 inferred): Documentation made unverifiable claims, hallucinated counts or proofs
- **fake-green** (1, 1 inferred): Tests reported green but never ran or skipped real validation
- **flake** (4, 4 inferred): Test timing/race condition, deflake required, logical time or retry
- **gate-activation** (4, 4 inferred): Pre-push secret/verification gate caught an escape or bypass
- **stall** (4, 4 inferred): Agent/process hung or deadlocked, watchdog detected, restart required
- **test-pollution** (5, 5 inferred): Test config leaked between shards, state not isolated, mock pollution

| Class | What Happened | Resolution | Source |
| --- | --- | --- | --- |
| gate-activation (inferred) | record auto_merge caller-safe fix path and delta secret gate | record auto_merge caller-safe fix path and delta secret gate | commit a969fc4c |
| flake (inferred) | deflake windows-shard concurrency tests (proven under stress) | * fix(tests): deflake windows-shard concurrency tests (stress-proven) | PR #714 |
| flake (inferred) | fix: deflake test_compact_preserves_ttl_for_expiry on slow CI runners | The "held right now" assertion re-read the wall clock AFTER appendi... | commit 3d47aa99 |
| test-pollution (inferred) | fix: make CLAUDE.md linter scan tracked files, and stop test scaffo... | The runner failure was pollution, not a real violation. CI reported: | commit 33fb4a11 |
| gate-activation (inferred) | docs: GATES-FIRED.md — which guardrails actually fired, with citations | * docs: add GATES-FIRED.md — evidence-backed audit of guardrails | PR #665 |
| test-pollution (inferred) | ﻿fix: isolate test [redacted] root to prevent heartbeat file pollution | Problem: test_ui_demo_mode.py's TestDefaultModeUnaffected class did... | PR #645 |
| doc-invented (inferred) | fix: make escape-repro tests run + classify unresolvable PRs as UNV... | Replaced skipped merge-conflict tests with working fixtures via git... | commit b0303353 |
| conflict (inferred) | fix: merge main + resolve conflicts | fix: merge main + resolve conflicts | commit f4b7fae2 |
| conflict (inferred) | fix: merge main + resolve conflicts | fix: merge main + resolve conflicts | commit ee62d43d |
| conflict (inferred) | fix: merge main + resolve conflicts | fix: merge main + resolve conflicts | commit 0f544b26 |
| conflict (inferred) | fix: merge main + resolve conflicts | fix: merge main + resolve conflicts | commit 659f545b |
| conflict (inferred) | fix: merge main + resolve conflicts | fix: merge main + resolve conflicts | commit 44ac3f24 |
| conflict (inferred) | fix: merge main + resolve conflicts | fix: merge main + resolve conflicts | commit d2712292 |
| conflict (inferred) | fix: merge main + resolve conflicts | fix: merge main + resolve conflicts | commit 1224229b |
| conflict (inferred) | fix: merge main + resolve conflicts | fix: merge main + resolve conflicts | commit bd7dae0b |
| conflict (inferred) | fix: merge main + resolve conflicts | fix: merge main + resolve conflicts | commit db09a540 |
| conflict (inferred) | fix: merge main + resolve conflicts | fix: merge main + resolve conflicts | commit b70ddc7f |
| conflict (inferred) | fix: merge main, resolve tests/CLAUDE.md conflict | fix: merge main, resolve tests/CLAUDE.md conflict | commit 343f6bee |
| conflict (inferred) | fix: merge main, resolve tests/CLAUDE.md conflict | fix: merge main, resolve tests/CLAUDE.md conflict | commit 2aee17fd |
| conflict (inferred) | fix: merge main, resolve conflicts, fix test count | fix: merge main, resolve conflicts, fix test count | commit c92e52a4 |
| conflict (inferred) | fix: merge main, resolve conflicts, update domain CLAUDE.md docs | Merge main for commit_lint additions, resolve tests/CLAUDE.md and | commit 4acb4bd2 |
| conflict (inferred) | fix: merge main, resolve conflicts, add sleep-ok comments | Merge main for commit_lint additions, resolve tests/CLAUDE.md and | commit 7b31b55d |
| conflict (inferred) | merge: resolve conflict with main (use current branch version) | merge: resolve conflict with main (use current branch version) | commit e8048663 |
| conflict (inferred) | merge: resolve conflicts with main (use current branch versions) | merge: resolve conflicts with main (use current branch versions) | commit 2287c87e |
| conflict (inferred) | merge: resolve test count conflict with main | merge: resolve test count conflict with main | commit cfc25e15 |
| conflict (inferred) | merge: resolve remote conflict (187 suites correct) | merge: resolve remote conflict (187 suites correct) | commit f488c876 |
| conflict (inferred) | merge: resolve test count conflict (184+1+2=187) | merge: resolve test count conflict (184+1+2=187) | commit 589fb8c4 |
| conflict (inferred) | fix: resolve merge conflicts with main | Accept main's version of tests/CLAUDE.md (Python suite count and | commit ae548473 |
| fake-green (inferred) | actually execute playwright specs + minimal dashboard smoke | * ci(browser-proofs): actually execute playwright specs + minimal d... | PR #464 |
| ci-drift (inferred) | add pytest to main-full workflow (post-#450 drift) | The main-full.yml workflow was missing pytest from the Python depen... | PR #450 |
| flake (inferred) | fix: deflake watchdog boundary tests with logical time | Root cause (FLAKE 4): TestWatchdogRedBoundary writes heartbeats with | PR #432 |
| flake (inferred) | fix: deflake windows-timing tests (tracker_csrf readiness, rs3 leas... | * fix: deflake tracker_csrf tests with server readiness polling | PR #427 |
| test-pollution (inferred) | fix: seated test canned evidence string->array (post-1.6 shape); te... | fix: seated test canned evidence string->array (post-1.6 shape); te... | commit 2f1440c8 |
| doc-invented (inferred) | docs: correct hallucinated 0.3.0 CHANGELOG entries; README release ... | The auto-merged #332 section described test_battery as 'energy-aware | commit f8b69473 |
| stall (inferred) | fix: stall_check containment resolves both sides (runner 8.3 regres... | stall_check: verify_path_containment compared an UNRESOLVED short-form | commit 30583b81 |
| stall (inferred) | fix: stall_check.py—sanitize agent_id to prevent path traversal (CW... | Implement allowlist validation and defense-in-depth path containmen... | commit 44447f3d |
| test-pollution (inferred) | fix: wave_loop tests run in module tmpdir (cwd-pollution root cause... | fix: wave_loop tests run in module tmpdir (cwd-pollution root cause... | commit 3ec6547c |
| stall (inferred) | ci: run `ci` on every PR (fix docs-only deadlock); land held judgme... | Removes the job-level docs-only `if:` skip on `ci`. A skipped matri... | commit 00649b75 |
| gate-activation (inferred) | fix: secret-scan gate closes worktree/blob bypasses (wave-25) | --staged and --range now scan the actual git objects being | commit de1ddac7 |
| gate-activation (inferred) | fix: restore secret_scan push gate to detect files changed in commits | Fixes inert push gate that failed to scan secrets because git diff ... | commit c8b3c257 |
| test-pollution (inferred) | isolate tracker writes to tempdir; guard verify_dash against pollut... | - tools/verify_dash.py now explicitly sets AESOP_STATE_ROOT to temp... | commit 29356d85 |
| stall (inferred) | stall_check.py — silent-hang detection for the agent watchdog (wave... | Adds automated detection of stalled agents by scanning transcript m... | commit 17010688 |

<!-- Latest incident: 2026-10-15T19:51:08-05:00 -->
