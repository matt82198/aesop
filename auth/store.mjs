/**
 * Credential store for the auth and model CLI: one JSON file, written atomically.
 *
 * Location: `$AESOP_CONFIG_DIR/auth.json` (default `<home>/.aesop/auth.json`).
 * Shape: `{ version, providers: { <name>: { credential, model, addedAt } }, current: { provider, model } | null }`.
 * Env secrets are never persisted. A corrupt file is reported, never overwritten.
 *
 * Zero-dependency: node: builtins and relative repo files only.
 *
 * @module auth/store
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { GENERIC_ENV_VAR } from './providers.mjs';

/** Raised for unreadable, corrupt, or unwritable store files. The CLI maps it to exit 2. */
export class StoreError extends Error {
  /** @param {string} message one-line explanation (never contains a secret) */
  constructor(message) {
    super(message);
    this.name = 'StoreError';
  }
}

/**
 * Directory holding the store. AESOP_CONFIG_DIR overrides the default.
 * @param {NodeJS.ProcessEnv} [env=process.env] environment to read
 * @returns {string} absolute or relative config directory
 */
export function configDir(env = process.env) {
  if (env.AESOP_CONFIG_DIR) return env.AESOP_CONFIG_DIR;
  const home = env.HOME || env.USERPROFILE || os.homedir();
  return path.join(home, '.aesop');
}

/**
 * Full path of the single store file.
 * @param {NodeJS.ProcessEnv} [env=process.env] environment to read
 * @returns {string} path to auth.json
 */
export function storePath(env = process.env) {
  return path.join(configDir(env), 'auth.json');
}

/**
 * Read the store. A missing file is an empty store; malformed content throws StoreError.
 * @param {NodeJS.ProcessEnv} [env=process.env] environment to read
 * @returns {{version: number, providers: Record<string, object>, current: {provider: string, model: string}|null}}
 * @throws {StoreError} when the file exists but is not a valid store
 */
export function readStore(env = process.env) {
  const file = storePath(env);
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') return { version: 1, providers: {}, current: null };
    throw new StoreError(`cannot read store ${file} (${err.code || 'read error'})`);
  }
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    throw new StoreError(`store ${file} is not valid JSON; fix or remove it (it was left untouched)`);
  }
  const providers = json && typeof json === 'object' && !Array.isArray(json) ? json.providers : undefined;
  if (!providers || typeof providers !== 'object' || Array.isArray(providers)) {
    throw new StoreError(`store ${file} has no providers object; fix or remove it (it was left untouched)`);
  }
  for (const [name, entry] of Object.entries(providers)) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      throw new StoreError(`store ${file} entry for ${name} is malformed (it was left untouched)`);
    }
  }
  return { version: 1, providers, current: json.current ?? null };
}

/**
 * Write the store atomically: temp file in the same directory, then rename. Mode 0600 off win32.
 * @param {NodeJS.ProcessEnv} env environment to read
 * @param {{providers: Record<string, object>, current: object|null}} store full store to persist
 * @throws {StoreError} when the directory or file cannot be written
 */
export function writeStore(env, store) {
  const dir = configDir(env);
  const file = storePath(env);
  const tmp = path.join(dir, `.auth.json.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`);
  const body = JSON.stringify({ version: 1, providers: store.providers, current: store.current ?? null }, null, 2) + '\n';
  try {
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    fs.writeFileSync(tmp, body, { mode: 0o600, flag: 'wx' });
    if (process.platform !== 'win32') fs.chmodSync(tmp, 0o600);
    fs.renameSync(tmp, file);
  } catch (err) {
    try {
      fs.rmSync(tmp, { force: true });
    } catch {
      // best effort: the temp file is removed on failure so no secret residue is left behind
    }
    throw new StoreError(`cannot write store ${file} (${err.code || 'write error'})`);
  }
}

/**
 * Resolve where a provider's credential comes from. Precedence: AESOP_API_KEY, then the
 * provider's env variable, then the stored credential. Env values are never persisted.
 * @param {import('./providers.mjs').Provider} provider registry entry
 * @param {NodeJS.ProcessEnv} env environment to read
 * @param {{providers: Record<string, object>}} store store read via readStore
 * @returns {{source: 'env'|'store'|'none', key: string|null}} source and the raw key (callers must only mask it)
 */
export function resolveCredential(provider, env, store) {
  const generic = typeof env[GENERIC_ENV_VAR] === 'string' ? env[GENERIC_ENV_VAR].trim() : '';
  if (generic) return { source: 'env', key: generic };
  const named = provider.envVar && typeof env[provider.envVar] === 'string' ? env[provider.envVar].trim() : '';
  if (named) return { source: 'env', key: named };
  const entry = store.providers[provider.name];
  if (entry && typeof entry.credential === 'string' && entry.credential.length > 0) {
    return { source: 'store', key: entry.credential };
  }
  return { source: 'none', key: null };
}
