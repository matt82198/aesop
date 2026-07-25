# Aesop as an AI Micro-Kernel: Two Swappable Seats

**Status**: HS-1 (unified two-seat config) + HS-2 (live orchestrator-seat swap)
shipped. This doc is the conceptual centerpiece for both — what the seam
actually is, what is proven about it today, and how to swap a seat's model
in about a minute.

---

## What it is

Aesop's wave loop has exactly two places where a large language model makes
a decision:

1. **The worker seat** — does the work: writes files, runs commands, reports
   a structured result. Interface: `AgentDriver`.
2. **The orchestrator seat** — judges the work: decides whether a
   test-verified item is safe to ship. Interface: `OrchestratorBackend`
   (called through `OrchestratorDriver.decide()`).

Both seats are **swappable parts**, not the engine itself. The engine —
`driver/wave_loop.py`'s `run_wave()` — never talks to a model directly; it
calls exactly two abstract interfaces and does not care what sits behind
either one. This is the micro-kernel idea applied to an agent harness: keep
the kernel (the loop, the state contract, the human-facing report) tiny and
stable, and make every model a replaceable driver plugged into a fixed seam.

**Identity lives in files, not memory.** Aesop is crash-only: there is no
in-process state that a restart loses. Recovery is by reading `STATE.md`,
`tracker.json`, the recovery journal, and the Report JSON off disk — the same
files a *different* model in either seat would read and write. That is what
makes a seat swap safe: the model is not the thing being recovered, the
files are.

**What is invariant across a seat swap** — the two things a human or a
downstream tool actually depends on:

- **The Report JSON** — the wave scheduler's output contract (`phase`,
  `wave_id`, `items_selected`/`items_shipped`, `blocked`,
  `orchestrator_gate`, …, documented in full in `driver/wave_scheduler.py`'s
  module docstring). Swapping either seat adds *at most* two well-known
  optional keys (`orchestrator_review`, per-item `final_catch`) and changes
  no existing key's shape.
- **The state layer** — `STATE.md`, `tracker.json`, receipts, and the
  recovery journal. Same file names, same key sets, regardless of which
  model is deciding.

What is **not** invariant, and isn't meant to be: which model made a given
decision, and (for the orchestrator seat) whether a decision was even routed
through an API model at all versus made by the live harness. That is exactly
the part that's supposed to change when you swap a seat.

---

## The two seats

### Worker seat — `AgentDriver`

Selected by `seats.worker` in `aesop.config.json`. Concrete backends today:

| `backend` | What it is | Notes |
|---|---|---|
| `claude` (default) | Claude Code CLI harness | No API key; two ops run as concrete Python, three are serviced by the harness itself |
| `codex` | OpenAI Chat Completions | Requires a `json_schema`-capable model (`gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`; `gpt-3.5-turbo` is rejected at construction unless you pass `allow_unverified_models=True`) |
| `openai-compatible` | Any OpenAI-compatible HTTP endpoint | Ollama, OpenRouter, Together, etc. — **requires `base_url`** |

**Live wiring**: `driver/wave_scheduler.py`'s `resolve_worker_driver()` reads
`seats.worker` from `aesop.config.json` and calls `build_driver()` on it
(`driver/backend_config.py`). `--driver claude|codex` on the CLI overrides
the config. No `seats` block, or a bare legacy flat `{"backend": ...}` block
with no `seats` wrapper, keeps behavior byte-identical to a pre-0.4.0
install: `ClaudeCodeDriver`, no key needed. (The legacy flat block still
*parses*, and direct `build_driver()` callers still honor it — it is only
*inert* in the scheduler's default dispatch path, so migrate it into
`seats.worker` to actually activate it.)

### Orchestrator seat — `OrchestratorBackend`

Selected by `seats.orchestrator`. This is the decision seat: the thing that
calls `OrchestratorDriver.decide()` to produce a structured verdict.

| `backend` | What it is |
|---|---|
| `harness` (default, also accepts `"claude"`) | The **null** `HarnessOrchestratorBackend`. `decide_call()` raises on purpose — there is no Python code path that "calls" the harness. This is the honest way of saying: the live Claude Code session driving this loop IS the orchestrator seat, and no swapped backend exists. |
| `openai-compatible` | `OpenAICompatibleOrchestratorBackend` — a real OpenAI-compatible HTTP call, same `model`/`base_url`/`api_key_env`/`is_local` shape as the worker seat, plus `timeout_s`. `base_url` is optional here (defaults to the hosted OpenAI endpoint) — unlike the worker seat, which requires it. |

**Live wiring**: `driver/wave_loop.py`'s `run_wave(..., orchestrator_backend=...)`
Phase 6 — the pre-ship gate — routes **one `final_catch` decision per
test-verified item** through the configured backend when one is live;
`driver/wave_scheduler.py`'s `resolve_orchestrator_backend()` is what builds
that backend from `seats.orchestrator` and passes it through. With no
`seats.orchestrator` block (or `backend: "harness"`/`"claude"`),
`resolve_orchestrator_backend()` returns `None` and Phase 6 stays exactly
what it was pre-HS-2: `adversarial_review = "deferred"`, no
`orchestrator_review` key, no OpenAI backend constructed, no key required.

Be precise about scope here, because it's easy to overstate: Phase 6's
`final_catch` is the **only** decision point HS-2 wired to the seat. Backlog
ranking, in-session adjudication, and PR merges are still made by the live
harness directly — see [What's proven vs bounded](#whats-proven-vs-bounded).

---

## The invariant boundary

The claim "the Report JSON and state layer are unchanged across a seat
swap" is not just asserted in this doc — it's a committed, offline,
automated proof: `tests/test_hs2_swap_proof.py`. It drives the **same task**
through the public scheduler path twice — once with the default harness
orchestrator seat, once with a swapped `FakeOrchestratorBackend` — both on a
non-Claude fake worker seat, and asserts:

- Identical Report JSON key sets, top-level and per-item.
- Identical values for `slug`/`backend`/`tier`/`verified`/`testExit`.
- Identical tracker terminal state (`in_progress`) and structure.
- Identical journal file names and entry key sets.
- The swapped backend demonstrably decided (`call_count == 1`).

A companion **bounded live run** — `bench/results/hs2-swap-proof-2026-07-25.md`
/ `.json` — drove one real task (fix a broken `multiply`) through `run_wave`
twice against a live codex (gpt-4o-mini) worker seat: arm A the default
harness orchestrator seat, arm B `seats.orchestrator` = openai-compatible
gpt-4o-mini. Both arms dispatched, test-exit 0, `verified: True`; arm B's
gpt-4o-mini seat returned a schema-valid `final_catch` verdict (`merge`,
with evidence + confidence) on the first attempt. Result shape was
invariant modulo exactly the two documented opt-in keys
(`orchestrator_review`, `final_catch`). Total spend: 3 gpt-4o-mini calls
(~1.1k worker tokens + one decision call), well under the run's US$2 cap.
`git=None` — the live proof never shipped anything.

That is the whole evidentiary basis for "the seam is real and the boundary
holds": one offline proof covering the general mechanism plus one small,
real, bounded live run. See [Bounds](#whats-proven-vs-bounded) for what that
does and does not establish.

---

## How to swap a seat

Everything lives in one namespaced block, `seats`, in `aesop.config.json`:

```json
{
  "seats": {
    "worker": { "backend": "claude" },
    "orchestrator": { "backend": "harness" }
  }
}
```

That's the *default* — writing it explicitly changes nothing, and deleting
it changes nothing either. To swap a seat, replace its block. See the
[quickstart](#swap-a-seats-model-in-60-seconds) below for copy-paste
examples.

Fields, common to both seats' `openai-compatible` backend:

| Field | Required? | Meaning |
|---|---|---|
| `backend` | yes | `"claude"` / `"codex"` / `"openai-compatible"` (worker); `"harness"` / `"claude"` / `"openai-compatible"` (orchestrator) |
| `model` | required for `codex` and `openai-compatible` | Model id |
| `base_url` | **required** for worker `openai-compatible`; optional for orchestrator (defaults to `https://api.openai.com/v1`) | The HTTP endpoint |
| `api_key_env` | optional | Env var name holding the API key — read at **call time**, never stored in the config |
| `is_local` | optional | See below |

### SECURITY notes (read these before pointing a seat at a real endpoint)

- **`api_key_env` is a heuristic allowlist, not a guarantee.** Known
  LLM-provider names (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `OPENROUTER_API_KEY`, `TOGETHER_API_KEY`, `GROQ_API_KEY`,
  `MISTRAL_API_KEY`, `DEEPSEEK_API_KEY`, `FIREWORKS_API_KEY`,
  `OLLAMA_API_KEY`, `AZURE_OPENAI_API_KEY`, `GOOGLE_API_KEY`) pass
  **silently**. A name that doesn't look like a key env var, or that
  contains an obvious non-LLM secret fragment (`SECRET`, `TOKEN`,
  `PASSWORD`, `PASSWD`, `CREDENTIAL`, `PRIVATE`, `ACCESS`, `SESSION`,
  `COOKIE`, `SIGNING`), is **hard-rejected**. Any other key-shaped name
  (a custom LLM gateway) is **allowed but prints a loud `NOTICE` to
  stderr** naming the risk: its value *will* be sent as a Bearer token
  to your configured `base_url`. This is deliberately best-effort — no
  name check can prove an env var actually holds an LLM key. The NOTICE
  is the real signal; review it whenever a non-provider name appears.
- **`is_local: true` requires a loopback `base_url`** (`localhost`,
  `127.0.0.1`, or `::1`) — construction rejects it otherwise. `is_local`
  waives the API-key requirement (a dummy `local-only` Bearer is sent
  instead), so it must be pinned to loopback: `is_local` plus a remote
  `base_url` would ship your prompt content to an arbitrary host with no
  key needed at all.
- **`base_url` is SSRF-validated** (`driver/backend_config.py`'s
  `validate_base_url`): scheme must be `http`/`https`, no embedded
  credentials, and both IP literals *and* DNS-resolved hostnames are
  checked against private/loopback/link-local/reserved ranges (including
  IPv4-mapped IPv6 forms like `::ffff:169.254.169.254`, which would
  otherwise bypass the IPv4 checks, and the `169.254.169.254` cloud
  metadata address). **Residual, documented, not closed**: this is a
  load/construct-time check with a bounded (5s) DNS resolution. A
  TTL-0 DNS-rebinding attacker can pass validation and then re-point the
  name at a private address before the actual HTTP call; closing that
  fully requires connection-time address pinning in the transport, which
  this does not do. An unresolvable hostname is allowed through (offline
  config loading must not fail) and the eventual connection just fails
  on its own.

---

## What's PROVEN vs BOUNDED

Scrupulously, so nothing here gets over-read:

### PROVEN

- **Worker swap is live.** `resolve_worker_driver()` builds a real,
  configured `AgentDriver` from `seats.worker` and the scheduler dispatches
  through it; codex and openai-compatible backends have offline test
  coverage (`tests/test_codex_driver_e2e.py`, `tests/test_seats_config.py`).
- **Orchestrator swap is live** (HS-2). `run_wave`'s Phase 6 routes a real
  `final_catch` decision through a configured `OrchestratorBackend` when one
  is present; verdict has real effect (`block` stops the ship and
  quarantines files; `merge` approves).
- **Swap transparency is proven** two ways: an offline test suite
  (`tests/test_hs2_swap_proof.py`) establishing the no-op invariant and the
  end-to-end Report/state shape invariance, *and* one bounded live run
  (both seats real: worker + orchestrator gpt-4o-mini) where both arms went
  green with an invariant result shape. **Small N** — see below.

### BOUNDED / NOT yet claimed

- **The proofs are small-N.** The offline swap-transparency test is one
  synthetic task on a fake backend; the live run is one real task, one
  model (gpt-4o-mini), one repeat. This proves the *plumbing* — config to
  seat to real API call to schema-valid verdict to recorded effect — not
  decision **quality**, and not anything at scale.
- **Seat decision quality is a separate, ongoing question**, studied
  in the shadow-adjudication bench line (`bench/README.md`,
  `tools/seated_shadow_adjudication.py`). The headline finding there —
  **"Context at the Seam"**: decontextualized adjudication (facts only, no
  file brain) leaves both a frontier model (gpt-5.6-sol) and a cheaper
  model (gpt-5.5) abstaining (`undetermined`, ~80–100% of runs) on the
  hardest synthesis-heavy corpus item (item 9, a whitelist-gate-weakening
  false positive that requires chaining "health check is top-level only" +
  "secret_scan.py scans recursively" into "no real coverage gap"). Giving
  the *same* models **real seated context** (actual STATE.md/tracker.json/
  BUILDLOG.md plus the real cited code) flips both models to the correct
  `false_positive` verdict, **stable across repeated runs and across two
  independently-run corpus variants** (`bench/results/SEATED-AB-2026-07-24.md`,
  `bench/results/seated-redo-2026-07-24-gpt-5_6-sol_repeat3.md`,
  `bench/results/seated-redo-2026-07-24--neutral-seated-sol_repeat3.md`).
  This result is **robust in the sense that matters for this doc**: it
  reproduced after an earlier round of this same bench line caught and fixed
  a context-leak/seam confound (a dropped-prompt / schema-mismatch bug where
  "seated" runs weren't actually receiving the real context they claimed
  to) — the cited results here are the **re-verified**, post-fix runs, not
  the confounded ones. Bound: N=3 per model, one item, two OpenAI-family
  models, not a full-corpus or cross-lab claim.
- **Some orchestrator decisions are not routed through the seat at all.**
  Backlog ranking, PR merges, and in-session adjudication by the live
  harness are made outside `run_wave`'s Phase 6 and are unaffected by HS-2.
  The seat swap covers exactly one gate: the pre-ship `final_catch` check.
- **`wave_loop.py`'s standalone `--manifest` CLI still hardcodes
  `ClaudeCodeDriver`** and does not read `aesop.config.json` at all. The
  config-driven entry point is `driver/wave_scheduler.py` — that is where
  `seats` actually takes effect. If you invoke `wave_loop.py` directly by
  its manifest CLI, no seat swap applies.
- **Repair stays mechanical on both seats by design.** Swapping who
  decides `final_catch` never changes the bounded-retry repair semantics.

---

## The block gate

`run_wave`'s Phase 6 (`_orchestrator_final_catch` in `driver/wave_loop.py`)
reviews every **test-verified** item — a failed item never reaches the
seat at all — and acts on the verdict:

| Verdict | Effect |
|---|---|
| `merge` | Approved; ships exactly as it would with the default harness seat. |
| `block` | `verified` is flipped to `False`; the item does **not** ship; the recovery journal is rewritten so a resume can't skip-and-ship it; and the item is marked **terminal** (tracker status `blocked`, never re-selected) and visible in the Report (`Report.blocked: [{slug, reason, quarantine}]`). |
| `escalate` / `undetermined` / `DECISION_FAILED` | Degrades to today's default behavior: ships to branch (merge stays manual downstream) with an honest per-item record. **A seat outage never fabricates a verdict and never blocks a test-proven item** — this is crash-only degradation, not silent failure. |

**Quarantine on block**: a blocked item's already-written files are
restored to their pre-build state — `git checkout --` for tracked files,
delete for untracked ones — so refused code doesn't linger in the working
tree for a later `git add -A` to accidentally ship. This acts on **file
paths only**: empty strings, `.`/`..`, and directory entries are rejected
with a per-file error record rather than acted on, specifically because a
directory or dot pathspec would revert *other* items' uncommitted verified
work, not just the blocked item's. An ambiguous untracked/tracked
determination (e.g. a git index lock) never deletes — fail-safe, not
fail-delete.

**Gate visibility**: if every single decision on a wave came back
`DECISION_FAILED`, the wave-level `orchestrator_review.gate_status` is
`"degraded"` (not `"active"`) — a 100%-failing seat is not allowed to look
like an approving one, even though ship semantics for already-verified
items are unaffected.

---

## Honest engineering note

This seam did not work correctly on the first attempt at wiring it. Early
rounds of the shadow-adjudication bench line ran through a shim that
dropped the prompt before it reached the model, and a schema/prompt
mismatch meant "seated" runs weren't actually seeing the context they
claimed to have — green test output, wrong conclusion (**green is not
correct**). Each seat — worker and orchestrator — cleared **two rounds of
adversarial audit** before the swap-transparency claims in this doc were
considered solid enough to write down. The context-leak confound described
above under [Bounds](#whats-proven-vs-bounded) is exactly this class of
bug, caught, fixed, and re-verified before being cited here.

---

## Swap a seat's model in 60 seconds

Every config block below was verified offline against this repo's actual
loader — `load_backend_config()` → `build_driver()` /
`build_orchestrator_backend()` — with no network call and no API key
(construction is always offline-safe; keys are read at call time only).
Copy, paste, done.

### Worker seat

**Ollama, local, no key:**

```bash
ollama serve            # in one terminal
ollama pull mistral     # in another
```

```json
{
  "seats": {
    "worker": {
      "backend": "openai-compatible",
      "base_url": "http://localhost:11434/v1",
      "model": "mistral",
      "is_local": true
    }
  }
}
```

No env var needed — `is_local: true` sends a dummy `local-only` Bearer.
Verification tier: 3 (heaviest — small local models get the most checking).

**OpenRouter, hosted:**

```bash
export OPENROUTER_API_KEY=sk-or-...
```

```json
{
  "seats": {
    "worker": {
      "backend": "openai-compatible",
      "base_url": "https://openrouter.ai/api/v1",
      "model": "openai/gpt-4-turbo",
      "api_key_env": "OPENROUTER_API_KEY"
    }
  }
}
```

Verification tier: 2 (hosted, ~0.92 accuracy assumption).

**Codex (OpenAI Chat Completions), hosted:**

```bash
export OPENAI_API_KEY=sk-...
```

```json
{
  "seats": {
    "worker": {
      "backend": "codex",
      "model": "gpt-4o-mini"
    }
  }
}
```

`model` must support `response_format: json_schema` — `gpt-4o`,
`gpt-4o-mini`, and `gpt-4-turbo` are the known-capable set; anything else
(including `gpt-3.5-turbo`) is rejected at construction unless you pass
`allow_unverified_models=True` yourself. Verification tier: 2.

### Orchestrator seat

**OpenAI hosted (`gpt-4o-mini`) as the decision seat:**

```bash
export OPENAI_API_KEY=sk-...
```

```json
{
  "seats": {
    "orchestrator": {
      "backend": "openai-compatible",
      "model": "gpt-4o-mini",
      "api_key_env": "OPENAI_API_KEY"
    }
  }
}
```

Note `base_url` is omitted — the orchestrator seat defaults to
`https://api.openai.com/v1` (the worker seat has no such default; it
requires `base_url` explicitly).

**Ollama, local, no key, as the decision seat:**

```bash
ollama serve
ollama pull mistral
```

```json
{
  "seats": {
    "orchestrator": {
      "backend": "openai-compatible",
      "base_url": "http://localhost:11434/v1",
      "model": "mistral",
      "is_local": true
    }
  }
}
```

**Combine any worker + any orchestrator block** under one `seats` key —
they're independent. Running `driver/wave_scheduler.py --execute` against a
hosted (non-`is_local`) seat requires that seat's `api_key_env` to be set;
`--dry-run` never needs a key, because building a driver or backend is
always offline-safe.

---

## See also

- [docs/INSTALL.md](INSTALL.md) — "Using Non-Claude Backends" section: setup
  prerequisites, verification-tier table, troubleshooting.
- `driver/CLAUDE.md` — the full technical contract for both seats.
- `bench/README.md` — the held-out benchmark measuring *quality*, separate
  from the plumbing this doc covers.
- `bench/results/hs2-swap-proof-2026-07-25.md` — the bounded live
  swap-transparency run.
- `bench/results/SEATED-AB-2026-07-24.md` — the seated-context adjudication
  finding.
