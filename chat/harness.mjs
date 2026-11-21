/**
 * Model-agnostic provider registry for `aesop chat` when AESOP_CHAT_BACKEND=harness.
 *
 * The config file declares providers under a `providers` block (AESOP_CHAT_CONFIG). Every provider
 * passes through assertProviderAllowed at config load AND again at request time, so a bug in one path
 * cannot open the other. The TUI never calls an Anthropic host or the claude binary (spec H-R1, H-R2).
 * A refused provider is reported as a visible line and is never sent a request (fail closed).
 *
 * Node stdlib only; never imports ui/.
 *
 * @module chat/harness
 */

import { existsSync, readFileSync } from 'node:fs';
import http from 'node:http';
import { StringDecoder } from 'node:string_decoder';

/** Thrown when a provider, its base_url or a model id is not allowed. The message names the provider. */
export class ProviderRefused extends Error {
  /**
   * @param {string} provider registry id of the refused provider
   * @param {string} reason short, key-free reason
   */
  constructor(provider, reason) {
    super(`${provider}: refused: ${reason}`);
    this.name = 'ProviderRefused';
    this.provider = provider;
    this.reason = reason;
  }
}

const REFUSED_TYPES = new Set(['cli', 'exec', 'claude', 'anthropic']);
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1']);
const DONE = Symbol('done');
// Keys a provider entry may carry; any other key gets a NOTICE by name (spec H-R7, H-R24).
const PROVIDER_KEYS = new Set(['type', 'base_url', 'api_key_env', 'models', 'headers', 'timeout_ms', 'is_local', 'cost_per_mtok', 'allow_insecure']);
const MAX_ALIAS_HOPS = 3;

/**
 * Lowercases a host, drops trailing dots and IPv6 brackets.
 * @param {string} host
 * @returns {string}
 */
export function normalizeHost(host) {
  return String(host).toLowerCase().replace(/\.+$/, '').replace(/^\[(.*)\]$/, '$1');
}

/**
 * True for anthropic.com, *.anthropic.com, claude.ai and *.claude.ai (case-insensitive, trailing dot ignored).
 * @param {string} host
 * @returns {boolean}
 */
export function isAnthropicHost(host) {
  const h = normalizeHost(host);
  return h === 'anthropic.com' || h.endsWith('.anthropic.com') || h === 'claude.ai' || h.endsWith('.claude.ai');
}

/**
 * The single provider gate. Throws ProviderRefused when the provider (or the model id, when given) is not allowed.
 * @param {{id?: string, type?: string, base_url?: string, allow_insecure?: boolean}} provider
 * @param {string} [model] model id the turn would use
 */
export function assertProviderAllowed(provider, model) {
  const id = String(provider.id ?? '?');
  const type = String(provider.type ?? '');
  if (REFUSED_TYPES.has(type)) {
    throw new ProviderRefused(id, `provider type "${type}" is command-style or Anthropic`);
  }
  if (model !== undefined && type !== 'orchestrator' && /^(claude|anthropic\/)/i.test(String(model))) {
    throw new ProviderRefused(id, `model "${model}" resolves to Claude`);
  }
  let url;
  try {
    url = new URL(String(provider.base_url ?? ''));
  } catch {
    throw new ProviderRefused(id, 'base_url is not a valid URL');
  }
  if (isAnthropicHost(url.hostname)) throw new ProviderRefused(id, 'base_url host is an Anthropic host');
  if (url.protocol !== 'https:' && url.protocol !== 'http:') throw new ProviderRefused(id, 'base_url must be http or https');
  if (url.protocol === 'http:' && !LOOPBACK_HOSTS.has(normalizeHost(url.hostname)) && provider.allow_insecure !== true) {
    throw new ProviderRefused(id, 'non-loopback http requires allow_insecure');
  }
}

/**
 * Loads the provider registry. A missing config is an empty registry. Malformed JSON yields one error
 * line and an empty registry. Each provider is checked with assertProviderAllowed for its base_url and
 * every listed model; a refused provider is kept (so turns show the refusal) but never sent a request.
 * @param {string|undefined} configPath
 * @returns {{providers: Map<string, {id: string, raw: object, refused: string|null}>,
 *            notices: string[], defaultSelection: {provider: string, model: string}|null}}
 */
export function loadRegistry(configPath) {
  const registry = { providers: new Map(), notices: [], defaultSelection: null, aliases: new Map(), routes: new Map(), detectCandidates: [] };
  if (!configPath || !existsSync(configPath)) return registry;
  let cfg;
  try {
    cfg = JSON.parse(readFileSync(configPath, 'utf8'));
  } catch {
    registry.notices.push('error: config: malformed JSON, no providers loaded');
    return registry;
  }
  const block = cfg && typeof cfg === 'object' && cfg.providers && typeof cfg.providers === 'object' ? cfg.providers : {};
  for (const [id, raw] of Object.entries(block)) {
    if (!raw || typeof raw !== 'object') {
      registry.notices.push(`error: config: provider ${id} is not an object`);
      continue;
    }
    // Unknown provider keys are reported by name only (never by value, so a literal api_key is not echoed).
    for (const key of Object.keys(raw)) {
      if (!PROVIDER_KEYS.has(key)) registry.notices.push(`NOTICE: ${id}: unknown config key ${key} ignored`);
    }
    const models = Array.isArray(raw.models) ? raw.models.map(String) : [];
    const entry = { id, raw, refused: null };
    try {
      assertProviderAllowed({ ...raw, id });
      for (const m of models) assertProviderAllowed({ ...raw, id }, m);
    } catch (err) {
      if (!(err instanceof ProviderRefused)) throw err;
      entry.refused = err.reason;
      registry.notices.push(`refused: ${id}: ${err.reason}`);
    }
    if (entry.refused === null && raw.allow_insecure === true) {
      registry.notices.push(`NOTICE: ${id} allows insecure http (allow_insecure: true)`);
    }
    registry.providers.set(id, entry);
  }
  const rawAliases = cfg.aliases && typeof cfg.aliases === 'object' && !Array.isArray(cfg.aliases) ? cfg.aliases : {};
  for (const [name, target] of Object.entries(rawAliases)) {
    if (typeof target !== 'string') {
      registry.notices.push(`error: config: alias ${name} must be a string`);
      continue;
    }
    const resolved = resolveAliasChain(rawAliases, name, registry.notices);
    if (resolved !== null) registry.aliases.set(name, resolved);
  }
  registry.routes = parseRoutes(cfg.routes, registry);
  // Candidates for /detect only (H-R23). Nothing is probed until the user asks.
  if (Array.isArray(cfg.detect_candidates)) registry.detectCandidates = cfg.detect_candidates;
  if (typeof cfg.default === 'string') {
    const picked = resolveSelection(registry, cfg.default);
    if (picked.error) registry.notices.push(`error: config: default: ${picked.error}`);
    else registry.defaultSelection = { provider: picked.provider, model: picked.model };
  }
  return registry;
}

/**
 * Follows an alias chain to a "provider/model" target. A chain may pass through at most 3 aliases; a cycle,
 * an unknown name or a deeper chain is reported as a config error and yields null (the alias is dropped).
 * @param {Record<string, string>} aliases raw alias table
 * @param {string} name alias to resolve
 * @param {string[]} notices receives the error line
 * @returns {string|null} "provider/model" or null
 */
function resolveAliasChain(aliases, name, notices) {
  let target = aliases[name];
  const seen = new Set([name]);
  let hops = 0;
  while (!String(target).includes('/')) {
    hops += 1;
    if (seen.has(target)) {
      notices.push(`error: config: alias cycle at ${name}`);
      return null;
    }
    if (hops > MAX_ALIAS_HOPS || !Object.prototype.hasOwnProperty.call(aliases, target)) {
      notices.push(`error: config: alias ${name} does not resolve within ${MAX_ALIAS_HOPS} hops`);
      return null;
    }
    seen.add(target);
    target = aliases[target];
  }
  return String(target);
}

/**
 * Resolves a /model argument or a default: an alias name, or "provider/model". The provider must be
 * registered and the model must be listed by it (or its models is "any"). A refused provider stays
 * selectable so its turns show the refusal; the request-time gate still refuses it.
 * @param {ReturnType<typeof loadRegistry>} registry
 * @param {string} arg
 * @returns {{provider: string, model: string}|{error: string}}
 */
export function resolveSelection(registry, arg) {
  const text = String(arg ?? '').trim();
  let target = text;
  if (!text.includes('/')) {
    if (!registry.aliases.has(text)) return { error: `unknown alias or provider/model: ${text}` };
    target = registry.aliases.get(text);
  }
  const slash = target.indexOf('/');
  const provider = target.slice(0, slash);
  const model = target.slice(slash + 1);
  const entry = registry.providers.get(provider);
  if (slash <= 0 || !entry) return { error: `unknown provider: ${provider}` };
  const listed = entry.raw.models === 'any' || (Array.isArray(entry.raw.models) && entry.raw.models.map(String).includes(model));
  if (!listed || model === '') return { error: `unknown model: ${model} for ${provider}` };
  return { provider, model };
}

/**
 * Pure provider-config check shared with the Python seam (tests/fixtures/provider-config-cases.json).
 * Runs the same gate as the registry: the base_url, the type and every listed model.
 * @param {object} config one provider entry
 * @returns {{ok: boolean, reason: string|null}}
 */
export function validateProviderConfig(config) {
  const raw = config && typeof config === 'object' ? config : {};
  try {
    assertProviderAllowed({ ...raw, id: 'config' });
    for (const m of Array.isArray(raw.models) ? raw.models.map(String) : []) assertProviderAllowed({ ...raw, id: 'config' }, m);
    return { ok: true, reason: null };
  } catch (err) {
    if (!(err instanceof ProviderRefused)) throw err;
    return { ok: false, reason: err.reason };
  }
}

/**
 * Parses one stream line. SSE (`data: {json}`, `data: [DONE]`) and NDJSON (`{json}` per line) are both
 * accepted; comments, keepalives and other lines yield ''. Returns DONE on the SSE terminal frame.
 * @param {string} line line without its trailing CR
 * @param {(usage: object) => void} [onUsage] receives a frame's `usage` object when it carries one (H-R22)
 * @returns {string|typeof DONE}
 * @throws {TurnFailure} bad-response when a frame is not JSON
 */
function parseStreamLine(line, onUsage) {
  const text = line.trim();
  if (text === '' || text.startsWith(':')) return '';
  let payload;
  if (text.startsWith('data:')) {
    payload = text.slice(5).trim();
    if (payload === '[DONE]') return DONE;
  } else if (text.startsWith('{')) {
    payload = text;
  } else {
    return '';
  }
  let obj;
  try {
    obj = JSON.parse(payload);
  } catch {
    throw new TurnFailure('bad-response', 'malformed stream frame');
  }
  if (obj && typeof obj.usage === 'object' && obj.usage !== null && onUsage) onUsage(obj.usage);
  const content = obj?.choices?.[0]?.delta?.content ?? obj?.message?.content ?? obj?.response;
  return typeof content === 'string' ? content : '';
}

/**
 * Parses the `routes` block. Each task class maps to a "provider/model" (or alias) string, or to an
 * object: `target`, `candidates` (ordered list, replaces the primary), `fallback` (list), `fallback_on`
 * ("any" lets non-retryable failures fall back too) and `strategy` ("cheapest" or "fastest").
 * A target that does not resolve is a config error line and drops that route.
 * @param {unknown} rawRoutes
 * @param {ReturnType<typeof loadRegistry>} registry
 * @returns {Map<string, {target: object|null, candidates: object[]|null, fallback: object[], fallbackOn: string, strategy: string|null}>}
 */
function parseRoutes(rawRoutes, registry) {
  const routes = new Map();
  if (!rawRoutes || typeof rawRoutes !== 'object' || Array.isArray(rawRoutes)) return routes;
  for (const [task, value] of Object.entries(rawRoutes)) {
    const spec = typeof value === 'string' ? { target: value } : value;
    if (!spec || typeof spec !== 'object' || Array.isArray(spec)) {
      registry.notices.push(`error: config: route ${task} must be a string or an object`);
      continue;
    }
    const bad = [];
    const pick = (v) => {
      const picked = resolveSelection(registry, String(v));
      if (picked.error) {
        registry.notices.push(`error: config: route ${task}: ${picked.error}`);
        bad.push(v);
        return null;
      }
      return picked;
    };
    const target = spec.target === undefined ? null : pick(spec.target);
    const candidates = Array.isArray(spec.candidates) ? spec.candidates.map(pick) : null;
    const fallback = Array.isArray(spec.fallback) ? spec.fallback.map(pick) : [];
    if (bad.length > 0) continue;
    routes.set(task, {
      target,
      candidates,
      fallback,
      fallbackOn: spec.fallback_on === 'any' ? 'any' : 'retryable',
      strategy: spec.strategy === 'cheapest' || spec.strategy === 'fastest' ? spec.strategy : null,
    });
  }
  return routes;
}

/**
 * Matches a slash-prefixed route turn such as `/code write x` against the registry's routes.
 * `/chat` is never a route prefix; a bare `/code` with no body is not a route turn.
 * @param {ReturnType<typeof loadRegistry>} registry
 * @param {string} text
 * @returns {{task: string, body: string}|null}
 */
export function matchRoute(registry, text) {
  const m = /^\/([a-z][a-z0-9_-]*) +(\S[\s\S]*)$/.exec(String(text));
  if (!m || m[1] === 'chat' || !registry.routes.has(m[1])) return null;
  return { task: m[1], body: m[2] };
}

/** A classified request failure. `cls` is one of the H-R19 error classes; `reason` is short and key-free. */
class TurnFailure extends Error {
  /**
   * @param {string} cls
   * @param {string} reason
   */
  constructor(cls, reason) {
    super(`${cls}: ${reason}`);
    this.name = 'TurnFailure';
    this.cls = cls;
    this.reason = reason;
  }
}

/** Failure classes that may fall back to the next candidate without fallback_on "any" (H-R12, H-R13). */
const RETRYABLE = new Set(['connect', 'timeout', 'rate-limit', 'server']);
const BREAKER_THRESHOLD = 3;
const BREAKER_COOLDOWN_MS = 30000;
const DEFAULT_TIMEOUT_MS = 60000;

/**
 * Maps a non-2xx status to a classified failure.
 * @param {number} status
 * @returns {TurnFailure}
 */
function statusFailure(status) {
  if (status === 401 || status === 403) return new TurnFailure('auth', `HTTP ${status}`);
  if (status === 429) return new TurnFailure('rate-limit', 'HTTP 429');
  if (status >= 500) return new TurnFailure('server', `HTTP ${status}`);
  if (status >= 300 && status < 400) return new TurnFailure('bad-response', `redirect ${status} not followed`);
  return new TurnFailure('bad-response', `HTTP ${status}`);
}

/**
 * Clock for latency and the circuit breaker. Tests inject a fake clock via globalThis.__AESOP_CHAT_NOW.
 * @returns {number} milliseconds
 */
function clockNow() {
  const hook = globalThis.__AESOP_CHAT_NOW;
  return typeof hook === 'function' ? hook() : Date.now();
}

/**
 * Backend source for turns in harness mode. Each turn runs the route plan (the selected model for
 * plain turns, or the task route for a `/task` prefix): candidates are ordered by strategy, skipped while
 * their circuit breaker is open, and tried in order. Every failure becomes one `error: <provider>:
 * <class>: <reason>` line through `options.notify`; the turn never throws except when no provider is
 * selected. Each candidate's request is gated by assertProviderAllowed again at request time. Redirects
 * are never followed. Node stdlib only.
 * @param {ReturnType<typeof loadRegistry>} registry
 * @param {() => {provider: string|null, model: string|null}} getSelection live status selection
 * @param {Record<string, string|undefined>} [env] source of key env vars (api_key_env)
 * @param {{notify?: (text: string) => void}} [options] receives transcript system lines
 * @returns {(line: string, opts?: {task?: string}) => AsyncGenerator<string>}
 */
export function harnessSource(registry, getSelection, env = process.env, options = {}) {
  const notify = typeof options.notify === 'function' ? options.notify : () => {};
  // flash shows a line for one frame only (the transcript repaints every frame, so a skip notice
  // written there would be drawn again on each repaint). Defaults to notify.
  const flash = typeof options.flash === 'function' ? options.flash : notify;
  const reportUsage = typeof options.usage === 'function' ? options.usage : () => {};
  const breakers = new Map();
  const latency = new Map();
  const breaker = (id) => {
    if (!breakers.has(id)) breakers.set(id, { fails: 0, openUntil: 0, announced: false });
    return breakers.get(id);
  };
  const recordFailure = (id) => {
    const b = breaker(id);
    b.fails += 1;
    if (b.fails >= BREAKER_THRESHOLD) {
      b.openUntil = clockNow() + BREAKER_COOLDOWN_MS;
      b.announced = false;
    }
  };
  const recordSuccess = (id) => {
    const b = breaker(id);
    b.fails = 0;
    b.openUntil = 0;
    b.announced = false;
  };
  const costOf = (id) => {
    const entry = registry.providers.get(id);
    if (!entry) return Infinity;
    if (entry.raw.is_local === true) return 0;
    const c = entry.raw.cost_per_mtok;
    if (typeof c === 'number') return c;
    if (c && typeof c === 'object') return (Number(c.input) || 0) + (Number(c.output) || 0);
    return Infinity;
  };
  const orderCandidates = (list, strategy) => {
    const indexed = list.map((c, i) => ({ c, i }));
    if (strategy === 'cheapest') {
      indexed.sort((x, y) => (costOf(x.c.provider) - costOf(y.c.provider)) || x.i - y.i);
    } else if (strategy === 'fastest') {
      indexed.sort((x, y) => ((latency.get(x.c.provider) ?? 0) - (latency.get(y.c.provider) ?? 0)) || x.i - y.i);
    }
    const seen = new Set();
    const out = [];
    for (const { c } of indexed) {
      const key = `${c.provider}/${c.model}`;
      if (!seen.has(key)) { seen.add(key); out.push(c); }
    }
    return out;
  };

  /**
   * Prices one usage report for a provider: local is free, cost_per_mtok prices input and output.
   * @returns {{total: number, prompt: number, completion: number, cost: number|null}}
   */
  const usageOf = (id, u) => {
    const prompt = Number(u.prompt_tokens) || 0;
    const completion = Number(u.completion_tokens) || 0;
    const total = Number(u.total_tokens) || prompt + completion;
    const entry = registry.providers.get(id);
    let cost = null;
    if (entry?.raw.is_local === true) cost = 0;
    else if (entry && entry.raw.cost_per_mtok && typeof entry.raw.cost_per_mtok === 'object') {
      const c = entry.raw.cost_per_mtok;
      cost = ((Number(c.input) || 0) * prompt + (Number(c.output) || 0) * completion) / 1e6;
    }
    return { total, prompt, completion, cost };
  };

  // One request to one candidate. Returns {ok, yielded, failure, usage}; yields each text delta as it arrives.
  // An abort on `signal` destroys the request, so the server sees the connection close (H-R21).
  async function* streamOnce(entry, model, line, signal) {
    const id = entry.id;
    const outcome = { ok: false, yielded: 0, failure: null, usage: null };
    const reportOwn = (u) => { outcome.usage = u; };
    try {
      assertProviderAllowed({ ...entry.raw, id }, model);
    } catch (err) {
      if (!(err instanceof ProviderRefused)) throw err;
      outcome.failure = new TurnFailure('refused', err.reason);
      return outcome;
    }
    const keyName = entry.raw.api_key_env;
    const keyed = typeof keyName === 'string' && /^[A-Z_][A-Z0-9_]*$/.test(keyName);
    if (keyed && !env[keyName] && entry.raw.is_local !== true) {
      outcome.failure = new TurnFailure('no-key', `env ${keyName} is unset`);
      return outcome;
    }
    const base = String(entry.raw.base_url).replace(/\/+$/, '');
    const body = JSON.stringify({ model, stream: true, messages: [{ role: 'user', content: String(line) }] });
    const headers = { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body), accept: 'text/event-stream' };
    if (keyed && env[keyName]) headers.authorization = `Bearer ${env[keyName]}`;
    const timeoutMs = Number(entry.raw.timeout_ms) > 0 ? Number(entry.raw.timeout_ms) : DEFAULT_TIMEOUT_MS;
    const started = clockNow();
    let req = null;
    let timedOut = false;
    let timer = null;
    // Idle timer: armed before the response and re-armed on every body chunk.
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => { timedOut = true; if (req) req.destroy(); }, timeoutMs);
      timer.unref?.();
    };
    const timeoutFailure = () => new TurnFailure('timeout', `no response within ${timeoutMs} ms`);
    const onAbort = () => { if (req) req.destroy(); };
    if (signal?.aborted) return outcome;
    signal?.addEventListener?.('abort', onAbort, { once: true });
    let res;
    try {
      res = await new Promise((resolve, reject) => {
        req = http.request(`${base}/chat/completions`, { method: 'POST', headers }, resolve);
        req.on('error', (err) => reject(timedOut ? timeoutFailure() : new TurnFailure('connect', err.code ?? 'error')));
        arm();
        req.end(body);
      });
    } catch (err) {
      clearTimeout(timer);
      outcome.failure = err instanceof TurnFailure ? err : new TurnFailure('connect', 'error');
      return outcome;
    }
    if (res.statusCode < 200 || res.statusCode >= 300) {
      res.resume();
      clearTimeout(timer);
      outcome.failure = statusFailure(res.statusCode);
      return outcome;
    }
    const noteFirst = () => {
      if (outcome.yielded === 0) {
        const sample = clockNow() - started;
        const prev = latency.get(id);
        latency.set(id, prev === undefined ? sample : 0.5 * prev + 0.5 * sample);
      }
    };
    const ctype = String(res.headers['content-type'] ?? '');
    const wholeJson = /application\/json/i.test(ctype) && !/ndjson/i.test(ctype);
    const decoder = new StringDecoder('utf8');
    let pending = '';
    let done = false;
    try {
      if (wholeJson) {
        let whole = '';
        for await (const chunk of res) { arm(); whole += decoder.write(chunk); }
        whole += decoder.end();
        let obj;
        try {
          obj = JSON.parse(whole);
        } catch {
          throw new TurnFailure('bad-response', 'malformed JSON reply');
        }
        if (obj && typeof obj.usage === 'object' && obj.usage !== null) reportOwn(obj.usage);
        const text = obj?.choices?.[0]?.message?.content ?? obj?.message?.content;
        if (typeof text === 'string' && text !== '') {
          noteFirst();
          outcome.yielded += 1;
          yield text;
        }
      } else {
        for await (const chunk of res) {
          arm();
          pending += decoder.write(chunk);
          let nl = pending.indexOf('\n');
          while (nl >= 0) {
            const delta = parseStreamLine(pending.slice(0, nl).replace(/\r$/, ''), reportOwn);
            pending = pending.slice(nl + 1);
            if (delta === DONE) { done = true; break; }
            if (delta !== '') {
              noteFirst();
              outcome.yielded += 1;
              yield delta;
            }
            nl = pending.indexOf('\n');
          }
          if (done) break;
        }
        if (!done) {
          pending += decoder.end();
          if (pending.trim() !== '') {
            const delta = parseStreamLine(pending.replace(/\r$/, ''), reportOwn);
            if (delta !== DONE && delta !== '') {
              noteFirst();
              outcome.yielded += 1;
              yield delta;
            }
          }
        }
      }
      clearTimeout(timer);
      outcome.ok = true;
    } catch (err) {
      clearTimeout(timer);
      if (err instanceof TurnFailure) outcome.failure = err;
      else outcome.failure = timedOut ? timeoutFailure() : new TurnFailure('connect', 'stream cut');
    } finally {
      signal?.removeEventListener?.('abort', onAbort);
    }
    return outcome;
  }

  return async function* harnessTurn(line, opts = {}) {
    const sel = getSelection();
    if (!sel.provider) throw new Error('no provider configured (see /help)');
    const task = opts.task ?? 'chat';
    const signal = opts.signal;
    const route = registry.routes.get(task) ?? null;
    const primary = task === 'chat' ? { provider: sel.provider, model: sel.model } : route?.target ?? null;
    const list = route?.candidates ?? [primary, ...(route?.fallback ?? [])].filter(Boolean);
    if (list.length === 0) throw new Error(`route ${task} has no target`);
    const fallbackOn = route?.fallbackOn ?? 'retryable';
    const ordered = orderCandidates(list, route?.strategy ?? null);
    let attempted = 0;
    for (let i = 0; i < ordered.length; i += 1) {
      const cand = ordered[i];
      const b = breaker(cand.provider);
      if (b.openUntil > clockNow()) {
        if (!b.announced) {
          b.announced = true;
          flash(`skipping ${cand.provider}: circuit open after ${BREAKER_THRESHOLD} failures, retry after cooldown`);
        }
        continue;
      }
      attempted += 1;
      const entry = registry.providers.get(cand.provider);
      if (!entry) {
        notify(`error: ${cand.provider}: refused: unknown provider`);
        return;
      }
      const outcome = yield* streamOnce(entry, cand.model, line, signal);
      if (outcome.ok) {
        recordSuccess(cand.provider);
        if (outcome.usage) reportUsage({ provider: cand.provider, ...usageOf(cand.provider, outcome.usage) });
        return;
      }
      // Ctrl-c aborted the request: shown as cancelled, never a failure and never a fallback (H-R21).
      if (signal?.aborted) {
        notify(`cancelled: ${cand.provider} request aborted`);
        return;
      }
      const failure = outcome.failure;
      if (outcome.yielded > 0) {
        // A failure after the first chunk is never re-sent to another provider (H-R14).
        recordFailure(cand.provider);
        notify(`stream interrupted from ${cand.provider}`);
        return;
      }
      const retryable = RETRYABLE.has(failure.cls);
      if (retryable) recordFailure(cand.provider);
      notify(`error: ${cand.provider}: ${failure.cls}: ${failure.reason}`);
      const next = ordered[i + 1];
      if (!next || !(retryable || fallbackOn === 'any')) return;
      notify(`falling back: ${cand.provider} -> ${next.provider} (${failure.cls})`);
    }
    if (attempted === 0) notify(`error: ${ordered[0].provider}: server: no candidate available, circuit open`);
  };
}

/**
 * GET {base}/models for one provider. Only a 2xx answer counts as reachable; a redirect, an error, a
 * timeout or a refused provider is unreachable. Sends no chat request.
 * @param {{raw: object}} entry registry entry
 * @param {Record<string, string|undefined>} env source of key env vars
 * @param {number} [timeoutMs]
 * @returns {Promise<'reachable'|'unreachable'>}
 */
export function probeProvider(entry, env = process.env, timeoutMs = 1000) {
  return new Promise((resolve) => {
    const base = String(entry.raw.base_url ?? '').replace(/\/+$/, '');
    const headers = {};
    const keyName = entry.raw.api_key_env;
    if (typeof keyName === 'string' && /^[A-Z_][A-Z0-9_]*$/.test(keyName) && env[keyName]) headers.authorization = `Bearer ${env[keyName]}`;
    let req;
    try {
      req = http.get(`${base}/models`, { headers }, (res) => {
        res.resume();
        resolve(res.statusCode >= 200 && res.statusCode < 300 ? 'reachable' : 'unreachable');
      });
    } catch {
      resolve('unreachable');
      return;
    }
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      resolve('unreachable');
    });
    req.on('error', () => resolve('unreachable'));
  });
}

/**
 * Turns `detect_candidates` into loopback probe targets. A port number (or digit string) becomes
 * http://127.0.0.1:<port>/v1. A URL is probed only when its host is loopback; any other host is listed
 * as skipped and never contacted (H-R23). Anthropic hosts are skipped the same way.
 * @param {unknown[]} candidates
 * @returns {{targets: {label: string, base: string}[], lines: string[]}}
 */
export function detectTargets(candidates) {
  const targets = [];
  const lines = [];
  for (const c of candidates) {
    if (typeof c === 'number' || /^\d+$/.test(String(c))) {
      const port = Number(c);
      if (!Number.isInteger(port) || port < 1 || port > 65535) {
        lines.push(`detect: skipped ${String(c)}: not a valid port`);
        continue;
      }
      targets.push({ label: `127.0.0.1:${port}`, base: `http://127.0.0.1:${port}/v1` });
      continue;
    }
    let url;
    try {
      url = new URL(String(c));
    } catch {
      lines.push(`detect: skipped ${String(c)}: not a port or URL`);
      continue;
    }
    const host = normalizeHost(url.hostname);
    if (!LOOPBACK_HOSTS.has(host) || isAnthropicHost(host) || (url.protocol !== 'http:' && url.protocol !== 'https:')) {
      lines.push(`detect: skipped ${url.host}: not loopback, not probed`);
      continue;
    }
    targets.push({ label: url.host, base: `${url.protocol}//${url.host}${url.pathname.replace(/\/+$/, '')}` });
  }
  return { targets, lines };
}

/**
 * Lines for /detect: each loopback candidate gets one GET {base}/models probe; the rest are skipped.
 * Runs only when the user asks (never at start). Sends no chat request.
 * @param {ReturnType<typeof loadRegistry>|null} registry
 * @param {Record<string, string|undefined>} [env]
 * @param {(entry: object, env: object) => Promise<string>} [probe]
 * @returns {Promise<string[]>}
 */
export async function detectLines(registry, env = process.env, probe = probeProvider) {
  const { targets, lines } = detectTargets(registry?.detectCandidates ?? []);
  if (targets.length === 0 && lines.length === 0) return ['detect: no detect_candidates configured'];
  const out = [...lines];
  for (const t of targets) {
    const status = await probe({ raw: { base_url: t.base } }, env);
    out.push(`detect ${t.label}: ${status === 'reachable' ? 'reachable' : 'unreachable (absent)'}`);
  }
  return out;
}

/**
 * Lines for /auth (H-R24): one per provider, `key: set (env NAME)`, `key: missing (env NAME)` or
 * `key: not needed (local)`. Reads the environment only; never sends a request and never prints a key value.
 * @param {ReturnType<typeof loadRegistry>} registry
 * @param {Record<string, string|undefined>} [env]
 * @returns {string[]}
 */
export function authLines(registry, env = process.env) {
  const lines = [];
  for (const entry of registry.providers.values()) {
    const keyName = entry.raw.api_key_env;
    let status;
    if (entry.raw.is_local === true) status = 'key: not needed (local)';
    else if (typeof keyName === 'string' && keyName !== '') status = env[keyName] ? `key: set (env ${keyName})` : `key: missing (env ${keyName})`;
    else status = 'key: not needed (no api_key_env)';
    lines.push(`auth ${entry.id}: ${status}`);
  }
  return lines;
}

/**
 * Lines for /models: each alias with its target, then each provider with its models and status.
 * Status is refused (never probed), no key (api_key_env set but unset), or reachable/unreachable.
 * @param {ReturnType<typeof loadRegistry>} registry
 * @param {Record<string, string|undefined>} [env]
 * @param {(entry: object, env: object) => Promise<string>} [probe]
 * @returns {Promise<string[]>}
 */
export async function listModelLines(registry, env = process.env, probe = probeProvider) {
  const lines = [];
  for (const [name, target] of registry.aliases) lines.push(`alias ${name} -> ${target}`);
  for (const entry of registry.providers.values()) {
    const models = entry.raw.models === 'any' ? 'any' : (Array.isArray(entry.raw.models) ? entry.raw.models.map(String).join(', ') : '');
    let status;
    const keyName = entry.raw.api_key_env;
    if (entry.refused !== null) status = `refused (${entry.refused})`;
    else if (typeof keyName === 'string' && keyName !== '' && !env[keyName]) status = 'no key';
    else status = await probe(entry, env);
    lines.push(`provider ${entry.id}: ${status}; models: ${models || 'none'}`);
  }
  return lines;
}
