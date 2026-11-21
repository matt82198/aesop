// Ollama default provider, conversation context and error lines (fake OpenAI-compatible server on 127.0.0.1).
import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';
import { applyDefaultProvider, boundContext, harnessSource, loadRegistry, DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL } from '../chat/harness.mjs';

function startFake(handler) {
  const requests = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => { requests.push(JSON.parse(body || '{}')); handler(req, res, requests.length); });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({
    port: server.address().port, requests, close: () => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }),
  })));
}
const sse = (res, parts) => {
  res.writeHead(200, { 'content-type': 'text/event-stream' });
  for (const p of parts) res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: p } }] })}\n\n`);
  res.end('data: [DONE]\n\n');
};
const registryFor = (port, extra = {}) => {
  const r = loadRegistry(undefined);
  r.providers.set('ollama', { id: 'ollama', refused: null, raw: { type: 'openai', base_url: `http://127.0.0.1:${port}/v1`, models: 'any', is_local: true, ...extra } });
  return r;
};
async function turn(source, line) {
  let text = '';
  for await (const c of source(line)) text += c;
  return text;
}

test('default provider is loopback ollama with qwen3-coder:30b, only when no providers exist', () => {
  const r = loadRegistry(undefined);
  assert.equal(applyDefaultProvider(r), true);
  assert.equal(r.providers.get('ollama').raw.base_url, DEFAULT_OLLAMA_URL);
  assert.deepEqual(r.defaultSelection, { provider: 'ollama', model: DEFAULT_OLLAMA_MODEL });
  assert.equal(applyDefaultProvider(r), false);
});

test('SSE tokens stream in order and the request carries the selected model', async () => {
  const fake = await startFake((req, res) => sse(res, ['pine', 'apple']));
  try {
    const notes = [];
    const src = harnessSource(registryFor(fake.port), () => ({ provider: 'ollama', model: 'qwen3-coder:30b' }), {}, { notify: (t) => notes.push(t) });
    const chunks = [];
    for await (const c of src('hi')) chunks.push(c);
    assert.deepEqual(chunks, ['pine', 'apple']);
    assert.equal(fake.requests[0].model, 'qwen3-coder:30b');
    assert.equal(fake.requests[0].stream, true);
    assert.deepEqual(notes, []);
  } finally { await fake.close(); }
});

test('later turns send the earlier turns; a failed turn is not remembered', async () => {
  const fake = await startFake((req, res, n) => {
    if (n === 2) { res.writeHead(500, { 'content-type': 'application/json' }); res.end('{"error":{"message":"boom"}}'); return; }
    sse(res, [`reply${n}`]);
  });
  try {
    const src = harnessSource(registryFor(fake.port), () => ({ provider: 'ollama', model: 'm' }), {}, { notify: () => {} });
    await turn(src, 'first');
    await turn(src, 'second-fails');
    await turn(src, 'third');
    assert.deepEqual(fake.requests[0].messages, [{ role: 'user', content: 'first' }]);
    assert.deepEqual(fake.requests[2].messages, [
      { role: 'user', content: 'first' }, { role: 'assistant', content: 'reply1' }, { role: 'user', content: 'third' },
    ]);
  } finally { await fake.close(); }
});

test('boundContext keeps the newest messages, starts on a user message, honours both limits', () => {
  const convo = [];
  for (let i = 0; i < 30; i += 1) convo.push({ role: i % 2 ? 'assistant' : 'user', content: `m${i}` });
  const out = boundContext(convo, 5, 1000);
  assert.equal(out[0].role, 'user');
  assert.equal(out.at(-1).content, 'm29');
  assert.ok(out.length <= 5);
  const big = boundContext([{ role: 'user', content: 'a'.repeat(50) }, { role: 'assistant', content: 'b'.repeat(50) }], 20, 60);
  assert.deepEqual(big, []);
});

test('unreachable ollama gives a visible "cannot reach" line, never a silent echo', async () => {
  const fake = await startFake(() => {});
  const port = fake.port;
  await fake.close();
  const notes = [];
  const src = harnessSource(registryFor(port), () => ({ provider: 'ollama', model: 'm' }), {}, { notify: (t) => notes.push(t) });
  const text = await turn(src, 'hello');
  assert.equal(text, '');
  assert.equal(notes.length, 1);
  assert.match(notes[0], new RegExp(`cannot reach ollama at http://127\\.0\\.0\\.1:${port} — is it running\\?`));
});

test('unknown model shows the provider message; timeout shows a timeout line', async () => {
  const fake = await startFake((req, res, n) => {
    if (n === 1) { res.writeHead(404, { 'content-type': 'application/json' }); res.end('{"error":{"message":"model \\"nope\\" not found, try pulling it first"}}'); return; }
    // second request: never answer
  });
  try {
    const notes = [];
    const src = harnessSource(registryFor(fake.port, { timeout_ms: 150 }), () => ({ provider: 'ollama', model: 'nope' }), {}, { notify: (t) => notes.push(t) });
    await turn(src, 'a');
    assert.match(notes[0], /error: ollama: bad-response: HTTP 404: model "nope" not found/);
    await turn(src, 'b');
    assert.match(notes[1], /error: ollama: timeout: no response within 150 ms/);
  } finally { await fake.close(); }
});
