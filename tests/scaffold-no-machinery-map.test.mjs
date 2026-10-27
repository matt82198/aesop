// Contract under test (f-61b65af5):
//  - docs/MACHINERY-MAP.md is an aesop-only artifact: it cites this repository's own
//    tree (monitor, daemons, LANE-CONTRACT.md, ...). A fresh `aesop <target>` scaffold
//    must NOT ship it, because the targets it cites are never copied into the adopter.
//  - Positive control: a docs file the scaffold is meant to ship (docs/ARCHITECTURE.md)
//    must be present, so a scaffold that copied no docs at all cannot pass vacuously.
//
// Isolation: HOME/USERPROFILE point at a temp dir; no network.
//
// Run: node --test tests/scaffold-no-machinery-map.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLI = path.join(ROOT, 'bin', 'cli.js');
const SCAFFOLD_TIMEOUT_MS = 120000;

const MACHINERY_MAP = 'docs/MACHINERY-MAP.md';
const POSITIVE_CONTROL = 'docs/ARCHITECTURE.md';

function sandbox() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-scaffold-nomap-'));
  const home = path.join(base, 'home');
  fs.mkdirSync(home, { recursive: true });
  const env = { ...process.env, HOME: home, USERPROFILE: home, GIT_TERMINAL_PROMPT: '0' };
  return { base, env };
}

function scaffold(base, env, name) {
  const res = spawnSync(process.execPath, [CLI, name, '--no-skills'], {
    cwd: base,
    encoding: 'utf8',
    env,
    timeout: SCAFFOLD_TIMEOUT_MS,
    killSignal: 'SIGKILL'
  });
  assert.notEqual(res.signal, 'SIGKILL', `scaffold timed out after ${SCAFFOLD_TIMEOUT_MS} ms`);
  assert.equal(res.status, 0, `scaffold failed. stdout: ${res.stdout}\nstderr: ${res.stderr}`);
  return path.join(base, name);
}

test('fresh scaffold does not ship the aesop-only machinery map', () => {
  const { base, env } = sandbox();
  try {
    const target = scaffold(base, env, 'fresh-fleet');

    assert.ok(
      fs.existsSync(path.join(target, POSITIVE_CONTROL)),
      `${POSITIVE_CONTROL} must be shipped by the scaffold (positive control)`
    );
    assert.ok(
      !fs.existsSync(path.join(target, MACHINERY_MAP)),
      `${MACHINERY_MAP} must not be shipped by the scaffold: it cites aesop-only targets the scaffold never copies`
    );
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});
