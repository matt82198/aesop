/**
 * The one TTY decision for the stdio pipeline, in a CommonJS-loadable form.
 *
 * bin/cli.js is CommonJS and cannot synchronously require an ES module on every supported Node
 * (engines is >=18), so the decision lives here and stdio/tty.mjs re-exports it as the public seam.
 * Both bin/cli.js and stdio/tty.mjs call this same function: there is exactly one decision.
 *
 * Node stdlib only. Loading this module performs no I/O.
 *
 * @module stdio/tty-decision
 */

'use strict';

/**
 * @param {unknown} stream a Node stream or any object
 * @returns {boolean} true only when the stream is present and reports isTTY === true
 */
function isTTY(stream) {
  return Boolean(stream) && stream.isTTY === true;
}

/**
 * Chooses the pipeline mode from the attached streams.
 * @param {{stdin?: unknown, stdout?: unknown}} [streams] defaults to process.stdin and process.stdout
 * @returns {'interactive'|'filter'} 'interactive' only when both stdin and stdout are terminals
 */
function detectMode({ stdin = process.stdin, stdout = process.stdout } = {}) {
  return isTTY(stdin) && isTTY(stdout) ? 'interactive' : 'filter';
}

module.exports = { isTTY, detectMode };
