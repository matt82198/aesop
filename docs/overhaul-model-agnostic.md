# Aesop overhaul: a model-agnostic harness

**Status:** announcement. Figures are stated as recorded, with their sources. No dollar figures appear anywhere in this document.

## 1. What is changing

Aesop is moving to a pure model-agnostic harness. No single model vendor is required to run a session.

- **One turn engine.** `aesop chat` (the TUI) and `aesop ask` (headless, for agents) both run on one driver turn engine, `python -m driver.turn` (see `driver/turn.py`). The TUI is a view over that pipe, not a separate program.
- **Backends in parallel.** Claude Code runs the orchestration and review seat. Codex, Antigravity (agy) and local Ollama run as worker backends beside it.
- **Offloading by selection pressure.** Each task goes to the cheapest backend that passes its gate. Verification and receipts decide the outcome, not preference. A backend that fails a gate loses the task; a backend that passes keeps it.

## 2. Why: the token economics

One real session, 2026-11-21. The figures below are the ones recorded in the session; each row names its source.

### 2a. Claude Code lanes

Source: the per-lane `subagent_tokens` total in Claude Code's harness task notifications, 2026-11-21. One total per lane. The harness does not split these by cache, so no cache share is stated for lanes.

| Lane | Agent (model as recorded) | Subagent tokens |
|---|---|---|
| num_ctx buckets | javascript-pro | 72,845 |
| hook-friction fix | javascript-pro | 218,940 |
| driver plan | Plan (Haiku) | 92,351 |
| driver P1 build | python-pro (Haiku) | 148,801 |
| model-lever grant | javascript-pro (Haiku) | 115,712 |
| memory write | memory-keeper (Haiku) | 38,976 |
| push-gate research | Plan (Haiku) | 62,326 |

Sum of the seven rows above: 749,951 (arithmetic on the rows; not a separate measurement). Two further lanes were still running when these figures were recorded and are not included.

### 2b. Codex working thread

Source: the `token_count` event of one working thread, read by the orchestrator at 2026-11-21T20:54Z. Only that event was read.

| Field | Value |
|---|---|
| total_tokens | 72,013,649 |
| input_tokens | 71,898,043 |
| cached_input_tokens (part of input) | 71,464,704 (99.4% of input) <!-- metrics-verified: Codex token_count event 2026-11-21T20:54Z; 71,464,704 / 71,898,043 --> |
| output_tokens | 115,606 |
| reasoning_output_tokens | 28,894 |

### 2c. Local Ollama

Metered tokens: 0. Local models do not bill per token.

Measured latencies, from live verification outputs recorded in PR #1441 (`python -m driver.turn`) and from orchestrator checks the same day:

- qwen3-coder:30b: 7.5 to 8.8 s per reply (`aesop ask` and `driver.turn`).
- qwen2.5-coder:14b: 9.0 s per reply via `driver.turn`.
- Model swaps: 15 to 25 s.

### 2d. What is not measured

The orchestrator main thread's own token total is **not measured** in this document. No figure for it is stated.

### 2e. Reading

- Output is tiny next to input. In the Codex thread, output was 115,606 tokens against 71,898,043 input tokens.
- Nearly all spend is re-reading cached context. In the Codex thread, 99.4% of input was served from cache. <!-- metrics-verified: Codex token_count event 2026-11-21T20:54Z; 71,464,704 / 71,898,043 -->
- Bulk context work belongs on local or cheap tiers. A local tier has no metered tokens, and its cost is latency (see 2c).
- The expensive tier holds intent and verdicts. It should not carry bulk reads and writes.

## 3. Status (from git and GitHub)

Checked with `gh pr view N --repo matt82198/aesop --json state,mergeCommit` on 2026-11-21.

| PR | State | Change |
|---|---|---|
| #1432 | MERGED | Talk to local open-weight models via Ollama |
| #1435 | MERGED | Ollama `num_ctx` sized to the prompt, with an overflow warning |
| #1436 | MERGED | `aesop ask`: headless one-shot for agents |
| #1438 | MERGED | Cursor movement and deletion across Unicode graphemes |
| #1439 | MERGED | Ctrl-C cancels the active request while later turns are queued |
| #1440 | MERGED | Ollama `num_ctx` bucketed at 8k, 16k and 32k, never shrunk |
| #1441 | MERGED | One-shot turn engine `python -m driver.turn` (driver phase P1) |
| #1442 | OPEN | Driver spec: the turn engine is the driver (driver phase P0) |
| #1447 | OPEN | Streamed turns as AESOP-PIPE/1 frames, with cancel (driver phase P2) |

The driver phases run P0 to P7. P0 to P2 are as listed above. P3 to P7 are in progress.

## 4. Boundaries

- **Non-Anthropic endpoints only.** The TUI and `aesop ask` call only non-Anthropic endpoints. A provider or model id containing `claude` or `anthropic`, or a host under anthropic.com or claude.ai, is refused before any socket opens, with exit 6 (see `driver/turn.py` and `driver/turn_backend.py`).
- **Claude stays the orchestrator seat.** Model-agnostic describes the workers and the TUI. It does not remove the orchestrator's authority over verdicts and merges.
- **Every peer pushes through the same gates.** A Codex, agy or Ollama lane gets no bypass of the pre-push policy (`hooks/pre-push-policy.sh`) or the secret scan.
