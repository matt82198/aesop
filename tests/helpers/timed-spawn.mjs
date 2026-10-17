// Timed wrapper around child_process.spawnSync for the scaffold test files.
//
// Purpose: when a spawned child hangs and the owning file hits its timeout, node
// --test prints nothing about which child was running (the file never finishes,
// and a timed-out spawnSync's stderr is discarded). This helper writes a
// diagnostic straight to process.stderr so it streams to the CI log immediately,
// even when the file never completes.
//
// Observability only: the spawn options, the timeout, and the returned result
// are passed through unchanged.

import { spawnSync } from 'node:child_process';

/** Number of trailing characters of stderr/stdout included on failure. */
const TAIL_CHARS = 2000;

/**
 * Return the last TAIL_CHARS characters of a spawnSync output value.
 * @param {string | Buffer | null | undefined} value
 * @returns {string}
 */
function tail(value) {
  if (value === null || value === undefined) return '';
  const text = Buffer.isBuffer(value) ? value.toString('utf8') : String(value);
  return text.length > TAIL_CHARS ? text.slice(-TAIL_CHARS) : text;
}

/**
 * Run spawnSync and report its outcome to process.stderr.
 *
 * On `result.error` (including ETIMEDOUT) or a non-zero status, writes one header
 * line `[timed-spawn] <label> elapsed=<ms>ms status=<status> signal=<signal>
 * error=<code>` followed by the tail of stderr and stdout. On success, writes a
 * single `ok` line only when AESOP_TEST_SPAWN_TIMING=1.
 *
 * @param {string} cmd - Executable to run.
 * @param {string[]} args - Arguments passed to the executable.
 * @param {import('node:child_process').SpawnSyncOptions} opts - Passed to spawnSync unchanged.
 * @param {string} label - Short name identifying this spawn in the log (e.g. "scaffold").
 * @returns {import('node:child_process').SpawnSyncReturns<string | Buffer>} The spawnSync result, unchanged.
 */
export function timedSpawnSync(cmd, args, opts, label) {
  const started = Date.now();
  const result = spawnSync(cmd, args, opts);
  const elapsed = Date.now() - started;

  if (result.error || result.status !== 0) {
    const errorCode = result.error ? (result.error.code ?? 'ERR') : 'none';
    const header =
      `[timed-spawn] ${label} elapsed=${elapsed}ms status=${result.status} ` +
      `signal=${result.signal} error=${errorCode}`;
    process.stderr.write(
      `${header}\n--- stderr (tail) ---\n${tail(result.stderr)}` +
        `\n--- stdout (tail) ---\n${tail(result.stdout)}\n`
    );
  } else if (process.env.AESOP_TEST_SPAWN_TIMING === '1') {
    process.stderr.write(`[timed-spawn] ${label} elapsed=${elapsed}ms ok\n`);
  }

  return result;
}
