/**
 * TTY detection seam, exporting isTTY(stream) and detectMode(streams). chat/cli.mjs calls detectMode()
 * to choose interactive mode; the stdio pipeline always runs as a filter and never reads these values.
 *
 * Node stdlib only. Importing this module performs no I/O.
 *
 * @module stdio/tty
 */

/**
 * @param {unknown} stream a Node stream or any object
 * @returns {boolean} true only when the stream is present and reports isTTY === true
 */
export function isTTY(stream) {
  return Boolean(stream) && stream.isTTY === true;
}

/**
 * Chooses the pipeline mode from the attached streams.
 * @param {{stdin?: unknown, stdout?: unknown}} [streams] defaults to process.stdin and process.stdout
 * @returns {'interactive'|'filter'} 'interactive' only when both stdin and stdout are terminals
 */
export function detectMode({ stdin = process.stdin, stdout = process.stdout } = {}) {
  return isTTY(stdin) && isTTY(stdout) ? 'interactive' : 'filter';
}
