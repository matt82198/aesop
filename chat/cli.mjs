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

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import { dirname, join } from 'node:path';
import readline from 'node:readline';
import { detectMode } from '../stdio/tty.mjs';
import { applyKey, createInputState } from './input.mjs';
// Stdlib-only history store. A missing, unreadable or malformed file starts with empty history.
function loadHistory(file) {
  if (!file) return [];
  try {
    const parsed = JSON.parse(readFileSync(file, 'utf8'));
    return Array.isArray(parsed) ? parsed.filter((l) => typeof l === 'string').slice(-HISTORY_FILE_LIMIT) : [];
  } catch {
    return [];
  }
}
// Best-effort append: history is a convenience, so a write failure never ends the session.
function saveHistory(file, history) {
  if (!file) return;
  try {
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, `${JSON.stringify(history.slice(-HISTORY_FILE_LIMIT), null, 2)}\n`, 'utf8');
  } catch {
    // Ignored on purpose: the line was still submitted.
  }
}
import { resolveColor, resolveUnicode } from './render.mjs';
import { Session, pipeSource } from './session.mjs';

const HELP_TEXT = [
  'aesop chat - interactive chat TUI (needs a terminal on stdin and stdout)',
  '',
  'Slash commands:',
  '  /help                  list these commands',
  '  /model <prov>/<model>  switch provider and model (transcript is kept)',
  '  /auth                  show credential status (no network call)',
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
  source = pipeSource,
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
  // Number of terminal lines the last frame drew; the next frame moves back over them.
  let drawnLines = 0;
  const session = new Session({
    columns: stdout.columns || 80,
    rows: stdout.rows || 24,
    color: resolveColor({ env, argv, isTTY: true }),
    unicode: resolveUnicode({ env }),
    status: { cwd: process.cwd() },
    onFrame: (frame) => {
      const up = drawnLines > 1 ? CURSOR_UP(drawnLines - 1) : '';
      drawnLines = frame.split('\n').length;
      stdout.write(`\r${up}${CLEAR_TO_END}${frame}`);
    },
  });

  return new Promise((resolve) => {
    let input = { ...createInputState(), history, historyIndex: history.length };
    let chain = Promise.resolve();

    // The terminal is restored exactly once, whichever exit path runs first: finish (/exit, ctrl-c
    // twice, ctrl-d), a process 'exit' (written synchronously), or SIGTERM / SIGHUP / SIGINT. Later calls are no-ops.
    let restored = false;
    const restoreTerminal = () => {
      if (restored) return;
      restored = true;
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

    const submit = (line) => {
      if (line.trim() !== '') {
        history.push(line);
        saveHistory(historyFile, history);
      }
      chain = chain.then(async () => {
        // Slash commands run inside the session; only a plain turn ever reaches the backend source.
        const chunks = line.startsWith('/') ? undefined : source(line);
        await session.submit(line, chunks);
        if (session.state.exit) finish(0);
      }).catch((err) => {
        // A backend that throws synchronously is rendered, never left as an unhandled rejection;
        // the session keeps running so ctrl-d can still exit cleanly.
        session.state.transcript.push({ role: 'system', text: `error: ${err && err.message ? err.message : err}` });
        session.emit();
      });
    };

    const onResize = () => {
      session.resize(stdout.columns || 80, stdout.rows || 24);
      session.setInput(input);
    };

    const onKeypress = (str, key) => {
      const before = input;
      try {
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
      const result = applyKey(input, toKey(str, key));
      input = result.state;
      const effect = result.effect;
      if (effect && effect.type === 'quit') {
        finish(0);
        return;
      }
      if (effect && effect.type === 'hint') {
        session.setHint(effect.text);
      } else if (effect && effect.type === 'submit') {
        session.setHint(null);
        session.setInput(input);
        submit(effect.line);
        return;
      }
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
