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
import { CONTROL_CHANNEL_MESSAGE } from '../stdio/pipe.mjs';

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
  // Contract (stdio/CLAUDE.md:43, ruling R-DECIDE-1): a control record without an `id` is a
  // FramingError. Every object record gets an id here unless the caller supplied one.
  const framed = typeof msg === 'object' && msg !== null && !('id' in msg) ? { ...msg, id: 'ctl' } : msg;
  const body = typeof framed === 'string' ? framed : JSON.stringify(framed);
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
 * R1 (S5 round 2): no fragment of any credential reaches stdout or stderr. The 4-character prefix and
 * the 4-character suffix of each key must be absent, the key length must not be printed as a bare
 * number, and no `key=...` text may be printed. Output shows provider, source and a fixed word (`set`
 * or `none`) only. The earlier "last four characters are shown" contract is retired.
 */
function assertNoKeyFragment(r, keys) {
  const text = r.stdout + r.stderr;
  for (const k of keys) {
    const prefix = k.slice(0, 4);
    const suffix = k.slice(-4);
    assert.ok(!text.includes(prefix), `key prefix "${prefix}" leaked:\n${text}`);
    assert.ok(!text.includes(suffix), `key fragment "${suffix}" leaked:\n${text}`);
    assert.doesNotMatch(text, new RegExp(`(?<![0-9])${k.length}(?![0-9])`),
      `key length ${k.length} printed as a bare number:\n${text}`);
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

test('auth status --provider anthropic after login reports source store and prints no key fragment (no last-4 masking)', (t) => {
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

// R-CONTROL-1 (ruling, event store): a rejected op is answered by an error envelope and the pipe continues,
// exit 0; only framing faults exit 2. Expectation unchanged from the S5 item 5 contract.
test('control message with an unknown op answers one error envelope (no op field, error "unknown op"), exit 0, op name absent from stdout and stderr', (t) => {
  const sb = makeSandbox(t);
  const probe = 'auth.nosuchop';
  const r = control(sb, { type: 'control', op: probe, id: 'u1' });
  assert.equal(r.status, 0, `an unknown op is an op failure, not malformed input: exit 0 expected; stderr:\n${r.stderr}`);
  assert.equal(r.stderr.trim(), '', 'enveloped rejection leaves stderr empty (R-CONTROL-1: op failures are answered on stdout, not stderr)');
  const envelope = singleJsonLine(r.stdout);
  assert.equal(envelope.type, 'control', `envelope type:\n${r.stdout}`);
  assert.equal(envelope.id, 'u1', `envelope must carry the request id:\n${r.stdout}`);
  assert.equal(envelope.ok, false, `envelope must report ok:false:\n${r.stdout}`);
  assert.equal(envelope.error, 'unknown op', `error text must be exactly "unknown op":\n${r.stdout}`);
  assert.ok(!('op' in envelope), `unknown-op envelope must omit the op field:\n${r.stdout}`);
  assert.ok(!(r.stdout + r.stderr).includes(probe), `caller-supplied op name must not appear on stdout or stderr:\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
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
  const res = spawnAuthCliDirect(sb, ['--control'], JSON.stringify({ type: 'control', op: 'auth.status', id: 'ctl' }) + '\n');
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

// R-CONTROL-1 (ruling, event store): a rejected argument on a known op is an envelope {ok:false}, exit 0.
test('R2 control auth.status with a key-shaped unknown provider answers one error envelope (op kept, ok false), exit 0, probe absent from stdout and stderr', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.status', id: 'p1', provider: DUMMY_ARG });
  assert.equal(r.status, 0, `a rejected argument on a known op is an op failure, not malformed framing: exit 0 expected; stderr:\n${r.stderr}`);
  const envelope = singleJsonLine(r.stdout);
  assert.equal(envelope.type, 'control', `envelope type:\n${r.stdout}`);
  assert.equal(envelope.id, 'p1', `envelope must carry the request id:\n${r.stdout}`);
  assert.equal(envelope.ok, false, `envelope must report ok:false:\n${r.stdout}`);
  assert.equal(envelope.op, 'auth.status', `known op must be kept in the envelope:\n${r.stdout}`);
  assert.equal(typeof envelope.error, 'string', `envelope must carry an error string:\n${r.stdout}`);
  assert.ok(envelope.error.length > 0, `error string must not be empty:\n${r.stdout}`);
  const text = r.stdout + r.stderr;
  assert.ok(!text.includes(DUMMY_ARG), `key-shaped provider probe echoed in output:\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
  assert.ok(!text.includes('TESTDUMMY'), `R-NOECHO-1 (tests assert absence, never exact echo): core fragment of the provider probe echoed in output:\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
  assert.ok(!envelope.error.includes(DUMMY_ARG),
    `error string must not echo the probe:\n${r.stdout}`);
  assert.ok(envelope.error.includes('anthropic') && envelope.error.includes('local'),
    `envelope error must list the known providers:\n${r.stdout}`);
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

// R-CONTROL-1 (ruling, event store): an unknown op is an op failure answered by an envelope, exit 0.
test('R2 control key-shaped unknown op answers one error envelope (no op field, error "unknown op"), exit 0, probe absent from stdout and stderr', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: DUMMY_ARG, id: 'k1' });
  assert.equal(r.status, 0, `an unknown op is an op failure: exit 0 expected; stderr:\n${r.stderr}`);
  assert.equal(r.stderr.trim(), '', 'enveloped rejection leaves stderr empty (R-CONTROL-1: op failures are answered on stdout, not stderr)');
  const envelope = singleJsonLine(r.stdout);
  assert.equal(envelope.type, 'control', `envelope type:\n${r.stdout}`);
  assert.equal(envelope.id, 'k1',`envelope must carry the request id:\n${r.stdout}`);
  assert.equal(envelope.ok, false, `envelope must report ok:false:\n${r.stdout}`);
  assert.equal(envelope.error, 'unknown op', `error text must be exactly "unknown op":\n${r.stdout}`);
  assert.ok(!('op' in envelope), `unknown-op envelope must omit the op field:\n${r.stdout}`);
  assert.ok(!(r.stdout + r.stderr).includes(DUMMY_ARG), `key-shaped op probe echoed in output:\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
  assert.ok(!(r.stdout + r.stderr).includes('TESTDUMMY'), `R-NOECHO-1 (tests assert absence, never exact echo): core fragment of the op probe echoed in output:\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
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
  const r = run(sb, ['--control'], { input: JSON.stringify({ type: 'control', op: 'auth.status', id: 'ctl' }) + '\n' });
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

// ---------------------------------------------------------------------------
// S5 review, item 2c: an unknown all-lowercase flag is reported as an unknown flag, never echoed.
// ---------------------------------------------------------------------------

test('S5-2c auth status with an unknown all-lowercase flag says unknown flag and does not echo the name', (t) => {
  const sb = makeSandbox(t);
  const probe = ['vmqkzthe', 'wrlbxonj', 'ufgyaspc', 'dihqtmzv'].join('');
  const r = run(sb, ['auth', 'status', `--${probe}`]);
  assert.equal(r.status, 2, `expected exit 2, got ${r.status}\nstderr:\n${r.stderr}`);
  assert.ok(!(r.stdout + r.stderr).includes(probe), `flag name echoed in output:\n${r.stderr}`);
  assert.match(r.stderr, /unknown flag/, `message must say unknown flag:\n${r.stderr}`);
});

// ---------------------------------------------------------------------------
// S5 review, item 3: a bare --control on any non-top-level path names the declared channel.
// ---------------------------------------------------------------------------

const CONTROL_CHANNEL = 'aesop pipe --input-format stream-json --output-format stream-json';

function assertControlChannelNamed(r, invocation) {
  assert.equal(r.status, 2, `${invocation}: expected exit 2, got ${r.status}\nstderr:\n${r.stderr}`);
  assert.ok(r.stderr.includes(CONTROL_CHANNEL), `${invocation}: stderr must name the declared channel:\n${r.stderr}`);
}

test('S5-3a aesop auth status --control exits 2 and names the stream-json channel', (t) => {
  const sb = makeSandbox(t);
  assertControlChannelNamed(run(sb, ['auth', 'status', '--control']), 'auth status --control');
});

test('S5-3b aesop auth login --control exits 2 and names the stream-json channel', (t) => {
  const sb = makeSandbox(t);
  assertControlChannelNamed(run(sb, ['auth', 'login', '--control']), 'auth login --control');
});

test('S5-3c aesop model list --control exits 2 and names the stream-json channel', (t) => {
  const sb = makeSandbox(t);
  assertControlChannelNamed(run(sb, ['model', 'list', '--control']), 'model list --control');
});

test('S5-3d aesop model current --control exits 2 and names the stream-json channel', (t) => {
  const sb = makeSandbox(t);
  assertControlChannelNamed(run(sb, ['model', 'current', '--control']), 'model current --control');
});

test('S5-3e aesop pipe --control exits 2 and names the stream-json channel', (t) => {
  const sb = makeSandbox(t);
  assertControlChannelNamed(run(sb, ['pipe', '--control']), 'pipe --control');
});

// R3 (S5 round 5, decided): `--control` in ANY form on ANY bin path exits 2 and prints exactly one
// stderr line equal to CONTROL_CHANNEL_MESSAGE (imported from stdio/pipe.mjs), with no `error: ` or
// `aesop-pipe: ` prefix and nothing on stdout.
function assertExactControlLine(r, invocation) {
  assert.equal(r.status, 2, `${invocation}: expected exit 2, got ${r.status}\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
  assert.equal(r.stdout, '', `${invocation}: stdout must be empty:\n${r.stdout}`);
  const lines = r.stderr.replace(/\r?\n$/, '').split(/\r?\n/);
  assert.equal(lines.length, 1, `${invocation}: stderr must be exactly one line, got:\n${r.stderr}`);
  assert.equal(lines[0], CONTROL_CHANNEL_MESSAGE, `${invocation}: stderr line must equal CONTROL_CHANNEL_MESSAGE exactly:\n${r.stderr}`);
}

test('R3a bare --control exits 2 with exactly the CONTROL_CHANNEL_MESSAGE line', (t) => {
  const sb = makeSandbox(t);
  assertExactControlLine(run(sb, ['--control']), 'bare --control');
});

test('R3b bare --control=anything exits 2 with exactly the CONTROL_CHANNEL_MESSAGE line', (t) => {
  const sb = makeSandbox(t);
  assertExactControlLine(run(sb, ['--control=x']), 'bare --control=x');
});

test('R3c auth status --control=x exits 2 with exactly the CONTROL_CHANNEL_MESSAGE line', (t) => {
  const sb = makeSandbox(t);
  assertExactControlLine(run(sb, ['auth', 'status', '--control=x']), 'auth status --control=x');
});

test('R3d model list --control=x exits 2 with exactly the CONTROL_CHANNEL_MESSAGE line', (t) => {
  const sb = makeSandbox(t);
  assertExactControlLine(run(sb, ['model', 'list', '--control=x']), 'model list --control=x');
});

test('R3e pipe --control exits 2 with exactly the CONTROL_CHANNEL_MESSAGE line', (t) => {
  const sb = makeSandbox(t);
  assertExactControlLine(run(sb, ['pipe', '--control']), 'pipe --control');
});

test('R3f pipe --control=x exits 2 with exactly the CONTROL_CHANNEL_MESSAGE line', (t) => {
  const sb = makeSandbox(t);
  assertExactControlLine(run(sb, ['pipe', '--control=x']), 'pipe --control=x');
});

// ---------------------------------------------------------------------------
// S5 review, item 4: logout clears a selection that is backed only by an env credential (no stored
// entry). Before the fix, dropCredentials only cleared `current` when it dropped a stored entry.
// ---------------------------------------------------------------------------

/** A store with no credential entries and a selection naming anthropic (the env credential is the only backing). */
const ENV_ONLY_SELECTION_STORE = JSON.stringify({
  providers: {},
  current: { provider: 'anthropic', model: 'claude-fable-5-1' },
});

function writeAuthStore(sb, text) {
  fs.mkdirSync(sb.cfg, { recursive: true });
  fs.writeFileSync(path.join(sb.cfg, 'auth.json'), text);
}

function assertNoSelection(r, invocation) {
  assert.equal(r.status, 0, `${invocation}: model current stderr:\n${r.stderr}`);
  const obj = singleJsonLine(r.stdout);
  assert.equal(obj.selector, null, `${invocation}: model current must report no selection:\n${r.stdout}`);
  assert.equal(obj.provider, null, `${invocation}: provider must be null:\n${r.stdout}`);
  assert.equal(obj.model, null, `${invocation}: model must be null:\n${r.stdout}`);
}

test('S5-4a auth logout (all) clears a selection backed only by an env credential, so model current reports none', (t) => {
  const sb = makeSandbox(t);
  writeAuthStore(sb, ENV_ONLY_SELECTION_STORE);
  const env = { ANTHROPIC_API_KEY: ENV_KEY };
  const out = run(sb, ['auth', 'logout'], { env });
  assert.equal(out.status, 0, `logout stderr:\n${out.stderr}`);
  assertNoSecret(out, [ENV_KEY]);
  // R5 (S5 round 5, decided): logout does not clear an env credential, which keeps resolving. Stdout
  // carries exactly one extra line naming the provider and source=env, never the value or a fragment.
  // This test asserts the logout stdout gains exactly one line, the exact "still set: anthropic source=env"
  // line, and that a following auth status still reports source=env. It never checks the env object itself.
  // Baseline: the same logout with no env credential in a fresh sandbox. The env run must add exactly one line.
  const baseSb = makeSandbox(t);
  const baseOut = run(baseSb, ['auth', 'logout']);
  assert.equal(baseOut.status, 0, `R5 baseline logout stderr:\n${baseOut.stderr}`);
  const nonEmpty = (text) => text.split(/\r?\n/).filter((l) => l.trim() !== '');
  const baseLines = nonEmpty(baseOut.stdout);
  const outLines = nonEmpty(out.stdout);
  assert.equal(outLines.length, baseLines.length + 1,
    `R5: logout with a live env credential must add exactly one line to stdout:\nbaseline:\n${baseOut.stdout}\nwith env:\n${out.stdout}`);
  const extra = outLines.filter((l) => !baseLines.includes(l));
  assert.equal(extra.length, 1, `R5: exactly one stdout line must be extra:\n${out.stdout}`);
  assert.equal(extra[0], 'still set: anthropic source=env (logout does not remove an environment credential)',
    `R5: the extra logout line must be the exact still-set line:\n${out.stdout}`);
  assertNoKeyFragment(out, [ENV_KEY]);
  const st = run(sb, ['auth', 'status', '--provider', 'anthropic'], { env });
  assert.equal(st.status, 0, `status stderr:\n${st.stderr}`);
  assert.match(st.stdout, /\benv\b/, `R5: after logout auth status must still report source=env for anthropic:\n${st.stdout}`);
  assertNoSelection(run(sb, ['model', 'current'], { env }), 'auth logout');
});

test('S5-4b auth logout --provider anthropic clears a selection backed only by an env credential, so model current reports none', (t) => {
  const sb = makeSandbox(t);
  writeAuthStore(sb, ENV_ONLY_SELECTION_STORE);
  const env = { ANTHROPIC_API_KEY: ENV_KEY };
  const out = run(sb, ['auth', 'logout', '--provider', 'anthropic'], { env });
  assert.equal(out.status, 0, `logout stderr:\n${out.stderr}`);
  assertNoSecret(out, [ENV_KEY]);
  assertNoSelection(run(sb, ['model', 'current'], { env }), 'auth logout --provider anthropic');
});

test('S5-4c control auth.logout clears a selection backed only by an env credential, so model current reports none', (t) => {
  const sb = makeSandbox(t);
  writeAuthStore(sb, ENV_ONLY_SELECTION_STORE);
  const env = { ANTHROPIC_API_KEY: ENV_KEY };
  const out = control(sb, { type: 'control', op: 'auth.logout' }, { env });
  assert.equal(out.status, 0, `control auth.logout stderr:\n${out.stderr}`);
  const reply = singleJsonLine(out.stdout);
  assert.equal(reply.ok, true, `control auth.logout must reply ok:\n${out.stdout}`);
  const cur = control(sb, { type: 'control', op: 'model.current' }, { env });
  assert.equal(cur.status, 0, `stderr:\n${cur.stderr}`);
  const ctlEnv = singleJsonLine(cur.stdout);
  assert.equal(ctlEnv.result.selector, null, `after control auth.logout the selector must be null:\n${cur.stdout}`);
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

// ---------------------------------------------------------------------------
// R9 (S5 round 5, R-CONTROL-1): every error envelope carries a machine-readable `code`. The envelope is
// ok:false with the op kept (known op) or dropped (unknown op), exit 0 per R-CONTROL-1 (enveloped rejection
// exits 0; framing faults exit 2, and the exit code of a rejected known-op argument moved from 2 to 0),
// and the pipe keeps running.
// A status reply with no credential is an ok reply, not an error. S2 emits the code field; these pins are guards
// that fail if the field or the no-echo behaviour regresses.
// ---------------------------------------------------------------------------

test('R9a control envelope for an unknown op carries code unknown_op', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.nosuchop', id: 'c9a' });
  assert.equal(r.status, 0, `R9a: an unknown op is an op failure, exit 0 expected; stderr:\n${r.stderr}`);
  const envelope = singleJsonLine(r.stdout);
  assert.equal(envelope.ok, false, `R9a: envelope must report ok:false:\n${r.stdout}`);
  assert.equal(envelope.code, 'unknown_op', `R9a: unknown-op envelope must carry code "unknown_op" (R-CONTROL-1):\n${r.stdout}`);
});

test('R9b control envelope for a known op with an unknown provider carries code unknown_provider', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.status', id: 'c9b', provider: DUMMY_ARG });
  assert.equal(r.status, 0, `R9b: a rejected argument on a known op is an op failure, exit 0 expected; stderr:\n${r.stderr}`);
  const envelope = singleJsonLine(r.stdout);
  assert.equal(envelope.ok, false, `R9b: envelope must report ok:false:\n${r.stdout}`);
  assert.equal(envelope.op, 'auth.status', `R9b: known op must be kept in the envelope:\n${r.stdout}`);
  assert.equal(typeof envelope.code, 'string', `R9b: envelope must carry a string code (R-CONTROL-1):\n${r.stdout}`);
  assert.ok(envelope.code.length > 0, `R9b: code must be non-empty:\n${r.stdout}`);
  assert.equal(envelope.code, 'unknown_provider', `R9b: unknown provider must carry code "unknown_provider":\n${r.stdout}`);
});

test('R9c control model.use with an unknown model selector carries code unknown_model_selector', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'model.use', id: 'c9c', model: 'nosuch/model' });
  assert.equal(r.status, 0, `R9c: a bad selector on a known op is an op failure, exit 0 expected; stderr:\n${r.stderr}`);
  const envelope = singleJsonLine(r.stdout);
  assert.equal(envelope.ok, false, `R9c: envelope must report ok:false:\n${r.stdout}`);
  assert.equal(envelope.code, 'unknown_model_selector', `R9c: bad model selector must carry code "unknown_model_selector" (R-CONTROL-1):\n${r.stdout}`);
});

test('R9d control auth.status with no credential replies with the status envelope, not an error', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.status', id: 'c9d' });
  assert.equal(r.status, 0, `R9d: stderr:\n${r.stderr}`);
  const reply = singleJsonLine(r.stdout);
  assert.equal(reply.ok, true, `R9d: no credential is a status reply, not an error (ok:true expected):\n${r.stdout}`);
  assert.equal(reply.op, 'auth.status', `R9d: reply must name the op:\n${r.stdout}`);
  assert.equal(typeof reply.result, 'object', `R9d: status reply must carry a result object:\n${r.stdout}`);
  assert.ok(!('error' in reply), `R9d: a status reply must carry no error field:\n${r.stdout}`);
  assert.ok(!('code' in reply), `R9d: a status reply must carry no code field:\n${r.stdout}`);
  assert.ok(JSON.stringify(reply.result).includes('"none"'), `R9d: no credential must report source none:\n${r.stdout}`);
});

// R9e/R9f (S5 round 7, ruling R-NOECHO-1): a NON-STRING provider or model selector is the wrong shape,
// so its code is invalid_argument. unknown_provider and unknown_model_selector are for well-formed strings
// outside the set. Exit 0 here is per R-CONTROL-1: a rejected known-op argument is an enveloped rejection
// (exit 0), where it previously exited 2; framing faults still exit 2.
test('R9e control auth.login with a non-string provider carries code invalid_argument', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'auth.login', id: 'c9e', provider: 42, api_key: KEY });
  assert.equal(r.status, 0, `R9e: a wrong-shape argument on a known op is an op failure, exit 0 expected; stderr:\n${r.stderr}`);
  assertNoSecret(r, [KEY]);
  const envelope = singleJsonLine(r.stdout);
  assert.equal(envelope.ok, false, `R9e: envelope must report ok:false:\n${r.stdout}`);
  assert.equal(envelope.op, 'auth.login', `R9e: known op must be kept in the envelope:\n${r.stdout}`);
  assert.equal(envelope.code, 'invalid_argument', `R9e: a non-string provider must carry code "invalid_argument" (R-NOECHO-1):\n${r.stdout}`);
});

test('R9f control model.use with a non-string selector carries code invalid_argument', (t) => {
  const sb = makeSandbox(t);
  const r = control(sb, { type: 'control', op: 'model.use', id: 'c9f', model: 7 });
  assert.equal(r.status, 0, `R9f: a wrong-shape selector on a known op is an op failure, exit 0 expected; stderr:\n${r.stderr}`);
  const envelope = singleJsonLine(r.stdout);
  assert.equal(envelope.ok, false, `R9f: envelope must report ok:false:\n${r.stdout}`);
  assert.equal(envelope.op, 'model.use', `R9f: known op must be kept in the envelope:\n${r.stdout}`);
  assert.equal(envelope.code, 'invalid_argument', `R9f: a non-string selector must carry code "invalid_argument" (R-NOECHO-1):\n${r.stdout}`);
});
