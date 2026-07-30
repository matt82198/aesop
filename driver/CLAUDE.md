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
  bounded repair, per-repo batched git ship, recovery journal. Supports optional
  orchestrator_backend for live-seat swap (see driver/orchestrator-swap/). RS3-W + RS5
  claim lifecycle: fail-CLOSED gate, fingerprint-bound journal, deterministic claim state.
  Tests: test_wave_loop_rs3.
- **wave_bridge.py** — Phase 3: bridges AgentDriver backends to wave manifest items
  (build_manifest_item / dispatch_item; green ONLY from test exit code, see below).
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