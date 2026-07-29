
# Incidents

Operational failures tracked by class: detection, resolution, and source reference.

**Summary**

- **ci-drift** (3): CI workflow state out of sync (missing deps, env setup, tools)
- **conflict** (6): Merge/rebase conflict, module shadowing, unintended override
- **doc-invented** (1): Documentation made unverifiable claims, hallucinated counts or proofs
- **fake-green** (2): Tests reported green but never ran or skipped real validation
- **flake** (6): Test timing/race condition, deflake required, logical time or retry
- **gate-activation** (7): Pre-push secret/verification gate caught an escape or bypass
- **stall** (16): Agent/process hung or deadlocked, watchdog detected, restart required
- **test-pollution** (6): Test config leaked between shards, state not isolated, mock pollution

| Class | What Happened | Resolution | Source |
| --- | --- | --- | --- |
| stall | Merge pull request #100 from matt82198/feat/wave12-stall-check | feat(tools): stall_check.py — silent-hang detection for the watchdo... | PR #100 |
| test-pollution | Merge pull request #101 from matt82198/fix/wave12-tracker-test-isol... | fix(tests): isolate tracker writes; guard verify_dash from pollutin... | PR #101 |
| stall | Merge pull request #108 from matt82198/fix/wave13-test-ci-machinery | ci/tools: wire orphan suite, dedup self-tests, metrics gate, stall_... | PR #108 |
| stall | Merge pull request #157 from matt82198/revert/rogue-stall-check-push | revert: rogue direct-to-main push 2d28b52 (stall-check TestCase wrap) | PR #157 |
| gate-activation | Merge remote-tracking branch 'origin/feat/wave19-secretscan-push-ga... |  | commit 723a3d9 |
| stall | Merge remote-tracking branch 'origin/feat/wave19-stall-check-v2' in... |  | commit 6a8265f |
| stall | Merge pull request #158 from matt82198/integration/wave19-merge-train | Wave 19: secret-scan hardening, backup-fleet, ci-merge-wait, host-h... | PR #158 |
| stall | Merge pull request #171 from matt82198/feat/wave29-ci-docs-fix | ci: fix docs-only merge deadlock + land judgment-results doc | PR #171 |
| test-pollution | Merge pull request #207 from matt82198/feat/wave-rc10 | wave rc.10: state_store shard isolation, MCP cost-trend tools, wave... | PR #207 |
| stall | Merge remote-tracking branch 'origin/fix/g2-stallcheck-traversal' i... |  | commit 3b68844 |
| conflict | Merge origin/main into feat/ui-acceptance-criteria-authoring | Resolve conflicts: | commit cbd040e |
| conflict | Merge branch 'origin/main' into docs/claim-honesty: resolve structu... | Merge commit resolving conflict from PR #467 (stats block relocatio... | commit fb81744 |
| gate-activation | remove invented precision from Gates That Fired paragraph | Rewrite with qualitative-but-concrete phrasing per coordinator review: | commit 93c66b6 |
| ci-drift | correct stats-refresh.yml YAML structure | - Simplified branch creation logic (removed redundant find-pr step) | commit 85596f2 |
| fake-green | actually execute playwright specs + minimal dashboard smoke | * ci(browser-proofs): actually execute playwright specs + minimal d... | PR #464 |
| fake-green | actually execute playwright specs + minimal dashboard smoke | Add Playwright TypeScript test infrastructure to CI browser-proofs ... | commit 8873971 |
| ci-drift | add pytest to main-full workflow (post-#450 drift) | The main-full.yml workflow was missing pytest from the Python depen... | PR #450 |
| ci-drift | add pytest to main-full workflow (post-#450 drift) | The main-full.yml workflow was missing pytest from the Python depen... | PR #450 |
| conflict | merge: bench/seam-loop-consolidation + origin/main (PR #450) | Resolved conflict in tests/CLAUDE.md by taking merged union: | PR #450 |
| conflict | restore original wave_scheduler spec + add lane_scheduler pilot | Reconciliation fix: restored the full 898-line test_wave_scheduler.... | commit 8cb11f5 |
| test-pollution | stop test_ui_wave_context leaking MockConfig into sys.modules (shar... | Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com> | commit 2db03b1 |
| flake | fix: deflake watchdog boundary tests with logical time | Root cause (FLAKE 4): TestWatchdogRedBoundary writes heartbeats with | PR #432 |
| flake | fix: deflake watchdog boundary tests with logical time | Root cause (FLAKE 4): TestWatchdogRedBoundary writes heartbeats with | commit 4c1eeba |
| flake | fix: deflake windows-timing tests (tracker_csrf readiness, rs3 leas... | * fix: deflake tracker_csrf tests with server readiness polling | PR #427 |
| flake | fix: deflake heartbeat staleness tests with logical time | Root cause (FLAKE 3): Boundary condition tests | commit dacd880 |
| flake | fix: deflake rs3 lease TTL expiry test with logical time | Root cause (FLAKE 2): test_expired_claim_is_reclaimable() relies on | commit ca1d382 |
| flake | fix: deflake tracker_csrf tests with server readiness polling | Root cause (FLAKE 1): Tests start a ThreadingHTTPServer thread but | commit 57b16c4 |
| gate-activation | bench: reword ft96 vocabulary so the secrets-hygiene gate stays strict | Scenario text tripped test_no_secrets_in_prompts (api_key literal).... | commit ef491c7 |
| test-pollution | fix: seated test canned evidence string->array (post-1.6 shape); te... | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit 2f1440c |
| doc-invented | docs: correct hallucinated 0.3.0 CHANGELOG entries; README release ... | The auto-merged #332 section described test_battery as 'energy-aware | commit f8b6947 |
| stall | fix: stall_check containment resolves both sides (runner 8.3 regres... | stall_check: verify_path_containment compared an UNRESOLVED short-form | commit 30583b8 |
| stall | fix: stall_check.py—sanitize agent_id to prevent path traversal (CW... | Implement allowlist validation and defense-in-depth path containmen... | commit 44447f3 |
| stall | feat: stall detection enhancements—activity predicates + recovery a... | Item 1: Add --active-from flag to stall_check.py for optional activ... | commit 8ea07fc |
| stall | feat: stall_check.mjs — silent agent hang detector for monitor | OPS P1 requirement: Detect STALLED agents (silent hangs) determinis... | commit 7b1e4de |
| stall | fix: reproduce—distinguish expected pre-init findings from real fai... | Installed mode now classifies doctor check failures: | commit cb088ec |
| test-pollution | fix: wave_loop tests run in module tmpdir (cwd-pollution root cause... | Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> | commit 3ec6547 |
| gate-activation | fix: secret_scan.py—fail CLOSED on file/git read errors (P1 security) | The pre-push secret-detection gate had two fail-open vulnerabilitie... | commit dc76586 |
| gate-activation | wave rc.6-obs: transcript digest, CLAUDE.md linter, CONTRIBUTING, b... | 4 new-file items (orchestrator straggler-takeover: workflow spun on... | commit 0481fbc |
| stall | ci: run `ci` on every PR (fix docs-only deadlock); land held judgme... | Removes the job-level docs-only `if:` skip on `ci`. A skipped matri... | commit 00649b7 |
| gate-activation | fix: secret-scan gate closes worktree/blob bypasses (wave-25) | --staged and --range now scan the actual git objects being | commit de1ddac |
| stall | fix/test: wrap bare test functions in unittest.TestCase | 7 module-level test functions in tests/test_stall_check.py now prop... | commit e998181 |
| gate-activation | fix: restore secret_scan push gate to detect files changed in commits | Fixes inert push gate that failed to scan secrets because git diff ... | commit c8b3c25 |
| stall | fix: wrap bare test functions in unittest.TestCase for CI collection | test_stall_check.py defined 7 pytest-style module-level test functions | commit 2d28b52 |
| conflict | Merge wave-14 U4 (Overview view pack) with U7 (Cost view pack) | Resolved conflicts by unioning both sides' intent: | commit 00400ca |
| test-pollution | isolate tracker writes to tempdir; guard verify_dash against pollut... | - tools/verify_dash.py now explicitly sets AESOP_STATE_ROOT to temp... | commit 29356d8 |
| stall | stall_check.py — silent-hang detection for the agent watchdog (wave... | Adds automated detection of stalled agents by scanning transcript m... | commit 1701068 |
| conflict | Merge pull request #67 into feat/port-ops-tools (PR #68) | Resolve merge conflict in tools/CLAUDE.md: Union of both PRs — reta... | PR #67 |

<!-- Latest incident: 2026-07-13T19:50:27-05:00 -->
