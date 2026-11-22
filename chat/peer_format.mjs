// Pure formatting for peer-bus feed rows (chat/peer_bus.mjs writes them, chat/render.mjs draws them).
// No I/O here, so the renderer and the /peers command agree on one outcome string.

/**
 * Human duration: "12s" under a minute, "4m03s" from a minute up. Negative or missing values read as 0s.
 * @param {number} ms
 * @returns {string}
 */
export function formatDuration(ms) {
  const total = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  if (total < 60) return `${total}s`;
  return `${Math.floor(total / 60)}m${String(total % 60).padStart(2, '0')}s`;
}

/**
 * Outcome text for a finished peer job or a posted message: "ok (exit 0, 4m03s)" or "FAILED (exit 2, 12s)".
 * A missing exit code falls back to the failure reason (for example "timeout").
 * @param {{status?: string, exit?: number|null, reason?: string, elapsed_ms?: number, elapsedMs?: number}} ev
 * @returns {string}
 */
export function peerOutcome(ev) {
  const elapsed = ev.elapsed_ms ?? ev.elapsedMs;
  const detail = ev.exit !== null && ev.exit !== undefined ? `exit ${ev.exit}` : (ev.reason ?? 'no exit');
  const label = ev.status === 'ok' ? 'ok' : 'FAILED';
  return `${label} (${detail}, ${formatDuration(elapsed)})`;
}
