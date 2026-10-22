// Contract under test (CI-A1 / G1.3, branch ci/push-gate-generator-timing):
//  - the scaffold announces how many push-gate generators it starts, before they run
//  - every generator reports its own wall time on stdout when it finishes, ok or FAILED
//  - the scaffold reports the total wall time once Promise.all settles
// Why: a CI deadline red ("CLI exited null" at 60 s on the Windows shard) must carry the
// per-generator durations, so the next timeout is bounded from evidence, not guessed.
//
// Isolation: HOME/USERPROFILE point at a temp dir; no network, no remote.
//
// Run: node --test tests/push-gate-generator-timing.test.mjs

import './helpers/isolated-env.mjs';

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { timedSpawnSync } from './helpers/timed-spawn.mjs';

const CLI = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'bin', 'cli.js');

// The push-gate generators bin/cli.js runs (PUSH_GATE_ARTIFACTS), by script basename.
const GENERATOR_SCRIPTS = [
  'gen_tool_index.py',
  'pyflakes_gate.py',
  'subprocess_guard.py',
  'portability_check.py',
  'stateapi_lint.py'
];

test('scaffold prints push-gate start, per-generator timings, and the total', () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-pgtiming-'));
  try {
    const home = path.join(base, 'home');
    fs.mkdirSync(home, { recursive: true });
    const env = { ...process.env, HOME: home, USERPROFILE: home, GIT_TERMINAL_PROMPT: '0' };

    const res = timedSpawnSync(
      process.execPath,
      [CLI, 'timing-fleet', '--no-skills'],
      { cwd: base, encoding: 'utf8', env, timeout: 120000, killSignal: 'SIGKILL' },
      'scaffold'
    );
    const out = `${res.stdout || ''}\n${res.stderr || ''}`;
    assert.equal(res.status, 0, `scaffold failed.\n${out}`);

    const startMatch = out.match(/^\[push-gate\] starting (\d+) generators$/m);
    assert.ok(startMatch, `scaffold must print "[push-gate] starting <n> generators".\n${out}`);
    assert.equal(Number(startMatch[1]), GENERATOR_SCRIPTS.length, `start line must count every generator.\n${out}`);

    const lineRe = /^\[push-gate\] (\S+) (ok|FAILED) in (\S+) ms(?:: .*)?$/gm;
    const timed = [...out.matchAll(lineRe)];
    assert.equal(timed.length, GENERATOR_SCRIPTS.length, `expected one timing line per generator.\n${out}`);
    for (const m of timed) {
      assert.match(m[3], /^\d+$/, `timing must be a non-negative integer ms, got "${m[3]}" on: ${m[0]}`);
    }
    assert.deepEqual(
      timed.map((m) => m[1]).sort(),
      [...GENERATOR_SCRIPTS].sort(),
      `each generator must report its own timing line.\n${out}`
    );

    const totalMatch = out.match(/^\[push-gate\] all (\d+) generators done in (\d+) ms$/m);
    assert.ok(totalMatch, `scaffold must print "[push-gate] all <n> generators done in <ms> ms".\n${out}`);
    assert.equal(Number(totalMatch[1]), GENERATOR_SCRIPTS.length, `total line must count every generator.\n${out}`);

    const startAt = out.indexOf('[push-gate] starting');
    const firstAt = out.indexOf('[push-gate] ', startAt + 1);
    const totalAt = out.indexOf('[push-gate] all ');
    assert.ok(startAt < firstAt && firstAt < totalAt, `start line, then generator lines, then the total.\n${out}`);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});
