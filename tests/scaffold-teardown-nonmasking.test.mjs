// Pins the teardown contract for tests/scaffold-ignores-artifacts.test.mjs (S1 RED spec).
//
// Incident (PR #1364 run 40584945960, windows-shard 0): the scaffold test's `finally`
// called fs.rmSync(root, ...) and threw EBUSY on a locked `target` directory. The throw
// replaced whatever the try block had asserted (the 45 s budget or the status check),
// so the CI log could not show which assertion failed first.
//
// Required behaviour:
//  1. tests/helpers/cleanup.mjs exports cleanupDir(root, {label}) which retries
//     fs.rmSync, never throws, and prints one line `cleanup: <label>: <code> <message>`
//     to stderr on failure.
//  2. tests/scaffold-ignores-artifacts.test.mjs uses cleanupDir from ./helpers/cleanup.mjs
//     and has no fs.rmSync( call inside its finally block.
//  3. The scaffold test's budget assertion message includes elapsed_ms.
//
// Run: node --test tests/scaffold-teardown-nonmasking.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const TESTS_DIR = path.dirname(fileURLToPath(import.meta.url));
const HELPER_PATH = path.join(TESTS_DIR, 'helpers', 'cleanup.mjs');
const SCAFFOLD_TEST_PATH = path.join(TESTS_DIR, 'scaffold-ignores-artifacts.test.mjs');

test('cleanupDir does not throw on a locked dir and writes a "cleanup:" line naming EBUSY to stderr', async () => {
  const mod = await import(pathToFileURL(HELPER_PATH).href);
  assert.equal(typeof mod.cleanupDir, 'function', 'tests/helpers/cleanup.mjs must export cleanupDir');

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-cleanup-probe-'));
  const realRmSync = fs.rmSync;
  // Simulate a Windows lock: every rmSync attempt fails with EBUSY.
  fs.rmSync = () => {
    const err = new Error(`EBUSY: resource busy or locked, rmdir '${dir}'`);
    err.code = 'EBUSY';
    throw err;
  };
  const origWrite = process.stderr.write;
  let captured = '';
  process.stderr.write = (chunk, ...rest) => {
    captured += String(chunk);
    const cb = rest.find((x) => typeof x === 'function');
    if (cb) cb();
    return true;
  };
  try {
    assert.doesNotThrow(() => mod.cleanupDir(dir, { label: 'probe' }));
  } finally {
    process.stderr.write = origWrite;
    fs.rmSync = realRmSync;
    fs.rmSync(dir, { recursive: true, force: true });
  }
  assert.match(captured, /cleanup:/, `stderr missing "cleanup:" line; captured: ${JSON.stringify(captured)}`);
  assert.match(captured, /EBUSY/, `stderr missing "EBUSY" code; captured: ${JSON.stringify(captured)}`);
  assert.match(captured, /cleanup: probe: EBUSY/, `expected "cleanup: probe: EBUSY ..." line; captured: ${JSON.stringify(captured)}`);
});

test('scaffold-ignores-artifacts test imports cleanupDir from ./helpers/cleanup.mjs and has no fs.rmSync in finally', () => {
  const src = fs.readFileSync(SCAFFOLD_TEST_PATH, 'utf8');
  assert.match(
    src,
    /import\s*\{[^}]*\bcleanupDir\b[^}]*\}\s*from\s*['"]\.\/helpers\/cleanup\.mjs['"]/,
    'scaffold test must import { cleanupDir } from ./helpers/cleanup.mjs'
  );
  const finallyIdx = src.lastIndexOf('finally');
  assert.ok(finallyIdx >= 0, 'scaffold test must still have a finally block for teardown');
  const finallyBody = src.slice(finallyIdx);
  assert.ok(
    !finallyBody.includes('fs.rmSync('),
    'scaffold test finally block must not call fs.rmSync( directly (its throw masks the real assertion); use cleanupDir'
  );
});

test('scaffold-ignores-artifacts budget assertion message includes elapsed_ms', () => {
  const src = fs.readFileSync(SCAFFOLD_TEST_PATH, 'utf8');
  const anchor = src.indexOf('elapsed < SCAFFOLD_TIMEOUT_MS');
  assert.ok(anchor >= 0, 'scaffold test must still contain the elapsed < SCAFFOLD_TIMEOUT_MS budget assertion');
  // The assertion (and its message) spans a few lines; look at the window that follows it.
  const window = src.slice(anchor, anchor + 300);
  assert.ok(
    window.includes('elapsed_ms'),
    'budget assertion message must include elapsed_ms so the failure shows the measured time even when teardown fails'
  );
});
