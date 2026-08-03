// Regression suite for the fail-CLOSED agent-stall signal in monitor/collect-signals.mjs (GAP 3).
//
// Before this guard, every failure path of checkAgentStalls() returned
// {available:false, count:0}: a broken/absent/timed-out stall check was
// indistinguishable from "no agents are stalled". This suite proves the
// collector now emits a REAL degraded signal (state UNKNOWN, degraded true,
// count null) and shouts about it in BRIEF.md instead of going quiet.
//
// Node.js built-ins only (node:test, node:assert, node:fs, node:path, node:os,
// node:child_process), fixture dirs under os.tmpdir(), no cwd pollution.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const collectorPath = path.join(here, '..', 'monitor', 'collect-signals.mjs');
const collectorSource = fs.readFileSync(collectorPath, 'utf8');

function getTestTimeout() {
  const defaultTimeout = process.platform === 'win32' ? 60000 : 30000;
  return Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || defaultTimeout;
}

function createFixture() {
  const tempDir = path.join(os.tmpdir(), 'aesop-stallfc-' + Math.random().toString(36).slice(2, 9));
  const fixtureRoot = path.join(tempDir, 'fixture');
  fs.mkdirSync(path.join(fixtureRoot, 'state'), { recursive: true });
  fs.mkdirSync(path.join(fixtureRoot, 'monitor'), { recursive: true });
  return {
    root: fixtureRoot,
    monitorDir: path.join(fixtureRoot, 'monitor'),
    tempDir,
    cleanup: () => {
      try {
        fs.rmSync(tempDir, { recursive: true, force: true });
      } catch (e) {
        // ignore cleanup errors
      }
    },
  };
}

function runCollector(fixture, envOverrides = {}) {
  const env = {
    ...process.env,
    AESOP_ROOT: fixture.root,
    BRAIN_ROOT: path.join(fixture.root, '..', '.claude'),
    SCRIPTS_ROOT: path.join(fixture.root, '..', 'scripts'),
    TEMP_ROOT: path.join(fixture.tempDir, 'temp'),
    ...envOverrides,
  };
  // process.execPath, not 'node': one test deliberately empties PATH so the
  // stall check cannot resolve `python`, and a bare 'node' would die with it.
  const result = spawnSync(process.execPath, [collectorPath], {
    env,
    encoding: 'utf8',
    timeout: getTestTimeout(),
    killSignal: 'SIGKILL',
  });
  if (result.error) {
    throw new Error(`Failed to spawn collector: ${result.error.message}`);
  }
  return result;
}

function readSignals(fixture) {
  const signalsPath = path.join(fixture.monitorDir, 'SIGNALS.json');
  assert.ok(fs.existsSync(signalsPath), 'SIGNALS.json should exist');
  return JSON.parse(fs.readFileSync(signalsPath, 'utf8'));
}

// === Static guard: the fail-open literals must never come back ===

test('fail-closed: no error path returns a zero stall count', () => {
  // The exact shape of the original bug: an error branch claiming zero stalls.
  const failOpen = /available:\s*false,\s*count:\s*0/;
  assert.ok(
    !failOpen.test(collectorSource),
    'checkAgentStalls must not return {available:false, count:0} on any error path — ' +
    'a check that did not run is UNKNOWN, not "no stalls"');
});

test('fail-closed: a degraded stall signal helper exists and yields count null', () => {
  assert.ok(
    /function degradedStallSignal\(/.test(collectorSource),
    'a single degradedStallSignal() helper should build every failure-path signal');
  const helper = collectorSource.slice(
    collectorSource.indexOf('function degradedStallSignal('),
    collectorSource.indexOf('function checkAgentStalls('));
  assert.match(helper, /state:\s*'UNKNOWN'/, 'degraded signal must carry state UNKNOWN');
  assert.match(helper, /degraded:\s*true/, 'degraded signal must set degraded:true');
  assert.match(helper, /count:\s*null/, 'degraded signal must set count:null, never 0');
});

test('fail-closed: every stall failure branch routes through the degraded helper', () => {
  const fn = collectorSource.slice(
    collectorSource.indexOf('function checkAgentStalls('),
    collectorSource.indexOf('// === AUTO Actions ==='));
  // Spawn error, kill signal, nonzero exit, non-JSON stdout, non-array stdout,
  // missing tool, and the outer catch: seven distinct fail-closed returns.
  const degradedReturns = (fn.match(/return degradedStallSignal\(/g) || []).length;
  assert.ok(degradedReturns >= 6,
    `expected every failure branch to return a degraded signal, found ${degradedReturns}`);
  assert.ok(!/catch \(e\) \{\s*\/\/[^\n]*\n\s*return \{/.test(fn),
    'the outer catch must not hand-roll a signal object');
});

test('fail-closed: BRIEF.md flags a degraded stall check instead of a quiet skip', () => {
  const briefBlock = collectorSource.slice(
    collectorSource.indexOf("brief.push('## Agent stalls')"),
    collectorSource.indexOf("brief.push('## Main CI status"));
  assert.ok(!/NOT-AVAILABLE/.test(briefBlock),
    'BRIEF.md must not report a broken stall check as a neutral NOT-AVAILABLE line');
  assert.match(briefBlock, /DEGRADED/,
    'BRIEF.md must mark an unrunnable stall check as DEGRADED/UNKNOWN');
});

// === Behavioral: run the real collector ===

test('collector emits the stall-signal contract fields', () => {
  const fixture = createFixture();
  try {
    runCollector(fixture);
    const signals = readSignals(fixture);
    const stalls = signals.agentStalls;
    assert.ok(stalls, 'agentStalls signal should exist');
    assert.ok('available' in stalls, 'agentStalls should report availability');
    assert.ok('state' in stalls, 'agentStalls should carry an explicit state');
    assert.ok('degraded' in stalls, 'agentStalls should carry an explicit degraded flag');
    assert.ok(['OK', 'STALLED', 'UNKNOWN'].includes(stalls.state),
      `unexpected stall state: ${stalls.state}`);
    // The core invariant: degraded and a numeric count are mutually exclusive.
    if (stalls.degraded) {
      assert.strictEqual(stalls.count, null,
        'a degraded stall check must never publish a numeric count');
      assert.strictEqual(stalls.state, 'UNKNOWN', 'degraded implies state UNKNOWN');
      assert.strictEqual(stalls.available, false, 'degraded implies available=false');
      assert.ok(stalls.reason, 'a degraded signal must name the reason it could not run');
    } else {
      assert.strictEqual(stalls.state === 'STALLED', stalls.count > 0,
        'state must agree with the count when the check actually ran');
    }
  } finally {
    fixture.cleanup();
  }
});

test('collector reports UNKNOWN (not zero stalls) when python cannot be spawned', () => {
  const fixture = createFixture();
  try {
    // Empty PATH: `python` cannot resolve, so the stall check cannot run.
    // Pre-fix this produced count:0 == "no stalls detected".
    const emptyPathDir = path.join(fixture.tempDir, 'nopath');
    fs.mkdirSync(emptyPathDir, { recursive: true });
    runCollector(fixture, { PATH: emptyPathDir, Path: emptyPathDir });

    const stalls = readSignals(fixture).agentStalls;
    assert.strictEqual(stalls.degraded, true,
      'an unspawnable stall check must be reported as degraded');
    assert.strictEqual(stalls.state, 'UNKNOWN', 'state must be UNKNOWN');
    assert.strictEqual(stalls.count, null, 'count must be null, never 0');
    assert.ok(stalls.reason && stalls.reason.length > 0, 'reason must be populated');

    const brief = fs.readFileSync(path.join(fixture.monitorDir, 'BRIEF.md'), 'utf8');
    assert.ok(brief.includes('## Agent stalls'), 'BRIEF.md should keep the section');
    assert.ok(brief.includes('DEGRADED'),
      'BRIEF.md must shout that stall detection is degraded');
    assert.ok(!brief.includes('No agent stalls detected'),
      'BRIEF.md must not claim a clean bill of health from a check that never ran');
  } finally {
    fixture.cleanup();
  }
});
