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
  const registry = { providers: new Map(), notices: [], defaultSelection: null };
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
  if (typeof cfg.default === 'string') {
    const slash = cfg.default.indexOf('/');
    if (slash > 0) registry.defaultSelection = { provider: cfg.default.slice(0, slash), model: cfg.default.slice(slash + 1) };
  }
  return registry;
}

/**
 * Parses one SSE line. Returns DONE on the terminal frame, the text delta ('' when the frame has none),
 * or '' for comments, keepalives and blank lines.
 * @param {string} line
 * @param {string} id provider id, used in the error text only
 */
function parseSseLine(line, id) {
  if (!line.startsWith('data:')) return '';
  const payload = line.slice(5).trim();
  if (payload === '[DONE]') return DONE;
  let obj;
  try {
    obj = JSON.parse(payload);
  } catch {
    throw new Error(`${id}: bad-response: malformed stream frame`);
  }
  const content = obj?.choices?.[0]?.delta?.content;
  return typeof content === 'string' ? content : '';
}

/**
 * Backend source for plain turns in harness mode. Re-checks the provider with assertProviderAllowed
 * at request time, then streams OpenAI-compatible SSE deltas. Redirects are never followed.
 * @param {ReturnType<typeof loadRegistry>} registry
 * @param {() => {provider: string|null, model: string|null}} getSelection live status selection
 * @param {Record<string, string|undefined>} [env] source of key env vars (api_key_env)
 * @returns {(line: string) => AsyncGenerator<string>}
 */
export function harnessSource(registry, getSelection, env = process.env) {
  return async function* harnessTurn(line) {
    const { provider, model } = getSelection();
    if (!provider) throw new Error('no provider configured (see /help)');
    const entry = registry.providers.get(provider);
    if (!entry) throw new Error(`unknown provider: ${provider}`);
    assertProviderAllowed({ ...entry.raw, id: provider }, model ?? undefined);
    const base = String(entry.raw.base_url).replace(/\/+$/, '');
    const body = JSON.stringify({ model, stream: true, messages: [{ role: 'user', content: String(line) }] });
    const headers = { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body), accept: 'text/event-stream' };
    const keyName = entry.raw.api_key_env;
    if (typeof keyName === 'string' && /^[A-Z_][A-Z0-9_]*$/.test(keyName) && env[keyName]) {
      headers.authorization = `Bearer ${env[keyName]}`;
    }
    const res = await new Promise((resolve, reject) => {
      const req = http.request(`${base}/chat/completions`, { method: 'POST', headers }, resolve);
      req.on('error', (err) => reject(new Error(`${provider}: connect: ${err.code ?? 'error'}`)));
      req.end(body);
    });
    if (res.statusCode >= 300 && res.statusCode < 400) {
      res.resume();
      throw new Error(`${provider}: redirect ${res.statusCode} not followed`);
    }
    if (res.statusCode < 200 || res.statusCode >= 300) {
      res.resume();
      throw new Error(`${provider}: HTTP ${res.statusCode}`);
    }
    const decoder = new StringDecoder('utf8');
    let pending = '';
    for await (const chunk of res) {
      pending += decoder.write(chunk);
      let nl = pending.indexOf('\n');
      while (nl >= 0) {
        const delta = parseSseLine(pending.slice(0, nl).replace(/\r$/, ''), provider);
        pending = pending.slice(nl + 1);
        if (delta === DONE) {
          res.destroy();
          return;
        }
        if (delta !== '') yield delta;
        nl = pending.indexOf('\n');
      }
    }
    pending += decoder.end();
    if (pending !== '') {
      const delta = parseSseLine(pending.replace(/\r$/, ''), provider);
      if (delta !== DONE && delta !== '') yield delta;
    }
  };
}
