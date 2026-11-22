import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import test from 'node:test';
import { runAsk } from '../chat/ask.mjs';
import { startFake } from './helpers/fake_openai_server.mjs';

const receipt = { id: 'valid', sha: 'abc', shell: 'ps', scenario: 'exit during held stream', exit: 0, seconds: 3 };

async function fixture(t) {
  let reply = '"valid"';
  const server = await startFake((_req, _body, res) => {
    res.writeHead(200, { 'content-type': 'application/x-ndjson' });
    res.end(JSON.stringify({ message: { content: reply }, done: true }) + '\n');
  });
  t.after(() => server.close());
  const dir = mkdtempSync(join(tmpdir(), 'proof-receipt-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const config = join(dir, 'config.json');
  writeFileSync(config, JSON.stringify({ providers: {
    fake: { type: 'ollama', base_url: server.url, models: 'any', is_local: true },
  } }));
  async function ask(menu, answer, flags = []) {
    reply = answer;
    let output = '', error = '';
    const code = await runAsk({
      argv: ['--model', 'fake/test', '--proof-receipt', ...flags, 'select proof'],
      stdin: Readable.from([JSON.stringify({ data: { sha: 'abc', shell: 'ps', menu } })]),
      stdout: { write(s) { output += s; } }, stderr: { write(s) { error += s; } },
      env: { AESOP_CHAT_CONFIG: config, AESOP_ACTIVITY_FEED: join(dir, 'feed') },
    });
    return { code, output, error };
  }
  return { server, ask };
}

test('proof receipt sends only eligible IDs and NONE as a native scalar enum', async (t) => {
  const { ask, server } = await fixture(t);
  const others = [{ seconds: 3.001 }, { seconds: null }, { seconds: -1 }, { seconds: '1' },
    { exit: 1 }, { exit: null, seconds: null }, { timed_out: true }, { ok: false },
    { sha: 'other' }, { shell: 'bash' }, { scenario: 'idle' }];
  const menu = [receipt, { ...receipt, id: 'instant', seconds: 0 },
    ...others.map((change, i) => ({ ...receipt, id: 'excluded-' + i, ...change }))];
  const result = await ask(menu, '"valid"');
  assert.equal(result.code, 0, result.error);
  assert.equal(result.output, '"valid"\n');
  const body = server.posts()[0].body;
  assert.deepEqual(body.format, { type: 'string', enum: ['valid', 'instant', 'NONE'] });
  assert.doesNotMatch(JSON.stringify(body.messages), /excluded-/);
  assert.match(body.messages.at(-1).content, /instant/);
});

test('proof receipt rejects wrapped, unlisted and malformed answers without emitting a selection', async (t) => {
  const { ask } = await fixture(t);
  for (const answer of ['{"id":"valid"}', '["valid"]', '"unknown"', 'valid', 'null', '"valid" "NONE"']) {
    const result = await ask([receipt], answer);
    assert.equal(result.code, 4, result.error);
    assert.equal(result.output, '');
  }
  const result = await ask([receipt], '{"id":"valid"}', ['--json']);
  assert.equal(result.code, 4);
  assert.equal(JSON.parse(result.output).ok, false);
  assert.equal(JSON.parse(result.output).text, '');
});

test('proof receipt admits only NONE when no receipt qualifies', async (t) => {
  const { ask, server } = await fixture(t);
  const menu = [{ ...receipt, exit: null, seconds: null }];
  const result = await ask(menu, '"NONE"');
  assert.equal(result.code, 0, result.error);
  assert.equal(result.output, '"NONE"\n');
  assert.deepEqual(server.posts()[0].body.format.enum, ['NONE']);
  assert.equal((await ask(menu, '"valid"')).code, 4);
});

test('ambiguous or malformed menus are rejected before a model request', async (t) => {
  const { ask, server } = await fixture(t);
  for (const menu of [null, [receipt, { ...receipt, exit: 1 }], [{ ...receipt, id: 'NONE' }], [null]]) {
    assert.equal((await ask(menu, '"NONE"')).code, 2);
  }
  assert.equal(server.posts().length, 0);
});
