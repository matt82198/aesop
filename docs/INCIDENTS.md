
# Incidents

Operational failures tracked by class: detection, resolution, and source reference.

**Summary**

- **ci-drift** (4): CI workflow state out of sync (missing deps, env setup, tools)
- **conflict** (84): Merge/rebase conflict, module shadowing, unintended override
- **doc-invented** (6): Documentation made unverifiable claims, hallucinated counts or proofs
- **fake-green** (7): Tests reported green but never ran or skipped real validation
- **flake** (7): Test timing/race condition, deflake required, logical time or retry
- **gate-activation** (56): Pre-push secret/verification gate caught an escape or bypass
- **stall** (37): Agent/process hung or deadlocked, watchdog detected, restart required
- **test-pollution** (25): Test config leaked between shards, state not isolated, mock pollution

| Class | What Happened | Resolution | Source |
| --- | --- | --- | --- |
| gate-activation | feat: port dash-extra.mjs and secret_scan.py for complete public te... | Adds missing essential dependencies: | commit a27c4f8a |
| gate-activation | Setup npm OIDC trusted publishing via GitHub Actions | Add publish.yml workflow that triggers on GitHub Release, uses OIDC... | commit da7f8521 |
| gate-activation | docs: harden rule docs to express canonical principles | - CARDINAL-RULES.md §1: strengthen orchestrator role (main-thread-o... | commit 858ac08e |
| gate-activation | installable pre-push policy hook + audit ledger (pillar 2) | hooks/pre-push-policy.sh enforces branch discipline (no main/master... | commit ff692a18 |
| gate-activation | hash-chained audit log + --verify-audit-log; local-gate docs (P2) | Each SECURITY-AUDIT.log event carries prev_hash (SHA-256 of prior l... | commit fb38d59a |
| stall | correct targetDir parsing; refuse symlinked .git/hooks (P1+MED) | P1 JS fix: targetDir parser was using wrong array indices to determ... | commit a21c5fcc |
| stall | drain-free stdio + exit-event for proposals concurrent-race (Linux ... | The concurrent-race test (real accept subprocess) hung on Linux CI,... | commit fb948d1b |
| stall | drain-free stdio + exit-event for proposals concurrent-race (Linux ... | The concurrent-race test (real accept subprocess) hung on Linux CI,... | commit e0e11970 |
| conflict | Merge pull request #67 into feat/port-ops-tools (PR #68) | Resolve merge conflict in tools/CLAUDE.md: Union of both PRs — reta... | PR #67 |
| gate-activation | test(tools): coverage for 9 ported ops tools (wave-8) | Add comprehensive pytest suites for recently merged tools: | commit d692426d |
| stall | stall_check.py — silent-hang detection for the agent watchdog (wave... | Adds automated detection of stalled agents by scanning transcript m... | commit 17010688 |
| test-pollution | isolate tracker writes to tempdir; guard verify_dash against pollut... | - tools/verify_dash.py now explicitly sets AESOP_STATE_ROOT to temp... | commit 29356d85 |
| stall | Merge pull request #100 from matt82198/feat/wave12-stall-check | feat(tools): stall_check.py — silent-hang detection for the watchdo... | PR #100 |
| test-pollution | Merge pull request #101 from matt82198/fix/wave12-tracker-test-isol... | fix(tests): isolate tracker writes; guard verify_dash from pollutin... | PR #101 |
| stall | wire dash-watchdog-gui tests, dedup shell test steps, add metrics gate | - Add tests/dash-watchdog-gui.test.sh to CI step (5 real assertions... | commit 78602d06 |
| stall | Merge pull request #108 from matt82198/fix/wave13-test-ci-machinery | ci/tools: wire orphan suite, dedup self-tests, metrics gate, stall_... | PR #108 |
| gate-activation | wave-14 U1: dashboard foundation — Vite/React/TS scaffold, design s... | - ui/web/ scaffold: Vite 8.1.1 + React 19.2.7 + TS 6.0.2 + Vitest 4... | commit 2cb71c21 |
| conflict | Merge wave-14 U4 (Overview view pack) with U7 (Cost view pack) | Resolved conflicts by unioning both sides' intent: | commit 00400ca3 |
| stall | fix: wrap bare test functions in unittest.TestCase for CI collection | test_stall_check.py defined 7 pytest-style module-level test functions | commit 2d28b523 |
| gate-activation | fix: bin/cli.js scaffolder manifest — fix three defects breaking np... | (a) filesToCopy omitted state_store/, skills/, mcp/, scan/ — ui/col... | commit 238d8db6 |
| gate-activation | fix: restore secret_scan push gate to detect files changed in commits | Fixes inert push gate that failed to scan secrets because git diff ... | commit c8b3c257 |
| stall | Merge pull request #157 from matt82198/revert/rogue-stall-check-push | revert: rogue direct-to-main push 2d28b52 (stall-check TestCase wrap) | PR #157 |
| stall | fix/test: wrap bare test functions in unittest.TestCase | 7 module-level test functions in tests/test_stall_check.py now prop... | commit e9981815 |
| gate-activation | Merge remote-tracking branch 'origin/feat/wave19-secretscan-push-ga... |  | commit 723a3d9a |
| stall | Merge remote-tracking branch 'origin/feat/wave19-stall-check-v2' in... |  | commit 6a8265f9 |
| gate-activation | fix: unquoted-assignment secret pattern no longer flags code expres... | The generic_secret_assignment unquoted branch matched ANY non-space... | commit 83fd9e93 |
| stall | Merge pull request #158 from matt82198/integration/wave19-merge-train | Wave 19: secret-scan hardening, backup-fleet, ci-merge-wait, host-h... | PR #158 |
| gate-activation | fix: secret-scan gate closes worktree/blob bypasses (wave-25) | --staged and --range now scan the actual git objects being | commit de1ddac7 |
| gate-activation | docs: wave-25 close checkpoint (STATE.md, AUDIT-BACKLOG.md) | STATE.md: Update phase from wave-7 to wave-26-credibility-safety. W... | commit a974c3ec |
| stall | test: add orchestration-core decision-logic tests (wave-26) | Targets the aesop-owned decision logic behind the orchestration cor... | commit 5234dd02 |
| stall | ci: run `ci` on every PR (fix docs-only deadlock); land held judgme... | Removes the job-level docs-only `if:` skip on `ci`. A skipped matri... | commit 00649b75 |
| stall | Merge pull request #171 from matt82198/feat/wave29-ci-docs-fix | ci: fix docs-only merge deadlock + land judgment-results doc | PR #171 |
| stall | docs: refresh STATE.md to wave-29 (waves 25-29 shipped) + verify do... | Pure-docs change — doubles as the proof that the wave-29 CI fix res... | commit b88c2f36 |
| stall | release: cut 0.1.0-rc.1 release candidate | Bump version 0.1.0-beta.6 -> 0.1.0-rc.1 and prepare release materials: | commit 4b68393d |
| doc-invented | docs: tell the 0.1.0-rc.1 self-shipped milestone, honestly | Aesop reached its first release candidate by running its own wave loop | commit 3af0c714 |
| gate-activation | wave rc.6-obs: transcript digest, CLAUDE.md linter, CONTRIBUTING, b... | 4 new-file items (orchestrator straggler-takeover: workflow spun on... | commit 0481fbc4 |
| test-pollution | Merge pull request #207 from matt82198/feat/wave-rc10 | wave rc.10: state_store shard isolation, MCP cost-trend tools, wave... | PR #207 |
| gate-activation | feat: transcript-sampled-bench — extract coding tasks from real tra... | Phase 1 (sampler + schema + tests; no grading run): | commit 4d642dc8 |
| gate-activation | docs: multi-model currency sweep — AgentDriver Phase 1-3 documentation | Add missing driver subsystem files (openai_compatible_driver.py, ba... | commit 35ff2050 |
| gate-activation | fix: secret_scan.py—fail CLOSED on file/git read errors (P1 security) | The pre-push secret-detection gate had two fail-open vulnerabilitie... | commit dc765865 |
| test-pollution | feat: audit report generator — deterministic markdown audit aggregator | Implements tools/audit_report.py to deterministically aggregate mac... | commit 5a417223 |
| test-pollution | fix: wave_loop tests run in module tmpdir (cwd-pollution root cause... | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 3ec6547c |
| stall | feat: aesop reproduce — offline verification suite | Add `aesop reproduce` command that runs the offline test suite loca... | commit 427bbc0c |
| gate-activation | feat: bench transcript-sampled judgment task set (N=150, wave-27 WS2a) | Extract 150 judgment/analysis tasks from real aesop fleet transcripts, | commit 763b2b18 |
| stall | fix: reproduce—distinguish expected pre-init findings from real fai... | Installed mode now classifies doctor check failures: | commit cb088ecd |
| stall | feat: stall detection enhancements—activity predicates + recovery a... | Item 1: Add --active-from flag to stall_check.py for optional activ... | commit 8ea07fc1 |
| stall | fix: stall_check.py—sanitize agent_id to prevent path traversal (CW... | Implement allowlist validation and defense-in-depth path containmen... | commit 44447f3d |
| stall | Merge remote-tracking branch 'origin/fix/g2-stallcheck-traversal' i... |  | commit 3b688446 |
| stall | fix: stall_check containment resolves both sides (runner 8.3 regres... | stall_check: verify_path_containment compared an UNRESOLVED short-form | commit 30583b81 |
| test-pollution | fix: the ORIGINAL identity polluter — hook --test set user.name uns... | hooks/pre-push-policy.sh Test 4 wrote 'John "Jack" Doe' into whatever | commit 266f04d3 |
| doc-invented | docs: correct hallucinated 0.3.0 CHANGELOG entries; README release ... | The auto-merged #332 section described test_battery as 'energy-aware | commit f8b69473 |
| stall | fix: address 6 verified P1/P2/P3 findings in Windows task installer | P1 CORE — run-hidden.vbs exit-code propagation | commit 2e494ba5 |
| gate-activation | docs: systems/resilience positioning — The Aesop Hypothesis in-repo... | - NEW: docs/THE-AESOP-HYPOTHESIS.md (~1600 words) — design philosop... | commit 4ae375ae |
| test-pollution | fix: seated test canned evidence string->array (post-1.6 shape); te... | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 2f1440c8 |
| test-pollution | chore: wave-close bookkeeping for 0.4.0 (tracker reconcile + BUILDLOG) | - Tracker reconciliation: marked 7 items DONE (HS-0/1/2 shipped ite... | PR #382 |
| test-pollution | chore: /refinesystem close-out — STATE.md NEXT STEPS (0.4.0 clean @... | Updates STATE.md with finalized status from /refinesystem close-out: | PR #393 |
| test-pollution | fix: isolate frontier-slice test output to gitignored temp location | The frontier-slice benchmark harness was writing results to the com... | PR #400 |
| test-pollution | release: stage v0.4.1 (version bump + changelog) | Onboarding overhaul, CI/test hardening, stats truth-sync, bench exp... | PR #415 |
| gate-activation | bench: N=100 extension (ft61-ft100) + Amendment 2 + BENCH_API_KEY H... | * bench: N=100 extension (ft61-ft100) + Amendment 2 + BENCH_API_KEY... | PR #421 |
| gate-activation | bench/seam-s-dispatcher: REWORK with REAL repair loop (fidelity fix) | CRITICAL FIXES addressing coordinator fidelity review: | commit 32e2f324 |
| gate-activation | bench/seam-s-dispatcher: STUDY DESIGN FIX - uniform repair budget a... | CRITICAL FIX addressing coordinator's round-2 review: | commit d2081798 |
| gate-activation | bench: v5 tool-call answer channel for all 130 tasks (Amendment 4, ... | * bench: token_sets.py helper for v5 tool-use token extraction | PR #426 |
| flake | fix: deflake windows-timing tests (tracker_csrf readiness, rs3 leas... | * fix: deflake tracker_csrf tests with server readiness polling | PR #427 |
| gate-activation | bench: seam-discrimination study — tasks, runners, integration (K=1... | * bench: seeded-defect task st05 (config default override, 2-hop) -... | PR #429 |
| flake | fix: deflake watchdog boundary tests with logical time | Root cause (FLAKE 4): TestWatchdogRedBoundary writes heartbeats with | PR #432 |
| test-pollution | context-eng UI: spec-sharpness + file-scope views (read-only) | * feat(ui): context-engineering lane C — three read-only dashboard ... | PR #444 |
| gate-activation | agent-forensics --help exit-0, reconstitute python3\|\|python + sheba... | - tools/agent-forensics.sh: --help/-h now prints usage to stdout an... | PR #448 |
| conflict | restore original wave_scheduler spec + add lane_scheduler pilot | Reconciliation fix: restored the full 898-line test_wave_scheduler.... | commit 8cb11f59 |
| stall | zero-friction reproduce path [d49b4d16 WS1] | * feat(onboarding): zero-friction reproduce path [d49b4d16 WS1] | PR #458 |
| ci-drift | add pytest to main-full workflow (post-#450 drift) | The main-full.yml workflow was missing pytest from the Python depen... | PR #450 |
| stall | reproduce context detection + graceful React-step gating | * fix(cli): reproduce context detection + graceful React-step gating | PR #463 |
| fake-green | actually execute playwright specs + minimal dashboard smoke | * ci(browser-proofs): actually execute playwright specs + minimal d... | PR #464 |
| ci-drift | drop wave count from surfaces + scheduled README stats refresh | * feat(stats): drop wave count from surfaces + scheduled README sta... | PR #465 |
| conflict | Merge origin/main into feat/ui-acceptance-criteria-authoring | Resolve conflicts: | commit cbd040eb |
| test-pollution | incident-log generator — failure taxonomy from the committed record | Implements tools/incident_report.py that mines git history for oper... | commit 0902d05d |
| gate-activation | docs: claim honesty — topology/tier split, scope boundaries, crash-... | * docs: claim honesty — topology/tier split, scope boundaries, cras... | PR #468 |
| gate-activation | adversarial-review phase -- opt-out, sampled, repair-routed | Implements Phase 5.75 as a standard opt-out wave phase between exac... | commit 72c24c55 |
| gate-activation | chaos-wave resilience harness + auto-generated reliability report | Implements a fault-injection harness that runs a small OFFLINE wave... | commit 819a1854 |
| conflict | Merge origin/feat/ui-acceptance-criteria-authoring (resolve dist co... |  | commit 9b5134a9 |
| test-pollution | feat: INCIDENT-RESPONSE-SLA.md computed from real incident data | New docs/INCIDENT-RESPONSE-SLA.md analyzes 48 operational incidents | PR #497 |
| stall | docs: add ADOPTION-CHECKLIST.md with per-step acceptance criteria | Provides PORTING/install companion guide with actual commands and C... | PR #501 |
| gate-activation | feat: add Guardrail G4 spec-contract validator for agent dispatch c... | AST-scans driver/*.py, monitor/*.py, tools/*.py for agent-dispatch ... | commit ed07b9c1 |
| test-pollution | feat: adversarial trap-test suite for recurring incident regression... | Add tests/test_traps.py with 14 mechanically checkable regression t... | PR #5 |
| conflict | Merge origin/main into feat/tracker-autoclose | Resolved tests/CLAUDE.md suite-count conflict via verify_test_suite... | commit b7124d56 |
| conflict | Merge origin/main into feat/guardrail-g2-test-coverage | Resolved tests/CLAUDE.md suite-count conflict via verify_test_suite... | commit 935fb79d |
| conflict | Merge origin/main into feat/subprocess-guard | Resolved tests/CLAUDE.md suite-count conflict via verify_test_suite... | commit a5c70f0b |
| conflict | Merge origin/main into feat/spec-contract-validator | Resolved tests/CLAUDE.md suite-count conflict via verify_test_suite... | commit 496e1d9c |
| conflict | merge: resolve tests/CLAUDE.md count conflict with main | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 5ad5de02 |
| gate-activation | feat: CODE-TASTE.md walkthrough of key design decisions | Annotated design-rationale document explaining the WHY behind: | PR #496 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Merged origin/main into feat/tracker-autoclose; kept origin/main's ... | commit 6439a87f |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Accept origin/main's version which includes both test_agent_prompt_... | commit b0dd937b |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit c1faa57d |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Merged Security section test_agent_prompt_hygiene from origin/main | commit 5982c03d |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Merged origin/main into feat/watcher-linter. Conflict in tests/CLAU... | commit 379476e5 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 138ca671 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 56cace7f |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 7cda7c2c |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit bb7dcc26 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit cd3de6ef |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit df7137cb |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit ccad043b |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 7beffc69 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 7f9b965a |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit 921843c1 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit a33a757e |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 3a7abfed |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit cdce095f |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
 | commit a4966d28 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit f75291ec |
| gate-activation | integration: guardrails batch — spec-contract linter, G1 tracker-au... | * feat: spec-contract-linter gate for domain CLAUDE.md validation | PR #518 |
| conflict | fix: resolve merge conflicts with main | Accept main's version of tests/CLAUDE.md (Python suite count and | commit ae548473 |
| stall | ci: wire six guardrail gates as real enforcement (G3, G4, G6, track... | * feat(tools): add ratchet baseline mode to subprocess_guard (G6) a... | PR #525 |
| gate-activation | docs: add Mermaid architecture diagram to README | Replaces the ASCII wave-cycle-diagram.txt with an embedded Mermaid ... | commit 44f0e207 |
| conflict | merge: resolve test count conflict (184+1+2=187) | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit 589fb8c4 |
| conflict | merge: resolve remote conflict (187 suites correct) | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit f488c876 |
| conflict | Merge main (post-568): resolve test count conflict | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit cfe6a504 |
| conflict | Merge main (post-568): resolve conflicts in tests/tools CLAUDE.md | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit 512370a9 |
| conflict | Merge main (post-568): resolve test count conflict | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit 101c6f4c |
| conflict | merge: resolve test count conflict with main |  | commit cfc25e15 |
| conflict | merge: resolve conflicts with main (use current branch versions) |  | commit 2287c87e |
| conflict | merge: resolve conflict with main (use current branch version) |  | commit e8048663 |
| conflict | fix: merge main, resolve conflicts, add sleep-ok comments | Merge main for commit_lint additions, resolve tests/CLAUDE.md and | commit 7b31b55d |
| conflict | fix: merge main, resolve conflicts, update domain CLAUDE.md docs | Merge main for commit_lint additions, resolve tests/CLAUDE.md and | commit 4acb4bd2 |
| conflict | fix: merge main, resolve conflicts, fix test count | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit c92e52a4 |
| conflict | fix: merge main, resolve tests/CLAUDE.md conflict | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit 2aee17fd |
| conflict | fix: merge main, resolve tests/CLAUDE.md conflict | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
 | commit 343f6bee |
| conflict | fix: merge main + resolve conflicts |  | commit b70ddc7f |
| conflict | fix: merge main + resolve conflicts |  | commit db09a540 |
| conflict | fix: merge main + resolve conflicts |  | commit bd7dae0b |
| conflict | fix: merge main + resolve conflicts |  | commit 1224229b |
| conflict | fix: merge main + resolve conflicts |  | commit d2712292 |
| conflict | fix: merge main + resolve conflicts |  | commit 44ac3f24 |
| conflict | fix: merge main + resolve conflicts |  | commit 659f545b |
| conflict | fix: merge main + resolve conflicts |  | commit 0f544b26 |
| conflict | fix: merge main + resolve conflicts |  | commit ee62d43d |
| conflict | fix: merge main + resolve conflicts |  | commit f4b7fae2 |
| gate-activation | feat: add init_project.py scaffolder and wire into CLI | Add tools/init_project.py -- project initialization scaffolder that | commit b53fd165 |
| gate-activation | feat: add dispatch_lint for merge automation enforcement | Enforces merge automation rule in code: agents must use tools/auto_... | commit 55f5adf8 |
| gate-activation | fix: exclude dispatch_lint.py from admin-flag trap test | dispatch_lint.py is the enforcement tool that detects --admin patte... | commit ba61d2dd |
| conflict | build(guardrail): add STATE.md checkpoint-accuracy verifier (guardr... | Implements tools/state_md_verifier.py: catches false progress claim... | PR #1 |
| doc-invented | fix: make escape-repro tests run + classify unresolvable PRs as UNV... | Replaced skipped merge-conflict tests with working fixtures via git... | commit b0303353 |
| conflict | fix: update test suite count to match actual Python tests (191 suites) | Test count in tests/CLAUDE.md was out of sync with actual test files. | commit 3f35f45b |
| conflict | Merge main into fix/auto-merge-shell-injection - resolve tools/CLAU... |  | commit 1605be9d |
| gate-activation | fix: G5 commit-scope verification + pre-push gate wiring | Two fixes to the CLAUDE.md synchronization guardrail (G5): | commit c56a2b6e |
| conflict | Merge origin/main into docs/wave-currency | Resolved conflict in tools/CLAUDE.md by accepting origin/main's mor... | commit dcc33e79 |
| test-pollution | fix: add consistency fixes to instance_manager.py | Resolves three analyst-lens consistency gaps in multi-instance coor... | PR #606 |
| conflict | fix: update test suite count to match actual Python tests (191 suites) | Test count in tests/CLAUDE.md was out of sync with actual test files. | commit ad84ef71 |
| conflict | Merge feat/multibox-lease-claims (resolve conflicts, keep P0 fixes) |  | commit 0d65388c |
| gate-activation | fix: guard hook interpreters from silent failure | Adds interpreter health check (hook_preflight.py) to detect broken or | commit b84e640f |
| conflict | Merge main into feature/reconcile-test-counts to resolve conflicts |  | commit 19bac22c |
| gate-activation | fix: guard hook interpreters from silent failure | Adds interpreter health check (hook_preflight.py) to detect broken or | commit 15dc2e3a |
| test-pollution | ﻿fix: isolate test conductor3 root to prevent heartbeat file pollution | Problem: test_ui_demo_mode.py's TestDefaultModeUnaffected class did... | PR #645 |
| gate-activation | docs: GATES-FIRED.md — which guardrails actually fired, with citations | * docs: add GATES-FIRED.md — evidence-backed audit of guardrails | PR #665 |
| conflict | merge: resolve conflict with main (test suite count drift) | origin/main and refactor/transcript-health-modules both touched | commit fbb46c2b |
| test-pollution | fix: make CLAUDE.md linter scan tracked files, and stop test scaffo... | The runner failure was pollution, not a real violation. CI reported: | commit 33fb4a11 |
| flake | fix: deflake test_compact_preserves_ttl_for_expiry on slow CI runners | The "held right now" assertion re-read the wall clock AFTER appendi... | commit 3d47aa99 |
| conflict | Merge branch 'origin/main' - resolve tests/CLAUDE.md suite count co... |  | commit c75a2d6f |
| stall | guard(ci): consume is_docs_only — skip non-required jobs on docs-on... | C1 four core changes: | commit 85447580 |
| stall | guard: fix concurrency inversion + fail-closed CANCELLED handling (Q0) | Two live correctness defects on the merge path, plus two CI-cost fi... | commit 2f613037 |
| flake | deflake windows-shard concurrency tests (stress-proven) | Two Windows-shard flakes fired within 30 minutes on 2026-08-03. The... | commit 78a7e912 |
| gate-activation | merge-queue advancer daemon - deterministic 5-min actor (Q1+Q2) | Merging currently depends on a live session: measured green->merge ... | commit d5798eae |
| gate-activation | guard: cli gate propagator fail-closed on signal-kill (null status ... | bin/cli.js propagated child exit codes with `result.status \|\| 0`. s... | commit ff33a401 |
| conflict | Merge origin/main into fix/cli-worktree-gitfile | Resolved conflict in tools/init_project.py: | commit c28886f3 |
| conflict | Merge origin/main: resolve state_store/CLAUDE.md conflict |  | commit f9d5a685 |
| conflict | Merge origin/main: resolve verify_cost_summary_drawer.py conflict |  | commit 1d075c79 |
| flake | deflake windows-shard concurrency tests (proven under stress) | * fix(tests): deflake windows-shard concurrency tests (stress-proven) | PR #714 |
| stall | seven pre-push gates fail closed when their script is missing | check_tracker_guard, check_import_resolution, check_claudemd_sync, | PR #786 |
| gate-activation | Implement humanize_ledger.py — append-only JSONL ledger for humaniz... | Lane B: Records before/after text pairs with sentence-level alignme... | commit 58c52fa6 |
| conflict | Merge origin/main into guard/runability-tempdir-failopen | Resolved conflicts in tests/CLAUDE.md (took main's version, counts ... | commit a53eddb1 |
| conflict | Merge origin/main into guard/count-ignores-unmerged-stages | Resolved conflict in tests/CLAUDE.md by taking main's test suite co... | commit 141ab152 |
| conflict | guard: status-bucket fail-open lint + crossos_drift fix (GAP5) | * guard: status-bucket fail-open lint + crossos_drift fail-closed f... | PR #707 |
| conflict | guard: scheduled-task cadence vs SLA check (GAP7) | * guard: scheduled-task cadence vs SLA check (GAP7) | PR #701 |
| conflict | Merge origin/main into fix/wave-manifest-lint-cluster | Resolved conflict in tools/wave_manifest_lint.py: kept PR's version... | commit 81cb9e21 |
| conflict | Merge origin/main into fix/wave-manifest-lint-cluster (retry) | Re-resolved conflicts: took main's version of CLAUDE.md files, kept... | commit de95254f |
| conflict | guard: lint-evasion detector -- flag concat-obfuscated gate tokens | * feat(tools): lint-evasion detector for concat-obfuscated gate tokens | PR #700 |
| conflict | guard: runtime lane-liveness check + fail-closed monitor stall sign... | * guard: runtime lane-liveness check + fail-closed monitor stall si... | PR #704 |
| conflict | guard: --check modes must be read-only (meta-gate, GAP6) | * guard: --check modes must be read-only (meta-gate, GAP6) | PR #706 |
| conflict | guard: exhaustive gate inventory — every gate tool must have an inv... | * guard: exhaustive gate inventory - every gate tool must have an i... | PR #709 |
| stall | stop docs-only-gate failures from skip-cascading required checks | Symptom: ~17 armed PRs stuck unmerged for ~8h. gh pr checks showed | PR #835 |
| gate-activation | guard: hook-tool existence manifest + required-checks drift gate (G... | * guard: hook-tool existence manifest + required-checks drift gate ... | PR #705 |
| test-pollution | guard: test-isolation tripwire + harness-level HOME isolation (fail... | Incident 2026-10-05: running the Node suite on a developer box let | PR #831 |
| test-pollution | test: re-verify agent_detail_roundtrip stable under ci_shard_runner... | STATE.md NEXT STEPS item 4: re-verified test_agent_detail_roundtrip... | PR #843 |
| conflict | fix: daemon halt detection uses halt.py as single source of truth | * fix: daemon halt detection uses halt.py as single source of truth | PR #773 |
| gate-activation | ci: fix main-full.yml rejected by GitHub since #850; lint GitHub sc... | * ci: fix main-full.yml rejected by GitHub since #850; lint GitHub ... | PR #850 |
| test-pollution | docs: reconcile STATE.md NEXT STEPS with shipped PRs | Mark items 3, 4, 7 as DONE (items shipped in PRs #842, #843, #844). | PR #858 |
| gate-activation | chore: inert-code audit cleanup (priority #2) | * chore: inert-code audit cleanup (priority #2) | PR #2 |
| conflict | bounded bisect — eviction-first red handling with culprit isolation... | * feat(queue): bounded bisect — eviction-first red handling with cu... | PR #745 |
| conflict | guard: PR symbol survival check — catch conflict-resolution symbol ... | * guard: PR symbol survival check — catch conflict-resolution symbo... | PR #865 |
| gate-activation | guard: isolate shell tests from real conductor3 state (GAP: live he... | * guard: isolate shell tests from real conductor3 state (GAP: live ... | PR #856 |
| stall | skills check fails closed, and the installer installs skills itself | * fix(doctor): skills check fails closed; installer text names the ... | PR #784 |
| gate-activation | feat: session-independent PR sweep (closes STATE.md item 9 residual) | * feat: session-independent PR sweep (tools/pr_sweep.py, STATE.md i... | PR #874 |
| conflict | Merge origin/main: resolve conflict, keep both resolver + schema_ve... |  | commit 43e1b9a2 |
| flake | fix: deflake watchdog boundary tests with logical time | Root cause: timing race condition in boundary tests | commit f3edb0b5 |
| ci-drift | add pytest to main-full workflow (post-#450 drift) | The main-full.yml workflow was missing pytest | PR #450 |
| gate-activation | fix: secret-scan gate closes worktree bypasses | Pre-push gate now detects file changes in commits | commit 4c2c1bd9 |
| fake-green | actually execute playwright specs | Fixes the long-standing issue where browser-proofs never ran any specs | commit 2bce0c34 |
| conflict | Merge origin/main to resolve conflicts Co-Authored-By: Claude Haiku... |  | commit abf7df03 |
| conflict | Merge origin/main: resolve tools/CLAUDE.md conflict, keep both reso... |  | commit 5033bcdc |
| fake-green | actually execute playwright specs | Fixes the long-standing issue where browser-proofs never ran any specs | commit 0ffdf725 |
| gate-activation | gate-activation: secret-scan gate false positives blocked backup | ENFORCE: doc-shaped fixture allowlist by fingerprint [exists: partial] | commit eb1079e1 |
| flake | deflake: incident_report order flapped per run; test pins it | ENFORCE: test_deterministic_ordering + twice-run identity [exists: ... | PR #946 |
| test-pollution | test-pollution: unit test hit live bench and spent money | ENFORCE: unit tests fail on any live model call (tripwire) [exists:... | commit 9e7939ff |
| stall | stall: heartbeat skipped on BLOCKED cycles; fleet reads as hung pro... | ENFORCE: heartbeat written on every cycle incl. BLOCKED [exists: no] | commit 78a92595 |
| gate-activation | gates fired: G13 nested-def false positive blocked promotion | ENFORCE: red-first nested-def fixture for G13 [exists: no] | commit 604ff79d |
| stall | stall: watchdog task unregistered for weeks, hung process unnoticed | ENFORCE: alarm when watchdog task absent or disabled [exists: partial] | commit 464b469c |
| test-pollution | test-pollution: test suite wrote 1234567890 into live heartbeat | ENFORCE: heartbeat tripwire fails test run on real-root write [exis... | commit 4427099c |
| test-pollution | test-pollution: test_state_md_verifier tag test was not hermetic | ENFORCE: hermetic tag test on tmp origin, no real refs [exists: yes] | PR #943 |
| ci-drift | post-#944 drift: windows aggregator misread docs-only skip on schedule | ENFORCE: scheduled docs-only skip fixture in CI [exists: partial] | PR #944 |
| fake-green | fake-green: PRs #934 and #932 armed for auto-merge, checks never-ran | ENFORCE: pr_sweep re-triggers or closes 0-check armed PRs [exists: no] | PR #934 |
| conflict | conflict: install check matched legacy task (module shadowing) | ENFORCE: divergence check keys on full task path [exists: no] | commit fb7ea54a |
| fake-green | fake-green: two hooks on disk never-ran, unwired in settings | ENFORCE: selftest blocks when on-disk hook is not wired [exists: pa... | commit da4b2d0c |
| gate-activation | gate-activation: lane-contract hook gates fired, refuses not injects | ENFORCE: hook behaviour test vs documented contract [exists: no] | commit cb68df22 |
| fake-green | fake-green: session-end-log hook never-ran, schema-invalid output | ENFORCE: schema test on hook stdout at session end [exists: no] | commit c1dfd4c7 |
| doc-invented | doc-invented: STATE.md v0.9.0 claim unverifiable; v0.9.1 released | ENFORCE: state_md_verifier version-vs-tag check [exists: partial] | commit 90618eff |
| fake-green | fake-green: sync reported OK while primary on Aug branch, never-ran | ENFORCE: sync verifier asserts HEAD == origin default [exists: no] | commit 8367e286 |
| test-pollution | test-pollution: /power never pulled; box 374 commits behind | ENFORCE: /power fails closed when origin is ahead [exists: no] | commit 78afeaf1 |
| gate-activation | gate-activation: no-grep-on-main-thread gates fired on worktree lanes | ENFORCE: grep guard detects main thread, allows worktree cwd [exist... | commit 63473495 |
| stall | stall: registered watchdog $HOME unresolved, parity check blind | ENFORCE: install writes absolute resolved command path [exists: no] | commit 6007cd72 |
| stall | stall: selfheal reads wrong heartbeat path (stall check blind) | ENFORCE: selfheal path parity test vs writer path [exists: no] | commit 5f762965 |
| conflict | conflict: scanner copies drifted brain vs aesop (module shadowing) | ENFORCE: one canonical scanner + cross-repo hash gate [exists: no] | commit 4c726a86 |
| gate-activation | gate-activation: hooks fail open on malformed input (verification b... | ENFORCE: fail-closed conformance test for ALL hooks [exists: no] | commit 16acee99 |
| gate-activation | gate-activation: primary-branch-guard gates fired on substring match | ENFORCE: parse git verb + resolve toplevel, red-first tests [exists... | commit 14e8265d |
| gate-activation | gate-activation: force-haiku hook gates fired a week, refused dispatch | ENFORCE: hook test asserts doc claim = hook action [exists: no] | commit 012c0c68 |
| gate-activation | gate-activation: orchestrator bypassed hook gates fired on two blocks | ENFORCE: deny = stop-and-report, machine-checked escape [exists: no] | commit b1f8a85a |
| doc-invented | doc-invented: state words not bound to checks (unverifiable) | ENFORCE: state claims must cite a check id; lint fails otherwise [e... | commit 98eb1a3a |
| doc-invented | doc-invented: lane claims relayed before CI verified them (unverifi... | ENFORCE: relay blocked until CI run id and merged state checked [ex... | commit 8205122c |

<!-- Latest incident: 2026-07-11T17:39:58-05:00 -->
