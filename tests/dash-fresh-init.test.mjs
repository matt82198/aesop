// F-09: `aesop dash` (step 4 of init's own Getting Started) must launch the
// dashboard from inside a freshly initialized repo. init does not scaffold ui/,
// so tools/dash.js must resolve ui/serve.py from the installed package, not from
// process.cwd().
//
// Contract: in a fresh `git init` repo, after `aesop init`, `aesop dash` must
// not report "dashboard script not found", and the served dashboard must answer
// HTTP on the chosen port.
//
// Run: node --test tests/dash-fresh-init.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gitCmd } from './helpers/git-helpers.mjs';

const CLI = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'bin', 'cli.js');

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function killTree(child) {
  if (!child.pid) return;
  if (process.platform === 'win32') {
    // Kill only the process tree this test spawned (pid from our own child).
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
  } else {
    // The child is spawned detached (its own process group), so SIGKILL to the group
    // reaches the dashboard server that `aesop dash` forks. Killing only the direct
    // child left that server alive and writing into the repo while the finally-block
    // rmSync ran (CI ENOTEMPTY: rmdir /tmp/shard-2/aesop-dash-fresh-*).
    try { process.kill(-child.pid, 'SIGKILL'); } catch { /* group already gone */ }
    try { child.kill('SIGKILL'); } catch { /* already gone */ }
  }
}

// Resolves once the direct child has exited, or after a bounded wait.
function waitForExit(child, ms) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
  });
}

async function waitForHttp(port, timeoutMs, stillAlive) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline && stillAlive()) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(1000) });
      if (res.status > 0) return res.status;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 300));
  }
  return null;
}

test('aesop dash launches the dashboard from a freshly init-ed repo', async () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-dash-fresh-'));
  gitCmd(repo, ['init', '-q']);
  const init = spawnSync(process.execPath, [CLI, 'init', '--name', 'demo', '--no-skills'], {
    cwd: repo,
    encoding: 'utf8',
    timeout: 120000,
  });
  assert.equal(init.status, 0, `init failed: ${init.stderr}`);

  const port = await freePort();
  const child = spawn(process.execPath, [CLI, 'dash'], {
    cwd: repo,
    env: { ...process.env, PORT: String(port) },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    // Own process group on POSIX so killTree can reach the forked dashboard server.
    detached: process.platform !== 'win32',
  });
  let stderr = '';
  let exitCode = null;
  child.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
  child.stdout.on('data', () => {});
  child.on('exit', (code) => { exitCode = code; });

  try {
    const status = await waitForHttp(port, 60000, () => exitCode === null);
    assert.ok(
      !stderr.includes('dashboard script not found'),
      `aesop dash in a fresh init repo failed: ${stderr.trim()}`
    );
    assert.notEqual(exitCode, 1, `aesop dash exited 1: ${stderr.trim()}`);
    assert.notEqual(status, null, `dashboard never answered on port ${port}. stderr: ${stderr.trim()}`);
  } finally {
    killTree(child);
    await waitForExit(child, 5000);
    fs.rmSync(repo, { recursive: true, force: true });
  }
});
