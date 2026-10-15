// doctor names every push-gate baseline and tools/INDEX.md it needs, and the
// regenerate command in the FAIL line, so a fresh scaffold cannot fail a push
// without doctor saying why.
//
// Run: node --test tests/doctor-baseline-checks.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLI = path.join(ROOT, 'bin', 'cli.js');
const DOCTOR = path.join(ROOT, 'tools', 'doctor.js');

function scaffoldFleet() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-doctor-baseline-'));
  const home = path.join(base, 'home');
  fs.mkdirSync(home, { recursive: true });
  const env = { ...process.env, HOME: home, USERPROFILE: home, GIT_TERMINAL_PROMPT: '0' };
  const res = spawnSync(process.execPath, [CLI, 'fresh-fleet', '--no-skills'], {
    cwd: base,
    encoding: 'utf8',
    env,
    timeout: Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 120000,
    killSignal: 'SIGKILL'
  });
  assert.equal(res.status, 0, `scaffold failed: ${res.stderr}`);
  return { target: path.join(base, 'fresh-fleet'), env };
}

function runDoctor(target, env) {
  const res = spawnSync(process.execPath, [DOCTOR, '--json'], {
    cwd: target,
    encoding: 'utf8',
    env,
    timeout: 60000,
    killSignal: 'SIGKILL'
  });
  return JSON.parse(res.stdout);
}

function baselineRow(report, file) {
  const row = report.checks.find(c => c.label.includes(file));
  assert.ok(row, `doctor must report a check for ${file}. labels: ${report.checks.map(c => c.label).join(' | ')}`);
  return row;
}

test('doctor reports PASS for every generated baseline on a fresh scaffold', () => {
  const { target, env } = scaffoldFleet();
  const report = runDoctor(target, env);
  for (const file of [
    'tools/INDEX.md',
    '.pyflakes-baseline.json',
    '.subprocess-guard-baseline.json',
    '.portability-baseline.json',
    '.stateapi-baseline.json'
  ]) {
    assert.equal(baselineRow(report, file).passed, true, `${file} must PASS on a fresh scaffold`);
  }
});

test('doctor FAIL line names a missing subprocess baseline and its regenerate command', () => {
  const { target, env } = scaffoldFleet();
  fs.unlinkSync(path.join(target, '.subprocess-guard-baseline.json'));
  const report = runDoctor(target, env);
  const row = baselineRow(report, '.subprocess-guard-baseline.json');
  assert.equal(row.passed, false, 'missing baseline must FAIL');
  assert.ok(row.hint.includes('tools/subprocess_guard.py'), `hint must name the regenerate command: ${row.hint}`);
  assert.ok(row.hint.includes('--update-baseline'), `hint must name --update-baseline: ${row.hint}`);
  assert.equal(report.summary.passed < report.summary.total, true, 'doctor must not report all green');
});

test('doctor FAIL line names a missing tools/INDEX.md and its regenerate command', () => {
  const { target, env } = scaffoldFleet();
  fs.unlinkSync(path.join(target, 'tools', 'INDEX.md'));
  const report = runDoctor(target, env);
  const row = baselineRow(report, 'tools/INDEX.md');
  assert.equal(row.passed, false, 'missing INDEX must FAIL');
  assert.ok(row.hint.includes('python tools/gen_tool_index.py --regenerate'), `hint: ${row.hint}`);
});
