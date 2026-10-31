#!/usr/bin/env node
/**
 * Auth and model verbs for aesop: `aesop auth ...`, `aesop model ...`, and the control channel.
 *
 * Exit codes: 0 ok; 2 bad usage, malformed input, or a corrupt store; 3 `model use` for a
 * provider that needs a credential when none is available. Secrets never reach stdout or
 * stderr: status masks to the last four characters.
 *
 * Invoked by bin/cli.js with the full argv (`auth login ...`, `model list`, ...), or with
 * `--control` and one JSON control line on stdin (`{"type":"control","op":"auth.status"}`).
 *
 * Zero-dependency: node: builtins and relative repo files only.
 *
 * @module auth/cli
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PROVIDERS, findProvider, parseSelector } from './providers.mjs';
import { readStore, writeStore, resolveCredential, StoreError } from './store.mjs';

export const EXIT_OK = 0;
export const EXIT_USAGE = 2;
export const EXIT_NO_CREDENTIAL = 3;

/** Bad usage or malformed input. Message is safe to print. */
class UsageError extends Error {
  /** @param {string} message one-line explanation */
  constructor(message) {
    super(message);
    this.name = 'UsageError';
  }
}

/**
 * Parse `--provider <name>`, `--api-key-stdin`, and positional arguments.
 * @param {string[]} argv arguments after the verb
 * @param {{value: string[], flag: string[]}} spec flags that take a value, and boolean flags
 * @returns {{values: Record<string, string>, flags: Set<string>, positional: string[]}} parsed view
 * @throws {UsageError} on an unknown flag or a value flag with no value
 */
function parseArgs(argv, spec) {
  const values = {};
  const flags = new Set();
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg.startsWith('--')) {
      positional.push(arg);
      continue;
    }
    const name = arg.slice(2);
    if (spec.value.includes(name)) {
      if (i + 1 >= argv.length || argv[i + 1].startsWith('--')) {
        throw new UsageError(`--${name} requires a value`);
      }
      values[name] = argv[++i];
    } else if (spec.flag.includes(name)) {
      flags.add(name);
    } else {
      throw new UsageError(`unknown flag --${name} for this verb`);
    }
  }
  return { values, flags, positional };
}

/** Provider list text for usage errors. */
function providerNames() {
  return PROVIDERS.map((p) => p.name).join(', ');
}

/** Resolve `--provider` to a registry entry or throw a usage error. */
function requireProvider(name) {
  const provider = findProvider(name);
  if (!provider) throw new UsageError(`unknown provider "${name}" (registered: ${providerNames()})`);
  return provider;
}

/** Mask a key to its last four characters. Returns null when there is no key. */
function keyTailOf(key) {
  return key ? key.slice(-4) : null;
}

/** Status rows for the given providers, with no secret in any field. */
function statusRows(providers, env, store) {
  return providers.map((provider) => {
    const { source, key } = resolveCredential(provider, env, store);
    const entry = store.providers[provider.name];
    return {
      provider: provider.name,
      source,
      model: entry?.model ?? provider.defaultModel,
      keyTail: keyTailOf(key),
    };
  });
}

/**
 * `auth login --provider <name> --api-key-stdin`.
 * @param {string[]} argv arguments after `auth login`
 * @param {object} io runtime seam (env, readStdin, out, err)
 * @returns {Promise<number>} exit code
 */
async function authLogin(argv, io) {
  const { values, flags, positional } = parseArgs(argv, { value: ['provider'], flag: ['api-key-stdin'] });
  if (positional.length) throw new UsageError(`unexpected argument "${positional[0]}"; keys are read from stdin only`);
  if (!values.provider) throw new UsageError('auth login requires --provider <name>');
  const provider = requireProvider(values.provider);
  if (provider.envVar === null) throw new UsageError(`provider ${provider.name} needs no credential`);
  if (!flags.has('api-key-stdin')) {
    throw new UsageError('auth login requires --api-key-stdin (the key is read from stdin, never accepted as an argument)');
  }
  const key = (await io.readStdin()).split(/\r?\n/)[0].trim();
  if (!key) throw new UsageError('no key on stdin');
  const store = readStore(io.env);
  const previous = store.providers[provider.name];
  store.providers[provider.name] = {
    credential: key,
    model: previous?.model ?? provider.defaultModel,
    addedAt: new Date().toISOString(),
  };
  writeStore(io.env, store);
  io.out(`logged in: ${provider.name} source=store key=...${keyTailOf(key)}\n`);
  return EXIT_OK;
}

/**
 * `auth status [--provider <name>]`.
 * @param {string[]} argv arguments after `auth status`
 * @param {object} io runtime seam
 * @returns {Promise<number>} exit code
 */
async function authStatus(argv, io) {
  const { values, positional } = parseArgs(argv, { value: ['provider'], flag: [] });
  if (positional.length) throw new UsageError(`unexpected argument "${positional[0]}"`);
  const providers = values.provider ? [requireProvider(values.provider)] : [...PROVIDERS];
  const store = readStore(io.env);
  const rows = statusRows(providers, io.env, store);
  if (!io.stdoutIsTTY) {
    io.out(`${JSON.stringify({ ok: true, op: 'auth.status', providers: rows })}\n`);
    return EXIT_OK;
  }
  for (const row of rows) {
    io.out(`${row.provider}: source=${row.source} model=${row.model} key=${row.keyTail ? `...${row.keyTail}` : '-'}\n`);
  }
  return EXIT_OK;
}

/**
 * `auth logout [--provider <name>]`. Without --provider, clears every stored credential.
 * @param {string[]} argv arguments after `auth logout`
 * @param {object} io runtime seam
 * @returns {Promise<number>} exit code
 */
async function authLogout(argv, io) {
  const { values, positional } = parseArgs(argv, { value: ['provider'], flag: [] });
  if (positional.length) throw new UsageError(`unexpected argument "${positional[0]}"`);
  const store = readStore(io.env);
  const before = Object.keys(store.providers).length;
  if (values.provider) {
    const provider = requireProvider(values.provider);
    delete store.providers[provider.name];
  } else {
    store.providers = {};
  }
  if (Object.keys(store.providers).length !== before) writeStore(io.env, store);
  io.out(values.provider ? `logged out: ${values.provider}\n` : 'logged out: all providers\n');
  return EXIT_OK;
}

/**
 * `auth <verb>` dispatcher.
 * @param {string[]} argv arguments after `auth`
 * @param {object} io runtime seam
 * @returns {Promise<number>} exit code
 */
async function authVerb(argv, io) {
  const [verb, ...rest] = argv;
  if (verb === 'login') return authLogin(rest, io);
  if (verb === 'status') return authStatus(rest, io);
  if (verb === 'logout') return authLogout(rest, io);
  throw new UsageError(verb ? `unknown auth verb "${verb}" (use login, status, logout)` : 'auth requires a verb: login, status, or logout');
}

/**
 * `model list | use <provider>/<model> | current`.
 * @param {string[]} argv arguments after `model`
 * @param {object} io runtime seam
 * @returns {number} exit code
 */
function modelVerb(argv, io) {
  const [verb, ...rest] = argv;
  if (verb === 'list') {
    if (rest.length) throw new UsageError('model list takes no arguments');
    if (!io.stdoutIsTTY) {
      const providers = PROVIDERS.map((p) => ({ provider: p.name, defaultModel: p.defaultModel, models: [...p.models], envVar: p.envVar }));
      io.out(`${JSON.stringify({ ok: true, op: 'model.list', providers })}\n`);
      return EXIT_OK;
    }
    for (const p of PROVIDERS) {
      const need = p.envVar === null ? 'no credential needed' : `credential env ${p.envVar}`;
      io.out(`${p.name}: default=${p.defaultModel} models=${p.models.join(', ')} (${need})\n`);
    }
    return EXIT_OK;
  }
  if (verb === 'use') {
    if (rest.length !== 1) throw new UsageError('model use requires exactly one <provider>/<model> argument');
    const selector = parseSelector(rest[0]);
    if (!selector) throw new UsageError(`malformed selector "${rest[0]}" (expected <provider>/<model>)`);
    const provider = requireProvider(selector.provider);
    if (!provider.models.includes(selector.model)) {
      throw new UsageError(`unknown model "${selector.model}" for ${provider.name} (models: ${provider.models.join(', ')})`);
    }
    const store = readStore(io.env);
    if (provider.envVar !== null && resolveCredential(provider, io.env, store).source === 'none') {
      io.err(`no credential for ${provider.name}: set ${provider.envVar} or AESOP_API_KEY, or run: auth login --provider ${provider.name} --api-key-stdin\n`);
      return EXIT_NO_CREDENTIAL;
    }
    if (store.providers[provider.name]) store.providers[provider.name].model = selector.model;
    store.current = { provider: provider.name, model: selector.model };
    writeStore(io.env, store);
    io.out(`current model: ${provider.name}/${selector.model}\n`);
    return EXIT_OK;
  }
  if (verb === 'current') {
    if (rest.length) throw new UsageError('model current takes no arguments');
    const store = readStore(io.env);
    const cur = store.current;
    const selector = cur ? `${cur.provider}/${cur.model}` : null;
    if (!io.stdoutIsTTY) {
      io.out(`${JSON.stringify({ ok: true, op: 'model.current', provider: cur?.provider ?? null, model: cur?.model ?? null, selector })}\n`);
    } else if (!cur) {
      io.out(`no model selected (default: anthropic/${findProvider('anthropic').defaultModel})\n`);
    } else {
      io.out(`${selector}\n`);
    }
    return EXIT_OK;
  }
  throw new UsageError(verb ? `unknown model verb "${verb}" (use list, use, current)` : 'model requires a verb: list, use, or current');
}

/**
 * Answer one control message. Output is exactly one JSON line; no secret is ever included.
 * @param {string} text full stdin text
 * @param {object} io runtime seam
 * @returns {Promise<number>} exit code
 */
async function runControl(text, io) {
  const line = text.split(/\r?\n/).find((l) => l.trim() !== '');
  if (line === undefined) throw new UsageError('control: empty stdin');
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    throw new UsageError('control: malformed JSON on stdin');
  }
  if (!msg || typeof msg !== 'object' || msg.type !== 'control') {
    throw new UsageError('control: expected {"type":"control","op":...}');
  }
  if (msg.op === 'auth.status') {
    const store = readStore(io.env);
    io.out(`${JSON.stringify({ ok: true, op: msg.op, providers: statusRows([...PROVIDERS], io.env, store) })}\n`);
    return EXIT_OK;
  }
  if (msg.op === 'model.list') {
    const providers = PROVIDERS.map((p) => ({ provider: p.name, defaultModel: p.defaultModel, models: [...p.models], envVar: p.envVar }));
    io.out(`${JSON.stringify({ ok: true, op: msg.op, providers })}\n`);
    return EXIT_OK;
  }
  if (msg.op === 'model.current') {
    const store = readStore(io.env);
    const cur = store.current ?? { provider: null, model: null };
    io.out(`${JSON.stringify({ ok: true, op: msg.op, provider: cur.provider, model: cur.model })}\n`);
    return EXIT_OK;
  }
  throw new UsageError(`control: unknown op "${String(msg.op)}"`);
}

/**
 * Entry point shared by bin/cli.js and the direct-run path. Never throws; maps every failure to an exit code.
 * @param {string[]} argv argv after the program name (`auth ...`, `model ...`, or `--control`)
 * @param {{env: NodeJS.ProcessEnv, readStdin: () => Promise<string>, out: (s: string) => void, err: (s: string) => void}} io runtime seam
 * @returns {Promise<number>} exit code: 0, 2, or 3
 */
export async function runAuthCli(argv, io) {
  try {
    const [head, ...rest] = argv;
    if (head === '--control') return await runControl(await io.readStdin(), io);
    if (head === 'auth') return await authVerb(rest, io);
    if (head === 'model') return modelVerb(rest, io);
    throw new UsageError(`not an auth or model verb: ${head ?? '(none)'}`);
  } catch (err) {
    const message = err instanceof UsageError || err instanceof StoreError ? err.message : `unexpected failure (${err?.name || 'error'})`;
    io.err(`error: ${message}\n`);
    return EXIT_USAGE;
  }
}

/**
 * Real process streams as the io seam.
 * @returns {{env: NodeJS.ProcessEnv, readStdin: () => Promise<string>, out: (s: string) => void, err: (s: string) => void}} io bound to process
 */
function processIo() {
  return {
    env: process.env,
    stdoutIsTTY: Boolean(process.stdout.isTTY),
    readStdin: async () => {
      if (process.stdin.isTTY) return '';
      const chunks = [];
      for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
      return Buffer.concat(chunks).toString('utf8');
    },
    out: (s) => process.stdout.write(s),
    err: (s) => process.stderr.write(s),
  };
}

const isDirectRun = (() => {
  if (!process.argv[1]) return false;
  try {
    return path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
  } catch {
    return false;
  }
})();

if (isDirectRun) {
  process.exitCode = await runAuthCli(process.argv.slice(2), processIo());
}
