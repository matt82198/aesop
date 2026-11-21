// Ollama context window sizing: native /api/chat with options.num_ctx, and a visible warning when input exceeds the window.
import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';
import { harnessSource, loadRegistry, sizeOllamaContext } from '../chat/harness.mjs';

function startFake(handler) {
  const requests = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => { requests.push({ url: req.url, body: JSON.parse(body || '{}') }); handler(req, res); });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({
    port: server.address().port, requests, close: () => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }),
  })));
}
const frame = (o) => `${JSON.stringify(o)}\n`;
const ndjson = (res) => {
  res.writeHead(200, { 'content-type': 'application/x-ndjson' });
  res.write(frame({ message: { role: 'assistant', content: 'man' }, done: false }));
  res.write(frame({ message: { role: 'assistant', content: 'go' }, done: false }));
  res.end(frame({ message: { role: 'assistant', content: '' }, done: true, prompt_eval_count: 7, eval_count: 2 }));
};
const sse = (res) => {
  res.writeHead(200, { 'content-type': 'text/event-stream' });
  res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: 'ok' } }] })}\n\n`);
  res.end('data: [DONE]\n\n');
};
const registryFor = (id, port, extra = {}) => {
  const r = loadRegistry(undefined);
  r.providers.set(id, { id, refused: null, raw: { type: 'openai', base_url: `http://127.0.0.1:${port}/v1`, models: 'any', is_local: true, ...extra } });
  return r;
};
async function run(registry, id, line, notes = []) {
  const src = harnessSource(registry, () => ({ provider: id, model: 'm' }), {}, { notify: (t) => notes.push(t) });
  let text = '';
  for await (const c of src(line)) text += c;
  return text;
}

test('ollama uses native /api/chat and declares num_ctx sized to the prompt', async () => {
  const fake = await startFake((req, res) => ndjson(res));
  try {
    const text = await run(registryFor('ollama', fake.port), 'ollama', 'x'.repeat(35000));
    assert.equal(text, 'mango');
    assert.equal(fake.requests[0].url, '/api/chat');
    const n = fake.requests[0].body.options.num_ctx;
    assert.ok(n >= 10000 + 1024, `num_ctx ${n} must cover ~10000 prompt tokens plus a reply reserve`);
    assert.ok(n <= 32768);
  } finally { await fake.close(); }
});

test('num_ctx has a floor for short prompts and honors num_ctx_max and model_max_ctx', async () => {
  const fake = await startFake((req, res) => ndjson(res));
  try {
    await run(registryFor('ollama', fake.port), 'ollama', 'hi');
    assert.ok(fake.requests[0].body.options.num_ctx >= 4096);
    await run(registryFor('ollama', fake.port, { num_ctx_max: 8192 }), 'ollama', 'x'.repeat(70000));
    assert.equal(fake.requests[1].body.options.num_ctx, 8192);
    await run(registryFor('ollama', fake.port, { model_max_ctx: 6144 }), 'ollama', 'x'.repeat(70000));
    assert.equal(fake.requests[2].body.options.num_ctx, 6144);
  } finally { await fake.close(); }
});

test('oversized input warns before the reply, naming the window', async () => {
  const fake = await startFake((req, res) => ndjson(res));
  try {
    const notes = [];
    await run(registryFor('ollama', fake.port, { num_ctx_max: 8192 }), 'ollama', 'x'.repeat(70000), notes);
    assert.equal(notes.length, 1);
    assert.match(notes[0], /^warning: input ~20000 tokens exceeds the model window 8192; the start will be cut$/);
    const quiet = [];
    await run(registryFor('ollama', fake.port), 'ollama', 'short', quiet);
    assert.deepEqual(quiet, []);
  } finally { await fake.close(); }
});

test('non-ollama OpenAI-compatible providers are unaffected', async () => {
  const fake = await startFake((req, res) => sse(res));
  try {
    const notes = [];
    const text = await run(registryFor('other', fake.port), 'other', 'x'.repeat(200000), notes);
    assert.equal(text, 'ok');
    assert.equal(fake.requests[0].url, '/v1/chat/completions');
    assert.equal(fake.requests[0].body.options, undefined);
    assert.deepEqual(notes, []);
  } finally { await fake.close(); }
});

// ---- bucketed + sticky num_ctx (stops Ollama reloading the model on every ctx change) ----
const tokMsg = (tokens) => [{ role: 'user', content: 'x'.repeat(Math.round(tokens * 3.5)) }];

test('buckets: smallest of 8192/16384/32768 that fits estimate + reserve, at the edges', () => {
  assert.equal(sizeOllamaContext(tokMsg(10)).numCtx, 8192);
  assert.equal(sizeOllamaContext(tokMsg(8192 - 1024)).numCtx, 8192);
  assert.equal(sizeOllamaContext(tokMsg(8192 - 1024 + 1)).numCtx, 16384);
  assert.equal(sizeOllamaContext(tokMsg(16384 - 1024)).numCtx, 16384);
  assert.equal(sizeOllamaContext(tokMsg(16384 - 1024 + 1)).numCtx, 32768);
  const over = sizeOllamaContext(tokMsg(40000));
  assert.equal(over.numCtx, 32768);
  assert.equal(over.truncates, true);
});

test('buckets are filtered by num_ctx_max and model_max_ctx, and configurable via num_ctx_buckets', () => {
  const hi = [{ role: 'user', content: 'hi' }];
  assert.equal(sizeOllamaContext(hi, { num_ctx_buckets: [12000, 24000] }).numCtx, 12000);
  assert.equal(sizeOllamaContext(hi, { num_ctx_buckets: [32768] }).numCtx, 32768);
  assert.equal(sizeOllamaContext(tokMsg(10000), { num_ctx_buckets: [8192, 16384], num_ctx_max: 12000 }).numCtx, 8192);
  assert.equal(sizeOllamaContext(tokMsg(20000), { model_max_ctx: 16384 }).numCtx, 16384);
});

test('sticky: a long conversation sends a non-decreasing num_ctx and a short turn never shrinks it', async () => {
  const fake = await startFake((req, res) => ndjson(res));
  try {
    const reg = registryFor('ollama', fake.port);
    // 200 and 20000 chars fit 8192 (~5715 tokens + 1024); 60000 chars (~17143 tokens) needs 32768
    for (const n of [200, 20000, 5, 60000, 5]) await run(reg, 'ollama', 'x'.repeat(n));
    const seq = fake.requests.map((r) => r.body.options.num_ctx);
    assert.deepEqual(seq, [8192, 8192, 8192, 32768, 32768]);
  } finally { await fake.close(); }
});

test('sticky: growth then a short turn keeps the larger bucket', async () => {
  const fake = await startFake((req, res) => ndjson(res));
  try {
    const reg = registryFor('ollama', fake.port);
    await run(reg, 'ollama', 'x'.repeat(35000)); // ~10000 tokens -> 16384
    await run(reg, 'ollama', 'tiny');
    assert.deepEqual(fake.requests.map((r) => r.body.options.num_ctx), [16384, 16384]);
  } finally { await fake.close(); }
});

test('reload notice is emitted exactly when the bucket grows, not on first send or steady state', async () => {
  const fake = await startFake((req, res) => ndjson(res));
  try {
    const reg = registryFor('ollama', fake.port);
    const notes = [];
    await run(reg, 'ollama', 'tiny', notes);
    await run(reg, 'ollama', 'tiny', notes);
    assert.deepEqual(notes, []);
    await run(reg, 'ollama', 'x'.repeat(35000), notes);
    assert.deepEqual(notes, ['growing model window to 16384 (Ollama reloads the model; this can take ~20 s)']);
    await run(reg, 'ollama', 'tiny', notes);
    assert.equal(notes.length, 1);
  } finally { await fake.close(); }
});

test('num_ctx_buckets from provider config is honored on the wire', async () => {
  const fake = await startFake((req, res) => ndjson(res));
  try {
    await run(registryFor('ollama', fake.port, { num_ctx_buckets: [12288] }), 'ollama', 'hi');
    assert.equal(fake.requests[0].body.options.num_ctx, 12288);
  } finally { await fake.close(); }
});
