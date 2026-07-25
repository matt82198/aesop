# AgentDriver — multi-model portability for aesop

> Run aesop's wave loop on backends other than Claude Code — Codex, and
> eventually open models — through one narrow interface.

This directory contains the **AgentDriver seam** (Phase 1), a working Codex
implementation (Phase 2), and the wave bridge connecting backends to orchestrator
dispatch (Phase 3). The phase-1 abstraction seam is the interface; the
phase-2 Codex driver proves a non-Claude backend can execute a real coding task
end-to-end and produce orchestrator-verified results, entirely offline (no API
key, no network in CI tests). Phase 3 routes work items through any backend with
honest verification (test exit code only) and backend-driven verification tier.

---

## The problem

Aesop's orchestration core — the wave / flat-dispatch cycle — is written against
Claude Code's Workflow harness. It calls `agent()`, `parallel()`, the
Read/Write/Bash tools, and `budget.spent()` directly. Those calls only exist
inside Claude Code, so the wave loop cannot run anywhere else.

The spike's finding: ~80% of aesop (daemons, tools, state_store, MCP, UI) is
already portable. The coupling is concentrated in **how the wave loop talks to
its execution backend**. Extract that into one interface and the loop becomes
backend-agnostic.

## The abstraction

`AgentDriver` (in `agent_driver.py`) is that interface — an abstract base class
with **five** operations, which are everything the wave loop needs from any
backend:

| # | Operation                         | What it gives the wave loop                                  |
|---|-----------------------------------|--------------------------------------------------------------|
| 1 | `probe_capabilities()`            | Honest self-report → drives the whole verification strategy. |
| 2 | `dispatch_worker(request)`        | Spawn one isolated worker (read/write files, run shell, return structured result). |
| 3 | `worker_status(worker_id)`        | Liveness / stall detection for the watchdog.                 |
| 4 | `run_command(cmd, cwd, shell)`    | Orchestrator-side execution: tests, git, verification.       |
| 5 | `resolve_model(role)`             | Abstract role (`worker`/`setup`/`verify`) → concrete model.  |

Plus one optional op: `get_tokens_spent()`.

The wave loop calls **only** these methods. A new backend is a new subclass — no
changes to the orchestration algorithm. That is the entire point of the seam.

### What maps where (Claude Code reference)

In Claude Code the actual dispatch runs inside the harness, not in this Python
process. So the reference adapter (`claude_code_driver.py`) is deliberately thin:

| Operation            | Claude Code mechanism        | Concrete here? |
|----------------------|------------------------------|:--------------:|
| `probe_capabilities` | static known-good facts      | yes            |
| `resolve_model`      | Anthropic model-name map     | yes            |
| `run_command`        | Bash tool (subprocess out of harness) | yes   |
| `dispatch_worker`    | Workflow `agent()`/Task tool | harness-serviced |
| `worker_status`      | harness + heartbeat files    | harness-serviced |

The two harness-serviced ops raise a clear, explained error out of harness
rather than fake a Claude agent from plain Python. When the wave-flat-dispatch
template is refactored onto the driver (Phase 1, below), these become the
documented handoff points to the harness.

---

## Per-backend capability matrix

Encoded honestly in each driver's `probe_capabilities()` — the numbers are the
spike's findings as data, so the orchestrator can plan **before** any CLI wiring
exists:

| Capability             | **claude-code** | **codex** (Phase 2)       | open-model (future)        |
|------------------------|:---------------:|:-------------------------:|:--------------------------:|
| parallel_dispatch      | native          | no — external event loop  | no — external event loop   |
| worker filesystem I/O  | yes (tools)     | no — orchestrator injects | no — orchestrator injects  |
| worker shell           | yes (Bash tool) | no — orchestrator runs    | no — orchestrator runs     |
| structured output      | yes (~perfect)  | yes (JSON schema)         | partial — regex recovery   |
| worktree isolation     | git-worktree    | no — temp-dir             | no — temp-dir              |
| native cost tracking   | `budget.spent()`| usage metadata (real)     | manual (len/4 estimate)    |
| tool-use accuracy      | ~0.99           | ~0.90–0.95                | ~0.70–0.85                 |
| **verification tier**  | **1**           | **2**                     | **4**                      |

Read the trend down the last row — it is the design's thesis, not an accident.

---

## The key insight — verification is load-bearing

> **Cheaper / weaker workers RAISE the need for the verification layer aesop
> already built. They do not lower the orchestrator's burden — they shift cost
> from inference to orchestration.**

Aesop's original bet was "Haiku suffices as a subagent because its tool-use
accuracy is ~99%, so the orchestrator only spot-checks lightly." A weaker
backend breaks that assumption:

- **Codex (gpt-3.5/4-turbo, ~90–95%)** returns 5–10% malformed JSON. The
  orchestrator must validate every output and budget ≥2 repair rounds → **Tier 2**.
- **Open models (~70–85%)** return 15–30% malformed JSON. Every Build result
  needs validation + strong spot-check (Sonnet-verified), with high triage
  escalation → **Tier 4**.

So the money saved on cheap inference is partly re-spent on orchestrator
verification (the spike models DeepSeek at ~6.7× cheaper than Haiku, not the
headline 10×, once recovery is counted). This is exactly why aesop's existing
adversarial-review / mutation / verification tooling is **load-bearing** for
portability: it is the thing that makes weak-but-cheap backends viable at all.
`recommended_verification_tier` on every `DriverCapabilities` encodes this
inverse relationship — lower accuracy, higher tier — and the test suite asserts
it as an invariant (`TestVerificationThesisEncoded`).

The Haiku baseline is **unchanged**: it stays Tier 1. Portability *extends* the
system; it does not replace the original principle (autonomous fleet work needs
high-confidence subagents **plus** strong orchestration).

---

## Phased roadmap

| Phase | Scope                                      | Status                     |
|-------|--------------------------------------------|----------------------------|
| **1** | Driver interface + Claude Code parity      | **shipped**                |
|       | Reference adapter, honest Codex stub, tests| in this directory          |
|       | Refactor wave-flat-dispatch onto the driver| next (harness handoff pts) |
| **2** | Codex OpenAI Chat Completions HTTP backend | **shipped**                |
|       | Transport seam (urllib, injectable); dispatch_worker (file injection, JSON validation with retry, full-file replacement); run_command (subprocess); worker_status (in-memory registry); verification_policy mapping; offline test suite with FakeTransport + gated live test | implemented in `codex_driver.py` + `openai_transport.py` + `verification_policy.py` + tests |
| **3** | Wave bridge + verification policy routing  | **shipped**                |
|       | Connect AgentDriver backends to wave-flat-dispatch manifest items; honest green (test exit code only); backend-driven verification tier. OpenAI-compatible driver for hosted models (OpenRouter, etc.) and local models (Ollama). | implemented in `wave_bridge.py` + `openai_compatible_driver.py` + tests |
| **4** | Open-model runner library                   | future                     |
|       | Dedicated Ollama/local-model adapter, per-model prompt tuning, error-recovery protocol, Tier-4 enforcement, `bench/` accuracy benchmark | future |

**Deployment posture** (from the spike): Claude Code is production. Codex and
open models stay **experimental / opt-in** until their tiers are proven —
recommend Haiku for production fleets.

---

## Usage

```python
import sys
sys.path.insert(0, "driver")           # bare imports within the domain

from claude_code_driver import ClaudeCodeDriver
from codex_driver import CodexDriver
from verification_policy import verification_policy
from backend_config import build_driver, load_backend_config, describe_backend

for driver in (ClaudeCodeDriver(), CodexDriver()):
    caps = driver.probe_capabilities()
    print(caps.summary())              # ASCII one-liner for logs/dashboards
    print("worker model ->", driver.resolve_model("worker"))
    
    # The verification policy is RESOLVED in Python (verification_policy function)
    # and baked into the manifest by build_manifest_item. JS consumes these literal fields;
    # it does NOT recompute the policy (that was the drift trap).
    policy = verification_policy(caps)
    print("verification tier:", caps.recommended_verification_tier)
    print("  repair_cap:", policy['repair_cap'])
    print("  require_adversarial_review:", policy['require_adversarial_review'])
    print("  spot_check_frac:", policy['spot_check_frac'])
    print("  validate_all_json:", policy['validate_all_json'])
```

### Running other models (OpenAI-compatible backends)

Aesop can target any OpenAI Chat Completions-compatible endpoint: OpenRouter,
Together AI, Ollama (local), or any service offering a compatible API. Use the
`OpenAICompatibleDriver`:

```python
from openai_compatible_driver import OpenAICompatibleDriver

# OpenRouter (hosted model, Tier 2)
driver = OpenAICompatibleDriver(
    base_url="https://openrouter.ai/api/v1",
    model="openrouter/auto",           # or specific model like "openai/gpt-4-turbo"
    api_key_env="OPENAI_API_KEY",      # env var name (default)
)

# Local Ollama (small/local model, Tier 3)
driver = OpenAICompatibleDriver(
    base_url="http://localhost:11434/v1",
    model="neural-chat",
    is_local=True,                      # Marks as local -> tier 3, higher verification
)

# Together AI
driver = OpenAICompatibleDriver(
    base_url="https://api.together.xyz/v1",
    model="meta-llama/Llama-2-70b-chat",
    api_key_env="TOGETHER_API_KEY",
)
```

All OpenAI-compatible backends run through the **Phase 2 orchestrator-managed
execution contract**: the orchestrator injects file contents, validates JSON
output, writes files, and runs tests. No backend has native filesystem/shell
access. The driver reports its honest verification tier (2 for hosted strong
models, 3 for local/small models), and the wave template enforces the
appropriate verification policy (validate all JSON, spot-check, repair bounds).

To run aesop against a backend:

1. Set up the environment (API key, endpoint URL).
2. Instantiate the driver.
3. Pass it to your wave orchestration loop:
   ```python
   caps = driver.probe_capabilities()
   policy = verification_policy(caps)
   # Proceed with wave dispatch, respecting policy.verification_tier
   ```

**Important note**: Non-agentic backends (Ollama, smaller models) run at a higher
verification tier (3+) because their tool-use accuracy is lower. The cost of
cheaper inference is re-spent on orchestrator verification. See the verification
thesis in the README for details.

### Configuring a backend

Aesop's backend can be configured via an `aesop.config.json` file in the
repository root (or any other path). This allows dropping a single JSON file to
switch backends without changing code. The configuration is **offline-safe**:
building a driver requires no API key; keys are read from environment variables
at call time during live dispatch.

**Unified two-seat config (0.4.0, HS-1)** — one namespaced block selects BOTH
seats: `seats.worker` (the coding agents; same fields as the legacy block
below, wins over it when both are present) and `seats.orchestrator` (the
decision seat; `"harness"` = the live Claude Code session, the default;
`"openai-compatible"` routes `OrchestratorDriver.decide()` to an API model
with `model`/`base_url`/`api_key_env`/`is_local`):

```json
{
  "seats": {
    "worker": { "backend": "claude" },
    "orchestrator": { "backend": "openai-compatible", "model": "gpt-4o-mini" }
  }
}
```

`build_driver(load_backend_config())` builds the worker seat;
`build_orchestrator_backend(load_backend_config())` builds the orchestrator
seat (returning the null `HarnessOrchestratorBackend` -- whose `decide_call`
raises -- when the seat is absent or `harness`/`claude`). With **no** `seats`
block, behavior is byte-identical to today: Claude Code worker + harness
orchestrator, no OpenAI backend constructed, no key required -- and a bare
legacy flat block (below) stays INERT in `wave_scheduler`'s default path
(it was dead config before 0.4.0; migrate it to `seats.worker` to opt in).
Consumers: `wave_scheduler.py` (worker seat from `seats.worker` only;
`--driver` overrides) and the shadow adjudication tools (orchestrator seat;
`--model` overrides).

Guardrails: `base_url` is SSRF-checked (scheme, IP literals, AND
DNS-resolved addresses; TTL-0 rebinding residual documented in
`validate_base_url`), `is_local: true` requires a loopback `base_url`
(localhost/127.0.0.1/::1 -- it disables the key requirement), and
`api_key_env` must be an LLM-key-shaped name (`*_KEY`/`*_API_KEY`, no
SECRET/TOKEN/... fragments) so a config cannot exfiltrate arbitrary env
secrets as Bearer tokens.

**Configuration schema** (legacy/flat worker block; parses + validates, honored
by direct `build_driver()` callers, but inert in the scheduler default -- see
above):
```json
{
  "backend": "claude" | "codex" | "openai-compatible",
  "model": "...",                    // Required for codex, openai-compatible
  "base_url": "...",                 // Required for openai-compatible
  "api_key_env": "OPENAI_API_KEY",   // Optional (default: OPENAI_API_KEY)
  "is_local": false,                 // Optional, for openai-compatible only
  "max_owned_bytes": 200000,         // Optional file-size limit
  "max_retries": 2,                  // Optional retry cap for malformed JSON
  "timeout_s": 120.0                 // Optional HTTP timeout
}
```

**Default**: If no config file exists, aesop uses Claude Code (preserves today's
behavior).

**Example configurations**:

```python
# backend_config.py provides helpers to load and build drivers from JSON:

from backend_config import load_backend_config, build_driver, describe_backend

# Load from aesop.config.json (or default to Claude)
config = load_backend_config()  # path="aesop.config.json" by default

# Instantiate the driver (offline-safe; no API key required at build time)
driver = build_driver(config)

# Describe the backend for logging
print(describe_backend(config))
# Example output: "claude-code: parallel=1 wfs=1 ... tier=1"
```

**Claude Code** (production):
```json
{
  "backend": "claude"
}
```
- Haiku workers, Tier 1 verification
- No API key needed (Claude Code manages auth)
- Recommended for production

**OpenAI Codex** (experimental):
```json
{
  "backend": "codex",
  "model": "gpt-4o-mini"
}
```
- Requires `OPENAI_API_KEY` environment variable set
- Tier 2 verification (validate all JSON, spot-check, repair budget)
- Cheaper than Claude but needs higher orchestrator burden

**OpenAI-compatible hosted model** (OpenRouter, experimental):
```json
{
  "backend": "openai-compatible",
  "base_url": "https://openrouter.ai/api/v1",
  "model": "openai/gpt-4-turbo",
  "api_key_env": "OPENROUTER_API_KEY"
}
```
- Requires the named environment variable set
- Tier 2 verification (hosted strong model)
- Supports any OpenAI-compatible endpoint (OpenRouter, Together, etc.)

**OpenAI-compatible local model** (Ollama, experimental):
```json
{
  "backend": "openai-compatible",
  "base_url": "http://localhost:11434/v1",
  "model": "neural-chat",
  "is_local": true
}
```
- No API key required (uses dummy `local-only` if not set)
- Tier 3 verification (local small model: validate all, heavy spot-check, adversarial review)
- Requires `ollama serve` running on localhost:11434

**How to run**:

1. Copy `driver/aesop.config.example.json` to `aesop.config.json` in the repo root
2. Edit `aesop.config.json` to select a backend and set any required fields
3. For non-Claude backends, set the API key environment variable:
   ```bash
   export OPENAI_API_KEY=sk-...        # for Codex
   export OPENROUTER_API_KEY=sk-...    # for OpenRouter
   # Ollama needs no key
   ```
4. Run aesop; the orchestration loop loads the config and instantiates the driver (from backend_config):
   ```python
   from backend_config import load_backend_config, build_driver
   
   config = load_backend_config()
   driver = build_driver(config)
   # Pass driver to wave orchestration loop
   ```

**Testing**: All drivers build offline (no API key required at import or build
time). Keys are read from `os.environ` only at dispatch time if a live call is
made. Tests can inject a `FakeTransport` to avoid network entirely.

### Wiring verification tier into a wave manifest

When building a manifest for `wave-flat-dispatch.template.mjs`, the backend's
verification policy is RESOLVED in Python (via `build_manifest_item`) and baked
into the manifest as literal fields:

```python
from wave_bridge import build_manifest_item

item = {
    "slug": "fix-test",
    "ownsFiles": ["test.py"],
    "prompt": "Fix the test",
    "testCmd": "python -m unittest test",
}

# build_manifest_item resolves ALL FOUR policy knobs from the driver's tier
# and includes them in the manifest as literal fields (no JS recomputation).
manifest_item = build_manifest_item(driver, item)

# Result includes:
#   "model": "haiku"  (from driver.resolve_model)
#   "verificationTier": 1  (from driver.probe_capabilities)
#   "repairCap": 1  (from verification_policy)
#   "requireAdversarialReview": false  (from verification_policy)
#   "spotCheckFrac": 0.10  (from verification_policy)
#   "validateAllJson": false  (from verification_policy)
```

The wave template **consumes these literal fields directly** — it does NOT
recompute the policy. This eliminates the JS/Python drift trap: the single source
of truth is Python's `verification_policy()` function; JS just uses the manifest
values. See `skills/buildsystem/wave-flat-dispatch.template.mjs` for the complete
template arguments and consumption logic.

Run the contract tests:

```
python -m unittest tests.test_agent_driver
python -m unittest tests.test_codex_driver_e2e
python -m unittest tests.test_openai_compatible_driver
```

## Design constraints

- **stdlib-only** at this layer (`abc`, `dataclasses`, `typing`, `subprocess`).
  Provider SDKs (`openai`, `ollama`, …) belong to the concrete adapters, added
  in Phases 2–3 — the interface stays importable everywhere.
- **ASCII-only**, **Windows + Linux safe**.
- **Honest probes.** `DriverCapabilities` defaults are conservative (no native
  abilities, accuracy 0.0, tier 4). A backend must *opt in* to each capability.
