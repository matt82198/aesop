#!/usr/bin/env node
/**
 * MCP fleet_status watchdog heartbeat reader.
 *
 * Regression: mcp/server.mjs read the watchdog beat from STATE_ROOT, but the only writer
 * (daemons/backup-fleet.sh) writes CONDUCTOR_ROOT/state/.watchdog-heartbeat, and
 * monitor/collect-signals.mjs resolveWatchdogHeartbeat reads the same place.
 *
 * Drives the real server over stdio (tools/call fleet_status) and asserts the output.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';

const SERVER = join(dirname(fileURLToPath(import.meta.url)), '..', 'mcp', 'server.mjs');

const nowSec = () => Math.floor(Date.now() / 1000);

function writeBeat(dir, epochSec) {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, '.watchdog-heartbeat'), `${epochSec}`);
}

/**
 * Spawn the server with the given roots, call fleet_status once, return parsed output.
 * CONDUCTOR_ROOT is removed from the inherited env unless passed explicitly.
 */
async function fleetStatus({ aesopRoot, stateRoot, conductorRoot }) {
  const env = { ...process.env, AESOP_ROOT: aesopRoot, AESOP_STATE_ROOT: stateRoot };
  delete env.CONDUCTOR_ROOT;
  if (conductorRoot) env.CONDUCTOR_ROOT = conductorRoot;

  const child = spawn(process.execPath, [SERVER, '--root', aesopRoot], {
    env,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const rl = createInterface({ input: child.stdout });

  const response = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('no tools/call response within 5s')), 5000);
    rl.on('line', (line) => {
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        return;
      }
      if (msg.id === 1) {
        clearTimeout(timer);
        resolve(msg);
      }
    });
  });

  child.stdin.write(JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    method: 'tools/call',
    params: { name: 'fleet_status', arguments: {} },
  }) + '\n');

  try {
    const msg = await response;
    assert.ok(msg.result, `no result in response: ${JSON.stringify(msg)}`);
    return JSON.parse(msg.result.content[0].text);
  } finally {
    rl.close();
    child.kill();
  }
}

function makeFixture() {
  const base = mkdtempSync(join(tmpdir(), 'hb-reader-'));
  return {
    base,
    stateRoot: join(base, 'state'),
    conductorRoot: join(base, 'conductor3'),
    aesopRoot: join(base, 'aesop'),
  };
}

test('control: fresh beat under CONDUCTOR_ROOT/state is reported ALIVE', async () => {
  const f = makeFixture();
  try {
    writeBeat(join(f.conductorRoot, 'state'), nowSec());
    const status = await fleetStatus(f);
    assert.equal(status.watchdog.alive, 'ALIVE');
  } finally {
    rmSync(f.base, { recursive: true, force: true });
  }
});

test('stale beat under CONDUCTOR_ROOT is STALE even when STATE_ROOT holds a fresh beat', async () => {
  const f = makeFixture();
  try {
    writeBeat(f.stateRoot, nowSec());
    writeBeat(join(f.conductorRoot, 'state'), nowSec() - 3600);
    const status = await fleetStatus(f);
    assert.equal(status.watchdog.alive, 'STALE');
  } finally {
    rmSync(f.base, { recursive: true, force: true });
  }
});

test('no beat under CONDUCTOR_ROOT is MISSING, never ALIVE from a STATE_ROOT beat', async () => {
  const f = makeFixture();
  try {
    writeBeat(f.stateRoot, nowSec());
    mkdirSync(join(f.conductorRoot, 'state'), { recursive: true });
    const status = await fleetStatus(f);
    assert.equal(status.watchdog.alive, 'MISSING');
  } finally {
    rmSync(f.base, { recursive: true, force: true });
  }
});

test('unresolvable watchdog root is UNKNOWN, never ALIVE from a STATE_ROOT beat', async () => {
  const f = makeFixture();
  try {
    writeBeat(f.stateRoot, nowSec());
    // No CONDUCTOR_ROOT, and basename(AESOP_ROOT) is not 'aesop', so the root cannot be resolved.
    const status = await fleetStatus({ aesopRoot: join(f.base, 'fixture-root'), stateRoot: f.stateRoot });
    assert.equal(status.watchdog.alive, 'UNKNOWN');
  } finally {
    rmSync(f.base, { recursive: true, force: true });
  }
});
