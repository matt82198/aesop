// S1 SPEC for the generic auth and model-selection CLI surface (bin/cli.js verbs
// `auth` and `model`). Written before the implementation (auth/{store,providers,cli}.mjs)
// exists, so every test here is expected to FAIL until S2 lands.
//
// Contract under test (modeled on Claude Code auth):
//  - aesop auth login --provider <name> [--api-key-stdin]
//  - aesop auth status [--provider <name>]
//  - aesop auth logout [--provider <name>]
//  - aesop model list
//  - aesop model use <provider>/<model>
//  - aesop model current
//  - Precedence: an env credential (AESOP_API_KEY or the provider-named variable)
//    beats the stored credential; status reports source = env | store | none, never the secret.
//  - Secrets never reach stdout or stderr; status masks to the last 4 characters.
//  - Every verb is callable as a control message on stdin when stdout is not a TTY:
//    {"type":"control","op":"auth.status"} -> one JSON line on stdout, same fields, no secret.
//  - Exit codes: 0 ok, 2 bad usage or malformed input, 3 no credential for the selected provider.
//  - Seed registry: anthropic (env ANTHROPIC_API_KEY, default model claude-fable-5-1) and
//    local (no credential needed).
//
// Each test uses a fresh temp AESOP_CONFIG_DIR, a fresh HOME and a fresh cwd, and spawns
// bin/cli.js. No test touches the real user config or the real repo.
//
// Run: node --test tests/auth-cli.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLI = path.join(REPO_ROOT, 'bin', 'cli.js');

// Dummy credentials assembled at runtime so no literal looks like a real key.
const KEY = ['dummy', 'cred', 'value', 'WXYZ7890'].join('_');
const KEY_LAST4 = '7890';
const ENV_KEY = ['dummy', 'env', 'value', 'QRST4321'].join('_');
const ENV_KEY_LAST4 = '4321';
const CREDENTIAL_ENV_VARS = ['AESOP_API_KEY', 'ANTHROPIC_API_KEY', 'BENCH_API_KEY', 'OPENAI_API_KEY'];

/** Fresh sandbox: config dir is NOT pre-created (the implementation must create it). */
function makeSandbox(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-auth-cli-'));
  const sb = {
    root,
    cfg: path.join(root, 'config'),
    home: path.join(root, 'home'),
    cwd: path.join(root, 'cwd'),
  };
  fs.mkdirSync(sb.home);
  fs.mkdirSync(sb.cwd);
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return sb;
}

/** Run bin/cli.js with the given args. Credential env vars are cleared unless passed in `env`. */
function run(sb, args, { input = '', env = {} } = {}) {
  const childEnv = { ...process.env, HOME: sb.home, USERPROFILE: sb.home, AESOP_CONFIG_DIR: sb.cfg };
  for (const k of CREDENTIAL_ENV_VARS) delete childEnv[k];
  Object.assign(childEnv, env);
  const res = spawnSync(process.execPath, [CLI, ...args], {
    cwd: sb.cwd,
    env: childEnv,
    input,
    encoding: 'utf8',
    timeout: 30000,
  });
  // auth/model verbs must never fall through to the scaffolder (which copies a fleet into cwd
  // and exits 0). Without this guard, exit-0 assertions would pass against the unmodified CLI.
  const leftover = fs.readdirSync(sb.cwd);
  assert.deepEqual(leftover, [], `auth/model verbs must not scaffold into cwd; found: ${leftover.join(', ')}`);
  return { status: res.status, stdout: res.stdout ?? '', stderr: res.stderr ?? '' };
}

/**
 * Send one control message over the DECLARED control channel: `aesop pipe` with stream-json
 * on both sides. Bare `aesop` with no TTY is a byte-exact filter and never inspects stdin.
 */
function control(sb, msg, opts = {}) {
  const body = typeof msg === 'string' ? msg : JSON.stringify(msg);
  return run(sb, ['pipe', '--input-format', 'stream-json', '--output-format', 'stream-json'],
    { ...opts, input: body + '\n' });
}

/** Parse stdout as exactly one JSON line. Fails the test with the raw output if it is not. */
function singleJsonLine(stdout) {
  const lines = stdout.split(/\r?\n/).filter((l) => l.trim() !== '');
  assert.equal(lines.length, 1, `expected exactly one stdout line, got:\n${stdout}`);
  return JSON.parse(lines[0]);
}

/** Usage errors must be specific: exit 2 and not the top-level "unknown option/namespace" fallback. */
function assertUsageError(r) {
  assert.equal(r.status, 2, `expected exit 2, got ${r.status}\nstderr:\n${r.stderr}`);
  assert.notEqual(r.stderr.trim(), '', 'usage error must explain itself on stderr');
  assert.doesNotMatch(r.stderr, /unknown (option|namespace|verb|command)/i,
    'auth/model usage must be recognized as a verb, not rejected as an unknown option');
}

function assertNoSecret(r, secrets) {
  for (const s of secrets) {
    assert.ok(!r.stdout.includes(s), `secret leaked to stdout: ${s}`);
    assert.ok(!r.stderr.includes(s), `secret leaked to stderr: ${s}`);
  }
}

// ---------------------------------------------------------------------------
// auth login / status (human output)
// ---------------------------------------------------------------------------

test('auth login --provider anthropic --api-key-stdin stores the key from stdin and exits 0', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assertNoSecret(r, [KEY]);
});

test('auth status --provider anthropic after login reports source store and masks to last 4 characters', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const r = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.match(r.stdout, /\bstore\b/, `status must name the store as source:\n${r.stdout}`);
  assert.ok(r.stdout.includes(KEY_LAST4), `status must show the last 4 characters:\n${r.stdout}`);
  assertNoSecret(r, [KEY]);
});

test('auth status with no credential for anthropic reports source none and exits 0', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.match(r.stdout, /\bnone\b/, `status must report source none:\n${r.stdout}`);
});

test('auth status with no --provider lists the seeded providers', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'status']);
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.match(r.stdout, /anthropic/);
  assert.match(r.stdout, /local/);
});

test('auth status --provider local reports no credential needed and exits 0', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'status', '--provider', 'local']);
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.match(r.stdout, /\bnone\b/, `local provider must report source none:\n${r.stdout}`);
});

// ---------------------------------------------------------------------------
// auth status (control message)
// ---------------------------------------------------------------------------

test('control auth.status prints exactly one JSON line on stdout with no secret', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const r = control(sb, { type: 'control', op: 'auth.status' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  const obj = singleJsonLine(r.stdout);
  assert.equal(typeof obj, 'object');
  assert.ok(!r.stdout.includes(KEY), 'control status must never contain the secret');
  assert.ok(JSON.stringify(obj).includes(KEY_LAST4), 'control status must include the masked last 4');
});

test('control auth.status source field matches the human output source for the same state', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const human = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  const ctl = control(sb, { type: 'control', op: 'auth.status' });
  assert.equal(human.status, 0, `human stderr:\n${human.stderr}`);
  assert.equal(ctl.status, 0, `control stderr:\n${ctl.stderr}`);
  assert.match(human.stdout, /\bstore\b/);
  // The control output carries the same fields; the source value must be 'store' there too.
  assert.ok(JSON.stringify(singleJsonLine(ctl.stdout)).includes('"store"'),
    `control status must report source "store":\n${ctl.stdout}`);
});

test('control auth.status with no credential reports source none in one JSON line', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.status' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  const obj = singleJsonLine(r.stdout);
  assert.equal(typeof obj, 'object');
  assert.ok(JSON.stringify(obj).includes('"none"'), `expected source "none":\n${r.stdout}`);
});

test('control op reply echoes the request id back and carries the op envelope', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.status', id: 'req-42' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  const obj = singleJsonLine(r.stdout);
  assert.equal(obj.type, 'control', `reply type must be control:\n${r.stdout}`);
  assert.equal(obj.op, 'auth.status', `reply must name the op:\n${r.stdout}`);
  assert.equal(obj.id, 'req-42', `reply must echo the request id:\n${r.stdout}`);
  assert.equal(obj.ok, true, `reply must carry ok: true:\n${r.stdout}`);
  assert.equal(typeof obj.result, 'object', `reply must carry a result object:\n${r.stdout}`);
});

// ---------------------------------------------------------------------------
// precedence: env beats store
// ---------------------------------------------------------------------------

test('ANTHROPIC_API_KEY in env beats a stored credential: status reports source env', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const r = control(sb, { type: 'control', op: 'auth.status' }, { env: { ANTHROPIC_API_KEY: ENV_KEY } });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.ok(JSON.stringify(singleJsonLine(r.stdout)).includes('"env"'),
    `env credential must win and be reported as source env:\n${r.stdout}`);
  assertNoSecret(r, [KEY, ENV_KEY]);
});

test('AESOP_API_KEY in env beats a stored credential: status reports source env', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const r = run(sb, ['auth', 'status', '--provider', 'anthropic'], { env: { AESOP_API_KEY: ENV_KEY } });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.match(r.stdout, /\benv\b/, `AESOP_API_KEY must be reported as source env:\n${r.stdout}`);
  assert.ok(r.stdout.includes(ENV_KEY_LAST4), `env key must be masked to its last 4:\n${r.stdout}`);
  assertNoSecret(r, [KEY, ENV_KEY]);
});

test('ANTHROPIC_API_KEY in env alone (no store) reports source env and exits 0', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.status' }, { env: { ANTHROPIC_API_KEY: ENV_KEY } });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.ok(JSON.stringify(singleJsonLine(r.stdout)).includes('"env"'), `expected source env:\n${r.stdout}`);
  assert.ok(!r.stdout.includes(ENV_KEY), 'env secret must never be echoed');
});

// ---------------------------------------------------------------------------
// auth logout
// ---------------------------------------------------------------------------

test('auth logout --provider anthropic removes the stored credential so status reports none', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const out = run(sb, ['auth', 'logout', '--provider', 'anthropic']);
  assert.equal(out.status, 0, `logout stderr:\n${out.stderr}`);
  assertNoSecret(out, [KEY]);
  const st = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  assert.equal(st.status, 0, `stderr:\n${st.stderr}`);
  assert.match(st.stdout, /\bnone\b/, `after logout source must be none:\n${st.stdout}`);
});

test('auth logout with no --provider clears the stored credential for every provider', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const out = run(sb, ['auth', 'logout']);
  assert.equal(out.status, 0, `stderr:\n${out.stderr}`);
  const st = control(sb, { type: 'control', op: 'auth.status' });
  assert.equal(st.status, 0, `stderr:\n${st.stderr}`);
  assert.ok(JSON.stringify(singleJsonLine(st.stdout)).includes('"none"'),
    `after logout-all source must be none:\n${st.stdout}`);
});

test('auth logout with no stored credential is a no-op that exits 0', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'logout', '--provider', 'anthropic']);
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
});

// ---------------------------------------------------------------------------
// model list / use / current
// ---------------------------------------------------------------------------

test('model list exits 0 and names both seeded providers and the anthropic default model', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['model', 'list']);
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.match(r.stdout, /anthropic/);
  assert.match(r.stdout, /local/);
  assert.match(r.stdout, /claude-fable-5-1/);
});

test('control model.list prints exactly one JSON line that names the anthropic default model', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'model.list' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  singleJsonLine(r.stdout);
  assert.match(r.stdout, /claude-fable-5-1/);
});

test('model use anthropic/claude-fable-5-1 then model current reports the selection', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const use = run(sb, ['model', 'use', 'anthropic/claude-fable-5-1']);
  assert.equal(use.status, 0, `use stderr:\n${use.stderr}`);
  const cur = run(sb, ['model', 'current']);
  assert.equal(cur.status, 0, `stderr:\n${cur.stderr}`);
  assert.match(cur.stdout, /anthropic\/claude-fable-5-1/);
});

test('model use selection persists across processes (a fresh model current sees it)', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const use = run(sb, ['model', 'use', 'anthropic/claude-fable-5-1']);
  assert.equal(use.status, 0, `use stderr:\n${use.stderr}`);
  const cur = control(sb, { type: 'control', op: 'model.current' });
  assert.equal(cur.status, 0, `stderr:\n${cur.stderr}`);
  singleJsonLine(cur.stdout);
  assert.match(cur.stdout, /claude-fable-5-1/);
});

test('model use local/<model> needs no credential and exits 0', (t) => {
  const sb = makeSandbox(t);
  const list = run(sb, ['model', 'list']);
  assert.equal(list.status, 0, `list stderr:\n${list.stderr}`);
  const r = run(sb, ['model', 'use', 'local/default']);
  assert.equal(r.status, 0, `local provider must not require a credential; stderr:\n${r.stderr}`);
});

test('model use anthropic/<model> with no credential for anthropic exits 3', (t) => {
  // ASSUMPTION (flagged in hand-back): exit 3 is attached to model use for a provider that
  // needs a credential and has none. The brief does not name the verb that returns 3.
  const sb = makeSandbox(t);
  const r = run(sb, ['model', 'use', 'anthropic/claude-fable-5-1']);
  assert.equal(r.status, 3, `expected exit 3 (no credential), got ${r.status}\nstderr:\n${r.stderr}`);
  assertNoSecret(r, [KEY]);
});

test('model use anthropic/<model> with a stored credential exits 0 (contrast for the exit 3 case)', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const r = run(sb, ['model', 'use', 'anthropic/claude-fable-5-1']);
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
});

test('model use anthropic/<model> with an env credential exits 0', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['model', 'use', 'anthropic/claude-fable-5-1'], { env: { ANTHROPIC_API_KEY: ENV_KEY } });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assertNoSecret(r, [ENV_KEY]);
});

// ---------------------------------------------------------------------------
// exit 2: bad usage and malformed input
// ---------------------------------------------------------------------------

test('auth login without --provider is bad usage: exit 2 with a specific message', (t) => {
  const sb = makeSandbox(t);
  assertUsageError(run(sb, ['auth', 'login', '--api-key-stdin'], { input: KEY + '\n' }));
});

test('auth login with an unregistered provider is bad usage: exit 2', (t) => {
  const sb = makeSandbox(t);
  assertUsageError(run(sb, ['auth', 'login', '--provider', 'nosuchprovider', '--api-key-stdin'], { input: KEY + '\n' }));
});

test('auth with no verb is bad usage: exit 2', (t) => {
  const sb = makeSandbox(t);
  assertUsageError(run(sb, ['auth']));
});

test('model use with no argument is bad usage: exit 2', (t) => {
  const sb = makeSandbox(t);
  assertUsageError(run(sb, ['model', 'use']));
});

test('model use with a malformed selector (no provider/model slash) is bad usage: exit 2', (t) => {
  const sb = makeSandbox(t);
  assertUsageError(run(sb, ['model', 'use', 'claude-fable-5-1']));
});

test('control message with malformed JSON is malformed input: exit 2', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, '{"type":"control","op":');
  assert.equal(r.status, 2, `expected exit 2, got ${r.status}\nstderr:\n${r.stderr}`);
  assert.equal(r.stdout, '', `malformed input must write nothing to stdout:\n${r.stdout}`);
  assert.match(r.stderr, /^aesop-pipe:/, `malformed input must explain itself on stderr:\n${r.stderr}`);
});

test('control message with an unknown op is malformed input: exit 2', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.nosuchop' });
  assert.equal(r.status, 2, `expected exit 2, got ${r.status}\nstderr:\n${r.stderr}`);
  assert.match(r.stderr, /^aesop-pipe:/, `unknown op must produce one aesop-pipe: stderr line:\n${r.stderr}`);
  assert.match(r.stderr, /auth\.nosuchop/, `stderr must name the unknown op:\n${r.stderr}`);
});
