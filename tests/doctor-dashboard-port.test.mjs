// Doctor port check honours the configured dashboard port. Defect: tools/doctor.js probed a
// hardcoded 8770 while `aesop dash` launches on aesop.config.json dashboard.port, so doctor
// reported a false FAIL when 8770 was busy and never checked the port the user configured.
//
// Run: node --test tests/doctor-dashboard-port.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const DOCTOR = path.join(REPO_ROOT, 'tools', 'doctor.js');

function makeTempDir(prefix) {
  return fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
}

function listen(port = 0) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}

function portOf(server) {
  return server.address().port;
}

function closeServer(server) {
  return new Promise((resolve) => server.close(() => resolve()));
}

// A loopback port that is free at the moment of the call.
async function freePort() {
  const server = await listen(0);
  try {
    return portOf(server);
  } finally {
    await closeServer(server);
  }
}

function makeProject(config) {
  const root = makeTempDir('aesop-doctor-port-');
  fs.writeFileSync(path.join(root, 'aesop.config.json'), JSON.stringify(config, null, 2));
  return root;
}

function runDoctorJson(cwd) {
  const home = makeTempDir('aesop-doctor-port-home-');
  const env = { ...process.env, HOME: home, USERPROFILE: home };
  delete env.PORT;
  const res = spawnSync(process.execPath, [DOCTOR, '--json'], {
    cwd,
    encoding: 'utf8',
    env,
    timeout: 60000,
    killSignal: 'SIGKILL'
  });
  try {
    return JSON.parse(res.stdout);
  } catch (e) {
    assert.fail(`doctor --json must emit JSON. stdout: ${res.stdout} stderr: ${res.stderr}`);
  }
}

function portRow(report) {
  const row = report.checks.find(c => /^Port \d+ available$/.test(c.label));
  assert.ok(row, `doctor must report a port row. labels: ${report.checks.map(c => c.label).join(' | ')}`);
  return row;
}

test('doctor port row names the configured dashboard port and PASSes when that port is free', async () => {
  const port = await freePort();
  const root = makeProject({ description: 'port', repos: [], layout: 'init', dashboard: { port } });
  const row = portRow(runDoctorJson(root));
  assert.equal(row.label, `Port ${port} available`, `label must name the configured port ${port}`);
  assert.equal(row.passed, true, `configured port ${port} is free; hint: ${row.hint}`);
});

test('doctor port row FAILs naming the configured dashboard port when that port is busy', async () => {
  const server = await listen(0);
  try {
    const port = portOf(server);
    const root = makeProject({ description: 'port', repos: [], layout: 'init', dashboard: { port } });
    const row = portRow(runDoctorJson(root));
    assert.equal(row.label, `Port ${port} available`, `label must name the configured port ${port}`);
    assert.equal(row.passed, false, `configured port ${port} is held open by the test`);
    assert.ok(row.hint.includes(String(port)), `hint must name port ${port}: ${row.hint}`);
  } finally {
    await closeServer(server);
  }
});

test('doctor port row defaults to 8770 when aesop.config.json has no dashboard.port', () => {
  const root = makeProject({ description: 'port', repos: [], layout: 'init' });
  const row = portRow(runDoctorJson(root));
  assert.equal(row.label, 'Port 8770 available', 'absent dashboard.port must fall back to 8770');
});
