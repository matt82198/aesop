import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// S1 spec for chat/input.mjs (the pure line-editor reducer and the slash-command parser).
// Drives the real module with readline-shaped key objects; no source-text inspection.
//
// Pinned surface (assumptions listed in the S1 hand-back):
//   createInputState() -> {inputBuffer, cursor, history, historyIndex, ...}
//   applyKey(state, key) -> {state, effect}
//       effect: null | {type:'submit', line} | {type:'quit'} | {type:'hint', text}
//   parseSlash(line) -> null for non-commands | {command, arg?} | {error: string}
//
// Key objects follow node:readline keypress shape: {name, ctrl, meta, sequence}. Enter is
// name 'return'; the implementation must also accept name 'enter'.
//
// Run: node --test --test-reporter=tap tests/chat-input.test.mjs

const testDir = dirname(fileURLToPath(import.meta.url));
const inputPath = join(testDir, '..', 'chat', 'input.mjs');

async function loadInput() {
  return import(pathToFileURL(inputPath).href);
}

const ch = (c) => ({ name: c, ctrl: false, meta: false, sequence: c });
const named = (name) => ({ name, ctrl: false, meta: false, sequence: '' });
const ctrl = (name) => ({ name, ctrl: true, meta: false, sequence: '' });
const enter = () => named('return');
const up = () => named('up');
const down = () => named('down');

function typeAll(applyKey, state, text) {
  let s = state;
  for (const c of text) s = applyKey(s, ch(c)).state;
  return s;
}

test('input_printable_key_inserts_at_cursor', async () => {
  const { applyKey, createInputState } = await loadInput();
  const s = { ...createInputState(), inputBuffer: 'ac', cursor: 1 };
  const r = applyKey(s, ch('b'));
  assert.equal(r.state.inputBuffer, 'abc', 'printable key must insert at the cursor');
  assert.equal(r.state.cursor, 2, 'cursor must advance past the inserted character');
});

test('input_backspace_deletes_before_cursor_and_is_noop_at_start', async () => {
  const { applyKey, createInputState } = await loadInput();
  const mid = applyKey({ ...createInputState(), inputBuffer: 'abc', cursor: 2 }, named('backspace'));
  assert.equal(mid.state.inputBuffer, 'ac', 'backspace must delete the character before the cursor');
  assert.equal(mid.state.cursor, 1);
  const start = applyKey({ ...createInputState(), inputBuffer: 'abc', cursor: 0 }, named('backspace'));
  assert.equal(start.state.inputBuffer, 'abc', 'backspace at column 0 must not change the buffer');
  assert.equal(start.state.cursor, 0);
});

test('input_left_and_right_move_cursor_and_clamp_at_bounds', async () => {
  const { applyKey, createInputState } = await loadInput();
  const base = { ...createInputState(), inputBuffer: 'abc', cursor: 1 };
  assert.equal(applyKey(base, named('left')).state.cursor, 0, 'left moves one column back');
  assert.equal(applyKey({ ...base, cursor: 0 }, named('left')).state.cursor, 0, 'left clamps at 0');
  assert.equal(applyKey(base, named('right')).state.cursor, 2, 'right moves one column forward');
  assert.equal(applyKey({ ...base, cursor: 3 }, named('right')).state.cursor, 3, 'right clamps at buffer end');
});

test('input_home_and_end_move_to_line_bounds', async () => {
  const { applyKey, createInputState } = await loadInput();
  const base = { ...createInputState(), inputBuffer: 'hello', cursor: 2 };
  assert.equal(applyKey(base, named('home')).state.cursor, 0, 'home moves to column 0');
  assert.equal(applyKey(base, named('end')).state.cursor, 5, 'end moves to the buffer length');
});

test('input_ctrl_u_clears_the_line', async () => {
  const { applyKey, createInputState } = await loadInput();
  const r = applyKey({ ...createInputState(), inputBuffer: 'hello world', cursor: 3 }, ctrl('u'));
  assert.equal(r.state.inputBuffer, '', 'ctrl-u must clear the whole input line');
  assert.equal(r.state.cursor, 0, 'cursor must return to 0 after clearing');
});

test('input_ctrl_c_once_prints_hint_and_does_not_quit', async () => {
  const { applyKey, createInputState } = await loadInput();
  const r = applyKey(createInputState(), ctrl('c'));
  assert.ok(r.effect !== null, 'the first ctrl-c must produce an effect (the hint)');
  assert.equal(r.effect.type, 'hint', 'the first ctrl-c must print a hint');
  assert.notEqual(r.effect.type, 'quit', 'a single ctrl-c must not quit');
});

test('input_ctrl_c_twice_quits', async () => {
  const { applyKey, createInputState } = await loadInput();
  const first = applyKey(createInputState(), ctrl('c'));
  const second = applyKey(first.state, ctrl('c'));
  assert.equal(second.effect?.type, 'quit', 'a second consecutive ctrl-c must quit');
});

test('input_ctrl_c_counter_resets_after_another_key', async () => {
  const { applyKey, createInputState } = await loadInput();
  const first = applyKey(createInputState(), ctrl('c'));
  const typed = applyKey(first.state, ch('a'));
  const again = applyKey(typed.state, ctrl('c'));
  assert.notEqual(again.effect?.type, 'quit', 'a ctrl-c separated by another key must not quit');
});

test('input_enter_submits_line_and_clears_buffer', async () => {
  const { applyKey, createInputState } = await loadInput();
  const typed = typeAll(applyKey, createInputState(), 'hi');
  const r = applyKey(typed, enter());
  assert.equal(r.effect?.type, 'submit', 'enter must submit the buffer');
  assert.equal(r.effect.line, 'hi', 'the submitted line must be the buffer contents');
  assert.equal(r.state.inputBuffer, '', 'the buffer must be cleared after submit');
  assert.equal(r.state.cursor, 0, 'the cursor must reset after submit');
});

test('input_up_and_down_walk_history_and_restore_draft', async () => {
  const { applyKey, createInputState } = await loadInput();
  let s = createInputState();
  for (const line of ['one', 'two']) {
    s = applyKey(typeAll(applyKey, s, line), enter()).state;
  }
  s = typeAll(applyKey, s, 'dr');

  let r = applyKey(s, up());
  assert.equal(r.state.inputBuffer, 'two', 'up recalls the most recent entry');
  r = applyKey(r.state, up());
  assert.equal(r.state.inputBuffer, 'one', 'a second up recalls the entry before it');
  r = applyKey(r.state, down());
  assert.equal(r.state.inputBuffer, 'two', 'down walks forward through history');
  r = applyKey(r.state, down());
  assert.equal(r.state.inputBuffer, 'dr', 'down past the newest entry restores the unsent draft');
});

test('input_backslash_enter_continues_instead_of_submitting', async () => {
  const { applyKey, createInputState } = await loadInput();
  const typed = typeAll(applyKey, createInputState(), 'foo\\');
  const r = applyKey(typed, enter());
  assert.notEqual(r.effect?.type, 'submit', 'a line ending in a backslash must not submit on enter');
  assert.ok(r.state.inputBuffer.includes('\n'), 'the continuation must start a new line inside the buffer');
});

test('input_multiline_continuation_submits_joined_lines', async () => {
  const { applyKey, createInputState } = await loadInput();
  let s = applyKey(typeAll(applyKey, createInputState(), 'foo\\'), enter()).state;
  s = typeAll(applyKey, s, 'bar');
  const r = applyKey(s, enter());
  assert.equal(r.effect?.type, 'submit', 'enter on a non-continued line must submit the whole block');
  assert.equal(r.effect.line, 'foo\nbar', 'the continuation backslash is dropped and the lines are joined with newline');
});

test('parse_slash_recognises_each_documented_command', async () => {
  const { parseSlash } = await loadInput();
  assert.equal(parseSlash('/help').command, 'help', '/help must parse to command help');
  assert.equal(parseSlash('/auth').command, 'auth', '/auth must parse to command auth');
  assert.equal(parseSlash('/clear').command, 'clear', '/clear must parse to command clear');
  assert.equal(parseSlash('/exit').command, 'exit', '/exit must parse to command exit');
  assert.equal(parseSlash('/quit').command, 'quit', '/quit must parse to command quit');
});

test('parse_slash_model_carries_provider_slash_model', async () => {
  const { parseSlash } = await loadInput();
  const parsed = parseSlash('/model anthropic/claude-opus-5');
  assert.equal(parsed.command, 'model', '/model must parse to command model');
  assert.equal(parsed.arg, 'anthropic/claude-opus-5', 'the provider/model argument must be carried verbatim');
});

test('parse_slash_unknown_command_returns_error_object_without_throwing', async () => {
  const { parseSlash } = await loadInput();
  let result;
  assert.doesNotThrow(() => {
    result = parseSlash('/nosuchcommand');
  }, 'an unknown slash command must never throw');
  assert.ok(result && typeof result === 'object', 'an unknown slash command must return an object');
  assert.ok(
    typeof result.error === 'string' && result.error.length > 0,
    `an unknown slash command must return {error: non-empty string}; got ${JSON.stringify(result)}`,
  );
});

test('parse_slash_plain_text_is_not_a_command', async () => {
  const { parseSlash } = await loadInput();
  assert.equal(parseSlash('hello there'), null, 'a line without a leading slash is not a slash command');
});

// ---------------------------------------------------------------------------
// chat/session.mjs: streaming and /model (kept in this file: the lane's file list allows four test files)
// ---------------------------------------------------------------------------

const sessionPath = join(testDir, '..', 'chat', 'session.mjs');
const ANSI = /\x1b\[[0-9;?]*[A-Za-z]/g;
const stripAnsi = (s) => s.replace(ANSI, '');
const TIMEOUT_MS = 3000;

async function loadSession() {
  return import(pathToFileURL(sessionPath).href);
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Rejects if the awaited promise does not settle within TIMEOUT_MS, so a hang becomes a failure. */
function withTimeout(promise, label) {
  let timer;
  const guard = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`timed out waiting for ${label}`)), TIMEOUT_MS);
  });
  return Promise.race([promise, guard]).finally(() => clearTimeout(timer));
}

async function* chunksOf(parts) {
  for (const p of parts) yield p;
}

const BASE_STATUS = {
  model: 'claude-haiku-5-5',
  provider: 'anthropic',
  cwd: '/work',
  tokens: 0,
  elapsedMs: 0,
  spinnerTick: 0,
};

test('session_first_chunk_is_framed_before_stream_ends', async () => {
  const { Session } = await loadSession();
  const frames = [];
  const firstSeen = deferred();
  let streamEnded = false;
  let endedWhenFirstFrameSeen = null;
  let releaseRest;
  const rest = new Promise((res) => {
    releaseRest = res;
  });

  async function* backend() {
    yield 'Hel';
    await rest;
    yield 'lo';
    streamEnded = true;
  }

  const session = new Session({
    columns: 80,
    rows: 24,
    color: false,
    status: { ...BASE_STATUS },
    onFrame: (frame) => {
      frames.push(frame);
      if (stripAnsi(frame).includes('Hel') && endedWhenFirstFrameSeen === null) {
        endedWhenFirstFrameSeen = streamEnded;
        firstSeen.resolve();
      }
    },
  });

  const turn = session.submit('hi', backend());
  await withTimeout(firstSeen.promise, 'a frame containing the first chunk "Hel"');
  assert.equal(
    endedWhenFirstFrameSeen,
    false,
    'the frame with the first chunk must be emitted while the backend stream is still open (true streaming)',
  );
  releaseRest();
  await withTimeout(turn, 'the turn to complete');

  const last = stripAnsi(frames[frames.length - 1]);
  assert.ok(last.includes('Hello'), `the final frame must contain the full streamed reply; got:\n${last}`);
});

test('session_model_command_switches_status_without_touching_transcript', async () => {
  const { Session } = await loadSession();
  const session = new Session({
    columns: 80,
    rows: 24,
    color: false,
    status: { ...BASE_STATUS },
    onFrame: () => {},
  });

  await withTimeout(session.submit('hi', chunksOf(['hello back'])), 'the first turn');
  const transcriptBefore = JSON.stringify(session.state.transcript);
  assert.ok(session.state.transcript.length > 0, 'control: the first turn must leave transcript entries');

  await withTimeout(session.submit('/model anthropic/claude-opus-5'), 'the /model command');

  assert.equal(session.state.status.model, 'claude-opus-5', '/model must set status.model to the model part');
  assert.equal(session.state.status.provider, 'anthropic', '/model must set status.provider to the provider part');
  assert.equal(
    JSON.stringify(session.state.transcript),
    transcriptBefore,
    '/model must not add to, remove from, or edit the transcript',
  );
});
