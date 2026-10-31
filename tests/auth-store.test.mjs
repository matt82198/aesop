// S1 SPEC for the auth credential store (auth/store.mjs, reached through bin/cli.js).
// Written before the implementation exists; every test is expected to FAIL until S2 lands.
//
// The store module's internal API is not specified yet, so these tests drive the store
// only through the CLI verbs and inspect its effect on disk under AESOP_CONFIG_DIR:
//  - one JSON file under the user config directory (AESOP_CONFIG_DIR overrides)
//  - per-provider entries shaped {credential, model, addedAt}
//  - written atomically (no temp residue), mode 0600 where the platform supports it
//  - an env credential is never persisted
//  - a corrupt store is reported, not clobbered
//
// Run: node --test tests/auth-store.test.mjs

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
const KEY = ['dummy', 'store', 'value', 'JKLM1357'].join('_');
const KEY2 = ['dummy', 'store', 'second', 'NOPQ2468'].join('_');
const ENV_KEY = ['dummy', 'store', 'envonly', 'RSTU9753'].join('_');
const CREDENTIAL_ENV_VARS = ['AESOP_API_KEY', 'ANTHROPIC_API_KEY', 'BENCH_API_KEY', 'OPENAI_API_KEY'];

function makeSandbox(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-auth-store-'));
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
  // auth/model verbs must never scaffold a fleet into cwd (the unmodified CLI does, and exits 0).
  const leftover = fs.readdirSync(sb.cwd);
  assert.deepEqual(leftover, [], `auth/model verbs must not scaffold into cwd; found: ${leftover.join(', ')}`);
  return { status: res.status, stdout: res.stdout ?? '', stderr: res.stderr ?? '' };
}

/** Every regular file under dir, as absolute paths. */
function listFiles(dir) {
  if (!fs.existsSync(dir)) return [];
  const out = [];
  const walk = (d) => {
    for (const name of fs.readdirSync(d)) {
      const p = path.join(d, name);
      if (fs.lstatSync(p).isDirectory()) walk(p);
      else out.push(p);
    }
  };
  walk(dir);
  return out.sort();
}

/** Collect every object in a parsed JSON tree that carries a `credential` property. */
function findCredentialEntries(node, acc = []) {
  if (node && typeof node === 'object') {
    if (!Array.isArray(node) && 'credential' in node) acc.push(node);
    for (const v of Object.values(node)) findCredentialEntries(v, acc);
  }
  return acc;
}

/** The single store file: exactly one file under the config dir, parsed as JSON. */
function readStoreJson(sb) {
  const files = listFiles(sb.cfg);
  assert.equal(files.length, 1, `expected exactly one store file under ${sb.cfg}, found:\n${files.join('\n')}`);
  return { file: files[0], text: fs.readFileSync(files[0], 'utf8'), json: JSON.parse(fs.readFileSync(files[0], 'utf8')) };
}

function loginAnthropic(sb, key = KEY) {
  const r = run(sb, ['auth', 'login', '--provider', 'anthropic', '--api-key-stdin'], { input: key + '\n' });
  assert.equal(r.status, 0, `login stderr:\n${r.stderr}`);
  return r;
}

// ---------------------------------------------------------------------------
// location, shape, persistence
// ---------------------------------------------------------------------------

test('login creates the config dir when missing and writes exactly one JSON store file in it', (t) => {
  const sb = makeSandbox(t);
  assert.equal(fs.existsSync(sb.cfg), false, 'precondition: config dir must not exist yet');
  loginAnthropic(sb);
  const { json } = readStoreJson(sb);
  assert.equal(typeof json, 'object');
});

test('store entry for anthropic is shaped {credential, model, addedAt} with an ISO addedAt', (t) => {
  const sb = makeSandbox(t);
  loginAnthropic(sb);
  const { json } = readStoreJson(sb);
  const entries = findCredentialEntries(json);
  assert.equal(entries.length, 1, `expected one provider entry, got ${entries.length}`);
  const entry = entries[0];
  assert.equal(entry.credential, KEY, 'stored credential must be the key given on stdin');
  assert.ok('model' in entry, 'entry must carry a model field');
  assert.ok('addedAt' in entry, 'entry must carry an addedAt field');
  assert.ok(!Number.isNaN(Date.parse(entry.addedAt)), `addedAt must be an ISO timestamp, got ${entry.addedAt}`);
});

test('login does not write anything outside AESOP_CONFIG_DIR (HOME stays untouched)', (t) => {
  const sb = makeSandbox(t);
  loginAnthropic(sb);
  assert.deepEqual(listFiles(sb.home), [], 'HOME must not receive any aesop auth state');
});

test('a second login for the same provider replaces the credential instead of appending a duplicate', (t) => {
  const sb = makeSandbox(t);
  loginAnthropic(sb, KEY);
  loginAnthropic(sb, KEY2);
  const { json } = readStoreJson(sb);
  const entries = findCredentialEntries(json);
  assert.equal(entries.length, 1, `expected one anthropic entry after re-login, got ${entries.length}`);
  assert.equal(entries[0].credential, KEY2, 're-login must store the new key');
});

test('re-login status prints no fragment of either key (no-fragment rule, not last-4 masking)', (t) => {
  const sb = makeSandbox(t);
  loginAnthropic(sb, KEY);
  loginAnthropic(sb, KEY2);
  const r = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.match(r.stdout, /"credential":"set"/, `status must report the credential as set:\n${r.stdout}`);
  const oldLast4 = KEY.slice(-4);
  const newLast4 = KEY2.slice(-4);
  assert.ok(!r.stdout.includes(oldLast4), `status must not show the old key's last 4 (${oldLast4}):\n${r.stdout}`);
  assert.ok(!r.stdout.includes(newLast4), `status must not show the new key's last 4 (${newLast4}):\n${r.stdout}`);
  for (const k of [KEY, KEY2]) {
    assert.ok(!r.stdout.includes(k.slice(0, 4)), `status must not show the first 4 of a key (${k.slice(0, 4)}):\n${r.stdout}`);
    assert.doesNotMatch(r.stdout, new RegExp(`(?<![0-9])${k.length}(?![0-9])`),
      `status must not print a key length (${k.length}) as a bare number:\n${r.stdout}`);
  }
  assert.ok(!r.stdout.includes(KEY), 'old key must not appear');
  assert.ok(!r.stdout.includes(KEY2), 'new key must not appear either');
});

test('write is atomic: after login the config dir holds only the final store file (no temp residue)', (t) => {
  const sb = makeSandbox(t);
  loginAnthropic(sb);
  loginAnthropic(sb, KEY2);
  const files = listFiles(sb.cfg);
  assert.equal(files.length, 1, `temp or backup residue left behind:\n${files.join('\n')}`);
  assert.doesNotMatch(path.basename(files[0]), /\.(tmp|bak|partial)$/i);
});

test('store file is mode 0600 where the platform supports POSIX modes', (t) => {
  if (process.platform === 'win32') {
    t.skip('POSIX file modes are not enforced on win32');
    return;
  }
  const sb = makeSandbox(t);
  loginAnthropic(sb);
  const { file } = readStoreJson(sb);
  const mode = fs.statSync(file).mode & 0o777;
  assert.equal(mode, 0o600, `store file mode must be 0600, got ${mode.toString(8)}`);
});

test('store survives a process boundary: a fresh status process reads the stored credential', (t) => {
  const sb = makeSandbox(t);
  loginAnthropic(sb);
  const r = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  assert.match(r.stdout, /\bstore\b/);
  assert.match(r.stdout, /"credential":"set"/, `status must report the stored credential as set:\n${r.stdout}`);
  const storedLast4 = KEY.slice(-4);
  assert.ok(!r.stdout.includes(storedLast4), `status must not show any fragment of the stored key (last 4: ${storedLast4}):\n${r.stdout}`);
});

// ---------------------------------------------------------------------------
// removal, model persistence, env isolation
// ---------------------------------------------------------------------------

test('logout removes the stored secret from the store file', (t) => {
  const sb = makeSandbox(t);
  loginAnthropic(sb);
  const out = run(sb, ['auth', 'logout', '--provider', 'anthropic']);
  assert.equal(out.status, 0, `stderr:\n${out.stderr}`);
  const files = listFiles(sb.cfg);
  for (const f of files) {
    assert.ok(!fs.readFileSync(f, 'utf8').includes(KEY), `secret still on disk in ${f} after logout`);
  }
  const entries = files.length === 1 ? findCredentialEntries(JSON.parse(fs.readFileSync(files[0], 'utf8'))) : [];
  for (const e of entries) {
    assert.ok(!e.credential, 'a logged-out provider must not keep a non-empty credential');
  }
});

test('model use records the model selection in the store entry', (t) => {
  const sb = makeSandbox(t);
  loginAnthropic(sb);
  const use = run(sb, ['model', 'use', 'anthropic/claude-fable-5-1']);
  assert.equal(use.status, 0, `stderr:\n${use.stderr}`);
  const { json } = readStoreJson(sb);
  const entries = findCredentialEntries(json);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].model, 'claude-fable-5-1', 'the selected model must be persisted on the provider entry');
});

test('an env credential is never written to the store file', (t) => {
  const sb = makeSandbox(t);
  // Status-only run with an env credential: must not persist it anywhere under the config dir.
  const r = run(sb, ['auth', 'status', '--provider', 'anthropic'], { env: { ANTHROPIC_API_KEY: ENV_KEY } });
  assert.equal(r.status, 0, `stderr:\n${r.stderr}`);
  for (const f of listFiles(sb.cfg)) {
    assert.ok(!fs.readFileSync(f, 'utf8').includes(ENV_KEY), `env secret persisted to ${f}`);
  }
});

test('a corrupt store file is reported with exit 2 and is not overwritten', (t) => {
  const sb = makeSandbox(t);
  fs.mkdirSync(sb.cfg, { recursive: true });
  // Locate the store by a successful login first, then corrupt it. The corruption is written
  // to the same file the store uses, so this test needs the store's file name from a login.
  loginAnthropic(sb);
  const { file } = readStoreJson(sb);
  const corrupt = '{"anthropic": {"credential": ' + '"broken';
  fs.writeFileSync(file, corrupt);
  const r = run(sb, ['auth', 'status', '--provider', 'anthropic']);
  assert.equal(r.status, 2, `corrupt store must be malformed input (exit 2), got ${r.status}\nstderr:\n${r.stderr}`);
  assert.doesNotMatch(r.stderr, /at .*\.mjs:\d+/, 'corrupt store must not dump a stack trace');
  assert.equal(fs.readFileSync(file, 'utf8'), corrupt, 'a corrupt store must not be clobbered');
});
