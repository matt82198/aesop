/**
 * Provider registry for the generic auth and model-selection CLI.
 *
 * Zero-dependency: node: builtins and relative repo files only.
 *
 * @module auth/providers
 */

/**
 * @typedef {object} Provider
 * @property {string} name provider id used by `--provider` and by `<provider>/<model>` selectors
 * @property {string|null} envVar environment variable that carries a credential, or null when none is needed
 * @property {string} defaultModel model selected at login
 * @property {ReadonlyArray<string>} models models a selector may name for this provider
 */

/** Generic credential override. Counts as an env source for every provider. */
export const GENERIC_ENV_VAR = 'AESOP_API_KEY';

/** @type {ReadonlyArray<Provider>} */
export const PROVIDERS = Object.freeze([
  Object.freeze({
    name: 'anthropic',
    envVar: 'ANTHROPIC_API_KEY',
    defaultModel: 'claude-fable-5-1',
    models: Object.freeze(['claude-fable-5-1', 'claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5']),
  }),
  Object.freeze({
    name: 'local',
    envVar: null,
    defaultModel: 'default',
    models: Object.freeze(['default']),
  }),
]);

/**
 * Look up a provider by name.
 * @param {string} name provider id
 * @returns {Provider|null} the registry entry, or null when unregistered
 */
export function findProvider(name) {
  return PROVIDERS.find((p) => p.name === name) ?? null;
}

/**
 * Parse a `<provider>/<model>` selector.
 * @param {string} selector raw argument, e.g. `anthropic/claude-fable-5-1`
 * @returns {{provider: string, model: string}|null} parts, or null when malformed
 */
export function parseSelector(selector) {
  if (typeof selector !== 'string') return null;
  const idx = selector.indexOf('/');
  if (idx <= 0 || idx === selector.length - 1) return null;
  const model = selector.slice(idx + 1);
  if (model.includes('/')) return null;
  return { provider: selector.slice(0, idx), model };
}
