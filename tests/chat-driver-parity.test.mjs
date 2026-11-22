// Golden-transcript parity: the same scripted turns run through the Node harness (chat/harness.mjs) and
// through the driver backend (chat/driver_source.mjs -> driver/turn.py). The two transcripts must be equal:
// every text delta, every system line, every usage report, every thrown error, and every request the fake
// providers received (path, body, key header, body length). This is the gate spec H-R26 names for flipping
// the chat default to the driver. Offline: fake servers on 127.0.0.1 only.
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { setTimeout as sleep } from 'node:timers/promises';
import { driverSource } from '../chat/driver_source.mjs';
import { harnessSource, loadRegistry } from '../chat/harness.mjs';
import { sse, startFake } from './helpers/fake_openai_server.mjs';

const dirs = [];
process.on('exit', () => { for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* best effort */ } } });
const KEY = `sk-${'parity'}-${'SENTINEL'}`;

const delta = (text) => `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}\n\n`;
const head = (res, type = 'text/event-stream') => res.writeHead(200, { 'content-type': type });
async function closedPort() {
  const server = net.createServer();
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const { port } = server.address();
  await new Promise((r) => server.close(r));
  return port;
}

// Named provider behaviours. Each is (req, body, res, n) where n is the 1-based request count.
const HANDLERS = {
  hello: (q, b, res) => sse(res, ['hel', 'lo']),
  usage: (q, b, res) => sse(res, ['ok'], { usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 } }),
  echoCount: (q, b, res) => sse(res, [`reply to ${b.messages.length} message(s)`]),
  ndjson: (q, b, res) => {
    head(res, 'application/x-ndjson');
    res.write(`${JSON.stringify({ message: { content: 'man' }, done: false })}\n`);
    res.write(`${JSON.stringify({ message: { content: 'go' }, done: false })}\n`);
    res.end(`${JSON.stringify({ message: { content: '' }, done: true, prompt_eval_count: 7, eval_count: 2 })}\n`);
  },
  wholeJson: (q, b, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ choices: [{ message: { content: 'whole' } }], usage: { prompt_tokens: 3, completion_tokens: 1 } }));
  },
  splitFrames: (q, b, res) => {
    const frame = Buffer.from(`: keepalive\r\n\r\ndata: ${JSON.stringify({ choices: [{ delta: { content: 'a€b' } }] })}\r\n\r\n`, 'utf8');
    const cut = frame.indexOf(Buffer.from('€', 'utf8')) + 1;
    head(res);
    res.write(frame.subarray(0, cut));
    setTimeout(() => { res.write(frame.subarray(cut)); res.end('data: [DONE]\n\n'); }, 40);
  },
  emptyDeltas: (q, b, res) => {
    head(res);
    for (const d of [{ content: null }, { content: '' }, { reasoning: 'hm' }, {}]) res.write(`data: ${JSON.stringify({ choices: [{ delta: d }] })}\n\n`);
    res.end('data: [DONE]\n\n');
  },
  s401: (q, b, res) => { res.writeHead(401, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: { message: 'bad   key\n here' } })); },
  s404: (q, b, res) => { res.writeHead(404, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: "model 'm' not found" })); },
  s429: (q, b, res) => { res.writeHead(429); res.end('slow down'); },
  s503: (q, b, res) => { res.writeHead(503); res.end('x'); },
  redirect: (q, b, res) => { res.writeHead(302, { location: 'http://127.0.0.1:9/v1/chat/completions' }); res.end(); },
  malformed: (q, b, res) => { head(res); res.end('data: {not json}\n\n'); },
  cutAfterTwo: (q, b, res) => { head(res); res.write(delta('one ')); res.write(delta('two')); setTimeout(() => res.socket.destroy(), 150); },
  cutBeforeAny: (q, b, res) => { head(res); setTimeout(() => res.socket.destroy(), 30); },
  hang: () => { /* never answers */ },
  holdAfterOne: (q, b, res) => { head(res); res.write(delta('x')); },
  firstHoldsThenHello: (q, b, res, n) => { if (n === 1) { head(res); res.write(delta('x')); } else sse(res, ['after']); },
};

// Each scenario: fakes (label -> handler name, or 'closed' for a port nothing listens on), a config whose
// base_url values are fake labels, an env, and turns. A turn may pin the selection, the task, the test
// clock, or abort after N deltas.
const SCENARIOS = [
  { name: 'sse reply, then a second turn that carries the first as context',
    fakes: { A: 'echoCount' }, config: { providers: { A: { models: ['m'] } }, default: 'A/m' },
    turns: [{ line: 'first' }, { line: 'second' }, { line: 'third' }] },
  { name: 'usage is priced: local is free, cost_per_mtok prices a remote',
    fakes: { A: 'usage', B: 'usage' },
    config: { providers: { A: { models: ['m'] }, B: { models: ['m'], is_local: false, allow_insecure: true, cost_per_mtok: { input: 2, output: 3 } } }, default: 'A/m' },
    turns: [{ line: 'hi' }, { line: 'hi', select: 'B/m' }] },
  { name: 'ndjson and whole-json replies normalise to deltas',
    fakes: { A: 'ndjson', B: 'wholeJson' }, config: { providers: { A: { models: ['m'] }, B: { models: ['m'] } }, default: 'A/m' },
    turns: [{ line: 'hi' }, { line: 'hi', select: 'B/m' }] },
  { name: 'split frames, a split multibyte character, CRLF and keepalives',
    fakes: { A: 'splitFrames' }, config: { providers: { A: { models: ['m'] } }, default: 'A/m' }, turns: [{ line: 'hi' }] },
  { name: 'an empty reply is not remembered as context',
    fakes: { A: 'emptyDeltas', B: 'echoCount' }, config: { providers: { A: { models: ['m'] }, B: { models: ['m'] } }, default: 'A/m' },
    turns: [{ line: 'hi' }, { line: 'again', select: 'B/m' }] },
  { name: 'ollama: native route, bucketed and sticky num_ctx, growth notice, over-window warning',
    fakes: { ollama: 'ndjson' }, config: { providers: { ollama: { type: 'openai', models: 'any' } }, default: 'ollama/m' },
    turns: [{ line: 'tiny' }, { line: 'x'.repeat(35000) }, { line: 'tiny' }, { line: 'y'.repeat(130000) }] },
  { name: 'auth failure does not fall back; fallback_on any does',
    fakes: { A: 's401', B: 'hello' },
    config: { providers: { A: { models: ['m'] }, B: { models: ['m'] } }, default: 'A/m',
      routes: { chat: { target: 'A/m', fallback: ['B/m'] }, any: { target: 'A/m', fallback: ['B/m'], fallback_on: 'any' } } },
    turns: [{ line: 'hi' }, { line: 'hi', task: 'any' }] },
  { name: 'retryable failures fall back in order: 429 then 503 then success',
    fakes: { A: 's429', B: 's503', C: 'hello' },
    config: { providers: { A: { models: ['m'] }, B: { models: ['m'] }, C: { models: ['m'] } }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m', 'C/m'] } } },
    turns: [{ line: 'hi' }] },
  { name: 'connection refused is a connect error and falls back',
    fakes: { A: 'closed', B: 'hello' },
    config: { providers: { A: { models: ['m'] }, B: { models: ['m'] } }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m'] } } },
    turns: [{ line: 'hi' }] },
  { name: 'provider error bodies, a redirect and a malformed frame each give one line',
    fakes: { A: 's404', B: 'redirect', C: 'malformed' },
    config: { providers: { A: { models: ['m'] }, B: { models: ['m'] }, C: { models: ['m'] } }, default: 'A/m' },
    turns: [{ line: 'hi' }, { line: 'hi', select: 'B/m' }, { line: 'hi', select: 'C/m' }] },
  { name: 'a cut after the first delta keeps the text and never re-sends',
    fakes: { A: 'cutAfterTwo', B: 'hello' },
    config: { providers: { A: { models: ['m'] }, B: { models: ['m'] } }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m'] } } },
    turns: [{ line: 'hi' }] },
  { name: 'a cut before any delta is a stream-cut error line',
    fakes: { A: 'cutBeforeAny' }, config: { providers: { A: { models: ['m'] } }, default: 'A/m' }, turns: [{ line: 'hi' }] },
  { name: 'a hang past timeout_ms is a timeout and falls back',
    fakes: { A: 'hang', B: 'hello' },
    config: { providers: { A: { models: ['m'], timeout_ms: 300 }, B: { models: ['m'] } }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m'] } } },
    turns: [{ line: 'hi' }] },
  { name: 'cancel mid-stream shows cancelled, never falls back, and the session keeps working',
    fakes: { A: 'firstHoldsThenHello', B: 'hello' },
    config: { providers: { A: { models: ['m'] }, B: { models: ['m'] } }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m'] } } },
    turns: [{ line: 'hi', abortAfter: 1 }, { line: 'next' }] },
  { name: 'boundary refusals at request time send nothing',
    fakes: { A: 'hello', B: 'hello' },
    config: { providers: { A: { models: 'any' }, B: { models: ['m'] }, C: { models: ['m'], base_url: 'https://api.anthropic.com/v1' }, D: { type: 'cli', models: ['m'], fake: 'A' } }, default: 'A/m',
      routes: { any: { target: 'A/claude-3-haiku', fallback: ['B/m'], fallback_on: 'any' } } },
    turns: [{ line: 'hi', select: 'A/openrouter/anthropic/claude-3.5-sonnet' }, { line: 'hi', select: 'C/m' }, { line: 'hi', select: 'D/m' }, { line: 'hi', task: 'any' }] },
  { name: 'a keyed provider sends its key; an unset key is no-key and sends nothing',
    fakes: { A: 'hello', B: 'hello' },
    config: { providers: { A: { models: ['m'], is_local: false, allow_insecure: true, api_key_env: 'PARITY_KEY' }, B: { models: ['m'], is_local: false, allow_insecure: true, api_key_env: 'PARITY_MISSING' } }, default: 'A/m' },
    env: { PARITY_KEY: KEY }, turns: [{ line: 'hi' }, { line: 'hi', select: 'B/m' }] },
  { name: 'task routes, a route with no candidates, an unknown provider and no selection',
    fakes: { A: 'hello', B: 'echoCount' },
    config: { providers: { A: { models: ['m'] }, B: { models: ['m'] } }, default: 'A/m', routes: { code: 'B/m', empty: { candidates: [] } } },
    mutate: (registry) => { registry.routes.set('ghost', { target: { provider: 'gone', model: 'm' }, candidates: null, fallback: [], fallbackOn: 'retryable', strategy: null }); },
    turns: [{ line: 'hi' }, { line: 'write x', task: 'code' }, { line: 'hi', task: 'empty' }, { line: 'hi', task: 'ghost' }, { line: 'hi', select: null }] },
  { name: 'circuit breaker: three failures open it, the skip is flashed once, the cooldown closes it',
    fakes: { A: 's503', B: 'hello' },
    config: { providers: { A: { models: ['m'] }, B: { models: ['m'] } }, default: 'A/m', routes: { chat: { target: 'A/m', fallback: ['B/m'] } } },
    turns: [{ line: 't0', now: 1000000 }, { line: 't1', now: 1000000 }, { line: 't2', now: 1000000 }, { line: 'skip', now: 1000000 }, { line: 'skip2', now: 1000000 }, { line: 'again', now: 1031000 }] },
  { name: 'every candidate behind an open breaker gives one server line',
    fakes: { A: 's503' }, config: { providers: { A: { models: ['m'] } }, default: 'A/m' },
    turns: [{ line: 't0', now: 5 }, { line: 't1', now: 5 }, { line: 't2', now: 5 }, { line: 'blocked', now: 5 }] },
  { name: 'cheapest and fastest strategies order the candidates',
    fakes: { A: 'hello', B: 'echoCount' },
    config: { providers: { A: { models: ['m'], is_local: false, allow_insecure: true, cost_per_mtok: { input: 2, output: 3 } }, B: { models: ['m'], is_local: false, allow_insecure: true, cost_per_mtok: 1 } }, default: 'A/m',
      routes: { cheap: { strategy: 'cheapest', candidates: ['A/m', 'B/m', 'B/m'] }, fast: { strategy: 'fastest', candidates: ['A/m', 'B/m'] } } },
    turns: [{ line: 'hi', task: 'cheap', now: 7 }, { line: 'hi', task: 'fast', now: 7 }] },
];

/** Runs one scenario through one backend and returns its transcript and the requests the fakes saw. */
async function runScenario(makeSource, scenario) {
  const fakes = {};
  const labels = new Map();
  for (const [label, kind] of Object.entries(scenario.fakes)) {
    if (kind === 'closed') {
      const port = await closedPort();
      fakes[label] = { port, url: `http://127.0.0.1:${port}/v1`, requests: [], close: async () => {} };
    } else {
      fakes[label] = await startFake(HANDLERS[kind]);
    }
    labels.set(String(fakes[label].port), label);
  }
  const scrub = (text) => String(text).replace(/127\.0\.0\.1:(\d+)/g, (m, port) => (labels.has(port) ? `<${labels.get(port)}>` : m));
  const dir = mkdtempSync(join(tmpdir(), 'aesop-driver-parity-'));
  dirs.push(dir);
  const config = structuredClone(scenario.config);
  for (const [id, raw] of Object.entries(config.providers)) {
    raw.type ??= 'openai-compatible';
    raw.is_local ??= true;
    raw.base_url ??= fakes[raw.fake ?? id].url;
    delete raw.fake;
  }
  const file = join(dir, 'aesop.config.json');
  writeFileSync(file, JSON.stringify(config));
  const registry = loadRegistry(file);
  scenario.mutate?.(registry);
  let selection = registry.defaultSelection ? { ...registry.defaultSelection } : { provider: null, model: null };
  const log = [];
  const source = makeSource(registry, () => selection, scenario.env ?? {}, {
    notify: (text) => log.push({ notify: scrub(text) }),
    flash: (text) => log.push({ flash: scrub(text) }),
    usage: (u) => log.push({ usage: u }),
  });
  try {
    for (const turn of scenario.turns) {
      if (turn.select === null) selection = { provider: null, model: null };
      else if (turn.select) { const at = turn.select.indexOf('/'); selection = { provider: turn.select.slice(0, at), model: turn.select.slice(at + 1) }; }
      if (turn.now !== undefined) globalThis.__AESOP_CHAT_NOW = () => turn.now;
      const controller = new AbortController();
      log.push({ turn: turn.line.length > 40 ? `${turn.line.slice(0, 8)}...(${turn.line.length})` : turn.line });
      let seen = 0;
      try {
        for await (const chunk of source(turn.line, { task: turn.task, signal: controller.signal })) {
          log.push({ delta: chunk });
          seen += 1;
          if (turn.abortAfter === seen) controller.abort();
        }
        log.push({ end: true });
      } catch (err) {
        log.push({ thrown: err.message });
      } finally {
        delete globalThis.__AESOP_CHAT_NOW;
      }
    }
  } finally {
    source.close?.();
    await sleep(20);
    for (const fake of Object.values(fakes)) await fake.close();
  }
  const requests = {};
  for (const [label, fake] of Object.entries(fakes)) {
    requests[label] = fake.requests.map((r) => ({
      method: r.method, url: r.url, body: r.body, authorization: r.headers.authorization ?? null,
      accept: r.headers.accept ?? null, contentType: r.headers['content-type'] ?? null, contentLength: r.headers['content-length'] ?? null,
    }));
  }
  return { log, requests };
}

const transcripts = new Map();
for (const scenario of SCENARIOS) {
  test(`parity: ${scenario.name}`, async () => {
    const viaHarness = await runScenario(harnessSource, scenario);
    const viaDriver = await runScenario(driverSource, scenario);
    transcripts.set(scenario.name, viaDriver);
    assert.ok(viaHarness.log.length > scenario.turns.length, 'the harness transcript is not empty');
    assert.deepEqual(viaDriver.log, viaHarness.log, 'transcript (deltas, system lines, usage, throws)');
    assert.deepEqual(viaDriver.requests, viaHarness.requests, 'requests received by the fake providers');
  });
}

// Absolute anchors on the DRIVER transcript, so two equally empty or equally wrong transcripts cannot pass.
test('parity anchors: the driver transcript says what the spec says', () => {
  const lines = (name) => transcripts.get(name).log.flatMap((e) => (e.notify ? [e.notify] : e.flash ? [`flash: ${e.flash}`] : e.thrown ? [`thrown: ${e.thrown}`] : []));
  const text = (name) => transcripts.get(name).log.filter((e) => e.delta !== undefined).map((e) => e.delta).join('|');
  const reqs = (name, label) => transcripts.get(name).requests[label];

  assert.equal(text(SCENARIOS[0].name), 'reply to 1 message(s)|reply to 3 message(s)|reply to 5 message(s)');
  assert.deepEqual(transcripts.get(SCENARIOS[1].name).log.filter((e) => e.usage).map((e) => e.usage), [
    { provider: 'A', total: 1500, prompt: 1200, completion: 300, cost: 0 },
    { provider: 'B', total: 1500, prompt: 1200, completion: 300, cost: (2 * 1200 + 3 * 300) / 1e6 },
  ]);
  assert.equal(text(SCENARIOS[3].name), 'a€b');
  assert.deepEqual(reqs(SCENARIOS[5].name, 'ollama').map((r) => [r.url, r.body.options.num_ctx]), [['/api/chat', 8192], ['/api/chat', 16384], ['/api/chat', 16384], ['/api/chat', 32768]]);
  assert.deepEqual(lines(SCENARIOS[5].name), [
    'growing model window to 16384 (Ollama reloads the model; this can take ~20 s)',
    'growing model window to 32768 (Ollama reloads the model; this can take ~20 s)',
    'warning: input ~37146 tokens exceeds the model window 32768; the start will be cut',
  ]);
  assert.deepEqual(lines(SCENARIOS[6].name), ['error: A: auth: HTTP 401: bad key here', 'error: A: auth: HTTP 401: bad key here', 'falling back: A -> B (auth)']);
  assert.deepEqual(lines(SCENARIOS[7].name), ['error: A: rate-limit: HTTP 429', 'falling back: A -> B (rate-limit)', 'error: B: server: HTTP 503', 'falling back: B -> C (server)']);
  assert.deepEqual(lines(SCENARIOS[8].name), ['error: A: connect: cannot reach A at http://<A> — is it running? (ECONNREFUSED)', 'falling back: A -> B (connect)']);
  assert.deepEqual(lines(SCENARIOS[10].name), ['stream interrupted from A']);
  assert.equal(text(SCENARIOS[10].name), 'one |two');
  assert.equal(reqs(SCENARIOS[10].name, 'B').length, 0);
  assert.deepEqual(lines(SCENARIOS[12].name), ['error: A: timeout: no response within 300 ms', 'falling back: A -> B (timeout)']);
  assert.deepEqual(lines(SCENARIOS[13].name), ['cancelled: A request aborted']);
  assert.equal(text(SCENARIOS[13].name), 'x|after');
  assert.equal(reqs(SCENARIOS[13].name, 'B').length, 0);
  assert.deepEqual(lines(SCENARIOS[14].name), [
    'error: A: refused: model "openrouter/anthropic/claude-3.5-sonnet" resolves to Claude',
    'error: C: refused: base_url host is an Anthropic host',
    'error: D: refused: provider type "cli" is command-style or Anthropic',
    'error: A: refused: model "claude-3-haiku" resolves to Claude',
    'falling back: A -> B (refused)',
  ]);
  assert.equal(reqs(SCENARIOS[14].name, 'A').length, 0);
  assert.equal(reqs(SCENARIOS[15].name, 'A')[0].authorization, `Bearer ${KEY}`);
  assert.deepEqual(lines(SCENARIOS[15].name), ['error: B: no-key: env PARITY_MISSING is unset']);
  assert.equal(reqs(SCENARIOS[15].name, 'B').length, 0);
  assert.deepEqual(lines(SCENARIOS[16].name), ['thrown: route empty has no target', 'error: gone: refused: unknown provider', 'thrown: no provider configured (see /help)']);
  assert.equal(lines(SCENARIOS[17].name).filter((l) => l.startsWith('flash: skipping A')).length, 1);
  assert.equal(reqs(SCENARIOS[17].name, 'A').length, 4, 'three failures, two skipped turns, one retry after the cooldown');
  assert.deepEqual(lines(SCENARIOS[18].name).slice(-2), ['flash: skipping A: circuit open after 3 failures, retry after cooldown', 'error: A: server: no candidate available, circuit open']);
  assert.equal(text(SCENARIOS[19].name), 'reply to 1 message(s)|hel|lo');
  for (const { log } of transcripts.values()) assert.ok(!JSON.stringify(log).includes(KEY), 'no key in any transcript');
});
