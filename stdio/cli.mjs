#!/usr/bin/env node
/**
 * Executable entry for the stdio pipeline: `node stdio/cli.mjs [flags]`.
 *
 * Calls runPipe with the process streams and sets process.exitCode. It never calls process.exit,
 * so stdout drains before the process ends.
 *
 * Flags: --input-format, --output-format, --content-type, --max-frame, --help.
 * See stdio/CLAUDE.md for the contract.
 *
 * @module stdio/cli
 */

import { runPipe } from './pipe.mjs';

const exitCode = await runPipe({
  stdin: process.stdin,
  stdout: process.stdout,
  stderr: process.stderr,
  argv: process.argv.slice(2),
});

process.exitCode = exitCode;
