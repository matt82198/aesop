// Test-only bounded wait: poll named conditions until they all hold, or fail loudly naming the ones
// that never did. It replaces fixed sleeps, so a test waits on the observable its next assertion
// reads instead of on wall time. Never retries the action under test and never swallows a timeout.

/** Default bound for one wait. Far above any healthy turn; well under the runner's per-test timeout. */
export const WAIT_TIMEOUT_MS = 5000;
/** Poll interval. Coarser than it looks on Windows (about 15 ms timer resolution); that is fine. */
export const WAIT_INTERVAL_MS = 5;

/**
 * A named condition: `what` is the text a timeout reports, `ok` is the predicate.
 * @typedef {{what: string, ok: (...args: any[]) => boolean}} Condition
 */

/**
 * Builds a named condition.
 * @param {string} what what is being waited for, as it should read in a timeout message
 * @param {(...args: any[]) => boolean} ok predicate; a throw counts as "not yet"
 * @returns {Condition}
 */
export function seen(what, ok) {
  return { what, ok };
}

function holds(cond, args) {
  try {
    return cond.ok(...args) === true;
  } catch {
    // An observable that is not there yet (for example posts()[0] before any post) is "not yet".
    return false;
  }
}

/**
 * Waits until every condition holds at the same poll. On timeout it throws an Error whose message
 * names each condition that was still unmet, plus `detail()` when given (a snapshot for the log).
 *
 * @param {Condition[]} conds conditions that must all hold
 * @param {{timeoutMs?: number, intervalMs?: number, args?: any[], label?: string, detail?: () => string}} [opts]
 * @returns {Promise<number>} milliseconds waited
 */
export async function waitUntil(conds, opts = {}) {
  const { timeoutMs = WAIT_TIMEOUT_MS, intervalMs = WAIT_INTERVAL_MS, args = [], label = '', detail } = opts;
  if (!Array.isArray(conds) || conds.length === 0) throw new TypeError('waitUntil needs at least one condition');
  for (const c of conds) {
    if (!c || typeof c.what !== 'string' || c.what === '' || typeof c.ok !== 'function') {
      throw new TypeError('every waitUntil condition needs a non-empty `what` and an `ok` predicate');
    }
  }
  const started = Date.now();
  for (;;) {
    const unmet = conds.filter((c) => !holds(c, args));
    if (unmet.length === 0) return Date.now() - started;
    const waited = Date.now() - started;
    if (waited >= timeoutMs) {
      let extra = '';
      if (typeof detail === 'function') {
        try { extra = `\n${detail()}`; } catch { /* the snapshot is best effort */ }
      }
      const head = `wait-until timed out after ${waited} ms${label ? ` (${label})` : ''}; still waiting for:`;
      throw new Error(`${head}\n${unmet.map((c) => `  - ${c.what}`).join('\n')}${extra}`);
    }
    await new Promise((resolve) => { setTimeout(resolve, intervalMs); });
  }
}
