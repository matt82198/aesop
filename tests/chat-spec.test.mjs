import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { EventEmitter } from 'node:events';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Durable spec tests for chat/SPEC.md. One test per requirement, named "R<n>: ...". Do not rename or
// remove them: tools/chat_spec_drift.mjs reports removed or renamed R-tests as weakened.
// Run: node --import ./tests/helpers/isolated-env.mjs --test --test-force-exit tests/chat-spec.test.mjs

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const load = (p) => import(pathToFileURL(join(root, p)).href);
const PASTE_ON = '\x1b[?2004h';
const PASTE_OFF = '\x1b[?2004l';
const strip = (s) => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function* echo(line) {
  yield String(line);
}

function tmpHist(initial) {
  const dir = mkdtempSync(join(tmpdir(), 'aesop-chat-spec-'));
  const file = join(dir, 'h.json');
  if (initial !== undefined) writeFileSync(file, typeof initial === 'string' ? initial : JSON.stringify(initial));
  const read = () => JSON.parse(readFileSync(file, 'utf8'));
  const cleanup = () => {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      // Windows can hold handles briefly.
    }
  };
  return { file, read, cleanup };
}

// Starts runChat in-process on TTY-shaped fakes. Returns an api for keys, resize and quit.
async function startChat({ file, source = echo, columns = 80, rows = 24 } = {}) {
  const { runChat } = await load('chat/cli.mjs');
  const stdin = Object.assign(new EventEmitter(), { isTTY: true, setRawMode() {}, resume() {}, pause() {} });
  const writes = [];
  const stdout = Object.assign(new EventEmitter(), {
    isTTY: true,
    columns,
    rows,
    write(s) {
      writes.push(String(s));
      return true;
    },
  });
  const env = { NO_COLOR: '1', LANG: 'en_US.UTF-8', ...(file ? { AESOP_CHAT_HISTORY: file } : {}) };
  let exited = false;
  const done = runChat({ argv: [], stdin, stdout, stderr: { write: () => true }, env, source }).then((c) => {
    exited = true;
    return c;
  });
  const press = (str, key) => stdin.emit('keypress', str, key);
  const k = (name, extra = {}) => press(extra.sequence ?? '', { name, ctrl: false, meta: false, sequence: '', ...extra });
  const api = {
    writes,
    get exited() {
      return exited;
    },
    type: (s) => {
      for (const c of s) press(c, undefined);
    },
    enter: () => k('return', { sequence: '\r' }),
    ctrlC: () => k('c', { ctrl: true, sequence: '\x03' }),
    esc: () => k('escape', { sequence: '\x1b' }),
    resize: (c, r) => {
      stdout.columns = c;
      stdout.rows = r;
      stdout.emit('resize');
    },
    submit: async (s) => {
      api.type(s);
      api.enter();
      await sleep(40);
    },
    quit: async () => {
      api.ctrlC();
      api.ctrlC();
      return Promise.race([done, sleep(3000).then(() => { throw new Error('runChat did not exit'); })]);
    },
    done,
  };
  await sleep(10);
  return api;
}

// Display width: CJK and fullwidth characters take two columns.
function displayWidth(s) {
  let w = 0;
  for (const ch of s) {
    const c = ch.codePointAt(0);
    const wide = (c >= 0x1100 && c <= 0x115f) || (c >= 0x2e80 && c <= 0xa4cf) || (c >= 0xac00 && c <= 0xd7a3)
      || (c >= 0xf900 && c <= 0xfaff) || (c >= 0xfe30 && c <= 0xfe6f) || (c >= 0xff00 && c <= 0xff60);
    w += wide ? 2 : 1;
  }
  return w;
}

const sysLines = (n) => Array.from({ length: n }, (_, i) => ({ role: 'system', text: `line ${i}` }));
const key = (name, extra = {}) => ({ name, ctrl: false, meta: false, sequence: '', ...extra });

test('R1: a ctrl-c quit hint disappears on the next non-ctrl-c key', async () => {
  const chat = await startChat();
  chat.ctrlC();
  chat.type('a');
  const last = chat.writes[chat.writes.length - 1];
  await chat.quit();
  assert.ok(!last.includes('press ctrl-c again'), 'latest frame must not still show the quit hint');
});

test('R2: Esc after one ctrl-c cancels the pending quit', async () => {
  const chat = await startChat();
  chat.ctrlC();
  chat.esc();
  chat.ctrlC();
  await sleep(30);
  assert.equal(chat.exited, false, 'ctrl-c, Esc, ctrl-c must not exit');
  assert.equal(await chat.quit(), 0);
});

test('R3: the input reducer reads no clock', async () => {
  const { applyKey, createInputState } = await load('chat/input.mjs');
  const real = Date.now;
  Date.now = () => {
    throw new Error('clock read in reducer');
  };
  try {
    applyKey(createInputState(), key('a', { sequence: 'a' }));
    applyKey({ ...createInputState(), pasting: true }, key('a', { sequence: 'a' }));
  } finally {
    Date.now = real;
  }
});

test('R4: two instances sharing one history file do not lose each other\'s lines', async () => {
  const h = tmpHist([]);
  try {
    const a = await startChat({ file: h.file });
    const b = await startChat({ file: h.file });
    await a.submit('a');
    await b.submit('b');
    await a.quit();
    await b.quit();
    const got = h.read();
    assert.ok(got.includes('a') && got.includes('b'), `history must hold both lines, got ${JSON.stringify(got)}`);
  } finally {
    h.cleanup();
  }
});

test('R5: submitting the same line twice in a row stores one history entry', async () => {
  const h = tmpHist([]);
  try {
    const c = await startChat({ file: h.file });
    await c.submit('hi');
    await c.submit('hi');
    await c.quit();
    assert.deepEqual(h.read(), ['hi']);
  } finally {
    h.cleanup();
  }
});

test('R6: a backslash-continued entry persists as one history entry', async () => {
  const h = tmpHist([]);
  try {
    const c = await startChat({ file: h.file });
    await c.submit('a\\');
    await c.submit('b');
    await c.quit();
    assert.deepEqual(h.read(), ['a\nb']);
  } finally {
    h.cleanup();
  }
});

test('R7: history is capped at 500 entries, newest kept', async () => {
  const h = tmpHist(Array.from({ length: 600 }, (_, i) => `old${i}`));
  try {
    const c = await startChat({ file: h.file });
    await c.submit('new');
    await c.quit();
    const got = h.read();
    assert.equal(got.length, 500);
    assert.equal(got[got.length - 1], 'new');
  } finally {
    h.cleanup();
  }
});

test('R8: after a resize every frame line fits the new width', async () => {
  const c = await startChat({ columns: 80 });
  c.type('x'.repeat(70));
  c.resize(30, 24);
  const frame = strip(c.writes[c.writes.length - 1]).replace(/^\r/, '');
  await c.quit();
  for (const line of frame.split('\n')) assert.ok(displayWidth(line) <= 30, `line too wide after resize: ${JSON.stringify(line)}`);
});

test('R9: a resize repaint moves up over the physical rows the old frame occupies after reflow', async () => {
  const c = await startChat({ columns: 80 });
  c.type('x'.repeat(70));
  const prev = strip(c.writes[c.writes.length - 1]).replace(/^\r/, '').split('\n');
  c.resize(30, 24);
  const raw = c.writes[c.writes.length - 1];
  await c.quit();
  const m = /^\r(?:\x1b\[(\d+)A)?\x1b\[J/.exec(raw);
  assert.ok(m, 'repaint must start with CR, optional cursor-up, then clear-to-end');
  const up = m[1] ? Number(m[1]) : 0;
  const rowsNow = prev.reduce((n, l) => n + Math.max(1, Math.ceil(displayWidth(l) / 30)), 0);
  assert.equal(up, rowsNow - 1, 'cursor-up must cover the reflowed rows of the previous frame');
});

test('R10: a frame never exceeds rows and the input line is last', async () => {
  const { renderFrame } = await load('chat/render.mjs');
  const lines = renderFrame({ transcript: sysLines(40), inputBuffer: 'hi', cursor: 2, status: {} }, { columns: 40, rows: 10 }).split('\n');
  assert.ok(lines.length <= 10, `frame has ${lines.length} lines`);
  assert.ok(lines[lines.length - 1].startsWith('> '), 'input line must be last');
});

test('R11: the first frame tells the user how to get help', async () => {
  const c = await startChat();
  const first = strip(c.writes.find((w) => w.includes('chat')) ?? '');
  await c.quit();
  assert.ok(first.includes('/help'), 'first frame must mention /help');
});

test('R12: /help documents the keys', async () => {
  const c = await startChat();
  await c.submit('/help');
  const out = strip(c.writes.join(''));
  await c.quit();
  for (const word of ['ctrl-u', 'ctrl-d', 'history']) assert.ok(out.includes(word), `/help output must mention ${word}`);
});

test('R13: an unknown slash command shows an error and never reaches the backend', async () => {
  const calls = [];
  const c = await startChat({ source: (l) => { calls.push(l); return echo(l); } });
  await c.submit('/bogus');
  const out = strip(c.writes.join(''));
  await c.quit();
  assert.ok(out.includes('unknown command: /bogus'));
  assert.deepEqual(calls, []);
});

test('R14: TAB on a lone slash prefix completes the command', async () => {
  const { applyKey, createInputState } = await load('chat/input.mjs');
  const tab = key('tab', { sequence: '\t' });
  assert.equal(applyKey({ ...createInputState(), inputBuffer: '/he', cursor: 3 }, tab).state.inputBuffer, '/help');
  assert.equal(applyKey({ ...createInputState(), inputBuffer: '/q', cursor: 2 }, tab).state.inputBuffer, '/quit');
});

test('R15: wide characters are measured as two columns when wrapping', async () => {
  const { renderFrame } = await load('chat/render.mjs');
  const text = '日本語'.repeat(4);
  const frame = renderFrame({ transcript: [{ role: 'user', text }], status: {} }, { columns: 20, rows: 30 });
  for (const line of frame.split('\n')) assert.ok(displayWidth(line) <= 20, `line is ${displayWidth(line)} columns wide: ${line}`);
});

test('R16: a mid-stream backend error is shown and the session keeps working', async () => {
  let n = 0;
  const source = (l) => {
    n += 1;
    if (n > 1) return echo(l);
    return (async function* () {
      yield 'one';
      throw new Error('boom');
    })();
  };
  const c = await startChat({ source });
  await c.submit('first');
  await c.submit('second');
  const out = strip(c.writes.join(''));
  await c.quit();
  assert.ok(out.includes('stream error: boom'));
  assert.equal(n, 2, 'a second turn must still reach the source');
});

test('R17: error lines are red when color is on', async () => {
  const { renderFrame } = await load('chat/render.mjs');
  for (const text of ['stream error: boom', 'error: bad']) {
    const frame = renderFrame({ transcript: [{ role: 'system', text }], status: {} }, { columns: 60, rows: 20, color: true });
    const line = frame.split('\n').find((l) => l.includes(text));
    assert.ok(line && line.includes('\x1b[31m'), `${text} must be drawn red`);
  }
});

test('R18: truncated transcript says how many lines are hidden', async () => {
  const { renderFrame } = await load('chat/render.mjs');
  const lines = renderFrame({ transcript: sysLines(40), status: {} }, { columns: 40, rows: 10 }).split('\n');
  assert.ok(lines.length <= 10);
  assert.ok(lines.some((l) => /\(\d+ earlier lines? hidden\)/.test(l)), 'frame must show an (N earlier lines hidden) marker');
});

test('R19: a bare Windows console gets ASCII, Windows Terminal gets unicode', async () => {
  const { resolveUnicode, renderFrame } = await load('chat/render.mjs');
  const real = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32' });
  try {
    assert.equal(resolveUnicode({ env: {} }), false);
    assert.equal(resolveUnicode({ env: { WT_SESSION: 'x' } }), true);
  } finally {
    Object.defineProperty(process, 'platform', real);
  }
  assert.ok(!/[─-╿]/.test(renderFrame({ transcript: [{ role: 'tool', text: 't' }], status: {} }, { columns: 30, rows: 12, unicode: false })));
});

test('R20: a CRLF paste produces one newline per line break', async () => {
  const { applyKey, createInputState } = await load('chat/input.mjs');
  let s = createInputState();
  for (const k of [key('paste-start'), key('a', { sequence: 'a' }), key('return', { sequence: '\r' }), key('enter', { sequence: '\n' }), key('b', { sequence: 'b' }), key('paste-end')]) {
    s = applyKey(s, k).state;
  }
  assert.equal(s.inputBuffer, 'a\nb');
});

test('R21: ctrl-w deletes the word before the cursor', async () => {
  const { applyKey, createInputState } = await load('chat/input.mjs');
  const r = applyKey({ ...createInputState(), inputBuffer: 'foo bar', cursor: 7 }, key('w', { ctrl: true, sequence: '\x17' }));
  assert.equal(r.state.inputBuffer, 'foo ');
  assert.equal(r.state.cursor, 4);
});

test('R22: ctrl-k deletes from the cursor to the end of the line', async () => {
  const { applyKey, createInputState } = await load('chat/input.mjs');
  const r = applyKey({ ...createInputState(), inputBuffer: 'foobar', cursor: 3 }, key('k', { ctrl: true, sequence: '\x0b' }));
  assert.equal(r.state.inputBuffer, 'foo');
  assert.equal(r.state.cursor, 3);
});

test('R23: a corrupt or non-array history file starts empty and the session works', async () => {
  const h = tmpHist('{"x":1}');
  try {
    const c = await startChat({ file: h.file });
    await c.submit('hi');
    assert.equal(await c.quit(), 0);
    assert.deepEqual(h.read(), ['hi']);
  } finally {
    h.cleanup();
  }
});

test('R24: exit restores the terminal exactly once', async () => {
  const c = await startChat();
  const code = await c.quit();
  const all = c.writes.join('');
  assert.equal(code, 0);
  assert.equal(all.split(PASTE_ON).length - 1, 1);
  assert.equal(all.split(PASTE_OFF).length - 1, 1);
});
