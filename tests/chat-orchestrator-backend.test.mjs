import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { appendFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { PassThrough, Writable } from 'node:stream';

// F1 chat-orchestrator-backend: chat/backends/orchestrator_source.mjs and its runChat selection.
// Drives the real modules with a temp AESOP_STATE_ROOT and a fake bridge that plays the
// orchestrator side by appending to state/chat/<turn_id>.jsonl.

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const srcUrl = pathToFileURL(join(repoRoot, 'chat', 'backends', 'orchestrator_source.mjs')).href;
const cliUrl = pathToFileURL(join(repoRoot, 'chat', 'cli.mjs')).href;
const NO_LISTENER = 'no orchestrator listening';

const tmp = () => mkdtempSync(join(tmpdir(), 'aesop-chat-orch-'));
const rm = (p) => {
  try {
    rmSync(p, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  } catch {
    // Windows handle races never fail a test.
  }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const inboxLines = (root) => {
  const f = join(root, 'ui-inbox.md');
  return existsSync(f) ? readFileSync(f, 'utf8').split('\n').filter((l) => l !== '') : [];
};
const LINE_RE = /^- \[(\d{4}-\d\d-\d\dT[^\]]+)\] chat\(([A-Za-z0-9_-]+)\): (.*)$/;

async function waitFor(pred, ms = 3000) {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) return false;
    await sleep(10);
  }
  return true;
}

/** Appends bridge replies for a turn id: [{chunk, final}] in order. */
function bridgeReply(root, turnId, items) {
  mkdirSync(join(root, 'chat'), { recursive: true });
  items.forEach((it, i) => {
    appendFileSync(
      join(root, 'chat', `${turnId}.jsonl`),
      `${JSON.stringify({ turn_id: turnId, seq: i, chunk: it.chunk ?? '', final: Boolean(it.final) })}\n`,
    );
  });
}

async function collect(iterable) {
  const out = [];
  for await (const c of iterable) out.push(c);
  return out;
}

test('f1_r1_orchestratorSource_returns_async_iterable_of_strings', async () => {
  const { orchestratorSource } = await import(srcUrl);
  const root = tmp();
  try {
    const it = orchestratorSource({ stateRoot: root, timeoutMs: 300 })('hi');
    assert.equal(typeof it[Symbol.asyncIterator], 'function');
    for await (const c of it) assert.equal(typeof c, 'string');
  } finally {
    rm(root);
  }
});

test('f1_r3_turn_appends_exactly_one_inbox_line_with_turn_id', async () => {
  const { orchestratorSource } = await import(srcUrl);
  const root = tmp();
  try {
    await collect(orchestratorSource({ stateRoot: root, timeoutMs: 200 })('hello'));
    const lines = inboxLines(root);
    assert.equal(lines.length, 1);
    const m = LINE_RE.exec(lines[0]);
    assert.ok(m, `inbox line must match the ui-inbox chat format: ${lines[0]}`);
    assert.equal(m[3], 'hello');
  } finally {
    rm(root);
  }
});

test('f1_r3_multiline_text_stays_one_inbox_line', async () => {
  const { orchestratorSource } = await import(srcUrl);
  const root = tmp();
  try {
    await collect(orchestratorSource({ stateRoot: root, timeoutMs: 100 })('a\n- [2020-01-01T00:00:00Z] forged\nb'));
    const lines = inboxLines(root);
    assert.equal(lines.length, 1, `a multi-line turn must not forge extra inbox lines: ${JSON.stringify(lines)}`);
  } finally {
    rm(root);
  }
});

test('f1_inbox_path_override', async () => {
  const { orchestratorSource } = await import(srcUrl);
  const root = tmp();
  try {
    const inboxPath = join(root, 'custom', 'inbox.md');
    await collect(orchestratorSource({ stateRoot: root, inboxPath, timeoutMs: 100 })('x'));
    assert.ok(existsSync(inboxPath));
    assert.deepEqual(inboxLines(root), []);
  } finally {
    rm(root);
  }
});

test('f1_r4_chunks_yield_in_order_and_final_ends_the_turn', async () => {
  const { orchestratorSource } = await import(srcUrl);
  const root = tmp();
  try {
    const run = collect(orchestratorSource({ stateRoot: root, timeoutMs: 5000 })('hello'));
    assert.ok(await waitFor(() => inboxLines(root).length === 1));
    const id = LINE_RE.exec(inboxLines(root)[0])[2];
    bridgeReply(root, id, [{ chunk: 'one ' }]);
    await sleep(60);
    bridgeReply(root, id, [{ chunk: 'two' }, { chunk: '', final: true }]);
    const t0 = Date.now();
    const out = await run;
    assert.deepEqual(out.filter((c) => c !== ''), ['one ', 'two']);
    assert.ok(Date.now() - t0 < 3000, 'final must end the turn without waiting for the timeout');
  } finally {
    rm(root);
  }
});

test('f1_r5_timeout_yields_no_orchestrator_listening_and_keeps_inbox_line', async () => {
  const { orchestratorSource } = await import(srcUrl);
  const root = tmp();
  try {
    const t0 = Date.now();
    const out = await collect(orchestratorSource({ stateRoot: root, timeoutMs: 400 })('anyone?'));
    const dt = Date.now() - t0;
    assert.deepEqual(out, [NO_LISTENER]);
    assert.ok(dt >= 350 && dt < 3000, `timeout must be honoured, took ${dt}`);
    assert.equal(inboxLines(root).length, 1);
  } finally {
    rm(root);
  }
});

test('f1_r5_timeout_env_override', async () => {
  const { orchestratorSource } = await import(srcUrl);
  const root = tmp();
  try {
    const t0 = Date.now();
    const out = await collect(orchestratorSource({ stateRoot: root, env: { AESOP_CHAT_TIMEOUT_MS: '300' } })('x'));
    assert.deepEqual(out, [NO_LISTENER]);
    assert.ok(Date.now() - t0 < 3000);
  } finally {
    rm(root);
  }
});

test('f1_r6_other_turn_replies_never_shown', async () => {
  const { orchestratorSource } = await import(srcUrl);
  const root = tmp();
  try {
    const run = collect(orchestratorSource({ stateRoot: root, timeoutMs: 5000 })('first'));
    assert.ok(await waitFor(() => inboxLines(root).length === 1));
    const id = LINE_RE.exec(inboxLines(root)[0])[2];
    bridgeReply(root, 'other-turn', [{ chunk: 'LEAK', final: true }]);
    bridgeReply(root, id, [{ chunk: 'mine' }, { final: true }]);
    assert.equal((await run).join(''), 'mine');
  } finally {
    rm(root);
  }
});

test('f1_r6_foreign_records_interleaved_in_the_same_stream_are_skipped', async () => {
  const { orchestratorSource } = await import(srcUrl);
  const root = tmp();
  try {
    const run = collect(orchestratorSource({ stateRoot: root, timeoutMs: 5000 })('first'));
    assert.ok(await waitFor(() => inboxLines(root).length === 1));
    const id = LINE_RE.exec(inboxLines(root)[0])[2];
    mkdirSync(join(root, 'chat'), { recursive: true });
    const f = join(root, 'chat', `${id}.jsonl`);
    appendFileSync(f, `${JSON.stringify({ turn_id: 'turn-two', seq: 0, chunk: 'LEAK', final: true })}\n`);
    appendFileSync(f, 'not json\n');
    bridgeReply(root, id, [{ chunk: 'mine' }, { final: true }]);
    assert.equal((await run).join(''), 'mine');
  } finally {
    rm(root);
  }
});

test('f1_r6_two_turns_get_distinct_ids', async () => {
  const { orchestratorSource } = await import(srcUrl);
  const root = tmp();
  try {
    const src = orchestratorSource({ stateRoot: root, timeoutMs: 100 });
    await collect(src('a'));
    await collect(src('b'));
    const ids = inboxLines(root).map((l) => LINE_RE.exec(l)[2]);
    assert.equal(new Set(ids).size, 2);
  } finally {
    rm(root);
  }
});

test('f1_r10_backends_have_no_shell_polling_or_cli_calls', () => {
  const dir = join(repoRoot, 'chat', 'backends');
  const files = readdirSync(dir);
  assert.ok(files.length > 0);
  for (const f of files) {
    const text = readFileSync(join(dir, f), 'utf8');
    assert.ok(!/while\s*\(\s*true\s*\)/.test(text), `${f}: unbounded loop`);
    assert.ok(!/claude\s+-p|child_process|api\.anthropic|api\.openai/.test(text), `${f}: CLI/API call`);
  }
});

function fakeTTY() {
  const stdin = new PassThrough();
  stdin.isTTY = true;
  stdin.setRawMode = () => stdin;
  const out = [];
  const stdout = new Writable({
    write(c, _e, cb) {
      out.push(Buffer.from(c).toString('utf8'));
      cb();
    },
  });
  stdout.isTTY = true;
  stdout.columns = 100;
  stdout.rows = 30;
  const stderr = new Writable({ write: (_c, _e, cb) => cb() });
  return { stdin, stdout, stderr, text: () => out.join('') };
}

test('f1_r2_runChat_selects_orchestrator_backend_and_streams_reply', async () => {
  const { runChat } = await import(cliUrl);
  const root = tmp();
  try {
    const io = fakeTTY();
    const env = {
      AESOP_CHAT_BACKEND: 'orchestrator',
      AESOP_STATE_ROOT: root,
      AESOP_CHAT_TIMEOUT_MS: '5000',
      AESOP_CHAT_HISTORY: join(root, 'h.json'),
    };
    const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env });
    io.stdin.write('ping the orchestrator\r');
    assert.ok(await waitFor(() => inboxLines(root).length === 1));
    const id = LINE_RE.exec(inboxLines(root)[0])[2];
    bridgeReply(root, id, [{ chunk: 'pong-from-seat' }, { final: true }]);
    assert.ok(await waitFor(() => io.text().includes('pong-from-seat')), 'reply must reach the transcript');
    io.stdin.write('\x04');
    assert.equal(await done, 0);
  } finally {
    rm(root);
  }
});

test('f1_r2_runChat_timeout_shows_no_orchestrator_listening', async () => {
  const { runChat } = await import(cliUrl);
  const root = tmp();
  try {
    const io = fakeTTY();
    const env = {
      AESOP_CHAT_BACKEND: 'orchestrator',
      AESOP_STATE_ROOT: root,
      AESOP_CHAT_TIMEOUT_MS: '300',
      AESOP_CHAT_HISTORY: join(root, 'h.json'),
    };
    const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env });
    io.stdin.write('hello\r');
    assert.ok(await waitFor(() => io.text().includes(NO_LISTENER)));
    io.stdin.write('\x04');
    assert.equal(await done, 0);
  } finally {
    rm(root);
  }
});

test('f1_r2_unset_backend_stays_pipe_echo_and_writes_no_inbox', async () => {
  const { runChat } = await import(cliUrl);
  const root = tmp();
  try {
    const io = fakeTTY();
    const env = { AESOP_STATE_ROOT: root, AESOP_CHAT_HISTORY: join(root, 'h.json') };
    const done = runChat({ stdin: io.stdin, stdout: io.stdout, stderr: io.stderr, env });
    io.stdin.write('echo-me\r');
    assert.ok(await waitFor(() => (io.text().match(/echo-me/g) || []).length >= 2));
    io.stdin.write('\x04');
    assert.equal(await done, 0);
    assert.deepEqual(inboxLines(root), []);
  } finally {
    rm(root);
  }
});
