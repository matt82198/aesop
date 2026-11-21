# Spec: chat-model-harness (H)

Authorization: Matt 2026-11-21, verbatim: "Model-Agnostic CLI Harnesses (For Open-Weight & Custom Routing) is the goal for the tui". Boundary, Matt's choice: "Non-Anthropic only".
Frozen original: `chat-model-harness.original.md` (byte-identical, never edited).

## Goal

`aesop chat` becomes a model-agnostic terminal harness: one config file declares providers (local open-weight servers such as Ollama, llama.cpp server, vLLM and LM Studio, plus any OpenAI-compatible endpoint Matt configures, for example OpenRouter), the chat switches among them with `/model`, routes each turn by rule (per-task, fallback chain, cost or latency), and normalises streaming and errors so every provider looks the same on screen. WHY: Matt wants open-weight models and his own routing to be first-class in the TUI, with no vendor lock-in and no per-token Anthropic spend from the TUI; Claude stays reachable only through the seated orchestrator (F1), so the TUI itself never holds a Claude credential or spawns the `claude` CLI.

## Non-goals

- No Anthropic API, no `claude` CLI, no headless CLI from the TUI (Matt HARD RULE, memory no-api-calls-through-cli). Claude is reached only as the F1 orchestrator provider.
- No tool-using agent loop in chat in this spec (file edits, shell). Chat turns are text in, text out; tool-call normalisation is a later spec (see Risks).
- No model download or server lifecycle management (we detect and talk to servers; we never install or start them).
- No new dependencies: Node stdlib only (`node:http`, `node:fs`, `node:net`), no `package.json` in `chat/`.
- No second turn engine: the Python driver is the turn engine and the chat is an adapter over it (H-R26); the driver seam is reused, not forked (H-R26..H-R28).
- No changes to `render.mjs` or the input reducer.

## Existing seams this builds on (path:line on main at branch time)

- `chat/session.mjs:32-33` `DEFAULT_STATUS` hardcodes `provider: 'anthropic'`, `model: 'claude-haiku-5-5'`; this violates the boundary as a default label and is replaced (H-R3).
- `chat/session.mjs:216-228` `setModel(arg)` accepts any `provider/model` text with no registry check; `chat/session.mjs:206-210` `/auth` prints a fixed "no credentials are configured" line. Both become registry-backed (H-R6, H-R24).
- `chat/session.mjs:118,127,131` the `source` option is the backend seam: `(line) => AsyncIterable<string>|Iterable<string>`. The harness is delivered as a `source` factory; `Session` and `render.mjs` stay unchanged.
- `driver/openai_compatible_driver.py:35-66` (`make_openai_compatible_transport`, loopback-pinned `is_local` key waiver via `validate_is_local_base_url`) and `:134-200` (`OpenAICompatibleDriver`, `is_local` tier-3 probe) are the Python source of truth for URL validation and key waiver.
- `driver/orchestrator-swap/CLAUDE.md` (Files section): `backend_config.py` seats config, base_url SSRF check, `api_key_env` allowlist, `is_local` pinned to loopback, "NO seats block = byte-identical to today". The chat registry mirrors these rules (H-R26).
- `docs/specs/chat-orchestrator-backend.md` (PR 1427, F1): `orchestratorSource({stateRoot, inboxPath, onIntent})` selected by `AESOP_CHAT_BACKEND`. F1 is registered as one provider here (H-R20).
- `chat/SPEC.md` on `feat/chat-tui-improve-loop` (PR 1428): R16 (a mid-stream backend error is shown and the session keeps working) and R17 (error lines red) are the error-visibility floor; H-R19 extends them per provider.

## Relation to F1 and the spec conventions (PR 1427)

- F1 (orchestrator backend) is exactly ONE provider in this registry, id `orchestrator`, type `orchestrator`. It is the only provider type allowed to lead to Claude, and only because the seat is operator-configured and reached through aesop's own inbox, never by the TUI calling an API. When F1 is not seated the provider reports "no orchestrator listening" (F1-R5) and routing falls through its chain like any other provider failure.
- Conventions follow `docs/specs/README.md` on PR 1427: this file is the live spec; `chat-model-harness.original.md` is the byte-identical frozen copy (verify with `cmp`; `git log --follow` on the original shows one commit); only the live spec is amended. Requirement IDs are `H-R<n>`. Plan drift = `100 * (1 - LCS_similarity(spec, original))` with line-level LCS similarity `2 * LCS_lines / (lines(spec) + lines(original))`. Implementation drift = unmet requirement tests / total requirement tests * 100, measured like `tools/chat_spec_drift.mjs`. Exit ceiling 86% (Matt, 2026-11-21). <!-- metrics-verified: Matt 2026-11-21 stated threshold, not a measured stat --> One test per requirement, named `H-R<n>: ...`, in `tests/chat-harness-spec.test.mjs`.
- This PR adds text only; code and tests belong to later PRs.

## The provider boundary (HARD requirement)

The TUI may call: loopback open-weight servers and operator-configured OpenAI-compatible endpoints. The TUI must never call: any Anthropic host, or the `claude` binary. This is enforced at config load AND at request time, fail closed, through a single function `assertProviderAllowed` called in both places, so a bug in one path cannot open the other. It is H-R1 and H-R2 below.

## Requirements

Each requirement is one line of observable behavior, then `Check:` an OFFLINE-testable acceptance. All checks run against a fake OpenAI-compatible server on `127.0.0.1` with an ephemeral port started inside the test (`tests/helpers/fake_openai_server.mjs`: serves `POST /v1/chat/completions` as JSON or SSE and `GET /v1/models`, with scriptable delays, status codes and malformed frames, and records every request it receives). Tests never touch a real network; a test-wide guard replaces `http.request`, `https.request` and `net.connect` so any non-loopback target throws and fails the test.

### Boundary and safety

- H-R1: A provider whose `base_url` host is `anthropic.com`, any `*.anthropic.com`, or `claude.ai`/`*.claude.ai` (case-insensitive, including userinfo tricks like `http://127.0.0.1@api.anthropic.com`, a trailing dot, and percent-encoded forms) is refused, fail closed. Check: loading a config with each such URL throws `ProviderRefused` naming the provider; the fake server records zero requests; a mutant with the host compare removed turns the test red.
- H-R2: No code path in `chat/` spawns the `claude` binary or any command-style provider; a provider of type `cli`, `exec`, `claude` or `anthropic` is refused, and a provider whose model id starts with `claude` or `anthropic/` is refused unless its type is `orchestrator`. Check: configs with those types and model ids throw `ProviderRefused`; a spy on `child_process.spawn/execFile/exec` records zero calls across the whole H suite; with the config mutated after load, request time refuses via the same `assertProviderAllowed` and nothing is sent.
- H-R3: With no config file the chat starts with no provider selected, shows `no provider configured (see /help)` in the status, never claims `anthropic` as provider, and a plain turn yields one visible system line instead of a network call. Check: empty temp config dir; the first frame has no `anthropic`; submit "hi" shows the line; the fake server records zero requests.
- H-R4: Redirects are never followed to a host other than the validated `base_url` host, and a redirect toward an Anthropic host is refused. Check: the fake server answers 302 to `http://api.anthropic.com/...` and to a second loopback port; the chat shows an error line and no second connection is made.
- H-R5: Non-loopback `http://` endpoints are refused unless the provider sets `allow_insecure: true`; `https://` is required for every non-loopback host by default. Check: `http://example.test` is refused; `http://127.0.0.1:<port>` and `http://localhost:<port>` are accepted; the flag-on case is accepted and a NOTICE line is emitted.

### Registry, config file, aliases, switching

- H-R6: `/model <provider>/<model>` switches only to a registered provider and a model that provider lists (or whose `models` is `"any"`); otherwise it shows `unknown provider: <p>` or `unknown model: <m> for <p>` and leaves the current selection unchanged; the transcript is kept. Check: switch to fake provider A, then a bogus name, then B; status and the next request's `model` field follow exactly; transcript length is unchanged by `/model`.
- H-R7: The provider registry loads from a JSON config (default `aesop.config.json` key `providers`, override env `AESOP_CHAT_CONFIG`), shaped `providers: { <id>: { type, base_url, api_key_env?, models?, headers?, timeout_ms?, is_local?, cost_per_mtok? } }` plus `aliases`, `routes`, `default`. Check: a fixture file loads to the expected normalised registry; unknown keys produce a visible NOTICE, not a crash; malformed JSON gives one error line and the chat still starts in the H-R3 state.
- H-R8: Model aliases (`aliases: { fast: "ollama/qwen2.5:7b" }`) resolve in `/model fast` and in routes; alias chains up to depth 3 resolve, cycles are rejected at load. Check: alias, alias-to-alias and cycle configs; the request `model` equals the resolved id.
- H-R9: `/models` (and `/model` with no argument) lists providers, models, alias targets and a per-provider status (`reachable`, `unreachable`, `no key`, `refused`) without sending any chat request. Check: the transcript lists all fake providers; the fake server records only `GET /v1/models`, no `POST`.
- H-R10: The active selection persists across `/clear` and is restored on the next start from a state file under the aesop state root, written atomically and never containing a key. Check: select B, `/clear`, still B; a new `runChat` with the same state file starts on B; the file content contains no key material.

### Routing

- H-R11: Per-task routing: `routes` maps a task class (`chat` default, `summarize`, `code`, `plan`, chosen by a slash prefix like `/code ...` or a rule `match`) to a provider/model or alias; an unmatched turn uses the selected model. Check: two fake providers; `/code x` hits provider B with B's model, plain text hits A.
- H-R12: Fallback chains: a route may list `fallback: [...]`; on a retryable failure (connect error, timeout, HTTP 429/5xx, stream cut before the first chunk) the next entry is tried, each at most once per turn, and the transcript shows `falling back: A -> B (reason)`. Check: A scripted to 503 then B succeeds: one transcript, both attempts recorded by the servers in order, the fallback line present.
- H-R13: A non-retryable failure (HTTP 400/401/403/404, or a refused provider) does NOT fall back silently: the error is shown and the chain stops unless the route sets `fallback_on: "any"`. Check: A scripted 401; B receives zero requests by default and one with `fallback_on: "any"`.
- H-R14: A failure after the first chunk was already shown never re-sends the turn to another provider (no duplicated or spliced text); it shows `stream interrupted from <provider>` and keeps the partial text. Check: A sends two chunks then drops the socket; B records zero requests; the transcript keeps both chunks plus the error line.
- H-R15: Cost and latency aware routing: a route with `strategy: "cheapest"` or `"fastest"` orders candidates by configured `cost_per_mtok` (input plus output) or by an in-memory exponentially weighted first-chunk latency, with local providers at cost 0; ties resolve in config order. Check: fake A (slow, cost 0) and B (fast, cost 5) with scripted delays and an injected clock: `cheapest` picks A, after warmup `fastest` picks B, deterministically.
- H-R16: Provider health: after N consecutive retryable failures (default 3) a provider is skipped for a cooldown (default 30 s, injected clock) and the skip is announced once; a success resets it. Check: scripted failures trip the breaker, the next turn skips A with no request to it, and after the clock advances A is tried again.

### Streaming, errors, normalisation

- H-R17: The OpenAI SSE stream (`data: {json}` frames, `data: [DONE]`, comment and keepalive lines, CRLF, frames split across TCP chunks, multi-byte UTF-8 split across chunks) is normalised into the chat `source` contract: an async iterable of text deltas, each framed before the next is awaited (chat invariant 5). Check: the fake server splits a frame and a 3-byte character across writes; the transcript equals the full text; the first frame appears before the server finishes (the buffer-everything mutant turns red).
- H-R18: A non-streaming JSON reply (server ignores `stream`) and an Ollama-style NDJSON stream are normalised to the same deltas, and `delta.content` null, empty or reasoning-only fields never produce `null` or `undefined` text. Check: three fake modes yield identical transcripts for the same text.
- H-R19: Every failure is visible as one system line `error: <provider>: <class>: <short reason>` (classes: `connect`, `timeout`, `auth`, `rate-limit`, `server`, `bad-response`, `refused`, `no-key`, `cancelled`), red when color is on, never a stack trace, never containing a key or Authorization header; the session accepts the next turn. Check: scripted 401, 429, 500, malformed JSON, connect refusal and a hang past `timeout_ms`; each yields exactly one matching line, the next turn succeeds, and the output contains none of the test key strings.
- H-R20: The F1 orchestrator backend is registered as provider id `orchestrator` (type `orchestrator`) wrapping `orchestratorSource`; `/model orchestrator/seat` routes plain turns to it, and its "no orchestrator listening" end is a retryable failure for fallback purposes. Check: with a fake `orchestratorSource` injected, a turn reaches it and the fake HTTP server sees nothing; with it silent and a fallback to a fake HTTP provider, the fallback line and the HTTP reply appear.
- H-R21: ctrl-c during a streaming turn aborts the in-flight request (socket destroyed), shows `cancelled`, and does not trigger fallback. Check: the fake server holds the response open; abort; the server observes the close within 1 s and B records zero requests.
- H-R22: `status.tokens` is filled from the response `usage` when present, otherwise from a chars/4 estimate marked `~`; per-turn cost uses `cost_per_mtok` when configured; local providers show cost `0`. Check: fake with and without `usage`; status values match exactly and the estimate carries the `~` marker.

### Local detection, secrets

- H-R23: Local endpoint detection: `/detect` (and an opt-in startup probe, `detect: true`) issues `GET /v1/models` only against loopback candidates (Ollama 11434, llama.cpp server 8080, vLLM 8000, LM Studio 1234, plus configured ports) with a 500 ms timeout, lists what answered, and offers them as session-only providers with `is_local: true` and no key requirement; it never probes a non-loopback host and never runs by default. Check: candidate ports injected to point at two fake servers and one closed port: two listed, one reported absent; no probe without the flag; a non-loopback candidate is rejected; the key waiver applies only to the validated loopback URL, never to URL text.
- H-R24: Keys come only from the env var named by `api_key_env` (same allowlist-shaped names as the Python guard) or an `api_key_file` path; a literal key in the config file is refused with a NOTICE. Keys are never written to history, state files, transcript, logs, error lines or `/auth` output; `/auth` shows per provider `key: set (env NAME)`, `key: missing (env NAME)` or `key: not needed (local)` and makes no network call. Check: set `KEY=sk-test-SENTINEL`; run a full session including errors and `/auth`; the sentinel is absent from the history file, state file, every frame and stderr; the Authorization header the fake server received is `Bearer sk-test-SENTINEL` (sent, not leaked); `/auth` records zero requests.
- H-R25: A key is sent only to the provider that owns it and only to the validated host; switching providers or following any redirect never forwards the Authorization header to a different host. Check: two fake servers on different ports; the second records no Authorization header derived from provider one's key.

### Driver seam reuse

- H-R26: The driver is the turn engine: every chat turn, every `aesop ask` and every codex, gemini or agy turn runs through the Python driver (`python -m driver.turn`, `driver/turn_backend.py`), and `chat/` is a thin spawn adapter that exchanges AESOP-PIPE/1 frames (`stdio/frames.mjs`) with one driver child per session; where the Node harness and the driver disagree, the driver (the pipe) wins. The driver child is not a provider: it is aesop's own engine, never the `claude` binary, and it applies the same boundary (H-R1, H-R2) before any socket opens. Until a golden-transcript parity test passes, the Node harness stays the default and `AESOP_CHAT_BACKEND=driver` selects the driver. One config, two readers, no fork: the registry validates `base_url`, `is_local` and `api_key_env` with the same rules as `driver/backend_config.py` and `driver/openai_compatible_driver.py`, proven by a shared conformance fixture (`tests/fixtures/provider-config-cases.json`: input config, expected accept or refuse, reason) executed by BOTH a Node test and a Python test. Check: both suites pass on the same fixture; adding a case only one side handles turns exactly that side red; a golden-transcript test runs the same scripted turns through the harness and through the driver, and the two transcripts (text, system lines, requests received) are equal.
- H-R27: The chat registry maps onto the driver seats without translation: a `providers.<id>` entry with `type: "openai-compatible"` is accepted by `driver/backend_config.py` as a seat definition unchanged, and an existing `aesop.config.json` with no `providers` block behaves byte-identically. Check: a Python test builds a driver from a chat-shaped provider entry against a FakeTransport; a snapshot test of chat startup with an old config equals the pre-change frames.
- H-R28: `AESOP_CHAT_BACKEND` (F1-R2) keeps its meaning: `pipe` (default when unset) is the existing behavior byte-identical, `orchestrator` is F1, and `harness` selects this registry; an unknown value is an error line, never a silent default. Check: the three values plus a bogus one against the existing chat suites; the pipe default frames are byte-identical.

## Patterns adopted from other harnesses (research)

Surveyed 2026-11-21, docs only, no provider calls. URLs were fetched (HTTP 200) unless marked recalled.

- LiteLLM Router (https://docs.litellm.ai/docs/routing): `model_list` of deployments sharing a `model_name` group; `fallbacks`, `num_retries`, cooldowns on repeated failure (H-R12, H-R16); `routing_strategy` values `simple-shuffle`, `latency-based-routing`, `least-busy`, `usage-based-routing`, `cost-based-routing` (H-R15).
- opencode (https://opencode.ai/docs/providers/): `opencode.json` `provider.<id>` with `options.baseURL`, a `models` map, `{env:VAR}` and `{file:path}` secret substitution, an OpenAI-compatible adapter (H-R7, H-R24); `small_model` for cheap background tasks (H-R11).
- aider (https://aider.chat/docs/config/adv-model-settings.html): `--alias fast:model` aliases, model settings and metadata files for per-model cost and context, architect/editor split models (H-R8, H-R11, H-R22).
- simonw/llm (https://llm.datasette.io/en/stable/other-models.html): `extra-openai-models.yaml` entries (`model_id`, `model_name`, `api_base`, `api_key_name`, `aliases`) make any OpenAI-compatible server a named model with aliases (H-R7, H-R8).
- Continue (https://docs.continue.dev/reference): `config.yaml` `models:` each with `provider`, `model`, `apiBase` and `roles` (chat, edit, apply, autocomplete, embed) for per-task routing (H-R11).
- Codex CLI (recalled, not fetched; config docs in the openai/codex repo): `[model_providers.<id>]` with `base_url`, `env_key`, `wire_api`, chosen by profile; secrets by env var name only (H-R24).
- goose (recalled, not fetched; block.github.io/goose docs): `GOOSE_PROVIDER` and `GOOSE_MODEL` plus a lead/worker model pair for per-task cost routing (H-R11).

## Risks

- Tool-call normalisation differs per open-weight server (function-call JSON in content, `tool_calls` deltas, missing templates); deferred because chat has no tool loop yet. A later spec must define it before chat can run agent tasks.
- Many local servers omit `usage` in streams; H-R22 estimates, so token counts are approximate.
- OpenRouter and similar gateways route to Claude by model id (`anthropic/claude-...`); H-R2 refuses that prefix on non-orchestrator providers, which can false-positive on fine-tunes named `claude-*`; there is no escape hatch by design (fail closed).
- Host checks cannot see a gateway that CNAMEs to an Anthropic host; the model-id rule and the operator's own config are the remaining defenses.
- Node `http` has no built-in proxy handling; `HTTP_PROXY` is ignored on purpose (a proxy could reroute to a refused host).
- Config duplication between Node and Python is controlled only by the H-R26 shared fixture; if it is skipped the two will drift.

## Open questions for Matt

1. (not blocking) Should a gateway model id that resolves to Claude (for example OpenRouter `anthropic/claude-*`) be refused (spec default, H-R2) or allowed because the call goes to a third party, not Anthropic? Default until answered: refused.
2. (BLOCKING for H-R20 wiring only; the rest can proceed) Is F1 the only way chat may reach Claude, with `/model orchestrator/seat` as the only switch between "ask the orchestrator" and "ask a local model"? Default until answered: yes.
3. (not blocking) Where should the provider config live: a new `providers` block in `aesop.config.json` (spec default, shared with the driver seats) or a separate `chat.config.json` under the state root? Default until answered: `aesop.config.json`.
