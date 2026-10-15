#!/usr/bin/env node
/**
 * MCP instances DB test (F-06).
 *
 * Registers an instance + claim through tools/instance_manager.py (the real writer),
 * then calls the MCP fleet_instances tool over stdio. The MCP must read the same
 * database the writer produced: absent must be false and the instance listed.
 * Uses a temp AESOP_STATE_ROOT; never touches the real state dir.
 */

import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const PY = process.env.PYTHON || 'python';

function fail(msg) {
  console.error(`FAIL: ${msg}`);
  process.exit(1);
}

function runPy(args, env) {
  const r = spawnSync(PY, ['-I', join(REPO_ROOT, 'tools', 'instance_manager.py'), ...args], {
    env,
    encoding: 'utf-8',
    cwd: REPO_ROOT,
    timeout: 60000,
  });
  if (r.status !== 0) fail(`instance_manager ${args.join(' ')} exited ${r.status}: ${r.stderr}`);
}

function startServer(env) {
  const proc = spawn('node', [join(REPO_ROOT, 'mcp', 'server.mjs'), '--root', REPO_ROOT], {
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
    const timer = setTimeout(() => bad(new Error(`timeout waiting for ${method}`)), 15000);
    pending.set(id, (msg) => { clearTimeout(timer); ok(msg); });
    proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
  return { proc, request };
}

async function main() {
  const tmp = mkdtempSync(join(tmpdir(), 'aesop-mcp-instances-'));
  const stateRoot = join(tmp, 'state');
  mkdirSync(stateRoot, { recursive: true });
  const env = { ...process.env, AESOP_STATE_ROOT: stateRoot };
  let server;
  try {
    runPy(['register', 'inst-red-1', 'testhost', String(process.pid)], env);
    runPy(['claim', 'inst-red-1', 'src/red_file.py'], env);

    server = startServer(env);
    await server.request('initialize', {
      protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 't', version: '0' },
    });
    const resp = await server.request('tools/call', { name: 'fleet_instances', arguments: {} });
    const text = resp.result && resp.result.content && resp.result.content[0] && resp.result.content[0].text;
    if (!text) fail(`no tool content: ${JSON.stringify(resp)}`);
    const data = JSON.parse(text);
    if (data.absent === true) fail(`MCP reports absent while the instance writer has data: ${data.reason}`);
    const ids = (data.instances || []).map((i) => i.instance_id || i.id);
    if (!ids.includes('inst-red-1')) fail(`instance inst-red-1 not listed: ${text}`);
    console.log('PASS: MCP fleet_instances reads the instance writer database');
  } finally {
    if (server) server.proc.kill();
    rmSync(tmp, { recursive: true, force: true });
  }
  process.exit(0);
}

main().catch((err) => fail(err.message));
