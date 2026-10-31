// ESM resolve hook for the bin/cli.js chat-entry wiring tests. Any import that resolves to
// chat/cli.mjs is redirected to tests/helpers/chat-entry-stub.mjs, so a child process started with
// tests/helpers/chat-entry-stub-register.mjs under --import sees a stubbed shouldEnterChat/runChat.
// Only the chat entry module is affected; every other specifier resolves normally.

const STUB_URL = new URL('./chat-entry-stub.mjs', import.meta.url).href;

/**
 * @param {string} specifier
 * @param {object} context
 * @param {(specifier: string, context: object) => Promise<object>} nextResolve
 */
export async function resolve(specifier, context, nextResolve) {
  const resolved = await nextResolve(specifier, context);
  if (resolved.url.endsWith('/chat/cli.mjs')) {
    return { url: STUB_URL, format: 'module', shortCircuit: true };
  }
  return resolved;
}
