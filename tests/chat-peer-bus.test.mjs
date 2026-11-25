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

test('the peer slash commands parse through parseSlash and tab-complete /use', async () => {
  const { applyKey, createInputState, parseSlash, SLASH_COMMANDS } = await load('chat/input.mjs');
  assert.deepEqual(parseSlash('/codex fix the bug'), { command: 'codex', arg: 'fix the bug' });
  assert.deepEqual(parseSlash('/codex-app ship it'), { command: 'codex-app', arg: 'ship it' });
  assert.deepEqual(parseSlash('/agy hi'), { command: 'agy', arg: 'hi' });
  assert.deepEqual(parseSlash('/peers'), { command: 'peers' });
  assert.deepEqual(parseSlash('/use agy-headless fix flake'), { command: 'use', arg: 'agy-headless fix flake' });
  for (const c of ['codex', 'codex-app', 'agy', 'peers', 'use']) assert.ok(SLASH_COMMANDS.includes(c), c);
  const completed = applyKey({ ...createInputState(), inputBuffer: '/us', cursor: 3 }, { name: 'tab', ctrl: false, meta: false, sequence: '\t' });
  assert.equal(completed.state.inputBuffer, '/use');
});

function writeRoster(dir, extraRows = []) {
  const file = join(dir, 'worker-roster.md');
  writeFileSync(file, [
    '# Worker roster',
    'direction: Dispatch build lanes to agy headless; codex app runs evals.',
    '| kind | invoke | when |', '|---|---|---|',
    '| codex app headless | `codex app-server --listen stdio://` | ABs, sims, evals. |',
    '| agy headless | `agy -p "<prompt>" --output-format json` | Dispatch work and run tests. |',
    '| agy | `agy -i "<prompt>" --model gemini-4-carbon-high` | Interactive ACR work. |',
    '| codex | `codex --yolo "<prompt>"` | Human-seated design or review. |',
    '| codex cli | `codex exec -s workspace-write -C <wt> "<prompt>"` | Simple tasks. |',
    '| haiku | Agent call with model `haiku`. | Failed work that needs Claude. |',
    ...extraRows,
  ].join('\n') + '\n', 'utf8');
  return file;
}

test('/peers reads worker-roster.md live as the single source of truth and shows peer-bus usage', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  const d = tmp();
  const env = peerEnv({ WORKER_ROSTER_FILE: writeRoster(d, ['| custom probe | `probe --go` | Dynamic custom row. |']) });
  handoffCfg(env);
  const lines = [];
  await runPeerCommand({ command: 'peers', env, notify: (t) => lines.push(t) });
  const text = lines.join('\n');
  for (const needle of ['Dispatch build lanes to agy headless', 'codex-app-headless', 'agy-headless', 'codex-cli', 'haiku', 'custom-probe', 'Dynamic custom row.', 'probe --go', '/use custom-probe <task>', '/codex-app', '/agy', '/codex']) {
    assert.ok(text.includes(needle), `expected /peers output to include ${JSON.stringify(needle)}:\n${text}`);
  }
  writeRoster(d, ['| live reload worker | `reload --now` | Added after start. |']);
  const updated = [];
  await runPeerCommand({ command: 'peers', env, notify: (t) => updated.push(t) });
  assert.ok(updated.join('\n').includes('live-reload-worker') && !updated.join('\n').includes('custom-probe'));
});

test('/use routes a named roster peer task to the orchestrator over the feed and ui-inbox', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  const d = tmp();
  const inboxFile = join(d, 'ui-inbox.md');
  writeFileSync(inboxFile, '# UI Inbox\n', 'utf8');
  const env = peerEnv({ WORKER_ROSTER_FILE: writeRoster(d), AESOP_ORCHESTRATOR_INBOX: inboxFile });
  handoffCfg(env);
  const r1 = await runPeerCommand({ command: 'use', arg: 'agy-headless reproduce line 1\nand line 2', env, notify: () => {} });
  const r2 = await runPeerCommand({ command: 'use', arg: '"codex cli" fix single file', env, notify: () => {} });
  const r3 = await runPeerCommand({ command: 'use', arg: 'codex-app run the A/B sim', env, notify: () => {} });
  assert.equal(r1.status, 'ok');
  assert.equal(r1.peer, 'orchestrator');
  assert.equal(r2.status, 'ok');
  assert.equal(r3.status, 'ok');
  const ev = feedEvents(env);
  assert.equal(ev.length, 6);
  assert.equal(ev[0].to, 'orchestrator');
  assert.match(ev[0].prompt, /use agy headless \(agy-headless\): reproduce line 1/);
  assert.match(ev[2].prompt, /use codex cli \(codex-cli\): fix single file/);
  assert.match(ev[4].prompt, /use codex app headless \(codex-app-headless\): run the A\/B sim/);
  const frame = await frameFor(ev.slice(0, 2));
  assert.ok(frame.includes('-> orchestrator') && frame.includes('<- orchestrator') && frame.includes('use agy headless (agy-headless)'), frame);
  const inboxLines = readFileSync(inboxFile, 'utf8').trim().split('\n').filter((l) => l.startsWith('- ['));
  assert.equal(inboxLines.length, 3);
  assert.match(inboxLines[0], /^- \[\d{4}-\d{2}-\d{2}T[^\]]+\] matt: use agy headless \(agy-headless\): reproduce line 1 \/ and line 2$/);
  assert.match(inboxLines[1], /matt: use codex cli \(codex-cli\): fix single file$/);
  assert.match(inboxLines[2], /matt: use codex app headless \(codex-app-headless\): run the A\/B sim$/);
});

test('/use refuses unknown worker kinds, missing tasks, and missing roster without writing anything', async () => {
  const { runPeerCommand } = await load('chat/peer_bus.mjs');
  const d = tmp();
  const inboxFile = join(d, 'ui-inbox.md');
  writeFileSync(inboxFile, '', 'utf8');
  const env = peerEnv({ WORKER_ROSTER_FILE: writeRoster(d), AESOP_ORCHESTRATOR_INBOX: inboxFile });
  const badKindNotes = [];
  const noTaskNotes = [];
  const noRosterNotes = [];
  assert.equal(await runPeerCommand({ command: 'use', arg: 'nonexistent-peer do work', env, notify: (t) => badKindNotes.push(t) }), null);
  assert.ok(badKindNotes.some((t) => t.startsWith('refused:') && t.includes('agy-headless')));
  assert.equal(await runPeerCommand({ command: 'use', arg: 'agy', env, notify: (t) => noTaskNotes.push(t) }), null);
  assert.ok(noTaskNotes.some((t) => t.includes('usage: /use')));
  assert.equal(await runPeerCommand({ command: 'use', arg: 'agy do work', env: { ...env, WORKER_ROSTER_FILE: join(d, 'missing.md') }, notify: (t) => noRosterNotes.push(t) }), null);
  assert.ok(noRosterNotes.some((t) => t.includes('ROSTER-MISSING')));
  assert.equal(feedEvents(env).length, 0);
  assert.equal(readFileSync(inboxFile, 'utf8'), '');
});

test('aesop peers and aesop post --to orchestrator work from bin/cli.js without polluting cwd or HOME', () => {
  const d = tmp();
  const cwd = join(d, 'empty-cwd');
  const home = join(d, 'isolated-home');
  mkdirSync(cwd, { recursive: true });
  mkdirSync(home, { recursive: true });
  const inboxFile = join(d, 'ui-inbox.md');
  writeFileSync(inboxFile, '', 'utf8');
  const baseEnv = { ...peerEnv(), HOME: home, USERPROFILE: home, WORKER_ROSTER_FILE: writeRoster(d), AESOP_ORCHESTRATOR_INBOX: inboxFile };
  const run = (args, extra = {}) => spawnSync(process.execPath, [cliPath, ...args], { cwd, env: { ...baseEnv, ...extra }, encoding: 'utf8' });

  const help = run(['peers', '--help']);
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /aesop peers/);
  const list = run(['peers']);
  assert.equal(list.status, 0, list.stderr);
  assert.ok(list.stdout.includes('agy-headless') && list.stdout.includes('/use agy-headless <task>'), list.stdout);
  assert.deepEqual(readdirSync(cwd), []);
  assert.deepEqual(readdirSync(home), []);

  const asJson = run(['peers', '--json']);
  assert.equal(asJson.status, 0, asJson.stderr);
  const parsed = JSON.parse(asJson.stdout);
  assert.equal(parsed.kinds.length, 6);
  assert.equal(parsed.kinds[1].id, 'agy-headless');

  const missing = run(['peers'], { WORKER_ROSTER_FILE: join(d, 'absent.md') });
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /ROSTER-MISSING/);

  const routed = run(['peers', 'use', 'haiku', 'retry failed lane']);
  assert.equal(routed.status, 0, routed.stderr);
  assert.match(routed.stdout, /^posted orchestrator-/);
  const directTo = run(['post', '--to', 'orchestrator', 'check PR #1494']);
  assert.equal(directTo.status, 0, directTo.stderr);
  assert.match(directTo.stdout, /^posted orchestrator-/);
  assert.equal(run(['post', '--to', 'nobody', 'hi']).status, 2);

  const inbox = readFileSync(inboxFile, 'utf8').trim().split('\n');
  assert.equal(inbox.length, 2);
  assert.match(inbox[0], /matt: use haiku \(haiku\): retry failed lane$/);
  assert.match(inbox[1], /matt: check PR #1494$/);
  assert.equal(feedEvents(baseEnv).filter((e) => e.to === 'orchestrator').length, 4);
  assert.deepEqual(readdirSync(cwd), []);
});

test('interactive runChat session renders /peers and /use without invoking the model source', async () => {
  const { PassThrough, Writable } = await import('node:stream');
  const { runChat } = await load('chat/cli.mjs');
  const d = tmp();
  const inboxFile = join(d, 'ui-inbox.md');
  writeFileSync(inboxFile, '', 'utf8');
  const env = peerEnv({ WORKER_ROSTER_FILE: writeRoster(d), AESOP_ORCHESTRATOR_INBOX: inboxFile, AESOP_CHAT_HISTORY: join(d, 'history.json'), NO_COLOR: '1' });
  handoffCfg(env);
  const stdin = new PassThrough();
  stdin.isTTY = true;
  stdin.setRawMode = () => {};
  let rawOut = '';
  const stdout = new Writable({ write(chunk, _enc, cb) { rawOut += Buffer.from(chunk).toString('utf8'); cb(); } });
  stdout.isTTY = true;
  stdout.columns = 110;
  stdout.rows = 45;
  const stderr = new Writable({ write(_c, _e, cb) { cb(); } });
  const backendCalls = [];
  const done = runChat({ stdin, stdout, stderr, env, source: async function* (l) { backendCalls.push(l); yield 'x'; } });
  stdin.write('/peers\r/use agy-headless reproduce pre-push rc=1\r');
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline && !rawOut.includes('<- orchestrator')) await new Promise((r) => setTimeout(r, 20));
  stdin.write('\x03\x03');
  assert.equal(await done, 0);
  assert.deepEqual(backendCalls, []);
  assert.ok(stripAnsi(rawOut).includes('[agy-headless] agy headless') && stripAnsi(rawOut).includes('<- orchestrator'));
  assert.match(readFileSync(inboxFile, 'utf8'), /matt: use agy headless \(agy-headless\): reproduce pre-push rc=1/);
});
