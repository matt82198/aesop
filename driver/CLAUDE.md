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
  bounded repair, per-repo batched git ship, recovery journal. HS-2 (live seat swap):
  `run_wave(orchestrator_backend=...)` Phase 6 routes ONE final_catch decision per
  test-VERIFIED item through a configured orchestrator seat (schema: decisions/
  final_catch.schema.json): merge=approve; block=verified flipped False, not shipped,
  journal rewritten, AND written files QUARANTINED (FILE paths only — empty/dot/directory
  pathspecs rejected with per-file error records, no cross-item destruction; tracked ->
  git checkout, untracked deleted ONLY on clean ls-files exit-1, ambiguous = error never
  delete; globs `:(literal)`-neutralized; backslashes normalized; outside git = honest
  skip, surfaced in Report.blocked); escalate/undetermined/DECISION_FAILED=ship with
  honest record (crash-only degradation — a seat outage never fabricates or blocks).
  None/null harness backend -> "deferred", byte-identical to pre-HS-2 (no key, no
  backend constructed). Repair stays mechanical on both seats. orchestrator_review
  carries verdict_counts, blocked_detail {slug,reason}, seat_tokens_spent and
  gate_status ("degraded" when all decisions failed); after decisions the ceiling is
  re-checked with driver+seat spend (abort_reason=cost_ceiling_exceeded_after_decisions;
  an unmetered None driver spend never crashes — seat-only spend or ledger fallback) —
  live-seat path only. RS3-W round-2 robustness: claim gate REAL + fail-CLOSED
  (coordination reads EventStore streams, TTL expiry, stale-lease release by slug key;
  claim errors SKIP — never claim-less dispatch/repair); journal entries
  fingerprint-bound + atomic (reused slug never inherits stale verified state; torn
  writes can't corrupt resume); resumed-verified items restore filesWritten and always
  reach a terminal shipped record (honest no_changes no-op ship — no recovery livelock);
  dup slugs rejected at preflight; executor failures recorded, green never vacuous;
  Windows quoting doubles only quote-preceding backslashes. RS5 claim lifecycle:
  wave-level instance id, ttl sized to the driver command timeout (10x, 1h floor — never
  the 300s default a real build outlives); claim HELD across build -> repair -> ship,
  released exactly once in run_wave's finally (every exit path); repair re-dispatch and
  ship FENCED via current_holder (lost claim = honest claim_lost abort, never
  double-ship; try_claim retracts on error, no phantom holder). Tests: test_wave_loop_rs3.
- **wave_bridge.py** — Phase 3: bridges AgentDriver backends to wave manifest items
  (build_manifest_item / dispatch_item; green ONLY from test exit code, see below).
- **backend_config.py** — HS-1 unified two-seat config: `seats.worker` + `seats.orchestrator`
  in aesop.config.json select BOTH seats from ONE block (legacy flat block parses but is
  INERT in the scheduler default; seats.worker wins when both present). `build_driver()` =
  worker seat (raw-dict paths run the loader's validation); `build_orchestrator_backend()`
  = decision seat (absent/harness/claude → null `HarnessOrchestratorBackend`;
  openai-compatible → configured backend). Guards: base_url SSRF check incl. time-bounded
  DNS resolution; `is_local` pinned to loopback (key waiver keys off VALIDATED is_local,
  never URL text); `api_key_env` allowlist-primary (SECRET/TOKEN/... fragments
  hard-reject, other key-shaped names loud NOTICE). NO seats block = byte-identical to
  today (no key needed).
- **context_pack.py** — build_context_pack() reads ONLY allowlisted control files
  (STATE.md, BUILDLOG.md, tracker.json, MEMORY.md, explicit brief: under repo/conductor
  roots) — cardinal rule 4 in code. Size-bounded, deterministic truncation, manifest.
- **orchestrator_backend.py** — OrchestratorBackend protocol: decide_call(prompt, schema)
  → raw text. Real impl: OpenAICompatibleOrchestratorBackend (gpt-5 temperature fallback;
  api_key_env + is_local dummy-key; validate_base_url in __init__). HarnessOrchestratorBackend
  = null default seat (decide_call raises: the live harness IS the orchestrator). Fake for tests.
- **orchestrator_driver.py** — OrchestratorDriver: structured verdicts via the
  OrchestratorBackend protocol (no AgentDriver coupling).
- **adjudication_gate.py** — increment 3 (conservative): two-tier escalation gate — cheaper
  challenger decides; undetermined/low-conf/disallowed-type/content-seeded-spot-check calls
  escalate to the incumbent (frontier). Never emits an unconfident verdict as final.
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
recompute drift); Claude tier-1 path stays byte-identical. Also derives a BASELINE
`acceptanceCriteria` from `testCmd` when the item has none of its own (authored criteria
always win, untouched; no testCmd + no authored criteria = no key set at all, true no-op —
byte-identical build prompt). Shape matches wave-flat-dispatch.template.mjs's
`acceptanceCriteriaSection`: `[{statement, verifiable_by}]`. **dispatch_item(driver, item)**:
routes by worker_filesystem_access. HONESTY: ok=True ONLY on test exit 0, never from the
model's done:true; no testCmd -> ok/verified=False (reason='no_test_command'); exception ->
fail-safe False. Ownership enforced at driver level. Offline tests prove Codex+FakeTransport
takes a RED stub to green via real test exit 0.

## Wave Scheduler (WS3a Pilot) + GATE-1 Handoff Kit

**wave_scheduler.py** — single-cycle backlog orchestration: intake up to N file-disjoint todo items from tracker.json (empty/missing ownsFiles REJECTED; paths normalized posix+casefold-on-Windows before overlap checks; required fields pre-validated) -> manifest via build_manifest_item (model + verificationTier from driver.probe) -> HALT + cost-ceiling gates (fail-CLOSED: module import failure or check exception = abort with honest Report, phase=gate_unavailable) -> run_wave (recovery journal + git ship) -> STOP before merge; Report JSON with per-item observability (GATE-1). After ship, selected items atomically marked in_progress in tracker (temp+os.replace; dry-run never mutates) so a second run cannot double-dispatch.

**CLI** (HS-1): `python driver/wave_scheduler.py --tracker <path> --max-items N --dry-run|--execute [--driver claude|codex] [--config <path>]`. Default: worker seat from aesop.config.json seats.worker ONLY (no seats block → claude; a bare legacy flat block stays inert — migrate to seats.worker) — the seats path also reaches openai-compatible; `--driver` OVERRIDES the config. Hosted seat + --execute requires the seat's api_key_env (is_local: none); dry-run never needs a key. HS-2: seats.orchestrator resolved by `resolve_orchestrator_backend()` (absent/harness/claude → None = live harness stays orchestrator; openai-compatible → live backend, same --execute key gate) and passed into run_wave; Report JSON shape is IDENTICAL either way (swap transparency — seat activity lives in run_wave's result: orchestrator_review + per-item final_catch, plus a stderr notice).

**Tests** (35+): disjoint/normalization/rejection, gate fail-closed, dry-run, GATE-1 per-item/driver/ceiling/codex tests; module-tmpdir hygiene; all TestCase. **Invariants**: stdlib-only, ASCII, Windows+Linux safe (list-form subprocess); manifest items carry resolved policy knobs from verification_policy (no recompute drift); merge stays manual in the pilot.

### REPORT-CONTRACT (GATE-1 Orchestrator Handoff)

Scheduler emits a Report JSON the orchestrator uses for merge eligibility. Fields: phase
(dispatch|intake|halt|ceiling|gate_unavailable|manifest), wave_id, items_selected[],
items_shipped[] ({slug, backend, tier 1-4|null, verified — test-exit-0-only, false = NOT
PROVEN, testExit}), merged (pilot: always false, manual merge), success, timestamp,
branch/sha (set on ship), halt_reason/ceiling_reason/error (optional). Live seat ONLY
(HS-2 block gate; default shape unchanged): blocked[] ({slug, reason, quarantine:
clean|errors|skipped|unknown, + quarantine_errors / quarantine_skipped_reason —
a failed quarantine means refused code may still be in the tree}; item also gets
TERMINAL tracker status "blocked" — never re-selected) + orchestrator_gate {seat, model,
decisions, verdict_counts, blocked, decision_failed, seat_tokens_spent, status
active|degraded|no_decisions}; any block -> success false. Ceiling is checked
BEFORE run_wave dispatch (phase=ceiling); mid-wave trips are run_wave's responsibility.
Tracker sync: LOUD on unmapped slugs (tracker_unmapped_slugs -> success false). Full JSON
shape lives in wave_scheduler.py's module docstring.

## Status

Phases 1-3 + Wave Scheduler (WS3a/GATE-1) + HS-1 + HS-2 seat swap shipped (proof:
bench/results/hs2-swap-proof-2026-07-25.md; merge manual in pilot) + RS3-W round-2
robustness + RS5 claim lifecycle (see wave_loop.py bullet).