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
  const registry = { providers: new Map(), notices: [], defaultSelection: null, aliases: new Map() };
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
