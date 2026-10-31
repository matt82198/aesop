import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { PassThrough, Readable, Writable } from 'node:stream';

// S1 spec for the chat session default backend (chat/session.mjs) and the terminal driver in
// chat/cli.mjs runChat (bracketed paste, raw-mode restore). Drives the real modules with a fake
// TTY: a PassThrough stdin (real readline keypress decoding) with isTTY and a recording
// setRawMode, and a Writable stdout that captures every byte.
//
// Pinned surface:
//   R1: the default backend is the stdio pipe runtime. stdio/pipe.mjs exports
//       runPipe({stdin, stdout, stderr, argv}) -> Promise<exit code>; the chat default source must
//       produce the reply the pipe runtime emits for the same turn text (raw passthrough).
//   R2: runChat writes ESC[?2004h on a raw TTY at start and ESC[?2004l on exit.
//   R3: setRawMode(false) is the last raw-mode call after ctrl-c twice, ctrl-d, and a source error.
//
// Run: node --test --test-reporter=tap tests/chat-session.test.mjs

const testDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(testDir, '..');
const sessionPath = join(repoRoot, 'chat', 'session.mjs');
const cliPath = join(repoRoot, 'chat', 'cli.mjs');
const pipePath = join(repoRoot, 'stdio', 'pipe.mjs');

async function loadSession() {
  return import(pathToFileURL(sessionPath).href);
}

async function loadChatCli() {
  return import(pathToFileURL(cliPath).href);
}

async function loadPipe() {
  return import(pathToFileURL(pipePath).href);
}

function fakeTTY() {
  const stdin = new PassThrough();
  stdin.isTTY = true;
  const rawCalls = [];
  stdin.setRawMode = (on) => {
    rawCalls.push(on);
  };
  const out = [];
  const stdout = new Writable({
    write(chunk, _enc, cb) {
      out.push(Buffer.from(chunk).toString('utf8'));
      cb();
    },
  });
  stdout.isTTY = true;
  stdout.columns = 80;
  stdout.rows = 24;
  const errOut = [];
  const stderr = new Writable({
    write(chunk, _enc, cb) {
      errOut.push(Buffer.from(chunk).toString('utf8'));
      cb();
    },
  });
  return {
    stdin,
    stdout,
    stderr,
    rawCalls,
    text: () => out.join(''),
    errText: () => errOut.join(''),
  };
}

function settle(promise, ms = 5000) {
  let timer;
  const limit = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`runChat did not exit within ${ms} ms`)), ms);
  });
  return Promise.race([promise, limit]).finally(() => clearTimeout(timer));
}

async function waitFor(predicate, ms = 2000) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > ms) return false;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return true;
}

function captureUnhandled() {
  const seen = [];
  const handler = (reason) => {
    seen.push(reason);
  };
  process.on('unhandledRejection', handler);
  return {
    seen,
    stop: () => process.off('unhandledRejection', handler),
  };
}

/** Runs the pipe runtime in its default raw passthrough mode and returns what it wrote to stdout. */
async function pipeRawOutput(text) {
  const { runPipe } = await loadPipe();
  const written = [];
  const stdin = Readable.from([Buffer.from(text, 'utf8')]);
  const stdout = new Writable({
    write(chunk, _enc, cb) {
      written.push(Buffer.from(chunk));
      cb();
    },
  });
  const stderr = new Writable({
    write(_chunk, _enc, cb) {
      cb();
    },
  });
  const code = await runPipe({ stdin, stdout, stderr, argv: [] });
  assert.equal(code, 0, 'the pipe runtime must exit 0 on a raw passthrough');
  return Buffer.concat(written).toString('utf8');
}

function lastAssistantText(session) {
  const replies = session.state.transcript.filter((entry) => entry.role === 'assistant');
  assert.ok(replies.length > 0, 'a submitted turn must leave an assistant entry in the transcript');
  return replies[replies.length - 1].text;
}

// ---------------------------------------------------------------------------------------------
// R1: the default backend is the pipe runtime, not the word-echo stub.
// ---------------------------------------------------------------------------------------------

test('session_default_source_renders_the_pipe_runtime_reply_for_the_same_turn', async () => {
  const { Session } = await loadSession();
  // Whitespace is the discriminator: the echo stub splits on whitespace and rejoins with single
  // spaces and loses the newline, while the pipe runtime passes the bytes through unchanged.
  const line = 'alpha   beta\ngamma';
  const expected = await pipeRawOutput(line);
  const session = new Session({ columns: 200, rows: 50, onFrame: () => {} });
  await session.submit(line);
  assert.equal(
    lastAssistantText(session),
    expected,
    `the default chat backend must be the pipe runtime: the reply to ${JSON.stringify(line)} must equal the pipe output ${JSON.stringify(expected)}, not a word echo`,
  );
});

test('session_default_source_is_not_a_word_echo_for_hello_world', async () => {
  const { Session } = await loadSession();
  const line = 'hello   world';
  const expected = await pipeRawOutput(line);
  const session = new Session({ columns: 200, rows: 50, onFrame: () => {} });
  await session.submit(line);
  assert.notEqual(
    lastAssistantText(session),
    'hello world',
    'the default backend must not echo the turn word by word (the echo stub collapses the triple space)',
  );
  assert.equal(lastAssistantText(session), expected, 'the default backend must render the pipe runtime output');
});

// ---------------------------------------------------------------------------------------------
// R2: bracketed paste at the terminal driver.
// ---------------------------------------------------------------------------------------------

test('runchat_enables_bracketed_paste_on_start_and_disables_it_on_exit', async () => {
  const { runChat } = await loadChatCli();
  const io = fakeTTY();
  const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env: {} });
  io.stdin.write('\x04');
  const code = await settle(done);
  assert.equal(code, 0, `ctrl-d on an empty line must exit 0; got ${code}`);
  const text = io.text();
  const on = text.indexOf('\x1b[?2004h');
  const off = text.lastIndexOf('\x1b[?2004l');
  assert.ok(on >= 0, `runChat on a raw TTY must enable bracketed paste with ESC[?2004h; output starts: ${JSON.stringify(text.slice(0, 80))}`);
  assert.ok(off > on, `runChat must disable bracketed paste with ESC[?2004l after enabling it; enable at ${on}, disable at ${off}`);
});

test('runchat_bracketed_paste_is_one_submit_on_plain_enter', async () => {
  const { runChat } = await loadChatCli();
  const { echoSource } = await loadSession();
  const io = fakeTTY();
  const calls = [];
  const source = (line) => {
    calls.push(line);
    return echoSource(line);
  };
  const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env: {}, source });
  try {
    io.stdin.write('\x1b[200~first\nsecond\x1b[201~');
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.deepEqual(calls, [], 'the paste must not submit: no backend call before a plain Enter after the paste end');
    io.stdin.write('\r');
    const submitted = await waitFor(() => calls.length === 1);
    assert.ok(submitted, 'a plain Enter after the paste must submit the buffer to the backend');
    assert.equal(calls[0], 'first\nsecond', 'the one submit must carry the whole multi-line paste, newline included');
  } finally {
    io.stdin.write('\x03\x03');
    await settle(done).catch(() => {});
  }
});

// ---------------------------------------------------------------------------------------------
// R3: raw-mode restore on every exit path, and source errors handled in place.
// ---------------------------------------------------------------------------------------------

test('runchat_ctrl_c_twice_restores_raw_mode_as_last_call', async () => {
  const { runChat } = await loadChatCli();
  const { echoSource } = await loadSession();
  const io = fakeTTY();
  const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env: {}, source: echoSource });
  io.stdin.write('\x03');
  io.stdin.write('\x03');
  const code = await settle(done);
  assert.equal(code, 0, 'ctrl-c twice must exit 0');
  assert.equal(
    io.rawCalls[io.rawCalls.length - 1],
    false,
    `the last setRawMode call after ctrl-c twice must be false; calls: ${JSON.stringify(io.rawCalls)}`,
  );
});

test('runchat_ctrl_d_on_empty_line_restores_raw_mode_as_last_call', async () => {
  const { runChat } = await loadChatCli();
  const io = fakeTTY();
  const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env: {} });
  io.stdin.write('\x04');
  const code = await settle(done);
  assert.equal(code, 0, 'ctrl-d on an empty line must exit 0');
  assert.equal(
    io.rawCalls[io.rawCalls.length - 1],
    false,
    `the last setRawMode call after ctrl-d must be false; calls: ${JSON.stringify(io.rawCalls)}`,
  );
});

test('runchat_source_error_thrown_during_submit_is_rendered_and_raw_mode_restored', async () => {
  const { runChat } = await loadChatCli();
  const io = fakeTTY();
  const unhandled = captureUnhandled();
  const source = () => {
    throw new Error('backend exploded');
  };
  const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env: {}, source });
  try {
    io.stdin.write('hi\r');
    const rendered = await waitFor(() => io.text().includes('backend exploded'));
    io.stdin.write('\x04');
    const code = await settle(done);
    assert.ok(rendered, `a source error during submit must be rendered on the error line; output tail: ${JSON.stringify(io.text().slice(-200))}`);
    assert.equal(code, 0, 'after a source error the session must still exit cleanly on ctrl-d');
    assert.equal(
      io.rawCalls[io.rawCalls.length - 1],
      false,
      `raw mode must be restored after a source error; calls: ${JSON.stringify(io.rawCalls)}`,
    );
    assert.deepEqual(unhandled.seen, [], 'a source error must be caught, not left as an unhandled rejection');
  } finally {
    unhandled.stop();
  }
});

test('runchat_async_stream_error_is_rendered_and_raw_mode_restored', async () => {
  const { runChat } = await loadChatCli();
  const io = fakeTTY();
  const unhandled = captureUnhandled();
  async function* breaking() {
    yield 'partial';
    throw new Error('stream broke');
  }
  const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env: {}, source: breaking });
  try {
    io.stdin.write('hi\r');
    const rendered = await waitFor(() => io.text().includes('stream error: stream broke'));
    io.stdin.write('\x04');
    const code = await settle(done);
    assert.ok(rendered, 'a stream error mid-reply must be rendered as "stream error: <message>"');
    assert.equal(code, 0, 'after a stream error the session must still exit cleanly on ctrl-d');
    assert.equal(io.rawCalls[io.rawCalls.length - 1], false, 'raw mode must be restored after a stream error');
    assert.deepEqual(unhandled.seen, [], 'a stream error must not surface as an unhandled rejection');
  } finally {
    unhandled.stop();
  }
});
