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
 *    source; the default is the local echo source (no network call).
 *
 * Node stdlib only; never imports ui/.
 *
 * @module chat/cli
 */

import readline from 'node:readline';
import { detectMode } from '../stdio/tty.mjs';
import { applyKey, createInputState } from './input.mjs';
import { resolveColor, resolveUnicode } from './render.mjs';
import { Session, echoSource } from './session.mjs';

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

// Hand-written ANSI: clear the screen and home the cursor before each frame.
const CLEAR_AND_HOME = '\x1b[2J\x1b[H';

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
  source = echoSource,
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

function runInteractive({ argv, stdin, stdout, env, source }) {
  const session = new Session({
    columns: stdout.columns || 80,
    rows: stdout.rows || 24,
    color: resolveColor({ env, argv, isTTY: true }),
    unicode: resolveUnicode({ env }),
    status: { cwd: process.cwd() },
    onFrame: (frame) => stdout.write(`${CLEAR_AND_HOME}${frame}`),
  });

  return new Promise((resolve) => {
    let input = createInputState();
    let chain = Promise.resolve();

    const finish = (code) => {
      stdin.removeListener('keypress', onKeypress);
      if (typeof stdin.setRawMode === 'function') stdin.setRawMode(false);
      stdin.pause();
      resolve(code);
    };

    const submit = (line) => {
      chain = chain.then(async () => {
        await session.submit(line, source(line));
        if (session.state.exit) finish(0);
      });
    };

    const onKeypress = (str, key) => {
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

    readline.emitKeypressEvents(stdin);
    stdin.setRawMode(true);
    stdin.on('keypress', onKeypress);
    stdin.resume();
    session.setInput(input);
  });
}
