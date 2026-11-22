# chat/ — Interactive chat TUI

**Purpose**: the `aesop chat` verb, a zero-dependency terminal chat on the stdio TTY seam. It renders a frame per change, reads keys through `node:readline`, and streams backend replies chunk by chunk. The default backend is the stdio pipe runtime (`stdio/pipe.mjs`, raw passthrough: the turn text is the pipe's stdin and the reply is its stdout bytes); `pipeSource` yields each stdout write as it arrives. Repaints are line-relative (no full-screen clear). This package makes no network call.

## Keys
- Enter submits the buffer.
- Paste is bracketed paste (ESC[200~ ... ESC[201~): a pasted multi-line block keeps its newlines and stays one buffer until a plain Enter after the paste submits it. No Shift key is needed.
- A trailing backslash then Enter continues the line onto a new line.
- A backslash that is not the last character is literal text.
- ctrl-c once shows a hint; ctrl-c twice exits.
- ctrl-d on an empty line exits; on a non-empty line it deletes the character under the cursor.
- TAB expands to the next 8-column tab stop, counted from the screen column after the `> ` prefix (the buffer column restarts after each newline).
- Esc leaves paste mode (it binds nothing else yet). ctrl-c also leaves paste mode, and still only hints on the first press.
- A paste with no paste-end marker is not stuck: after PASTE_IDLE_MS (400 ms) with no key, paste mode ends and a plain Enter submits.

## Paste lifecycle
- PASTE_ON (ESC[?2004h) is written once at start. PASTE_OFF (ESC[?2004l) is written exactly once, by whichever comes first: finish (`/exit`, ctrl-c twice, ctrl-d on an empty line), a process `exit`, SIGTERM (exit 143), SIGHUP (exit 129) or an external SIGINT (exit 130; POSIX only, on win32 these handlers are not reached). Raw mode is restored in the same single restore.
- A throw while handling a key is rendered as an error line; it ends nothing, writes no PASTE_OFF, and ctrl-d still exits.

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python ~/scripts/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as the code it describes.

## Runtime

Node stdlib only, ESM `.mjs`. No `package.json` inside `chat/`. The TTY decision comes from `stdio/tty.mjs` (`detectMode`, `isTTY`) and is never re-derived here.

## Contract

**Files**
- `chat/render.mjs` — pure. `renderFrame(state, {columns, rows, color, unicode})` returns lines joined by `\n`; `resolveColor({env, argv, isTTY})` (NO_COLOR and `--no-color` off, FORCE_COLOR on); `resolveUnicode({env, unicode})` (first of LC_ALL, LC_CTYPE, LANG decides; UTF-8 on).
- `chat/input.mjs` — pure reducer. `createInputState()`; `applyKey(state, key)` returns `{state, effect}` for readline-shaped keys; `parseSlash(line)` returns `{command, arg?}`, `{error}`, or `null`.
- `chat/session.mjs` — `class Session({columns, rows, color, status, onFrame, source?})`. `submit(line, chunks?)` returns a promise; `resize(columns, rows)` sets the size the next frame uses. `state` holds `transcript` and `status`. `pipeSource(line, {stdin?})` is the default backend (the pipe runtime; `stdin` injects the pipe input and each stdout write streams out as it arrives). The session exports no echo backend; the test-only `echoSource` lives in `tests/helpers/chat-echo-source.mjs`.
- `chat/cli.mjs` — `runChat({argv, stdin, stdout, stderr, env, source?})` returns an exit code. Input history persists in the JSON-array file named by env `AESOP_CHAT_HISTORY` (loaded on start, appended on submit, capped at 500 lines). The save is atomic: it writes `<file>.<pid>.tmp` beside the target, then renames over it. A failed save never ends the session and never corrupts the existing file; it shows one status line in the chat, `history not saved: <error code>`; unset (or empty) defaults to `~/.aesop/chat-history.json` (os.homedir(); tests redirect HOME/USERPROFILE via tests/helpers/isolated-env.mjs, so the default never touches the real home). `shouldEnterChat({interactive, args, env})` is the bare-invocation seam: true only on a TTY with `--chat-default` or `AESOP_DEFAULT=chat`. `bin/cli.js` dynamic-imports this file for both the `chat` verb and the bare case.

**Slash commands**: `/feed on|off|<N>` (see Activity feed), `/help`, `/model <provider>/<model>` (or an alias), `/models`, `/auth`, `/detect`, `/clear`, `/exit`, `/quit`.

**Registry (harness mode, H-R6 to H-R9)**: `loadRegistry` reads `providers`, `aliases` and `default` from the config. Unknown keys inside a provider entry produce a `NOTICE` naming the key only (never its value); top-level keys outside `providers`/`aliases`/`default` are ignored, since the file is shared with other seats. Alias chains resolve up to 3 hops; cycles and deeper chains are a config `error:` line and the alias is dropped. `resolveSelection` makes `/model` accept only a registered provider and a listed model (or `models: "any"`); anything else leaves the selection unchanged with `unknown provider:` or `unknown model:`. `/models` lists aliases and providers with a status (`refused`, `no key`, `reachable`, `unreachable`); the only request it sends is `GET {base_url}/models`. `validateProviderConfig` is the pure gate checked against `tests/fixtures/provider-config-cases.json`.

**Exit codes**
- `0` — help printed, or the interactive session ended (`/exit`, `/quit`, ctrl-c twice, or ctrl-d on an empty line).
- `2` — usage error, or stdin/stdout is not a terminal. In that case stdout stays empty and stderr gets exactly one line naming `aesop pipe`.
- `143` (SIGTERM), `129` (SIGHUP), `130` (SIGINT) — the signal exits; the terminal is restored first. The PASTE_OFF write is best-effort and never changes the exit code, even when it throws.

**Harness backend (`AESOP_CHAT_BACKEND=harness`, `chat/harness.mjs`)**: providers come from the `providers` block of the file named by `AESOP_CHAT_CONFIG`. `assertProviderAllowed(provider, model)` is the single gate, run at load and again at request time. It refuses Anthropic or claude.ai hosts, command-style types (`cli`, `exec`, `claude`, `anthropic`), model ids containing a token-bounded `claude` or `anthropic` match anywhere in the id, including gateway routes (except type `orchestrator`), non-http(s) URLs, and non-loopback http without `allow_insecure`. A refused provider stays visible as a `refused:` line and is never sent a request. `harnessSource` streams OpenAI-compatible SSE over `node:http` and never follows redirects. H-R1 to H-R5 are green; the Python seam (H-R26) is wired behind `AESOP_CHAT_BACKEND=driver` (next paragraph).

**Driver backend (`AESOP_CHAT_BACKEND=driver`, `chat/driver_source.mjs`, spec H-R26)**: every turn runs in the aesop turn engine (`driver/turn.py --serve`, contract in driver/TURN.md). `driverSource` has the signature of `harnessSource` and holds no turn logic: it starts one driver child per session (the Python interpreter from `AESOP_PYTHON`, else `python` on Windows and `python3` elsewhere, on this package's `driver/turn.py`), writes one JSON line per turn on the child's stdin (line, task, selection, the registry as data, and only the key values providers name in `api_key_env`), and reads AESOP-PIPE/1 frames back through `stdio/frames.mjs`.

**Driver backend, session behaviour**: registry loading, `/model`, `/models`, `/auth`, `/detect`, selection memory, ctrl-c and the built-in Ollama default are the same code as harness mode. Ctrl-c sends a `cancel`; the child closes the request and reports `cancelled`. The child is ended on every chat exit path and also exits by itself when its stdin closes. The harness stays the default: the flip waits on `tests/chat-driver-parity.test.mjs` (the same scripted turns through both backends, equal transcripts and equal requests). Known difference, where the driver wins: a provider id that names Claude or Anthropic is refused by the driver. Tests: `tests/chat-driver-parity.test.mjs`, `tests/chat-driver-backend.test.mjs`.

**Routes and fallback (H-R11 to H-R15, H-R18, H-R19, H-R24)**: `routes` maps a task class to a `provider/model` (or alias) or an object `{target, candidates, fallback, fallback_on, strategy}`. A `/<task> text` turn (task declared in `routes`, never `chat`) uses that route; plain turns use the selected model, plus `routes.chat` fallback and strategy when present. Candidates are tried in order, each at most once per turn. Only connect, timeout, rate-limit and server failures (and a stream cut before the first chunk) fall back; a failure after the first chunk shows `stream interrupted from <provider>` and never re-sends. `fallback_on: "any"` also falls back on auth or refused failures. `strategy` is `cheapest` (cost_per_mtok, local = 0) or `fastest` (in-memory latency EWMA).

**Breaker and errors**: A breaker skips a provider after 3 consecutive retryable failures for a 30 s cooldown, announced once. Every failure is one `error: <provider>: <class>: <reason>` line via `harnessSource`'s `notify` option. JSON, SSE and NDJSON replies normalise to the same deltas.

**Harness UI details**: `/auth` in harness mode lists env key status offline (H-R24). Breaker skip (H-R16) is flashed as a one-frame hint, since the transcript repaints every frame. Ctrl-c during a streamed harness reply aborts the request, shows `cancelled`, and never falls back (H-R21). Token counts come from the provider's `usage` (or chars/4 marked `~`), and a local provider's cost is `$0` <!-- metrics-verified: chat/harness.mjs usageOf sets cost = 0 for is_local providers --> (H-R22).

**Detect**: `/detect` probes only loopback `detect_candidates` (ports or loopback URLs) and only when asked; other hosts are listed as skipped (H-R23).

**Wiring in bin/cli.js**: `aesop chat`; bare `aesop` on a TTY enters chat when `--chat-default` or `AESOP_DEFAULT=chat` is set. A bare `aesop` on a non-TTY stays the pipe filter.

## Invariants

1. Zero dependencies: only `node:` builtins and in-repo relative modules, enforced by `tests/chat-zero-dependency.test.mjs`.
2. Never import `ui/` (the web dashboard).
3. `renderFrame` is pure: no I/O and no clock reads. Elapsed time and the spinner tick come from `state.status`.
4. The input reducer is pure: `applyKey` never touches a stream.
5. True streaming: each backend chunk is rendered and emitted before the next chunk is awaited. This holds for injected sources and for the default `pipeSource`. A buffered implementation fails `session_first_chunk_is_framed_before_stream_ends`.
6. `color: false` emits no `ESC` byte. ANSI escapes are hand-written constants, not a library.
7. Box-drawing characters appear only when `unicode` is true. ASCII (`+ - |`) is the fallback; `TERM=dumb` and a non-UTF-8 locale (first of LC_ALL, LC_CTYPE, LANG) force it.
8. Every rendered line fits within `columns` as measured with ANSI stripped. Prefixes are clipped before text is wrapped. A frame never has more than `rows` lines: the oldest transcript lines go first, then the banner, and the input line always stays.
9. The spinner glyph is drawn only on the streaming assistant line. A timer (100 ms, unref'd, cleared when the reply ends) drives `status.spinnerTick` and `status.elapsedMs`, so both move during a backend stall. Each frame repaints in place: the cursor moves up over the lines the previous frame drew, then ESC[J clears to the end of the screen. No ESC[2J or ESC[H is ever written, so scrollback is kept.

## How to test

```
node --test tests/chat-render.test.mjs tests/chat-input.test.mjs tests/chat-cli-wiring.test.mjs tests/chat-zero-dependency.test.mjs
node --test tests/chat-visual.test.mjs
node --test tests/stdio-cli-wiring.test.mjs tests/stdio-pipe.test.mjs tests/stdio-zero-dependency.test.mjs
```

Anti-vacuity: a test is only accepted when breaking the production code in `chat/` turns it red. The streaming mutant (buffer every chunk before emitting) is the reference check.

## Default backend (Ollama)

With `AESOP_CHAT_BACKEND` unset and no injected `source`, `aesop chat` uses the harness. If the config (`AESOP_CHAT_CONFIG`, else `./aesop.config.json`) declares no `providers`, a built-in `ollama` provider (`http://127.0.0.1:11434/v1`, models `any`, local, 300 s idle timeout) is added and `ollama/qwen3-coder:30b` is selected. `AESOP_CHAT_BACKEND=pipe` restores the raw pipe backend. Each plain turn sends the prior completed turns (newest 20 messages or 24000 chars, `boundContext`); failed turns are not remembered. An unreachable provider shows `error: <p>: connect: cannot reach <p> at <origin> - is it running?` (also once at startup for the built-in default); non-2xx replies include the provider's own error message. `/models` also lists the ids a provider serves when its `models` is `any`. Tests: `tests/chat-ollama-default.test.mjs`.

**Ollama context window**: Ollama's `/v1` route ignores `num_ctx` and runs at 4096 tokens, silently cutting the start of long input. A provider with id or type `ollama` is therefore sent to the native `POST {base_url minus /v1}/api/chat` (NDJSON, same deltas; the final frame's `prompt_eval_count`/`eval_count` become usage) with `options.num_ctx` = the smallest bucket of [8192, 16384, 32768] (override: `providers.<id>.num_ctx_buckets`; filtered to `num_ctx_max` and `model_max_ctx`, falling back to the cap if none fit) that holds the prompt estimate (chars/3.5) plus a 1024 reply reserve.

**Sticky num_ctx**: Ollama reloads the model (13-23 s) whenever `num_ctx` changes, so it is sticky: per process and per (provider, base_url, model) a request never sends a smaller `num_ctx` than the last one (it only grows; `aesop ask` is one-shot, so it gets bucketing only). When a request grows the bucket, `notify` emits `growing model window to N (Ollama reloads the model; this can take ~20 s)`. Concurrent asks of different buckets can still force a reload; keep agent prompts within one bucket or set `num_ctx_buckets` to a single value (e.g. `[16384]`). When the prompt alone exceeds the largest bucket, `notify` emits `warning: input ~N tokens exceeds the model window M; the start will be cut` before the reply (chat transcript; `aesop ask` wires `notify` to stderr, exit code unchanged). Other providers are untouched. Tests: `tests/chat-ollama-context.test.mjs`.

## `aesop ask` (one-shot, `chat/ask.mjs`)

`aesop ask [--model <provider>/<model>] [--system <text>] [--from <name>] [--json] [--timeout <s>] "<prompt>"`: `runAsk({argv, stdin, stdout, stderr, env, cwd})` returns the exit code. It reuses the registry code (`loadRegistry`, `applyDefaultProvider`, `assertProviderAllowed`, `resolveSelection`) and the chat config lookup (`AESOP_CHAT_CONFIG`, else `./aesop.config.json`, else the built-in ollama default). No TTY needed; no history file, no conversation state. Plain mode writes only the answer to stdout (a final newline is added if missing); diagnostics go to stderr. `--json` writes one object `{ok, provider, model, text, elapsed_ms, error?}` to stdout (not streamed). `--timeout` is a total wall-clock cap in seconds (default 300).

**Message assembly (finding f-ce0779b1)**: piped (non-TTY) stdin is context. ask sends the prompt, the stdin text and `--system` to the driver as parts, and the driver's one builder (`_build_messages`, driver/TURN.md) assembles them: a system message, then one user message with stdin first and the prompt after it, no delimiter. `tests/chat-ask-parity.test.mjs` requires the request captured from ask to equal the one from `driver/turn.py` for the same input (the legacy harness path joins the same parts in the same order into its single user line).

**The ask turn runs in the driver (spec H-R26)**: `chat/ask.mjs` hands its one turn to `driverSource` (the driver child, `driver/turn.py --serve`) and ends the child before it returns. Arguments, selection, the activity feed and exit codes did not change. `AESOP_ASK_BACKEND=harness` keeps the in-process `harnessSource` as a way back; any other value is exit 2. With no usable Python the turn is `error: driver: connect: cannot start the turn engine` (exit 3).

| Exit | Meaning |
|------|---------|
| 0 | ok |
| 2 | usage (no prompt, unknown option, bad `--timeout`, unknown provider/model, no provider selected) |
| 3 | provider unreachable (`cannot reach <p> at <origin> — is it running?` on stderr) |
| 4 | provider or model error (HTTP error, auth, bad response, stream cut) |
| 5 | timeout |
| 6 | boundary refused (Anthropic host, Claude model id, `cli`/`claude` provider type); no request is sent |

`ask --proof-receipt` takes JSON stdin with `data.sha`, `data.shell` and `data.menu`.
Each menu entry supplies a unique string ID, revision (`sha`), shell, scenario, exit code (`exit`) and elapsed seconds.
The native Ollama request offers IDs proving a successful exit during a held stream on that revision and shell within three seconds, plus `"NONE"`.
The pipe buffers the reply until it validates one JSON string from that enum. An invalid reply exits 4 with empty answer text.
The schema-constrained turn always goes through the native harness (the driver has no `responseSchema`).

Failure classes come from the engine's `error: <provider>: <class>: <reason>` lines (connect=3, timeout=5, refused=6, else 4). Tests: `tests/chat-ask.test.mjs` (spawns the real CLI against a fake 127.0.0.1 server; unchanged by the move to the driver) and `tests/chat-ask-driver.test.mjs` (the turn goes through the driver, the flag, no process left behind).

## Activity feed (`chat/activity_feed.mjs`)

Every `aesop ask` appends JSONL events to ONE per-user file shared by every checkout: `<HOME || USERPROFILE || homedir()>/.aesop/state/chat/activity.jsonl` (override: `AESOP_ACTIVITY_FEED`; `AESOP_STATE_ROOT` is not consulted). The same rule is `driver/activity_feed.py` `feed_path`; keep the two in step. The Python driver (`run_turn`, `OpenAICompatibleDriver`) writes the same schema, with `from` from `AESOP_FROM`: `start {job, ts, from, model, prompt, prompt_bytes, stdin_preview (first 2 KB), stdin_bytes}`, `chunk {job, seq, text}` (split to <=600 chars per line), `error {job, error}` on failure, `end {job, status: ok|error|timeout|refused, exit, elapsed_ms, error?}`. One `appendFileSync` per line, so concurrent askers never interleave within a line. The file rolls to `activity.1.jsonl` at 5 MB. Write failures never change the ask's stdout or exit code (one stderr warning). Caller label: `--from <name>`, else `AESOP_CALLER`, else parent process name (generic shells and node are skipped), else `agent`.

`aesop chat` tails the feed from its current end (`tailFeed`: `fs.watch` on the directory plus a 1 s unref'd interval; no fd held between polls; stopped in the single terminal restore). Each job is a display-only `role: 'feed'` transcript entry (`applyFeedEvent`, drawn by `render.mjs` `feedLines`): dim header, prompt, stdin preview collapsed to 3 lines, streamed answer, status line. Entries never reach `backend.source`, so they are never model context. Slash: `/feed on`, `/feed off`, `/feed N` (replay the last N jobs). Tests: `tests/chat-activity-feed.test.mjs`.

## Peer bus (`chat/peer_bus.mjs`, `chat/peer_format.mjs`)

Slash commands send work to peers and show it in the feed. `/codex <task>` writes the task text as a handoff in `codex_handoff_dir` and opens a visible window via `launch_tui.py` (run script, then `launch.sh`, which records codex's exit to `<job>.exit`; END fires from that file via `fs.watch`, with a timeout). `/codex-app <msg>` runs `codex_send.py --thread <codex_app_thread>` (never the newest-thread default; an empty thread refuses). `/agy <msg>` runs `console_send.py send <agy_pid> <file>` and accepts one printable ASCII line only. `/peers` prints the config and the last status per peer. 
Config: `AESOP_PEERS`, else `~/.aesop/peers.json`, created with defaults on first use; a bad file is an error naming it. Sender paths are overridable (`AESOP_PEER_PYTHON`, `AESOP_PEER_CODEX_SEND`, `AESOP_PEER_CONSOLE_SEND`, `AESOP_PEER_LAUNCH_TUI`, `AESOP_PEER_CODEX_TIMEOUT_MS`) so tests stub them. Feed rows use `kind: "peer"` with `to` (or `dir: "in"` plus `from` for posts); the TUI draws a `->` header with elapsed time, the full body (40 lines, then `(+N lines)`), then a `<-` outcome line. Peer stdout is captured for the SENT id only and is never rendered or written to the feed. `aesop post --from <name> "<text>"` (bin/cli.js, `postCli`) writes one inbound peer event. Tests: `tests/chat-peer-bus.test.mjs`.

## Spec

`chat/SPEC.md` is the durable improvement spec (R1..R24). `chat/SPEC.original.md` is its frozen original and never changes. `tests/chat-spec.test.mjs` has one `R<n>:` test per requirement.
