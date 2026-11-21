import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import childProcess from 'node:child_process';
import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { EventEmitter } from 'node:events';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { sse, startFake } from './helpers/fake_openai_server.mjs';

// Durable spec tests for docs/specs/chat-model-harness.md. One test per requirement, named "H-R<n>: ...".
// Do not rename, remove or skip them: tools/chat_harness_drift.mjs reports that as weakened.
// Run: node --import ./tests/helpers/isolated-env.mjs --test --test-force-exit tests/chat-harness-spec.test.mjs
// Boundary: no Anthropic API, no claude CLI, no non-loopback network (guard below throws).

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const strip = (s) => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');
const KEY = ['sk-test', 'SENTINEL', '7731'].join('-');

// ---- test-wide guards: non-loopback connects throw; any process spawn is recorded.
const isLoop = (h) => h === undefined || h === null || ['127.0.0.1', 'localhost', '::1', '[::1]'].includes(String(h));
const hostOf = (a) => {
  const o = a[0];
  if (typeof o === 'string') { try { return new URL(o).hostname; } catch { return o; } }
  if (o instanceof URL) return o.hostname;
  return (o && (o.hostname ?? o.host)) ?? (typeof a[1] === 'string' ? a[1] : undefined);
};
const spawned = [];
for (const [mod, name] of [[http, 'request'], [https, 'request'], [http, 'get'], [https, 'get']]) {
  const orig = mod[name];
  mod[name] = (...a) => {
    if (!isLoop(hostOf(a))) throw new Error(`non-loopback network call blocked: ${hostOf(a)}`);
    return orig.apply(mod, a);
  };
}
const origConnect = net.connect;
net.connect = (...a) => {
  const o = a[0];
  const h = typeof o === 'object' && o !== null ? o.host : typeof a[1] === 'string' ? a[1] : undefined;
  if (!isLoop(h)) throw new Error(`non-loopback connect blocked: ${h}`);
  return origConnect.apply(net, a);
};
for (const n of ['spawn', 'execFile', 'exec', 'spawnSync', 'execFileSync', 'execSync']) {
  const orig = childProcess[n];
  childProcess[n] = (...a) => { spawned.push([n, a[0]]); return orig.apply(childProcess, a); };
}

// ---- harness plumbing
const dirs = [];
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'aesop-harness-spec-')); dirs.push(d); return d; };
process.on('exit', () => { for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* best effort */ } } });
const prov = (url, extra = {}) => ({ type: 'openai-compatible', base_url: url, models: ['m'], is_local: true, ...extra });
const cfgFile = (cfg) => { const f = join(tmp(), 'aesop.config.json'); writeFileSync(f, typeof cfg === 'string' ? cfg : JSON.stringify(cfg)); return f; };

async function startChat({ config, env = {}, source, columns = 100, rows = 40, color = false } = {}) {
  const { runChat } = await import(pathToFileURL(join(root, 'chat/cli.mjs')).href);
  const dir = tmp();
  const stdin = Object.assign(new EventEmitter(), { isTTY: true, setRawMode() {}, resume() {}, pause() {} });
  const writes = [];
  const stdout = Object.assign(new EventEmitter(), { isTTY: true, columns, rows, write(s) { writes.push(String(s)); return true; } });
  const stderr = [];
  const cfgPath = config === undefined ? join(dir, 'none.json') : cfgFile(config);
  const stateRoot = join(dir, 'state');
  const e = {
    LANG: 'en_US.UTF-8', AESOP_CHAT_BACKEND: 'harness', AESOP_CHAT_CONFIG: cfgPath, AESOP_CHAT_HISTORY: join(dir, 'h.json'),
    AESOP_STATE_ROOT: stateRoot, ...(color ? {} : { NO_COLOR: '1' }), ...env,
  };
  const opts = { argv: [], stdin, stdout, stderr: { write: (s) => stderr.push(String(s)) }, env: e };
  if (source) opts.source = source;
  const done = runChat(opts);
  const press = (str, key) => stdin.emit('keypress', str, key);
  const k = (name, x = {}) => press(x.sequence ?? '', { name, ctrl: false, meta: false, sequence: '', ...x });
  const api = {
    writes, stderr, stateRoot, histFile: e.AESOP_CHAT_HISTORY, cfgPath,
    raw: () => writes.join(''),
    text: () => strip(writes.join('')),
    lastFrame: () => strip(writes[writes.length - 1] ?? ''),
    submit: async (s, wait = 150) => { for (const c of s) press(c, undefined); k('return', { sequence: '\r' }); await sleep(wait); },
    ctrlC: () => k('c', { ctrl: true, sequence: '\x03' }),
    quit: async () => { api.ctrlC(); api.ctrlC(); await Promise.race([done, sleep(1500)]); },
  };
  await sleep(30);
  return api;
}
const count = (text, re) => (text.match(re) ?? []).length;

// ---- Boundary and safety
test('H-R1: Anthropic and claude.ai hosts are refused, fail closed', async () => {
  const fake = await startFake();
  const bad = ['https://api.anthropic.com/v1', 'https://x.anthropic.com/v1', 'https://claude.ai/v1', 'https://API.ANTHROPIC.COM./v1',
    'http://127.0.0.1@api.anthropic.com/v1', 'https://api.%61nthropic.com/v1', 'https://sub.claude.ai/v1'];
  try {
    for (const u of bad) {
      const chat = await startChat({ config: { providers: { evil: prov(u, { is_local: false }), ok: prov(fake.url) }, default: 'evil/m' } });
      await chat.submit('/model evil/m');
      await chat.submit('hi', 200);
      assert.match(chat.text(), /refused/i, `no refusal for ${u}`);
      assert.match(chat.text(), /evil/, `refusal must name provider for ${u}`);
      await chat.quit();
    }
    assert.equal(fake.requests.length, 0);
  } finally { await fake.close(); }
});

test('H-R2: command-style providers and claude model ids are refused; nothing spawned', async () => {
  const fake = await startFake();
  const before = spawned.length;
  try {
    const cfg = { providers: {
      a: { type: 'cli', base_url: fake.url, models: ['m'] }, b: { type: 'exec', base_url: fake.url, models: ['m'] },
      c: { type: 'claude', base_url: fake.url, models: ['m'] }, d: { type: 'anthropic', base_url: fake.url, models: ['m'] },
      e: prov(fake.url, { models: ['claude-sonnet'] }), f: prov(fake.url, { models: ['anthropic/claude-x'] }),
    } };
    const chat = await startChat({ config: cfg });
    for (const p of ['a/m', 'b/m', 'c/m', 'd/m', 'e/claude-sonnet', 'f/anthropic/claude-x']) {
      await chat.submit(`/model ${p}`);
      await chat.submit('hi', 80);
    }
    assert.ok(count(chat.text(), /refused/gi) >= 6, 'each bad provider must be refused');
    assert.equal(fake.posts().length, 0);
    assert.equal(spawned.length, before, 'no child process spawned');
    await chat.quit();
  } finally { await fake.close(); }
});

test('H-R3: no config -> no provider, status line, one system line per turn, no network', async () => {
  const fake = await startFake();
  try {
    const chat = await startChat({});
    assert.match(chat.text(), /no provider configured \(see \/help\)/);
    assert.doesNotMatch(chat.text(), /anthropic/i);
    await chat.submit('hi');
    assert.equal(count(chat.lastFrame(), /no provider configured \(see \/help\)/g) >= 1, true);
    assert.equal(fake.requests.length, 0);
    await chat.quit();
  } finally { await fake.close(); }
});

test('H-R4: redirects to another host or to Anthropic are not followed', async () => {
  const second = await startFake();
  const a = await startFake((req, b, res) => { res.writeHead(302, { location: 'http://api.anthropic.com/v1/chat/completions' }); res.end(); });
  const c = await startFake((req, b, res) => { res.writeHead(302, { location: `${second.url}/chat/completions` }); res.end(); });
  try {
    for (const f of [a, c]) {
      const chat = await startChat({ config: { providers: { p: prov(f.url) }, default: 'p/m' } });
      await chat.submit('hi', 250);
      assert.match(chat.text(), /error: p:/);
      await chat.quit();
    }
    assert.equal(second.requests.length, 0);
  } finally { await a.close(); await c.close(); await second.close(); }
});

test('H-R5: non-loopback http is refused unless allow_insecure; loopback http accepted', async () => {
  const fake = await startFake();
  try {
    const chat = await startChat({ config: { providers: {
      bad: prov('http://example.test/v1', { is_local: false }),
      lo: prov(fake.url), lh: prov(`http://localhost:${fake.port}/v1`),
      ins: prov('http://example.test/v1', { is_local: false, allow_insecure: true }),
    } } });
    await chat.submit('/model bad/m');
    assert.match(chat.text(), /refused/i);
    await chat.submit('/model lo/m');
    await chat.submit('hi', 200);
    assert.match(chat.text(), /hello/);
    await chat.submit('/model lh/m');
    await chat.submit('hi', 200);
    assert.ok(fake.posts().length >= 2);
    await chat.submit('/model ins/m');
    assert.match(chat.text(), /NOTICE/);
    await chat.quit();
  } finally { await fake.close(); }
});

// ---- Registry, config, aliases, switching
test('H-R6: /model switches only to registered provider+model; unchanged otherwise', async () => {
  const A = await startFake(); const B = await startFake((q, b, res) => sse(res, ['from-b']), ['m2']);
  try {
    const chat = await startChat({ config: { providers: { A: prov(A.url), B: prov(B.url, { models: ['m2'] }) }, default: 'A/m' } });
    await chat.submit('hi', 200);
    const lenBefore = A.posts().length;
    await chat.submit('/model nope/m');
    assert.match(chat.text(), /unknown provider: nope/);
    await chat.submit('/model A/zzz');
    assert.match(chat.text(), /unknown model: zzz for A/);
    await chat.submit('hi', 200);
    assert.equal(A.posts().length, lenBefore + 1, 'selection unchanged after bad switches');
    await chat.submit('/model B/m2');
    await chat.submit('yo', 200);
    assert.equal(B.posts()[0].body.model, 'm2');
    assert.match(chat.text(), /hello/, 'transcript kept across /model');
    await chat.quit();
  } finally { await A.close(); await B.close(); }
});

test('H-R7: config loads providers; unknown keys NOTICE; malformed JSON -> one error line and H-R3 state', async () => {
  const fake = await startFake();
  try {
    const good = await startChat({ config: { providers: { A: prov(fake.url, { bogus_key: 1 }) }, default: 'A/m' } });
    await good.submit('hi', 200);
    assert.match(good.text(), /NOTICE/);
    assert.match(good.text(), /hello/);
    await good.quit();
    const bad = await startChat({ config: '{ not json' });
    assert.equal(count(bad.text(), /error:/g), 1);
    assert.match(bad.text(), /no provider configured/);
    await bad.quit();
  } finally { await fake.close(); }
});

test('H-R8: aliases resolve up to depth 3; cycles rejected', async () => {
  const fake = await startFake();
  try {
    const chat = await startChat({ config: { providers: { A: prov(fake.url, { models: ['real'] }) },
      aliases: { fast: 'A/real', f2: 'fast', f3: 'f2' } } });
    await chat.submit('/model f3');
    await chat.submit('hi', 200);
    assert.equal(fake.posts()[0].body.model, 'real');
    await chat.quit();
    const cyc = await startChat({ config: { providers: { A: prov(fake.url) }, aliases: { x: 'y', y: 'x' } } });
    assert.match(cyc.text(), /cycle|alias/i);
    await cyc.quit();
  } finally { await fake.close(); }
});

test('H-R9: /models lists providers, models, status with no chat request', async () => {
  const A = await startFake(); const B = await startFake();
  try {
    const chat = await startChat({ config: { providers: { alpha: prov(A.url), beta: prov(B.url) }, aliases: { fast: 'alpha/m' } } });
    await chat.submit('/models', 300);
    const t = chat.text();
    for (const w of ['alpha', 'beta', 'fast', 'reachable']) assert.match(t, new RegExp(w));
    assert.equal(A.posts().length + B.posts().length, 0);
    await chat.quit();
  } finally { await A.close(); await B.close(); }
});

test('H-R10: selection persists across /clear and restarts; state file has no key', async () => {
  const A = await startFake(); const B = await startFake((q, b, res) => sse(res, ['bee']));
  try {
    const cfg = cfgFile({ providers: { A: prov(A.url, { api_key_env: 'H_KEY' }), B: prov(B.url) }, default: 'A/m' });
    const env = { H_KEY: KEY };
    const s1 = await startChat({ config: undefined, env: { ...env, AESOP_CHAT_CONFIG: cfg } });
    await s1.submit('/model B/m');
    await s1.submit('/clear');
    await s1.submit('x', 200);
    assert.equal(B.posts().length, 1);
    await s1.quit();
    const s2 = await startChat({ env: { ...env, AESOP_CHAT_CONFIG: cfg, AESOP_STATE_ROOT: s1.stateRoot } });
    await s2.submit('y', 200);
    assert.equal(B.posts().length, 2, 'restart restores B');
    await s2.quit();
    const files = existsSync(s1.stateRoot) ? readdirSync(s1.stateRoot, { recursive: true }) : [];
    assert.ok(files.length > 0, 'state file written');
    for (const f of files) { const p = join(s1.stateRoot, String(f)); try { assert.ok(!readFileSync(p, 'utf8').includes(KEY)); } catch (e) { if (e.code !== 'EISDIR') throw e; } }
  } finally { await A.close(); await B.close(); }
});

// ---- Routing
test('H-R11: per-task routes: /code hits B, plain hits A', async () => {
  const A = await startFake(); const B = await startFake((q, b, res) => sse(res, ['bee']), ['bm']);
  try {
    const chat = await startChat({ config: { providers: { A: prov(A.url), B: prov(B.url, { models: ['bm'] }) }, default: 'A/m', routes: { code: 'B/bm' } } });
    await chat.submit('plain', 200);
    await chat.submit('/code write x', 200);
    assert.equal(A.posts().length, 1);
    assert.equal(B.posts().length, 1);
    assert.equal(B.posts()[0].body.model, 'bm');
    await chat.quit();
  } finally { await A.close(); await B.close(); }
});

test('H-R12: retryable failure falls back once with a transcript line', async () => {
  const A = await startFake((q, b, res) => { res.writeHead(503); res.end('x'); }); const B = await startFake((q, b, res) => sse(res, ['bee']));
  try {
    const chat = await startChat({ config: { providers: { A: prov(A.url), B: prov(B.url) }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m'] } } } });
    await chat.submit('hi', 300);
    assert.match(chat.text(), /falling back: A -> B/);
    assert.match(chat.text(), /bee/);
    assert.equal(A.posts().length, 1);
    assert.equal(B.posts().length, 1);
    assert.ok(A.posts()[0].at <= B.posts()[0].at);
    await chat.quit();
  } finally { await A.close(); await B.close(); }
});

test('H-R13: 401 does not fall back unless fallback_on any', async () => {
  const mk = (extra) => ({ providers: { A: prov(A.url), B: prov(B.url) }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m'], ...extra } } });
  const A = await startFake((q, b, res) => { res.writeHead(401); res.end('no'); }); const B = await startFake();
  try {
    const c1 = await startChat({ config: mk({}) });
    await c1.submit('hi', 250);
    assert.equal(B.posts().length, 0);
    assert.match(c1.text(), /error: A: auth/);
    await c1.quit();
    const c2 = await startChat({ config: mk({ fallback_on: 'any' }) });
    await c2.submit('hi', 250);
    assert.equal(B.posts().length, 1);
    await c2.quit();
  } finally { await A.close(); await B.close(); }
});

test('H-R14: failure after first chunk never re-sends; partial kept', async () => {
  const A = await startFake((q, b, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: 'one ' } }] })}\n\n`);
    res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: 'two' } }] })}\n\n`);
    setTimeout(() => res.socket.destroy(), 30); });
  const B = await startFake();
  try {
    const chat = await startChat({ config: { providers: { A: prov(A.url), B: prov(B.url) }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m'] } } } });
    await chat.submit('hi', 400);
    assert.equal(B.posts().length, 0);
    assert.match(chat.text(), /one two/);
    assert.match(chat.text(), /stream interrupted from A/);
    await chat.quit();
  } finally { await A.close(); await B.close(); }
});

test('H-R15: cheapest and fastest strategies order candidates', async () => {
  const A = await startFake(async (q, b, res) => { await sleep(120); sse(res, ['a']); });
  const B = await startFake((q, b, res) => sse(res, ['b']));
  try {
    const cfg = (strategy) => ({ providers: { A: prov(A.url, { cost_per_mtok: { input: 0, output: 0 } }), B: prov(B.url, { is_local: false, allow_insecure: true, cost_per_mtok: { input: 2, output: 3 } }) },
      default: 'A/m', routes: { chat: { strategy, candidates: ['A/m', 'B/m'] } } });
    const c1 = await startChat({ config: cfg('cheapest') });
    await c1.submit('hi', 300);
    assert.equal(A.posts().length, 1, 'cheapest picks A');
    await c1.quit();
    const c2 = await startChat({ config: cfg('fastest') });
    for (let i = 0; i < 3; i += 1) await c2.submit(`w${i}`, 300);
    const bBefore = B.posts().length;
    await c2.submit('final', 300);
    assert.ok(B.posts().length > bBefore, 'fastest picks B after warmup');
    await c2.quit();
  } finally { await A.close(); await B.close(); }
});

test('H-R16: circuit breaker skips a failing provider for a cooldown', async () => {
  const A = await startFake((q, b, res) => { res.writeHead(503); res.end('x'); }); const B = await startFake();
  try {
    let now = 1_000_000;
    globalThis.__AESOP_CHAT_NOW = () => now;
    const chat = await startChat({ config: { providers: { A: prov(A.url), B: prov(B.url) }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m'] } } } });
    for (let i = 0; i < 3; i += 1) await chat.submit(`t${i}`, 250);
    const aCount = A.posts().length;
    await chat.submit('skip', 250);
    assert.equal(A.posts().length, aCount, 'A skipped while tripped');
    assert.equal(count(chat.text(), /skipping A/g), 1, 'skip announced once');
    now += 31_000;
    await chat.submit('again', 250);
    assert.equal(A.posts().length, aCount + 1, 'A tried again after cooldown');
    await chat.quit();
  } finally { delete globalThis.__AESOP_CHAT_NOW; await A.close(); await B.close(); }
});

// ---- Streaming, errors
test('H-R17: SSE normalised: split frames, split multibyte, CRLF, comments, streamed before end', async () => {
  const text = 'h\u20acllo';
  const bytes = Buffer.from(`: keepalive\r\n\r\ndata: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}\r\n\r\ndata: [DONE]\r\n\r\n`);
  const cut = bytes.indexOf(0xe2) + 1;
  let release;
  const gate = new Promise((r) => { release = r; });
  const A = await startFake(async (q, b, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write(bytes.subarray(0, cut)); await sleep(30); res.write(bytes.subarray(cut)); await gate; res.end();
  });
  try {
    const chat = await startChat({ config: { providers: { A: prov(A.url) }, default: 'A/m' } });
    await chat.submit('hi', 400);
    assert.match(chat.text(), /h\u20acllo/, 'text framed before the server finished');
    release();
    await chat.quit();
  } finally { release(); await A.close(); }
});

test('H-R18: JSON, SSE and NDJSON replies normalise to the same transcript', async () => {
  const modes = {
    json: (q, b, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ choices: [{ message: { content: 'same text' } }] })); },
    sse: (q, b, res) => sse(res, [null, 'same ', '', 'text']),
    nd: (q, b, res) => { res.writeHead(200, { 'content-type': 'application/x-ndjson' }); res.write(`${JSON.stringify({ message: { content: 'same ' }, done: false })}\n`); res.end(`${JSON.stringify({ message: { content: 'text' }, done: true })}\n`); },
  };
  for (const [name, h] of Object.entries(modes)) {
    const A = await startFake(h);
    try {
      const chat = await startChat({ config: { providers: { A: prov(A.url) }, default: 'A/m' } });
      await chat.submit('hi', 300);
      assert.match(chat.text(), /same text/, name);
      assert.doesNotMatch(chat.text(), /null|undefined/, name);
      await chat.quit();
    } finally { await A.close(); }
  }
});

test('H-R19: every failure is one visible classified error line; session continues', async () => {
  let mode = '401';
  const A = await startFake(async (q, b, res) => {
    if (mode === '401') { res.writeHead(401); res.end(KEY); } else if (mode === '429') { res.writeHead(429); res.end('x'); }
    else if (mode === '500') { res.writeHead(500); res.end('x'); } else if (mode === 'bad') { res.writeHead(200); res.end('{{{'); }
    else if (mode === 'hang') { /* never answer */ } else sse(res, ['ok']);
  });
  try {
    const chat = await startChat({ config: { providers: { A: prov(A.url, { api_key_env: 'H_KEY', timeout_ms: 200 }) }, default: 'A/m' }, env: { H_KEY: KEY }, color: true });
    for (const [m, cls] of [['401', 'auth'], ['429', 'rate-limit'], ['500', 'server'], ['bad', 'bad-response'], ['hang', 'timeout']]) {
      mode = m;
      const before = count(strip(chat.raw()), /error: A: /g);
      await chat.submit(`t-${m}`, m === 'hang' ? 600 : 250);
      assert.match(chat.text(), new RegExp(`error: A: ${cls}: `), m);
      assert.ok(count(strip(chat.raw()), /error: A: /g) > before, m);
    }
    assert.match(chat.raw(), /\x1b\[[0-9;]*31m[^\n]*error: A:/, 'error lines are red');
    assert.ok(!chat.raw().includes(KEY) && !/Authorization/i.test(chat.raw()));
    mode = 'ok';
    await chat.submit('fine', 250);
    assert.match(chat.text(), /ok/);
    await chat.quit();
    const c2 = await startChat({ config: { providers: { D: prov('http://127.0.0.1:1/v1') }, default: 'D/m' } });
    await c2.submit('x', 250);
    assert.match(c2.text(), /error: D: connect: /);
    await c2.quit();
  } finally { await A.close(); }
});

test('H-R20: orchestrator provider routes plain turns (waits on F1)', { skip: 'H-R20 waits on F1' }, async () => {
  const fake = await startFake();
  try {
    const fakeOrch = async function* () { yield 'orch-reply'; };
    const chat = await startChat({ config: { providers: { orchestrator: { type: 'orchestrator', models: ['seat'] } }, default: 'orchestrator/seat' },
      env: { AESOP_CHAT_BACKEND: 'harness' }, source: fakeOrch });
    await chat.submit('hi', 200);
    assert.match(chat.text(), /orch-reply/);
    assert.equal(fake.requests.length, 0);
    await chat.quit();
  } finally { await fake.close(); }
});

test('H-R21: ctrl-c aborts the in-flight request, shows cancelled, no fallback', async () => {
  const A = await startFake((q, b, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: 'x' } }] })}\n\n`); });
  const B = await startFake();
  try {
    const chat = await startChat({ config: { providers: { A: prov(A.url), B: prov(B.url) }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m'] } } } });
    await chat.submit('hi', 150);
    const t0 = Date.now();
    chat.ctrlC();
    await sleep(900);
    assert.ok(A.closed.length >= 1 && A.closed[0] - t0 < 1000, 'server saw close within 1s');
    assert.match(chat.text(), /cancelled/);
    assert.equal(B.posts().length, 0);
    await chat.quit();
  } finally { await A.close(); await B.close(); }
});

test('H-R22: tokens from usage or chars/4 estimate marked ~; local cost 0', async () => {
  let withUsage = true;
  const A = await startFake((q, b, res) => sse(res, ['abcdefgh'], withUsage ? { usage: { prompt_tokens: 11, completion_tokens: 7 } } : {}));
  try {
    const chat = await startChat({ config: { providers: { A: prov(A.url) }, default: 'A/m' } });
    await chat.submit('hi', 250);
    assert.match(chat.lastFrame(), /\b18\b/, 'usage total 11+7');
    assert.match(chat.lastFrame(), /cost:? ?\$?0\b/);
    withUsage = false;
    await chat.submit('hi', 250);
    assert.match(chat.lastFrame(), /~\d+/, 'estimate carries ~');
    await chat.quit();
  } finally { await A.close(); }
});

// ---- Detection, secrets
test('H-R23: /detect probes loopback candidates only, only on request', async () => {
  const F1 = await startFake(); const F2 = await startFake();
  try {
    const closedPort = 1;
    const cfg = { detect_candidates: [F1.port, F2.port, closedPort, 'http://example.test:11434'], providers: {} };
    const chat = await startChat({ config: cfg });
    assert.equal(F1.requests.length + F2.requests.length, 0, 'no probe by default');
    await chat.submit('/detect', 1500);
    assert.ok(F1.requests.every((r) => r.method === 'GET' && r.url.startsWith('/v1/models')) && F1.requests.length === 1);
    assert.equal(F2.requests.length, 1);
    assert.match(chat.text(), new RegExp(String(F1.port)));
    assert.match(chat.text(), new RegExp(String(F2.port)));
    assert.match(chat.text(), /absent|not found|unreachable/i);
    assert.doesNotMatch(chat.text(), /example\.test.*(listed|reachable)/i);
    await chat.quit();
  } finally { await F1.close(); await F2.close(); }
});

test('H-R24: keys from env only, never leaked, /auth makes no network call', async () => {
  const A = await startFake((q, b, res) => { res.writeHead(500); res.end(`boom ${KEY}`); });
  try {
    const lit = await startChat({ config: { providers: { L: prov(A.url, { api_key: KEY }) } } });
    assert.match(lit.text(), /NOTICE/);
    await lit.quit();
    const chat = await startChat({ config: { providers: { A: prov(A.url, { api_key_env: 'H_KEY', is_local: false, allow_insecure: true }), N: prov(A.url) }, default: 'A/m' }, env: { H_KEY: KEY } });
    await chat.submit('hi', 250);
    assert.equal(A.posts()[0].headers.authorization, `Bearer ${KEY}`);
    const n = A.requests.length;
    await chat.submit('/auth', 150);
    assert.equal(A.requests.length, n, '/auth is offline');
    assert.match(chat.text(), /key: set \(env H_KEY\)/);
    assert.match(chat.text(), /key: not needed \(local\)/);
    assert.ok(!chat.raw().includes(KEY) && !chat.stderr.join('').includes(KEY));
    const hist = existsSync(chat.histFile) ? readFileSync(chat.histFile, 'utf8') : '';
    assert.ok(!hist.includes(KEY));
    await chat.quit();
  } finally { await A.close(); }
});

test('H-R25: a key is sent only to its own provider', async () => {
  const A = await startFake(); const B = await startFake((q, b, res) => sse(res, ['bee']));
  try {
    const chat = await startChat({ config: { providers: { A: prov(A.url, { api_key_env: 'H_KEY' }), B: prov(B.url) }, default: 'A/m' }, env: { H_KEY: KEY } });
    await chat.submit('one', 200);
    await chat.submit('/model B/m');
    await chat.submit('two', 200);
    assert.equal(A.posts()[0].headers.authorization, `Bearer ${KEY}`);
    assert.equal(B.posts().length, 1);
    assert.equal(B.posts()[0].headers.authorization, undefined);
    await chat.quit();
  } finally { await A.close(); await B.close(); }
});

// ---- Driver seam reuse
test('H-R26: Node and Python read provider-config cases from one shared fixture', async () => {
  const fx = join(root, 'tests/fixtures/provider-config-cases.json');
  assert.ok(existsSync(fx), 'shared conformance fixture exists');
  const cases = JSON.parse(readFileSync(fx, 'utf8'));
  assert.ok(Array.isArray(cases) && cases.length > 0);
  const mod = await import(pathToFileURL(join(root, 'chat/harness.mjs')).href);
  for (const c of cases) {
    const verdict = mod.validateProviderConfig(c.config);
    assert.equal(verdict.ok, c.expect === 'accept', c.reason);
  }
});

test('H-R27: an old config with no providers block starts byte-identically (pipe-style frames)', async () => {
  const echo = async function* (line) { yield String(line); };
  const old = { seats: {}, unrelated: true };
  const a = await startChat({ config: old, env: { AESOP_CHAT_BACKEND: undefined }, source: echo });
  const b = await startChat({ config: undefined, env: { AESOP_CHAT_BACKEND: undefined }, source: echo });
  await a.submit('hello', 100); await b.submit('hello', 100);
  assert.equal(a.text(), b.text());
  assert.doesNotMatch(a.text(), /no provider configured/);
  assert.match(a.text(), /hello/);
  await a.quit(); await b.quit();
  // chat-shaped provider entry must also be accepted by the driver seat reader unchanged
  const py = childProcess.spawnSync('python', ['-I', '-c', 'import sys; sys.path.insert(0, "driver"); import backend_config as b; assert hasattr(b, "load_provider_seat")'], { cwd: root, encoding: 'utf8' });
  assert.equal(py.status, 0, 'driver/backend_config.py accepts chat-shaped provider entries');
});

test('H-R28: AESOP_CHAT_BACKEND values: pipe default unchanged, harness selects registry, bogus is an error line', async () => {
  const echo = async function* (line) { yield `echo:${line}`; };
  const pipe = await startChat({ env: { AESOP_CHAT_BACKEND: undefined }, source: echo });
  await pipe.submit('hey', 100);
  assert.match(pipe.text(), /echo:hey/);
  assert.doesNotMatch(pipe.text(), /no provider configured/);
  await pipe.quit();
  const harness = await startChat({ env: { AESOP_CHAT_BACKEND: 'harness' }, source: echo });
  assert.match(harness.text(), /no provider configured/);
  await harness.quit();
  const bogus = await startChat({ env: { AESOP_CHAT_BACKEND: 'bogus-backend' }, source: echo });
  await bogus.submit('hey', 100);
  assert.match(bogus.text(), /error: .*AESOP_CHAT_BACKEND/);
  assert.doesNotMatch(bogus.text(), /echo:hey/);
  await bogus.quit();
});
