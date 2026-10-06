// Tests for the CI-modes product surface in bin/cli.js + tools/doctor.js:
//   - `--help` advertises `init --ci-mode`, `doctor --json`, `runner install|remove`
//   - `doctor --json` carries a report-only `ci_capability` section (never counted as a check)
//   - `runner install --dry-run` is refused on a Smart-App-Control box (injected probe fixture)
//   - `init --ci-mode` is forwarded to tools/init_project.py; a bad mode exits 2
//
// Run: node --test tests/ci-modes.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLI = path.join(repoRoot, 'bin', 'cli.js');
const TIMEOUT = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 60000;

const BLOCKED_PROBES = {
  os: { system: 'Windows', release: '11', version: '10.0.26200' },
  cpu_cores: 32,
  ram_gb: 63.6,
  windows_code_integrity: { smart_app_control_state: 1, umci_enforcement_status: 2 },
  wsl: { present: true },
  docker: { present: true },
  gh: { present: true, authenticated: true },
  cloudflared: { present: false },
  receipt_tools: { emit_receipt: false, verify_receipt: false, verify_workflow: false }
};

function withFixture(probes, fn) {
  const td = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-cimodes-'));
  try {
    const fx = path.join(td, 'probes.json');
    fs.writeFileSync(fx, JSON.stringify(probes));
    return fn(td, { ...process.env, AESOP_CI_PROBE_FIXTURE: fx });
  } finally {
    fs.rmSync(td, { recursive: true, force: true });
  }
}

function runCli(args, cwd, env) {
  return spawnSync(process.execPath, [CLI, ...args], {
    cwd, env: env || process.env, encoding: 'utf8', timeout: TIMEOUT, killSignal: 'SIGKILL',
    stdio: ['pipe', 'pipe', 'pipe']
  });
}

test('--help advertises the CI-modes surface', () => {
  const res = runCli(['--help'], repoRoot);
  assert.equal(res.status, 0, res.stderr);
  for (const needle of ['--ci-mode', 'runner install', 'runner remove', 'doctor [--json]', '--instances', '--labels', '--dry-run', '--set-fork-policy']) {
    assert.ok(res.stdout.includes(needle), `--help missing ${needle}`);
  }
});

test('doctor --json reports ci_capability without counting it as a check', () => {
  withFixture(BLOCKED_PROBES, (td, env) => {
    const res = runCli(['doctor', '--json'], td, env);
    let data;
    try { data = JSON.parse(res.stdout); } catch (e) { assert.fail(`doctor --json is not JSON: ${res.stdout.slice(0, 300)} ${res.stderr.slice(0, 300)}`); }
    assert.ok(Array.isArray(data.checks), 'checks array');
    assert.equal(data.summary.total, data.checks.length, 'summary counts only the pass/fail checks');
    const modes = data.ci_capability.modes.map((m) => m.mode).sort();
    assert.deepEqual(modes, ['hosted', 'local-receipt-gate', 'self-hosted-runner']);
    const runner = data.ci_capability.modes.find((m) => m.mode === 'self-hosted-runner');
    assert.equal(runner.runnable, false);
    assert.match(runner.why, /Smart App Control/);
    assert.equal(data.ci_capability.windows.smart_app_control, 'enforced');
    // report-only: no mode row carries a pass/fail verdict
    for (const m of data.ci_capability.modes) assert.ok(!('passed' in m));
  });
});

test('doctor text output renders the mode -> runnable table', () => {
  withFixture(BLOCKED_PROBES, (td, env) => {
    const res = runCli(['doctor'], td, env);
    assert.match(res.stdout, /CI capability/);
    assert.match(res.stdout, /runnable here/);
    assert.match(res.stdout, /self-hosted-runner\s+no/);
    assert.match(res.stdout, /hosted\s+yes/);
  });
});

test('runner install --dry-run is refused on a Smart App Control box', () => {
  withFixture(BLOCKED_PROBES, (td, env) => {
    const res = runCli(['runner', 'install', '--dry-run', '--repo', 'acme/widgets'], td, env);
    assert.equal(res.status, 1, res.stdout + res.stderr);
    assert.match(res.stdout + res.stderr, /REFUSED/);
    assert.match(res.stdout + res.stderr, /Smart App Control/);
  });
});

test('runner with no subcommand exits 2 with usage', () => {
  const res = runCli(['runner'], repoRoot);
  assert.equal(res.status, 2);
  assert.match(res.stdout + res.stderr, /install|remove/);
});

test('init --ci-mode is forwarded; an unknown mode exits 2 and writes nothing', () => {
  const td = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-init-cimode-'));
  try {
    spawnSync('git', ['init', '-q'], { cwd: td, encoding: 'utf8', timeout: TIMEOUT });
    const bad = runCli(['init', '--ci-mode', 'mainframe'], td);
    assert.equal(bad.status, 2, bad.stdout + bad.stderr);
    assert.ok(!fs.existsSync(path.join(td, 'aesop.config.json')), 'nothing scaffolded on a bad mode');
    const good = runCli(['init', '--ci-mode', 'self-hosted-runner'], td);
    assert.equal(good.status, 0, good.stdout + good.stderr);
    const cfg = JSON.parse(fs.readFileSync(path.join(td, 'aesop.config.json'), 'utf8'));
    assert.deepEqual(cfg.ci.mode, ['self-hosted-runner']);
    assert.ok(fs.readFileSync(path.join(td, '.github', 'workflows', 'ci.yml'), 'utf8').includes('self-hosted'));
  } finally {
    fs.rmSync(td, { recursive: true, force: true });
  }
});
