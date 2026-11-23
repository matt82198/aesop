// Doctor checks the headless worker CLI that aesop.config.json selects.
//
// seats.worker.backend "codex-exec" -> a "Codex CLI" row: PASS when `codex` resolves on PATH
//                                       and `codex --version` reports, FAIL with a fix line otherwise.
// seats.worker.backend "agy-headless" -> an "agy CLI" row: the same contract against `agy --version`.
// Any other backend (or no seats block) -> both rows n/a, and neither affects the exit code.
//
// Fake CLIs are shims on a temp PATH; no real codex or agy is ever invoked.
//
// Run: node --test tests/cli-doctor-codex-exec.test.mjs

import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLI = path.join(ROOT, 'bin', 'cli.js');
const DOCTOR = path.join(ROOT, 'tools', 'doctor.js');
const TIMEOUT = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 120000;
const FAKE_VERSION = '9.9.9-test';

let fleetBase = null;   // scaffolded once; every test mutates only aesop.config.json
let fleetTarget = null;
let fleetEnv = null;    // base env with HOME isolated to the temp dir

// Scaffold a fresh fleet (same path as doctor-baseline-checks.test.mjs) so every check except
// the CLI rows is in its normal, passing state.
before(() => {
  fleetBase = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-doctor-cli-'));
  const home = path.join(fleetBase, 'home');
  fs.mkdirSync(home, { recursive: true });
  fleetEnv = { ...process.env, HOME: home, USERPROFILE: home, GIT_TERMINAL_PROMPT: '0' };
  const res = spawnSync(process.execPath, [CLI, 'cli-fleet', '--name', 'cli-fleet', '--no-skills'], {
    cwd: fleetBase,
    encoding: 'utf8',
    env: fleetEnv,
    timeout: TIMEOUT,
    killSignal: 'SIGKILL'
  });
  assert.equal(res.status, 0, `scaffold failed: ${res.stderr}`);
  fleetTarget = path.join(fleetBase, 'cli-fleet');
});

/** Write a fake CLI named `name` into dir: a POSIX shim and a Windows .cmd, both answering --version. */
function writeFakeCli(dir, name, version) {
  fs.mkdirSync(dir, { recursive: true });
  const posix = path.join(dir, name);
  fs.writeFileSync(posix, `#!/bin/sh\nif [ "$1" = "--version" ]; then echo "${name}-cli ${version}"; exit 0; fi\nexit 0\n`);
  fs.chmodSync(posix, 0o755);
  fs.writeFileSync(path.join(dir, `${name}.cmd`), `@echo off\r\nif "%1"=="--version" (echo ${name}-cli ${version})\r\n`);
}

/** Return a copy of env whose PATH has `shimDir` first (or, with null, PATH minus every dir holding a codex/agy). */
function envWithPath(base, prepend, names) {
  const env = { ...base };
  const key = Object.keys(env).find(k => k.toUpperCase() === 'PATH') || 'PATH';
  const sep = path.delimiter;
  const entries = (env[key] || '').split(sep).filter(Boolean);
  const kept = prepend === null
    ? entries.filter(dir => !names.some(n => ['', '.cmd', '.exe'].some(ext => fs.existsSync(path.join(dir, n + ext)))))
    : [prepend, ...entries];
  env[key] = kept.join(sep);
  return env;
}

/** Set (or clear) seats.worker.backend in the shared fleet's aesop.config.json. */
function setWorkerBackend(backend) {
  const configPath = path.join(fleetTarget, 'aesop.config.json');
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  config.seats = backend === undefined ? undefined : { worker: { backend } };
  if (config.seats === undefined) delete config.seats;
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
}

function runDoctor(env) {
  const res = spawnSync(process.execPath, [DOCTOR, '--json'], {
    cwd: fleetTarget,
    encoding: 'utf8',
    env,
    timeout: 60000,
    killSignal: 'SIGKILL'
  });
  assert.ok(res.stdout, `doctor produced no JSON stdout. stderr: ${res.stderr}`);
  return { status: res.status, report: JSON.parse(res.stdout), stderr: res.stderr };
}

function cliRow(report, label) {
  const row = report.checks.find(c => c.label === label);
  assert.ok(row, `doctor must report a "${label}" check. labels: ${report.checks.map(c => c.label).join(' | ')}`);
  return row;
}

function tempShimDir(tag) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `aesop-shim-${tag}-`));
}

// ---- codex-exec ------------------------------------------------------------

test('codex-exec: "Codex CLI" PASSes and reports `codex --version` when codex resolves on PATH', () => {
  setWorkerBackend('codex-exec');
  const shim = tempShimDir('codex-pass');
  writeFakeCli(shim, 'codex', FAKE_VERSION);
  const { report } = runDoctor(envWithPath(fleetEnv, shim));
  const row = cliRow(report, 'Codex CLI');
  assert.equal(row.passed, true, `Codex CLI must PASS with codex on PATH. hint: ${row.hint}`);
  assert.equal(row.na, false, 'Codex CLI must not be n/a when the backend is codex-exec');
  assert.ok(row.hint.includes(FAKE_VERSION), `hint must report codex --version output. hint: ${row.hint}`);
});

test('codex-exec: "Codex CLI" FAILs with a fix line when codex is not on PATH', () => {
  setWorkerBackend('codex-exec');
  const { report } = runDoctor(envWithPath(fleetEnv, null, ['codex']));
  const row = cliRow(report, 'Codex CLI');
  assert.equal(row.passed, false, 'Codex CLI must FAIL when codex is missing');
  assert.equal(row.na, false, 'a missing codex is a FAIL, not n/a');
  assert.ok(/install|PATH/i.test(row.hint), `hint must name the fix (install or PATH). hint: ${row.hint}`);
  assert.ok(row.hint.includes('codex'), `hint must name codex. hint: ${row.hint}`);
});

// ---- agy-headless ----------------------------------------------------------

test('agy-headless: "agy CLI" PASSes and reports `agy --version` when agy resolves on PATH', () => {
  setWorkerBackend('agy-headless');
  const shim = tempShimDir('agy-pass');
  writeFakeCli(shim, 'agy', FAKE_VERSION);
  const { report } = runDoctor(envWithPath(fleetEnv, shim));
  const row = cliRow(report, 'agy CLI');
  assert.equal(row.passed, true, `agy CLI must PASS with agy on PATH. hint: ${row.hint}`);
  assert.equal(row.na, false, 'agy CLI must not be n/a when the backend is agy-headless');
  assert.ok(row.hint.includes(FAKE_VERSION), `hint must report agy --version output. hint: ${row.hint}`);
});

test('agy-headless: "agy CLI" FAILs with a fix line when agy is not on PATH', () => {
  setWorkerBackend('agy-headless');
  const { report } = runDoctor(envWithPath(fleetEnv, null, ['agy']));
  const row = cliRow(report, 'agy CLI');
  assert.equal(row.passed, false, 'agy CLI must FAIL when agy is missing');
  assert.equal(row.na, false, 'a missing agy is a FAIL, not n/a');
  assert.ok(/install|PATH/i.test(row.hint), `hint must name the fix (install or PATH). hint: ${row.hint}`);
  assert.ok(row.hint.includes('agy'), `hint must name agy. hint: ${row.hint}`);
});

// ---- other backends and exit code -----------------------------------------

test('other backend: both CLI rows print n/a and neither affects the exit code', () => {
  setWorkerBackend('claude');
  const shim = tempShimDir('other');
  writeFakeCli(shim, 'codex', FAKE_VERSION);
  writeFakeCli(shim, 'agy', FAKE_VERSION);
  const withShims = runDoctor(envWithPath(fleetEnv, shim));
  for (const label of ['Codex CLI', 'agy CLI']) {
    const row = cliRow(withShims.report, label);
    assert.equal(row.na, true, `${label} must be n/a for backend "claude"`);
    assert.equal(row.passed, true, `${label} n/a must not count as failed`);
  }
  const withoutShims = runDoctor(envWithPath(fleetEnv, null, ['codex', 'agy']));
  assert.equal(withShims.status, withoutShims.status,
    'missing codex/agy must not change the exit code when the backend is claude');
});

test('exit code is non-zero only for the FAIL case: codex-exec present PASSes, codex absent FAILs', () => {
  setWorkerBackend('codex-exec');
  const shim = tempShimDir('exit-pass');
  writeFakeCli(shim, 'codex', FAKE_VERSION);
  const pass = runDoctor(envWithPath(fleetEnv, shim));
  const fail = runDoctor(envWithPath(fleetEnv, null, ['codex']));
  assert.equal(cliRow(pass.report, 'Codex CLI').passed, true, 'precondition: codex present must PASS');
  assert.equal(cliRow(fail.report, 'Codex CLI').passed, false, 'precondition: codex absent must FAIL');
  assert.equal(fail.status, 1, 'doctor must exit 1 when the Codex CLI check FAILs');
  assert.equal(pass.status, 0, 'doctor must exit 0 when every counted check (incl. Codex CLI) PASSes');
});
