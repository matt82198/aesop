// Test-only local echo backend for the chat suite.
//
// This is the word-echo source that chat/session.mjs used to export as `echoSource`. The product
// no longer exports it (the default backend is the stdio pipe runtime, and nothing in production
// calls the echo stub). Tests that need an injectable backend import it from here instead, so the
// suite keeps a source to inject while chat/session.mjs stays free of test-only exports.
//
// Streams the line back one word per chunk. No network.

/**
 * @param {string} line
 * @returns {AsyncGenerator<string>}
 */
export async function* echoSource(line) {
  const words = String(line).split(/\s+/).filter((w) => w !== '');
  for (let i = 0; i < words.length; i += 1) {
    yield i === 0 ? words[i] : ` ${words[i]}`;
  }
}
