# driver/ — AgentDriver backend-portability seam

**What**: the domain contract letting aesop's wave loop run on non-Claude backends; the loop dispatches only through the `AgentDriver` interface. **Phases 1-3 shipped**.

## Files

- **agent_driver.py** — the `AgentDriver` ABC + capability/request/result dataclasses.
  The contract. stdlib-only, no provider SDKs.
- **claude_code_driver.py** — reference adapter (Claude Code parity): two ops concrete
  Python, three serviced by the harness.
- **codex_driver.py** — Phase 2: OpenAI Chat Completions HTTP backend: dispatch_worker (file
  injection, JSON retry), run_command (own `command_timeout_s` knob, defaults to timeout_s), worker_status, get_tokens_spent. Transport injectable.
- **proc_util.py** — `run_shell_bounded()`: shared run_command backing (RS-A F1/F7). Timeout
  truly bounds wall-clock: child in own group/session; on expiry the WHOLE tree is killed
  (taskkill /T /F Windows, killpg POSIX), exit 124 promptly, partial output preserved. Never
  subprocess.run(shell=True, timeout=) — Windows kills only cmd.exe then re-blocks on the orphaned grandchild.
- **openai_transport.py** — stdlib urllib transport for the OpenAI endpoint; injectable
  seam (FakeTransport in tests: no API key or network).
- **openai_compatible_driver.py** — OpenAI-compatible backend (Ollama, OpenRouter, etc.);
  construct-time validate_base_url; key waiver only via loopback-validated is_local.
- **verification_policy.py** — Maps verification tier -> orchestrator tuning (validate_all_json, spot_check_frac, repair_cap, require_adversarial_review).
- **wave_loop.py** — the wave ENGINE: preflight ownership guard, parallel build,
  bounded repair, per-repo batched git ship, recovery journal. Halt kill-switch checks
  at all phase boundaries (PHASE 3/5/5.75/6/7) mirror cost-ceiling pattern, abort cleanly
  when _check_halt() detects sentinel. Supports optional orchestrator_backend for
  live-seat swap (see driver/orchestrator-swap/). RS3-W + RS5 claim lifecycle: fail-CLOSED
  gate, fingerprint-bound journal, deterministic claim state. Tests: test_wave_loop_rs3,
  test_wave_loop_halt_enforcement.
- **wave_bridge.py** — Phase 3: bridges AgentDriver backends to wave manifest items
  (build_manifest_item / dispatch_item; green ONLY from test exit code, see below).
- **anthropic_driver.py** — Anthropic Messages API driver for bench seam (direct HTTP, no SDK).
- **anthropic_transport.py** — stdlib urllib transport for the Anthropic endpoint.
- **backend_config.py** — Seat config builder: reads `aesop.config.json` seats block, constructs driver instances.
- **context_pack.py** — Context-pack assembly for orchestrator decisions (allowlist-only reads).
- **wave_scheduler.py** — Wave-manifest scheduler: builds worker driver from config, dispatches wave items.
- **decisions/** — Decision type schema registry (sibling lane owns schemas; absent = optional).
- **../tests/** — test_agent_driver (contract), test_codex_driver_e2e (offline + gated
  live), test_wave_bridge, test_orchestrator_driver, test_adjudication_gate,
  test_hs2_swap_proof, test_hs2_block_gate, test_wave_loop_rs3 (round-2 robustness:
  N1/N3/N4/N5/N6/N7/N10 — all offline). Details: tests/CLAUDE.md.

## The five operations (what the wave loop needs from ANY backend)

1. `probe_capabilities() -> DriverCapabilities` — honest self-report (parallel? fs? shell? structured? worktree? cost? accuracy? → verification tier). Read once; everything keys off it.
2. `dispatch_worker(request) -> WorkerResult` — spawn ONE isolated worker (prompt + owned_files + workdir); may read/write/run + return a **structured** result (extent per probe).
3. `worker_status(worker_id) -> WorkerStatus` — liveness / stall detection for the watchdog.
4. `run_command(command, cwd, shell) -> CommandResult` — ORCHESTRATOR-side exec (tests, git, verify). Distinct from a worker shell.
5. `resolve_model(role) -> str` — map `worker`/`setup`/`verify` to a concrete backend model id. Optional (non-abstract): `get_tokens_spent()`.

## Invariants

- The wave loop calls **only** `AgentDriver` methods — never `agent()`, `parallel()`,
  Read/Write/Bash tools, or `budget.spent()` directly. That is the seam.
- The orchestrator calls **only** `OrchestratorDriver.decide()`. Context packs are allowlist-only
  (STATE.md, BUILDLOG.md, tracker.json, MEMORY.md, explicit brief); arbitrary reads raise
  `ContextPackViolation` — **cardinal rule 4 in code**.
- `probe_capabilities()` must be **honest**. Defaults conservative (no native abilities, accuracy 0.0, tier 4) — optimism is opt-in, never default.
- **Weaker workers → higher verification tier.** Lower `tool_use_accuracy` raises `recommended_verification_tier`: cheaper backends RAISE the orchestrator's burden.
- Unknown roles in `resolve_model()` fall back to the worker model — a mis-typed role can never silently escalate cost.
- **Fail-safe verdicts**: `OrchestratorDriver.decide()` returns DECISION_FAILED after retries exhausted; never fabricates a passing verdict (never-green principle).
- **AdjudicationGate safety invariant** (increment 3): the final verdict is EITHER a confident
  challenger verdict OR the incumbent's; undetermined/DECISION_FAILED/low-confidence is NEVER final.
- stdlib-only, ASCII-only, Windows + Linux safe. Concrete adapters own any provider SDK, not this layer.

## Phase 2 (Codex) + Phase 3 (Bridge) Implementation Details

Capability matrix (as encoded): claude-code = parallel, worker fs/shell, worktree isolation,
native cost, accuracy ~0.99, tier 1; codex = none natively (orchestrator injects/runs; temp-dir;
usage-metadata cost), accuracy ~0.92, tier 2. NOTE: ClaudeCodeDriver.get_tokens_spent() is None BY CONTRACT (cost_ceiling ledger fallback).

Codex driver (Tier 2): injects file contents into prompt, calls OpenAI Chat Completions via injectable transport, validates JSON with bounded retry, enforces ownership. CRITICAL: Green = exit 0 only. Verification policy: tier 2 -> {validate_all_json:True, spot_check_frac:0.50, repair_cap:2, require_adversarial_review:True}. **P1 Security**: Default model map uses gpt-4o-mini (worker, supports json_schema); init-time guard rejects models lacking json_schema support unless `allow_unverified_models=True` (P1 gate: prevent gpt-3.5-turbo silent failures).

**build_manifest_item(driver, item)**: enriches a backlog item with model, verificationTier,
and the four verification_policy knobs — resolved ONCE as literal manifest fields (no
recompute drift); Claude tier-1 path stays byte-identical (also derives a baseline `acceptanceCriteria` from `testCmd` when none authored; authored wins; none = no-op). **dispatch_item(driver, item)**:
routes by worker_filesystem_access. HONESTY: ok=True ONLY on test exit 0, never from the
model's done:true; no testCmd -> ok/verified=False (reason='no_test_command'); exception ->
fail-safe False. Ownership enforced at driver level. Offline tests prove Codex+FakeTransport
takes a RED stub to green via real test exit 0.

## Status

Phases 1-3 shipped. All file I/O uses explicit `encoding="utf-8"`. For orchestrator-backend selection and seat-swap features (HS-1/HS-2), read driver/orchestrator-swap/CLAUDE.md.

## wave_loop.py Refactoring (Complexity Reduction)

**Date**: 2026-07-31

**What**: Decomposed `_run_wave_inner` function (1206 lines, cyclomatic complexity 141, grade F) into 19 specialized phase functions, each with cyclomatic complexity <= 20 (grade C or better). Main function now CC 13, a 91% reduction in complexity. <!-- metrics-verified: radon -m cc driver/wave_loop.py -s -->

**Why**: Reduced complexity improves maintainability, testability, and code comprehension. Large monolithic functions are difficult to reason about and modify safely. Extraction follows the documented 5-phase architecture: preflight validation, parallel build, bounded repair, orchestrator final-catch, and per-repo git ship.

**How**: Extracted functions organized by phase:
- **Preflight**: `_preflight_check_duplicate_slugs`, `_preflight_resolve_repos`, `_preflight_check_ownership`, `_resolve_verification_policy`, `_check_cost_ceiling`
- **Build**: `_build_items_parallel`, `_dispatch_single_item` (former nested closure now explicit parameters)
- **Repair**: `_repair_one_round`, `_verify_exact_gate`, `_dispatch_adversarial_review_phase`, `_repair_refuted_item`
- **Orchestrator**: `_run_orchestrator_final_catch_phase`
- **Ship**: `_ship_check_repo_toplevel`, `_collect_verified_items_for_ship`, `_ship_one_repo`, `_ship_add_files`, `_ship_commit_and_push`, `_ship_verified_items_per_repo`

**Safeguards**: All 19 control-flow abort escapes preserved exactly (same signal semantics to caller). Thread-safety preserved: `resume_stats_lock` guard timing unchanged. Journal writes: same format, same ordering, same claim-fence timing. Cost-ceiling checks: same abort points (preflight, repair, decisions). Git operations: same per-repo boundaries and error handling. Tests: all 32 wave_loop tests (test_wave_loop_rs3.py) pass with zero regressions.

**Encoding**: Fixed 2 encoding violations (subprocess.run without encoding parameter) at lines 504 and 564; now 0 violations.

**Metrics**: `_run_wave_inner` CC 141 → 13 (grade F → C). Total extracted functions: 19. Max extracted CC: 17 (still grade C). Test pass rate: 32/32 (100%). <!-- metrics-verified: npm run test:py -- tests/test_wave_loop_rs3.py -v -->

## wave_scheduler.py Refactoring (Complexity Reduction)

**Date**: 2026-07-31

**What**: Decomposed `run_wave_scheduler` function (377 lines, cyclomatic complexity 82, grade F) into 10 specialized phase functions, each with cyclomatic complexity <= 20 (grade C or better). Main function now CC 15, an 82% reduction in complexity. <!-- metrics-verified: radon cc driver/wave_scheduler.py -s -->

**Why**: Reduced complexity improves maintainability, testability, and code comprehension. The monolithic orchestrator mixed manifest loading, driver construction, dispatch, and outcome collection in a single 377-line function. Extraction follows the wave_loop pattern: identify phases, extract with explicit parameters (not closures), preserve all early-return paths exactly.

**How**: Extracted functions organized by phase:
- **Intake**: `_phase_intake_and_validate(tracker_path, max_items)` — tracker load + validation + disjoint selection (B/8)
- **Manifest**: `_phase_build_manifest(selected_items, selected_ids, driver)` — manifest construction (A/4)
- **Wave Execution**: `_phase_run_wave_and_process(...)` — wave execution + result processing + tracker update + report (C/16)
- **Result Processing**: `_phase_process_wave_result(wave_result, driver)`, `_build_items_shipped`, `_build_blocked_lane`, `_build_blocked_lane_entry`, `_build_orchestrator_gate`, `_derive_gate_status` — result extraction helpers (all A-B grades)
- **Tracker Update**: `_phase_update_tracker_and_derive_success(...)` — tracker update + conflict detection + success metrics (C/17)

**Safeguards**: All 12 control-flow abort escapes preserved exactly:
1. gates unavailable → return gate_unavailable report
2. halt check error → return halt report with error
3. is_halted (first check) → return halt report with reason
4. no valid items → return intake report with success=True
5. no selected items → return intake report with success=True
6. manifest build error → return manifest report with error
7. dry_run flag → return manifest report with success=True
8. ceiling check error → return ceiling report with error
9. ceiling_exceeded → return ceiling report with reason
10. final halt check error → return halt report with error
11. is_halted (final check) → return halt report with reason
12. wave dispatch exception → return error report with tracker state in envelope

Thread-safety: no locks in wave_scheduler, preserved as-is. Exception envelope carries `tracker_update_attempted` and `tracker_update_error` from nested phases, enabling recovery detection across report assembly crashes. Cost-ceiling check timing preserved (Phase 6, before final dispatch gates). Git operations: none in wave_scheduler, only via wave_loop. Tests: all 35 wave_scheduler tests pass with zero regressions.

**Encoding**: All file I/O uses explicit `encoding="utf-8"`. No encoding violations. <!-- metrics-verified: python tools/encoding_lint.py --check --paths driver/wave_scheduler.py -->

**Subprocess decoding (G10)**: every `subprocess.*` call in this domain that decodes output — `wave_loop.py`'s git/gh shell-outs — passes BOTH `encoding="utf-8"` AND `errors="replace"`. The encoding alone is only half the rule: strict UTF-8 decoding of one undecodable byte (0x97, the cp1252 em-dash, is the common one in branch names and PR titles) raises inside subprocess's reader THREAD, never reaches the caller, and silently leaves `stdout` as `None` so the next `.strip()` dies with a meaningless `AttributeError`. That crashed the merge queue on 24+ consecutive scheduled passes. `errors="ignore"` is forbidden — a corrupted byte must stay visible as U+FFFD, not vanish from a ref name the loop is about to act on. Enforced by `tools/encoding_lint.py`, which fail-closes repo-wide in the pre-push hook.

**Metrics**: `run_wave_scheduler` CC 82 → 15 (grade F → C). Total extracted functions: 10. Max extracted CC: 17 (still grade C). Test pass rate: 35/35 (100%). <!-- metrics-verified: python -m radon cc driver/wave_scheduler.py -s -->