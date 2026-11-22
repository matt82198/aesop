// Cross-process activity feed: every `aesop ask` appends start/chunk/end events to one JSONL file and
// `aesop chat` tails it, so offloaded work shows up in one terminal. Stdlib only. Feed failures never
// propagate: a writer warns once on stderr and goes quiet, a reader just stops seeing events.
import { appendFileSync, closeSync, existsSync, mkdirSync, openSync, readSync, renameSync, statSync, watch } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { basename, dirname, join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { homedir } from 'node:os';

export const ROTATE_BYTES = 5 * 1024 * 1024;
export const PREVIEW_BYTES = 2048;
const PROMPT_CAP = 1024;
const CHUNK_SPLIT = 600; // chars per chunk line, so a line stays far below one atomic append

/**
 * Feed path: AESOP_ACTIVITY_FEED, else ONE per-user file shared by every checkout:
 * <HOME || USERPROFILE || homedir()>/.aesop/state/chat/activity.jsonl.
 * The same rule lives in driver/activity_feed.py (feed_path); keep the two in step. The directory
 * is created lazily by the writer and the tail, never by this function.
 */
export function feedPath(env = process.env) {
  if (env.AESOP_ACTIVITY_FEED) return env.AESOP_ACTIVITY_FEED;
  const home = env.HOME || env.USERPROFILE || homedir();
  return join(home, '.aesop', 'state', 'chat', 'activity.jsonl');
}

const GENERIC = /^(node|nodejs|bash|sh|zsh|dash|fish|cmd|powershell|pwsh|conhost|python3?|env|npm|npx|git-bash|windowsterminal)(\.exe)?$/i;

/** Best-effort name of the parent process; null when unknown or generic. */
function parentName() {
  try {
    let name = '';
    if (process.platform === 'win32') {
      const out = execFileSync('tasklist', ['/FI', `PID eq ${process.ppid}`, '/FO', 'CSV', '/NH'], { timeout: 1500, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], windowsHide: true });
      name = /^"([^"]+)"/.exec(out.trim())?.[1] ?? '';
    } else {
      name = execFileSync('ps', ['-o', 'comm=', '-p', String(process.ppid)], { timeout: 1500, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    }
    name = basename(name);
    return name && !GENERIC.test(name) ? name.replace(/\.exe$/i, '') : null;
  } catch {
    return null;
  }
}

/** Caller label: --from, else AESOP_CALLER, else the parent process name, else "agent". */
export function callerLabel(from, env = process.env) {
  if (from) return from;
  if (env.AESOP_CALLER) return env.AESOP_CALLER;
  return parentName() ?? 'agent';
}

function cap(text, bytes) {
  const buf = Buffer.from(text, 'utf8');
  return buf.length <= bytes ? text : buf.subarray(0, bytes).toString('utf8').replace(/�$/, '');
}

/**
 * Writer for one ask job. Every method swallows failures (one stderr warning at most).
 * @param {{env?: object, stderr?: {write: Function}, job?: string, now?: () => number}} [o]
 */
export function createFeedWriter({ env = process.env, stderr = process.stderr, job, now = Date.now } = {}) {
  const file = feedPath(env);
  const id = job ?? `${now().toString(36)}-${process.pid.toString(36)}-${randomBytes(2).toString('hex')}`;
  let warned = false;
  let seq = 0;
  const put = (obj) => {
    try {
      appendFileSync(file, `${JSON.stringify({ job: id, ...obj })}\n`);
    } catch (err) {
      if (!warned) {
        warned = true;
        try { stderr.write(`aesop ask: activity feed disabled (${err && err.code ? err.code : 'write failed'})\n`); } catch { /* ignore */ }
      }
    }
  };
  const rotate = () => {
    try {
      if (statSync(file).size >= ROTATE_BYTES) renameSync(file, join(dirname(file), `${basename(file, '.jsonl')}.1.jsonl`));
    } catch { /* missing file or lost a rotate race: fine */ }
  };
  return {
    job: id,
    file,
    start({ from, model, prompt, stdin = '' }) {
      try { mkdirSync(dirname(file), { recursive: true }); } catch { /* put() warns */ }
      rotate();
      put({
        type: 'start', ts: now(), from, model,
        prompt: cap(prompt, PROMPT_CAP), prompt_bytes: Buffer.byteLength(prompt),
        stdin_preview: cap(stdin, PREVIEW_BYTES), stdin_bytes: Buffer.byteLength(stdin),
      });
    },
    chunk(text) {
      const chars = [...String(text)];
      for (let i = 0; i < chars.length; i += CHUNK_SPLIT) {
        seq += 1;
        put({ type: 'chunk', seq, text: chars.slice(i, i + CHUNK_SPLIT).join('') });
      }
    },
    error(message) {
      put({ type: 'error', error: cap(String(message), 1024) });
    },
    end({ status, exit, elapsed_ms, error }) {
      const o = { type: 'end', status, exit, elapsed_ms };
      if (error) o.error = cap(String(error), 1024);
      put(o);
    },
  };
}

/** Parses JSONL text into events; malformed or foreign lines are skipped. */
export function parseLines(text) {
  const out = [];
  for (const l of text.split('\n')) {
    if (l === '') continue;
    try {
      const o = JSON.parse(l);
      if (o && typeof o.job === 'string' && typeof o.type === 'string') out.push(o);
    } catch { /* skip */ }
  }
  return out;
}

function readRange(file, from, to) {
  const len = to - from;
  if (len <= 0) return Buffer.alloc(0);
  const fd = openSync(file, 'r');
  try {
    const buf = Buffer.alloc(len);
    const n = readSync(fd, buf, 0, len, from);
    return buf.subarray(0, n);
  } finally {
    closeSync(fd);
  }
}

/** Events of the last `n` jobs (by start order), reading at most the last 1 MB of the feed. */
export function lastJobs(file, n) {
  if (n <= 0) return [];
  try {
    const size = statSync(file).size;
    const from = Math.max(0, size - 1024 * 1024);
    let text = readRange(file, from, size).toString('utf8');
    if (from > 0) text = text.slice(text.indexOf('\n') + 1);
    const events = parseLines(text);
    const order = [];
    for (const e of events) if (!order.includes(e.job)) order.push(e.job);
    const keep = new Set(order.slice(-n));
    return events.filter((e) => keep.has(e.job));
  } catch {
    return [];
  }
}

/**
 * Tails the feed from its current END (no backlog). fs.watch on the directory plus a slow interval
 * fallback; both are released by stop(). No fd stays open between polls.
 * @returns {{stop: () => void, poll: () => void}}
 */
export function tailFeed({ file, onEvent, intervalMs = 1000 }) {
  let offset = 0;
  let partial = '';
  try { offset = statSync(file).size; } catch { offset = 0; }
  let stopped = false;
  const poll = () => {
    if (stopped) return;
    try {
      const size = statSync(file).size;
      if (size < offset) { offset = 0; partial = ''; } // rotated or truncated
      if (size === offset) return;
      const buf = readRange(file, offset, size);
      offset += buf.length;
      const text = partial + buf.toString('utf8');
      const cut = text.lastIndexOf('\n');
      if (cut < 0) { partial = text; return; }
      partial = text.slice(cut + 1);
      for (const ev of parseLines(text.slice(0, cut))) {
        try { onEvent(ev); } catch { /* a bad handler never kills the tail */ }
      }
    } catch { /* file absent or unreadable: try again next tick */ }
  };
  let watcher = null;
  try {
    const dir = dirname(file);
    mkdirSync(dir, { recursive: true });
    if (existsSync(dir)) {
      watcher = watch(dir, { persistent: false }, (_e, name) => { if (!name || name === basename(file)) poll(); });
      watcher.on('error', () => {});
    }
  } catch { /* the interval fallback still covers it */ }
  const timer = setInterval(poll, intervalMs);
  timer.unref();
  return {
    poll,
    stop() {
      stopped = true;
      clearInterval(timer);
      try { watcher?.close(); } catch { /* ignore */ }
      watcher = null;
    },
  };
}

/**
 * Peer-bus rows (kind 'peer'): outbound dispatches start running and end with an outcome; inbound posts
 * arrive finished. Display-only, like ask rows.
 */
function applyPeerEvent(jobs, transcript, ev) {
  let entry = jobs.get(ev.job);
  if (ev.type === 'start') {
    if (entry) return false;
    const inbound = ev.dir === 'in';
    entry = {
      role: 'feed', kind: 'peer', dir: inbound ? 'in' : 'out', job: ev.job,
      peer: (inbound ? ev.from : ev.to) ?? 'peer', body: ev.prompt ?? '', startedAt: ev.ts ?? Date.now(),
      status: null, exit: null, elapsedMs: 0, sent: undefined, reason: undefined,
    };
    if (inbound) {
      entry.status = ev.status ?? 'ok';
      entry.exit = ev.exit ?? null;
      entry.elapsedMs = ev.elapsed_ms ?? 0;
    }
    jobs.set(ev.job, entry);
    transcript.push(entry);
    return true;
  }
  if (!entry) return false;
  if (ev.type === 'end') {
    entry.status = ev.status ?? 'ok';
    entry.exit = ev.exit ?? null;
    entry.elapsedMs = ev.elapsed_ms ?? 0;
    entry.sent = ev.sent;
    entry.reason = ev.reason;
    return true;
  }
  return false;
}

/**
 * Applies a feed event to the transcript. Entries are `role: 'feed'` and live only in the display
 * transcript; they are never handed to a model source. Returns true when the transcript changed.
 * @param {Map<string, object>} jobs job id -> transcript entry
 * @param {object[]} transcript
 * @param {object} ev
 */
export function applyFeedEvent(jobs, transcript, ev) {
  if (ev.kind === 'peer') return applyPeerEvent(jobs, transcript, ev);
  let entry = jobs.get(ev.job);
  if (ev.type === 'start') {
    if (entry) return false;
    entry = {
      role: 'feed', job: ev.job, from: ev.from ?? 'agent', model: ev.model ?? '', prompt: ev.prompt ?? '',
      preview: ev.stdin_preview ?? '', previewBytes: ev.stdin_bytes ?? 0, text: '', status: null,
    };
    jobs.set(ev.job, entry);
    transcript.push(entry);
    return true;
  }
  if (!entry) return false; // a job that began before we tailed: ignore its tail events
  if (ev.type === 'chunk') { entry.text += ev.text ?? ''; return true; }
  if (ev.type === 'error') { entry.error = ev.error; return true; }
  if (ev.type === 'end') {
    entry.status = ev.status ?? 'ok';
    entry.exit = ev.exit;
    entry.elapsedMs = ev.elapsed_ms;
    entry.error = ev.error;
    return true;
  }
  return false;
}
