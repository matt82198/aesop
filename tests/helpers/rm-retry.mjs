/**
 * rm-retry.mjs: retrying recursive removal for test cleanup (row f-2c462710).
 *
 * Exists for the Windows handle-release race: a child process exits, but a file
 * handle inside the tree it held is released a moment later, so an immediate
 * rmSync fails with EPERM / EBUSY / ENOTEMPTY. rmRetrySync retries those codes
 * with a synchronous sleep between attempts.
 *
 * Zero dependencies. ESM.
 */

import fs from 'node:fs';

const RETRYABLE = new Set(['EPERM', 'EBUSY', 'ENOTEMPTY']);

/** Shared buffer used only to block the thread for a synchronous sleep. */
const sleepCell = new Int32Array(new SharedArrayBuffer(4));

/**
 * Synchronously sleep for `ms` milliseconds.
 * @param {number} ms
 */
function sleepSync(ms) {
  Atomics.wait(sleepCell, 0, 0, ms);
}

/**
 * Recursively and forcibly remove `path`, retrying transient Windows lock errors.
 *
 * ENOENT never throws (force handles a missing path). EPERM, EBUSY and ENOTEMPTY
 * are retried up to `maxRetries` attempts total, sleeping `retryDelay` ms between
 * them. After the last failure the last error is rethrown. Any other error is
 * rethrown immediately.
 *
 * @param {string} path Path to remove.
 * @param {{ maxRetries?: number, retryDelay?: number, rm?: Function }} [options]
 * @param {number} [options.maxRetries=10] Maximum number of removal attempts.
 * @param {number} [options.retryDelay=50] Milliseconds to sleep between attempts.
 * @param {Function} [options.rm=fs.rmSync] Removal function, injectable so tests can script per-attempt failures; called as rm(path, { recursive: true, force: true }).
 * @returns {void}
 * @throws {Error} The last removal error once retries are exhausted, or any non-retryable error.
 */
export function rmRetrySync(path, { maxRetries = 10, retryDelay = 50, rm = fs.rmSync } = {}) {
  const attempts = Math.max(1, maxRetries);
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      rm(path, { recursive: true, force: true });
      return;
    } catch (err) {
      if (!err || !RETRYABLE.has(err.code)) throw err;
      lastError = err;
      if (attempt < attempts) sleepSync(retryDelay);
    }
  }
  throw lastError;
}
