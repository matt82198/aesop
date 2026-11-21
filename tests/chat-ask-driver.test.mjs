// `aesop ask` runs its turn in the driver child (driver/turn.py --serve) by way of chat/driver_source.mjs.
// tests/chat-ask.test.mjs is the unchanged oracle for the exit codes and the output; this file proves the
// turn really goes through the driver, that the old path is still reachable by a flag, and that nothing is
// left running when ask exits. The real bin/cli.js is spawned with no TTY against a fake 127.0.0.1 server.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { sse, startFake } from './helpers/fake_openai_server.mjs';

const cliPath = join(dirname(fileURLToPath(import.meta.url)), '..', 'bin', 'cli.js');
const tmp = () => mkdtempSync(join(tmpdir(), 'aesop-ask-driver-'));

function ask(args, { cwd, config, env = {}, stdin = null } = {}) {
  return new Promise((resolve) => {
    const home = tmp();
    const child = spawn(process.execPath, [cliPath, 'ask', ...args], {
      cwd, env: { ...process.env, HOME: home, USERPROFILE: home, AESOP_CHAT_CONFIG: config ?? '', ...env }, stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => { stdout += d; });
    child.stderr.on('data', (d) => { stderr += d; });
    child.on('close', (code) => { rmSync(home, { recursive: true, force: true }); resolve({ code, stdout, stderr, at: Date.now() }); });
    if (stdin !== null) child.stdin.end(stdin); else child.stdin.end();
  });
}

async function withFake(handler, fn, raw = {}) {
  const fake = await startFake(handler);
  const dir = tmp();
  try {
    const config = join(dir, 'aesop.config.json');
    writeFileSync(config, JSON.stringify({ providers: { fake: { type: 'openai', base_url: fake.url, models: 'any', is_local: true, timeout_ms: 5000, ...raw } }, default: 'fake/m1' }));
    await fn({ fake, dir, config });
  } finally {
    await fake.close();
    rmSync(dir, { recursive: true, force: true });
  }
}

test('ask runs its turn in the driver child: with no interpreter the turn cannot start', async () => {
  await withFake((req, body, res) => sse(res, ['pine', 'apple']), async ({ fake, dir, config }) => {
    const r = await ask(['say pineapple'], { cwd: dir, config, env: { AESOP_PYTHON: join(dir, 'no-such-python') } });
    assert.equal(r.code, 3, r.stderr);
    assert.equal(r.stdout, '');
    assert.match(r.stderr, /error: driver: connect: cannot start the turn engine/);
    assert.equal(fake.posts().length, 0, 'no engine, no request');
    const json = await ask(['--json', 'say pineapple'], { cwd: dir, config, env: { AESOP_PYTHON: join(dir, 'no-such-python') } });
    const obj = JSON.parse(json.stdout);
    assert.equal(json.code, 3);
    assert.equal(obj.ok, false);
    assert.match(obj.error, /cannot start the turn engine/);
  });
});

test('AESOP_ASK_BACKEND=harness keeps the old in-process path (reversible)', async () => {
  await withFake((req, body, res) => sse(res, ['pine', 'apple']), async ({ fake, dir, config }) => {
    const r = await ask(['say pineapple'], { cwd: dir, config, env: { AESOP_ASK_BACKEND: 'harness', AESOP_PYTHON: join(dir, 'no-such-python') } });
    assert.equal(r.code, 0, r.stderr);
    assert.equal(r.stdout, 'pineapple\n');
    assert.equal(fake.posts().length, 1);
    const bad = await ask(['say pineapple'], { cwd: dir, config, env: { AESOP_ASK_BACKEND: 'nope' } });
    assert.equal(bad.code, 2);
    assert.match(bad.stderr, /AESOP_ASK_BACKEND/);
  });
});

test('through the driver: the answer streams to stdout, the request is the same, exit 0', async () => {
  await withFake((req, body, res) => sse(res, ['pine', 'apple']), async ({ fake, dir, config }) => {
    const r = await ask(['--system', 'be brief', 'say pineapple'], { cwd: dir, config, stdin: 'ctx line\n' });
    assert.equal(r.code, 0, r.stderr);
    assert.equal(r.stdout, 'pineapple\n');
    assert.equal(r.stderr, '');
    const post = fake.posts()[0];
    assert.equal(post.url, '/v1/chat/completions');
    assert.deepEqual(post.body, { model: 'm1', stream: true, messages: [{ role: 'user', content: 'be brief\n\nsay pineapple\n\n--- context (stdin) ---\nctx line\n' }] });
  });
});

test('through the driver: a timeout is exit 5 and the provider sees the connection close', async () => {
  await withFake((req, body, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); }, async ({ fake, dir, config }) => {
    const r = await ask(['--timeout', '1', 'hang'], { cwd: dir, config });
    assert.equal(r.code, 5, r.stderr);
    assert.match(r.stderr, /error: fake: timeout: timed out after 1 s/);
    // On a slow machine the timeout can fire before the engine has connected, and then there is no
    // request at all. Whatever did reach the provider must be closed once ask has exited.
    for (let i = 0; i < 80 && fake.closed.length < fake.posts().length; i += 1) await sleep(25);
    assert.equal(fake.closed.length, fake.posts().length, 'nothing is left holding a provider connection after ask exits');
  });
});

test('through the driver: ollama input over the window warns on stderr and still exits 0', async () => {
  const handler = (req, body, res) => {
    res.writeHead(200, { 'content-type': 'application/x-ndjson' });
    res.write(`${JSON.stringify({ message: { content: 'man' }, done: false })}\n`);
    res.end(`${JSON.stringify({ message: { content: 'go' }, done: true, prompt_eval_count: 7, eval_count: 2 })}\n`);
  };
  const fake = await startFake(handler);
  const dir = tmp();
  try {
    const config = join(dir, 'aesop.config.json');
    writeFileSync(config, JSON.stringify({ providers: { ollama: { type: 'openai', base_url: fake.url, models: 'any', is_local: true } }, default: 'ollama/m' }));
    const paste = `The code is ZEBRA.\n${'filler line\n'.repeat(13000)}`;
    const r = await ask(['what is the code?'], { cwd: dir, config, stdin: paste });
    assert.equal(r.code, 0, r.stderr);
    assert.equal(r.stdout, 'mango\n');
    assert.match(r.stderr, /^warning: input ~\d+ tokens exceeds the model window 32768; the start will be cut\n$/);
    const post = fake.posts()[0];
    assert.equal(post.url, '/api/chat');
    assert.equal(post.body.options.num_ctx, 32768);
    assert.ok(post.body.messages[0].content.includes('The code is ZEBRA.'));
  } finally {
    await fake.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
