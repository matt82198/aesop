// Peer bus (v3): the TUI dispatches work to Codex CLI (a visible launch window running codex exec),
// the Codex app (codex_send.py to an explicit thread) and the agy CLI (console_send.py to a pid).
// Every dispatch writes kind:'peer' start/end events to the activity feed, which the TUI tails and draws.
// Peer stdout is captured and discarded: it is never written to the feed, the transcript or the screen.
// Sender command paths are env-overridable (AESOP_PEER_PYTHON, AESOP_PEER_CODEX_SEND, AESOP_PEER_CONSOLE_SEND,
// AESOP_PEER_LAUNCH_TUI) so tests drive the real code path against stubs. Stdlib only.
import { spawn } from 'node:child_process';
import { appendFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, watch, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { feedPath, parseLines } from './activity_feed.mjs';
import { peerOutcome } from './peer_format.mjs';

/** Slash commands owned by the peer bus. */
export const PEER_COMMANDS = new Set(['codex', 'codex-app', 'agy', 'peers', 'use']);

/** Defaults written on first use of ~/.aesop/peers.json (or AESOP_PEERS). */
export const DEFAULT_PEERS = Object.freeze({
  codex_app_thread: '01a200cd-7eb3-7281-8bcd-47b29dbe4f39',
  agy_pid: 77648,
  codex_handoff_dir: 'C:/Users/Mattt/Desktop/aesop-tui-handoff/codex-handoffs',
});

const CODEX_TIMEOUT_MS = 6 * 60 * 60 * 1000;

/** A bad peers config: the message names the file. */
export class PeersConfigError extends Error {}
/** A dispatch that is refused before anything is sent; the message starts with "refused:". */
export class PeerRefused extends Error {}
/** A missing, unreadable, or empty worker roster file; the message starts with "ROSTER-MISSING:". */
export class RosterError extends Error {}

const homeOf = (env = process.env) => env.HOME || env.USERPROFILE || homedir();
export const peersConfigPath = (env = process.env) => env.AESOP_PEERS || join(homeOf(env), '.aesop', 'peers.json');
export const rosterPath = (env = process.env) => env.WORKER_ROSTER_FILE || join(env.AESOP_HOME || join(homeOf(env), '.claude'), 'docs', 'worker-roster.md');
export const slugifyKind = (kind) => String(kind).trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');

export function parseWorkerRoster(text) {
  let direction = null;
  const rows = [];
  for (const line of String(text).split(/\r?\n/)) {
    const dirMatch = /^\s*direction:\s*(.+?)\s*$/i.exec(line);
    if (dirMatch && direction === null) direction = dirMatch[1];
    const trimmed = line.trim();
    if (!trimmed.startsWith('|')) continue;
    const cells = trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
    if (cells.length < 3) continue;
    const [kind, invoke, when] = cells;
    if (kind.toLowerCase() === 'kind' || cells.every((c) => /^:?-{2,}:?$/.test(c))) continue;
    const id = slugifyKind(kind);
    if (id && invoke && when) rows.push({ id, kind, invoke, when });
  }
  return { direction, rows };
}

export function loadWorkerRoster(env = process.env) {
  const file = rosterPath(env);
  let text;
  try { text = readFileSync(file, 'utf8'); } catch { throw new RosterError(`ROSTER-MISSING: cannot read worker roster at ${file}`); }
  const { direction, rows } = parseWorkerRoster(text);
  if (rows.length === 0) throw new RosterError(`ROSTER-MISSING: worker roster has no kind rows at ${file}`);
  return { file, direction, rows };
}

export function resolveRosterKind(rows, rawQuery) {
  const q = String(rawQuery ?? '').trim().toLowerCase();
  const qSlug = slugifyKind(q);
  if (!qSlug) return null;
  const exact = rows.find((r) => r.id === qSlug || r.kind.toLowerCase() === q);
  if (exact) return exact;
  const prefixed = rows.filter((r) => r.id.startsWith(`${qSlug}-`));
  return prefixed.length === 1 ? prefixed[0] : null;
}

function parseUseArg(arg) {
  const t = String(arg ?? '').trim();
  if (!t) return null;
  if (t[0] === '"' || t[0] === "'") {
    const close = t.indexOf(t[0], 1);
    const [kind, task] = close > 1 ? [t.slice(1, close).trim(), t.slice(close + 1).trim()] : ['', ''];
    return kind && task ? { kind, task } : null;
  }
  const sp = t.search(/\s/);
  const [kind, task] = sp === -1 ? ['', ''] : [t.slice(0, sp).trim(), t.slice(sp + 1).trim()];
  return kind && task ? { kind, task } : null;
}

/**
 * Reads the peers config, creating it with DEFAULT_PEERS on first use. A bad file throws PeersConfigError.
 * @param {object} [env]
 * @returns {{codex_app_thread: string, agy_pid: number, codex_handoff_dir: string}}
 */
export function loadPeersConfig(env = process.env, { createIfMissing = true } = {}) {
  const file = peersConfigPath(env);
  if (!existsSync(file)) {
    if (!createIfMissing) return { ...DEFAULT_PEERS };
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, `${JSON.stringify(DEFAULT_PEERS, null, 2)}\n`);
    return { ...DEFAULT_PEERS };
  }
  let raw;
  try {
    raw = JSON.parse(readFileSync(file, 'utf8'));
  } catch (err) {
    throw new PeersConfigError(`bad peers config ${file}: ${err.message}`);
  }
  const problems = [];
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    problems.push('not a JSON object');
  } else {
    if (typeof raw.codex_app_thread !== 'string') problems.push('codex_app_thread must be a string');
    if (!Number.isInteger(raw.agy_pid) || raw.agy_pid <= 0) problems.push('agy_pid must be a positive integer');
    if (typeof raw.codex_handoff_dir !== 'string' || raw.codex_handoff_dir === '') problems.push('codex_handoff_dir must be a non-empty string');
  }
  if (problems.length > 0) throw new PeersConfigError(`bad peers config ${file}: ${problems.join('; ')}`);
  return { codex_app_thread: raw.codex_app_thread, agy_pid: raw.agy_pid, codex_handoff_dir: raw.codex_handoff_dir };
}

/** Why a /agy message cannot be sent, or null when it is one printable ASCII line. */
function agyRefusal(body) {
  if (/[\r\n]/.test(body)) return 'takes one line (no newlines)';
  if (!/^[\x20-\x7e]+$/.test(body)) return 'takes printable ASCII only';
  if (body.trim() === '') return 'message is empty';
  return null;
}

function assertDispatchable(peer, body, cfg, env) {
  if (peer === 'codex-app' && !cfg.codex_app_thread) {
    throw new PeerRefused(`refused: /codex-app has no codex_app_thread in ${peersConfigPath(env)}; nothing sent`);
  }
  if (peer === 'agy') {
    const why = agyRefusal(body);
    if (why) throw new PeerRefused(`refused: /agy ${why}; nothing sent`);
  }
}

/** Forward-slash form for paths written into bash scripts. */
const posix = (p) => p.replace(/\\/g, '/');
const pyCmd = (env) => env.AESOP_PEER_PYTHON || 'python';
const scriptFor = (env, key, name) => env[key] || join(homedir(), 'scripts', name);

function newJobId(peer) {
  return `${peer}-${Date.now().toString(36)}-${randomBytes(2).toString('hex')}`;
}

/** Appends one feed line. A feed failure never propagates; it reports false. */
function writeFeed(env, ev) {
  try {
    const file = feedPath(env);
    mkdirSync(dirname(file), { recursive: true });
    appendFileSync(file, `${JSON.stringify(ev)}\n`);
    return true;
  } catch {
    return false;
  }
}

/** Runs a command, capturing stdout for the SENT id only. Nothing is ever echoed. */
function spawnCapture(cmd, args, env, extra) {
  return new Promise((resolve) => {
    let stdout = '';
    let child;
    try {
      child = spawn(cmd, args, { env: { ...env, ...extra }, windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
    } catch {
      resolve({ code: null, stdout, error: 'spawn error' });
      return;
    }
    child.stdout.on('data', (d) => { stdout += d; });
    child.on('error', () => resolve({ code: null, stdout, error: 'spawn error' }));
    child.on('close', (code) => resolve({ code, stdout }));
  });
}

/**
 * Resolves with the codex exit code once launch.sh writes <dir>/<job>.exit. Driven by fs.watch on the
 * handoff directory; the existence check runs once after the watch starts, so a file that landed first
 * still completes. The timeout bounds a window that was closed before codex finished.
 */
function waitForExitFile(dir, job, timeoutMs) {
  const name = `${job}.exit`;
  const file = join(dir, name);
  return new Promise((resolve) => {
    let watcher = null;
    let settled = false;
    const timer = setTimeout(() => settle({ exit: null, reason: 'timeout' }), timeoutMs);
    const settle = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { watcher?.close(); } catch { /* ignore */ }
      resolve(value);
    };
    const check = () => {
      let text;
      try { text = readFileSync(file, 'utf8').trim(); } catch { return; }
      if (text === '') return;
      const code = Number(text);
      settle(Number.isInteger(code) ? { exit: code } : { exit: null, reason: 'bad exit file' });
    };
    try {
      watcher = watch(dir, (_event, changed) => { if (!changed || changed === name) check(); });
      watcher.on('error', () => {});
    } catch { /* the timeout still bounds the wait */ }
    check();
  });
}

function finish(env, { peer, job, started, now, status, exit, sent, reason }) {
  const ev = { job, type: 'end', kind: 'peer', to: peer, status, exit, elapsed_ms: Math.max(0, now() - started) };
  if (sent) ev.sent = sent;
  if (reason) ev.reason = reason;
  writeFeed(env, ev);
  return { job, peer, status, exit, elapsedMs: ev.elapsed_ms, sent, reason };
}

async function runSenderJob(ctx, cfg) {
  const { peer, body, env } = ctx;
  let args;
  let extra = {};
  let cleanup = () => {};
  if (peer === 'codex-app') {
    args = [scriptFor(env, 'AESOP_PEER_CODEX_SEND', 'codex_send.py'), '--thread', cfg.codex_app_thread, '--message', body];
  } else {
    const dir = mkdtempSync(join(tmpdir(), 'aesop-agy-'));
    const msg = join(dir, 'msg.txt');
    writeFileSync(msg, body);
    args = [scriptFor(env, 'AESOP_PEER_CONSOLE_SEND', 'console_send.py'), 'send', String(cfg.agy_pid), msg];
    extra = { PYTHONIOENCODING: 'utf-8' };
    cleanup = () => { try { rmSync(dir, { recursive: true, force: true }); } catch { /* best effort */ } };
  }
  const res = await spawnCapture(pyCmd(env), args, env, extra);
  cleanup();
  const sent = /^SENT (\S+)/m.exec(res.stdout)?.[1];
  const ok = res.code === 0;
  return finish(env, {
    ...ctx, status: ok ? 'ok' : 'failed', exit: res.code, sent, reason: res.code === null ? res.error : undefined,
  });
}

async function runCodex(ctx, cfg) {
  const { job, body, env } = ctx;
  const dir = cfg.codex_handoff_dir;
  mkdirSync(dir, { recursive: true });
  // Written before the first await: the handoff exists as soon as the call returns.
  const md = join(dir, `${job}.md`);
  const run = join(dir, `run-${job}.sh`);
  writeFileSync(md, body);
  writeFileSync(run, `#!/usr/bin/env bash\nexec bash ${posix(join(dir, 'launch.sh'))} ${posix(md)}\n`);
  const topic = body.split('\n')[0].replace(/["\r]/g, '').slice(0, 40);
  const launcher = scriptFor(env, 'AESOP_PEER_LAUNCH_TUI', 'launch_tui.py');
  const launched = await spawnCapture(pyCmd(env), [launcher, '--script', run, '--title', `codex: ${topic}`], env, {});
  if (launched.code !== 0) {
    return finish(env, { ...ctx, status: 'failed', exit: launched.code, reason: launched.code === null ? 'spawn error' : undefined });
  }
  const timeoutMs = Number(env.AESOP_PEER_CODEX_TIMEOUT_MS) > 0 ? Number(env.AESOP_PEER_CODEX_TIMEOUT_MS) : CODEX_TIMEOUT_MS;
  const done = await waitForExitFile(dir, job, timeoutMs);
  return finish(env, { ...ctx, status: done.exit === 0 ? 'ok' : 'failed', exit: done.exit, reason: done.reason });
}

/**
 * Dispatches one peer job and resolves when it ends. The start event is written before any sender runs.
 * Throws PeersConfigError or PeerRefused before anything is written.
 * @param {{peer: 'codex'|'codex-app'|'agy', body: string, env?: object, now?: () => number}} o
 */
export async function runPeer({ peer, body, env = process.env, now = Date.now }) {
  const cfg = loadPeersConfig(env);
  assertDispatchable(peer, body, cfg, env);
  const job = newJobId(peer);
  const started = now();
  writeFeed(env, {
    job, type: 'start', kind: 'peer', dir: 'out', from: 'tui', to: peer, ts: started,
    model: '', prompt: body, prompt_bytes: Buffer.byteLength(body), stdin_preview: '', stdin_bytes: 0,
  });
  const ctx = { peer, job, body, started, now, env };
  return peer === 'codex' ? runCodex(ctx, cfg) : runSenderJob(ctx, cfg);
}

export function orchestratorInboxPath(env = process.env) {
  if (env.AESOP_ORCHESTRATOR_INBOX) return { file: env.AESOP_ORCHESTRATOR_INBOX, explicit: true };
  const condRoot = env.CONDUCTOR_ROOT || env.AESOP_CONDUCTOR3_ROOT || join(homeOf(env), 'conductor3'); // default orchestrator inbox root when present
  const stateDir = join(condRoot, 'state');
  return existsSync(stateDir) ? { file: join(stateDir, 'ui-inbox.md'), explicit: false } : { file: null, explicit: false };
}

function appendInboxLine(inbox, isoTs, from, text) {
  if (!inbox.file) return false;
  try {
    if (inbox.explicit) mkdirSync(dirname(inbox.file), { recursive: true });
    appendFileSync(inbox.file, `- [${isoTs}] ${from}: ${String(text).replace(/\r?\n/g, ' / ').trim()}\n`, 'utf8');
    return true;
  } catch { return false; }
}

export function postToOrchestrator({ from = 'matt', text, env = process.env, now = Date.now }) {
  const [job, started] = [newJobId('orchestrator'), now()];
  const [isoTs, inbox] = [new Date(started).toISOString(), orchestratorInboxPath(env)];
  const feedOk = writeFeed(env, { job, type: 'start', kind: 'peer', dir: 'out', from, to: 'orchestrator', ts: started, model: '', prompt: text, prompt_bytes: Buffer.byteLength(text), stdin_preview: '', stdin_bytes: 0 });
  const inboxOk = appendInboxLine(inbox, isoTs, from, text);
  const ok = inbox.explicit ? inboxOk : (feedOk || inboxOk);
  const res = finish(env, { peer: 'orchestrator', job, started, now, status: ok ? 'ok' : 'failed', exit: ok ? 0 : 1, sent: inboxOk ? isoTs : (feedOk ? 'feed' : undefined), reason: ok ? undefined : 'bus not writable' });
  return { ...res, ok };
}

export function routePeerViaOrchestrator({ kindQuery, task, from = 'matt', env = process.env, now = Date.now }) {
  const roster = loadWorkerRoster(env);
  const row = resolveRosterKind(roster.rows, kindQuery);
  if (!row) throw new PeerRefused(`refused: unknown worker kind "${kindQuery}" (available in roster: ${roster.rows.map((r) => r.id).join(', ')}); nothing sent`);
  return postToOrchestrator({ from, text: `use ${row.kind} (${row.id}): ${task}`, env, now });
}

/** Slash-command entry used by chat/cli.mjs. Refusals and config errors go to notify and resolve null. */
export async function runPeerCommand({ command, arg = '', env = process.env, notify = () => {} }) {
  try {
    if (command === 'peers') { for (const line of peerStatusLines(env)) notify(line); return null; }
    if (command === 'use') {
      const parsed = parseUseArg(arg);
      if (!parsed) { notify('usage: /use <kind> <task> (see /peers)'); return null; }
      return routePeerViaOrchestrator({ kindQuery: parsed.kind, task: parsed.task, from: 'matt', env });
    }
    if (arg === '') { notify(`usage: /${command} <text>`); return null; }
    return await runPeer({ peer: command, body: arg, env });
  } catch (err) {
    if (err instanceof PeersConfigError || err instanceof PeerRefused || err instanceof RosterError) notify(err.message);
    else notify(`error: ${err && err.message ? err.message : err}`);
    return null;
  }
}

/** Last end (or inbound post) event per peer name, read from the feed. */
function lastPeerEvents(env) {
  let text;
  try {
    text = readFileSync(feedPath(env), 'utf8');
  } catch {
    return new Map();
  }
  const last = new Map();
  for (const ev of parseLines(text)) {
    if (ev.kind !== 'peer') continue;
    if (ev.type === 'end' && ev.to) last.set(ev.to, ev);
    else if (ev.type === 'start' && ev.dir === 'in' && ev.from) last.set(ev.from, ev);
  }
  return last;
}

/** Lines for /peers and `aesop peers`: the worker roster + peer-bus commands, then config and last status per peer. */
export function peerStatusLines(env = process.env, { createConfig = true } = {}) {
  const lines = [];
  try {
    const roster = loadWorkerRoster(env);
    lines.push(`worker roster: ${roster.file}`);
    if (roster.direction) lines.push(`direction: ${roster.direction}`);
    lines.push('route via orchestrator: /use <kind> <task>  (CLI: aesop peers use <kind> "<task>" | aesop post --to orchestrator "<text>")');
    lines.push('direct TUI peer bus: /codex <task> | /codex-app <msg> | /agy <msg>');
    for (const row of roster.rows) {
      lines.push(`[${row.id}] ${row.kind} - ${row.when}`, `  invoke: ${row.invoke}`, `  bus:    /use ${row.id} <task>`);
    }
  } catch (err) {
    if (err instanceof RosterError) lines.push(`worker roster: unavailable (${rosterPath(env)})`);
    else throw err;
  }
  const cfg = loadPeersConfig(env, { createIfMissing: createConfig });
  const last = lastPeerEvents(env);
  lines.push(`peers config: ${peersConfigPath(env)}`, `codex_app_thread: ${cfg.codex_app_thread || '(not set)'}`, `agy_pid: ${cfg.agy_pid}`, `codex_handoff_dir: ${cfg.codex_handoff_dir}`);
  for (const peer of ['orchestrator', 'codex', 'codex-app', 'agy']) {
    const ev = last.get(peer);
    lines.push(ev ? `${peer}: last ${ev.job} ${peerOutcome(ev)}` : `${peer}: no jobs recorded`);
  }
  return lines;
}

const PEERS_HELP = 'Usage: aesop peers [--json] | aesop peers use <kind> "<task>" | aesop post --to orchestrator "<msg>"\nInside `aesop chat`: /peers | /use <kind> <task> | /codex <task> | /codex-app <msg> | /agy <msg>';

export async function peersCli({ argv, env = process.env, stdout, stderr }) {
  if (argv.includes('--help') || argv.includes('-h')) { stdout.write(`${PEERS_HELP}\n`); return 0; }
  if (argv[0] === 'use') {
    const kindQuery = argv[1];
    const task = argv.slice(2).join(' ').trim();
    if (!kindQuery || !task) { stderr.write('usage: aesop peers use <kind> "<task>"\n'); return 2; }
    try {
      const res = routePeerViaOrchestrator({ kindQuery, task, from: 'matt', env });
      if (!res.ok) { stderr.write('aesop peers: peer bus not writable\n'); return 1; }
      stdout.write(`posted ${res.job}\n`);
      return 0;
    } catch (err) {
      stderr.write(`aesop peers: ${err && err.message ? err.message : err}\n`);
      return err instanceof RosterError ? 1 : 2;
    }
  }
  const asJson = argv.length === 1 && argv[0] === '--json';
  if (argv.length > 0 && !asJson) { stderr.write('usage: aesop peers [--json] | aesop peers use <kind> "<task>"\n'); return 2; }
  try {
    const roster = loadWorkerRoster(env);
    if (!asJson) { stdout.write(`${peerStatusLines(env, { createConfig: false }).join('\n')}\n`); return 0; }
    const last = lastPeerEvents(env);
    const kinds = roster.rows.map((r) => ({ ...r, tuiUse: `/use ${r.id} <task>`, cliUse: `aesop peers use ${r.id} "<task>"` }));
    const lastStatus = Object.fromEntries(['orchestrator', 'codex', 'codex-app', 'agy'].map((p) => [p, last.get(p) ?? null]));
    stdout.write(`${JSON.stringify({ rosterFile: roster.file, direction: roster.direction, kinds, directTui: ['/codex <task>', '/codex-app <msg>', '/agy <msg>'], peersConfig: loadPeersConfig(env, { createIfMissing: false }), lastStatus }, null, 2)}\n`);
    return 0;
  } catch (err) {
    stderr.write(`aesop peers: ${err && err.message ? err.message : err}\n`);
    return err instanceof RosterError ? 1 : 2;
  }
}

/**
 * Writes one inbound peer event (a peer reporting done or blocked). Returns the job id, or null when the
 * feed is not writable.
 * @param {{from: string, text: string, env?: object, now?: () => number}} o
 */
export function postPeerMessage({ from, text, env = process.env, now = Date.now }) {
  const job = newJobId(from);
  const ok = writeFeed(env, {
    job, type: 'start', kind: 'peer', dir: 'in', from, to: 'tui', ts: now(),
    prompt: text, prompt_bytes: Buffer.byteLength(text), status: 'ok', exit: 0, elapsed_ms: 0,
  });
  return ok ? job : null;
}

/**
 * `aesop post --from <name> "<text>"` or `aesop post --to orchestrator [--from <name>] "<text>"`.
 * Returns the exit code: 0 posted, 1 feed not writable, 2 usage.
 * @param {{argv: string[], env?: object, stdout: {write: Function}, stderr: {write: Function}}} o
 */
export async function postCli({ argv, env = process.env, stdout, stderr }) {
  let from;
  let to;
  const words = [];
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--from') {
      from = argv[i + 1];
      i += 1;
    } else if (argv[i] === '--to') {
      to = argv[i + 1];
      i += 1;
    } else {
      words.push(argv[i]);
    }
  }
  const text = words.join(' ').trim();
  const usage = 'usage: aesop post --from <peer> "<text>" | aesop post --to orchestrator [--from <name>] "<text>"\n';
  if (to !== undefined) {
    const caller = from ?? 'matt';
    if (to !== 'orchestrator' || !/^[A-Za-z0-9_-]+$/.test(caller) || text === '') { stderr.write(usage); return 2; }
    const res = postToOrchestrator({ from: caller, text, env });
    if (!res.ok) { stderr.write('aesop post: peer bus not writable\n'); return 1; }
    stdout.write(`posted ${res.job}\n`);
    return 0;
  }
  if (!from || !/^[A-Za-z0-9_-]+$/.test(from) || text === '') {
    stderr.write(usage);
    return 2;
  }
  const job = postPeerMessage({ from, text, env });
  if (!job) {
    stderr.write('aesop post: activity feed not writable\n');
    return 1;
  }
  stdout.write(`posted ${job}\n`);
  return 0;
}
