# driver/ — AgentDriver backend-portability seam

**What**: the domain contract letting aesop's wave loop run on non-Claude backends
(Codex, open models); the loop dispatches only through the `AgentDriver` interface.
**Phases 1-3 shipped** (interface + reference adapter; Codex Chat Completions; wave bridge).

## Files

- **agent_driver.py** — the `AgentDriver` ABC + capability/request/result
  dataclasses. The contract. stdlib-only, no provider SDKs.
- **claude_code_driver.py** — reference adapter (Claude Code parity). Thin +
  documented: two ops are concrete Python, three are serviced by the harness.
- **codex_driver.py** — Phase 2 IMPLEMENTATION: OpenAI Chat Completions HTTP
  backend. Fully wired: dispatch_worker (file injection, JSON validation with
  retry, full-file replacement), run_command (subprocess), worker_status
  (in-memory registry), get_tokens_spent (aggregate usage). Transport injectable
  for offline testing.
- **openai_transport.py** — stdlib urllib transport for OpenAI Chat Completions
  endpoint. Injectable seam so tests feed canned responses (FakeTransport) with
  no API key or network.
- **openai_compatible_driver.py** — OpenAI-compatible backend (Ollama, OpenRouter, etc.).
- **verification_policy.py** — Maps verification tier -> orchestrator tuning (validate_all_json,
  spot_check_frac, repair_cap, require_adversarial_review).
- **wave_loop.py** — the wave ENGINE: preflight ownership guard, parallel build,
  bounded repair, adversarial review, per-repo batched git ship, recovery journal.
- **wave_bridge.py** — Phase 3: bridges AgentDriver backends to wave manifest items.
  build_manifest_item() enriches with verificationTier + model; dispatch_item() routes
  by capability and decides green ONLY from test exit code (not model's say-so).
- **backend_config.py** — HS-1 unified two-seat config: `seats.worker` + `seats.orchestrator`
  in aesop.config.json select BOTH seats from ONE block (legacy flat backend block still parses
  but is INERT in the scheduler default — seats is the opt-in surface; seats.worker wins when both
  present). `build_driver()` = worker seat (raw-dict seats promotion uses the same replace+validate
  path as the loader); `build_orchestrator_backend()` = decision seat (absent/harness/claude → null
  `HarnessOrchestratorBackend`; openai-compatible → configured backend). Guards: base_url SSRF check
  incl. DNS-resolved addrs (TTL-0 rebinding residual documented), `is_local` pinned to loopback
  base_url, `api_key_env` restricted to LLM-key-shaped names (deny SECRET/TOKEN/... fragments).
  NO seats block = byte-identical to today (no key needed).
- **context_pack.py** — OrchestratorDriver increment 1: build_context_pack() reads
  ONLY allowlisted control files (STATE.md, BUILDLOG.md, tracker.json, MEMORY.md, explicit
  brief: paths under repo/conductor roots). Enforces cardinal rule 4 ("orchestrator reads
  only the file brain") in code. Size-bounded with deterministic truncation (oldest-first
  for logs) and manifest tracking.
- **orchestrator_backend.py** — OrchestratorBackend protocol: decide_call(prompt, schema) → raw
  text. Real impl: OpenAICompatibleOrchestratorBackend (gpt-5 temperature fallback; seat knobs
  api_key_env + is_local dummy-key; validate_base_url in __init__). HarnessOrchestratorBackend =
  null default seat (decide_call raises: the live harness IS the orchestrator). Fake for tests.
- **orchestrator_driver.py** — OrchestratorDriver: uses OrchestratorBackend.decide_call()
  to make structured verdicts via OrchestratorBackend protocol (no AgentDriver coupling).
- **adjudication_gate.py** — increment 3 (conservative): two-tier escalation gate — cheaper
  challenger decides; undetermined/low-conf/disallowed-type/content-seeded-spot-check calls
  escalate to the incumbent (frontier). Never emits an unconfident verdict as final.
- **decisions/** — Decision type schema registry (sibling lane owns schemas; absent = optional).
- **../tests/** — test_agent_driver (contract), test_codex_driver_e2e (Phase 2 offline + gated
  live), test_wave_bridge (Phase 3 honest-green e2e), test_orchestrator_driver (increment 1:
  allowlist/ContextPackViolation, size cap, decide() retry+fail-safe, schema — all offline),
  test_adjudication_gate (increment 3: escalation + safety invariant + spot-check sampling).

## The five operations (what the wave loop needs from ANY backend)

1. `probe_capabilities() -> DriverCapabilities` — honest self-report (parallel? fs? shell? structured? worktree? cost? accuracy? → verification tier). Read once; everything keys off it.
2. `dispatch_worker(request) -> WorkerResult` — spawn ONE isolated worker (prompt + owned_files + workdir); may read/write/run + return a **structured** result (extent per probe).
3. `worker_status(worker_id) -> WorkerStatus` — liveness / stall detection for the watchdog.
4. `run_command(command, cwd, shell) -> CommandResult` — ORCHESTRATOR-side exec (tests, git, verify). Distinct from a worker shell.
5. `resolve_model(role) -> str` — map `worker`/`setup`/`verify` to a concrete backend model id.

Optional (non-abstract): `get_tokens_spent()`.

## Invariants

- The wave loop calls **only** `AgentDriver` methods — never `agent()`,
  `parallel()`, Read/Write/Bash tools, or `budget.spent()` directly. That is
  the seam.
- The orchestrator calls **only** `OrchestratorDriver.decide()` — never raw tool
  APIs or harness methods. Context packs are allowlist-only (STATE.md, BUILDLOG.md,
  tracker.json, MEMORY.md, explicit brief: paths under repo/conductor roots); arbitrary
  reads are a code-level violation (`ContextPackViolation`), not a convention. This
  **enforces cardinal rule 4 in code**.
- `probe_capabilities()` must be **honest**. Defaults are conservative (no
  native abilities, accuracy 0.0, tier 4) — optimism is opt-in, never default.
- **Weaker workers → higher verification tier.** Lower `tool_use_accuracy`
  raises `recommended_verification_tier`. Cheaper/weaker backends RAISE the
  orchestrator's burden; they do not lower it.
- Unknown roles in `resolve_model()` fall back to the worker model — a mis-typed
  role can never silently escalate cost.
- **Fail-safe verdicts**: `OrchestratorDriver.decide()` returns `{'verdict':
  'DECISION_FAILED', ...}` after retries exhausted; never fabricates a passing
  verdict (mirrors the worker seat's never-green principle).
- **AdjudicationGate safety invariant** (increment 3): the gate's final verdict is EITHER
  a confident challenger verdict OR the incumbent's verdict. It NEVER emits an undetermined/
  DECISION_FAILED/low-confidence challenger verdict as final. The gate is incumbent-safe
  by construction: every escalation to the incumbent preserves correctness.
- stdlib-only (`abc`, `dataclasses`, `typing`, `subprocess`), ASCII-only,
  Windows + Linux safe. Concrete adapters own any provider SDK, not this layer.

## Per-backend capability matrix (as encoded)

| Capability            | claude-code  | codex (Phase 2)       |
|-----------------------|:------------:|:---------------------:|
| parallel_dispatch     | yes          | no (ext loop)         |
| worker filesystem     | yes          | no (orch injects)      |
| worker shell          | yes          | no (orch runs)         |
| structured_output     | yes (~perfect)| yes (JSON schema)      |
| worktree_isolation    | yes          | no (temp-dir)         |
| native_cost_tracking  | yes          | yes (usage metadata)   |
| tool_use_accuracy     | ~0.99        | ~0.92                 |
| verification_tier     | 1            | 2                     |

## Phase 2 Codex Implementation Details

Codex driver (Tier 2): injects file contents into prompt, calls OpenAI Chat Completions via injectable transport, validates JSON with bounded retry, enforces ownership. CRITICAL: Green = exit 0 only. Verification policy: tier 2 -> {validate_all_json:True, spot_check_frac:0.50, repair_cap:2, require_adversarial_review:True}. **P1 Security**: Default model map uses gpt-4o-mini (worker, supports json_schema); init-time guard rejects models lacking json_schema support unless `allow_unverified_models=True` (P1 gate: prevent gpt-3.5-turbo silent failures).

## Phase 3 Bridge Implementation Details

**build_manifest_item(driver, item)**: enriches a backlog item with model (resolve_model),
verificationTier (probe), and the four verification_policy knobs — resolved ONCE as literal
manifest fields so the template cannot recompute/drift; Claude tier-1 path stays byte-identical
(repairCap=1, requireAdversarialReview=false, spotCheckFrac=0.10, validateAllJson=false).
**dispatch_item(driver, item)**: routes by worker_filesystem_access (True -> harness route;
False -> orchestrator-managed dispatch_worker + run_command test). HONESTY: ok=True ONLY on
test exit 0, never from the model's done:true; no testCmd -> ok=False, verified=False,
reason='no_test_command' ("no test" != "verified"); exception -> fail-safe False. Ownership
enforced at driver level. Offline tests prove Codex+FakeTransport takes a RED unittest stub
to green via real test exit 0 (no API key, no network).

## Wave Scheduler (WS3a Pilot) + GATE-1 Handoff Kit

**wave_scheduler.py** — single-cycle backlog orchestration: intake up to N file-disjoint todo items from tracker.json (empty/missing ownsFiles REJECTED; paths normalized posix+casefold-on-Windows before overlap checks; required fields pre-validated) -> manifest via build_manifest_item (model + verificationTier from driver.probe) -> HALT + cost-ceiling gates (fail-CLOSED: module import failure or check exception = abort with honest Report, phase=gate_unavailable) -> run_wave (recovery journal + git ship) -> STOP before merge; Report JSON with per-item observability (GATE-1). After ship, selected items atomically marked in_progress in tracker (temp+os.replace; dry-run never mutates) so a second run cannot double-dispatch.

**CLI** (HS-1): `python driver/wave_scheduler.py --tracker <path> --max-items N --dry-run|--execute [--driver claude|codex] [--config <path>]`. Default: worker seat from aesop.config.json seats.worker ONLY (no seats block → claude; a bare legacy flat block stays inert — migrate to seats.worker) — the seats path also reaches openai-compatible; `--driver` OVERRIDES the config. Hosted seat + --execute requires the seat's api_key_env (is_local: none); dry-run never needs a key.

**Tests** (35+): disjoint/normalization/rejection, gate fail-closed, dry-run, GATE-1 per-item/driver/ceiling/codex tests; module-tmpdir hygiene; all TestCase.

**Invariants**: stdlib-only, ASCII, Windows+Linux safe (list-form subprocess); manifest items carry resolved policy knobs from verification_policy (no recompute drift); merge stays manual in the pilot.

### REPORT-CONTRACT (GATE-1 Orchestrator Handoff)

Scheduler emits a Report JSON the orchestrator uses for merge eligibility. Fields: phase
(dispatch|intake|halt|ceiling|gate_unavailable|manifest), wave_id, items_selected[],
items_shipped[] ({slug, backend, tier 1-4|null, verified — test-exit-0-only, false = NOT
PROVEN, testExit}), merged (pilot: always false, manual merge), success, timestamp,
branch/sha (set on ship), halt_reason/ceiling_reason/error (optional). Ceiling is checked
BEFORE run_wave dispatch (phase=ceiling); mid-wave trips are run_wave's responsibility.
Tracker sync: LOUD on unmapped slugs (tracker_unmapped_slugs -> success false). Full JSON
shape lives in wave_scheduler.py's module docstring.

## Status

- **Phase 1**: shipped. Interface + Claude reference adapter + contract tests.
- **Phase 2**: shipped. Codex OpenAI Chat Completions implementation. Offline tests GREEN.
- **Phase 3**: shipped. Wave bridge: driver → manifest, orchestrator-side dispatch.
  Proves non-Claude backends drive items end-to-end with honest green (test exit 0 only).
- **Wave Scheduler (WS3a) + GATE-1**: shipped. Single-cycle orchestration: intake → manifest → dispatch → report (manual merge). Per-item observability, driver injection (--driver claude|codex), ceiling semantics documented.