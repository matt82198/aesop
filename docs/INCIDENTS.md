
# Incidents

Operational failures tracked by class: detection, resolution, and source reference.

**Summary**

- **ci-drift** (1): CI workflow state out of sync (missing deps, env setup, tools)
- **conflict** (69): Merge/rebase conflict, module shadowing, unintended override
- **doc-invented** (2): Documentation made unverifiable claims, hallucinated counts or proofs
- **fake-green** (1): Tests reported green but never ran or skipped real validation
- **flake** (4): Test timing/race condition, deflake required, logical time or retry
- **gate-activation** (7): Pre-push secret/verification gate caught an escape or bypass
- **stall** (15): Agent/process hung or deadlocked, watchdog detected, restart required
- **test-pollution** (7): Test config leaked between shards, state not isolated, mock pollution

| Class | What Happened | Resolution | Source |
| --- | --- | --- | --- |
| stall | Merge pull request #100 from matt82198/feat/wave12-stall-check | feat(tools): stall_check.py — silent-hang detection for the watchdo... | PR #100 |
| test-pollution | Merge pull request #101 from matt82198/fix/wave12-tracker-test-isol... | fix(tests): isolate tracker writes; guard verify_dash from pollutin... | PR #101 |
| stall | Merge pull request #108 from matt82198/fix/wave13-test-ci-machinery | ci/tools: wire orphan suite, dedup self-tests, metrics gate, stall_... | PR #108 |
| stall | Merge pull request #157 from matt82198/revert/rogue-stall-check-push | revert: rogue direct-to-main push 2d28b52 (stall-check TestCase wrap) | PR #157 |
| gate-activation | Merge remote-tracking branch 'origin/feat/wave19-secretscan-push-ga... |  | commit 723a3d9a |
| stall | Merge remote-tracking branch 'origin/feat/wave19-stall-check-v2' in... |  | commit 6a8265f9 |
| stall | Merge pull request #158 from matt82198/integration/wave19-merge-train | Wave 19: secret-scan hardening, backup-fleet, ci-merge-wait, host-h... | PR #158 |
| stall | Merge pull request #171 from matt82198/feat/wave29-ci-docs-fix | ci: fix docs-only merge deadlock + land judgment-results doc | PR #171 |
| test-pollution | Merge pull request #207 from matt82198/feat/wave-rc10 | wave rc.10: state_store shard isolation, MCP cost-trend tools, wave... | PR #207 |
| stall | Merge remote-tracking branch 'origin/fix/g2-stallcheck-traversal' i... |  | commit 3b688446 |
| conflict | Merge origin/feat/ui-acceptance-criteria-authoring (resolve dist co... |  | commit 9b5134a9 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit c1faa57d |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 138ca671 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit bb7dcc26 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit cd3de6ef |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit ccad043b |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 7beffc69 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 7f9b965a |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit 3a7abfed |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit cdce095f |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md |  | commit f75291ec |
| conflict | merge: resolve test count conflict with main |  | commit cfc25e15 |
| conflict | merge: resolve conflicts with main (use current branch versions) |  | commit 2287c87e |
| conflict | merge: resolve conflict with main (use current branch version) |  | commit e8048663 |
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
| conflict | Merge main into fix/auto-merge-shell-injection - resolve tools/CLAU... |  | commit 1605be9d |
| conflict | Merge feat/multibox-lease-claims (resolve conflicts, keep P0 fixes) |  | commit 0d65388c |
| conflict | Merge main into feature/reconcile-test-counts to resolve conflicts |  | commit 19bac22c |
| conflict | Merge branch 'origin/main' - resolve tests/CLAUDE.md suite count co... |  | commit c75a2d6f |
| conflict | Merge origin/main: resolve state_store/CLAUDE.md conflict |  | commit f9d5a685 |
| conflict | Merge origin/main: resolve hooks/CLAUDE.md conflict |  | commit 5e7f13af |
| conflict | Merge origin/main: resolve verify_cost_summary_drawer.py conflict |  | commit 1d075c79 |
| conflict | fix: merge main + resolve conflicts |  | commit 7deb9670 |
| conflict | fix: merge main + resolve conflicts |  | commit eac24db7 |
| conflict | fix: merge main + resolve conflicts |  | commit 5194fab0 |
| conflict | fix: merge main + resolve conflicts |  | commit da125425 |
| flake | deflake windows-shard concurrency tests (proven under stress) | * fix(tests): deflake windows-shard concurrency tests (stress-proven) | PR #714 |
| conflict | Merge origin/main into feat/multibox-inc4b-durability | Resolves PR #722 conflict by: | commit bbaa12cc |
| conflict | Merge origin/main into fix/cli-worktree-gitfile | Resolved conflict in tools/init_project.py: | commit c28886f3 |
| flake | fix: deflake test_compact_preserves_ttl_for_expiry on slow CI runners | The "held right now" assertion re-read the wall clock AFTER appendi... | commit 3d47aa99 |
| test-pollution | fix: make CLAUDE.md linter scan tracked files, and stop test scaffo... | The runner failure was pollution, not a real violation. CI reported: | commit 33fb4a11 |
| conflict | merge: resolve conflict with main (test suite count drift) | origin/main and refactor/transcript-health-modules both touched | commit fbb46c2b |
| gate-activation | docs: GATES-FIRED.md — which guardrails actually fired, with citations | * docs: add GATES-FIRED.md — evidence-backed audit of guardrails | PR #665 |
| test-pollution | ﻿fix: isolate test conductor3 root to prevent heartbeat file pollution | Problem: test_ui_demo_mode.py's TestDefaultModeUnaffected class did... | PR #645 |
| conflict | Merge origin/main into docs/wave-currency | Resolved conflict in tools/CLAUDE.md by accepting origin/main's mor... | commit dcc33e79 |
| doc-invented | fix: make escape-repro tests run + classify unresolvable PRs as UNV... | Replaced skipped merge-conflict tests with working fixtures via git... | commit b0303353 |
| gate-activation | fix: exclude dispatch_lint.py from admin-flag trap test | dispatch_lint.py is the enforcement tool that detects --admin patte... | commit ba61d2dd |
| conflict | fix: merge main, resolve tests/CLAUDE.md conflict | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> | commit 343f6bee |
| conflict | fix: merge main, resolve tests/CLAUDE.md conflict | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> | commit 2aee17fd |
| conflict | fix: merge main, resolve conflicts, fix test count | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> | commit c92e52a4 |
| conflict | fix: merge main, resolve conflicts, update domain CLAUDE.md docs | Merge main for commit_lint additions, resolve tests/CLAUDE.md and | commit 4acb4bd2 |
| conflict | fix: merge main, resolve conflicts, add sleep-ok comments | Merge main for commit_lint additions, resolve tests/CLAUDE.md and | commit 7b31b55d |
| conflict | Merge main (post-568): resolve test count conflict | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> | commit 101c6f4c |
| conflict | Merge main (post-568): resolve conflicts in tests/tools CLAUDE.md | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> | commit 512370a9 |
| conflict | Merge main (post-568): resolve test count conflict | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> | commit cfe6a504 |
| conflict | merge: resolve remote conflict (187 suites correct) | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> | commit f488c876 |
| conflict | merge: resolve test count conflict (184+1+2=187) | Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> | commit 589fb8c4 |
| conflict | fix: resolve merge conflicts with main | Accept main's version of tests/CLAUDE.md (Python suite count and | commit ae548473 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit a4966d28 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit a33a757e |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit 921843c1 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit df7137cb |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit 7cda7c2c |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit 56cace7f |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Merged origin/main into feat/watcher-linter. Conflict in tests/CLAU... | commit 379476e5 |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Merged Security section test_agent_prompt_hygiene from origin/main | commit 5982c03d |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Accept origin/main's version which includes both test_agent_prompt_... | commit b0dd937b |
| conflict | fix: resolve merge conflict with main in tests/CLAUDE.md | Merged origin/main into feat/tracker-autoclose; kept origin/main's ... | commit 6439a87f |
| conflict | merge: resolve tests/CLAUDE.md count conflict with main | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit 5ad5de02 |
| conflict | Merge origin/main into feat/spec-contract-validator | Resolved tests/CLAUDE.md suite-count conflict via verify_test_suite... | commit 496e1d9c |
| conflict | Merge origin/main into feat/subprocess-guard | Resolved tests/CLAUDE.md suite-count conflict via verify_test_suite... | commit a5c70f0b |
| conflict | Merge origin/main into feat/guardrail-g2-test-coverage | Resolved tests/CLAUDE.md suite-count conflict via verify_test_suite... | commit 935fb79d |
| conflict | Merge origin/main into feat/tracker-autoclose | Resolved tests/CLAUDE.md suite-count conflict via verify_test_suite... | commit b7124d56 |
| conflict | Merge origin/main into feat/ui-acceptance-criteria-authoring | Resolve conflicts: | commit cbd040eb |
| fake-green | actually execute playwright specs + minimal dashboard smoke | * ci(browser-proofs): actually execute playwright specs + minimal d... | PR #464 |
| ci-drift | add pytest to main-full workflow (post-#450 drift) | The main-full.yml workflow was missing pytest from the Python depen... | PR #450 |
| conflict | restore original wave_scheduler spec + add lane_scheduler pilot | Reconciliation fix: restored the full 898-line test_wave_scheduler.... | commit 8cb11f59 |
| flake | fix: deflake watchdog boundary tests with logical time | Root cause (FLAKE 4): TestWatchdogRedBoundary writes heartbeats with | PR #432 |
| flake | fix: deflake windows-timing tests (tracker_csrf readiness, rs3 leas... | * fix: deflake tracker_csrf tests with server readiness polling | PR #427 |
| test-pollution | fix: seated test canned evidence string->array (post-1.6 shape); te... | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit 2f1440c8 |
| doc-invented | docs: correct hallucinated 0.3.0 CHANGELOG entries; README release ... | The auto-merged #332 section described test_battery as 'energy-aware | commit f8b69473 |
| stall | fix: stall_check containment resolves both sides (runner 8.3 regres... | stall_check: verify_path_containment compared an UNRESOLVED short-form | commit 30583b81 |
| stall | fix: stall_check.py—sanitize agent_id to prevent path traversal (CW... | Implement allowlist validation and defense-in-depth path containmen... | commit 44447f3d |
| stall | feat: stall detection enhancements—activity predicates + recovery a... | Item 1: Add --active-from flag to stall_check.py for optional activ... | commit 8ea07fc1 |
| stall | fix: reproduce—distinguish expected pre-init findings from real fai... | Installed mode now classifies doctor check failures: | commit cb088ecd |
| test-pollution | fix: wave_loop tests run in module tmpdir (cwd-pollution root cause... | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit 3ec6547c |
| gate-activation | fix: secret_scan.py—fail CLOSED on file/git read errors (P1 security) | The pre-push secret-detection gate had two fail-open vulnerabilitie... | commit dc765865 |
| gate-activation | wave rc.6-obs: transcript digest, CLAUDE.md linter, CONTRIBUTING, b... | 4 new-file items (orchestrator straggler-takeover: workflow spun on... | commit 0481fbc4 |
| stall | ci: run `ci` on every PR (fix docs-only deadlock); land held judgme... | Removes the job-level docs-only `if:` skip on `ci`. A skipped matri... | commit 00649b75 |
| gate-activation | fix: secret-scan gate closes worktree/blob bypasses (wave-25) | --staged and --range now scan the actual git objects being | commit de1ddac7 |
| stall | fix/test: wrap bare test functions in unittest.TestCase | 7 module-level test functions in tests/test_stall_check.py now prop... | commit e9981815 |
| gate-activation | fix: restore secret_scan push gate to detect files changed in commits | Fixes inert push gate that failed to scan secrets because git diff ... | commit c8b3c257 |
| stall | fix: wrap bare test functions in unittest.TestCase for CI collection | test_stall_check.py defined 7 pytest-style module-level test functions | commit 2d28b523 |
| conflict | Merge wave-14 U4 (Overview view pack) with U7 (Cost view pack) | Resolved conflicts by unioning both sides' intent: | commit 00400ca3 |
| test-pollution | isolate tracker writes to tempdir; guard verify_dash against pollut... | - tools/verify_dash.py now explicitly sets AESOP_STATE_ROOT to temp... | commit 29356d85 |
| stall | stall_check.py — silent-hang detection for the agent watchdog (wave... | Adds automated detection of stalled agents by scanning transcript m... | commit 17010688 |
| conflict | Merge pull request #67 into feat/port-ops-tools (PR #68) | Resolve merge conflict in tools/CLAUDE.md: Union of both PRs — reta... | PR #67 |

<!-- Latest incident: 2026-07-13T19:50:27-05:00 -->
