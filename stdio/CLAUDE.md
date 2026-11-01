# stdio/ — Generic stdin/stdout pipeline

**Purpose**: a Claude-Code-shaped generic filter. Anything piped on stdin is accepted and passes through byte-exact by default. Callers select a format when they need one (text, JSON lines, stream-json, or the frame grammar below). The TTY detection seam (`stdio/tty.mjs`) is used by `chat/` (the interactive chat); pipes stay clean.

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as the code it describes.

## Runtime

Node stdlib only, ESM `.mjs`. Node was chosen because `bin/cli.js` and `mcp/server.mjs` are already Node, and Node gives uniform binary stdio on Windows and Linux.

## Contract

**Files**
- `stdio/cli.mjs` — entrypoint; parses flags and calls the pipe core.
- `stdio/pipe.mjs` — core `runPipe({stdin, stdout, stderr, argv})` returning `Promise<exit code>`. The chat's default backend `pipeSource(line, {stdin?})` in `chat/session.mjs` drives this core in raw passthrough and streams each stdout write as it arrives (it accepts an injected `stdin`); this file's contract is unchanged.
- `stdio/frames.mjs` — frame encoder and decoder.
- `stdio/tty.mjs` — `isTTY` and `detectMode`; the seam for the interactive chat.
- `stdio/tty-decision.js` — CommonJS home of `isTTY` and `detectMode` (`stream.isTTY === true`); `tty.mjs` re-exports it, `bin/cli.js` requires it directly.

**Flags**
- `--input-format raw|text|stream-json|frames` (default `raw`)
- `--output-format raw|text|json|stream-json|frames` (default `raw`)
- `--content-type <mime>` — content type declared for raw payloads
- `--max-frame <bytes>` — upper bound on one frame payload

**Frame grammar**
```
AESOP-PIPE/1\n
F <channel> <content-type> <length>\n<payload>\n
E <exit>\n
```
- Channels: `data` and `control`.
- Control message types: `hello`, `meta`, `event`, `receipt`, `seal`, `error`, `done`. An `error` control frame exits 2 with the fixed line `upstream reported an error`; its message is never echoed.
- An unknown control message type marked critical fails closed (exit 2). An unknown non-critical type is skipped.

**stream-json records**
- `data` records carry the payload base64-encoded.
- `result` records carry `bytes` and `sha256` of the full payload.
- `control` records (op dispatch) are accepted only with `--input-format stream-json --output-format stream-json`. Each record `{"type":"control","op":"<op>","id":<string|number|null>}` gets one stdout reply line `{"type":"control","op":...,"id":...,"ok":true,"result":{...}}`; a control-only stream prints just its replies. Ops come from a registry injected by `setControlDispatcher` (auth/cli.mjs, preloaded by bin/cli.js via `--import`); without a registration the pipe exits 2 with one `aesop-pipe:` line. A refused op does not stop the pipe: it gets an ok:false reply and the pipe exits 0. An unknown op gets `{"type":"control","id":<id>,"ok":false,"error":"unknown op","code":"unknown_op"}` with no `op` field; a known op with a rejected argument keeps `op` and a fixed safe `error` (for example `unknown model selector`).
  - Every error envelope carries a machine-readable `code` (R-CONTROL-1). The code set is owned by auth/cli.mjs `CONTROL_ERROR_CODES`; a refusal with no code is reported as `op_failed`.
  - Malformed framing (a non-JSON line, or a control record with no `id`) is a FramingError: exit 2, exactly one stderr line with the `aesop-pipe: ` prefix, and no stdout record.
- No caller-supplied string (flag, op, selector, provider, label, record type, a `result` record's bytes value) is echoed on stdout or stderr; `unknown flag`, `unknown option` and `unexpected argument` lines carry no value. `--control` or `--control=<x>` on any verb or entry path, the pipe included, exits 2 with the bare `CONTROL_CHANNEL_MESSAGE` line (no `aesop-pipe:` or `error:` prefix). The line and the one predicate `hasControlFlag` are defined once in stdio/pipe.mjs and re-exported by auth/cli.mjs (the pipe never imports auth/).

**Exit codes**
- `0` — ok.
- `2` — usage error, or malformed input the caller explicitly declared.
- EPIPE on stdout exits `0`.

## Invariants

1. Default `raw` mode never parses or alters bytes.
2. Never buffer the whole input. Backpressure is respected: pause reads while the output is full.
3. Stdout carries payload only. Stderr carries one-line `aesop-pipe: <reason>` diagnostics only.
4. Fail closed only on malformed input the caller explicitly declared a format for.
5. Zero dependencies, enforced by `tests/stdio-zero-dependency.test.mjs`.
6. No escape sequences on a non-TTY stdout.
7. Aesop-specific content (receipts, events) is only a control message type. Nothing aesop-specific is required to use the pipeline.

## How to test

```
node --test tests/stdio-pipe.test.mjs tests/stdio-zero-dependency.test.mjs tests/stdio-cli-wiring.test.mjs
```

Anti-vacuity: a test is only accepted when breaking the production code in `stdio/` turns it red.
