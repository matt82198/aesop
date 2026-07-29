# tests/ — Automated test suites (shell, Node, Python)

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Test Suite Map & Run Commands

**Shell (12 suites)**:
backup-fleet.test.sh, dash-watchdog-gui.test.sh, test_agent_forensics.sh, test_pre_push_policy.sh, test-run-watchdog.sh, test-run-watchdog-halt.sh, test-run-watchdog-lockguard.sh, test_reconstitute.sh, test_reconstitute_fixes.sh, test-selfheal.sh, test_waveguard.sh.
Run: `bash tests/test_pre_push_policy.sh && bash tests/backup-fleet.test.sh && bash tests/test_reconstitute.sh && bash tests/test_reconstitute_fixes.sh && bash tests/test_agent_forensics.sh && bash tests/test-selfheal.sh && bash hooks/pre-push-policy.sh --test && bash tools/reconstitute.sh --test`

**Node (23 suites)**:
buildsystem-template.test.mjs, cli-config.test.mjs, collect-signals.test.mjs, config-doc-drift.test.mjs, dash-agents-panel.test.mjs, dash-extra.test.mjs, demo-e2e.test.mjs, domain-map-drift.test.mjs, first-hour.test.mjs, fleet-cli.test.mjs, force-model-policy.test.mjs, lock.test.mjs, mcp-fleet.test.mjs, packaging-portability.test.mjs, proposals.test.mjs, reproduce-classifier.test.mjs, scaffold-hook-install.test.mjs, scaffold-onboarding.test.mjs, test_orchestration_core.test.mjs, wizard.test.mjs.
Run: `npm run test:node` or `node --test --test-force-exit --test-timeout=60000 tests/*.test.mjs`

**Python (156 suites)**:
Organized by category: API state/tracker (test_api_state, test_api_tracker, test_tracker_*), UI/SSE (test_serve*, test_sse_*, test_ui_*, test_wave13_ui_correctness, test_wave_*), Bench (test_bench_*, test_accuracy_harness, test_sample_transcripts_judgment, test_seated_shadow_adjudication — 8 new, test_seam_s_e2e_oracle — oracle grading e2e), Security (test_csrf_https_origins, test_secret_scan, test_secret_scan_gaps, test_symlink_guard), State store (test_state_store*), StateAPI facade (test_stateapi_read, test_stateapi_lint), Tools (test_tools_*, test_defect_escape, test_test_hygiene, test_cost_projection, test_wave_preflight — wave manifest preflight validator, test_cli_help_hygiene — uniform --help + fail-closed unknown-flag exit codes across 7 hand-parsed CLI tools), Reliability & Chaos (test_chaos_harness — 12 test cases: chaos-wave fault injection + recovery measurement, F1-F5 fault classes, offline deterministic, TDD-first), AgentDriver/OrchestratorDriver (test_agent_driver, test_orchestrator_driver, test_codex_driver_e2e — offline + gated live tests, test_worker_seam_breakit — r2 worker-seat break-it, test_hs2_swap_proof — HS-2 live orchestrator-seat swap: no-op invariant + seat verdict effects + end-to-end Report/state shape invariance, all offline, test_hs2_block_gate — HS-2 block-gate hardening: confidence prompt/schema agreement, blocked lane + terminal tracker state, degraded-gate visibility, quarantine incl. pathspec guard (FILE paths only: dir/dot/empty entries rejected, no cross-item destruction) + fail-safe untracked classification + Report.blocked quarantine visibility, seat-spend ceiling, evidence-array contract, all offline), Wave engine cross-repo (test_wave_cross_repo, test_wave_cross_repo_ship), Agents/Monitoring (test_alert_bridge, test_collectors, test_orchestration_core, test_stall_check, test_reconcile, test_healthcheck, test_halt, test_ci_merge_wait), Config/Launch (test_seats_config -- HS-1 unified two-seat config: seats parse, build_orchestrator_backend, api_key_env/is_local parity, no-op default invariant, scheduler + shadow-tool seat wiring, test_launch_tui, test_render, test_rotate_logs, test_metrics_gate, test_no_bare_test_functions, test_git_identity_check, test_self_stats), Daemons/Windows (test_install_tasks — win32-only, skipped elsewhere), Driver decisions (test_decision_schemas), Context-eng UI (test_ui_wave_context — spec-sharpness + file-scope read-only views).
Run: `npm run test:py` or `python -m unittest discover -s tests`

### Phase 2 AgentDriver Codex Tests (test_codex_driver_e2e.py)
- **Offline tests** (all run in CI, no OPENAI_API_KEY needed):
  - Happy path: FakeTransport returns valid schema → file written, ok=True, tokens_spent tracked.
  - Retry: malformed-then-valid JSON triggers bounded retry (<=2 attempts).
  - Fail-safe: always-malformed JSON → WORKER_FAILED, no files written (never green).
  - Ownership enforcement: out-of-scope paths rejected wholesale, no partial writes.
  - Oversized files: pre-dispatch max_owned_bytes guard fails safe (no truncation).
  - True e2e: RED stub + FakeTransport-supplied fix + run_command → GREEN (offline proof).
  - run_command: real subprocess execution (not mock).
  - run_command timeout (RS-A F1/F7, mirrored in test_agent_driver for ClaudeCodeDriver):
    wall-clock truly bounded (real grandchild sleep >> timeout returns exit 124 within a
    small multiple of timeout_s — never Windows `timeout /t`, which errors instantly
    without a console), process TREE killed (grandchild pid proven dead), partial
    stdout/stderr preserved, and command_timeout_s independent of the HTTP timeout_s.
  - worker_status: in-memory registry tracking.
  - verification_policy: tier->policy mapping (tier 1/2/3/4 return correct dicts; codex probe → tier 2 policy).
  - Probe unchanged: codex probe still returns honest Tier-2 (fs=False, shell=False, structured=True).
- **Live test** (gated by AESOP_CODEX_LIVE env var, skipped in CI):
  - Real end-to-end with OpenAI API (requires OPENAI_API_KEY + AESOP_CODEX_LIVE=1 to run).

### OrchestratorDriver Seam Tests (test_orchestrator_driver.py — increment 1, 20 suites)
- **Context pack allowlist enforcement** (mirrors cardinal rule 4):
  - STATE.md read from repo/conductor roots (fallback chain).
  - buildlog_tail:N reads last N lines of BUILDLOG.md.
  - tracker_open reads open items from tracker.json.
  - brief:<path> reads explicit files under allowlist (repo/conductor roots).
  - Arbitrary paths outside allowlist raise ContextPackViolation (code-level enforcement).
  - Unknown source types raise ContextPackViolation.
- **Context pack size capping**:
  - Size-bounded with deterministic truncation (oldest-first for logs).
  - Manifest tracks included/truncated/size for each source.
  - Oversized log sources truncated before other sources.
- **OrchestratorDriver.decide()** (happy path + fail-safe):
  - Valid JSON verdict returned with metadata (decision_type, retry_count, schema_validated).
  - Malformed JSON retries (<=2 attempts), then DECISION_FAILED (never green).
  - Missing required keys ('verdict', 'evidence') trigger fail-safe.
  - Backend command failure (non-zero exit) retries then fails safe.
- **Schema loading & validation**:
  - Schemas loaded from decisions/<type>.schema.json (optional; absent is OK).
  - Schemas cached per type to avoid re-loading.
  - Minimal validation enforced always (verdict + evidence keys).
  - Full schema validation applied when schema is present.
- **All offline**: FakeTransport, no API keys, no network, hermetic temp fixtures.

## Hygiene Rules (Permanent)

### Fixture Isolation
- Shell tests use `mktemp` or `$TMPDIR` with `trap` cleanup (never pollute ~).
- Python tests use `tempfile.TemporaryDirectory()` or isolated fixtures; `setUp`/`tearDown` required.
- No persistent side effects; all tests run independently on any branch.

### Cwd & Git Config Pollution (Wave-25 Enforcement)
- **cwd pollution**: Never bare `os.chdir()` without `try/finally` restoration or tearDown. Preferred: subprocess `cwd=` parameter.
- **git config pollution**: Tests must never call `git config user.*` on the live repo. Scope all identity changes to temp fixture repos only (validated by test_test_hygiene.py AST scanner).
- Violations cause Windows cleanup deadlock (deleted temp dirs leave poisoned cwd, later tests inherit it).

### Platform-Conditioned Repro (Permanent, incident-proven 2x)
- A fix for a windows-RUNNER-only failure is NOT done without reproducing the runner
  condition locally (8.3 short paths via FSO ShortPath + short TMPDIR) or captured runner
  evidence (forensic assertion messages). Local-green alone shipped two wrong fixes in one
  day; the third attempt with mandated repro found the real cause in one round.

### Dummy Secrets (Never Literal)
- Test secrets assembled at runtime via string concat (e.g., `"prefix" + "suffix"`) to evade `secret_scan.py`.
- Never commit literal `dummy_key_123` or test credentials to any file.
- Pragma guards exist in secret_scan.py for known test patterns.

## Test Philosophy: Gap-Centric

Tests document **actual gaps** found in rounds of refactoring/audit:
- Each finding → test case that reproduces the gap (failing first, TDD).
- Once fixed, test stays to prevent regression.
- No hypothetical tests; no "might fail someday" placeholders.
- Flaky CI (e.g., state_store SQLite deadlocks under parallel shards) recorded as real gaps + remediation notes (not skipped).

## Integration

- **npm scripts**: `npm run test:node`, `npm run test:sh`, `npm run test:py`, `npm run test:all`.
- **CI (.github/workflows/ci.yml)**: Each harness runs independently; one failure blocks merge.
- **Local**: Run full suite before commit: `npm run test:all` (or push-gate stops you).
- **HEAD-independent**: All tests run regardless of git branch (CI runs on main).
- **Concurrency-safe**: Tests use file locks (proposals.mjs, collect-signals.mjs) to prevent races.
- **Self-test mode**: Hooks & tools (pre-push-policy.sh, reconstitute.sh, tools/secret_scan.py) include `--test` flag for inline validation.

## Dropped (reason)
- Python count revised 147→148 (seam-loop-consolidation PR adds test_seam_s_e2e_oracle [5 new: oracle-layout e2e, worker-patch application, visible-test execution, oracle grading with known-good fix, oracle failure on no-op, subprocess pytest invocation]).
- Python count revised 151→152 (adds test_cli_help_hygiene [15 new: uniform --help (usage on stdout, exit 0, no side effect) + fail-closed unknown-flag exit codes for the 7 hand-parsed argv tools -- halt.py, stateapi_lint.py, ci_shard_runner.py, session_usage_summary.py, git_identity_check.py, wave_backlog_analyzer.py, alert_bridge.py]).
- Python count revised 150→151 (context-eng UI wave adds test_ui_wave_context [spec-sharpness + file-scope read-only views, PR #444]).
- Python count revised 133→134 (HS-2 block-gate hardening adds test_hs2_block_gate [19 new, +9 in the quarantine destructive-defect round = 28: pathspec guard rejects directory/"."/""/".." entries with error records, globs neutralized via :(literal) (a dir or dot spec ran `git checkout -- <dir>` and reverted OTHER items' uncommitted verified work — cross-item destruction now impossible by construction), Windows backslash tracked path restored not deleted, ambiguous ls-files failure (exit 128) never deletes (fail-safe, not fail-delete), quarantine outcome (clean|errors|skipped|unknown + error detail) surfaces in Report.blocked; final_catch confidence REQUIRED in prompt (schema/prompt contradiction fixed) + block-with-confidence validates, blocked items -> terminal tracker "blocked" + Report.blocked {slug,reason} + orchestrator_gate summary + honest success + no re-select on second run, all-failing seat -> gate status "degraded" while crash-only ship preserved, block quarantine restores tracked/deletes untracked in a REAL temp git repo + conservative skip outside git, seat token metering (Fake + OpenAI usage) + post-decision ceiling check includes seat spend + no extra check on no-op path, DECISION_FAILED evidence array contract]).
- Python count revised 132→133 (HS-2 adds test_hs2_swap_proof [20 new: run_wave/scheduler no-op invariant incl. Report-shape guard + no-key run, resolve_orchestrator_backend config mapping + execute key gate, live-seat final_catch verdict effects (merge/block/escalate/DECISION_FAILED/skip-unverified), end-to-end swap-transparency of Report JSON + tracker + journal, scheduler pass-through wiring]).
- Shell count corrected 7→11: test-run-watchdog{,-halt,-lockguard}.sh DO exist (a prior reconcile confused unwired with nonexistent) and all pass; halt/lockguard now wired into test:sh.
- Node count revised 17→18→19→20 (wave-28 adds reproduce-classifier.test.mjs) (added first-hour.test.mjs which was present but unlisted; added demo-e2e.test.mjs for init-prime-demo feature).
- Python count revised 131→132 (HS-1 adds test_seats_config [38 new: seats parse + backward compat, build_orchestrator_backend null/configured, api_key_env honored, is_local dummy-key, SSRF at load/build/construct, no-op default invariant, wave_scheduler resolve_worker_driver override semantics, shadow tools seat wiring; round-1 audit adds 29 more in-file: legacy-flat-inert-in-scheduler, is_local loopback pinning on all 4 paths, api_key_env name restriction, build_driver replace+validate promotion parity, DNS-resolved SSRF, orchestrator-backend fail-closed guard import, harness-seat model warn, --live help drift]).
- Python count revised 130→131 (r2 consolidation adds test_worker_seam_breakit [13 new: codex config-model honored, timeout_s bound to transport, transport-vs-validation error classification, separator-normalized ownership, partial-writes reported on failure]).
- Python count revised 111→118→119→123→124→125→126→127→128→136 (increment 4a adds test_seated_shadow_adjudication [8 new: context pack building, labels-never-leak, reasoning persistence, modal verdict, item9 flip detection, corpus loading]; orchestrator-swap increment 3 adds test_adjudication_gate [two-tier gate, offline proof, safety invariant assertions]; increment 2 added test_shadow_adjudication [blind adjudication, corpus parsing, scorecard math, labels-not-in-packs]; earlier increments added test_decision_schemas [decision catalog drift gate] and test_orchestrator_driver [20 suites for context_pack + OrchestratorDriver.decide()]) (wave-28 adds test_wave_scheduler; wave-29 adds test_stateapi_write, test_crossos_drift, test_frontier_slice, test_sse_disconnect) (wave-27 union adds test_accuracy_harness, test_cost_projection, test_sample_transcripts_judgment, test_stateapi_lint, test_stateapi_read, test_wave_cross_repo, test_wave_cross_repo_ship).
- Python count revised 67→95→98 (recounted: 94 existing + 1 new drift-test = 95 at branch #246; integration union adds test_backend_config_docs.py + test_wave_dispatch_agents_parity.py + test_fleet_ledger_injection.py = 98 total).
- Prior revisions: Node 15→17 (recounted); Shell 9→10 (test_waveguard.sh was present but unlisted); Python 60→65 (recounted).

---

Map of all domains: /CLAUDE.md
