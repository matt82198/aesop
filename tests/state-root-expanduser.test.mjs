// Tests that tools/dash.js expands a ~-prefixed aesop.config.json state_root to the
// user's home directory, and that the dashboard's exit status is non-zero when the
// python child dies by signal (exit code null).
//
// Defect (SEM-1 / SEM-15, wrong data on the dashboard, P0):
//   - dash.js:81-82 runs path.resolve(CURRENT_DIR, '~/.aesop-state'), producing a literal
//     "~" directory under the project, not the user's home state directory.
//   - dash.js:101 and watch.js:31-34 do `process.exitCode = code || 0`, so a signal death
//     (code === null) is reported as success.
//
// Method: the dashboard spawns `python3 ui/serve.py` (or `python` on PATH). The stub
// interpreter is a copy of the node binary named python3(.exe), placed first on PATH,
// with a --require preload (via NODE_OPTIONS, inherited by the stub only: the preload is
// a no-op unless the executable is named python3*) that records AESOP_STATE_ROOT and
// exits, or kills itself with SIGKILL when STATE_ROOT_MODE=signal. The real dash.js runs
// unmodified.
//
// Run: node --test tests/state-root-expanduser.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DASH = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'tools', 'dash.js');

// Preload loaded into the python3 stub only (basename of execPath decides). In the
// dash.js process itself (named node) it does nothing.
const STUB_PRELOAD = `
const path = require('path');
const fs = require('fs');
if (path.basename(process.execPath).toLowerCase().startsWith('python3')) {
  if (process.argv.includes('--version')) process.exit(0);
  fs.writeFileSync(process.env.STATE_ROOT_PROBE, JSON.stringify({
    AESOP_STATE_ROOT: process.env.AESOP_STATE_ROOT === undefined ? null : process.env.AESOP_STATE_ROOT,
    argv: process.argv.slice(1),
  }));
  if (process.env.STATE_ROOT_MODE === 'signal') process.kill(process.pid, 'SIGKILL');
  process.exit(0);
}
`;

function makeFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-state-root-'));
  const home = path.join(root, 'home');
  const project = path.join(root, 'project');
  const bin = path.join(root, 'bin');
  for (const d of [home, project, bin]) fs.mkdirSync(d, { recursive: true });

  const stubName = process.platform === 'win32' ? 'python3.exe' : 'python3';
  const stubExe = path.join(bin, stubName);
  try {
    fs.linkSync(process.execPath, stubExe);
  } catch {
    fs.copyFileSync(process.execPath, stubExe);
  }
  const preload = path.join(bin, 'stub-preload.cjs');
  fs.writeFileSync(preload, STUB_PRELOAD);
  fs.writeFileSync(path.join(project, 'aesop.config.json'),
    JSON.stringify({ state_root: '~/.aesop-state' }));
  return { root, home, project, bin, preload, probe: path.join(root, 'probe.json') };
}

// Build a child env with HOME/USERPROFILE pinned to the fixture home. Windows env keys
// are case-insensitive, so PATH is set under whatever casing the parent already uses.
function childEnv(fx, mode) {
  const env = { ...process.env };
  for (const k of Object.keys(env)) {
    if (/^(AESOP_STATE_ROOT|PORT)$/i.test(k)) delete env[k];
  }
  const setCI = (name, value) => {
    const key = Object.keys(env).find((k) => k.toUpperCase() === name) || name;
    env[key] = value;
  };
  setCI('HOME', fx.home);
  setCI('USERPROFILE', fx.home);
  setCI('PATH', fx.bin + path.delimiter + (process.env.PATH || process.env.Path || ''));
  // NODE_OPTIONS splits on whitespace and treats backslashes as escapes: use forward slashes.
  env.NODE_OPTIONS = `--require ${fx.preload.split(path.sep).join('/')}`;
  env.STATE_ROOT_PROBE = fx.probe;
  env.STATE_ROOT_MODE = mode;
  return env;
}

function runDash(fx, mode) {
  return spawnSync(process.execPath, [DASH], {
    cwd: fx.project,
    env: childEnv(fx, mode),
    encoding: 'utf-8',
    timeout: 60000,
  });
}

test('dash.js passes a ~ state_root through as the home-relative path', () => {
  const fx = makeFixture();
  try {
    const r = runDash(fx, 'exit');
    assert.ok(fs.existsSync(fx.probe), `stub never ran; stderr: ${r.stderr}`);
    const probe = JSON.parse(fs.readFileSync(fx.probe, 'utf-8'));
    assert.equal(probe.AESOP_STATE_ROOT, path.join(fx.home, '.aesop-state'));
    assert.equal(r.status, 0, `dash exit status; stderr: ${r.stderr}`);
  } finally {
    fs.rmSync(fx.root, { recursive: true, force: true });
  }
});

// A signal death only exists on POSIX: Windows has no signal delivery to a child, so
// node reports the terminate status as a number, never null. This case runs on the
// Linux shape (node --test on a POSIX host) and is skipped on win32 for that reason.
test('dash.js exits non-zero when the python child dies by signal (exit code null)',
  { skip: process.platform === 'win32' && 'POSIX signals only; run on the Linux shape' },
  () => {
    const fx = makeFixture();
    try {
      const r = runDash(fx, 'signal');
      assert.ok(fs.existsSync(fx.probe), `stub never ran; stderr: ${r.stderr}`);
      assert.notEqual(r.status, 0, `signal death reported as success; stderr: ${r.stderr}`);
      assert.notEqual(r.status, null, 'dash.js itself must exit with a number');
    } finally {
      fs.rmSync(fx.root, { recursive: true, force: true });
    }
  });
