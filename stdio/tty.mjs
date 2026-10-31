/**
 * TTY detection seam for the stdio pipeline (the public name). The decision itself lives in
 * stdio/tty-decision.js so CommonJS callers (bin/cli.js) and this ESM seam share one function.
 * bin/cli.js branches on detectMode(): 'interactive' scaffolds, 'filter' is the pipe.
 *
 * Node stdlib only. Importing this module performs no I/O.
 *
 * @module stdio/tty
 */

/**
 * isTTY(stream) → boolean: true only when the stream is present and reports isTTY === true.
 * detectMode({stdin, stdout}) → 'interactive' | 'filter': 'interactive' only when both are terminals.
 * Both are defined in tty-decision.js (CommonJS, so bin/cli.js can load the same function).
 */
export { isTTY, detectMode } from './tty-decision.js';
