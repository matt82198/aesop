#!/usr/bin/env node
/**
 * health-score python fallback test (tracker 397553876e57).
 *
 * tools/health-score.js must not depend on a literal `python3` binary.
 * Its siblings (dash.js, health.js, doctor.js) fall back to `python`.
 *   A. Only a `python` shim on PATH (no python3): health-score must still run
 *      and print a JSON score with exit 0.
 *   B. No interpreter on PATH at all: an explicit error message and non-zero
 *      exit (never a silent or reassuring result).
 * Uses a temp AESOP_STATE_ROOT and a scratch PATH dir; never touches real state.
 */

import { spawnSync } from 'node:child_process';
import { chmodSync, copyFileSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';

const REPO_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const HEALTH_JS = join(REPO_ROOT, 'tools', 'health-score.js');
const REAL_PY = process.env.PYTHON || 'python';
const IS_WIN = process.platform === 'win32';
const SYS_DIRS = IS_WIN && process.env.SystemRoot ? [join(process.env.SystemRoot, 'System32')] : [];

// Probe the real interpreter once: its executable path and its base prefix (stdlib home).
function resolveRealPython() {
  const r = spawnSync(REAL_PY, ['-c', 'import sys; print(sys.executable); print(sys.base_prefix)'], {
    encoding: 'utf-8', errors: 'replace', timeout: 30000,
  });
  assert.equal(r.status, 0, `cannot locate real interpreter via ${REAL_PY}: ${r.stderr}`);
  const [exe, basePrefix] = r.stdout.trim().split(/\r?\n/);
  return { exe, basePrefix };
}

// Make a directory whose only interpreter is `python` (never `python3`).
// POSIX: an sh shim. Windows: spawnSync cannot launch a .cmd without a shell,
// so copy python.exe and its DLLs and point PYTHONHOME at the real stdlib.
function writeShim(dir, real) {
  if (IS_WIN) {
    copyFileSync(real.exe, join(dir, 'python.exe'));
    for (const f of readdirSync(dirname(real.exe))) {
      if (/\.dll$/i.test(f)) copyFileSync(join(dirname(real.exe), f), join(dir, f));
    }
    return { PYTHONHOME: real.basePrefix };
  }
  const p = join(dir, 'python');
  writeFileSync(p, `#!/bin/sh\nexec "${real.exe}" "$@"\n`);
  chmodSync(p, 0o755);
  return {};
}

function runHealthScore(pathValue, stateRoot, fixtureRoot, extraEnv = {}) {
  const env = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (k.toUpperCase() !== 'PATH' && k.toUpperCase() !== 'PYTHONHOME') env[k] = v;
  }
  Object.assign(env, extraEnv);
  env.PATH = [pathValue, ...SYS_DIRS].filter(Boolean).join(IS_WIN ? ';' : ':');
  env.AESOP_ROOT = fixtureRoot;
  env.AESOP_STATE_ROOT = stateRoot;
  return spawnSync(process.execPath, [HEALTH_JS, 'health-score', '--json', '--cwd', fixtureRoot], {
    env, encoding: 'utf-8', errors: 'replace', timeout: 60000,
  });
}

describe('health-score python fallback (397553876e57)', () => {
  let tmp;
  let shimDir;
  let emptyDir;
  let stateRoot;
  let fixtureRoot;
  let shimEnv;

  before(() => {
    tmp = mkdtempSync(join(tmpdir(), 'aesop-hs-fallback-'));
    shimDir = join(tmp, 'shim');
    emptyDir = join(tmp, 'empty');
    stateRoot = join(tmp, 'state');
    fixtureRoot = join(tmp, 'fixture');
    for (const d of [shimDir, emptyDir, stateRoot, fixtureRoot]) mkdirSync(d, { recursive: true });
    shimEnv = writeShim(shimDir, resolveRealPython());
  });

  after(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it('A. runs under a python-only PATH (no python3) and prints a score', () => {
    const r = runHealthScore(shimDir, stateRoot, fixtureRoot, shimEnv);
    assert.equal(r.error, undefined, `spawn error: ${r.error && r.error.message}`);
    assert.equal(r.status, 0, `expected exit 0, got ${r.status}; stderr: ${r.stderr}`);
    const parsed = JSON.parse(r.stdout);
    assert.equal(typeof parsed.score, 'number', `no numeric score in: ${r.stdout}`);
  });

  it('B. with no interpreter on PATH, fails loudly with an explicit message', () => {
    const r = runHealthScore(emptyDir, stateRoot, fixtureRoot);
    assert.notEqual(r.status, 0, 'must not report success without an interpreter');
    assert.match(`${r.stdout}${r.stderr}`, /python/i, 'error must name the missing interpreter');
  });
});
