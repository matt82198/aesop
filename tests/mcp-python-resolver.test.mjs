#!/usr/bin/env node
/**
 * MCP python resolver test (e393287616ff).
 *
 * The instance/claims helper must not depend on a literal `python3` binary.
 *   A. Only a `python` shim on PATH: fleet_instances / fleet_claims /
 *      fleet_multibox_summary must resolve it and report the registered instance.
 *   B. AESOP_PYTHON override with no interpreter on PATH: the override is used.
 *   C. No interpreter anywhere: each tool returns an explicit `error` field
 *      (never absent:true, never a silent empty fleet).
 * Uses a temp AESOP_STATE_ROOT and scratch PATH dirs; never touches the real state dir.
 */

import { spawn, spawnSync } from 'node:child_process';
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { delimiter, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const PY = process.env.PYTHON || 'python';
const IS_WIN = process.platform === 'win32';
const SYS_DIRS = IS_WIN && process.env.SystemRoot ? [join(process.env.SystemRoot, 'System32')] : [];

function fail(msg) {
  console.error(`FAIL: ${msg}`);
  process.exit(1);
}

function runPy(args, env) {
  const r = spawnSync(PY, ['-I', join(REPO_ROOT, 'tools', 'instance_manager.py'), ...args], {
    env,
    encoding: 'utf-8',
    errors: 'replace',
    cwd: REPO_ROOT,
    timeout: 60000,
  });
  if (r.error || r.status !== 0) fail(`instance_manager ${args.join(' ')} exited ${r.status}: ${r.stderr}`);
}

function realInterpreter(env) {
  const r = spawnSync(PY, ['-c', 'import sys; print(sys.executable)'], {
    env,
    encoding: 'utf-8',
    errors: 'replace',
    timeout: 30000,
  });
  if (r.error || r.status !== 0 || !r.stdout.trim()) fail(`cannot locate real interpreter via ${PY}: ${r.stderr}`);
  return r.stdout.trim();
}

// Build an env whose PATH is exactly `pathValue` (Windows keys are case-insensitive: drop any Path/PATH).
function envWithPath(base, pathValue) {
  const env = {};
  for (const [k, v] of Object.entries(base)) {
    if (v !== undefined && k.toUpperCase() !== 'PATH') env[k] = v;
  }
  env.PATH = pathValue;
  return env;
}

// A `python` executable that only forwards to the real interpreter by absolute path.
function writeShim(dir, real) {
  if (IS_WIN) {
    writeFileSync(join(dir, 'python.cmd'), `@echo off\r\n"${real}" %*\r\n`);
  } else {
    const p = join(dir, 'python');
    writeFileSync(p, `#!/bin/sh\nexec "${real}" "$@"\n`);
    chmodSync(p, 0o755);
  }
}

function startServer(env) {
  const proc = spawn(process.execPath, [join(REPO_ROOT, 'mcp', 'server.mjs'), '--root', REPO_ROOT], {
    env: { ...env, AESOP_ROOT: REPO_ROOT },
  });
  const pending = new Map();
  let nextId = 1;
  const rl = createInterface({ input: proc.stdout });
  rl.on('line', (line) => {
    let msg;
    try { msg = JSON.parse(line); } catch { return; }
    if (msg.id !== undefined && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  });
  const request = (method, params) => new Promise((ok, bad) => {
    const id = nextId++;
    const timer = setTimeout(() => bad(new Error(`timeout waiting for ${method}`)), 20000);
    pending.set(id, (msg) => { clearTimeout(timer); ok(msg); });
    proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
  return { proc, request };
}

async function callTool(server, name) {
  const resp = await server.request('tools/call', { name, arguments: {} });
  const text = resp.result && resp.result.content && resp.result.content[0] && resp.result.content[0].text;
  if (!text) fail(`${name}: no tool content: ${JSON.stringify(resp)}`);
  return JSON.parse(text);
}

async function expectWorking(label, env, servers) {
  const server = startServer(env);
  servers.push(server);
  await server.request('initialize', {
    protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 't', version: '0' },
  });

  const inst = await callTool(server, 'fleet_instances');
  if (inst.absent === true) fail(`[${label}] fleet_instances absent:true reason=${inst.reason}`);
  if (inst.error) fail(`[${label}] fleet_instances error: ${inst.error}`);
  const ids = (inst.instances || []).map((i) => i.instance_id || i.id);
  if (!ids.includes('inst-red-1')) fail(`[${label}] fleet_instances did not list inst-red-1: ${JSON.stringify(inst)}`);

  const claims = await callTool(server, 'fleet_claims');
  if (claims.absent === true) fail(`[${label}] fleet_claims absent:true reason=${claims.reason}`);
  if (claims.error) fail(`[${label}] fleet_claims error: ${claims.error}`);
  if (!JSON.stringify(claims.by_instance || {}).includes('src/red_file.py')) {
    fail(`[${label}] fleet_claims missing src/red_file.py: ${JSON.stringify(claims)}`);
  }

  const mb = await callTool(server, 'fleet_multibox_summary');
  if (mb.absent === true) fail(`[${label}] fleet_multibox_summary absent:true reason=${mb.reason}`);
  if (mb.error) fail(`[${label}] fleet_multibox_summary error: ${mb.error}`);
  if (!(mb.instance_count >= 1)) fail(`[${label}] fleet_multibox_summary instance_count=${mb.instance_count}`);
  console.log(`PASS: [${label}] helper resolved and ran for fleet_instances, fleet_claims, fleet_multibox_summary`);
}

async function expectNoInterpreter(label, env, servers) {
  const server = startServer(env);
  servers.push(server);
  await server.request('initialize', {
    protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 't', version: '0' },
  });

  for (const name of ['fleet_instances', 'fleet_claims', 'fleet_multibox_summary']) {
    const r = await callTool(server, name);
    if (r.absent === true) fail(`[${label}] ${name} reported absent:true instead of an explicit error: ${JSON.stringify(r)}`);
    if (typeof r.error !== 'string' || r.error.length === 0) {
      fail(`[${label}] ${name} has no explicit error field: ${JSON.stringify(r)}`);
    }
    if (Array.isArray(r.instances) && r.instances.length !== 0) fail(`[${label}] ${name} non-empty instances on error`);
    if (r.by_instance && Object.keys(r.by_instance).length !== 0) fail(`[${label}] ${name} non-empty by_instance on error`);
    if (r.instance_count !== undefined && r.instance_count !== 0) fail(`[${label}] ${name} non-zero count on error`);
  }
  console.log(`PASS: [${label}] explicit error field returned for all three tools, no silent empty fleet`);
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), 'aesop-mcp-pyres-'));
  const stateRoot = join(tmp, 'state');
  const shimDir = join(tmp, 'shim-bin');
  const emptyDir = join(tmp, 'empty-bin');
  mkdirSync(stateRoot, { recursive: true });
  mkdirSync(shimDir);
  mkdirSync(emptyDir);
  const servers = [];
  try {
    const base = { ...process.env, AESOP_STATE_ROOT: stateRoot };
    delete base.AESOP_PYTHON;
    runPy(['register', 'inst-red-1', 'testhost', String(process.pid)], base);
    runPy(['claim', 'inst-red-1', 'src/red_file.py'], base);

    const real = realInterpreter(base);
    writeShim(shimDir, real);

    await expectWorking('python-shim-only', envWithPath(base, [shimDir, ...SYS_DIRS].join(delimiter)), servers);
    servers.splice(0).forEach((s) => s.proc.kill());

    await expectWorking(
      'aesop-python-override',
      { ...envWithPath(base, [emptyDir, ...SYS_DIRS].join(delimiter)), AESOP_PYTHON: real },
      servers,
    );
    servers.splice(0).forEach((s) => s.proc.kill());

    await expectNoInterpreter('no-interpreter', envWithPath(base, [emptyDir, ...SYS_DIRS].join(delimiter)), servers);
    servers.splice(0).forEach((s) => s.proc.kill());
  } finally {
    for (const s of servers) s.proc.kill();
    rmSync(tmp, { recursive: true, force: true });
  }
  process.exit(0);
}

main().catch((err) => fail(err.message));
