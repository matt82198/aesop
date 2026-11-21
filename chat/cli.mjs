/**
 * Entry for the `aesop chat` verb: runChat({argv, stdin, stdout, stderr, env}) -> exit code.
 *
 * Behaviour:
 *  - --help / -h prints the slash commands and returns 0 (no TTY needed);
 *  - any other flag (except --no-color) is a usage error (exit 2);
 *  - when stdin or stdout is not a terminal, nothing goes to stdout, exactly one line goes to
 *    stderr naming `aesop pipe`, and the exit code is 2. The decision comes from stdio/tty.mjs;
 *  - on a terminal it enters raw mode, reads keys through node:readline, and renders each
 *    frame through Session and renderFrame. Backend chunks come from a pluggable async-iterable
 *    source; the default is the stdio pipe runtime in raw passthrough (no network call).
 *  - bracketed paste is enabled (ESC[?2004h) on start and disabled (ESC[?2004l) exactly once: on finish
 *    (/exit, ctrl-c twice, ctrl-d), on a process 'exit', and on SIGTERM / SIGHUP / SIGINT (exit 143 / 129 / 130).
 *    A throw while handling a key is rendered as an error line and does not end the session.
 *
 * Node stdlib only; never imports ui/.
 *
 * @module chat/cli
 */

import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import { dirname, join } from 'node:path';
import readline from 'node:readline';
import { detectMode } from '../stdio/tty.mjs';
import { PASTE_IDLE_MS, applyKey, createInputState } from './input.mjs';
import { driverSource } from './driver_source.mjs';
import { applyDefaultProvider, authLines, detectLines, harnessSource, listModelLines, loadRegistry, matchRoute, probeProvider, resolveSelection } from './harness.mjs';
// Stdlib-only history store. A missing, unreadable or malformed file starts with empty history.
// AESOP_CHAT_BACKEND values. Unset or empty means pipe. Any other value is an error line (H-R28).
const BACKEND_VALUES = new Set(['pipe', 'orchestrator', 'harness', 'driver']);
// Per-viewer selection memory (H-R10): {provider, model} only, never a key. Stored under the state root.
function selectionFile(settings) {
  return join(settings.AESOP_STATE_ROOT || join(os.homedir(), '.aesop'), 'chat-selection.json');
}
function loadSelection(file, registry) {
  try {
    const parsed = JSON.parse(readFileSync(file, 'utf8'));
    if (typeof parsed?.provider !== 'string' || typeof parsed?.model !== 'string') return null;
    const picked = resolveSelection(registry, `${parsed.provider}/${parsed.model}`);
    return picked.error ? null : { provider: picked.provider, model: picked.model };
  } catch {
    return null;
  }
}
// Atomic write; a failed save is silent because the selection is only a convenience.
function saveSelection(file, selection) {
  const tmp = `${file}.${process.pid}.tmp`;
  try {
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(tmp, `${JSON.stringify({ provider: selection.provider, model: selection.model })}\n`, 'utf8');
    renameSync(tmp, file);
  } catch {
    try { rmSync(tmp, { force: true }); } catch { /* best effort */ }
  }
}

function loadHistory(file) {
  if (!file) return [];
  try {
    const parsed = JSON.parse(readFileSync(file, 'utf8'));
    return Array.isArray(parsed) ? parsed.filter((l) => typeof l === 'string').slice(-HISTORY_FILE_LIMIT) : [];
  } catch {
    return [];
  }
}
// Read used only by the save merge. ENOENT (no file yet) and malformed JSON count as empty history.
// Any other read error (EBUSY, EPERM, EACCES, EISDIR, ...) is rethrown so the save refuses to write:
// an empty merge would otherwise rename a one-line file over the whole existing history.
function readHistoryForSave(file) {
  let text;
  try {
    text = readFileSync(file, 'utf8');
  } catch (err) {
    if (err && err.code === 'ENOENT') return [];
    throw err;
  }
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed) ? parsed.filter((l) => typeof l === 'string').slice(-HISTORY_FILE_LIMIT) : [];
  } catch {
    return [];
  }
}
// Atomic save: the line is merged into the file as it is now on disk (re-read here, so a second
// instance's lines survive), written to a temp file beside the target, then renamed over it. A failed
// write never leaves the existing file truncated or half-written. A failure never ends the session;
// it returns a short reason (the error code) that the caller shows in the chat, or null on success.
function saveHistory(file, line) {
  if (!file) return null;
  const tmp = `${file}.${process.pid}.tmp`;
  try {
    mkdirSync(dirname(file), { recursive: true });
    const merged = readHistoryForSave(file);
    if (merged[merged.length - 1] !== line) merged.push(line);
    writeFileSync(tmp, `${JSON.stringify(merged.slice(-HISTORY_FILE_LIMIT), null, 2)}\n`, 'utf8');
    renameSync(tmp, file);
    return null;
  } catch (err) {
    try {
      rmSync(tmp, { force: true });
    } catch {
      // Nothing more to clean up; the save already failed.
    }
    return (err && err.code) || 'unknown error';
  }
}
import { displayWidth, resolveColor, resolveUnicode } from './render.mjs';
import { Session, pipeSource } from './session.mjs';

const HELP_TEXT = [
  'aesop chat - interactive chat TUI (needs a terminal on stdin and stdout)',
  '',
  'Slash commands:',
  '  /help                  list these commands',
  '  /model <prov>/<model>  switch provider and model (transcript is kept)',
  '  /auth                  show credential status (no network call)',
  '  /detect                probe loopback candidates only, on request',
  '  /clear                 clear the transcript',
  '  /exit, /quit           leave chat (ctrl-c twice also quits)',
  '',
  'Keys: enter submits; end a line with a backslash then enter to continue it;',
  'up/down walk history; ctrl-u clears the line; ctrl-c once shows a hint, twice quits.',
  '',
  'Options: --no-color disables color (NO_COLOR also does).',
].join('\n');

// Hand-written ANSI. Frames repaint in place: the cursor moves back up over the lines the previous
// frame drew, then ESC[J clears from there to the end of the screen. Scrollback above the frame is
// never wiped (no ESC[2J, no ESC[H).
const CURSOR_UP = (n) => `\x1b[${n}A`;
const CLEAR_TO_END = '\x1b[J';
const SGR_PATTERN = /\x1b\[[0-9;]*m/g;
// Physical terminal rows that logical lines occupy at `columns`: a line wider than the terminal
// wraps onto extra rows, and an empty line still takes one.
function physicalRows(lines, columns) {
  return lines.reduce((n, l) => n + Math.max(1, Math.ceil(displayWidth(l) / columns)), 0);
}
// Bracketed paste mode: the terminal wraps pasted text in ESC[200~ / ESC[201~.
const PASTE_ON = '\x1b[?2004h';
const PASTE_OFF = '\x1b[?2004l';
// Input history persists across sessions in the file named by AESOP_CHAT_HISTORY (a JSON array).
const HISTORY_FILE_LIMIT = 500;

/**
 * Maps a readline keypress to the reducer's key shape.
 * @param {string} str
 * @param {object|undefined} key
 */
function toKey(str, key) {
  if (key && typeof key === 'object') return key;
  const seq = typeof str === 'string' ? str : '';
  return { name: seq, ctrl: false, meta: false, sequence: seq };
}

/**
 * Runs the chat verb.
 * @param {{argv?: string[], stdin?: object, stdout?: object, stderr?: object,
 *          env?: Record<string, string|undefined>, source?: (line: string) => AsyncIterable<string>}} [io]
 * @returns {Promise<number>} exit code
 */
export async function runChat({
  argv = [],
  stdin = process.stdin,
  stdout = process.stdout,
  stderr = process.stderr,
  env = process.env,
  source,
} = {}) {
  if (argv.includes('--help') || argv.includes('-h')) {
    stdout.write(`${HELP_TEXT}\n`);
    return 0;
  }
  const unknown = argv.find((arg) => arg.startsWith('-') && arg !== '--no-color');
  if (unknown !== undefined) {
    stderr.write(`aesop chat: unknown option ${unknown} (try aesop chat --help)\n`);
    return 2;
  }
  if (detectMode({ stdin, stdout }) !== 'interactive') {
    stderr.write('aesop chat needs an interactive terminal; use `aesop pipe` for piped streams\n');
    return 2;
  }
  return runInteractive({ argv, stdin, stdout, env, source });
}

/**
 * Decides whether a bare `aesop` invocation opens chat instead of the wizard. Chat is opt-in
 * (--chat-default in args, or AESOP_DEFAULT=chat) and only ever on a terminal; a pipe stays the filter.
 * bin/cli.js calls this from its bare-invocation path.
 * @param {{interactive: boolean, args?: string[], env?: Record<string, string|undefined>}} opts
 * @returns {boolean}
 */
export function shouldEnterChat({ interactive, args = [], env = {} } = {}) {
  if (interactive !== true) return false;
  return args.includes('--chat-default') || env.AESOP_DEFAULT === 'chat';
}

function runInteractive({ argv, stdin, stdout, env, source }) {
  // Unset (or empty) AESOP_CHAT_HISTORY falls back to ~/.aesop/chat-history.json.
  const historyFile = env.AESOP_CHAT_HISTORY || join(os.homedir(), '.aesop', 'chat-history.json');
  const history = loadHistory(historyFile);
  // Plain text of the lines the last frame drew. After a resize the terminal reflows those lines
  // at the new width, so the next frame moves back over the physical rows they now occupy.
  let drawnPlain = [];
  // AESOP_CHAT_BACKEND=harness selects the model-agnostic provider registry (chat/harness.mjs).
  const backendValue = env.AESOP_CHAT_BACKEND;
  const badBackend = backendValue !== undefined && backendValue !== '' && !BACKEND_VALUES.has(backendValue);
  // Default backend: the model harness (local Ollama unless the config declares providers). An injected
  // source, or an explicit AESOP_CHAT_BACKEND=pipe|orchestrator, keeps the old behaviour.
  // AESOP_CHAT_BACKEND=driver runs the same registry, commands and keys, but every turn goes through the
  // aesop turn engine (driver/turn.py) by way of chat/driver_source.mjs. It takes the same config lookup and
  // built-in Ollama default as the unset case. The harness stays the default (spec H-R26).
  const driver = !source && backendValue === 'driver';
  const implicitHarness = !source && (backendValue === undefined || backendValue === '' || driver);
  const harness = backendValue === 'harness' || implicitHarness;
  let configPath = env.AESOP_CHAT_CONFIG;
  if (!configPath && implicitHarness) {
    const local = join(process.cwd(), 'aesop.config.json');
    if (existsSync(local)) configPath = local;
  }
  const registry = harness ? loadRegistry(configPath) : null;
  const defaulted = implicitHarness ? applyDefaultProvider(registry) : false;
  const stateFile = selectionFile(env);
  const selection = registry ? (loadSelection(stateFile, registry) ?? registry.defaultSelection) : null;
  const status = { cwd: process.cwd() };
  if (harness) Object.assign(status, { provider: selection ? selection.provider : null, model: selection ? selection.model : null });
  const backend = { source: source ?? pipeSource };
  if (badBackend) {
    // Never a silent default: the value is named, and turns get the error instead of a backend (H-R28).
    backend.source = async function* () { /* no backend for an unknown value */ };
  }
  // Harness hooks: /model is checked against the registry (an unknown name leaves the selection
  // unchanged); /models lists providers and aliases with probes that send only GET /models.
  const hooks = harness
    ? {
      setModel: (arg) => {
        const picked = resolveSelection(registry, arg);
        if (picked.error) return picked.error;
        session.state.status.provider = picked.provider;
        session.state.status.model = picked.model;
        if (registry) saveSelection(stateFile, picked);
        return null;
      },
      listModels: () => listModelLines(registry, env),
      auth: () => authLines(registry, env),
      detect: () => detectLines(registry, env),
    }
    : {};
  const session = new Session({
    columns: stdout.columns || 80,
    rows: stdout.rows || 24,
    color: resolveColor({ env, argv, isTTY: true }),
    unicode: resolveUnicode({ env }),
    status,
    hooks,
    onFrame: (frame) => {
      const rows = physicalRows(drawnPlain, stdout.columns || 80);
      const up = rows > 1 ? CURSOR_UP(rows - 1) : '';
      drawnPlain = frame.split('\n').map((l) => l.replace(SGR_PATTERN, ''));
      stdout.write(`\r${up}${CLEAR_TO_END}${frame}`);
    },
  });
  if (badBackend) {
    session.state.transcript.push({ role: 'system', text: `error: AESOP_CHAT_BACKEND: unknown value "${backendValue}" (use pipe, orchestrator, harness or driver)` });
  }
  if (harness) {
    // Fallback, error and cancel lines go to the transcript as they happen (H-R12, H-R19, H-R21).
    const notify = (text) => session.state.transcript.push({ role: 'system', text });
    // A skip notice is flashed for one frame: the transcript repaints every frame, so a line kept there
    // would be drawn again on each repaint (H-R16 announces the skip once).
    const flash = (text) => { session.setHint(text); session.setHint(null); };
    backend.source = (driver ? driverSource : harnessSource)(registry, () => ({ provider: session.state.status.provider, model: session.state.status.model }), env, {
      notify,
      flash,
      usage: (u) => session.setUsage(u),
    });
    for (const text of registry.notices) session.state.transcript.push({ role: 'system', text });
    if (defaulted) {
      // Startup reachability probe (GET /models on the loopback default). Unreachable is a visible line;
      // the session stays usable and never falls back to echo.
      const entry = registry.providers.get('ollama');
      probeProvider(entry, env, 1500).then((status) => {
        if (status === 'reachable') return;
        notify('error: ollama: connect: cannot reach ollama at http://127.0.0.1:11434 — is it running?');
        session.emit();
      }).catch(() => {});
    }
    if (!selection) session.state.transcript.push({ role: 'system', text: 'no provider configured (see /help)' });
  }

  return new Promise((resolve) => {
    let input = { ...createInputState(), history, historyIndex: history.length };
    let chain = Promise.resolve();

    // The terminal is restored exactly once, whichever exit path runs first: finish (/exit, ctrl-c
    // twice, ctrl-d), a process 'exit' (written synchronously), or SIGTERM / SIGHUP / SIGINT. Later calls are no-ops.
    let restored = false;
    let pasteFlushTimer = null;
    const clearPasteFlush = () => {
      if (pasteFlushTimer !== null) {
        clearTimeout(pasteFlushTimer);
        pasteFlushTimer = null;
      }
    };
    const restoreTerminal = () => {
      if (restored) return;
      restored = true;
      // The driver backend owns a child process; end it on every exit path. Other backends have no close.
      try { backend.source.close?.(); } catch { /* best effort: never changes the exit code */ }
      clearPasteFlush();
      stdin.removeListener('keypress', onKeypress);
      if (typeof stdout.removeListener === 'function') stdout.removeListener('resize', onResize);
      process.removeListener('exit', onProcessExit);
      process.removeListener('SIGTERM', onSigterm);
      process.removeListener('SIGHUP', onSighup);
      process.removeListener('SIGINT', onSigint);
      // PASTE_OFF is written first, best-effort: a throw from that write (EPIPE on a closed terminal) is
      // swallowed so the caller still reaches process.exit(code) with no stack trace. Raw mode is cleared
      // in a finally after the write attempt, so it is cleared whether or not the write threw.
      try {
        stdout.write(PASTE_OFF);
      } catch {
        // Best-effort: a failed PASTE_OFF write never changes the exit code.
      } finally {
        try {
          if (typeof stdin.setRawMode === 'function') stdin.setRawMode(false);
        } catch {
          // Best-effort: a closed or detached terminal never changes the exit code.
        }
      }
    };
    const onProcessExit = () => restoreTerminal();
    const onSignal = (code) => () => {
      restoreTerminal();
      process.exit(code);
    };
    const onSigterm = onSignal(143);
    const onSighup = onSignal(129);
    const onSigint = onSignal(130);

    const finish = (code) => {
      restoreTerminal();
      stdin.pause();
      resolve(code);
    };

    // The abort controller for the turn in flight. In harness mode ctrl-c while a reply streams aborts
    // the request (H-R21); the pipe backend keeps the hint-then-quit behaviour.
    let turnAbort = null;
    const submit = (line) => {
      let notice = null;
      // Consecutive duplicates store one entry, matching the reducer.
      if (line.trim() !== '') {
        if (history[history.length - 1] !== line) history.push(line);
        const failure = saveHistory(historyFile, line);
        if (failure) notice = `history not saved: ${failure}`;
      }
      chain = chain.then(async () => {
        // Only the turn at the front of the queue owns Ctrl-C.
        turnAbort = harness ? new AbortController() : null;
        // Slash commands run inside the session; only a plain turn ever reaches the backend source.
        // A harness route turn (`/code ...`) reaches the backend with its task class; other slash lines are commands.
        const routed = harness ? matchRoute(registry, line) : null;
        const signal = turnAbort?.signal;
        const chunks = routed ? backend.source(routed.body, { task: routed.task, signal })
          : line.startsWith('/') ? undefined : backend.source(line, signal ? { signal } : undefined);
        await session.submit(line, chunks);
        // A failed history save is shown as one visible status line; the session keeps running.
        if (notice) {
          session.state.transcript.push({ role: 'system', text: notice });
          session.emit();
        }
        if (session.state.exit) finish(0);
      }).catch((err) => {
        // A backend that throws synchronously is rendered, never left as an unhandled rejection;
        // the session keeps running so ctrl-d can still exit cleanly.
        session.state.transcript.push({ role: 'system', text: `error: ${err && err.message ? err.message : err}` });
        session.emit();
      }).finally(() => {
        turnAbort = null;
      });
    };

    const onResize = () => {
      session.resize(stdout.columns || 80, stdout.rows || 24);
      session.setInput(input);
    };

    const onKeypress = (str, key) => {
      const before = input;
      try {
        if (turnAbort && session.state.mode === 'streaming' && key && key.ctrl && key.name === 'c') {
          // Ctrl-c during a streamed harness reply aborts that request; the turn shows "cancelled".
          turnAbort.abort();
          return;
        }
        handleKey(str, key);
      } catch (err) {
        // A throw while handling a key (a failed render, for example) is shown as an error line and
        // never escapes to the stream or the process. The key that threw is dropped (the line editor
        // goes back to its state before the key), the session keeps running, and ctrl-d still exits.
        input = before;
        try {
          session.state.transcript.push({ role: 'system', text: `error: ${err && err.message ? err.message : err}` });
          session.setInput(input);
        } catch {
          // The render failed again; the next key press re-renders the whole state.
        }
      }
    };

    const handleKey = (str, key) => {
      const result = applyKey(input, { ...toKey(str, key), now: Date.now() });
      input = result.state;
      const effect = result.effect;
      if (effect && effect.type === 'quit') {
        finish(0);
        return;
      }
      if (effect && effect.type === 'hint') {
        clearPasteFlush();
        session.setHint(effect.text);
        session.setInput(input);
        return;
      }
      if (effect && effect.type === 'submit') {
        clearPasteFlush();
        session.setHint(null);
        session.setInput(input);
        submit(effect.line);
        return;
      }
      if (session.state.hint) {
        // Any key other than ctrl-c retires the quit hint.
        session.setHint(null);
      }
      if (input.pasting) {
        clearPasteFlush();
        pasteFlushTimer = setTimeout(() => {
          pasteFlushTimer = null;
          if (!restored) session.setInput(input);
        }, PASTE_IDLE_MS);
        pasteFlushTimer.unref?.();
        return;
      }
      clearPasteFlush();
      session.setInput(input);
    };

    // PASTE_ON is written only after setRawMode and every restore listener are in place, so a
    // throw from setRawMode or emitKeypressEvents leaves nothing on the terminal to undo.
    readline.emitKeypressEvents(stdin);
    stdin.setRawMode(true);
    stdin.on('keypress', onKeypress);
    if (typeof stdout.on === 'function') stdout.on('resize', onResize);
    process.on('exit', onProcessExit);
    process.on('SIGTERM', onSigterm);
    process.on('SIGHUP', onSighup);
    process.on('SIGINT', onSigint);
    try {
      stdout.write(PASTE_ON);
    } catch (err) {
      // Idempotent: a PASTE_OFF follows any PASTE_ON that did get written.
      restoreTerminal();
      throw err;
    }
    stdin.resume();
    session.setInput(input);
  });
}
