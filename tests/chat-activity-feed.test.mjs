import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { appendFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { sse, startFake } from './helpers/fake_openai_server.mjs';

// Activity feed: `aesop ask` appends JSONL events, `aesop chat` tails and renders them inline.
// Run: node --test tests/chat-activity-feed.test.mjs

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const cliPath = join(root, 'bin', 'cli.js');
const dirs = [];
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'aesop-feed-')); dirs.push(d); return d; };
process.on('exit', () => { for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* best effort */ } } });
const strip = (s) => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');

function config(url) {
  const f = join(tmp(), 'aesop.config.json');
  writeFileSync(f, JSON.stringify({ providers: { fake: { type: 'openai', base_url: url, models: 'any', is_local: true, timeout_ms: 5000 } }, default: 'fake/m1' }));
  return f;
}

function ask(args, { cfg, feed, stdin = null, env = {} }) {
  return new Promise((resolve) => {
    const home = tmp();
    const child = spawn(process.execPath, [cliPath, 'ask', ...args], {
      cwd: tmp(),
      env: { ...process.env, HOME: home, USERPROFILE: home, AESOP_CHAT_CONFIG: cfg, AESOP_ACTIVITY_FEED: feed, ...env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = ''; let stderr = '';
    child.stdout.on('data', (d) => { stdout += d; });
    child.stderr.on('data', (d) => { stderr += d; });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
    child.stdin.end(stdin ?? '');
  });
}

const readEvents = (file) => readFileSync(file, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));

// ---- writer side
test('feed line shapes: start, chunks, end with caller label and stdin preview', async () => {
  const fake = await startFake((req, body, res) => sse(res, ['hel', 'lo']));
  try {
    const feed = join(tmp(), 'chat', 'activity.jsonl');
    const r = await ask(['--from', 'codex', 'say hi'], { cfg: config(fake.url), feed, stdin: 'x'.repeat(5000) });
    assert.equal(r.code, 0);
    assert.equal(r.stdout, 'hello\n');
    const ev = readEvents(feed);
    assert.equal(ev[0].type, 'start');
    assert.equal(ev[0].from, 'codex');
    assert.equal(ev[0].model, 'fake/m1');
    assert.equal(ev[0].prompt, 'say hi');
    assert.equal(ev[0].stdin_bytes, 5000);
    assert.equal(ev[0].stdin_preview.length, 2048);
    const chunks = ev.filter((e) => e.type === 'chunk');
    assert.equal(chunks.map((c) => c.text).join(''), 'hello');
    assert.deepEqual(chunks.map((c) => c.seq), chunks.map((_, i) => i + 1));
    const end = ev[ev.length - 1];
    assert.equal(end.type, 'end');
    assert.equal(end.status, 'ok');
    assert.equal(end.exit, 0);
    assert.equal(typeof end.elapsed_ms, 'number');
    assert.equal(new Set(ev.map((e) => e.job)).size, 1);
  } finally { await fake.close(); }
});

test('caller label falls back to AESOP_CALLER, then agent-ish default', async () => {
  const fake = await startFake();
  try {
    const feed = join(tmp(), 'a.jsonl');
    await ask(['q'], { cfg: config(fake.url), feed, env: { AESOP_CALLER: 'gemini' } });
    assert.equal(readEvents(feed)[0].from, 'gemini');
    const feed2 = join(tmp(), 'b.jsonl');
    await ask(['q'], { cfg: config(fake.url), feed: feed2 });
    assert.ok(readEvents(feed2)[0].from.length > 0);
  } finally { await fake.close(); }
});

test('refused ask records an end event with status refused', async () => {
  const fake = await startFake();
  try {
    const feed = join(tmp(), 'r.jsonl');
    const r = await ask(['--model', 'claude/opus', 'q'], { cfg: config(fake.url), feed });
    assert.equal(r.code, 6);
    const ev = readEvents(feed);
    assert.deepEqual(ev.map((e) => e.type), ['start', 'error', 'end']);
    assert.match(ev[1].error, /refused/i);
    assert.equal(ev[ev.length - 1].status, 'refused');
    assert.equal(ev[ev.length - 1].exit, 6);
  } finally { await fake.close(); }
});

test('two concurrent asks append well-formed, non-interleaved lines', async () => {
  const fake = await startFake(async (req, body, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    for (let i = 0; i < 40; i += 1) {
      res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: `w${i} ${'y'.repeat(300)} ` } }] })}\n\n`);
      await sleep(2);
    }
    res.end('data: [DONE]\n\n');
  });
  try {
    const feed = join(tmp(), 'c.jsonl');
    const cfg = config(fake.url);
    const [a, b] = await Promise.all([ask(['--from', 'A', 'one'], { cfg, feed }), ask(['--from', 'B', 'two'], { cfg, feed })]);
    assert.equal(a.code, 0);
    assert.equal(b.code, 0);
    const lines = readFileSync(feed, 'utf8').split('\n').filter(Boolean);
    const ev = lines.map((l) => JSON.parse(l)); // throws on any torn line
    const jobs = [...new Set(ev.map((e) => e.job))];
    assert.equal(jobs.length, 2);
    for (const j of jobs) {
      const mine = ev.filter((e) => e.job === j);
      assert.equal(mine[0].type, 'start');
      assert.equal(mine[mine.length - 1].type, 'end');
      const seqs = mine.filter((e) => e.type === 'chunk').map((e) => e.seq);
      assert.deepEqual(seqs, seqs.map((_, i) => i + 1));
    }
    assert.ok(lines.every((l) => l.length < 3000), 'lines stay small');
  } finally { await fake.close(); }
});

test('an unwritable feed never fails the ask: exit 0, answer on stdout, warning on stderr', async () => {
  const fake = await startFake((req, body, res) => sse(res, ['ok!']));
  try {
    const blocker = join(tmp(), 'file');
    writeFileSync(blocker, 'x');
    const r = await ask(['q'], { cfg: config(fake.url), feed: join(blocker, 'sub', 'activity.jsonl') });
    assert.equal(r.code, 0);
    assert.equal(r.stdout, 'ok!\n');
    assert.match(r.stderr, /activity feed disabled/);
  } finally { await fake.close(); }
});

test('the feed rolls to activity.1.jsonl at 5 MB', async () => {
  const fake = await startFake();
  try {
    const dir = tmp();
    const feed = join(dir, 'activity.jsonl');
    writeFileSync(feed, `${JSON.stringify({ job: 'old', type: 'start' })}\n${' '.repeat(5 * 1024 * 1024)}\n`);
    await ask(['q'], { cfg: config(fake.url), feed });
    assert.ok(existsSync(join(dir, 'activity.1.jsonl')));
    assert.equal(readEvents(feed)[0].type, 'start');
    assert.notEqual(readEvents(feed)[0].job, 'old');
  } finally { await fake.close(); }
});

// ---- TUI side
async function startChat({ cfg, feed, env = {} }) {
  const { runChat } = await import(pathToFileURL(join(root, 'chat/cli.mjs')).href);
  const dir = tmp();
  const stdin = Object.assign(new EventEmitter(), { isTTY: true, setRawMode() {}, resume() {}, pause() {} });
  const writes = [];
  const stdout = Object.assign(new EventEmitter(), { isTTY: true, columns: 100, rows: 50, write(s) { writes.push(String(s)); return true; } });
  const e = {
    LANG: 'en_US.UTF-8', NO_COLOR: '1', AESOP_CHAT_BACKEND: 'harness', AESOP_CHAT_CONFIG: cfg, AESOP_CHAT_HISTORY: join(dir, 'h.json'),
    AESOP_STATE_ROOT: join(dir, 'state'), AESOP_ACTIVITY_FEED: feed, ...env,
  };
  const done = runChat({ argv: [], stdin, stdout, stderr: { write() {} }, env: e });
  const press = (str, key) => stdin.emit('keypress', str, key);
  const api = {
    writes,
    last: () => strip(writes[writes.length - 1] ?? ''),
    submit: async (s, wait = 200) => { for (const c of s) press(c, undefined); press('', { name: 'return', ctrl: false, meta: false, sequence: '\r' }); await sleep(wait); },
    quit: async () => { for (let i = 0; i < 2; i += 1) press('', { name: 'c', ctrl: true, meta: false, sequence: '\x03' }); await Promise.race([done, sleep(1500)]); },
  };
  await sleep(50);
  return api;
}
const put = (file, o) => appendFileSync(file, `${JSON.stringify(o)}\n`);

test('TUI renders a job from another process, streams chunks, shows status, keeps it out of model context', async () => {
  const fake = await startFake((req, body, res) => sse(res, ['model reply']));
  try {
    const feed = join(tmp(), 'activity.jsonl');
    writeFileSync(feed, `${JSON.stringify({ job: 'pre', type: 'start', from: 'old', model: 'x/y', prompt: 'BACKLOG-PROMPT' })}\n`);
    const chat = await startChat({ cfg: config(fake.url), feed });
    assert.ok(!chat.last().includes('BACKLOG-PROMPT'), 'tailing starts at EOF: no backlog');
    put(feed, { job: 'j1', type: 'start', ts: 1, from: 'codex', model: 'ollama/qwen', prompt: 'write one pytest test', stdin_preview: 'def add(a,b): return a+b\n', stdin_bytes: 25 });
    await sleep(1300);
    let f = chat.last();
    assert.ok(!f.includes('BACKLOG-PROMPT'), 'no backlog after new events either');
    assert.match(f, /codex/);
    assert.match(f, /ollama\/qwen/);
    assert.match(f, /write one pytest test/);
    assert.match(f, /def add\(a,b\): return a\+b/);
    put(feed, { job: 'j1', type: 'chunk', seq: 1, text: 'SECRET-FEED-ANSWER part1 ' });
    await sleep(1300);
    assert.match(chat.last(), /SECRET-FEED-ANSWER part1/);
    assert.ok(!/done/.test(chat.last()));
    put(feed, { job: 'j1', type: 'chunk', seq: 2, text: 'part2' });
    put(feed, { job: 'j1', type: 'end', status: 'ok', exit: 0, elapsed_ms: 1500 });
    await sleep(1300);
    f = chat.last();
    assert.match(f, /part1 part2/);
    assert.match(f, /done 1\.5s/);
    await chat.submit('hello model', 500);
    assert.equal(fake.posts().length, 1);
    const sent = JSON.stringify(fake.posts()[0].body);
    assert.ok(!/SECRET-FEED-ANSWER|pytest|codex|def add/.test(sent), 'feed text never reaches the model');
    await chat.quit();
  } finally { await fake.close(); }
});

test('concurrent jobs update their own blocks', async () => {
  const fake = await startFake();
  try {
    const feed = join(tmp(), 'activity.jsonl');
    const chat = await startChat({ cfg: config(fake.url), feed });
    put(feed, { job: 'a', type: 'start', from: 'codex', model: 'm/a', prompt: 'PA' });
    put(feed, { job: 'b', type: 'start', from: 'gemini', model: 'm/b', prompt: 'PB' });
    put(feed, { job: 'b', type: 'chunk', seq: 1, text: 'ANSWER-B' });
    put(feed, { job: 'a', type: 'chunk', seq: 1, text: 'ANSWER-A' });
    put(feed, { job: 'a', type: 'end', status: 'error', exit: 4, elapsed_ms: 10, error: 'boom' });
    await sleep(1300);
    const lines = chat.last().split('\n');
    const idx = (re) => lines.findIndex((l) => re.test(l));
    assert.ok(idx(/codex/) < idx(/ANSWER-A/) && idx(/ANSWER-A/) < idx(/error: boom/));
    assert.ok(idx(/gemini/) < idx(/ANSWER-B/));
    assert.ok(idx(/ANSWER-A/) < idx(/gemini/), 'blocks stay separate');
    await chat.quit();
  } finally { await fake.close(); }
});

test('/feed off suppresses jobs, /feed on resumes, /feed N replays the last N', async () => {
  const fake = await startFake();
  try {
    const feed = join(tmp(), 'activity.jsonl');
    const chat = await startChat({ cfg: config(fake.url), feed });
    await chat.submit('/feed off');
    put(feed, { job: 'x', type: 'start', from: 'codex', model: 'm/x', prompt: 'HIDDEN-PROMPT' });
    await sleep(1300);
    assert.ok(!chat.last().includes('HIDDEN-PROMPT'));
    await chat.submit('/feed on');
    put(feed, { job: 'y', type: 'start', from: 'codex', model: 'm/y', prompt: 'VISIBLE-PROMPT' });
    await sleep(1300);
    assert.match(chat.last(), /VISIBLE-PROMPT/);
    await chat.submit('/feed 0');
    assert.ok(!chat.last().includes('HIDDEN-PROMPT'));
    await chat.submit('/feed 5');
    assert.match(chat.last(), /HIDDEN-PROMPT/);
    await chat.quit();
  } finally { await fake.close(); }
});

test('the tail is released on exit: events after quit render nothing', async () => {
  const fake = await startFake();
  try {
    const feed = join(tmp(), 'activity.jsonl');
    const chat = await startChat({ cfg: config(fake.url), feed });
    await chat.quit();
    const n = chat.writes.length;
    put(feed, { job: 'z', type: 'start', from: 'codex', model: 'm/z', prompt: 'LATE' });
    await sleep(1300);
    assert.equal(chat.writes.length, n);
  } finally { await fake.close(); }
});
