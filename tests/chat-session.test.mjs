import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
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

// Test-only echo backend. chat/session.mjs no longer exports echoSource (finding C); the suite
// injects this copy instead.
async function loadEchoSource() {
  return import(pathToFileURL(join(repoRoot, 'tests', 'helpers', 'chat-echo-source.mjs')).href);
}

const PASTE_ON = '\x1b[?2004h';
const PASTE_OFF = '\x1b[?2004l';

function countOccurrences(haystack, needle) {
  return haystack.split(needle).length - 1;
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
  const { echoSource } = await loadEchoSource();
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
  const { echoSource } = await loadEchoSource();
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

// ---------------------------------------------------------------------------------------------
// A: bracketed paste is disabled on EVERY exit path (review round 2, finding A). Today only the
// finish() path writes ESC[?2004l; there is no process 'exit' listener, no SIGTERM/SIGHUP handler,
// the keypress handler has no try/catch, and finish() is not idempotent.
// ---------------------------------------------------------------------------------------------

/**
 * Spawns `node -e <script>` with a TTY-shaped stdin and stdout (a --require shim, same technique as
 * the bin/cli.js wiring tests). stdin stays open so the child lives until it is signalled or exits.
 */
function spawnChatScript(script) {
  const dir = mkdtempSync(join(tmpdir(), 'aesop-chat-paste-'));
  const shimPath = join(dir, 'tty-shim.cjs');
  writeFileSync(shimPath, [
    'process.stdin.isTTY = true;',
    'process.stdout.isTTY = true;',
    'process.stdin.setRawMode = () => {};',
  ].join('\n'));
  const out = [];
  const errOut = [];
  const proc = spawn(process.execPath, ['--require', shimPath, '-e', script], {
    cwd: dir,
    env: { ...process.env },
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
  });
  proc.stdout.on('data', (c) => out.push(c));
  proc.stderr.on('data', (c) => errOut.push(c));
  proc.stdin.on('error', () => {});
  const exited = new Promise((resolve) => {
    proc.on('close', (code, signal) => resolve({ code, signal }));
  });
  return {
    proc,
    exited,
    text: () => Buffer.concat(out).toString('utf8'),
    errText: () => Buffer.concat(errOut).toString('utf8'),
    cleanup: () => {
      try {
        rmSync(dir, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
      } catch {
        // Windows can hold handles briefly; cleanup never fails a test.
      }
    },
  };
}

test('paste_off_written_once_and_raw_mode_restored_once_when_finish_runs_twice', async () => {
  const { runChat } = await loadChatCli();
  const io = fakeTTY();
  const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env: {} });
  // One chunk, so readline decodes both keys in the same tick: "/exit" + Enter queues the submit,
  // then ctrl-d on the now-empty line quits (finish call 1). The queued submit then sees exit and
  // calls finish again (finish call 2). finish() must be idempotent.
  io.stdin.write('/exit\r\x04');
  const code = await settle(done);
  assert.equal(code, 0, `/exit followed by ctrl-d must exit 0; got ${code}`);
  assert.equal(
    countOccurrences(io.text(), PASTE_OFF),
    1,
    `finish() must write ESC[?2004l exactly once even when it runs twice; ${countOccurrences(io.text(), PASTE_OFF)} writes; tail: ${JSON.stringify(io.text().slice(-120))}`,
  );
  assert.equal(
    io.rawCalls.filter((on) => on === false).length,
    1,
    `finish() must restore raw mode exactly once; setRawMode calls: ${JSON.stringify(io.rawCalls)}`,
  );
});

test('throw_inside_keypress_handler_does_not_escape_and_session_still_cleans_up_at_exit', async () => {
  const { runChat } = await loadChatCli();
  const io = fakeTTY();
  const escaped = [];
  const onUncaught = (err) => {
    escaped.push(err);
  };
  process.on('uncaughtException', onUncaught);
  const realWrite = io.stdout.write.bind(io.stdout);
  let failNextWrite = false;
  io.stdout.write = (chunk, ...rest) => {
    if (failNextWrite) {
      failNextWrite = false;
      throw new Error('injected render failure');
    }
    return realWrite(chunk, ...rest);
  };
  const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env: {} });
  try {
    const ready = await waitFor(() => io.text().includes(PASTE_ON));
    assert.ok(ready, 'runChat must enable bracketed paste before the injected failure');
    failNextWrite = true;
    try {
      io.stdin.write('a');
    } catch (err) {
      escaped.push(err);
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
    io.stdin.write('\x04');
    const outcome = await settle(done).catch((err) => err);
    assert.deepEqual(
      escaped.map((e) => e.message),
      [],
      'a throw inside the keypress handler path must be caught where it happens; it escaped to the stream or the process instead',
    );
    assert.ok(
      !(outcome instanceof Error),
      `after a handled render failure ctrl-d must still end the session; got: ${outcome instanceof Error ? outcome.message : outcome}`,
    );
    const code = outcome;
    assert.equal(code, 0, `after a handled render failure ctrl-d must still exit 0; got ${code}`);
    const text = io.text();
    assert.ok(
      text.lastIndexOf(PASTE_OFF) > text.indexOf(PASTE_ON),
      'after a keypress-handler throw the real exit must still write ESC[?2004l',
    );
    assert.equal(io.rawCalls[io.rawCalls.length - 1], false, `raw mode must be restored at the real exit; calls: ${JSON.stringify(io.rawCalls)}`);
  } finally {
    process.off('uncaughtException', onUncaught);
    io.stdout.write = realWrite;
    io.stdin.write('\x03\x03');
    await settle(done).catch(() => {});
  }
});

// Item 1 (review round 3): chat/cli.mjs writes PASTE_ON before stdin.setRawMode(true) and before the
// exit/SIGTERM/SIGHUP/keypress listeners are registered. If setRawMode throws there, runInteractive
// rejects with paste still on and nothing restores it. Red until PASTE_ON moves after the listeners.
test('runchat_setrawmode_throw_never_leaves_paste_on_without_paste_off', async () => {
  const { runChat } = await loadChatCli();
  const io = fakeTTY();
  io.stdin.setRawMode = () => {
    throw new Error('injected setRawMode failure');
  };
  const outcome = await settle(
    runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env: {} }),
  ).then((value) => ({ value }), (error) => ({ error }));
  const text = io.text();
  const on = text.indexOf(PASTE_ON);
  const off = text.lastIndexOf(PASTE_OFF);
  assert.ok(
    on < 0 || off > on,
    `a setRawMode throw must not leave bracketed paste enabled: stdout must contain no ESC[?2004h, or a ESC[?2004l after it; stdout after PASTE_ON: ${JSON.stringify(text.slice(on))}; outcome: ${outcome.error ? `rejected with ${outcome.error.message}` : `resolved ${outcome.value}`}`,
  );
});

test('sigterm_to_a_live_chat_writes_paste_off_before_the_process_dies', { skip: process.platform === 'win32' && 'SIGTERM on win32 is TerminateProcess: no handler can run, assertion is unobservable here; runs on POSIX CI' }, async () => {
  const href = pathToFileURL(cliPath).href;
  const child = spawnChatScript(
    `import(${JSON.stringify(href)}).then((m) => m.runChat({ argv: [] })).then((code) => { process.exitCode = code; });`,
  );
  try {
    const ready = await waitFor(() => child.text().includes(PASTE_ON), 15000);
    assert.ok(ready, `the chat child must enable bracketed paste before SIGTERM is sent; stdout: ${JSON.stringify(child.text())}`);
    child.proc.kill('SIGTERM');
    const result = await Promise.race([
      child.exited,
      new Promise((_, reject) => setTimeout(() => reject(new Error('chat child did not exit after SIGTERM')), 15000)),
    ]);
    const text = child.text();
    const on = text.indexOf(PASTE_ON);
    const off = text.lastIndexOf(PASTE_OFF);
    assert.ok(
      off > on,
      `SIGTERM must make chat write ESC[?2004l on stdout before the process dies; stdout after PASTE_ON: ${JSON.stringify(text.slice(on))}; exit: ${JSON.stringify(result)}`,
    );
    assert.ok(
      result.signal === 'SIGTERM' || result.code === 143,
      `SIGTERM must map to exit 143 or signal SIGTERM; got code ${result.code} signal ${result.signal}`,
    );
  } finally {
    if (child.proc.exitCode === null && child.proc.signalCode === null) child.proc.kill('SIGKILL');
    child.cleanup();
  }
});

test('sigint_to_a_live_chat_writes_paste_off_before_the_process_dies', { skip: process.platform === 'win32' && 'SIGINT from outside on win32 is TerminateProcess: no handler can run, assertion is unobservable here; runs on POSIX CI' }, async () => {
  const href = pathToFileURL(cliPath).href;
  const child = spawnChatScript(
    `import(${JSON.stringify(href)}).then((m) => m.runChat({ argv: [] })).then((code) => { process.exitCode = code; });`,
  );
  try {
    const ready = await waitFor(() => child.text().includes(PASTE_ON), 15000);
    assert.ok(ready, `the chat child must enable bracketed paste before SIGINT is sent; stdout: ${JSON.stringify(child.text())}`);
    child.proc.kill('SIGINT');
    const result = await Promise.race([
      child.exited,
      new Promise((_, reject) => setTimeout(() => reject(new Error('chat child did not exit after SIGINT')), 15000)),
    ]);
    const text = child.text();
    const on = text.indexOf(PASTE_ON);
    const off = text.lastIndexOf(PASTE_OFF);
    assert.ok(
      off > on,
      `SIGINT from outside must make chat write ESC[?2004l on stdout before the process dies; stdout after PASTE_ON: ${JSON.stringify(text.slice(on))}; exit: ${JSON.stringify(result)}`,
    );
    assert.ok(
      result.signal === 'SIGINT' || result.code === 130,
      `SIGINT must map to exit 130 or signal SIGINT; got code ${result.code} signal ${result.signal}`,
    );
  } finally {
    if (child.proc.exitCode === null && child.proc.signalCode === null) child.proc.kill('SIGKILL');
    child.cleanup();
  }
});

// The SIGTERM listener calls restoreTerminal, which writes PASTE_OFF. A write that throws (EPIPE on a
// closed terminal) escapes the listener before process.exit(143) runs, so the documented code does not
// hold: Node dies with an uncaught exception (exit 1) and prints a stack trace. Red until onSignal
// exits with its code even when the PASTE_OFF write throws.
test('sigterm_with_a_throwing_paste_off_write_still_exits_143_without_a_stack_trace', { skip: process.platform === 'win32' && 'SIGTERM on win32 is TerminateProcess: no handler can run, assertion is unobservable here; runs on POSIX CI' }, async () => {
  const href = pathToFileURL(cliPath).href;
  // The child wraps process.stdout.write so the PASTE_OFF sequence throws like a write to a closed pipe.
  const child = spawnChatScript([
    'const realWrite = process.stdout.write.bind(process.stdout);',
    'process.stdout.write = (chunk, ...rest) => {',
    `  if (String(chunk).includes(${JSON.stringify(PASTE_OFF)})) { const e = new Error('write EPIPE'); e.code = 'EPIPE'; throw e; }`,
    '  return realWrite(chunk, ...rest);',
    '};',
    `import(${JSON.stringify(href)}).then((m) => m.runChat({ argv: [] })).then((code) => { process.exitCode = code; });`,
  ].join('\n'));
  try {
    const ready = await waitFor(() => child.text().includes(PASTE_ON), 15000);
    assert.ok(ready, `the chat child must enable bracketed paste before SIGTERM is sent; stdout: ${JSON.stringify(child.text())}`);
    child.proc.kill('SIGTERM');
    const result = await Promise.race([
      child.exited,
      new Promise((_, reject) => setTimeout(() => reject(new Error('chat child did not exit after SIGTERM')), 15000)),
    ]);
    const err = child.errText();
    assert.ok(
      result.signal === 'SIGTERM' || result.code === 143,
      `SIGTERM with a throwing PASTE_OFF write must still map to exit 143 or signal SIGTERM; got code ${result.code} signal ${result.signal}; stderr: ${JSON.stringify(err)}`,
    );
    assert.ok(
      !/^\s+at\s/m.test(err),
      `SIGTERM with a throwing PASTE_OFF write must not print a stack trace; stderr: ${JSON.stringify(err)}`,
    );
  } finally {
    if (child.proc.exitCode === null && child.proc.signalCode === null) child.proc.kill('SIGKILL');
    child.cleanup();
  }
});

test('process_exit_listener_writes_paste_off_when_the_process_exits_mid_chat', async () => {
  const href = pathToFileURL(cliPath).href;
  // Chat is live when process.exit(0) fires from outside the chat code path, so only a process
  // 'exit' listener can restore the terminal.
  const child = spawnChatScript(
    `import(${JSON.stringify(href)}).then((m) => m.runChat({ argv: [] })); setTimeout(() => process.exit(0), 1500);`,
  );
  try {
    const result = await Promise.race([
      child.exited,
      new Promise((_, reject) => setTimeout(() => reject(new Error('chat child did not exit')), 20000)),
    ]);
    const text = child.text();
    const on = text.indexOf(PASTE_ON);
    assert.ok(on >= 0, `the chat child must enable bracketed paste before process.exit; stdout: ${JSON.stringify(text)}`);
    assert.ok(
      text.lastIndexOf(PASTE_OFF) > on,
      `a process 'exit' during chat must write ESC[?2004l (no exit listener does so today); stdout after PASTE_ON: ${JSON.stringify(text.slice(on))}; exit: ${JSON.stringify(result)}`,
    );
  } finally {
    if (child.proc.exitCode === null && child.proc.signalCode === null) child.proc.kill('SIGKILL');
    child.cleanup();
  }
});

// ---------------------------------------------------------------------------------------------
// C: the test-only echo backend is not a product export (review round 2, finding C).
// ---------------------------------------------------------------------------------------------

test('session_module_exports_no_echo_source_name', async () => {
  const mod = await loadSession();
  assert.equal(
    'echoSource' in mod,
    false,
    'chat/session.mjs must not export echoSource: the echo backend is test-only and is imported from tests/helpers/chat-echo-source.mjs',
  );
});

test('test_helper_provides_an_injectable_echo_source', async () => {
  const { echoSource } = await loadEchoSource();
  assert.equal(typeof echoSource, 'function', 'tests/helpers/chat-echo-source.mjs must export echoSource as a function');
  const words = [];
  for await (const chunk of echoSource('one  two')) words.push(chunk);
  assert.equal(words.join(''), 'one two', 'the helper echo must stream the words of the line, space-joined');
});
