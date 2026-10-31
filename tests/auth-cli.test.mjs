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
//  - Secrets never reach stdout or stderr; no fragment of a key is printed (S5 round 2, R1: the
//    former last-4 display is retired; output shows provider, source and the fixed word set or none).
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
const ENV_KEY = ['dummy', 'env', 'value', 'QRST4321'].join('_');
// Shaped like a real key, used as an argv value in the R2 tests (never echoed by an error message).
const DUMMY_ARG = ['sk', 'ant', 'TESTDUMMY', '0123456789'].join('-');
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

/**
 * R1 (S5 round 2): no fragment of any credential reaches stdout or stderr. The 4-character suffix of
 * each key must be absent, and no `key=...` text may be printed. Output shows provider, source and a
 * fixed word (`set` or `none`) only. The earlier "last four characters are shown" contract is retired.
 */
function assertNoKeyFragment(r, keys) {
  const text = r.stdout + r.stderr;
  for (const k of keys) {
    const suffix = k.slice(-4);
    assert.ok(!text.includes(suffix), `key fragment "${suffix}" leaked:\n${text}`);
  }
  assert.doesNotMatch(text, /key=/, `output must not contain key=... text:\n${text}`);
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
  // R1: the last-4 display is retired; the status shows no fragment of the key.
  assertNoKeyFragment(r, [KEY]);
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
  // R1: the control reply carries no fragment of the key and marks the credential with the fixed word `set`.
  assertNoKeyFragment(r, [KEY]);
  assert.ok(JSON.stringify(obj).includes('"set"'), `control status must mark the stored credential as "set":\n${r.stdout}`);
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
  // R1: no fragment of the env key either (the last-4 display is retired).
  assertNoKeyFragment(r, [KEY, ENV_KEY]);
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

// ---------------------------------------------------------------------------
// S5 review items (red until S2 lands). Items 1-3 and 5 live here; item 4 is in stdio-pipe.test.mjs.
// ---------------------------------------------------------------------------

const AUTH_CLI = path.join(REPO_ROOT, 'auth', 'cli.mjs');
const SHORT_KEY_4 = 'abcd';
const SHORT_KEY_2 = 'Zq';
const LONG_KEY_32 = ['abcdefghijklmnop', 'qrstuvwxyz012345'].join('');
const LONG_KEY_32_TAIL = '2345';
const LONG_KEY_32_PREFIX = LONG_KEY_32.slice(0, 28);

/** The anthropic row from a single-line `auth status` JSON reply. */
function providerRow(stdout, name) {
  return singleJsonLine(stdout).providers.find((p) => p.provider === name);
}

/** Run auth/cli.mjs directly (not through bin/cli.js) with the same sandboxed env as run(). */
function spawnAuthCliDirect(sb, args, input) {
  const env = { ...process.env, HOME: sb.home, USERPROFILE: sb.home, AESOP_CONFIG_DIR: sb.cfg };
  for (const k of CREDENTIAL_ENV_VARS) delete env[k];
  return spawnSync(process.execPath, [AUTH_CLI, ...args], {
    cwd: sb.cwd,
    env,
    input,
    encoding: 'utf8',
    timeout: 30000,
  });
}

// ---------------------------------------------------------------------------
// item 1: short-key redaction
// ---------------------------------------------------------------------------

// R1 (S5 round 2): the login echo shows no key=... text, so the old key=*** display is gone too.
// The echo names the provider, the source and the fixed word `set`.
test('S5-1a login with a 4-char key never echoes it and shows no key=... text', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: SHORT_KEY_4 + '\n' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assertNoSecret(r, [SHORT_KEY_4]);
  assertNoKeyFragment(r, [SHORT_KEY_4]);
  assert.match(r.stdout, /\bset\b/, `login echo must mark the credential with the fixed word set:\n${r.stdout}`);
});

test('S5-1b login with a 2-char key never echoes it and shows no key=... text', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: SHORT_KEY_2 + '\n' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assertNoSecret(r, [SHORT_KEY_2]);
  assertNoKeyFragment(r, [SHORT_KEY_2]);
  assert.match(r.stdout, /\bset\b/, `login echo must mark the credential with the fixed word set:\n${r.stdout}`);
});

test('R1 login echo for a 32-char key shows no fragment of it and no key=... text', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assertNoSecret(r, [KEY]);
  assertNoKeyFragment(r, [KEY]);
  assert.match(r.stdout, /\bset\b/, `login echo must mark the credential with the fixed word set:\n${r.stdout}`);
});

test('S5-1c status after a 4-char login shows no tail of the key', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: SHORT_KEY_4 + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const st = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  assert.equal(st.status, 0, `stderr:\n${st.stderr}`);
  assertNoSecret(st, [SHORT_KEY_4]);
  assert.equal(providerRow(st.stdout, 'anthropic').keyTail, null,
    `a key shorter than 12 characters must carry no tail:\n${st.stdout}`);
});

// S5-1d changed by spec (S5 round 2, Matt: never print secrets): the assertions that a 32-char key
// shows its last four characters are deleted. A long key now shows no fragment at all, not even its tail.
test('S5-1d a 32-char key shows no fragment of it in status (R1, replaces the last-4 display)', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: LONG_KEY_32 + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const st = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  assert.equal(st.status, 0, `stderr:\n${st.stderr}`);
  assertNoSecret(st, [LONG_KEY_32, LONG_KEY_32_PREFIX]);
  assertNoKeyFragment(st, [LONG_KEY_32]);
  assert.doesNotMatch(st.stdout, new RegExp(LONG_KEY_32_TAIL), `status must not show the tail of a 32-char key:\n${st.stdout}`);
});

// ---------------------------------------------------------------------------
// item 2: no bare control mode
// ---------------------------------------------------------------------------

test('S5-2 auth/cli.mjs --control exits 2 with empty stdout and one stderr line naming aesop pipe', (t) => {
  const sb = makeSandbox(t);
  const res = spawnAuthCliDirect(sb, ['--control'], JSON.stringify({ type: 'control', op: 'auth.status' }) + '\n');
  assert.equal(res.status, 2, `expected exit 2, got ${res.status}\nstdout:\n${res.stdout}\nstderr:\n${res.stderr}`);
  assert.equal(res.stdout, '', `--control must print nothing to stdout:\n${res.stdout}`);
  const lines = String(res.stderr).split(/\r?\n/).filter((l) => l.trim() !== '');
  assert.equal(lines.length, 1, `expected exactly one stderr line, got:\n${res.stderr}`);
  assert.match(lines[0], /aesop pipe --input-format stream-json/,
    `the stderr line must name the declared channel:\n${res.stderr}`);
});

test('public_entry_auth_control_names_stream_json_channel', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', '--control']);
  assert.equal(r.status, 2, `expected exit 2, got ${r.status}\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
  assert.equal(r.stdout, '', `auth --control must print nothing to stdout:\n${r.stdout}`);
  assert.ok(r.stderr.includes('aesop pipe --input-format stream-json --output-format stream-json'),
    `the public entry must name the declared stream-json control channel on stderr:\n${r.stderr}`);
});

test('public_entry_auth_status_prints_no_key_material', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'status']);
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assertNoSecret(r, [KEY, ENV_KEY]);
  const reply = singleJsonLine(r.stdout);
  for (const row of reply.providers) {
    assert.equal(row.keyTail, null, `no credentials stored: keyTail must be null for ${row.provider}:\n${r.stdout}`);
  }
});

// ---------------------------------------------------------------------------
// item 3: UI-drivable ops over aesop pipe (stream-json both sides)
// ---------------------------------------------------------------------------

test('S5-3a control auth.login with api_key stores the key and replies with the status the verb reports', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.login', id: 'l1', provider: 'anthropic', api_key: KEY });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assertNoSecret(r, [KEY]);
  const reply = singleJsonLine(r.stdout);
  assert.equal(reply.type, 'control', `reply type:\n${r.stdout}`);
  assert.equal(reply.op, 'auth.login', `reply op:\n${r.stdout}`);
  assert.equal(reply.id, 'l1', `reply id:\n${r.stdout}`);
  assert.equal(reply.ok, true, `reply must be ok:\n${r.stdout}`);
  const verb = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  assert.equal(verb.status, 0, `verb stderr:\n${verb.stderr}`);
  assert.deepEqual(reply.result, JSON.parse(verb.stdout), 'auth.login result must equal what auth status reports after the op');
});

test('S5-3b control auth.logout replies ok with the status the verb reports after logout', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const r = control(sb, { type: 'control', op: 'auth.logout', id: 'o1', provider: 'anthropic' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assertNoSecret(r, [KEY]);
  const reply = singleJsonLine(r.stdout);
  assert.equal(reply.type, 'control');
  assert.equal(reply.op, 'auth.logout');
  assert.equal(reply.ok, true, `reply must be ok:\n${r.stdout}`);
  const verb = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  assert.equal(verb.status, 0, `verb stderr:\n${verb.stderr}`);
  assert.deepEqual(reply.result, JSON.parse(verb.stdout), 'auth.logout result must equal what auth status reports after the op');
});

test('S5-3c control model.use with a model selector replies ok with the selection model current reports', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const r = control(sb, { type: 'control', op: 'model.use', id: 'u1', model: 'anthropic/claude-fable-5-1' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  const reply = singleJsonLine(r.stdout);
  assert.equal(reply.type, 'control');
  assert.equal(reply.op, 'model.use');
  assert.equal(reply.ok, true, `reply must be ok:\n${r.stdout}`);
  const verb = run(sb, ['model', 'current']);
  assert.equal(verb.status, 0, `verb stderr:\n${verb.stderr}`);
  assert.deepEqual(reply.result, JSON.parse(verb.stdout), 'model.use result must equal what model current reports after the op');
});

// ---------------------------------------------------------------------------
// item 5: doc-grep tests RETIRED (R5). The former S5-5a/5b/5c tests read auth/CLAUDE.md and
// stdio/CLAUDE.md for phrases. A test must exercise behaviour, never grep docs, so they are deleted.
// Their intent is covered by behavioural tests: the short-key rule by S5-1a/1b/1d and R1 (no tail,
// no key=... text); the control channel by S5-2 and R3 (--control is refused, names the pipe channel).
// The unknown-op rule is exercised by "control message with an unknown op is malformed input" above.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// R1 (S5 round 2): control reply and login echo carry no key material; the fixed word is `set`.
// ---------------------------------------------------------------------------

test('R1 control auth.status for a 4-char stored key shows no fragment and marks it set', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: SHORT_KEY_4 + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const r = control(sb, { type: 'control', op: 'auth.status' });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  singleJsonLine(r.stdout);
  assertNoKeyFragment(r, [SHORT_KEY_4]);
  assert.ok(JSON.stringify(singleJsonLine(r.stdout)).includes('"set"'), `control status must mark the credential "set":\n${r.stdout}`);
});

// ---------------------------------------------------------------------------
// R2 (S5 round 2): error messages never echo argv values. Each case passes a key-shaped dummy value
// and asserts: exit 2, stderr explains itself, the value (and its TESTDUMMY core) appears in no output,
// and the message names what was expected (providers list, selector shape, or the flag name).
// ---------------------------------------------------------------------------

function assertArgvNotEchoed(r, label) {
  assert.equal(r.status, 2, `${label}: expected exit 2, got ${r.status}\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
  assert.notEqual(r.stderr.trim(), '', `${label}: usage error must explain itself on stderr`);
  const text = r.stdout + r.stderr;
  assert.ok(!text.includes(DUMMY_ARG), `${label}: argv value echoed in output:\n${text}`);
  assert.ok(!text.includes('TESTDUMMY'), `${label}: part of the argv value echoed in output:\n${text}`);
}

test('R2 auth login with an unexpected positional does not echo it and says keys come from stdin', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'login', DUMMY_ARG, '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assertArgvNotEchoed(r, 'auth login positional');
  assert.match(r.stderr, /stdin/, `message must say keys are read from stdin:\n${r.stderr}`);
});

test('R2 auth status with an unexpected positional does not echo it', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'status', DUMMY_ARG]);
  assertArgvNotEchoed(r, 'auth status positional');
  assert.match(r.stderr, /unexpected argument/, `message must name the problem:\n${r.stderr}`);
});

test('R2 auth logout with an unexpected positional does not echo it', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'logout', DUMMY_ARG]);
  assertArgvNotEchoed(r, 'auth logout positional');
  assert.match(r.stderr, /unexpected argument/, `message must name the problem:\n${r.stderr}`);
});

test('R2 auth login with a key-shaped unknown provider does not echo it and lists the known providers', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'login', '--provider', DUMMY_ARG, '--api-key-stdin'], { input: KEY + '\n' });
  assertArgvNotEchoed(r, 'auth login unknown provider');
  assert.ok(r.stderr.includes('anthropic') && r.stderr.includes('local'),
    `message must list the known providers:\n${r.stderr}`);
});

test('R2 auth status with a key-shaped unknown provider does not echo it and lists the known providers', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'status', '--provider', DUMMY_ARG]);
  assertArgvNotEchoed(r, 'auth status unknown provider');
  assert.ok(r.stderr.includes('anthropic') && r.stderr.includes('local'),
    `message must list the known providers:\n${r.stderr}`);
});

test('R2 control auth.status with a key-shaped unknown provider does not echo it and lists the known providers', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.status', provider: DUMMY_ARG });
  assertArgvNotEchoed(r, 'control unknown provider');
  assert.ok(r.stderr.includes('anthropic') && r.stderr.includes('local'),
    `message must list the known providers:\n${r.stderr}`);
});

test('R2 auth login with a key-shaped unknown flag does not echo the value and names the flag', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', 'login', '--provider', 'anthropic', `--api-key=${DUMMY_ARG}`], { input: KEY + '\n' });
  assertArgvNotEchoed(r, 'unknown flag');
  assert.match(r.stderr, /--api-key/, `message must name the flag without its value:\n${r.stderr}`);
});

test('R2 model use with a key-shaped malformed selector does not echo it and names the selector shape', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['model', 'use', DUMMY_ARG]);
  assertArgvNotEchoed(r, 'malformed selector');
  assert.ok(r.stderr.includes('<provider>/<model>'), `message must name the selector shape:\n${r.stderr}`);
});

test('R2 model use with a key-shaped unknown model does not echo it and lists the provider models', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['model', 'use', `anthropic/${DUMMY_ARG}`]);
  assertArgvNotEchoed(r, 'unknown model');
  assert.ok(r.stderr.includes('claude-fable-5-1'), `message must list the anthropic models:\n${r.stderr}`);
});

test('R2 control op with a key-shaped unknown op does not echo it', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: DUMMY_ARG });
  assert.equal(r.status, 2, `expected exit 2, got ${r.status}\nstderr:\n${r.stderr}`);
  assert.equal(r.stdout, '', `unknown op must write nothing to stdout:\n${r.stdout}`);
  assert.ok(!(r.stdout + r.stderr).includes(DUMMY_ARG), `argv value echoed in output:\n${r.stderr}`);
  assert.ok(!(r.stdout + r.stderr).includes('TESTDUMMY'), `part of the argv value echoed in output:\n${r.stderr}`);
  assert.match(r.stderr, /^aesop-pipe:/, `unknown op must produce one aesop-pipe: stderr line:\n${r.stderr}`);
});

test('R2 auth with a key-shaped unknown verb does not echo it and names the verbs', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['auth', DUMMY_ARG]);
  assertArgvNotEchoed(r, 'unknown auth verb');
  assert.ok(r.stderr.includes('login') && r.stderr.includes('status') && r.stderr.includes('logout'),
    `message must name the auth verbs:\n${r.stderr}`);
});

test('R2 model with a key-shaped unknown verb does not echo it and names the verbs', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['model', DUMMY_ARG]);
  assertArgvNotEchoed(r, 'unknown model verb');
  assert.ok(r.stderr.includes('list') && r.stderr.includes('use') && r.stderr.includes('current'),
    `message must name the model verbs:\n${r.stderr}`);
});

// ---------------------------------------------------------------------------
// R3 (S5 round 2): top-level `--control` is refused and names the declared channel. The scaffold
// help must not be printed.
// ---------------------------------------------------------------------------

test('R3 top-level node bin/cli.js --control exits non-zero, names the stream-json pipe, and prints no scaffold help', (t) => {
  const sb = makeSandbox(t);
  const r = run(sb, ['--control'], { input: JSON.stringify({ type: 'control', op: 'auth.status' }) + '\n' });
  assert.notEqual(r.status, 0, `top-level --control must exit non-zero, got ${r.status}\nstdout:\n${r.stdout}`);
  assert.ok(r.stderr.includes('aesop pipe --input-format stream-json --output-format stream-json'),
    `stderr must name the declared stream-json channel:\n${r.stderr}`);
  assert.doesNotMatch(r.stdout + r.stderr, /Show this help message/,
    `the scaffold help must not be printed for --control:\n${r.stdout}${r.stderr}`);
});

// ---------------------------------------------------------------------------
// R4 (S5 round 2): `auth logout` clears the current model selection. After it, `model current`
// reports no selection, in the same shape as a fresh store.
// ---------------------------------------------------------------------------

test('R4 auth logout clears the current model selection so model current reports none', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const use = run(sb, ['model', 'use', 'anthropic/claude-fable-5-1']);
  assert.equal(use.status, 0, `use stderr:\n${use.stderr}`);
  const out = run(sb, ['auth', 'logout']);
  assert.equal(out.status, 0, `logout stderr:\n${out.stderr}`);
  // Non-TTY stdout: model current prints the JSON object (no "no model selected" text, which is TTY-only).
  const cur = run(sb, ['model', 'current']);
  assert.equal(cur.status, 0, `stderr:\n${cur.stderr}`);
  const obj = singleJsonLine(cur.stdout);
  assert.equal(obj.selector, null, `after logout model current must report no selection:\n${cur.stdout}`);
  assert.equal(obj.provider, null, `after logout the provider must be null:\n${cur.stdout}`);
  assert.equal(obj.model, null, `after logout the model must be null:\n${cur.stdout}`);
  const ctl = control(sb, { type: 'control', op: 'model.current' });
  assert.equal(ctl.status, 0, `stderr:\n${ctl.stderr}`);
  const ctlEnv = singleJsonLine(ctl.stdout);
  assert.equal(ctlEnv.ok, true, `control reply must be ok:\n${ctl.stdout}`);
  assert.equal(ctlEnv.op, 'model.current', `control reply must name the model.current op:\n${ctl.stdout}`);
  assert.equal(ctlEnv.result.selector, null, `after logout the control selector must be null:\n${ctl.stdout}`);
});

test('R4 auth logout --provider anthropic clears a selection that names that provider', (t) => {
  const sb = makeSandbox(t);
  const login = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: KEY + '\n' });
  assert.equal(login.status, 0, `login stderr:\n${login.stderr}`);
  const use = run(sb, ['model', 'use', 'anthropic/claude-fable-5-1']);
  assert.equal(use.status, 0, `use stderr:\n${use.stderr}`);
  const out = run(sb, ['auth', 'logout', '--provider', 'anthropic']);
  assert.equal(out.status, 0, `logout stderr:\n${out.stderr}`);
  const cur = run(sb, ['model', 'current']);
  assert.equal(cur.status, 0, `stderr:\n${cur.stderr}`);
  assert.equal(singleJsonLine(cur.stdout).selector, null,
    `after logout of the selected provider model current must report no selection:\n${cur.stdout}`);
});
