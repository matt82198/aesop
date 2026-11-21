// Claude/Anthropic model-id boundary for the chat harness (spec H-R2). Every model id that names Claude or
// Anthropic is refused for a non-orchestrator provider, including ids routed through a gateway. Loopback only.
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { assertProviderAllowed, harnessSource, loadRegistry, ProviderRefused, resolveSelection } from '../chat/harness.mjs';

const REFUSED_IDS = [
  'openrouter/anthropic/claude-3.5-sonnet',
  'anthropic/claude-x',
  'claude-3-opus',
  ' claude-x',
  'x/claude-foo',
  'anthropic.claude-v2',
  'us.anthropic.claude-3',
  'OpenRouter/Anthropic/Claude',
];
const ALLOWED_IDS = ['qwen3-coder:30b', 'qwen3.8:27b', 'llama3.1:8b', 'mistral-nemo', 'claudette-7b'];

const loopback = 'http://127.0.0.1:9/v1';

function startFake() {
  const requests = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      requests.push({ url: req.url, body });
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      res.end('data: [DONE]\n\n');
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({
    url: `http://127.0.0.1:${server.address().port}/v1`,
    requests,
    close: () => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }),
  })));
}

async function turn(source, line) {
  let text = '';
  for await (const c of source(line)) text += c;
  return text;
}

test('H-R2: every Claude/Anthropic model id is refused with ProviderRefused for a non-orchestrator provider', () => {
  for (const id of REFUSED_IDS) {
    assert.throws(
      () => assertProviderAllowed({ id: 'p', type: 'openai', base_url: loopback }, id),
      (err) => err instanceof ProviderRefused,
      `model id ${JSON.stringify(id)} must be refused`,
    );
  }
});

test('H-R2: non-Claude model ids and claude-as-a-substring-of-a-word are still allowed', () => {
  for (const id of ALLOWED_IDS) {
    assert.doesNotThrow(
      () => assertProviderAllowed({ id: 'p', type: 'openai', base_url: loopback }, id),
      `model id ${JSON.stringify(id)} must be allowed`,
    );
  }
});

test('H-R2: a refused Claude model id in a config models list marks the provider refused at load', () => {
  const dir = mkdtempSync(join(tmpdir(), 'chat-boundary-'));
  try {
    const cfgPath = join(dir, 'cfg.json');
    writeFileSync(cfgPath, JSON.stringify({ providers: {
      gw: { type: 'openai', base_url: loopback, models: ['qwen3-coder:30b', 'x/claude-foo'] },
      ok: { type: 'openai', base_url: loopback, models: ['qwen3-coder:30b'] },
    } }));
    const registry = loadRegistry(cfgPath);
    assert.ok(registry.providers.get('gw').refused, 'gateway provider with a claude model must be refused at load');
    assert.equal(registry.providers.get('ok').refused, null);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('H-R2: /model with a Claude id on a models:"any" provider is refused at request time, nothing sent', async () => {
  const fake = await startFake();
  try {
    const registry = loadRegistry(undefined);
    registry.providers.set('gw', { id: 'gw', refused: null, raw: { type: 'openai', base_url: fake.url, models: 'any', is_local: true } });
    for (const model of ['openrouter/anthropic/claude-3.5-sonnet', ' claude-x', 'anthropic.claude-v2']) {
      const picked = resolveSelection(registry, `gw/${model}`);
      assert.equal(picked.error, undefined, 'the selection itself is accepted; the gate runs at request time');
      const notes = [];
      const src = harnessSource(registry, () => picked, {}, { notify: (t) => notes.push(t) });
      const text = await turn(src, 'hi');
      assert.equal(text, '', `no reply text for refused model ${JSON.stringify(model)}`);
      assert.ok(notes.some((n) => /refused/.test(n)), `refusal must be visible for ${JSON.stringify(model)}`);
    }
    assert.equal(fake.requests.length, 0, 'no request may reach the gateway');
  } finally { await fake.close(); }
});
