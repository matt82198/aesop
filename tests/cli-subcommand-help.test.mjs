// Tests for `--help` handling on bin/cli.js subcommands.
// DEFECT (f-566c405e): `node bin/cli.js init --help` RAN init and wrote .gitignore
// and state/.gitkeep into the cwd; `node bin/cli.js doctor --help` ran the doctor.
// A help flag must print usage and perform no side effects.
//
// Run: node --test tests/cli-subcommand-help.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLI = path.join(REPO_ROOT, 'bin', 'cli.js');

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
 * result plus every entry left in each temp dir afterwards.
 * @param {string[]} args CLI arguments
 */
function sandboxRun(args) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-clihelp-cwd-'));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-clihelp-home-'));
  try {
    const env = { ...process.env, HOME: home, USERPROFILE: home };
    delete env.AESOP_SKILLS_HOME;
    const res = spawnSync(process.execPath, [CLI, ...args], {
      cwd: work,
      env,
      input: '',
      encoding: 'utf8',
      timeout: 120000,
      windowsHide: true,
    });
    return { res, workEntries: listTree(work), homeEntries: listTree(home) };
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
    fs.rmSync(home, { recursive: true, force: true });
  }
}

for (const sub of ['init', 'doctor']) {
  test(`\`cli.js ${sub} --help\` prints usage, exits 0, and writes nothing`, () => {
    const r = sandboxRun([sub, '--help']);
    assert.equal(r.res.status, 0, `stderr: ${r.res.stderr}`);
    assert.match(r.res.stdout, /Usage|--help/, `stdout did not show help:\n${r.res.stdout}`);
    assert.deepEqual(r.workEntries, [], `cwd must stay empty, found: ${r.workEntries.join(', ')}`);
    assert.deepEqual(r.homeEntries, [], `HOME must stay empty, found: ${r.homeEntries.join(', ')}`);
  });
}

test('`cli.js --help` prints usage, exits 0, and writes nothing', () => {
  const r = sandboxRun(['--help']);
  assert.equal(r.res.status, 0, `stderr: ${r.res.stderr}`);
  assert.match(r.res.stdout, /Usage|--help/, `stdout did not show help:\n${r.res.stdout}`);
  assert.deepEqual(r.workEntries, [], `cwd must stay empty, found: ${r.workEntries.join(', ')}`);
  assert.deepEqual(r.homeEntries, [], `HOME must stay empty, found: ${r.homeEntries.join(', ')}`);
});
