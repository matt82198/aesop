#!/usr/bin/env node
/**
 * MCP server metadata test (0.10.1-prep S4).
 *
 * (a) mcp/package.json "author" must equal the root package.json "author".
 * (b) The live server's initialize response must advertise the current MCP
 *     protocol version: LATEST_PROTOCOL_VERSION of @modelcontextprotocol/sdk
 *     1.32.1 (dist/esm/types.js), which is 2025-11-25.
 *
 * (b) drives the real server over stdio and asserts the observable result.
 * Uses a temp AESOP_STATE_ROOT; never touches the real state dir.
 */

import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const EXPECTED_PROTOCOL_VERSION = '2025-11-25';

function fail(msg) {
  console.error(`FAIL: ${msg}`);
  process.exit(1);
}

function readJson(relPath) {
  return JSON.parse(readFileSync(join(REPO_ROOT, relPath), 'utf-8'));
}

function testAuthorMatchesRoot() {
  const rootAuthor = readJson('package.json').author;
  const mcpAuthor = readJson('mcp/package.json').author;
  if (!rootAuthor) fail('root package.json has no author');
  if (mcpAuthor !== rootAuthor) {
    fail(`mcp/package.json author ${JSON.stringify(mcpAuthor)} !== root author ${JSON.stringify(rootAuthor)}`);
  }
  console.log(`ok author: ${mcpAuthor}`);
}

async function testInitializeProtocolVersion() {
  const tmp = mkdtempSync(join(tmpdir(), 'aesop-mcp-metadata-'));
  const stateRoot = join(tmp, 'state');
  const env = { ...process.env, AESOP_STATE_ROOT: stateRoot, AESOP_ROOT: REPO_ROOT };
  const proc = spawn('node', [join(REPO_ROOT, 'mcp', 'server.mjs'), '--root', REPO_ROOT], { env });
  const pending = new Map();
  const rl = createInterface({ input: proc.stdout });
  rl.on('line', (line) => {
    let msg;
    try { msg = JSON.parse(line); } catch { return; }
    if (msg.id !== undefined && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  });
  try {
    const resp = await new Promise((ok, bad) => {
      const timer = setTimeout(() => bad(new Error('timeout waiting for initialize')), 15000);
      pending.set(1, (msg) => { clearTimeout(timer); ok(msg); });
      proc.stdin.write(JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'initialize',
        params: { protocolVersion: EXPECTED_PROTOCOL_VERSION, capabilities: {}, clientInfo: { name: 't', version: '0' } },
      }) + '\n');
    });
    const got = resp.result && resp.result.protocolVersion;
    if (got !== EXPECTED_PROTOCOL_VERSION) {
      fail(`initialize protocolVersion ${JSON.stringify(got)} !== ${JSON.stringify(EXPECTED_PROTOCOL_VERSION)}`);
    }
    console.log(`ok protocolVersion: ${got}`);
  } finally {
    proc.kill();
    rl.close();
    rmSync(tmp, { recursive: true, force: true });
  }
}

async function main() {
  testAuthorMatchesRoot();
  await testInitializeProtocolVersion();
  console.log('PASS mcp-metadata');
  process.exit(0);
}

main().catch((err) => fail(err && err.stack ? err.stack : String(err)));
