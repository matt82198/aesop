/**
 * Driver backend for `aesop chat` (AESOP_CHAT_BACKEND=driver): a thin adapter over the aesop turn engine.
 *
 * PURPOSE: this artifact exists to make the aesop chat TUI, aesop ask and codex/gemini/agy run every turn
 * through the aesop driver (one turn engine; the pipe is the product); status: real
 *
 * Every turn runs in the Python driver (`driver/turn.py --serve`, see driver/TURN.md). This module holds no
 * turn logic: it spawns ONE driver child per session, writes one JSON request per turn on the child's stdin,
 * and reads the AESOP-PIPE/1 frames (stdio/frames.mjs) the child writes back. `data` frames are the text
 * deltas; `control` frames carry the system lines, usage and the end of the turn. Routing, fallback, the
 * circuit breaker, the conversation context, the Ollama window and the provider boundary all live in the driver.
 *
 * The driver is aesop's own engine, never a provider: the only program this module ever starts is the Python
 * interpreter on driver/turn.py from this package. It never starts the `claude` binary and never calls an API.
 *
 * `driverSource` has the same signature and the same observable behaviour as `harnessSource` (chat/harness.mjs);
 * tests/chat-driver-parity.test.mjs runs the same scripted turns through both and compares the transcripts.
 *
 * Node stdlib only; never imports ui/.
 *
 * @module chat/driver_source
 */

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { FrameDecoder } from '../stdio/frames.mjs';

const TURN_SCRIPT = fileURLToPath(new URL('../driver/turn.py', import.meta.url));
const KEY_ENV_RE = /^[A-Z_][A-Z0-9_]*$/;

/**
 * The Python interpreter that runs the driver: AESOP_PYTHON when set, else `python` on Windows and
 * `python3` elsewhere.
 * @param {Record<string, string|undefined>} env
 * @returns {string}
 */
export function pythonCommand(env = process.env) {
  if (env.AESOP_PYTHON) return env.AESOP_PYTHON;
  return process.platform === 'win32' ? 'python' : 'python3';
}

/**
 * The registry as plain data for the driver: provider id -> raw config, and the resolved routes.
 * @param {{providers: Map<string, {raw: object}>, routes: Map<string, object>}} registry
 * @returns {{providers: Record<string, object>, routes: Record<string, object>}}
 */
export function registryPayload(registry) {
  const providers = {};
  for (const [id, entry] of registry.providers) providers[id] = entry.raw;
  const routes = {};
  for (const [task, route] of registry.routes ?? []) routes[task] = route;
  return { providers, routes };
}

/**
 * The key values the turn may need: only env vars that a provider names in `api_key_env`. They travel
 * on the child's stdin with the turn, never on its command line.
 * @param {{providers: Map<string, {raw: object}>}} registry
 * @param {Record<string, string|undefined>} env
 * @returns {Record<string, string>}
 */
function keysFor(registry, env) {
  const keys = {};
  for (const entry of registry.providers.values()) {
    const name = entry.raw.api_key_env;
    if (typeof name === 'string' && KEY_ENV_RE.test(name) && env[name]) keys[name] = String(env[name]);
  }
  return keys;
}

/**
 * Backend source for turns in driver mode. Same contract as harnessSource: an async generator of text
 * deltas per turn; system lines through `options.notify`, one-frame notices through `options.flash`, usage
 * through `options.usage`. The turn throws only when it cannot start (no provider selected, a route with
 * no target). Ctrl-c (`opts.signal`) sends a cancel to the driver, which closes the request and reports
 * `cancelled`.
 *
 * The returned function also has `close()`, which ends the driver child (the session calls it on exit; the
 * child also exits by itself when this process goes away, because its stdin closes).
 *
 * @param {object} registry loadRegistry() result
 * @param {() => {provider: string|null, model: string|null}} getSelection live status selection
 * @param {Record<string, string|undefined>} [env] source of key env vars (api_key_env) and AESOP_PYTHON
 * @param {{notify?: (text: string) => void, flash?: (text: string) => void, usage?: (u: object) => void,
 *          spawn?: typeof spawn}} [options]
 * @returns {((line: string, opts?: {task?: string, signal?: AbortSignal, oneShot?: {context?: string, system?: string}}) => AsyncGenerator<string>) & {close: () => void}}
 */
export function driverSource(registry, getSelection, env = process.env, options = {}) {
  const notify = typeof options.notify === 'function' ? options.notify : () => {};
  const flash = typeof options.flash === 'function' ? options.flash : notify;
  const reportUsage = typeof options.usage === 'function' ? options.usage : () => {};
  const spawnChild = typeof options.spawn === 'function' ? options.spawn : spawn;

  let child = null;
  let nextId = 1;
  /** Turn in flight: {id, queue: object[], wake: (() => void)|null}. The driver runs one turn at a time. */
  const turns = new Map();
  /** Ids sent to the current child that have not reported done, oldest first. */
  const sent = [];

  const push = (turn, item) => {
    turn.queue.push(item);
    if (turn.wake) { const wake = turn.wake; turn.wake = null; wake(); }
  };
  const failAll = (reason) => {
    for (const turn of turns.values()) push(turn, { kind: 'lost', reason });
  };
  // The child holds the event loop only while a turn is in flight, so an idle session can exit.
  const setRef = (on) => {
    if (!child) return;
    for (const handle of [child, child.stdin, child.stdout, child.stderr]) {
      try { if (on) handle?.ref?.(); else handle?.unref?.(); } catch { /* best effort */ }
    }
  };

  const start = () => {
    if (child) return child;
    const decoder = new FrameDecoder();
    const proc = spawnChild(pythonCommand(env), ['-I', TURN_SCRIPT, '--serve'], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    child = proc;
    const gone = (reason) => {
      if (child !== proc) return;
      child = null;
      sent.length = 0;
      failAll(reason);
    };
    proc.on('error', (err) => gone(`cannot start the turn engine (${err.code ?? 'error'})`));
    proc.on('exit', (code) => gone(`the turn engine exited (code ${code ?? '?'})`));
    proc.stdin.on('error', () => { /* a write to a dead child is reported by the exit handler */ });
    proc.stderr.on('data', () => { /* the engine's diagnostics are not part of the transcript */ });
    proc.stdout.on('data', (chunk) => {
      let items;
      try {
        items = decoder.push(chunk);
      } catch {
        gone('the turn engine sent a malformed frame');
        try { proc.kill(); } catch { /* already gone */ }
        return;
      }
      for (const item of items) {
        if (item.kind !== 'frame') continue;
        if (item.channel === 'data') {
          // The driver runs turns one at a time in the order sent, so a delta belongs to the oldest turn
          // that has not reported done. A turn the consumer already abandoned gets its deltas dropped.
          const turn = turns.get(sent[0]);
          if (turn) push(turn, { kind: 'delta', text: item.payload.toString('utf8') });
          continue;
        }
        let msg;
        try { msg = JSON.parse(item.payload.toString('utf8')); } catch { continue; }
        if (msg.type === 'done') {
          const at = sent.indexOf(msg.id);
          if (at >= 0) sent.splice(at, 1);
        }
        const turn = turns.get(msg.id);
        if (!turn) continue;
        if (msg.type === 'event') push(turn, { kind: 'event', event: msg.event, text: msg.text, usage: msg.usage });
        else if (msg.type === 'done') push(turn, { kind: 'done', msg });
      }
    });
    return proc;
  };

  const send = (obj) => {
    try { child?.stdin.write(`${JSON.stringify(obj)}\n`); } catch { /* reported by the exit handler */ }
  };

  async function* driverTurn(line, opts = {}) {
    const sel = getSelection();
    if (!sel.provider) throw new Error('no provider configured (see /help)');
    const signal = opts.signal;
    const turn = { id: nextId, queue: [], wake: null };
    nextId += 1;
    start();
    turns.set(turn.id, turn);
    setRef(true);
    const request = {
      op: 'turn', id: turn.id, task: opts.task ?? 'chat',
      selection: { provider: sel.provider, model: sel.model },
      registry: registryPayload(registry), keys: keysFor(registry, env),
    };
    if (opts.oneShot) {
      // A one-shot turn (aesop ask) sends its parts; the driver assembles the messages (driver/TURN.md).
      request.prompt = String(line);
      request.context = String(opts.oneShot.context ?? '');
      if (opts.oneShot.system) request.system = String(opts.oneShot.system);
    } else {
      request.line = String(line);
    }
    // Tests pin the clock through globalThis.__AESOP_CHAT_NOW (the same hook the harness reads).
    if (typeof globalThis.__AESOP_CHAT_NOW === 'function') request.now = globalThis.__AESOP_CHAT_NOW();
    const onAbort = () => send({ op: 'cancel', id: turn.id });
    let finished = false;
    sent.push(turn.id);
    send(request);
    if (signal?.aborted) onAbort();
    else signal?.addEventListener?.('abort', onAbort, { once: true });
    try {
      for (;;) {
        if (turn.queue.length === 0) await new Promise((resolve) => { turn.wake = resolve; });
        const item = turn.queue.shift();
        if (item.kind === 'delta') {
          yield item.text;
        } else if (item.kind === 'event') {
          if (item.event === 'notify') notify(item.text);
          else if (item.event === 'flash') flash(item.text);
          else if (item.event === 'usage') reportUsage(item.usage);
        } else if (item.kind === 'done') {
          finished = true;
          if (typeof item.msg.thrown === 'string') throw new Error(item.msg.thrown);
          return;
        } else {
          // The child went away mid-turn: one visible line; the next turn starts a fresh child.
          finished = true;
          notify(`error: driver: connect: ${item.reason}`);
          return;
        }
      }
    } finally {
      // A consumer that stops reading early leaves a turn running in the driver: cancel it.
      if (!finished) onAbort();
      signal?.removeEventListener?.('abort', onAbort);
      turns.delete(turn.id);
      if (turns.size === 0) setRef(false);
    }
  }

  driverTurn.close = () => {
    const proc = child;
    child = null;
    if (!proc) return;
    try { proc.stdin.end(); } catch { /* already closed */ }
    try { proc.kill(); } catch { /* already gone */ }
  };
  return driverTurn;
}
