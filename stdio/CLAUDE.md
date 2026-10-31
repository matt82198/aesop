# stdio/ — Generic stdin/stdout pipeline

**Purpose**: a Claude-Code-shaped generic filter. Anything piped on stdin is accepted and passes through byte-exact by default. Callers select a format when they need one (text, JSON lines, stream-json, or the frame grammar below). A TTY detection seam is reserved for the future interactive chat.

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
- `stdio/pipe.mjs` — core `runPipe({stdin, stdout, stderr, argv})` returning `Promise<exit code>`.
- `stdio/frames.mjs` — frame encoder and decoder.
- `stdio/tty.mjs` — `isTTY` and `detectMode`; the seam for the interactive chat.

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
- Control message types: `hello`, `meta`, `event`, `receipt`, `seal`, `error`, `done`.
- An unknown control message type marked critical fails closed (exit 2). An unknown non-critical type is skipped.

**stream-json records**
- `data` records carry the payload base64-encoded.
- `result` records carry `bytes` and `sha256` of the full payload.

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
