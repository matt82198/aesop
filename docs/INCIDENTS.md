
# Incidents

Operational failures tracked by class: detection, resolution, and source reference.
Rows marked (inferred) were classified from the commit subject because the commit carries no Incident-Class trailer.

**Summary**

- **ci-drift** (3, 3 inferred): CI workflow state out of sync (missing deps, env setup, tools)
- **conflict** (46, 46 inferred): Merge/rebase conflict, module shadowing, unintended override
- **doc-invented** (5, 5 inferred): Documentation made unverifiable claims, hallucinated counts or proofs
- **fake-green** (9, 7 inferred): Tests reported green but never ran or skipped real validation
- **flake** (7, 7 inferred): Test timing/race condition, deflake required, logical time or retry
- **gate-activation** (12, 12 inferred): Pre-push secret/verification gate caught an escape or bypass
- **stall** (8, 8 inferred): Agent/process hung or deadlocked, watchdog detected, restart required
- **test-pollution** (10, 10 inferred): Test config leaked between shards, state not isolated, mock pollution

| Class | What Happened | Resolution | Source |
| --- | --- | --- | --- |
| fake-green | fail-closed incident classification via commit trailer | * fix(tools): fail-closed incident classification via commit trailer | PR #960 |
| test-pollution (inferred) | live heartbeat pollution - tripwire fails on live-file change, wave... | Incident 2026-10-14: a test wrote the placeholder 1234567890 into t... | commit ac3e4b0b |
| fake-green | fail-closed incident classification via commit trailer | Incident classification no longer guesses from commit bodies. An | commit 4cf489dc |
| doc-invented (inferred) | doc-invented: lane claims relayed before CI verified them (unverifi... | ENFORCE: relay blocked until CI run id and merged state checked [ex... | commit 8205122c |
| doc-invented (inferred) | doc-invented: state words not bound to checks (unverifiable) | ENFORCE: state claims must cite a check id; lint fails otherwise [e... | commit 98eb1a3a |
| gate-activation (inferred) | gate-activation: orchestrator bypassed hook gates fired on two blocks | ENFORCE: deny = stop-and-report, machine-checked escape [exists: no] | commit b1f8a85a |
| gate-activation (inferred) | gate-activation: force-haiku hook gates fired a week, refused dispatch | ENFORCE: hook test asserts doc claim = hook action [exists: no] | commit 012c0c68 |
| gate-activation (inferred) | gate-activation: primary-branch-guard gates fired on substring match | ENFORCE: parse git verb + resolve toplevel, red-first tests [exists... | commit 14e8265d |
| gate-activation (inferred) | gate-activation: hooks fail open on malformed input (verification b... | ENFORCE: fail-closed conformance test for ALL hooks [exists: no] | commit 16acee99 |
| conflict (inferred) | conflict: scanner copies drifted brain vs aesop (module shadowing) | ENFORCE: one canonical scanner + cross-repo hash gate [exists: no] | commit 4c726a86 |
| stall (inferred) | stall: selfheal reads wrong heartbeat path (stall check blind) | ENFORCE: selfheal path parity test vs writer path [exists: no] | commit 5f762965 |
| stall (inferred) | stall: registered watchdog $HOME unresolved, parity check blind | ENFORCE: install writes absolute resolved command path [exists: no] | commit 6007cd72 |
| gate-activation (inferred) | gate-activation: no-grep-on-main-thread gates fired on worktree lanes | ENFORCE: grep guard detects main thread, allows worktree cwd [exist... | commit 63473495 |
| test-pollution (inferred) | test-pollution: /power never pulled; box 374 commits behind | ENFORCE: /power fails closed when origin is ahead [exists: no] | commit 78afeaf1 |
| fake-green (inferred) | fake-green: sync reported OK while primary on Aug branch, never-ran | ENFORCE: sync verifier asserts HEAD == origin default [exists: no] | commit 8367e286 |
| doc-invented (inferred) | doc-invented: STATE.md v0.9.0 claim unverifiable; v0.9.1 released | ENFORCE: state_md_verifier version-vs-tag check [exists: partial] | commit 90618eff |
| fake-green (inferred) | fake-green: session-end-log hook never-ran, schema-invalid output | ENFORCE: schema test on hook stdout at session end [exists: no] | commit c1dfd4c7 |
| gate-activation (inferred) | gate-activation: lane-contract hook gates fired, refuses not injects | ENFORCE: hook behaviour test vs documented contract [exists: no] | commit cb68df22 |
| fake-green (inferred) | fake-green: two hooks on disk never-ran, unwired in settings | ENFORCE: selftest blocks when on-disk hook is not wired [exists: pa... | commit da4b2d0c |
| conflict (inferred) | conflict: install check matched legacy task (module shadowing) | ENFORCE: divergence check keys on full task path [exists: no] | commit fb7ea54a |
| fake-green (inferred) | fake-green: PRs #934 and #932 armed for auto-merge, checks never-ran | ENFORCE: pr_sweep re-triggers or closes 0-check armed PRs [exists: no] | PR #934 |
| ci-drift (inferred) | post-#944 drift: windows aggregator misread docs-only skip on schedule | ENFORCE: scheduled docs-only skip fixture in CI [exists: partial] | PR #944 |
| test-pollution (inferred) | test-pollution: test_state_md_verifier tag test was not hermetic | ENFORCE: hermetic tag test on tmp origin, no real refs [exists: yes] | PR #943 |
| test-pollution (inferred) | test-pollution: test suite wrote 1234567890 into live heartbeat | ENFORCE: heartbeat tripwire fails test run on real-root write [exis... | commit 4427099c |
| stall (inferred) | stall: watchdog task unregistered for weeks, hung process unnoticed | ENFORCE: alarm when watchdog task absent or disabled [exists: partial] | commit 464b469c |
| gate-activation (inferred) | gates fired: G13 nested-def false positive blocked promotion | ENFORCE: red-first nested-def fixture for G13 [exists: no] | commit 604ff79d |
| stall (inferred) | stall: heartbeat skipped on BLOCKED cycles; fleet reads as hung pro... | ENFORCE: heartbeat written on every cycle incl. BLOCKED [exists: no] | commit 78a92595 |
| test-pollution (inferred) | test-pollution: unit test hit live bench and spent money | ENFORCE: unit tests fail on any live model call (tripwire) [exists:... | commit 9e7939ff |
| flake (inferred) | deflake: incident_report order flapped per run; test pins it | ENFORCE: test_deterministic_ordering + twice-run identity [exists: ... | PR #946 |
| gate-activation (inferred) | gate-activation: secret-scan gate false positives blocked backup | ENFORCE: doc-shaped fixture allowlist by fingerprint [exists: partial] | commit eb1079e1 |
| fake-green (inferred) | actually execute playwright specs | Fixes the long-standing issue where browser-proofs never ran any specs | commit 0ffdf725 |
| fake-green (inferred) | actually execute playwright specs | Fixes the long-standing issue where browser-proofs never ran any specs | commit 2bce0c34 |
| gate-activation (inferred) | fix: secret-scan gate closes worktree bypasses | Pre-push gate now detects file changes in commits | commit 4c2c1bd9 |
| ci-drift (inferred) | add pytest to main-full workflow (post-#450 drift) | The main-full.yml workflow was missing pytest | PR #450 |
| flake (inferred) | fix: deflake watchdog boundary tests with logical time | Root cause: timing race condition in boundary tests | commit f3edb0b5 |
| flake (inferred) | deflake windows-shard concurrency tests (proven under stress) | * fix(tests): deflake windows-shard concurrency tests (stress-proven) | PR #714 |
| flake (inferred) | deflake windows-shard concurrency tests (stress-proven) | Two Windows-shard flakes fired within 30 minutes on 2026-08-03. The... | commit 78a7e912 |
| flake (inferred) | fix: deflake test_compact_preserves_ttl_for_expiry on slow CI runners | The "held right now" assertion re-read the wall clock AFTER appendi... | commit 3d47aa99 |
| test-pollution (inferred) | fix: make CLAUDE.md linter scan tracked files, and stop test scaffo... | The runner failure was pollution, not a real violation. CI reported: | commit 33fb4a11 |
| conflict (inferred) | merge: resolve conflict with main (test suite count drift) | origin/main and refactor/transcript-health-modules both touched | commit fbb46c2b |
| gate-activation (inferred) | docs: GATES-FIRED.md — which guardrails actually fired, with citations | * docs: add GATES-FIRED.md — evidence-backed audit of guardrails | PR #665 |
| test-pollution (inferred) | ﻿fix: isolate test [redacted] root to prevent heartbeat file pollution | Problem: test_ui_demo_mode.py's TestDefaultModeUnaffected class did... | PR #645 |
| doc-invented (inferred) | fix: make escape-repro tests run + classify unresolvable PRs as UNV... | Replaced skipped merge-conflict tests with working fixtures via git... | commit b0303353 |
| conflict (inferred) | fix: merge main + resolve conflicts |  | commit f4b7fae2 |
| conflict (inferred) | fix: merge main + resolve conflicts |  | commit ee62d43d |
| conflict (inferred) | fix: merge main + resolve conflicts |  | commit 0f544b26 |
| conflict (inferred) | fix: merge main + resolve conflicts |  | commit 659f545b |
| conflict (inferred) | fix: merge main + resolve conflicts |  | commit 44ac3f24 |
| conflict (inferred) | fix: merge main + resolve conflicts |  | commit d2712292 |
| conflict (inferred) | fix: merge main + resolve conflicts |  | commit 1224229b |
| conflict (inferred) | fix: merge main + resolve conflicts |  | commit bd7dae0b |
| conflict (inferred) | fix: merge main + resolve conflicts |  | commit db09a540 |
| conflict (inferred) | fix: merge main + resolve conflicts |  | commit b70ddc7f |
| conflict (inferred) | fix: merge main, resolve tests/CLAUDE.md conflict | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit 343f6bee |
| conflict (inferred) | fix: merge main, resolve tests/CLAUDE.md conflict | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit 2aee17fd |
| conflict (inferred) | fix: merge main, resolve conflicts, fix test count | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit c92e52a4 |
| conflict (inferred) | fix: merge main, resolve conflicts, update domain CLAUDE.md docs | Merge main for commit_lint additions, resolve tests/CLAUDE.md and | commit 4acb4bd2 |
| conflict (inferred) | fix: merge main, resolve conflicts, add sleep-ok comments | Merge main for commit_lint additions, resolve tests/CLAUDE.md and | commit 7b31b55d |
| conflict (inferred) | merge: resolve conflict with main (use current branch version) |  | commit e8048663 |
| conflict (inferred) | merge: resolve conflicts with main (use current branch versions) |  | commit 2287c87e |
| conflict (inferred) | merge: resolve test count conflict with main |  | commit cfc25e15 |
| conflict (inferred) | merge: resolve remote conflict (187 suites correct) | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit f488c876 |
| conflict (inferred) | merge: resolve test count conflict (184+1+2=187) | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit 589fb8c4 |
| conflict (inferred) | fix: resolve merge conflicts with main | Accept main's version of tests/CLAUDE.md (Python suite count and | commit ae548473 |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit f75291ec |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit a4966d28 |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit cdce095f |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 3a7abfed |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit a33a757e |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 921843c1 |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 7f9b965a |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 7beffc69 |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit ccad043b |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit df7137cb |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit cd3de6ef |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit bb7dcc26 |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 7cda7c2c |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 56cace7f |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 138ca671 |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md | Merged origin/main into feat/watcher-linter. Conflict in tests/CLAU... | commit 379476e5 |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md | Merged Security section test_agent_prompt_hygiene from origin/main | commit 5982c03d |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit c1faa57d |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md | Accept origin/main's version which includes both test_agent_prompt_... | commit b0dd937b |
| conflict (inferred) | fix: resolve merge conflict with main in tests/CLAUDE.md | Merged origin/main into feat/tracker-autoclose; kept origin/main's ... | commit 6439a87f |
| conflict (inferred) | merge: resolve tests/CLAUDE.md count conflict with main | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 5ad5de02 |
| fake-green (inferred) | actually execute playwright specs + minimal dashboard smoke | * ci(browser-proofs): actually execute playwright specs + minimal d... | PR #464 |
| ci-drift (inferred) | add pytest to main-full workflow (post-#450 drift) | The main-full.yml workflow was missing pytest from the Python depen... | PR #450 |
| conflict (inferred) | restore original wave_scheduler spec + add lane_scheduler pilot | Reconciliation fix: restored the full 898-line test_wave_scheduler.... | commit 8cb11f59 |
| flake (inferred) | fix: deflake watchdog boundary tests with logical time | Root cause (FLAKE 4): TestWatchdogRedBoundary writes heartbeats with | PR #432 |
| flake (inferred) | fix: deflake windows-timing tests (tracker_csrf readiness, rs3 leas... | * fix: deflake tracker_csrf tests with server readiness polling | PR #427 |
| test-pollution (inferred) | fix: seated test canned evidence string->array (post-1.6 shape); te... | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 2f1440c8 |
| doc-invented (inferred) | docs: correct hallucinated 0.3.0 CHANGELOG entries; README release ... | The auto-merged #332 section described test_battery as 'energy-aware | commit f8b69473 |
| stall (inferred) | fix: stall_check containment resolves both sides (runner 8.3 regres... | stall_check: verify_path_containment compared an UNRESOLVED short-form | commit 30583b81 |
| stall (inferred) | fix: stall_check.py—sanitize agent_id to prevent path traversal (CW... | Implement allowlist validation and defense-in-depth path containmen... | commit 44447f3d |
| test-pollution (inferred) | fix: wave_loop tests run in module tmpdir (cwd-pollution root cause... | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 3ec6547c |
| stall (inferred) | ci: run `ci` on every PR (fix docs-only deadlock); land held judgme... | Removes the job-level docs-only `if:` skip on `ci`. A skipped matri... | commit 00649b75 |
| gate-activation (inferred) | fix: secret-scan gate closes worktree/blob bypasses (wave-25) | --staged and --range now scan the actual git objects being | commit de1ddac7 |
| gate-activation (inferred) | fix: restore secret_scan push gate to detect files changed in commits | Fixes inert push gate that failed to scan secrets because git diff ... | commit c8b3c257 |
| test-pollution (inferred) | isolate tracker writes to tempdir; guard verify_dash against pollut... | - tools/verify_dash.py now explicitly sets AESOP_STATE_ROOT to temp... | commit 29356d85 |
| stall (inferred) | stall_check.py — silent-hang detection for the agent watchdog (wave... | Adds automated detection of stalled agents by scanning transcript m... | commit 17010688 |

<!-- Latest incident: 2026-10-15T01:14:50Z -->
