// Packaging contract: the npm tarball must ship the MCP instances-claims helper.
// mcp/server.mjs spawns mcp/instances-claims.py for the fleet_instances, claims and
// multibox tools. If the package `files` allowlist omits it, those tools report
// "absent" in every installed copy (tracker b448b22a95bd).
//
// Contract under test: `npm pack --dry-run --json` lists mcp/instances-claims.py.
// Run: node --test tests/package-files.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');

function packedFilePaths() {
  const result = spawnSync('npm', ['pack', '--dry-run', '--json'], {
    cwd: REPO_ROOT,
    encoding: 'utf-8',
    errors: 'replace',
    shell: process.platform === 'win32',
    maxBuffer: 64 * 1024 * 1024,
  });
  assert.equal(result.status, 0, `npm pack --dry-run exited ${result.status}: ${result.stderr}`);
  const parsed = JSON.parse(result.stdout);
  assert.ok(Array.isArray(parsed) && parsed.length === 1, 'npm pack --json must report one package');
  return new Set(parsed[0].files.map((f) => f.path));
}

test('npm package ships mcp/instances-claims.py (MCP fleet_instances/claims/multibox helper)', () => {
  const files = packedFilePaths();
  assert.ok(
    files.has('mcp/instances-claims.py'),
    'mcp/instances-claims.py is missing from the npm pack file list; add mcp/*.py to package.json files'
  );
});

test('npm package still ships the MCP server entry point alongside the helper', () => {
  const files = packedFilePaths();
  assert.ok(files.has('mcp/server.mjs'), 'mcp/server.mjs must remain in the npm pack file list');
});
