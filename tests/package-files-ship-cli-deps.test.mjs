import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Pins the contract that every domain directory bin/cli.js requires or spawns is shipped
// in the npm tarball. A package whose "files" omits stdio/ installs cleanly but crashes at
// bin/cli.js with MODULE_NOT_FOUND. This test reads the real bin/cli.js and asks the real
// `npm pack --dry-run --json` what would ship. Nothing is mocked.

const testDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(testDir, '..');
const cliSource = readFileSync(join(repoRoot, 'bin', 'cli.js'), 'utf8');

/** Return true when a repo-relative path names a file inside a real top-level directory. */
function isDirScopedRepoFile(relPath) {
  const parts = relPath.split('/');
  if (parts.length < 2 || parts.some((p) => p === '..' || p === '')) return false;
  return existsSync(join(repoRoot, parts[0])) && statSync(join(repoRoot, parts[0])).isDirectory();
}

/**
 * Collect repo-relative script/module files that bin/cli.js references by path.
 * Three shapes are recognised:
 *   1. require('../<dir>/<file>.js|.mjs')
 *   2. path.join|resolve(__dirname, '..', '<dir>', ..., '<file>.js|.mjs')
 *   3. any quoted '<dir>/<file>.js|.mjs' literal (spawn/spawnSync argument strings)
 * @param {string} source contents of bin/cli.js
 * @returns {Set<string>} repo-relative paths such as 'stdio/cli.mjs'
 */
function collectReferencedFiles(source) {
  const found = new Set();
  const code = /\.(?:js|mjs)$/;

  for (const m of source.matchAll(/require\(\s*'\.\.\/([^']+)'\s*\)/g)) {
    if (code.test(m[1]) && isDirScopedRepoFile(m[1])) found.add(m[1]);
  }

  for (const m of source.matchAll(/path\.(?:join|resolve)\(([^)\n]*)\)/g)) {
    const quoted = [...m[1].matchAll(/'([^']*)'/g)].map((q) => q[1]);
    const up = quoted.indexOf('..');
    if (up === -1) continue;
    const rel = quoted.slice(up + 1).join('/');
    if (code.test(rel) && isDirScopedRepoFile(rel)) found.add(rel);
  }

  for (const m of source.matchAll(/'([A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-/]+\.(?:mjs|js))'/g)) {
    if (isDirScopedRepoFile(m[1])) found.add(m[1]);
  }

  return found;
}

let packCache = null;

/**
 * Run `npm pack --dry-run --json` once and return the tarball entry paths.
 * @returns {Set<string>} entry paths relative to the package root, e.g. 'stdio/cli.mjs'
 */
function packEntries() {
  if (packCache) return packCache;
  const res = spawnSync('npm', ['pack', '--dry-run', '--json'], {
    cwd: repoRoot,
    encoding: 'utf8',
    timeout: 120000,
    shell: process.platform === 'win32',
    windowsHide: true,
  });
  assert.equal(res.error, undefined, `npm pack failed to run: ${res.error && res.error.message}`);
  assert.equal(res.status, 0, `npm pack exited ${res.status}: ${res.stderr}`);
  const out = res.stdout.slice(res.stdout.indexOf('['));
  const [pack] = JSON.parse(out);
  packCache = new Set(pack.files.map((f) => f.path));
  return packCache;
}

const referenced = collectReferencedFiles(cliSource);
const referencedDirs = new Set([...referenced].map((p) => p.split('/')[0]));

test('bin/cli.js references at least one domain directory (scanner sanity)', () => {
  assert.ok(referenced.has('stdio/tty-decision.js'), `scanner missed require('../stdio/tty-decision.js'); found ${[...referenced].join(', ')}`);
  assert.ok(referenced.has('stdio/cli.mjs'), `scanner missed path.join(..., 'stdio', 'cli.mjs'); found ${[...referenced].join(', ')}`);
});

test('every domain directory bin/cli.js references has at least one npm-packed entry', () => {
  const entries = packEntries();
  const missingDirs = [...referencedDirs].filter((dir) => ![...entries].some((p) => p.startsWith(`${dir}/`)));
  assert.deepEqual(
    missingDirs,
    [],
    `bin/cli.js references these directories but npm pack ships none of their files: ${missingDirs.join(', ')}`,
  );
});

test('every file path bin/cli.js references is in the npm tarball entry list', () => {
  const entries = packEntries();
  const missingFiles = [...referenced].filter((f) => !entries.has(f)).sort();
  assert.deepEqual(
    missingFiles,
    [],
    `bin/cli.js references files that npm pack would not ship (add them to package.json "files"): ${missingFiles.join(', ')}`,
  );
});
