// Shared num_ctx fixture, Node side. The same file (tests/fixtures/num-ctx-cases.json) is run by
// tests/test_turn_context.py against driver/turn_context.py, so the harness and the driver cannot drift.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import http from 'node:http';
import test from 'node:test';
import { harnessSource, loadRegistry, sizeOllamaContext } from '../chat/harness.mjs';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/num-ctx-cases.json', import.meta.url), 'utf8'));

function messagesFor(c) {
  if (c.repeat) return [{ role: 'user', content: c.repeat.unit.repeat(c.repeat.count) }];
  return c.chars.map((n) => ({ role: 'user', content: 'x'.repeat(n) }));
}

function startFake() {
  const requests = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      requests.push({ url: req.url, body: JSON.parse(body || '{}') });
      res.writeHead(200, { 'content-type': 'application/x-ndjson' });
      res.write(`${JSON.stringify({ message: { role: 'assistant', content: 'ok' }, done: false })}\n`);
      res.end(`${JSON.stringify({ message: { role: 'assistant', content: '' }, done: true, prompt_eval_count: 1, eval_count: 1 })}\n`);
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({
    port: server.address().port, requests, close: () => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }),
  })));
}

test('num_ctx fixture: sizing cases match sizeOllamaContext', () => {
  assert.ok(fixture.sizing.length >= 10, 'the fixture has sizing cases');
  for (const c of fixture.sizing) {
    assert.deepEqual(sizeOllamaContext(messagesFor(c), c.raw), c.expect, c.name);
  }
});

test('num_ctx fixture: sequences match the num_ctx on the wire and the notices', async () => {
  assert.ok(fixture.sequences.length >= 3, 'the fixture has sequences');
  for (const seq of fixture.sequences) {
    const fake = await startFake();
    try {
      const registry = loadRegistry(undefined);
      registry.providers.set('ollama', { id: 'ollama', refused: null, raw: { type: 'openai', base_url: `http://127.0.0.1:${fake.port}/v1`, models: 'any', is_local: true, ...seq.raw } });
      for (const [i, step] of seq.steps.entries()) {
        const notes = [];
        // A fresh source per turn: no conversation context, so each request carries exactly this prompt.
        const source = harnessSource(registry, () => ({ provider: 'ollama', model: 'm' }), {}, { notify: (t) => notes.push(t) });
        let text = '';
        for await (const chunk of source('x'.repeat(step.chars))) text += chunk;
        assert.equal(text, 'ok', `${seq.name} step ${i}`);
        assert.equal(fake.requests[i].url, '/api/chat', `${seq.name} step ${i}`);
        assert.equal(fake.requests[i].body.options.num_ctx, step.num_ctx, `${seq.name} step ${i}`);
        assert.deepEqual(notes, step.notes, `${seq.name} step ${i}`);
      }
    } finally { await fake.close(); }
  }
});
