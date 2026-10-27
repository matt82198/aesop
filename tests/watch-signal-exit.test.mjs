// SEM-15: tools/watch.js maps a null child exit code (child killed by a signal)
// to success via `process.exitCode = code || 0`. A watched child that dies by
// signal must make watch report a non-zero exit. A child exiting 0 still reports 0.
//
// Uses only Node.js built-ins (node:test, node:assert, node:fs, node:path, node:os, node:child_process).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const watchPath = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'tools', 'watch.js');

// Build a fixture aesop root: watch.js refuses to run unless daemons/run-watchdog.sh exists in cwd.
function createFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-watch-exit-'));
  fs.mkdirSync(path.join(root, 'daemons'), { recursive: true });
  fs.writeFileSync(path.join(root, 'daemons', 'run-watchdog.sh'), '# fixture watchdog, never executed by the mock\n');
  return root;
}

// Preload module that replaces child_process.spawn with a stub child which emits a
// fixed 'exit' event. This drives the real `proc.on('exit', ...)` handler in watch.js
// cross-platform, including the null-code path that a real process cannot produce on win32.
function createSpawnStub(root, exitCode, exitSignal) {
  const stubPath = path.join(root, 'spawn-stub.cjs');
  const body = [
    "const cp = require('child_process');",
    "const { EventEmitter } = require('events');",
    'cp.spawn = function () {',
    '  const child = new EventEmitter();',
    '  setImmediate(() => child.emit(\'exit\', ' + JSON.stringify(exitCode) + ', ' + JSON.stringify(exitSignal) + '));',
    '  return child;',
    '};',
    '',
  ].join('\n');
  fs.writeFileSync(stubPath, body);
  return stubPath;
}

function runWatchWithStub(exitCode, exitSignal) {
  const root = createFixture();
  try {
    const stubPath = createSpawnStub(root, exitCode, exitSignal);
    const result = spawnSync(process.execPath, ['-r', stubPath, watchPath], {
      cwd: root,
      encoding: 'utf8',
      errors: 'replace',
      timeout: 30000,
    });
    return result;
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test('watch reports 0 when the watched child exits 0', () => {
  const result = runWatchWithStub(0, null);
  assert.equal(result.error, undefined, `spawn error: ${result.error && result.error.message}`);
  assert.equal(result.status, 0, `expected exit 0, got ${result.status}; stderr=${result.stderr}`);
});

test('watch reports non-zero when the watched child is killed by a signal (code null, signal SIGTERM)', () => {
  const result = runWatchWithStub(null, 'SIGTERM');
  assert.equal(result.error, undefined, `spawn error: ${result.error && result.error.message}`);
  assert.notEqual(result.status, 0, `expected non-zero exit for signal death, got ${result.status}`);
});

test('watch reports non-zero when the watched child exits with code null and no signal', () => {
  const result = runWatchWithStub(null, null);
  assert.equal(result.error, undefined, `spawn error: ${result.error && result.error.message}`);
  assert.notEqual(result.status, 0, `expected non-zero exit for null code, got ${result.status}`);
});

test('watch reports non-zero for a real POSIX signal death of the watchdog daemon', { skip: process.platform === 'win32' ? 'POSIX signals do not exist on win32; the stubbed null-code cases above cover the handler' : false }, () => {
  const root = createFixture();
  try {
    // The watchdog kills its own shell with SIGTERM, so the child dies by signal.
    fs.writeFileSync(path.join(root, 'daemons', 'run-watchdog.sh'), 'kill -TERM $$\n');
    const result = spawnSync(process.execPath, [watchPath], {
      cwd: root,
      encoding: 'utf8',
      errors: 'replace',
      timeout: 30000,
    });
    assert.equal(result.error, undefined, `spawn error: ${result.error && result.error.message}`);
    assert.notEqual(result.status, 0, `expected non-zero exit after SIGTERM, got status=${result.status} signal=${result.signal}`);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
