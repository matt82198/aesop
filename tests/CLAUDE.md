# tests/ — Automated test suites (shell, Node, Python)

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Test Suite Map & Run Commands

**Shell (13 suites)**: Discovered dynamically from `tests/*.test.sh`, `tests/test_*.sh`, `tests/test-*.sh` plus `hooks/pre-push-policy.sh --test`.
Organized by component: backup/recovery, watchdog daemon, git hooks, state reconciliation, UI, daemon self-healing, wave enforcement, test runners.
Run: `bash tools/run_shell_tests.sh` or `npm run test:sh`

**Node (26 suites)**: Discovered dynamically from `tests/*.test.mjs`.
Organized by category: CLI scaffolding, config management, signal collection, drift detection, dashboard UI, fleet/MCP APIs, test templating, orchestration core.
Run: `npm run test:node` or `node --test --test-force-exit --test-timeout=60000 tests/*.test.mjs`

**Python (236 suites)**: Discovered dynamically from `tests/test_*.py`.
Organized by category: API state/tracker (test_api_state, test_api_tracker, test_tracker_*, test_tracker_autoclose — Guardrail G1 auto-close on merged PRs), UI/SSE (test_serve*, test_sse_*, test_ui_*, test_wave13_ui_correctness, test_wave_*), Bench (test_bench_*, test_accuracy_harness, test_sample_transcripts_judgment, test_seated_shadow_adjudication — 8 new, test_seam_s_e2e_oracle — oracle grading e2e), Security (test_csrf_https_origins, test_secret_scan, test_secret_scan_gaps, test_symlink_guard, test_agent_prompt_hygiene, test_dispatch_lint — Guardrail G7: dispatch policy linter for merge automation enforcement, 25 test cases), State store (test_state_store*, test_lease_claims — multi-instance file-scope lease claims), StateAPI facade (test_stateapi_read, test_stateapi_lint, test_writeapi_markdown — markdown write-path unification, 12: +1 custom-header preservation for migrated writers, test_ws4_writer_migrations — WS4 inc 2: tools/buildlog.py + tools/ensure_state.py + tools/eod_sweep.py write STATE.md/BUILDLOG.md through WriteAPI, subprocess-level event+file dual-write proof, byte-compatible legacy formats, non-canonical BUILDLOG name fails closed), Tools (test_tools_*, test_defect_escape, test_test_hygiene, test_subprocess_guard — G6 AST guard for bare bash-without-cwd/shell=True/cwd=None/os.system in tests/, test_cost_projection, test_ci_gate_runability — Guardrail G2.5: every CI gate command is actually runnable, test_wave_preflight — wave manifest preflight validator, test_cli_help_hygiene — uniform --help + fail-closed unknown-flag exit codes across 7 hand-parsed CLI tools, test_spec_contract_validator — Guardrail G4: AST-scanned dispatch-call spec-contract validation (forbidden flags, credential-hunting/env-var allowlist, isolation marker, role-routing, `# contract-ok` suppression), test_otel_sink — OpenTelemetry tracing integration: span/metric construction, dry-run mode, fake exporter, state surface ingestion, hermetic tests with no network/SDK required, test_traps — adversarial regression trap suite for 5 incident classes, test_watcher_linter — Guardrail G3 watcher/polling anti-pattern linter: while-True+sleep, watch_/monitor_/poll_-named infinite loops, subprocess-in-loop, dispatch-prompt wait/poll/watch phrasing, # watcher-ok suppression, JSON output, test_commit_lint — conventional commit message linter: format/type/length/trailer/body-separator validation + CLI JSON output), AgentDriver/OrchestratorDriver (test_agent_driver, test_orchestrator_driver, test_codex_driver_e2e — offline + gated live tests, test_worker_seam_breakit — r2 worker-seat break-it, test_hs2_swap_proof — HS-2 live orchestrator-seat swap: no-op invariant + seat verdict effects + end-to-end Report/state shape invariance, all offline, test_hs2_block_gate — HS-2 block-gate hardening: confidence prompt/schema agreement, blocked lane + terminal tracker state, degraded-gate visibility, quarantine incl. pathspec guard (FILE paths only: dir/dot/empty entries rejected, no cross-item destruction) + fail-safe untracked classification + Report.blocked quarantine visibility, seat-spend ceiling, evidence-array contract, all offline), Wave engine cross-repo (test_wave_cross_repo, test_wave_cross_repo_ship, test_wave_e2e_first_wave — first-wave e2e proof: CLI invocation runs minimal wave against fixture repo, FakeDriver fixes to green, captures JSON report, tests report shape + final state hash + fixture isolation), Agents/Monitoring (test_alert_bridge, test_collectors, test_orchestration_core, test_stall_check, test_reconcile, test_healthcheck, test_halt, test_ci_merge_wait), Config/Launch (test_seats_config -- HS-1 unified two-seat config: seats parse, build_orchestrator_backend, api_key_env/is_local parity, no-op default invariant, scheduler + shadow-tool seat wiring, test_launch_tui, test_render, test_rotate_logs, test_metrics_gate, test_no_bare_test_functions, test_git_identity_check, test_self_stats, test_verify_test_suite_count_prepush -- pre-push wiring for the suite-count drift gate), Daemons/Windows (test_install_tasks — win32-only, skipped elsewhere), Driver decisions (test_decision_schemas), Context-eng UI (test_ui_wave_context — spec-sharpness + file-scope read-only views).
Run: `npm run test:py` or `python -m unittest discover -s tests`

**Live suite inventory**: Run `python tools/list_test_suites.py` for a detailed grouped listing with first-line summaries.

**Counts are gate-verified, never auto-corrected**: `python tools/verify_test_suite_count.py --check` is READ-ONLY — it fails the pre-push hook and CI when the counts above drift, and it never rewrites this file. When your lane adds or removes a test suite, run `python tools/verify_test_suite_count.py --regenerate` and commit the updated counts in the same PR.

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

### Timer Resolution (Windows CI ~15ms resolution)
- **time.sleep() enforcement**: Never use `time.sleep()` with values < 0.1 seconds (100ms) in tests; Windows CI timer resolution is ~15ms, so smaller sleeps are unreliable. Add `# sleep-ok` suppression comment for race-condition yields where sleep is not for timing assertions (validated by test_test_hygiene.py AST scanner).

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

## Guardrail G2.6: test_verify_gates_wired

Tests for tools/verify_gates_wired.py (Guardrail G2.6), the documented-gates-are-wired guardrail:
- All documented gates wired: exit 0 when every CI gate in CLAUDE.md is invoked in ci.yml
- Unwired gate detection: exit 1 when documented gates are missing from CI workflow
- verify_*.py mandatory gates: capture tools listed in "verify_*.py are mandatory CI gates" section
- Guardrail gate capture: capture tools marked with "(Guardrail Gx)" except pre-push-only gates
- Pre-push-only exclusion: skip gates documented with 'pre-push' but not 'CI' (not CI gates)
- Missing files fail-closed: return error (exit 1/2) if CLAUDE.md files unreadable/absent
- Fixture isolation: temp test structure with mock CLAUDE.md + ci.yml files, no side effects

---

**Historical note**: The exhaustive per-file suite listings and dropped-reason changelogs below this line were removed to eliminate the conflict-magnet in PR merges (every test-adding PR conflicted with every other). Suite discovery is now deterministic and automated via `python tools/list_test_suites.py` and `bash tools/run_shell_tests.sh`. Counts in the headers above are gate-verified by `python tools/verify_test_suite_count.py --check` (CI blocking gate).

Map of all domains: /CLAUDE.md
