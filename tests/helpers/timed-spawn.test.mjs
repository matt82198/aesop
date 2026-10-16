// Behaviour tests for tests/helpers/timed-spawn.mjs (timedSpawnSync).
// Run: node --test tests/helpers/timed-spawn.test.mjs
//
// Each case drives a real child process and captures what the helper writes to
// process.stderr. No mocks of child_process: the timeout and exit-status paths
// are exercised for real.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { timedSpawnSync } from './timed-spawn.mjs';

/**
 * Run fn while capturing every process.stderr.write call. Restores the original
 * writer even if fn throws.
 * @param {() => unknown} fn
 * @returns {{ result: unknown, output: string }}
 */
function captureStderr(fn) {
  const original = process.stderr.write;
  const chunks = [];
  process.stderr.write = (chunk, ...rest) => {
    chunks.push(String(chunk));
    const cb = rest.find((a) => typeof a === 'function');
    if (cb) cb();
    return true;
  };
  try {
    const result = fn();
    return { result, output: chunks.join('') };
  } finally {
    process.stderr.write = original;
  }
}

/** Run fn with AESOP_TEST_SPAWN_TIMING set to value (or unset when undefined). */
function withTimingEnv(value, fn) {
  const had = Object.prototype.hasOwnProperty.call(process.env, 'AESOP_TEST_SPAWN_TIMING');
  const prev = process.env.AESOP_TEST_SPAWN_TIMING;
  if (value === undefined) delete process.env.AESOP_TEST_SPAWN_TIMING;
  else process.env.AESOP_TEST_SPAWN_TIMING = value;
  try {
    return fn();
  } finally {
    if (had) process.env.AESOP_TEST_SPAWN_TIMING = prev;
    else delete process.env.AESOP_TEST_SPAWN_TIMING;
  }
}

test('timeout produces exactly one diagnostic header line naming label, elapsed, and ETIMEDOUT', () => {
  withTimingEnv(undefined, () => {
    const { result, output } = captureStderr(() =>
      timedSpawnSync(
        process.execPath,
        ['-e', 'setTimeout(() => {}, 5000)'],
        { encoding: 'utf8', timeout: 500, killSignal: 'SIGKILL' },
        'slow'
      )
    );
    assert.equal(result.error?.code, 'ETIMEDOUT', 'precondition: child must time out');
    const headers = output.split('\n').filter((line) => line.startsWith('[timed-spawn] '));
    assert.equal(headers.length, 1, `expected one header line, got:\n${output}`);
    assert.match(headers[0], /\[timed-spawn\] slow elapsed=\d+ms .*error=ETIMEDOUT/);
  });
});

test('timeout result is returned unchanged (error and signal preserved)', () => {
  withTimingEnv(undefined, () => {
    const { result } = captureStderr(() =>
      timedSpawnSync(
        process.execPath,
        ['-e', 'setTimeout(() => {}, 5000)'],
        { encoding: 'utf8', timeout: 500, killSignal: 'SIGKILL' },
        'slow'
      )
    );
    assert.equal(result.error.code, 'ETIMEDOUT');
    assert.equal(result.signal, 'SIGKILL');
  });
});

test('non-zero exit writes a header carrying the status and the stderr tail', () => {
  withTimingEnv(undefined, () => {
    const { result, output } = captureStderr(() =>
      timedSpawnSync(
        process.execPath,
        ['-e', 'process.stderr.write("boom-marker"); process.exit(3)'],
        { encoding: 'utf8' },
        'failing'
      )
    );
    assert.equal(result.status, 3);
    assert.match(output, /^\[timed-spawn\] failing elapsed=\d+ms status=3 signal=null error=none$/m);
    assert.match(output, /boom-marker/);
  });
});

test('exit 0 writes nothing when AESOP_TEST_SPAWN_TIMING is unset', () => {
  withTimingEnv(undefined, () => {
    const { result, output } = captureStderr(() =>
      timedSpawnSync(process.execPath, ['-e', ''], { encoding: 'utf8' }, 'quiet')
    );
    assert.equal(result.status, 0);
    assert.equal(output, '');
  });
});

test('exit 0 writes one ok line when AESOP_TEST_SPAWN_TIMING=1', () => {
  withTimingEnv('1', () => {
    const { result, output } = captureStderr(() =>
      timedSpawnSync(process.execPath, ['-e', ''], { encoding: 'utf8' }, 'timed-ok')
    );
    assert.equal(result.status, 0);
    assert.match(output, /^\[timed-spawn\] timed-ok elapsed=\d+ms ok\n$/);
  });
});
