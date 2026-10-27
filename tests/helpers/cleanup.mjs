// Non-throwing recursive directory cleanup for test teardown.
//
// Purpose: a Windows EBUSY/EPERM on a locked tree (AV scan, indexer) must never
// replace a test's real assertion failure with a teardown exception. cleanupDir
// retries the removal, and on failure reports one diagnostic line to stderr and
// returns false instead of throwing.

import fs from 'node:fs';

/**
 * Recursively remove `root`, retrying transient Windows locks. Never throws.
 *
 * On failure writes exactly one line `cleanup: <label>: <code> <message>` to
 * process.stderr and returns false.
 *
 * @param {string} root - Directory to remove.
 * @param {{label?: string}} [opts] - `label` names the caller in the log line (defaults to root).
 * @returns {boolean} true when removed (or already absent), false otherwise.
 */
export function cleanupDir(root, { label = root } = {}) {
  try {
    fs.rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
    return true;
  } catch (err) {
    const code = err?.code ?? 'ERR';
    const message = String(err?.message ?? err).split('\n')[0];
    process.stderr.write(`cleanup: ${label}: ${code} ${message}\n`);
    return false;
  }
}
