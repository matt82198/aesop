# chat/ — Interactive chat TUI

**Purpose**: the `aesop chat` verb, a zero-dependency terminal chat on the stdio TTY seam. It renders a frame per change, reads keys through `node:readline`, and streams backend replies chunk by chunk when the backend yields incrementally (an injected source). The default backend is the stdio pipe runtime (`stdio/pipe.mjs`, raw passthrough: the turn text is the pipe's stdin and the reply is its stdout bytes); it yields its one chunk only after the pipe finishes, so the default reply is buffered. This package makes no network call.

## Keys
- Enter submits the buffer.
- Paste is bracketed paste (ESC[200~ ... ESC[201~): a pasted multi-line block keeps its newlines and stays one buffer until a plain Enter after the paste submits it. No Shift key is needed.
- A trailing backslash then Enter continues the line onto a new line.
- A backslash that is not the last character is literal text.
- ctrl-c once shows a hint; ctrl-c twice exits.
- ctrl-d on an empty line exits; on a non-empty line it deletes the character under the cursor.
- Esc is not yet bound.

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
- `chat/session.mjs` — `class Session({columns, rows, color, status, onFrame, source?})`. `submit(line, chunks?)` returns a promise; `resize(columns, rows)` sets the size the next frame uses. `state` holds `transcript` and `status`. `pipeSource(line)` is the default backend (the pipe runtime). The session exports no echo backend; the test-only `echoSource` lives in `tests/helpers/chat-echo-source.mjs`.
- `chat/cli.mjs` — `runChat({argv, stdin, stdout, stderr, env, source?})` returns an exit code. `shouldEnterChat({interactive, args, env})` is the bare-invocation seam: true only on a TTY with `--chat-default` or `AESOP_DEFAULT=chat`. `bin/cli.js` dynamic-imports this file for both the `chat` verb and the bare case.

**Slash commands**: `/help`, `/model <provider>/<model>`, `/auth`, `/clear`, `/exit`, `/quit`.

**Exit codes**
- `0` — help printed, or the interactive session ended (`/exit`, `/quit`, ctrl-c twice, or ctrl-d on an empty line).
- `2` — usage error, or stdin/stdout is not a terminal. In that case stdout stays empty and stderr gets exactly one line naming `aesop pipe`.
- `143` (SIGTERM), `129` (SIGHUP), `130` (SIGINT) — the signal exits; the terminal is restored first. The PASTE_OFF write is best-effort and never changes the exit code, even when it throws.

**Wiring in bin/cli.js**: `aesop chat`; bare `aesop` on a TTY enters chat when `--chat-default` or `AESOP_DEFAULT=chat` is set. A bare `aesop` on a non-TTY stays the pipe filter.

## Invariants

1. Zero dependencies: only `node:` builtins and in-repo relative modules, enforced by `tests/chat-zero-dependency.test.mjs`.
2. Never import `ui/` (the web dashboard).
3. `renderFrame` is pure: no I/O and no clock reads. Elapsed time and the spinner tick come from `state.status`.
4. The input reducer is pure: `applyKey` never touches a stream.
5. True streaming: each backend chunk is rendered and emitted before the next chunk is awaited. This holds for injected sources only; the default `pipeSource` buffers its single chunk. A buffered implementation fails `session_first_chunk_is_framed_before_stream_ends`.
6. `color: false` emits no `ESC` byte. ANSI escapes are hand-written constants, not a library.
7. Box-drawing characters appear only when `unicode` is true. ASCII (`+ - |`) is the fallback; `TERM=dumb` and a non-UTF-8 locale (first of LC_ALL, LC_CTYPE, LANG) force it.
8. Every rendered line fits within `columns` as measured with ANSI stripped. Prefixes are clipped before text is wrapped. A frame never has more than `rows` lines: the oldest transcript lines go first, then the banner, and the input line always stays.
9. The spinner glyph is drawn only on the streaming assistant line, and it advances only when a backend chunk arrives (`status.spinnerTick` increments per chunk; there is no timer). Each frame is written whole after a clear-and-home, so the terminal repaints the full screen, not just that line.

## How to test

```
node --test tests/chat-render.test.mjs tests/chat-input.test.mjs tests/chat-cli-wiring.test.mjs tests/chat-zero-dependency.test.mjs
node --test tests/stdio-cli-wiring.test.mjs tests/stdio-pipe.test.mjs tests/stdio-zero-dependency.test.mjs
```

Anti-vacuity: a test is only accepted when breaking the production code in `chat/` turns it red. The streaming mutant (buffer every chunk before emitting) is the reference check.
