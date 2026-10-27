import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn, spawnSync } from 'node:child_process';
import { acquireLock, releaseLock } from '../tools/lock.mjs';

// Helper: create a temp dir + target "file" path to lock.
function createTempTarget() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lock-test-'));
  return path.join(dir, 'PROPOSALS.md');
}

// Helper: hand-craft a stale lock directory + marker file, bypassing
// acquireLock, to simulate a lock left behind by some other process.
function plantLock(filePath, { pid, ageMs }) {
  const lockDir = filePath + '.lock';
  fs.mkdirSync(lockDir, { recursive: true });
  const epochSeconds = Math.floor((Date.now() - ageMs) / 1000);
  const marker = `${pid}\n${epochSeconds}\n`;
  fs.writeFileSync(path.join(lockDir, 'pid-timestamp.txt'), marker, 'utf8');
  return lockDir;
}

// Helper: obtain a pid that is guaranteed to be dead (a child process that
// has already exited). More reliable than a hardcoded magic number, which
// risks colliding with a real live process on the test machine.
function getDeadPid() {
  const result = spawnSync(process.execPath, ['-e', 'process.exit(0)']);
  assert.strictEqual(result.status, 0, 'helper child should exit cleanly');
  return result.pid;
}

const STALE_AGE_MS = 11 * 60 * 1000; // > 10 minute threshold in lock.mjs

test('stale timestamp + dead pid: lock is broken and acquireLock succeeds', async (t) => {
  const filePath = createTempTarget();
  const deadPid = getDeadPid();
  const lockDir = plantLock(filePath, { pid: deadPid, ageMs: STALE_AGE_MS });

  // Silence + capture the expected "breaking lock" warning.
  const originalError = console.error;
  const logs = [];
  console.error = (...args) => logs.push(args.join(' '));

  let acquired;
  try {
    acquired = acquireLock(filePath, { timeoutMs: 5000 });
  } finally {
    console.error = originalError;
  }

  assert.strictEqual(acquired, lockDir, 'acquireLock should reclaim the stale-and-dead lock and return its dir');
  assert.ok(fs.existsSync(lockDir), 'lock dir should exist (now owned by us)');
  const marker = fs.readFileSync(path.join(lockDir, 'pid-timestamp.txt'), 'utf8');
  assert.ok(marker.startsWith(`${process.pid}\n`), 'reclaimed lock marker should record our own pid');
  assert.ok(
    logs.some((l) => l.includes('Stale lock detected') && l.includes('breaking lock')),
    'should log a stale-lock-broken warning'
  );

  releaseLock(acquired);
  fs.rmSync(path.dirname(filePath), { recursive: true, force: true });
});

test('stale timestamp + LIVE pid: lock is NOT broken, acquireLock fails closed at deadline', async (t) => {
  const filePath = createTempTarget();
  // Use our own pid: guaranteed alive for the duration of this test.
  const lockDir = plantLock(filePath, { pid: process.pid, ageMs: STALE_AGE_MS });

  const originalError = console.error;
  const logs = [];
  console.error = (...args) => logs.push(args.join(' '));

  let threw = false;
  try {
    acquireLock(filePath, { timeoutMs: 150 });
  } catch (e) {
    threw = true;
    assert.ok(/Failed to acquire/.test(e.message), 'should throw the fail-closed timeout error');
  } finally {
    console.error = originalError;
  }

  assert.ok(threw, 'acquireLock must throw (fail-closed) rather than proceed unlocked');
  assert.ok(fs.existsSync(lockDir), 'the live holder\'s lock dir must still exist (not broken)');
  const marker = fs.readFileSync(path.join(lockDir, 'pid-timestamp.txt'), 'utf8');
  assert.ok(marker.startsWith(`${process.pid}\n`), 'lock marker should be untouched (still the original live pid)');
  assert.ok(
    !logs.some((l) => l.includes('breaking lock')),
    'should never log a "breaking lock" message for a live-owner lock'
  );

  fs.rmSync(path.dirname(filePath), { recursive: true, force: true });
});

test('stale timestamp + missing/garbage pid: lock is broken with a warning logged', async (t) => {
  const filePath = createTempTarget();
  const lockDir = plantLock(filePath, { pid: 'not-a-pid', ageMs: STALE_AGE_MS });

  const originalError = console.error;
  const logs = [];
  console.error = (...args) => logs.push(args.join(' '));

  let acquired;
  try {
    acquired = acquireLock(filePath, { timeoutMs: 5000 });
  } finally {
    console.error = originalError;
  }

  assert.strictEqual(acquired, lockDir, 'acquireLock should reclaim the unparseable-pid lock');
  assert.ok(
    logs.some((l) => l.includes('unparseable pid')),
    'should log a warning about the missing/unparseable pid'
  );
  assert.ok(
    logs.some((l) => l.includes('Stale lock detected') && l.includes('breaking lock')),
    'should also log the stale-lock-broken warning'
  );

  releaseLock(acquired);
  fs.rmSync(path.dirname(filePath), { recursive: true, force: true });
});

test('fresh (non-stale) live-owned lock is left alone (regression guard)', async (t) => {
  const filePath = createTempTarget();
  const lockDir = plantLock(filePath, { pid: process.pid, ageMs: 1000 }); // 1s old, well under threshold

  let threw = false;
  try {
    acquireLock(filePath, { timeoutMs: 150 });
  } catch {
    threw = true;
  }

  assert.ok(threw, 'acquireLock must fail-closed on a fresh, still-live lock rather than break it');
  assert.ok(fs.existsSync(lockDir), 'fresh lock dir should still exist');

  fs.rmSync(path.dirname(filePath), { recursive: true, force: true });
});

// Helper: CPU time (user + system) consumed by this process so far, in ms.
function cpuMs() {
  const usage = process.cpuUsage();
  return (usage.user + usage.system) / 1000;
}

test('contended acquire sleeps during backoff instead of busy-spinning a core', async () => {
  const filePath = createTempTarget();
  const lockDir = plantLock(filePath, { pid: process.pid, ageMs: 1000 }); // live owner, not stale
  const timeoutMs = 600;

  const cpuBefore = cpuMs();
  const wallBefore = Date.now();
  let threw = false;
  try {
    acquireLock(filePath, { timeoutMs });
  } catch {
    threw = true;
  }
  const wallMs = Date.now() - wallBefore;
  const cpuUsedMs = cpuMs() - cpuBefore;

  assert.ok(threw, 'lock held by a live owner must fail closed at the deadline');
  assert.ok(wallMs >= timeoutMs - 50, `should wait roughly the full timeout (wall ${wallMs}ms)`);
  assert.ok(
    cpuUsedMs < wallMs * 0.5,
    `backoff must yield the CPU, not spin: cpu ${cpuUsedMs.toFixed(0)}ms over wall ${wallMs}ms`
  );
  assert.ok(fs.existsSync(lockDir), 'live owner lock must be untouched');

  fs.rmSync(path.dirname(filePath), { recursive: true, force: true });
});

test('contended acquire succeeds once the holder releases mid-wait', async () => {
  const filePath = createTempTarget();
  const lockDir = plantLock(filePath, { pid: process.pid, ageMs: 1000 });

  // A separate OS process removes the lock ~300ms from now. acquireLock blocks
  // this thread while waiting, so the release must come from another process.
  const releaser = spawn(
    process.execPath,
    ['-e', "setTimeout(() => require('node:fs').rmSync(process.argv[1], { recursive: true, force: true }), 300)", lockDir],
    { stdio: 'ignore' }
  );
  releaser.unref();

  const acquired = acquireLock(filePath, { timeoutMs: 10000 });
  assert.strictEqual(acquired, lockDir, 'acquireLock should return the lock dir once released');
  const marker = fs.readFileSync(path.join(lockDir, 'pid-timestamp.txt'), 'utf8');
  assert.ok(marker.startsWith(`${process.pid}\n`), 'acquired lock marker should record our own pid');

  releaseLock(acquired);
  assert.ok(!fs.existsSync(lockDir), 'releaseLock should remove the lock we own');
  fs.rmSync(path.dirname(filePath), { recursive: true, force: true });
});
