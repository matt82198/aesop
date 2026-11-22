// Seam parity (finding f-ce0779b1): `aesop ask` and `python driver/turn.py` are two entries to one turn
// engine, so the same input must reach the provider as the same request. Each test gives both entries the
// same stdin, the same --system text and the same prompt, captures the request a fake 127.0.0.1 provider
// receives from each, and compares them whole (method, path, JSON body). The message assembly is the
// driver's one builder: a system message, then one user message holding stdin and then the prompt. The
// only field allowed to describe the transport is `stream`, which both entries set to true.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { pythonCommand } from '../chat/driver_source.mjs';
import { sse, startFake } from './helpers/fake_openai_server.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const cliPath = join(root, 'bin', 'cli.js');
const turnPath = join(root, 'driver', 'turn.py');
const tmp = () => mkdtempSync(join(tmpdir(), 'aesop-ask-parity-'));

const SYSTEM = 'answer in one word';
const PROMPT = 'which fruit is named on line two?';
const STDIN = 'line one: apple\nline two: pineapple\n';

function run(command, args, { cwd, env, stdin }) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => { stdout += d; });
    child.stderr.on('data', (d) => { stderr += d; });
    child.on('error', (err) => resolve({ code: -1, stdout, stderr: `${stderr}${err.message}` }));
    child.on('close', (code) => resolve({ code, stdout, stderr }));
    child.stdin.on('error', () => { /* the child may exit before reading stdin */ });
    child.stdin.end(stdin);
  });
}

/** The one POST a provider received, reduced to what the provider acts on. */
function captured(fake) {
  const posts = fake.posts();
  assert.equal(posts.length, 1, 'exactly one request reaches the provider');
  return { method: posts[0].method, url: posts[0].url, body: posts[0].body };
}

/** Runs the same input through `aesop ask` and through `driver/turn.py`; returns both captured requests. */
async function bothEntries(providerId, raw, handler) {
  const dir = tmp();
  const home = tmp();
  const env = { ...process.env, HOME: home, USERPROFILE: home, AESOP_ACTIVITY_FEED: join(home, 'activity.jsonl') };
  delete env.AESOP_ASK_BACKEND;
  const askFake = await startFake(handler);
  const turnFake = await startFake(handler);
  try {
    const config = join(dir, 'aesop.config.json');
    writeFileSync(config, JSON.stringify({ providers: { [providerId]: { ...raw, base_url: askFake.url } }, default: `${providerId}/m1` }));
    const viaAsk = await run(process.execPath, [cliPath, 'ask', '--json', '--system', SYSTEM, PROMPT], { cwd: dir, env: { ...env, AESOP_CHAT_CONFIG: config }, stdin: STDIN });
    assert.equal(viaAsk.code, 0, viaAsk.stderr);
    const viaTurn = await run(pythonCommand(env), ['-I', turnPath, '--json', '--base-url', turnFake.url, '--model', `${providerId}/m1`, '--system', SYSTEM, PROMPT], { cwd: dir, env, stdin: STDIN });
    assert.equal(viaTurn.code, 0, viaTurn.stderr);
    assert.equal(JSON.parse(viaAsk.stdout).text, JSON.parse(viaTurn.stdout).text, 'both entries return the same answer');
    return { ask: captured(askFake), turn: captured(turnFake) };
  } finally {
    await askFake.close();
    await turnFake.close();
    rmSync(dir, { recursive: true, force: true });
    rmSync(home, { recursive: true, force: true });
  }
}

test('seam parity: ask and driver.turn send the same request for the same stdin, system and prompt', async () => {
  const { ask, turn } = await bothEntries('fake', { type: 'openai', models: 'any', is_local: true, timeout_ms: 5000 }, (req, body, res) => sse(res, ['pine', 'apple']));
  assert.deepEqual(ask, turn, 'the request captured from ask equals the one captured from driver.turn');
  // The shared assembly, stated once so a change to the builder is a visible decision.
  assert.deepEqual(turn.body.messages, [
    { role: 'system', content: SYSTEM },
    { role: 'user', content: `${STDIN.trimEnd()}\n\n${PROMPT}` },
  ]);
  assert.equal(ask.url, '/v1/chat/completions');
  assert.equal(ask.body.stream, true, 'streaming is transport metadata, present and equal on both entries');
  assert.doesNotMatch(JSON.stringify(ask.body), /context \(stdin\)/, 'no ask-only delimiter');
});

test('seam parity: on the Ollama native route both entries send the same request and the same window', async () => {
  const handler = (req, body, res) => {
    res.writeHead(200, { 'content-type': 'application/x-ndjson' });
    res.write(`${JSON.stringify({ message: { content: 'pineapple' }, done: false })}\n`);
    res.end(`${JSON.stringify({ message: { content: '' }, done: true, prompt_eval_count: 20, eval_count: 2 })}\n`);
  };
  const { ask, turn } = await bothEntries('ollama', { type: 'ollama', models: 'any', is_local: true, timeout_ms: 5000 }, handler);
  assert.deepEqual(ask, turn, 'the request captured from ask equals the one captured from driver.turn');
  assert.equal(ask.url, '/api/chat');
  assert.equal(ask.body.messages.length, 2);
  assert.equal(ask.body.messages[0].role, 'system');
  assert.equal(typeof ask.body.options.num_ctx, 'number');
});
