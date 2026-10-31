#!/usr/bin/env node
/**
 * Auth and model verbs for aesop: `aesop auth ...`, `aesop model ...`, and the control channel.
 *
 * Exit codes: 0 ok; 2 bad usage, malformed input, or a corrupt store; 3 `model use` for a
 * provider that needs a credential when none is available. Secrets never reach stdout or
 * stderr. Output names the provider, the credential source, and the fixed word `set` or `none`;
 * no fragment of a key is ever shown. Error messages name the expected shape (a provider list,
 * `<provider>/<model>`, a flag name) and never echo an argv value.
 *
 * Invoked by bin/cli.js with the full argv (`auth login ...`, `model list`, ...). Control ops
 * (`auth.status`, `auth.login`, `auth.logout`, `model.list`, `model.use`, `model.current`) travel
 * only on the declared stream-json channel: `aesop pipe --input-format stream-json --output-format stream-json`.
 *
 * Zero-dependency: node: builtins and relative repo files only.
 *
 * @module auth/cli
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PROVIDERS, findProvider, parseSelector } from './providers.mjs';
import { readStore, writeStore, resolveCredential, StoreError } from './store.mjs';
import { setControlDispatcher, CONTROL_CHANNEL_MESSAGE, hasControlFlag } from '../stdio/pipe.mjs';

// One copy of the control-channel line and of the `--control` predicate, owned by stdio/pipe.mjs (the
// pipe prints the line too and may not import auth/). Re-exported here so auth/cli.mjs is the public source.
export { CONTROL_CHANNEL_MESSAGE, hasControlFlag };

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
 * Machine-readable codes carried by every ControlOpError and every control error envelope (R-CONTROL-1).
 * `unknown_op`: op name not in the registry. `unknown_provider`: a string provider name not registered.
 * `unknown_model_selector`: a string that is malformed, unregistered or unlisted `<provider>/<model>`.
 * `invalid_argument`: a known op with an argument of the wrong shape, including a non-string provider or model selector. `no_credential`: the selected
 * model has no credential from env or store. `store_error`: the store is unreadable or corrupt.
 * `op_failed`: any other refusal of a known op.
 * @type {Readonly<string[]>}
 */
export const CONTROL_ERROR_CODES = Object.freeze([
  'unknown_op',
  'unknown_provider',
  'unknown_model_selector',
  'invalid_argument',
  'no_credential',
  'store_error',
  'op_failed',
]);

/**
 * A control op that cannot be answered. The message is safe to print; the stdio pipe maps it to an
 * ok:false envelope that carries `code`. Extends UsageError so the verb dispatcher prints it the same way.
 */
export class ControlOpError extends UsageError {
  /**
   * @param {string} message one-line explanation, safe to print
   * @param {string} code one of CONTROL_ERROR_CODES; a missing or unknown code is a programming error
   */
  constructor(message, code) {
    super(message);
    if (!CONTROL_ERROR_CODES.includes(code)) throw new TypeError(`ControlOpError needs a code from CONTROL_ERROR_CODES, got ${String(code)}`);
    this.name = 'ControlOpError';
    this.code = code;
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
    // Only the flag name is ever reported: anything after `=` is a value and is never echoed.
    const name = arg.slice(2).split('=')[0];
    if (spec.value.includes(name)) {
      if (i + 1 >= argv.length || argv[i + 1].startsWith('--')) {
        throw new UsageError(`--${name} requires a value`);
      }
      values[name] = argv[++i];
    } else if (spec.flag.includes(name)) {
      flags.add(name);
    } else {
      // The unknown flag is never echoed (it may be a key pasted by mistake); the valid list names the shape.
      const valid = [...spec.value, ...spec.flag].map((f) => `--${f}`).join(', ');
      throw new UsageError(`unknown flag for this verb (valid flags: ${valid})`);
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
  if (!provider) throw new UsageError(`unknown provider (registered: ${providerNames()})`);
  return provider;
}

/**
 * Status rows for the given providers, with no secret in any field. `credential` is the fixed word
 * `set` (a credential resolves from env or the store) or `none`. `keyTail` is the fixed null slot of the
 * status schema: no part of a key is ever shown (R1), so it is always null and existing consumers keep
 * their shape.
 * @param {object[]} providers registry entries to report
 * @param {NodeJS.ProcessEnv} env environment (credential source)
 * @param {object} store parsed store
 * @returns {{provider: string, source: string, model: string, credential: 'set'|'none', keyTail: null}[]} rows
 */
function statusRows(providers, env, store) {
  return providers.map((provider) => {
    const { source, key } = resolveCredential(provider, env, store);
    const entry = store.providers[provider.name];
    return {
      provider: provider.name,
      source,
      model: entry?.model ?? provider.defaultModel,
      credential: key ? 'set' : 'none',
      keyTail: null,
    };
  });
}

/**
 * The `auth status` JSON object. Shared by the verb (non-TTY stdout) and the control op `auth.status`.
 * @param {object[]} providers providers to report
 * @param {NodeJS.ProcessEnv} env environment (credential source)
 * @param {object} store parsed store
 * @returns {{ok: true, op: 'auth.status', providers: object[]}} machine-readable status
 */
function statusObject(providers, env, store) {
  return { ok: true, op: 'auth.status', providers: statusRows(providers, env, store) };
}

/**
 * The `model list` JSON object. Shared by the verb and the control op `model.list`.
 * @returns {{ok: true, op: 'model.list', providers: object[]}} machine-readable list
 */
function modelListObject() {
  const providers = PROVIDERS.map((p) => ({ provider: p.name, defaultModel: p.defaultModel, models: [...p.models], envVar: p.envVar }));
  return { ok: true, op: 'model.list', providers };
}

/**
 * The `model current` JSON object. Shared by the verb and the control op `model.current`.
 * @param {object} store parsed store
 * @returns {{ok: true, op: 'model.current', provider: string|null, model: string|null, selector: string|null}} selection
 */
function modelCurrentObject(store) {
  const cur = store.current;
  return {
    ok: true,
    op: 'model.current',
    provider: cur?.provider ?? null,
    model: cur?.model ?? null,
    selector: cur ? `${cur.provider}/${cur.model}` : null,
  };
}

/** Resolve a required-string `provider` field of a control record to its registry entry. */
function namedProvider(msg) {
  if (typeof msg.provider !== 'string') throw new ControlOpError('control: provider must be a string', 'invalid_argument');
  const provider = findProvider(msg.provider);
  if (!provider) throw new ControlOpError(`control: unknown provider (registered: ${providerNames()})`, 'unknown_provider');
  return provider;
}

/** Resolve the optional `provider` field of a control record to the providers it names. */
function controlProviders(msg) {
  if (msg.provider === undefined) return [...PROVIDERS];
  return [namedProvider(msg)];
}

/**
 * Store a credential for one provider, keeping the provider's selected model. The only write path
 * for `auth login` and the `auth.login` op.
 * @param {NodeJS.ProcessEnv} env environment (store location)
 * @param {{name: string, defaultModel: string}} provider registry entry
 * @param {string} key the credential; written to the store, never returned
 */
function storeCredential(env, provider, key) {
  const store = readStore(env);
  const previous = store.providers[provider.name];
  store.providers[provider.name] = {
    credential: key,
    model: previous?.model ?? provider.defaultModel,
    addedAt: new Date().toISOString(),
  };
  writeStore(env, store);
}

/**
 * Log out one provider, or every provider when `provider` is null. Drops its stored credential, and
 * clears the model selection whenever the selection names a logged-out provider, whether that
 * provider's credential came from the store or from an environment variable (the env var itself is
 * never touched). So `model current` never names a provider that is logged out (R4).
 * The only logout write path for `auth logout` and the `auth.logout` op.
 * @param {NodeJS.ProcessEnv} env environment (store location)
 * @param {{name: string}|null} provider registry entry, or null for all providers
 */
function dropCredentials(env, provider) {
  const store = readStore(env);
  const removed = Object.keys(store.providers).filter((name) => !provider || name === provider.name);
  for (const name of removed) delete store.providers[name];
  const selectionLost = store.current !== null && (!provider || store.current.provider === provider.name);
  if (selectionLost) store.current = null;
  if (removed.length || selectionLost) writeStore(env, store);
}

/**
 * Parse and validate a `<provider>/<model>` selector. Throws UsageError for a malformed selector,
 * an unregistered provider, or a model the provider does not list.
 * @param {string} text selector text
 * @returns {{provider: {name: string, envVar: string|null, models: string[]}, model: string}} validated pair
 */
function parseModelSelector(text) {
  const selector = parseSelector(text);
  if (!selector) throw new UsageError('malformed selector (expected <provider>/<model>)');
  const provider = requireProvider(selector.provider);
  if (!provider.models.includes(selector.model)) {
    throw new UsageError(`unknown model for ${provider.name} (models: ${provider.models.join(', ')})`);
  }
  return { provider, model: selector.model };
}

/** True when the provider needs no credential or one resolves from env or the store. */
function credentialAvailable(provider, env) {
  return provider.envVar === null || resolveCredential(provider, env, readStore(env)).source !== 'none';
}

/** The safe, one-line message for a model selection that has no credential behind it. */
function noCredentialMessage(provider) {
  return `no credential for ${provider.name}: set ${provider.envVar} or AESOP_API_KEY, or run: auth login --provider ${provider.name} --api-key-stdin`;
}

/**
 * Record the current model selection in the store. The only write path for `model use` and `model.use`.
 * @param {NodeJS.ProcessEnv} env environment (store location)
 * @param {{name: string}} provider validated provider
 * @param {string} model validated model id
 * @returns {object} the store as written
 */
function applyModelSelection(env, provider, model) {
  const store = readStore(env);
  if (store.providers[provider.name]) store.providers[provider.name].model = model;
  store.current = { provider: provider.name, model };
  writeStore(env, store);
  return store;
}

/**
 * Control op `auth.login`: `{api_key: string, provider?: string}`. Provider defaults to anthropic.
 * @param {object} msg control record
 * @param {NodeJS.ProcessEnv} env environment
 * @returns {object} the `auth status` object for that provider after the op
 */
function controlLogin(msg, env) {
  const provider = msg.provider === undefined ? findProvider('anthropic') : namedProvider(msg);
  if (provider.envVar === null) throw new ControlOpError(`control: provider ${provider.name} needs no credential`, 'invalid_argument');
  if (typeof msg.api_key !== 'string' || msg.api_key.trim() === '' || /[\r\n]/.test(msg.api_key.trim())) {
    throw new ControlOpError('control: auth.login requires api_key as a non-empty single-line string', 'invalid_argument');
  }
  storeCredential(env, provider, msg.api_key.trim());
  return statusObject([provider], env, readStore(env));
}

/**
 * Control op `auth.logout`: `{provider?: string}`. Without provider, clears every stored credential.
 * @param {object} msg control record
 * @param {NodeJS.ProcessEnv} env environment
 * @returns {object} the `auth status` object for the affected providers after the op
 */
function controlLogout(msg, env) {
  const provider = msg.provider === undefined ? null : namedProvider(msg);
  dropCredentials(env, provider);
  return statusObject(provider ? [provider] : [...PROVIDERS], env, readStore(env));
}

/**
 * Control op `model.use`: `{model: "<provider>/<model>"}`. Same checks as `model use`.
 * @param {object} msg control record
 * @param {NodeJS.ProcessEnv} env environment
 * @returns {object} the `model current` object after the op
 */
function controlModelUse(msg, env) {
  if (typeof msg.model !== 'string') throw new ControlOpError('control: model.use requires model as a <provider>/<model> string', 'invalid_argument');
  let parsed;
  try {
    parsed = parseModelSelector(msg.model);
  } catch (err) {
    // The selector is caller-supplied and is never echoed; the reply names the problem only.
    if (err instanceof UsageError) throw new ControlOpError('unknown model selector', 'unknown_model_selector');
    throw err;
  }
  const { provider, model } = parsed;
  if (!credentialAvailable(provider, env)) throw new ControlOpError(noCredentialMessage(provider), 'no_credential');
  return modelCurrentObject(applyModelSelection(env, provider, model));
}

/**
 * Control op registry. Each entry returns the same object its CLI verb prints as one JSON line.
 * Results carry source and names only, never key material.
 * @type {Readonly<Record<string, (msg: object, env: NodeJS.ProcessEnv) => object>>}
 */
const CONTROL_OPS = Object.freeze({
  'auth.status': (msg, env) => statusObject(controlProviders(msg), env, readStore(env)),
  'auth.login': controlLogin,
  'auth.logout': controlLogout,
  'model.list': () => modelListObject(),
  'model.current': (msg, env) => modelCurrentObject(readStore(env)),
  'model.use': controlModelUse,
});

/**
 * Answer one control op. Every failure is a ControlOpError with a safe message.
 * @param {object} msg control record with a string `op` and optional op arguments
 * @param {NodeJS.ProcessEnv} [env] environment, defaults to process.env
 * @returns {object} the result object for the op
 * @throws {ControlOpError} for an unknown op, bad arguments, or an unreadable store
 */
export function runControlOp(msg, env = process.env) {
  const op = msg.op;
  if (typeof op !== 'string' || !Object.prototype.hasOwnProperty.call(CONTROL_OPS, op)) {
    // The op is never echoed (it may be a pasted key). The pipe turns this into the fixed envelope
    // error "unknown op" with no op field.
    throw new ControlOpError('unknown op', 'unknown_op');
  }
  try {
    return CONTROL_OPS[op](msg, env);
  } catch (err) {
    if (err instanceof ControlOpError) throw err;
    // UsageError and StoreError messages are written to be safe to print; the pipe only forwards ControlOpError text.
    if (err instanceof StoreError) throw new ControlOpError(err.message, 'store_error');
    if (err instanceof UsageError) throw new ControlOpError(err.message, 'op_failed');
    throw new ControlOpError(`control op ${op} failed`, 'op_failed');
  }
}

/**
 * `auth login --provider <name> --api-key-stdin`.
 * @param {string[]} argv arguments after `auth login`
 * @param {object} io runtime seam (env, readStdin, out, err)
 * @returns {Promise<number>} exit code
 */
async function authLogin(argv, io) {
  const { values, flags, positional } = parseArgs(argv, { value: ['provider'], flag: ['api-key-stdin'] });
  if (positional.length) throw new UsageError('unexpected argument; keys are read from stdin only (--api-key-stdin)');
  if (!values.provider) throw new UsageError('auth login requires --provider <name>');
  const provider = requireProvider(values.provider);
  if (provider.envVar === null) throw new UsageError(`provider ${provider.name} needs no credential`);
  if (!flags.has('api-key-stdin')) {
    throw new UsageError('auth login requires --api-key-stdin (the key is read from stdin, never accepted as an argument)');
  }
  const key = (await io.readStdin()).split(/\r?\n/)[0].trim();
  if (!key) throw new UsageError('no key on stdin');
  storeCredential(io.env, provider, key);
  io.out(`logged in: ${provider.name} source=store credential=set\n`);
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
  if (positional.length) throw new UsageError('unexpected argument (auth status takes only --provider <name>)');
  const providers = values.provider ? [requireProvider(values.provider)] : [...PROVIDERS];
  const store = readStore(io.env);
  const rows = statusRows(providers, io.env, store);
  if (!io.stdoutIsTTY) {
    io.out(`${JSON.stringify(statusObject(providers, io.env, store))}\n`);
    return EXIT_OK;
  }
  for (const row of rows) {
    io.out(`${row.provider}: source=${row.source} model=${row.model} credential=${row.credential}\n`);
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
  if (positional.length) throw new UsageError('unexpected argument (auth logout takes only --provider <name>)');
  const provider = values.provider ? requireProvider(values.provider) : null;
  dropCredentials(io.env, provider);
  io.out(provider ? `logged out: ${provider.name}\n` : 'logged out: all providers\n');
  // Logout never unsets an environment credential, so each provider that still resolves from env gets one
  // line (R5). The line names the provider and source=env only; the value is never read into output.
  for (const row of statusRows(provider ? [provider] : [...PROVIDERS], io.env, readStore(io.env))) {
    if (row.source === 'env') io.out(`still set: ${row.provider} source=env (logout does not remove an environment credential)\n`);
  }
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
  throw new UsageError(verb ? 'unknown auth verb (use login, status, logout)' : 'auth requires a verb: login, status, or logout');
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
      io.out(`${JSON.stringify(modelListObject())}\n`);
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
    const { provider, model } = parseModelSelector(rest[0]);
    if (!credentialAvailable(provider, io.env)) {
      io.err(`${noCredentialMessage(provider)}\n`);
      return EXIT_NO_CREDENTIAL;
    }
    applyModelSelection(io.env, provider, model);
    io.out(`current model: ${provider.name}/${model}\n`);
    return EXIT_OK;
  }
  if (verb === 'current') {
    if (rest.length) throw new UsageError('model current takes no arguments');
    const store = readStore(io.env);
    const cur = store.current;
    if (!io.stdoutIsTTY) {
      io.out(`${JSON.stringify(modelCurrentObject(store))}\n`);
    } else if (!cur) {
      io.out(`no model selected (default: anthropic/${findProvider('anthropic').defaultModel})\n`);
    } else {
      io.out(`${cur.provider}/${cur.model}\n`);
    }
    return EXIT_OK;
  }
  throw new UsageError(verb ? 'unknown model verb (use list, use, current)' : 'model requires a verb: list, use, or current');
}

/**
 * Entry point shared by bin/cli.js and the direct-run path. Never throws; maps every failure to an exit code.
 * @param {string[]} argv argv after the program name (`auth ...` or `model ...`)
 * @param {{env: NodeJS.ProcessEnv, readStdin: () => Promise<string>, out: (s: string) => void, err: (s: string) => void}} io runtime seam
 * @returns {Promise<number>} exit code: 0, 2, or 3
 */
export async function runAuthCli(argv, io) {
  // Control records have one channel. `--control` or `--control=<x>` is refused on every auth/model path,
  // wherever it sits, first, with the bare CONTROL_CHANNEL_MESSAGE line (no `error:` prefix).
  if (hasControlFlag(argv)) {
    io.err(`${CONTROL_CHANNEL_MESSAGE}\n`);
    return EXIT_USAGE;
  }
  try {
    const [head, ...rest] = argv;
    if (head === 'auth') return await authVerb(rest, io);
    if (head === 'model') return modelVerb(rest, io);
    throw new UsageError('not an auth or model verb (use auth ... or model ...)');
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

// Control channel registration. bin/cli.js preloads this module (node --import) before running the
// stdio pipeline, so `aesop pipe --input-format stream-json --output-format stream-json` answers control
// records from the same registry as the verbs. Without the preload, control records fail closed.
setControlDispatcher((record) => Promise.resolve(runControlOp(record, process.env)));

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
