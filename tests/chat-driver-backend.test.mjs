// `aesop chat` with AESOP_CHAT_BACKEND=driver: the real TUI entry (chat/cli.mjs runChat) on a fake terminal,
// with turns served by the real driver child (driver/turn.py --serve) against a fake provider on 127.0.0.1.
// The golden transcript comparison is tests/chat-driver-parity.test.mjs; this file proves the flag is wired:
// the driver backend is selected only by the flag, text streams into the frame, ctrl-c cancels, and the
// child is gone when the chat ends.
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { driverSource } from '../chat/driver_source.mjs';
import { loadRegistry } from '../chat/harness.mjs';
import { sse, startFake } from './helpers/fake_openai_server.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dirs = [];
process.on('exit', () => { for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* best effort */ } } });
// eslint-disable-next-line no-control-regex
const strip = (s) => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');
const delta = (text) => `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}\n\n`;

async function waitFor(predicate, what, ms = 15000) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (predicate()) return;
    await sleep(25);
  }
  assert.fail(`timed out waiting for ${what}`);
}

async function startChat({ config, backend }) {
  const { runChat } = await import(pathToFileURL(join(root, 'chat/cli.mjs')).href);
  const dir = mkdtempSync(join(tmpdir(), 'aesop-driver-backend-'));
  dirs.push(dir);
  const cfgPath = join(dir, 'aesop.config.json');
  writeFileSync(cfgPath, JSON.stringify(config));
  const stdin = Object.assign(new EventEmitter(), { isTTY: true, setRawMode() {}, resume() {}, pause() {} });
  const writes = [];
  const stdout = Object.assign(new EventEmitter(), { isTTY: true, columns: 100, rows: 40, write(s) { writes.push(String(s)); return true; } });
  const env = { LANG: 'en_US.UTF-8', NO_COLOR: '1', AESOP_CHAT_CONFIG: cfgPath, AESOP_CHAT_HISTORY: join(dir, 'h.json'), AESOP_STATE_ROOT: join(dir, 'state') };
  if (backend !== undefined) env.AESOP_CHAT_BACKEND = backend;
  const done = runChat({ argv: [], stdin, stdout, stderr: { write() {} }, env });
  const press = (str, key) => stdin.emit('keypress', str, key);
  const key = (name, extra = {}) => press(extra.sequence ?? '', { name, ctrl: false, meta: false, sequence: '', ...extra });
  const api = {
    text: () => strip(writes.join('')),
    last: () => strip(writes[writes.length - 1] ?? ''),
    type: (line) => { for (const c of line) press(c, undefined); key('return', { sequence: '\r' }); },
    ctrlC: () => key('c', { ctrl: true, sequence: '\x03' }),
    quit: async () => { api.ctrlC(); api.ctrlC(); return Promise.race([done, sleep(3000).then(() => 'timeout')]); },
  };
  await sleep(30);
  return api;
}

const prov = (url, extra = {}) => ({ type: 'openai-compatible', base_url: url, models: ['m'], is_local: true, ...extra });

test('driver backend: a turn streams from the driver child into the chat frame', async () => {
  const fake = await startFake((q, b, res) => sse(res, ['from ', 'the ', 'driver']));
  try {
    const chat = await startChat({ config: { providers: { A: prov(fake.url) }, default: 'A/m' }, backend: 'driver' });
    chat.type('hi there');
    await waitFor(() => /from the driver/.test(chat.last()), 'the reply in the frame');
    assert.equal(fake.posts().length, 1);
    assert.deepEqual(fake.posts()[0].body, { model: 'm', stream: true, messages: [{ role: 'user', content: 'hi there' }] });
    chat.type('again');
    await waitFor(() => fake.posts().length === 2, 'the second turn');
    assert.equal(fake.posts()[1].body.messages.length, 3, 'the second turn carries the first as context');
    assert.equal(await chat.quit(), 0);
  } finally { await fake.close(); }
});

test('driver backend: ctrl-c during a reply cancels the request and shows cancelled', async () => {
  const fake = await startFake((q, b, res, n) => {
    if (n === 1) { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(delta('partial')); } else sse(res, ['second reply']);
  });
  try {
    const chat = await startChat({ config: { providers: { A: prov(fake.url) }, default: 'A/m' }, backend: 'driver' });
    chat.type('hold');
    await waitFor(() => /partial/.test(chat.last()), 'the first delta');
    const t0 = Date.now();
    chat.ctrlC();
    await waitFor(() => /cancelled: A request aborted/.test(chat.text()), 'the cancelled line');
    await waitFor(() => fake.closed.length >= 1, 'the provider to see the connection close');
    assert.ok(fake.closed[0] - t0 < 2000, 'the provider saw the close within 2 s of ctrl-c');
    chat.type('next');
    await waitFor(() => /second reply/.test(chat.last()), 'a turn after the cancel');
    assert.equal(await chat.quit(), 0);
  } finally { await fake.close(); }
});

test('driver backend: failures are the same visible lines, and the boundary still refuses', async () => {
  const fake = await startFake((q, b, res) => { res.writeHead(503); res.end('x'); });
  try {
    const chat = await startChat({ config: { providers: { A: prov(fake.url), G: prov(fake.url, { models: 'any' }) }, default: 'A/m' }, backend: 'driver' });
    chat.type('hi');
    await waitFor(() => /error: A: server: HTTP 503/.test(chat.text()), 'the error line');
    chat.type('/model G/openrouter/anthropic/claude-3.5-sonnet');
    chat.type('hi');
    await waitFor(() => /error: G: refused: model "openrouter\/anthropic\/claude-3.5-sonnet" resolves to Claude/.test(chat.text()), 'the refusal line');
    assert.equal(fake.posts().length, 1, 'the refused turn sent nothing');
    assert.equal(await chat.quit(), 0);
  } finally { await fake.close(); }
});

test('driver backend is opt-in: unset and harness never start a child; a bogus value names driver', async () => {
  const fake = await startFake((q, b, res) => sse(res, ['plain harness']));
  const started = [];
  try {
    // The source factory is the only place a child can start; count starts through its spawn seam.
    const registry = loadRegistry(undefined);
    registry.providers.set('A', { id: 'A', refused: null, raw: prov(fake.url) });
    const source = driverSource(registry, () => ({ provider: null, model: null }), {}, { spawn: (...a) => { started.push(a); throw new Error('must not spawn'); } });
    await assert.rejects(async () => { for await (const c of source('hi')) void c; }, /no provider configured/);
    assert.equal(started.length, 0, 'a turn that cannot start never starts the child');

    const chat = await startChat({ config: { providers: { A: prov(fake.url) }, default: 'A/m' }, backend: 'harness' });
    chat.type('hi');
    await waitFor(() => /plain harness/.test(chat.last()), 'the harness reply');
    assert.equal(await chat.quit(), 0);

    const bogus = await startChat({ config: { providers: { A: prov(fake.url) }, default: 'A/m' }, backend: 'nope' });
    assert.match(bogus.text(), /error: AESOP_CHAT_BACKEND: unknown value "nope" \(use pipe, orchestrator, harness or driver\)/);
    assert.equal(await bogus.quit(), 0);
  } finally { await fake.close(); }
});

test('driver source: the child is one per session, survives a cancel, and is gone after close', async () => {
  const fake = await startFake((q, b, res) => sse(res, ['ok']));
  const children = [];
  const { spawn } = await import('node:child_process');
  try {
    const registry = loadRegistry(undefined);
    registry.providers.set('A', { id: 'A', refused: null, raw: prov(fake.url) });
    const source = driverSource(registry, () => ({ provider: 'A', model: 'm' }), {}, { spawn: (...a) => { const c = spawn(...a); children.push(c); return c; } });
    for (let i = 0; i < 3; i += 1) {
      let text = '';
      for await (const chunk of source(`turn ${i}`)) text += chunk;
      assert.equal(text, 'ok');
    }
    assert.equal(children.length, 1, 'three turns, one child');
    const exited = new Promise((resolve) => children[0].once('exit', resolve));
    source.close();
    await Promise.race([exited, sleep(5000).then(() => assert.fail('the driver child did not exit after close'))]);
    assert.notEqual(children[0].exitCode ?? children[0].signalCode, null, 'the child process has ended');
    let text = '';
    for await (const chunk of source('after close')) text += chunk;
    assert.equal(text, 'ok', 'a turn after close starts a fresh child');
    assert.equal(children.length, 2);
    source.close();
  } finally { await fake.close(); }
});
