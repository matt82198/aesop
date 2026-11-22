import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

// Peer bus (v3): /codex, /codex-app, /agy, /peers in the TUI and `aesop post` from peers.
// Every sender is stubbed through an env-overridable command path (AESOP_PEER_*). No test sends
// to a real peer: the stub records its argv and exits with STUB_EXIT.
// Run: node --import ./tests/helpers/isolated-env.mjs --test --test-force-exit tests/chat-peer-bus.test.mjs

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const cliPath = join(root, 'bin', 'cli.js');
const load = (rel) => import(pathToFileURL(join(root, rel)).href);
const ANSI = /\x1b\[[0-9;?]*[A-Za-z]/g;
const stripAnsi = (s) => s.replace(ANSI, '');
const dirs = [];
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'aesop-peer-bus-')); dirs.push(d); return d; };
process.on('exit', () => { for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* best effort */ } } });

// Stub sender: records argv, the last argv entry's file content when it is a path, and PYTHONIOENCODING.
// It prints a line that must NEVER reach the feed or the frame (peer output is never rendered).
const stub = (() => {
  const f = join(tmp(), 'stub-sender.mjs');
  writeFileSync(f, [
    "import { appendFileSync, existsSync, readFileSync } from 'node:fs';",
    'const argv = process.argv.slice(2);',
    'const last = argv[argv.length - 1];',
    "const file = last !== undefined && existsSync(last) ? readFileSync(last, 'utf8') : null;",
    "appendFileSync(process.env.STUB_RECORD, JSON.stringify({ argv, file, pyio: process.env.PYTHONIOENCODING ?? null }) + '\\n');",
    "process.stdout.write(process.env.STUB_SENT_LINE ?? 'SENT stub-id\\n');",
    "process.stdout.write('SECRET PEER OUTPUT LINE\\n');",
    "process.exit(Number(process.env.STUB_EXIT ?? '0'));",
    '',
  ].join('\n'));
  return f;
})();

function peerEnv(over = {}) {
  const d = tmp();
  return {
    ...process.env,
    AESOP_ACTIVITY_FEED: join(d, 'activity.jsonl'),
    AESOP_PEERS: join(d, 'peers.json'),
    AESOP_PEER_PYTHON: process.execPath,
    AESOP_PEER_CODEX_SEND: stub,
    AESOP_PEER_CONSOLE_SEND: stub,
    AESOP_PEER_LAUNCH_TUI: stub,
    STUB_RECORD: join(d, 'record.jsonl'),
    STUB_EXIT: '0',
    ...over,
  };
}

function handoffCfg(env, over = {}) {
  const dir = join(dirname(env.AESOP_PEERS), 'handoffs');
  mkdirSync(dir, { recursive: true });
  const cfg = { codex_app_thread: 'thread-123', agy_pid: 77648, codex_handoff_dir: dir, ...over };
  writeFileSync(env.AESOP_PEERS, JSON.stringify(cfg));
  return cfg;
}

const records = (env) => (existsSync(env.STUB_RECORD)
  ? readFileSync(env.STUB_RECORD, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l))
  : []);
const feedEvents = (env) => (existsSync(env.AESOP_ACTIVITY_FEED)
  ? readFileSync(env.AESOP_ACTIVITY_FEED, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l))
  : []);

function makeState(over = {}) {
  return {
    transcript: [],
    inputBuffer: '',
    cursor: 0,
    status: { model: 'claude-haiku-5-5', provider: 'anthropic', cwd: 'C:/Users/Mattt/aesop', tokens: 0, elapsedMs: 0, spinnerTick: 0 },
    mode: 'idle',
    ...over,
  };
}

// Applies feed events to a transcript exactly as the TUI does, then renders the frame.
async function frameFor(events) {
  const { applyFeedEvent } = await load('chat/activity_feed.mjs');
  const { renderFrame } = await load('chat/render.mjs');
  const jobs = new Map();
  const transcript = [];
  for (const ev of events) applyFeedEvent(jobs, transcript, ev);
  return stripAnsi(renderFrame({ ...makeState(), transcript }, { columns: 100, rows: 400, color: false }));
}

const startEv = (over) => ({ job: 'j1', type: 'start', kind: 'peer', to: 'codex', from: 'matt', dir: 'out', ts: Date.now(), model: '', prompt: '', stdin_preview: '', stdin_bytes: 0, ...over });
const endEv = (over) => ({ job: 'j1', type: 'end', kind: 'peer', to: 'codex', status: 'ok', exit: 0, elapsed_ms: 1000, ...over });

test('peers config is created on first use with the current values', async () => {
  const { loadPeersConfig } = await load('chat/peer_bus.mjs');
  const env = peerEnv();
  const cfg = loadPeersConfig(env);
  assert.equal(cfg.codex_app_thread, '01a200cd-7eb3-7281-8bcd-47b29dbe4f39');
  assert.equal(cfg.agy_pid, 77648);
  assert.equal(cfg.codex_handoff_dir, 'C:/Users/Mattt/Desktop/aesop-tui-handoff/codex-handoffs');
  assert.deepEqual(JSON.parse(readFileSync(env.AESOP_PEERS, 'utf8')), cfg);
});

test('a bad peers file is a visible error that names the file', async () => {
  const { loadPeersConfig } = await load('chat/peer_bus.mjs');
  const env = peerEnv();
  writeFileSync(env.AESOP_PEERS, '{"agy_pid": "not a number"');
  assert.throws(() => loadPeersConfig(env), (err) => String(err.message).includes(env.AESOP_PEERS));
});

test('/codex-app sends to the explicit thread through the codex_send sender and records a peer feed pair', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  const env = peerEnv();
  handoffCfg(env);
  const res = await runPeerCommand({ command: 'codex-app', arg: 'ship it', env, notify: () => {} });
  assert.equal(res.status, 'ok');
  assert.equal(res.exit, 0);
  assert.equal(res.sent, 'stub-id');
  const [rec] = records(env);
  assert.deepEqual(rec.argv, ['--thread', 'thread-123', '--message', 'ship it']);
  const ev = feedEvents(env);
  assert.equal(ev[0].type, 'start');
  assert.equal(ev[0].kind, 'peer');
  assert.equal(ev[0].to, 'codex-app');
  assert.equal(ev[0].prompt, 'ship it');
  assert.equal(ev[1].type, 'end');
  assert.equal(ev[1].kind, 'peer');
  assert.equal(ev[1].exit, 0);
  assert.equal(ev[1].sent, 'stub-id');
});

test('/codex-app never runs without an explicit thread', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  const env = peerEnv();
  handoffCfg(env, { codex_app_thread: '' });
  const notes = [];
  const res = await runPeerCommand({ command: 'codex-app', arg: 'ship it', env, notify: (t) => notes.push(t) });
  assert.equal(res, null);
  assert.equal(records(env).length, 0, 'the sender must not run');
  assert.ok(notes.some((t) => t.includes('codex_app_thread')), `refusal must name the key: ${notes}`);
});

test('/agy sends the message file to the configured pid with UTF-8 stdio', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  const env = peerEnv();
  handoffCfg(env);
  const res = await runPeerCommand({ command: 'agy', arg: 'hello agy', env, notify: () => {} });
  assert.equal(res.status, 'ok');
  const [rec] = records(env);
  assert.equal(rec.argv[0], 'send');
  assert.equal(rec.argv[1], '77648');
  assert.equal(rec.file.trim(), 'hello agy');
  assert.equal(rec.pyio, 'utf-8');
});

test('/agy refuses non-ASCII and multi-line input visibly and sends nothing', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  for (const bad of ['caf\u00e9', 'line one\nline two', 'tab\there is fine but \u2603 is not']) {
    const env = peerEnv();
    handoffCfg(env);
    const notes = [];
    const res = await runPeerCommand({ command: 'agy', arg: bad, env, notify: (t) => notes.push(t) });
    assert.equal(res, null, `refused: ${JSON.stringify(bad)}`);
    assert.equal(records(env).length, 0);
    assert.ok(notes.some((t) => t.startsWith('refused')), `visible refusal for ${JSON.stringify(bad)}`);
    assert.equal(feedEvents(env).length, 0, 'a refused message writes no feed event');
  }
});

test('/codex writes the task text as the handoff, a two-line run script, and opens a launch window', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  const env = peerEnv();
  const cfg = handoffCfg(env);
  const dir = cfg.codex_handoff_dir;
  // Handoff files must exist synchronously, before the first await, so the job id is known at call time.
  const pending = runPeerCommand({ command: 'codex', arg: 'fix the flake\nsecond line', env, notify: () => {} });
  const mds = readdirSync(dir).filter((f) => f.endsWith('.md'));
  assert.equal(mds.length, 1);
  const job = mds[0].slice(0, -3);
  assert.equal(readFileSync(join(dir, mds[0]), 'utf8'), 'fix the flake\nsecond line');
  const run = join(dir, `run-${job}.sh`);
  assert.equal(readFileSync(run, 'utf8').trim().split('\n').length, 2);
  // The launch window finishes; codex's own exit arrives later as <job>.exit (written here as launch.sh would).
  await new Promise((r) => setTimeout(r, 300));
  const [rec] = records(env);
  assert.deepEqual(rec.argv, ['--script', run, '--title', 'codex: fix the flake']);
  writeFileSync(join(dir, `${job}.exit`), '3\n');
  const res = await pending;
  assert.equal(res.status, 'failed');
  assert.equal(res.exit, 3);
  const ends = feedEvents(env).filter((e) => e.type === 'end');
  assert.equal(ends.length, 1);
  assert.equal(ends[0].to, 'codex');
  assert.equal(ends[0].exit, 3);
});

test('/codex END fires from the .exit file and a silent codex ends as FAILED after the timeout', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  const env = peerEnv({ AESOP_PEER_CODEX_TIMEOUT_MS: '150' });
  handoffCfg(env);
  const res = await runPeerCommand({ command: 'codex', arg: 'never finishes', env, notify: () => {} });
  assert.equal(res.status, 'failed');
  assert.equal(res.reason, 'timeout');
});

test('a peer START renders the header and the full multi-line body', async () => {
  const body = ['first line', 'second line', 'third line'].join('\n');
  const frame = await frameFor([startEv({ prompt: body, to: 'codex-app' })]);
  assert.match(frame, /-> codex-app {2}j1 {2}running \d+s/);
  for (const l of ['first line', 'second line', 'third line']) assert.ok(frame.includes(l), `body line ${l}`);
});

test('a body over 40 lines shows the first 40 then (+N lines)', async () => {
  const body = Array.from({ length: 45 }, (_, i) => `line ${i + 1}`).join('\n');
  const frame = await frameFor([startEv({ prompt: body })]);
  assert.ok(frame.includes('line 40'));
  assert.ok(!frame.includes('line 41'), 'line 41 must not be shown');
  assert.ok(frame.includes('(+5 lines)'));
});

test('an END renders the exit code and the elapsed time; a failure renders FAILED', async () => {
  const ok = await frameFor([startEv({}), endEv({ elapsed_ms: 243000, exit: 0 })]);
  assert.ok(ok.includes('<- codex  j1  ok (exit 0, 4m03s)'), ok);
  const bad = await frameFor([startEv({}), endEv({ status: 'failed', exit: 2, elapsed_ms: 12000 })]);
  assert.ok(bad.includes('<- codex  j1  FAILED (exit 2, 12s)'), bad);
});

test('the Codex app END carries the SENT id', async () => {
  const frame = await frameFor([startEv({ to: 'codex-app' }), endEv({ to: 'codex-app', elapsed_ms: 1000, sent: 'abc-123' })]);
  assert.ok(frame.includes('<- codex-app  j1  ok (exit 0, 1s)  SENT abc-123'), frame);
});

test('no peer output is ever written to the feed or rendered', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  const env = peerEnv();
  handoffCfg(env);
  await runPeerCommand({ command: 'codex-app', arg: 'quiet please', env, notify: () => {} });
  assert.ok(!readFileSync(env.AESOP_ACTIVITY_FEED, 'utf8').includes('SECRET PEER OUTPUT'));
  const frame = await frameFor(feedEvents(env));
  assert.ok(!frame.includes('SECRET PEER OUTPUT'), frame);
  assert.ok(frame.includes('quiet please'));
});

test('aesop post writes one peer event that renders with its body and exit 0', async () => {
  const { postPeerMessage } = await load('chat/peer_bus.mjs');
  const env = peerEnv();
  const job = postPeerMessage({ from: 'codex', text: 'done: fixed the flake', env });
  const ev = feedEvents(env);
  assert.equal(ev.length, 1);
  assert.equal(ev[0].kind, 'peer');
  assert.equal(ev[0].from, 'codex');
  assert.equal(ev[0].job, job);
  const frame = await frameFor(ev);
  assert.ok(frame.includes(`<- codex  ${job}  ok (exit 0, 0s)`), frame);
  assert.ok(frame.includes('done: fixed the flake'));
});

test('aesop post via bin/cli.js writes the event and rejects a missing --from', () => {
  const env = peerEnv();
  const ok = spawnSync(process.execPath, [cliPath, 'post', '--from', 'probe', 'bus ok'], { env, encoding: 'utf8' });
  assert.equal(ok.status, 0, ok.stderr);
  const ev = feedEvents(env);
  assert.equal(ev.length, 1);
  assert.equal(ev[0].prompt, 'bus ok');
  assert.equal(ev[0].from, 'probe');
  const bad = spawnSync(process.execPath, [cliPath, 'post', 'no from'], { env, encoding: 'utf8' });
  assert.equal(bad.status, 2);
});

test('/peers reports the config and the last status per peer', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  const env = peerEnv();
  handoffCfg(env);
  await runPeerCommand({ command: 'codex-app', arg: 'status probe', env, notify: () => {} });
  const lines = [];
  await runPeerCommand({ command: 'peers', env, notify: (t) => lines.push(t) });
  const text = lines.join('\n');
  assert.ok(text.includes('codex_app_thread: thread-123'), text);
  assert.ok(text.includes('agy_pid: 77648'), text);
  assert.match(text, /codex-app: last \S+ ok \(exit 0/);
  assert.match(text, /agy: no jobs recorded/);
});

test('the four peer slash commands parse through parseSlash', async () => {
  const { parseSlash, SLASH_COMMANDS } = await load('chat/input.mjs');
  assert.deepEqual(parseSlash('/codex fix the bug'), { command: 'codex', arg: 'fix the bug' });
  assert.deepEqual(parseSlash('/codex-app ship it'), { command: 'codex-app', arg: 'ship it' });
  assert.deepEqual(parseSlash('/agy hi'), { command: 'agy', arg: 'hi' });
  assert.deepEqual(parseSlash('/peers'), { command: 'peers' });
  for (const c of ['codex', 'codex-app', 'agy', 'peers']) assert.ok(SLASH_COMMANDS.includes(c), c);
});
