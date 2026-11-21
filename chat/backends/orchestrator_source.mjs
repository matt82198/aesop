/**
 * Orchestrator chat backend (F1): a turn is appended to the aesop-owned inbox in the ui-inbox
 * line format and the reply is tailed from the bridge file state/chat/<turn_id>.jsonl.
 *
 * Transport (no model, CLI or network call is made here):
 *  - inbox line:  `- [ISO-ts] chat(<turn_id>): <text>` (newlines and backslashes escaped so one turn
 *    is always exactly one line and can never forge another inbox entry);
 *  - reply file:  one JSON object per line `{turn_id, seq, chunk, final}` appended by
 *    tools/chat_bridge.py (the orchestrator side). `final: true` ends the turn.
 * The tail loop is bounded by a deadline (default 120 s, AESOP_CHAT_TIMEOUT_MS) with backoff; on
 * timeout it yields exactly "no orchestrator listening" and returns, so a turn never hangs.
 *
 * Node stdlib only.
 *
 * @module chat/backends/orchestrator_source
 */

import { randomBytes } from 'node:crypto';
import { appendFileSync, closeSync, existsSync, fstatSync, mkdirSync, openSync, readSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/** The one line shown when no orchestrator answers within the timeout. */
export const NO_LISTENER_TEXT = 'no orchestrator listening';
const DEFAULT_TIMEOUT_MS = 120000;
const MIN_POLL_MS = 20;
const MAX_POLL_MS = 250;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Aesop state root: AESOP_STATE_ROOT, else the repo's state/ directory. */
export function resolveStateRoot(env = process.env) {
  if (env.AESOP_STATE_ROOT) return resolve(env.AESOP_STATE_ROOT);
  return join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'state');
}

/** Escapes a turn to one inbox line: backslash, CR and LF become literal escapes. */
export function escapeInboxText(text) {
  return String(text).replace(/\\/g, '\\\\').replace(/\r/g, '\\r').replace(/\n/g, '\\n');
}

function parseTimeout(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

/**
 * Reads complete new lines from `file` starting at byte `offset`. A trailing partial line (no
 * newline yet) is left for the next poll.
 * @returns {{lines: string[], offset: number}}
 */
function readNewLines(file, offset) {
  if (!existsSync(file)) return { lines: [], offset };
  const fd = openSync(file, 'r');
  try {
    const size = fstatSync(fd).size;
    if (size <= offset) return { lines: [], offset };
    const buf = Buffer.alloc(size - offset);
    readSync(fd, buf, 0, buf.length, offset);
    const end = buf.lastIndexOf(0x0a);
    if (end < 0) return { lines: [], offset };
    const lines = buf.subarray(0, end).toString('utf8').split('\n');
    return { lines, offset: offset + end + 1 };
  } finally {
    closeSync(fd);
  }
}

/**
 * Builds the backend. The result is `(line) => AsyncIterable<string>`.
 * @param {{stateRoot?: string, inboxPath?: string, timeoutMs?: number,
 *          env?: Record<string, string|undefined>, onIntent?: Function}} [opts]
 */
export function orchestratorSource({ stateRoot, inboxPath, timeoutMs, env = process.env } = {}) {
  const root = stateRoot ? resolve(stateRoot) : resolveStateRoot(env);
  const inbox = inboxPath ?? (env.AESOP_INBOX_PATH ? resolve(env.AESOP_INBOX_PATH) : join(root, 'ui-inbox.md'));
  const limit = parseTimeout(timeoutMs ?? env.AESOP_CHAT_TIMEOUT_MS, DEFAULT_TIMEOUT_MS);

  return async function* orchestratorTurn(line) {
    const turnId = `t${Date.now().toString(36)}${randomBytes(4).toString('hex')}`;
    const deadline = Date.now() + limit;
    mkdirSync(dirname(inbox), { recursive: true });
    appendFileSync(inbox, `- [${new Date().toISOString()}] chat(${turnId}): ${escapeInboxText(line)}\n`, 'utf8');

    const replyFile = join(root, 'chat', `${turnId}.jsonl`);
    let offset = 0;
    let delay = MIN_POLL_MS;
    while (Date.now() < deadline) {
      const read = readNewLines(replyFile, offset);
      offset = read.offset;
      let progressed = false;
      for (const raw of read.lines) {
        let msg;
        try {
          msg = JSON.parse(raw);
        } catch {
          continue;
        }
        // Correlation: a record for any other turn is never shown in this one.
        if (!msg || msg.turn_id !== turnId) continue;
        progressed = true;
        if (typeof msg.chunk === 'string' && msg.chunk !== '') yield msg.chunk;
        if (msg.final === true) return;
      }
      delay = progressed ? MIN_POLL_MS : Math.min(MAX_POLL_MS, Math.ceil(delay * 1.5));
      await sleep(Math.max(1, Math.min(delay, deadline - Date.now())));
    }
    yield NO_LISTENER_TEXT;
  };
}

/**
 * Picks the chat backend from env: AESOP_CHAT_BACKEND=orchestrator selects this module, anything
 * else (including unset and `pipe`) returns null so the caller keeps its default.
 * @returns {((line: string) => AsyncIterable<string>)|null}
 */
export function selectBackend(env = process.env) {
  return env.AESOP_CHAT_BACKEND === 'orchestrator' ? orchestratorSource({ env }) : null;
}
