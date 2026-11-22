import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { sse, startFake } from './helpers/fake_openai_server.mjs';

// `aesop ask` end to end: the real bin/cli.js is spawned with no TTY against a fake 127.0.0.1
// OpenAI-compatible server. Exit codes: 0 ok, 2 usage, 3 unreachable, 4 provider error, 5 timeout, 6 boundary.
//
// Run: node --test tests/chat-ask.test.mjs

const cliPath = join(dirname(fileURLToPath(import.meta.url)), '..', 'bin', 'cli.js');

function tmp() {
  return mkdtempSync(join(tmpdir(), 'aesop-ask-'));
}

function writeConfig(dir, providers, extra = {}) {
  const file = join(dir, 'aesop.config.json');
  writeFileSync(file, JSON.stringify({ providers, ...extra }));
  return file;
}

function localProvider(url) {
  return { fake: { type: 'openai', base_url: url, models: 'any', is_local: true, timeout_ms: 5000 } };
}

/** Runs `aesop ask` with piped stdio (no TTY). */
function ask(args, { cwd, config, stdin = null, env = {} } = {}) {
  return new Promise((resolve) => {
    const home = tmp();
    const child = spawn(process.execPath, [cliPath, 'ask', ...args], {
      cwd,
      env: { ...process.env, HOME: home, USERPROFILE: home, AESOP_CHAT_CONFIG: config ?? '', ...env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => { stdout += d; });
    child.stderr.on('data', (d) => { stderr += d; });
    child.on('close', (code) => {
      rmSync(home, { recursive: true, force: true });
      resolve({ code, stdout, stderr });
    });
    if (stdin !== null) child.stdin.end(stdin); else child.stdin.end();
  });
}

async function withFake(handler, fn) {
  const fake = await startFake(handler);
  const dir = tmp();
  try {
    const config = writeConfig(dir, localProvider(fake.url), { default: 'fake/m1' });
    await fn({ fake, dir, config });
  } finally {
    await fake.close();
    rmSync(dir, { recursive: true, force: true });
  }
}

test('plain mode streams only the answer on stdout, exit 0', async () => {
  await withFake((req, body, res) => sse(res, ['pine', 'apple']), async ({ fake, dir, config }) => {
    const r = await ask(['say pineapple'], { cwd: dir, config });
    assert.equal(r.code, 0, r.stderr);
    assert.equal(r.stdout, 'pineapple\n');
    assert.equal(fake.posts().length, 1);
    assert.equal(fake.posts()[0].body.model, 'm1');
    assert.match(fake.posts()[0].body.messages.at(-1).content, /say pineapple/);
  });
});

test('--json prints one object {ok, provider, model, text, elapsed_ms}', async () => {
  await withFake((req, body, res) => sse(res, ['pine', 'apple']), async ({ dir, config }) => {
    const r = await ask(['--json', 'say pineapple'], { cwd: dir, config });
    assert.equal(r.code, 0, r.stderr);
    const obj = JSON.parse(r.stdout);
    assert.equal(obj.ok, true);
    assert.equal(obj.provider, 'fake');
    assert.equal(obj.model, 'm1');
    assert.equal(obj.text, 'pineapple');
    assert.equal(typeof obj.elapsed_ms, 'number');
    assert.equal('error' in obj, false);
  });
});

test('piped stdin is appended as context and --system is sent', async () => {
  await withFake((req, body, res) => sse(res, ['ok']), async ({ fake, dir, config }) => {
    const r = await ask(['--system', 'be terse', 'review this'], { cwd: dir, config, stdin: 'def add(a,b): return a+b' });
    assert.equal(r.code, 0, r.stderr);
    const sent = JSON.stringify(fake.posts()[0].body.messages);
    assert.match(sent, /review this/);
    assert.match(sent, /def add\(a,b\): return a\+b/);
    assert.match(sent, /be terse/);
  });
});

test('--model provider/model overrides the default', async () => {
  await withFake((req, body, res) => sse(res, ['ok']), async ({ fake, dir, config }) => {
    const r = await ask(['--model', 'fake/other', 'hi'], { cwd: dir, config });
    assert.equal(r.code, 0, r.stderr);
    assert.equal(fake.posts()[0].body.model, 'other');
  });
});

test('exit 2: no prompt, unknown flag, bad timeout, unknown provider', async () => {
  await withFake((req, body, res) => sse(res, ['x']), async ({ fake, dir, config }) => {
    for (const args of [[], ['--bogus', 'hi'], ['--timeout', 'abc', 'hi'], ['--model', 'nope/x', 'hi'], ['--model']]) {
      const r = await ask(args, { cwd: dir, config });
      assert.equal(r.code, 2, `${args.join(' ')} -> ${r.code} ${r.stderr}`);
      assert.equal(r.stdout, '');
      assert.notEqual(r.stderr, '');
    }
    assert.equal(fake.posts().length, 0);
  });
});

test('exit 3: provider unreachable, exact text on stderr', async () => {
  const srv = net.createServer();
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const port = srv.address().port;
  await new Promise((r) => srv.close(r));
  const dir = tmp();
  try {
    const config = writeConfig(dir, localProvider(`http://127.0.0.1:${port}/v1`), { default: 'fake/m1' });
    const r = await ask(['hi'], { cwd: dir, config });
    assert.equal(r.code, 3);
    assert.equal(r.stdout, '');
    assert.match(r.stderr, /cannot reach fake at http:\/\/127\.0\.0\.1:\d+ — is it running\?/);
    const j = await ask(['--json', 'hi'], { cwd: dir, config });
    assert.equal(j.code, 3);
    const obj = JSON.parse(j.stdout);
    assert.equal(obj.ok, false);
    assert.match(obj.error, /cannot reach fake/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('exit 3: built-in ollama default says "cannot reach ollama at ... — is it running?"', async () => {
  const dir = tmp();
  try {
    // No config: default ollama on 11434. Only meaningful when nothing listens there.
    const probe = net.createServer();
    const free = await new Promise((r) => { probe.once('error', () => r(false)); probe.listen(11434, '127.0.0.1', () => r(true)); });
    if (free) await new Promise((r) => probe.close(r));
    if (!free) return; // a real Ollama is running; the live proof covers this path
    const r = await ask(['hi'], { cwd: dir });
    assert.equal(r.code, 3);
    assert.match(r.stderr, /cannot reach ollama at http:\/\/127\.0\.0\.1:11434 — is it running\?/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('exit 4: provider HTTP error and auth error', async () => {
  await withFake((req, body, res) => {
    res.writeHead(404, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'model not found' } }));
  }, async ({ dir, config }) => {
    const r = await ask(['hi'], { cwd: dir, config });
    assert.equal(r.code, 4);
    assert.equal(r.stdout, '');
    assert.match(r.stderr, /model not found/);
    const j = JSON.parse((await ask(['--json', 'hi'], { cwd: dir, config })).stdout);
    assert.equal(j.ok, false);
    assert.match(j.error, /model not found/);
  });
});

test('exit 5: --timeout fires when the provider hangs', async () => {
  await withFake((req, body, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': hold\n\n'); }, async ({ dir, config }) => {
    const t0 = Date.now();
    const r = await ask(['--timeout', '1', 'hi'], { cwd: dir, config });
    assert.equal(r.code, 5, r.stderr);
    assert.ok(Date.now() - t0 < 4000);
    assert.match(r.stderr, /timed out/);
  });
});

test('exit 6: Anthropic host, Claude model id, claude binary provider; no request is sent', async () => {
  await withFake((req, body, res) => sse(res, ['x']), async ({ fake, dir, config }) => {
    // Claude model id through a (local) gateway provider.
    const claudeModel = await ask(['--model', 'fake/claude-sonnet-4', 'hi'], { cwd: dir, config });
    assert.equal(claudeModel.code, 6, claudeModel.stderr);
    assert.equal(claudeModel.stdout, '');
    assert.equal(fake.posts().length, 0);
    const j = JSON.parse((await ask(['--json', '--model', 'fake/claude-sonnet-4', 'hi'], { cwd: dir, config })).stdout);
    assert.equal(j.ok, false);
    assert.match(j.error, /refused/);

    // Anthropic host and a claude binary (cli type) in the registry.
    const bad = join(dir, 'bad');
    const { mkdirSync } = await import('node:fs');
    mkdirSync(bad);
    const cfg = writeConfig(bad, {
      anth: { type: 'openai', base_url: 'https://api.anthropic.com/v1', models: 'any' },
      bin: { type: 'cli', base_url: 'http://127.0.0.1:1/v1', models: 'any' },
    });
    for (const m of ['anth/x', 'bin/x']) {
      const r = await ask(['--model', m, 'hi'], { cwd: bad, config: cfg });
      assert.equal(r.code, 6, `${m}: ${r.code} ${r.stderr}`);
      assert.match(r.stderr, /refused/);
    }
  });
});

test('one-shot: no history file and no files written into cwd', async () => {
  await withFake((req, body, res) => sse(res, ['ok']), async ({ dir, config }) => {
    const before = readdirSync(dir).sort();
    const home = tmp();
    const r = await ask(['hi'], { cwd: dir, config, env: { HOME: home, USERPROFILE: home } });
    assert.equal(r.code, 0, r.stderr);
    assert.deepEqual(readdirSync(dir).sort(), before);
    // The one write allowed under HOME is the shared activity feed (chat/activity_feed.mjs feedPath).
    assert.deepEqual(readdirSync(home), ['.aesop']);
    assert.deepEqual(readdirSync(join(home, '.aesop')), ['state']);
    assert.deepEqual(readdirSync(join(home, '.aesop', 'state', 'chat')), ['activity.jsonl']);
    rmSync(home, { recursive: true, force: true });
  });
});

// P0 wrong data (f-db2dd2fa): an oversized prompt on the native Ollama route must warn on stderr
// (the harness notify hook), and the warning must not change the exit code.
test('ollama oversized input: warning on stderr, exit 0, answer still on stdout', async () => {
  const frame = (o) => `${JSON.stringify(o)}\n`;
  const handler = (req, body, res) => {
    res.writeHead(200, { 'content-type': 'application/x-ndjson' });
    res.write(frame({ message: { role: 'assistant', content: 'man' }, done: false }));
    res.write(frame({ message: { role: 'assistant', content: 'go' }, done: false }));
    res.end(frame({ message: { role: 'assistant', content: '' }, done: true, prompt_eval_count: 7, eval_count: 2 }));
  };
  const fake = await startFake(handler);
  const dir = tmp();
  try {
    const provider = { type: 'ollama', base_url: `${fake.url}/v1`, models: 'any', is_local: true, timeout_ms: 5000, num_ctx_max: 8192 };
    const config = writeConfig(dir, { ollama: provider }, { default: 'ollama/m1' });
    const r = await ask(['summarize the input'], { cwd: dir, config, stdin: 'x'.repeat(70000) });
    assert.equal(r.code, 0, r.stderr);
    assert.equal(r.stdout, 'mango\n');
    assert.match(r.stderr, /^warning: input ~\d+ tokens exceeds the model window 8192; the start will be cut$/m);
  } finally {
    await fake.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('claudemd-lint returns a capped lossless repair without calling a model', async () => {
  await withFake((req, body, res) => sse(res, ['unexpected model call']), async ({ fake, dir, config }) => {
    const first = 'é'.repeat(510) + '.';
    const last = 'Done.';
    const r = await ask(['--split-claudemd-line', '--json', 'repair'], {
      cwd: dir, config, stdin: JSON.stringify({ data: { line: first + ' ' + last } }),
    });
    assert.equal(fake.posts().length, 0);
    assert.equal(r.code, 0, r.stderr);
    const result = JSON.parse(r.stdout);
    assert.equal(result.model, null);
    assert.deepEqual(JSON.parse(result.text), [first, last]);
  });
});

test('claudemd-lint refuses unsplittable input without a provider fallback', async () => {
  await withFake((req, body, res) => sse(res, ['unexpected model call']), async ({ fake, dir, config }) => {
    const r = await ask(['--split-claudemd-line', '--json'], {
      cwd: dir, config, stdin: JSON.stringify({ data: { line: 'x'.repeat(1025) } }),
    });
    assert.equal(r.code, 2);
    const result = JSON.parse(r.stdout);
    assert.equal(result.text, '');
    assert.match(result.error, /no safe sentence split/);
    assert.equal(fake.posts().length, 0);
  });
});

test('change-summary serializes source facts even when the model invents a result', async () => {
  const data = {
    title: 'Fix "quoted" paths',
    diff_headers: ['diff --git a/z b/z', 'diff --git "a/old name" "b/new name"',
      'diff --git a/path b/sub b/path b/sub', 'diff --git "a/\\303\\251" "b/\\303\\251"'],
    evidence: ['# fail 2', '# tests 2', '# pass 0', '# fail 2', '# pass 10 later', ' # fail 5'],
    why_options: ['Keep renamed paths readable.'],
  };
  const expected = { title: data.title, paths: ['z', 'new name', 'path b/sub', 'é'], counts: ['# fail 2', '# pass 0', '# fail 2'] };
  await withFake((_req, _body, res) => sse(res, ['CI is green.', '\n```']), async ({ dir, fake, config }) => {
    const feed = join(dir, 'feed.jsonl');
    for (const json of [false, true]) {
      const args = ['--change-summary', ...(json ? ['--json'] : []), 'summarize'];
      const r = await ask(args, { cwd: dir, config, stdin: JSON.stringify({ data }), env: { AESOP_ACTIVITY_FEED: feed } });
      assert.equal(r.code, 0, r.stderr);
      const answer = json ? JSON.parse(r.stdout).text : r.stdout.trimEnd();
      assert.deepEqual(JSON.parse(answer), expected);
      const events = readFileSync(feed, 'utf8').trim().split('\n').map(line => JSON.parse(line));
      assert.equal(events.filter(e => e.job === events.at(-1).job && e.type === 'chunk').map(e => e.text).join(''), answer);
    }
    assert.doesNotMatch(fake.posts()[0].body.messages.at(-1).content, /# fail 2|diff --git/);
  });
});

test('change-summary accepts only a supplied purpose and rejects malformed sources before calling the provider', async () => {
  const why = 'Keep renamed paths readable.';
  const data = { title: 'Rename', evidence: [], diff_headers: [], why_options: [why] };
  await withFake((_req, _body, res) => sse(res, [why]), async ({ dir, fake, config }) => {
    const run = (packet) => ask(['--change-summary', 'summarize'], { cwd: dir, config, stdin: JSON.stringify(packet) });
    assert.deepEqual(JSON.parse((await run({ data })).stdout), { title: 'Rename', paths: [], counts: [], why });
    for (const invalid of [null, { data: { ...data, evidence: 'not lines' } },
      { data: { ...data, diff_headers: ['diff --git a/src'] } }]) {
      assert.equal((await run(invalid)).code, 2);
    }
    assert.equal(fake.posts().length, 1);
  });
});
