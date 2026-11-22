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
export const PEER_COMMANDS = new Set(['codex', 'codex-app', 'agy', 'peers']);

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

/** Peers config path: AESOP_PEERS, else ~/.aesop/peers.json. */
export function peersConfigPath(env = process.env) {
  return env.AESOP_PEERS || join(homedir(), '.aesop', 'peers.json');
}

/**
 * Reads the peers config, creating it with DEFAULT_PEERS on first use. A bad file throws PeersConfigError.
 * @param {object} [env]
 * @returns {{codex_app_thread: string, agy_pid: number, codex_handoff_dir: string}}
 */
export function loadPeersConfig(env = process.env) {
  const file = peersConfigPath(env);
  if (!existsSync(file)) {
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

/**
 * Slash-command entry used by chat/cli.mjs. Refusals and config errors go to notify and resolve null.
 * @param {{command: string, arg?: string, env?: object, notify?: (text: string) => void}} o
 */
export async function runPeerCommand({ command, arg = '', env = process.env, notify = () => {} }) {
  try {
    if (command === 'peers') {
      for (const line of peerStatusLines(env)) notify(line);
      return null;
    }
    if (arg === '') {
      notify(`usage: /${command} <text>`);
      return null;
    }
    return await runPeer({ peer: command, body: arg, env });
  } catch (err) {
    if (err instanceof PeersConfigError || err instanceof PeerRefused) notify(err.message);
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

/** Lines for /peers: the config, then the last status per peer. */
export function peerStatusLines(env = process.env) {
  const cfg = loadPeersConfig(env);
  const last = lastPeerEvents(env);
  const lines = [
    `peers config: ${peersConfigPath(env)}`,
    `codex_app_thread: ${cfg.codex_app_thread || '(not set)'}`,
    `agy_pid: ${cfg.agy_pid}`,
    `codex_handoff_dir: ${cfg.codex_handoff_dir}`,
  ];
  for (const peer of ['codex', 'codex-app', 'agy']) {
    const ev = last.get(peer);
    lines.push(ev ? `${peer}: last ${ev.job} ${peerOutcome(ev)}` : `${peer}: no jobs recorded`);
  }
  return lines;
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
 * `aesop post --from <name> "<text>"`. Returns the exit code: 0 posted, 1 feed not writable, 2 usage.
 * @param {{argv: string[], env?: object, stdout: {write: Function}, stderr: {write: Function}}} o
 */
export async function postCli({ argv, env = process.env, stdout, stderr }) {
  let from;
  const words = [];
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--from') {
      from = argv[i + 1];
      i += 1;
    } else {
      words.push(argv[i]);
    }
  }
  const text = words.join(' ').trim();
  if (!from || !/^[A-Za-z0-9_-]+$/.test(from) || text === '') {
    stderr.write('usage: aesop post --from <peer> "<text>"\n');
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
