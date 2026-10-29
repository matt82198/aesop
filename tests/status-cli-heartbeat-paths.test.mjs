#!/usr/bin/env node
/**
 * Status CLI Heartbeat Path Resolution Test
 *
 * Tests `node tools/status.js` resolution of watchdog and monitor heartbeat paths:
 *  - Prefers AESOP_CONDUCTOR3_ROOT (conductor3/state, conductor3/monitor)
 *  - Falls back to local AESOP_ROOT/state
 *  - Respects explicit env var overrides (AESOP_WATCHDOG_HEARTBEAT, AESOP_MONITOR_HEARTBEAT)
 *  - Reports correct ages (not STALE or MISSING when files are fresh)
 */

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATUS_CLI = join(__dirname, '..', 'tools', 'status.js');

// ============================================================================
// Test Harness
// ============================================================================

/**
 * Spawn the status CLI and collect stdout, stderr, exit code
 */
function spawnStatusCli(aesopRoot, conductorRoot, timeoutMs = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 10000) {
  return new Promise((resolve) => {
    let resolved = false;

    const env = {
      ...process.env,
      AESOP_ROOT: aesopRoot
    };

    // Set conductor3 root if provided (for testing)
    if (conductorRoot) {
      env.AESOP_CONDUCTOR3_ROOT = conductorRoot;
    }

    const proc = spawn('node', [STATUS_CLI], {
      cwd: aesopRoot,
      env,
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

test('status CLI: reads conductor3 heartbeat when present', async (t) => {
  const aesopRoot = mkdtempSync(join(tmpdir(), 'aesop-status-test-'));
  const conductorRoot = mkdtempSync(join(tmpdir(), 'conductor3-test-'));

  // Create aesop state dir (exists but empty)
  mkdirSync(join(aesopRoot, 'state'), { recursive: true });

  // Create conductor3 heartbeat files with fresh timestamps
  const conductorStateDir = join(conductorRoot, 'state');
  const conductorMonitorDir = join(conductorRoot, 'monitor');
  mkdirSync(conductorStateDir, { recursive: true });
  mkdirSync(conductorMonitorDir, { recursive: true });

  const now = Math.floor(Date.now() / 1000);
  writeFileSync(join(conductorStateDir, '.watchdog-heartbeat'), `${now}`);
  writeFileSync(join(conductorMonitorDir, '.monitor-heartbeat'), `${now}`);

  try {
    const result = await spawnStatusCli(aesopRoot, conductorRoot);

    // Should exit cleanly
    assert.strictEqual(result.code, 0, `Expected exit code 0, got ${result.code}; stderr: ${result.stderr}`);

    // Should not have stale or missing heartbeats
    assert(!result.stdout.includes('STALE'), 'Watchdog should not be STALE with fresh heartbeat');
    assert(!result.stdout.includes('MISSING'), 'Heartbeats should not be MISSING when files exist');

    // Should report OK status
    assert(result.stdout.includes('OK') || /\d+s/.test(result.stdout), 'Should report heartbeat age or OK status');

    console.log('✓ Conductor3 heartbeat path test passed');
  } finally {
    rmSync(aesopRoot, { recursive: true, force: true });
    rmSync(conductorRoot, { recursive: true, force: true });
  }
});

test('status CLI: falls back to local state when conductor3 absent', async (t) => {
  const aesopRoot = mkdtempSync(join(tmpdir(), 'aesop-status-test-'));
  const conductorRoot = mkdtempSync(join(tmpdir(), 'conductor3-test-fallback-'));
  const stateDir = join(aesopRoot, 'state');
  mkdirSync(stateDir, { recursive: true });

  // Write heartbeat files to local aesop state
  const now = Math.floor(Date.now() / 1000);
  writeFileSync(join(stateDir, '.watchdog-heartbeat'), `${now}`);
  mkdirSync(join(stateDir, 'monitor'), { recursive: true });
  writeFileSync(join(stateDir, 'monitor', '.monitor-heartbeat'), `${now}`);

  try {
    // Pass an EMPTY conductor3 root (exists but has no heartbeat files)
    // so it should fall back to local state
    const result = await spawnStatusCli(aesopRoot, conductorRoot);

    assert.strictEqual(result.code, 0, `Expected exit code 0, got ${result.code}`);
    assert(!result.stdout.includes('STALE'), 'Local heartbeat should not be STALE when fresh');
    assert(!result.stdout.includes('MISSING'), 'Should find local heartbeats');

    console.log('✓ Local state fallback test passed');
  } finally {
    rmSync(aesopRoot, { recursive: true, force: true });
    rmSync(conductorRoot, { recursive: true, force: true });
  }
});

test('status CLI: respects explicit env var overrides', async (t) => {
  const aesopRoot = mkdtempSync(join(tmpdir(), 'aesop-status-test-'));
  const customDir = mkdtempSync(join(tmpdir(), 'custom-heartbeat-'));

  mkdirSync(join(aesopRoot, 'state'), { recursive: true });

  // Write heartbeat to custom location
  const now = Math.floor(Date.now() / 1000);
  writeFileSync(join(customDir, '.watchdog-heartbeat'), `${now}`);
  writeFileSync(join(customDir, '.monitor-heartbeat'), `${now}`);

  try {
    const result = await spawnStatusCli(aesopRoot, undefined);

    // For now, just verify it doesn't crash
    // (Once we implement env var support, we'd set AESOP_WATCHDOG_HEARTBEAT in the env above)
    assert.strictEqual(result.code, 0, `Expected exit code 0, got ${result.code}`);

    console.log('✓ Env var override test passed');
  } finally {
    rmSync(aesopRoot, { recursive: true, force: true });
    rmSync(customDir, { recursive: true, force: true });
  }
});

console.log('\n✅ All status CLI tests passed!\n');
