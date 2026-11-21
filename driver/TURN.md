# driver/TURN.md: the turn engine contract

PURPOSE: this artifact exists to make the aesop chat TUI, aesop ask and codex/gemini/agy run every turn through the aesop driver (one turn engine; the pipe is the product); status: real

The driver is the one turn engine (spec H-R26 in `docs/specs/chat-model-harness.md`). This file is the detailed contract for `python -m driver.turn`; `driver/CLAUDE.md` carries only the pointer. Boundary: non-Anthropic only. No code here calls the Anthropic API or the `claude` CLI.

## Files

- `turn_backend.py` (P1): `run_turn`, one non-streamed turn over `openai_compatible_driver`; `refusal_reason`; the exit-code constants.
- `turn_stream.py` (P2): `stream_request`, `stream_once`, `run_stream_turn`, `CancelToken`, `TurnFailure`. One streamed request to one provider entry `{"id", "raw"}` (the chat registry shape).
- `turn_context.py` (P3): `size_ollama_context`, `ContextSizer` (sticky window), `is_ollama`.
- `turn_frames.py` (P2): `FrameWriter`, `encode_frame`, `encode_end`, `decode_stream` for the AESOP-PIPE/1 grammar that `stdio/frames.mjs` decodes.
- `turn.py`: the CLI.

## Modes

```
python -m driver.turn [--json | --stream | --frames] [--model provider/model] [--base-url url] [--system text] [--timeout s] "prompt"
python -m driver.turn --serve [--model provider/model] [--base-url url]
```

- default: the whole reply on stdout, then a newline.
- `--json`: one object `{ok, provider, model, text, elapsed_ms, error?}`.
- `--stream`: text deltas on stdout as they arrive; system lines on stderr.
- `--frames`: one AESOP-PIPE/1 stream for the turn (below).
- `--serve`: one child for many turns (below).
- `--base-url`: an OpenAI-compatible base URL instead of the built-in local provider. The provider id is the `--model` prefix, or `custom`. A loopback host is treated as local (no key).
- `--timeout`: total wall-clock cap in seconds for the one-shot modes (default 300). When it fires the turn exits 5.

Piped (non-TTY) stdin is appended to the prompt as context in the one-shot modes.

## Exit codes (same table as `aesop ask`)

| Exit | Meaning |
|------|---------|
| 0 | ok |
| 2 | usage |
| 3 | provider unreachable (class `connect`) |
| 4 | provider or model error (`auth`, `rate-limit`, `server`, `bad-response`, `no-key`, a stream cut) |
| 5 | timeout |
| 6 | boundary refused; no socket is opened |
| 130 | cancelled (Ctrl-C in a one-shot streaming mode) |

## Frames stream

```
AESOP-PIPE/1
F control application/json <n>   {"type":"hello","protocol":1,"engine":"driver.turn"}
F data text/plain;charset=utf-8 <n>   <one text delta>
F control application/json <n>   {"type":"event","event":"notify","text":"error: <provider>: <class>: <reason>"}
F control application/json <n>   {"type":"event","event":"usage","usage":{...}}
F control application/json <n>   {"type":"done","ok":true,"exit":0,"provider":"...","model":"...","text":"...","elapsed_ms":123}
E <exit>
```

- `<n>` counts payload bytes. Each frame is flushed as soon as it is written, so a delta is visible before the next one is read from the provider.
- A failed turn's `done` carries `error`; a cancelled one carries `"cancelled": true`.
- System lines use the chat harness wording: `error: <provider>: <class>: <reason>`, `stream interrupted from <provider>` (a failure after the first delta; the partial text is kept) and `cancelled: <provider> request aborted`.

## Serve mode

Requests are one JSON object per stdin line:

- `{"op":"turn","id":<id>,"line":"text"}` or `{"op":"turn","id":<id>,"messages":[...]}`, optional `"model"`.
- `{"op":"cancel","id":<id>}` closes that turn's socket; the turn ends with `cancelled` and the session keeps serving.
- `{"op":"shutdown"}` or end of stdin cancels the turn in flight and ends the stream with `E 0`.

The session is ONE frames stream; every control frame of a turn carries its `id`, and a turn's `done` frame carries that turn's exit code. Turns run one at a time, in order. A line that is not a JSON object, or an unknown op, gets one `error: driver: bad-request: <reason>` notify event and the session continues.

## Transport rules

- `http.client` only: no proxy environment variable is read, and a redirect is never followed (a 3xx is `bad-response: redirect <status> not followed`).
- The idle timeout is the provider's `timeout_ms` (default 60000; the built-in local provider uses 300000). It bounds the connect, the wait for the response head and each wait for more body bytes.
- A key is read only from the env var named by `api_key_env`; a keyed provider with the variable unset and `is_local` not true is `no-key` and sends nothing.
- A parent that must stop the child at once uses `proc_util.kill_process_tree`; the provider then sees the connection close.

## Ollama context window (P3, `turn_context.py`, finding f-db2dd2fa)

Ollama's `/v1` route ignores `num_ctx` and runs at 4096 tokens, silently cutting the START of long input. So a provider with id or type `ollama` is sent to the native `POST {base_url minus /v1}/api/chat` (NDJSON, same deltas; the final frame's `prompt_eval_count` and `eval_count` become usage) with `options.num_ctx`. Every mode takes this route: the default mode and `--json` use the streamed path too, and only an injected test transport keeps the P1 `/v1` call.

- Window: the smallest bucket of 8192 / 16384 / 32768 (override `num_ctx_buckets`), filtered to `num_ctx_max` (default 32768) and `model_max_ctx`, that holds the prompt estimate (UTF-16 length / 3.5, rounded up) plus a 1024 reply reserve. If no bucket fits the filter, the cap itself is used; if none is large enough, the largest is used.
- Sticky: Ollama reloads the model when `num_ctx` changes, so per process and per (provider, base_url, model) the window never shrinks. A growth shows `growing model window to N (Ollama reloads the model; this can take ~20 s)`. A one-shot turn is one process, so it gets bucketing only; a `--serve` session gets the sticky behaviour.
- Over the window: when the prompt alone exceeds the window, `warning: input ~N tokens exceeds the model window M; the start will be cut` is shown before the reply (a `notify` event in frames mode, stderr in the other modes). The exit code does not change.
- Shared fixture: `tests/fixtures/num-ctx-cases.json` is run by `tests/test_turn_context.py` (this module) and by `tests/chat-num-ctx-fixture.test.mjs` (`sizeOllamaContext` and the sticky logic in `chat/harness.mjs`), so the two cannot drift.

## Tests

`tests/test_turn_backend.py`, `tests/test_turn_cli.py` (P1), `tests/test_turn_stream.py`, `tests/test_turn_serve.py` (P2), `tests/test_turn_context.py` (P3). The fake provider is `tests/turn_fake_server.py` on 127.0.0.1; nothing touches a real network.
