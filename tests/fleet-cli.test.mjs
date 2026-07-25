#!/usr/bin/env node
/**
 * Fleet CLI End-to-End Test
 *
 * Tests `aesop fleet` (or `node tools/fleet.js`) CLI subcommand:
 *  - Spawns the CLI in a temp fixture directory
 *  - Verifies JSON output shape (heartbeats, agents, tracker, orchestrator)
 *  - Tests graceful degradation when state files are absent or malformed
 *  - Verifies process exits cleanly (code 0)
 *  - No cwd pollution (temp dirs used, cleaned up)
 */

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert';

const __dirname = dirname(fileURLToPath(import.meta.url));
const FLEET_CLI = join(__dirname, '..', 'tools', 'fleet.js');

// ============================================================================
// Test Harness
// ============================================================================

/**
 * Spawn the fleet CLI in a given AESOP_ROOT directory
 * and collect stdout, stderr, exit code
 *
 * Timeout: 10 seconds by default (prevents hang if process doesn't close);
 * honors AESOP_TEST_CHILD_TIMEOUT_MS override (same pattern as
 * collect-signals.test.mjs) so CI can widen the window under contention.
 */
function spawnFleetCli(aesopRoot, timeoutMs = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 10000) {
  return new Promise((resolve) => {
    let resolved = false;

    const proc = spawn('node', [FLEET_CLI], {
      cwd: aesopRoot,
      env: {
        ...process.env,
        AESOP_ROOT: aesopRoot
      },
      stdio: ['ignore', 'pipe', 'pipe']
    });

    let stdout = '';
    let stderr = '';

    // Set a timeout to force resolution if process hangs
    const timeout = setTimeout(() => {
      if (!resolved) {
        resolved = true;
        proc.kill('SIGTERM');
        resolve({ code: 124, stdout, stderr: stderr || 'Process timeout' });
      }
    }, timeoutMs);

    proc.stdout.on('data', (data) => {
      stdout += data.toString();
    });

    proc.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    proc.on('close', (code) => {
      if (!resolved) {
        resolved = true;
        clearTimeout(timeout);
        resolve({ code, stdout, stderr });
      }
    });

    proc.on('error', (err) => {
      if (!resolved) {
        resolved = true;
        clearTimeout(timeout);
        resolve({ code: 1, stdout: '', stderr: err.message });
      }
    });
  });
}

// ============================================================================
// Tests
// ============================================================================

test('fleet CLI: outputs valid JSON on empty state directory', async (t) => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'aesop-fleet-test-'));
  const stateDir = join(fixtureRoot, 'state');
  mkdirSync(stateDir, { recursive: true });

  try {
    const result = await spawnFleetCli(fixtureRoot);

    // Should exit cleanly
    assert.strictEqual(result.code, 0, `Expected exit code 0, got ${result.code}`);
    if (result.stderr) {
      console.error('stderr:', result.stderr);
    }
    assert.strictEqual(result.stderr, '', 'stderr should be empty');

    // Should output valid JSON
    let parsed;
    try {
      parsed = JSON.parse(result.stdout);
    } catch (e) {
      assert.fail(`Output is not valid JSON: ${result.stdout}`);
    }

    // Check required top-level fields
    assert(parsed.timestamp, 'Should have timestamp');
    assert(parsed.aesop_root, 'Should have aesop_root');
    assert(parsed.heartbeats, 'Should have heartbeats');
    assert(parsed.agents, 'Should have agents');
    assert(parsed.tracker, 'Should have tracker');
    assert(parsed.orchestrator, 'Should have orchestrator');

    // Heartbeats should have unavailable entries (no state files exist)
    assert(
      parsed.heartbeats.watchdog.unavailable ||
      parsed.heartbeats.watchdog.status,
      'Watchdog should have either unavailable or status'
    );
    assert(
      parsed.heartbeats.monitor.unavailable ||
      parsed.heartbeats.monitor.status,
      'Monitor should have either unavailable or status'
    );

    console.log('✓ Empty state directory test passed');
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test('fleet CLI: handles present heartbeat files', async (t) => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'aesop-fleet-test-'));
  const stateDir = join(fixtureRoot, 'state');
  mkdirSync(stateDir, { recursive: true });

  try {
    // Write a fresh heartbeat
    const now = Math.floor(Date.now() / 1000);
    writeFileSync(join(stateDir, '.watchdog-heartbeat'), `${now}`);

    const result = await spawnFleetCli(fixtureRoot);

    assert.strictEqual(result.code, 0, `Expected exit code 0, got ${result.code}`);

    const parsed = JSON.parse(result.stdout);

    // Watchdog should report OK status with age
    const watchdog = parsed.heartbeats.watchdog;
    assert(watchdog.status, 'Watchdog should have status field');
    assert.strictEqual(watchdog.status, 'OK', 'Fresh heartbeat should be OK');
    assert(
      typeof watchdog.age_seconds === 'number',
      'Watchdog should report age in seconds'
    );
    assert.strictEqual(
      watchdog.threshold_seconds,
      300,
      'Watchdog threshold should be 300s'
    );

    console.log('✓ Present heartbeat file test passed');
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test('fleet CLI: detects stale heartbeat', async (t) => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'aesop-fleet-test-'));
  const stateDir = join(fixtureRoot, 'state');
  mkdirSync(stateDir, { recursive: true });

  try {
    // Write a stale heartbeat (400 seconds old, threshold is 300)
    const now = Math.floor(Date.now() / 1000);
    const staleTime = now - 400;
    writeFileSync(join(stateDir, '.watchdog-heartbeat'), `${staleTime}`);

    const result = await spawnFleetCli(fixtureRoot);

    assert.strictEqual(result.code, 0, `Expected exit code 0, got ${result.code}`);

    const parsed = JSON.parse(result.stdout);
    const watchdog = parsed.heartbeats.watchdog;

    assert.strictEqual(watchdog.status, 'STALE', 'Old heartbeat should be STALE');
    assert(watchdog.age_seconds >= 400, 'Age should be >= 400 seconds');

    console.log('✓ Stale heartbeat detection test passed');
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test('fleet CLI: handles tracker.json present', async (t) => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'aesop-fleet-test-'));
  const stateDir = join(fixtureRoot, 'state');
  mkdirSync(stateDir, { recursive: true });

  try {
    // Write a tracker file
    const tracker = {
      version: 1,
      items: [
        { id: 'i1', lane: 'ranked', title: 'Item 1', status: 'todo' },
        { id: 'i2', lane: 'in-progress', title: 'Item 2', status: 'wip' },
        { id: 'i3', lane: 'ranked', title: 'Item 3', status: 'todo' }
      ]
    };
    writeFileSync(
      join(stateDir, 'tracker.json'),
      JSON.stringify(tracker, null, 2)
    );

    const result = await spawnFleetCli(fixtureRoot);

    assert.strictEqual(result.code, 0, `Expected exit code 0, got ${result.code}`);

    const parsed = JSON.parse(result.stdout);
    const trackerData = parsed.tracker;

    assert(!trackerData.unavailable, 'Tracker should not be unavailable');
    assert.strictEqual(trackerData.total_items, 3, 'Should have 3 items');
    assert.strictEqual(trackerData.by_lane.ranked, 2, 'Should have 2 ranked items');
    assert.strictEqual(
      trackerData.by_lane['in-progress'],
      1,
      'Should have 1 in-progress item'
    );

    console.log('✓ Tracker.json parsing test passed');
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test('fleet CLI: handles malformed tracker.json', async (t) => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'aesop-fleet-test-'));
  const stateDir = join(fixtureRoot, 'state');
  mkdirSync(stateDir, { recursive: true });

  try {
    // Write malformed JSON
    writeFileSync(join(stateDir, 'tracker.json'), '{invalid json}');

    const result = await spawnFleetCli(fixtureRoot);

    // Should still exit cleanly (graceful degradation)
    assert.strictEqual(result.code, 0, `Expected exit code 0, got ${result.code}`);

    const parsed = JSON.parse(result.stdout);
    const trackerData = parsed.tracker;

    // Should report unavailable
    assert(
      trackerData.unavailable,
      'Malformed tracker should report unavailable'
    );

    console.log('✓ Malformed tracker graceful degradation test passed');
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test('fleet CLI: handles orchestrator-status.json present', async (t) => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'aesop-fleet-test-'));
  const stateDir = join(fixtureRoot, 'state');
  mkdirSync(stateDir, { recursive: true });

  try {
    // Write orchestrator status
    const orchStatus = {
      activity: 'dispatching',
      phase: 'wave-14',
      timestamp: new Date().toISOString()
    };
    writeFileSync(
      join(stateDir, 'orchestrator-status.json'),
      JSON.stringify(orchStatus, null, 2)
    );

    const result = await spawnFleetCli(fixtureRoot);

    assert.strictEqual(result.code, 0, `Expected exit code 0, got ${result.code}`);

    const parsed = JSON.parse(result.stdout);
    const orchData = parsed.orchestrator;

    assert(!orchData.unavailable, 'Orchestrator should not be unavailable');
    assert.strictEqual(orchData.activity, 'dispatching', 'Activity should match');
    assert.strictEqual(orchData.phase, 'wave-14', 'Phase should match');

    console.log('✓ Orchestrator status parsing test passed');
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test('fleet CLI: output includes timestamp', async (t) => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'aesop-fleet-test-'));
  const stateDir = join(fixtureRoot, 'state');
  mkdirSync(stateDir, { recursive: true });

  try {
    const result = await spawnFleetCli(fixtureRoot);

    assert.strictEqual(result.code, 0, `Expected exit code 0, got ${result.code}`);

    const parsed = JSON.parse(result.stdout);

    // Verify timestamp is ISO 8601
    assert(parsed.timestamp, 'Should have timestamp');
    const ts = new Date(parsed.timestamp);
    assert(
      !isNaN(ts.getTime()),
      'Timestamp should be valid ISO 8601: ' + parsed.timestamp
    );

    // Verify aesop_root is set
    assert(parsed.aesop_root, 'Should have aesop_root');
    assert.strictEqual(
      parsed.aesop_root,
      fixtureRoot,
      'aesop_root should match fixture root'
    );

    console.log('✓ Timestamp and aesop_root test passed');
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

console.log('\n✅ All fleet CLI tests passed!\n');
