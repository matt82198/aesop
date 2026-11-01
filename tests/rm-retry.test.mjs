// Contract pin for tests/helpers/rm-retry.mjs (S1 SPEC, red-first).
//
// rmRetrySync(path, { maxRetries = 10, retryDelay = 50, rm = fs.rmSync } = {}) removes a
// path recursively and forcibly. Each attempt calls rm(path, { recursive: true, force: true }).
// Seam contract: `maxRetries` is the TOTAL number of attempts, so rm is called exactly
// maxRetries times when every attempt fails (helper doc comment: "Maximum number of removal
// attempts"). EPERM / EBUSY / ENOTEMPTY are retried with retryDelay ms between attempts; any
// other code is rethrown immediately; after the last failed attempt the last error is rethrown.
//
// The `rm` option is the injection seam that pins the retry logic deterministically. It does
// not exist in the helper at HEAD, so tests 1-3 are red at HEAD: the option is ignored and the
// real fs.rmSync runs on a missing path instead.
//
// Motivation: tests/dash-fresh-init.test.mjs cleanup intermittently throws EPERM on
// Windows because a file handle or a process cwd is still held for a moment after a child exits.
//
// Placement: this lives at tests/ (not tests/helpers/) because the npm test glob
// `tests/*.test.mjs` does not descend into tests/helpers/.
//
// Run: node --import ./tests/helpers/isolated-env.mjs --test --test-force-exit tests/rm-retry.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { rmRetrySync } from './helpers/rm-retry.mjs';

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForFile(file, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (!fs.existsSync(file)) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${file}`);
    await sleep(10);
  }
}

function codedError(code, message) {
  return Object.assign(new Error(message), { code });
}

function missingPath() {
  return path.join(os.tmpdir(), `aesop-rm-retry-missing-${randomUUID()}`);
}

test('rmRetrySync removes a directory tree', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-rm-retry-tree-'));
  fs.mkdirSync(path.join(dir, 'nested'));
  fs.writeFileSync(path.join(dir, 'nested', 'file.txt'), 'x');

  rmRetrySync(dir);

  assert.equal(fs.existsSync(dir), false, 'directory tree must be removed');
});

test('rmRetrySync retries a retryable code and succeeds on the third attempt', () => {
  const missing = missingPath();
  let calls = 0;
  const rm = () => {
    calls += 1;
    if (calls <= 2) throw codedError('EPERM', `attempt ${calls} locked`);
  };

  assert.doesNotThrow(() => rmRetrySync(missing, { retryDelay: 1, rm }));
  assert.equal(calls, 3, 'rm must be called exactly 3 times: two EPERM failures then success');
});

test('rmRetrySync rethrows a non-retryable code immediately without retrying', () => {
  const missing = missingPath();
  const denied = codedError('EACCES', 'access denied');
  let calls = 0;
  const rm = () => {
    calls += 1;
    throw denied;
  };

  assert.throws(
    () => rmRetrySync(missing, { maxRetries: 5, retryDelay: 1, rm }),
    (err) => err === denied,
    'the same EACCES error object must propagate unchanged',
  );
  assert.equal(calls, 1, 'a non-retryable code must not be retried');
});

test('rmRetrySync exhausts maxRetries attempts and rethrows the last retryable error', () => {
  const missing = missingPath();
  let calls = 0;
  const rm = () => {
    calls += 1;
    throw codedError('EBUSY', `attempt ${calls} busy`);
  };

  assert.throws(
    () => rmRetrySync(missing, { maxRetries: 3, retryDelay: 1, rm }),
    (err) => err.code === 'EBUSY' && err.message === 'attempt 3 busy',
    'after maxRetries attempts the error from the final attempt must be rethrown',
  );
  assert.equal(calls, 3, 'maxRetries is the total attempt count, so rm is called 3 times');
});

test(
  'rmRetrySync retries until a child process holding its cwd inside the tree exits (win32 only)',
  {
    skip:
      process.platform !== 'win32' &&
      'a process cwd blocks removal of its directory on Windows only; POSIX unlink and rmdir succeed',
  },
  async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-rm-retry-cwd-'));
    const sub = path.join(root, 'child-cwd');
    fs.mkdirSync(sub);
    fs.writeFileSync(path.join(root, 'file.txt'), 'x');

    // The child runs with cwd inside the tree, signals readiness through a marker outside the
    // tree, then stays alive for ~600 ms before exiting. A single rmSync sees the locked
    // directory and fails; the retrying helper succeeds once the child has exited.
    const ready = path.join(os.tmpdir(), `aesop-rm-retry-ready-${randomUUID()}.marker`);
    const code = `require('node:fs').writeFileSync(${JSON.stringify(ready)}, 'ready'); setTimeout(() => {}, 600);`;

    const child = spawn(process.execPath, ['-e', code], {
      cwd: sub,
      stdio: 'ignore',
      windowsHide: true,
    });
    const exited = new Promise((resolve) => child.once('exit', resolve));

    try {
      await waitForFile(ready, 10_000);

      assert.doesNotThrow(() => rmRetrySync(root, { maxRetries: 40, retryDelay: 50 }));
      assert.equal(fs.existsSync(root), false, 'tree must be removed once the child releases its cwd');
    } finally {
      await exited;
      fs.rmSync(ready, { force: true });
      fs.rmSync(root, { recursive: true, force: true });
    }
  },
);

test('rmRetrySync is a no-op on a missing path', () => {
  const missing = missingPath();
  assert.equal(fs.existsSync(missing), false, 'precondition: path must not exist');

  assert.doesNotThrow(() => rmRetrySync(missing));
});
