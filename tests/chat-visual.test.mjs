import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { PassThrough } from 'node:stream';

// S1 spec (red) for the chat TUI visual gaps: the chat "needs to be at minimum as visual as
// Claude Code". Reference behaviour is Claude Code's TUI. Each R-test pins one observable
// behaviour; the GUARD test pins behaviour that already holds and must stay green.
//
//   R1  repaint in place: no full-screen clear (ESC[2J) or cursor-home (ESC[H) while streaming.
//   R2  spinner advances on time, not only on chunk arrival (a 600 ms stall must show >= 2 changes).
//   R3  streaming pipe source: pipeSource yields its first chunk before the backend closes stdin.
//       PINNED SEAM: pipeSource(line, { stdin }) accepts an injected stdin stream (chat/session.mjs:53).
//   R4  a TAB byte typed in the input line is kept (as a tab or as spaces), never dropped.
//   R5  input history survives a process restart. PINNED SEAM: env AESOP_CHAT_HISTORY names the
//       history file (read by chat/cli.mjs:132).
//   GUARD  bracketed paste off (ESC[?2004l) is still emitted on exit.
//
// Run: node --import ./tests/helpers/isolated-env.mjs --test --test-reporter=spec --test-reporter-destination=.s1.log --test-force-exit --test-timeout=60000 tests/chat-visual.test.mjs

const testDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(testDir, '..');
const cliPath = join(repoRoot, 'bin', 'cli.js');
const sessionPath = join(repoRoot, 'chat', 'session.mjs');
const inputPath = join(repoRoot, 'chat', 'input.mjs');

const CLEAR_SCREEN = '\x1b[2J';
const CURSOR_HOME = '\x1b[H';
const PASTE_OFF = '\x1b[?2004l';
const PASTE_ON = '\x1b[?2004h';
const UP_ARROW = '\x1b[A';

function freshTempDir(prefix = 'aesop-chat-visual-') {
  return mkdtempSync(join(tmpdir(), prefix));
}

function safeRm(p) {
  try {
    rmSync(p, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  } catch {
    // Windows can hold handles briefly; cleanup never fails a test.
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Makes the child's stdin and stdout look like a terminal so bin/cli.js enters chat.
// Same shim the chat-cli-wiring suite uses.
function writeTtyShim() {
  const shimDir = freshTempDir('aesop-chat-visual-tty-');
  const shim = join(shimDir, 'tty-shim.cjs');
  writeFileSync(shim, [
    'process.stdin.isTTY = true;',
    'process.stdout.isTTY = true;',
    'process.stdin.setRawMode = () => {};',
  ].join('\n'));
  return { shimDir, shimPath: shim.replace(/\\/g, '/') };
}

/**
 * Spawns `node bin/cli.js chat` on a shimmed TTY. `keys` is written to stdin in one chunk.
 * Optional env entries are merged over the parent env.
 */
async function runChatTty(keys, { cwd, env = {}, shimPath }) {
  return new Promise((resolve, reject) => {
    const proc = spawn(process.execPath, [cliPath, 'chat'], {
      cwd,
      env: {
        ...process.env,
        ...env,
        NODE_OPTIONS: `--require "${shimPath}"`,
      },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    const out = [];
    const err = [];
    const killTimer = setTimeout(() => proc.kill(), 20000);
    proc.stdout.on('data', (c) => out.push(c));
    proc.stderr.on('data', (c) => err.push(c));
    proc.stdin.on('error', () => {});
    proc.on('error', (e) => {
      clearTimeout(killTimer);
      reject(e);
    });
    proc.on('close', (code, signal) => {
      clearTimeout(killTimer);
      resolve({
        code,
        signal,
        stdout: Buffer.concat(out).toString('utf8'),
        stderr: Buffer.concat(err).toString('utf8'),
      });
    });
    proc.stdin.end(keys);
  });
}

function countOccurrences(haystack, needle) {
  return haystack.split(needle).length - 1;
}

// ---------------------------------------------------------------------------------------------
// R1: repaint in place
// ---------------------------------------------------------------------------------------------

test('R1_streaming_reply_never_emits_full_screen_clear_or_cursor_home', async () => {
  const dir = freshTempDir();
  const { shimDir, shimPath } = writeTtyShim();
  try {
    // One submitted line, then ctrl-d on the empty line to leave chat.
    const r = await runChatTty('hello\r\x04', { cwd: dir, shimPath });
    assert.equal(r.code, 0, `chat must exit 0 after ctrl-d; got ${r.code}; stderr:\n${r.stderr}`);
    assert.ok(r.stdout.includes('hello'), `the submitted line must be rendered; stdout:\n${r.stdout}`);
    assert.equal(
      countOccurrences(r.stdout, CLEAR_SCREEN),
      0,
      `a streaming reply must repaint in place: no full-screen clear (ESC[2J) may be emitted; found ${countOccurrences(r.stdout, CLEAR_SCREEN)}`,
    );
    assert.equal(
      countOccurrences(r.stdout, CURSOR_HOME),
      0,
      `a streaming reply must repaint in place: no cursor-home (ESC[H) may be emitted; found ${countOccurrences(r.stdout, CURSOR_HOME)}`,
    );
  } finally {
    safeRm(dir);
    safeRm(shimDir);
  }
});

// ---------------------------------------------------------------------------------------------
// R2: spinner advances on time
// ---------------------------------------------------------------------------------------------

test('R2_spinner_and_elapsed_advance_during_a_600ms_stall_after_one_chunk', async () => {
  const { Session } = await import(pathToFileURL(sessionPath).href);
  let stallStart = 0;
  let stallOver = false;
  const source = async function* stalled() {
    yield 'partial';
    stallStart = Date.now();
    await sleep(600);
    stallOver = true;
    yield ' done';
  };
  const session = new Session({
    columns: 80,
    rows: 24,
    color: false,
    onFrame: () => {},
    source,
  });

  const snapshots = [];
  const sampler = setInterval(() => {
    if (stallStart > 0 && !stallOver) {
      snapshots.push(JSON.stringify(session.state.status ?? null));
    }
  }, 50);
  try {
    await session.submit('go');
  } finally {
    clearInterval(sampler);
  }

  const distinct = new Set(snapshots);
  assert.ok(
    snapshots.length >= 4,
    `the sampler must observe the stall; got ${snapshots.length} samples during a 600 ms stall`,
  );
  assert.ok(
    distinct.size >= 3,
    `spinner frame and elapsed counter must change at least twice while the backend is stalled (chunk-driven spinner never moves); got ${distinct.size} distinct status snapshots over ${snapshots.length} samples: ${[...distinct].join(' | ')}`,
  );
});

// ---------------------------------------------------------------------------------------------
// R3: streaming pipe source
// ---------------------------------------------------------------------------------------------

test('R3_default_pipe_source_yields_first_chunk_before_backend_closes_stdin', async () => {
  const { pipeSource } = await import(pathToFileURL(sessionPath).href);
  const stdin = new PassThrough();
  const gen = pipeSource('ignored', { stdin });
  const first = gen.next();
  let firstSettled = false;
  first.then(
    () => {
      firstSettled = true;
    },
    () => {
      firstSettled = true;
    },
  );
  try {
    // The backend writes a chunk and keeps stdin open (it has not closed yet).
    stdin.write('first-chunk\n');
    await sleep(500);
    assert.ok(
      firstSettled,
      'pipeSource must yield the first chunk to the renderer before the backend closes stdout; the default source is buffering the whole reply',
    );
    const got = await first;
    assert.ok(
      !got.done && String(got.value).includes('first-chunk'),
      `the first yielded chunk must carry the first backend write; got ${JSON.stringify(got)}`,
    );
  } finally {
    stdin.end();
    // Drain the generator so the pipe runtime settles; a failure here is not the assertion.
    await gen.return?.().catch(() => {});
  }
});

// ---------------------------------------------------------------------------------------------
// R4: TAB is not dropped
// ---------------------------------------------------------------------------------------------

test('R4_tab_byte_in_input_line_is_kept_as_tab_or_spaces_not_dropped', async () => {
  const { applyKey, createInputState } = await import(pathToFileURL(inputPath).href);
  const tabKey = { name: 'tab', ctrl: false, meta: false, sequence: '\t' };
  let state = createInputState();
  for (const ch of 'a') {
    state = applyKey(state, { name: ch, ctrl: false, meta: false, sequence: ch }).state;
  }
  state = applyKey(state, tabKey).state;
  state = applyKey(state, { name: 'b', ctrl: false, meta: false, sequence: 'b' }).state;

  const serialized = JSON.stringify(state);
  // A tab shows up as the two characters backslash-t in JSON; expanded spaces show as spaces.
  assert.match(
    serialized,
    /a(\\t| {1,8})b/,
    `a TAB between "a" and "b" must be inserted as a tab or as spaces, not silently dropped; state after a,TAB,b: ${serialized}`,
  );
});

// ---------------------------------------------------------------------------------------------
// R5: input history across sessions
// ---------------------------------------------------------------------------------------------

test('R5_up_arrow_restores_last_line_submitted_in_a_previous_session', async () => {
  const dir = freshTempDir();
  const stateDir = freshTempDir('aesop-chat-history-');
  const historyFile = join(stateDir, 'chat-history.json');
  const { shimDir, shimPath } = writeTtyShim();
  const env = { AESOP_CHAT_HISTORY: historyFile };
  try {
    // Session 1: submit one line, then ctrl-d on the empty line.
    const first = await runChatTty('remember me\r\x04', { cwd: dir, env, shimPath });
    assert.equal(first.code, 0, `session 1 must exit 0; got ${first.code}; stderr:\n${first.stderr}`);

    // Session 2, same state dir: Up arrow, then ctrl-c twice to leave.
    const second = await runChatTty(`${UP_ARROW}\x03\x03`, { cwd: dir, env, shimPath });
    assert.ok(
      second.stdout.includes('remember me'),
      `after a restart with the same state dir, Up arrow must restore the previous session's line "remember me"; session 2 stdout:\n${second.stdout}`,
    );
  } finally {
    safeRm(dir);
    safeRm(stateDir);
    safeRm(shimDir);
  }
});

// ---------------------------------------------------------------------------------------------
// GUARD: paste off is still emitted on exit (already implemented; must stay green)
// ---------------------------------------------------------------------------------------------

test('GUARD_bracketed_paste_off_is_emitted_on_exit', async () => {
  const dir = freshTempDir();
  const { shimDir, shimPath } = writeTtyShim();
  try {
    const r = await runChatTty('\x04', { cwd: dir, shimPath });
    assert.equal(r.code, 0, `ctrl-d on an empty line must exit 0; got ${r.code}; stderr:\n${r.stderr}`);
    assert.ok(r.stdout.includes(PASTE_ON), 'paste on (ESC[?2004h) must be written at start');
    assert.equal(
      countOccurrences(r.stdout, PASTE_OFF),
      1,
      `paste off (ESC[?2004l) must be written exactly once on exit; got ${countOccurrences(r.stdout, PASTE_OFF)}`,
    );
  } finally {
    safeRm(dir);
    safeRm(shimDir);
  }
});

// ---------------------------------------------------------------------------------------------
// round2: S5 review defects (red at HEAD, one test per item)
// ---------------------------------------------------------------------------------------------

test('round2: TAB stop is screen-column aware, not buffer-index aware', async () => {
  const { applyKey, createInputState } = await import(pathToFileURL(inputPath).href);
  const { renderFrame } = await import(pathToFileURL(join(repoRoot, 'chat', 'render.mjs')).href);
  const frameOpts = { columns: 80, rows: 24, color: false, unicode: false };
  // The rendered input line is the last frame line. Its '_' marker sits at the cursor, so its
  // index is the cursor's screen column. The prompt prefix is measured from renderFrame output
  // on an empty line, so the test never hard-codes INPUT_PREFIX (2 columns: '> ').
  const screenColumn = (state) => {
    const frame = renderFrame(
      { transcript: [], status: {}, inputBuffer: state.inputBuffer, cursor: state.cursor },
      frameOpts,
    );
    const lines = frame.split('\n');
    return lines[lines.length - 1].indexOf('_');
  };
  const promptWidth = screenColumn(createInputState());
  assert.equal(promptWidth, 2, `the empty input line must place the cursor after the 2-column prompt; got ${promptWidth}`);

  let state = createInputState();
  for (const ch of 'ab') {
    state = applyKey(state, { name: ch, ctrl: false, meta: false, sequence: ch }).state;
  }
  state = applyKey(state, { name: 'tab', ctrl: false, meta: false, sequence: '\t' }).state;

  const column = screenColumn(state);
  assert.equal(
    column,
    promptWidth + state.cursor,
    `the rendered cursor column must equal prompt width + buffer cursor; got column ${column}, cursor ${state.cursor}`,
  );
  assert.equal(
    (promptWidth + state.cursor) % 8,
    0,
    `after "ab" then TAB the rendered cursor column must sit on an 8-column tab stop: (INPUT_PREFIX.length + cursor) % 8 must be 0; got column ${column} (prefix ${promptWidth} + cursor ${state.cursor}) so % 8 is ${(promptWidth + state.cursor) % 8}; the TAB expansion uses the buffer index, not the screen column`,
  );
});

test('round2: pipeSource header does not say pipeSource buffers the reply', () => {
  const headerLines = readFileSync(join(repoRoot, 'chat', 'session.mjs'), 'utf8').split('\n').slice(0, 12);
  for (const line of headerLines) {
    assert.doesNotMatch(
      line,
      /pipeSource[^\n]*buffer/i,
      `chat/session.mjs header line must not say pipeSource buffers (pipeSource streams now); offending line: ${line}`,
    );
  }
  // The header is prose wrapped across lines, so the same claim can span a line break.
  // Check the joined header as well so a wrapped "buffers" sentence still fails.
  const joined = headerLines.join(' ').replace(/\s+/g, ' ');
  assert.doesNotMatch(
    joined,
    /pipeSource[^.]*buffer/i,
    `chat/session.mjs header must not say pipeSource buffers the reply (the claim is wrapped across lines); header text: ${joined}`,
  );
});

test('round2: stdio/CLAUDE.md does not call the TTY seam reserved for the future chat', () => {
  const line3 = readFileSync(join(repoRoot, 'stdio', 'CLAUDE.md'), 'utf8').split('\n')[2];
  assert.doesNotMatch(
    line3,
    /reserved for the future interactive chat/i,
    `stdio/CLAUDE.md line 3 must not call the TTY seam reserved for the future interactive chat (the chat exists); line 3: ${line3}`,
  );
});

test('round2: chat/CLAUDE.md Keys section names the TAB key', () => {
  const doc = readFileSync(join(repoRoot, 'chat', 'CLAUDE.md'), 'utf8').split('\n');
  const start = doc.findIndex((l) => l.startsWith('## Keys'));
  assert.ok(start !== -1, 'chat/CLAUDE.md must have a "## Keys" section');
  const end = doc.findIndex((l, i) => i > start && l.startsWith('## '));
  const keys = doc.slice(start, end === -1 ? doc.length : end).join('\n');
  assert.match(
    keys,
    /\bTAB\b/,
    `chat/CLAUDE.md Keys section must name the TAB key (tab-stop expansion); Keys section:\n${keys}`,
  );
});

test('round2: chat/CLAUDE.md How-to-test section names tests/chat-visual.test.mjs', () => {
  const doc = readFileSync(join(repoRoot, 'chat', 'CLAUDE.md'), 'utf8').split('\n');
  const start = doc.findIndex((l) => l.startsWith('## How to test'));
  assert.ok(start !== -1, 'chat/CLAUDE.md must have a "## How to test" section');
  const section = doc.slice(start).join('\n');
  assert.ok(
    section.includes('tests/chat-visual.test.mjs'),
    `chat/CLAUDE.md How-to-test section must name tests/chat-visual.test.mjs; section:\n${section}`,
  );
});

// ---------------------------------------------------------------------------------------------
// GUARD f-5709408c: a throwing setRawMode(false) in the restore finally must not change the exit
// ---------------------------------------------------------------------------------------------

test('GUARD f-5709408c: setRawMode(false) throwing in finally does not change the exit path', async () => {
  const dir = freshTempDir();
  const shimDir = freshTempDir('aesop-chat-visual-tty-');
  const shim = join(shimDir, 'tty-shim-throw-off.cjs');
  writeFileSync(shim, [
    'process.stdin.isTTY = true;',
    'process.stdout.isTTY = true;',
    'process.stdin.setRawMode = (on) => {',
    "  if (on === false) throw new Error('setRawMode(false) boom');",
    '};',
  ].join('\n'));
  try {
    const r = await runChatTty('\x04', { cwd: dir, shimPath: shim.replace(/\\/g, '/') });
    assert.equal(r.code, 0, `ctrl-d must still exit 0 when setRawMode(false) throws; got ${r.code}; stderr:\n${r.stderr}`);
    assert.doesNotMatch(
      r.stderr,
      /^\s*at /m,
      `a swallowed setRawMode(false) throw must not leak a stack frame to stderr; stderr:\n${r.stderr}`,
    );
  } finally {
    safeRm(dir);
    safeRm(shimDir);
  }
});
