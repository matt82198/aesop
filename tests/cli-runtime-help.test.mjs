// Tests for `--help` and `-h` on the remaining runtime subcommands of bin/cli.js.
// Follow-up to tests/cli-subcommand-help.test.mjs (init, doctor), which covers the
// SUBCOMMAND_HELP entries. Every other runtime subcommand named in the dispatcher's
// `runtimeCommands` list must print a usage block, exit 0, write nothing to stderr,
// and leave the cwd untouched before any side effect runs.
//
// Subcommand set: the union of bin/CLAUDE.md "Runtime commands" row and the dispatcher's
// `runtimeCommands` array (bin/cli.js). The row omits `health` and `runner`; the dispatcher
// includes both, so both are covered here.
//
// Run: node --test tests/cli-runtime-help.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLI = path.join(REPO_ROOT, 'bin', 'cli.js');

// Subcommands not yet covered by SUBCOMMAND_HELP in bin/cli.js (init and doctor are
// covered by tests/cli-subcommand-help.test.mjs).
const REMAINING_SUBCOMMANDS = ['watch', 'dash', 'status', 'fleet', 'health', 'health-score', 'reproduce', 'runner'];

// Short timeout: a subcommand that runs its side effect may block (watch, dash), and a
// timeout surfaces as a failed assertion instead of a two-minute hang.
const SPAWN_TIMEOUT_MS = 30000;

function listTree(dir) {
  const out = [];
  const walk = (d, rel) => {
    for (const name of fs.readdirSync(d)) {
      const abs = path.join(d, name);
      const r = rel ? `${rel}/${name}` : name;
      out.push(r);
      if (fs.statSync(abs).isDirectory()) walk(abs, r);
    }
  };
  walk(dir, '');
  return out.sort();
}

/**
 * Run the CLI in a fresh empty cwd with a fresh fake HOME and report the spawn
 * result plus every entry left in the cwd afterwards.
 * @param {string[]} args CLI arguments
 */
function sandboxRun(args) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-clihelp-rt-cwd-'));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-clihelp-rt-home-'));
  try {
    const env = { ...process.env, HOME: home, USERPROFILE: home };
    delete env.AESOP_SKILLS_HOME;
    const res = spawnSync(process.execPath, [CLI, ...args], {
      cwd: work,
      env,
      input: '',
      encoding: 'utf8',
      timeout: SPAWN_TIMEOUT_MS,
      windowsHide: true,
    });
    return { res, workEntries: listTree(work) };
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
    fs.rmSync(home, { recursive: true, force: true });
  }
}

for (const sub of REMAINING_SUBCOMMANDS) {
  for (const flag of ['--help', '-h']) {
    test(`\`cli.js ${sub} ${flag}\` prints usage, exits 0, writes no stderr, and leaves cwd empty`, () => {
      const r = sandboxRun([sub, flag]);
      assert.equal(r.res.error, undefined, `spawn failed or timed out: ${r.res.error}`);
      assert.equal(r.res.status, 0, `exit status ${r.res.status}; stderr: ${r.res.stderr}`);
      assert.match(r.res.stdout, new RegExp(`Usage`), `stdout has no Usage block:\n${r.res.stdout}`);
      assert.ok(r.res.stdout.includes(sub), `stdout does not name "${sub}":\n${r.res.stdout}`);
      assert.equal(r.res.stderr, '', `stderr must be empty, got: ${r.res.stderr}`);
      assert.deepEqual(r.workEntries, [], `cwd must stay empty, found: ${r.workEntries.join(', ')}`);
    });
  }
}
