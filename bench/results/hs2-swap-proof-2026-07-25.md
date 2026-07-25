# HS-2 orchestrator-seat swap proof (offline + bounded live) -- 2026-07-25

## What was built

HS-1 shipped `build_orchestrator_backend(config)` but its only consumers were
the two shadow-adjudication bench tools -- `seats.orchestrator` changed nothing
in the live wave path. HS-2 wires the configured seat into the live path:

- `run_wave(..., orchestrator_backend=...)` (driver/wave_loop.py): Phase 6
  (previously always `adversarial_review = "deferred"`) now routes a
  `final_catch` decision per test-VERIFIED item through
  `OrchestratorDriver.decide()` against the configured backend, validated
  against `driver/decisions/final_catch.schema.json`.
- `run_wave_scheduler(..., orchestrator_backend=...)` +
  `resolve_orchestrator_backend()` (driver/wave_scheduler.py): the CLI reads
  `seats.orchestrator` from aesop.config.json and passes a live backend
  through; `--execute` on a hosted seat requires the seat's `api_key_env`
  (mirrors the worker-seat gate); dry runs never need a key.

Verdict semantics (conservative, incumbent-safe; decision SEMANTICS of the
default path unchanged):
- `merge` -> approved; ships exactly as today.
- `block` -> verified flipped False; item does NOT ship; journal updated so a
  resume cannot skip-and-ship it.
- `escalate` / `undetermined` / `DECISION_FAILED` -> degrade to today's
  behavior (ship to branch, merge stays manual downstream) with an honest
  per-item record. A seat outage never fabricates a verdict and never blocks
  a test-proven item (crash-only degradation).

## What is PROVEN

1. **Hard no-op invariant** (offline, tests/test_hs2_swap_proof.py):
   with no `seats.orchestrator` block -- or the null harness seat -- the wave
   engine behavior is byte-identical to pre-HS-2: `adversarial_review` stays
   `"deferred"`, no `orchestrator_review` / `final_catch` keys exist anywhere,
   the default-path Report JSON key set is exactly the pre-HS-2 contract, and
   a full scheduler execute run completes with the OpenAI key env var REMOVED
   (no OpenAI backend is constructed). A configured seat is strictly opt-in.

2. **The seat is genuinely live** (offline): with a configured backend, the
   decision routes through it (prompt carries the decision type + item under
   review; call counts asserted) and the verdict has real effect -- `block`
   stops the ship and rewrites the journal; only verified items reach the
   seat; an exhausted/erroring backend degrades to today's behavior with a
   `decision_failed_deferred` record, never a fabricated verdict.

3. **Swap transparency, end-to-end** (offline): the same task driven through
   the public scheduler path with (a) the default harness seat and (b) a
   swapped `FakeOrchestratorBackend`, both on a non-Claude fake worker seat,
   yields an INVARIANT human interface and state layer: identical Report JSON
   key sets (top-level and per-item), identical values for
   slug/backend/tier/verified/testExit, identical tracker terminal state
   (`in_progress`) and structure, identical journal file names and entry key
   sets. The swapped backend demonstrably decided (call_count == 1).

4. **Bounded live proof** (hs2-swap-proof-2026-07-25.json): ONE real task
   (fix a broken `multiply`) through `run_wave` twice -- arm A default
   harness seat, arm B `seats.orchestrator` = openai-compatible gpt-4o-mini --
   both on a live codex (gpt-4o-mini) worker seat built from a seats config.
   Both arms: dispatched, test exit 0, verified True. Arm B's gpt-4o-mini
   seat returned a schema-valid `final_catch` verdict (`merge`, evidence +
   confidence) on the first attempt. Result shape invariant modulo exactly
   the two documented opt-in keys (`orchestrator_review`, `final_catch`).
   Spend: 3 gpt-4o-mini calls total (~1.1k worker tokens + one decision
   call) -- well under the US$2 cap. `git=None`: the live proof never ships.

## Bounds (what is NOT claimed)

- The live proof is one tiny task, one model (gpt-4o-mini), one repeat. It
  proves the plumbing end-to-end (config -> seat -> real API -> schema-valid
  verdict -> effect recorded), not decision QUALITY. Seat quality remains the
  subject of the shadow-adjudication / seated-A-B bench line.
- The wave engine's `final_catch` is the pre-SHIP gate inside run_wave (the
  pilot still stops before merge; merge stays manual). Orchestrator decisions
  outside the engine -- backlog ranking, in-session adjudication done by the
  live harness, PR merges -- are NOT routed through the seat by HS-2.
- Repair remains mechanical (bounded retry on test failure) on both seats by
  design: HS-2 changes who decides, never the decision semantics.
- `wave_loop.py`'s standalone CLI (`--manifest` mode) still constructs
  ClaudeCodeDriver directly and does not read the config; the config-driven
  entry point is `wave_scheduler.py` (deferred, documented).
- Report JSON deliberately gains NO new keys (swap transparency + no-op
  byte-identity); seat activity is observable in the run_wave result
  (`orchestrator_review`, per-item `final_catch`) and a stderr notice.
