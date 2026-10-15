// Tests for the scaffolder's top-level flag handling (bin/cli.js).
// Contract under test:
//  - `--version` and `-v` print the package.json version to stdout and exit 0,
//    before any filesystem work (no directory scaffolded, home untouched).
//  - any `-`-prefixed argument that is not a documented flag prints exactly one
//    `unknown option: <arg>` line to stderr (followed by the help text) and exits 2,
//    before any filesystem work. A typo must never scaffold a project or touch ~/.claude.
//  - `--help` lists `--version`.
//  - documented flags are NOT reported as unknown (over-rejection guard).
//
// Every spawn runs with cwd and HOME/USERPROFILE pointed at fresh temp dirs and
// AESOP_SKILLS_HOME unset, so any regression that scaffolds or installs skills is
// observable as a non-empty directory after the run.
//
// Run: node --test tests/cli-version-unknown-flags.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLI = path.join(REPO_ROOT, 'bin', 'cli.js');
const PKG_VERSION = JSON.parse(
  fs.readFileSync(path.join(REPO_ROOT, 'package.json'), 'utf8'),
).version;

/** Recursively list every entry under dir (relative paths). Empty when dir is empty. */
function listTree(dir) {
  const out = [];
  const walk = (d) => {
    for (const name of fs.readdirSync(d)) {
      const p = path.join(d, name);
      out.push(path.relative(dir, p));
      if (fs.lstatSync(p).isDirectory()) walk(p);
    }
  };
  walk(dir);
  return out.sort();
}

/**
 * Run the CLI in a fresh cwd with a fresh fake HOME and report the spawn result
 * plus every entry left in each temp dir afterwards.
 * @param {string[]} args CLI arguments
 */
function sandboxRun(args) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-cliver-cwd-'));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-cliver-home-'));
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

test('--version prints the package.json version, exits 0, and creates nothing', () => {
  const r = sandboxRun(['--version']);
  assert.equal(r.res.status, 0, `stderr: ${r.res.stderr}`);
  assert.equal(r.res.stdout.trim(), PKG_VERSION);
  assert.deepEqual(r.workEntries, []);
  assert.deepEqual(r.homeEntries, []);
});

test('-v prints the package.json version, exits 0, and creates nothing', () => {
  const r = sandboxRun(['-v']);
  assert.equal(r.res.status, 0, `stderr: ${r.res.stderr}`);
  assert.equal(r.res.stdout.trim(), PKG_VERSION);
  assert.deepEqual(r.workEntries, []);
  assert.deepEqual(r.homeEntries, []);
});

test('--version wins over a target directory and scaffolds nothing', () => {
  const r = sandboxRun(['my-fleet', '--version']);
  assert.equal(r.res.status, 0, `stderr: ${r.res.stderr}`);
  assert.equal(r.res.stdout.trim(), PKG_VERSION);
  assert.deepEqual(r.workEntries, []);
  assert.deepEqual(r.homeEntries, []);
});

test('--bogus exits 2 with one unknown-option line, creates no directory, leaves home untouched', () => {
  const r = sandboxRun(['--bogus']);
  assert.equal(r.res.status, 2, `stdout: ${r.res.stdout}\nstderr: ${r.res.stderr}`);
  const unknownLines = r.res.stderr.split(/\r?\n/).filter((l) => l.startsWith('unknown option:'));
  assert.deepEqual(unknownLines, ['unknown option: --bogus']);
  assert.match(r.res.stderr, /Options:/, 'help text should follow the unknown-option line');
  assert.deepEqual(r.workEntries, [], 'no aesop-fleet or other directory may be created');
  assert.deepEqual(r.homeEntries, [], 'fake HOME (and ~/.claude/skills under it) must be untouched');
});

test('--help lists --version', () => {
  const r = sandboxRun(['--help']);
  assert.equal(r.res.status, 0, `stderr: ${r.res.stderr}`);
  assert.match(r.res.stdout, /--version/);
});

test('a documented flag is not reported as unknown', () => {
  const r = sandboxRun(['--help', '--no-skills']);
  assert.equal(r.res.status, 0, `stderr: ${r.res.stderr}`);
  assert.doesNotMatch(r.res.stderr, /unknown option:/);
});
