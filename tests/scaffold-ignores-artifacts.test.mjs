// Contract under test (f-694132f3):
//  - `node bin/cli.js <target>` scaffolds from the package root (bin/..) and must NOT copy
//    ignored build/runtime artifacts (ui/**/node_modules, aesop-fleet/) into the target.
//  - The scaffold call must finish well inside a 45 s budget even when the source tree
//    carries a sizeable ignored artifact tree; a walk that descends into it is a regression.
//
// Fixture: a tmp copy of the repo (bin/, ui/, tools/, ... minus .git) that ALSO contains
//  - ui/web/node_modules/big/   (200 small files)
//  - aesop-fleet/               (50 files, at the repo root)
//
// Run: node --test tests/scaffold-ignores-artifacts.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { cleanupDir } from './helpers/cleanup.mjs';

const REPO_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
// Budget 45 s: CI windows-shard measured 27.9 s for a correct 651-file scaffold (run 40559065071);
// a scaffold that copies ui/web/node_modules exceeds the 60 s test timeout (row f-694132f3), so 45 s still catches the regression.
const SCAFFOLD_TIMEOUT_MS = 45000;

function makeFiles(dir, count, prefix) {
  fs.mkdirSync(dir, { recursive: true });
  for (let i = 0; i < count; i++) {
    fs.writeFileSync(path.join(dir, `${prefix}-${i}.txt`), `artifact ${i}\n`);
  }
}

/** Recursively collect every entry name under dir (files and dirs). */
function collectNames(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    out.push(entry.name);
    if (entry.isDirectory()) collectNames(path.join(dir, entry.name), out);
  }
  return out;
}

test('scaffold does not copy ignored artifacts (ui/**/node_modules, aesop-fleet/) and finishes under 45 s', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-scaffold-ignores-'));
  try {
    // 1. Tmp copy of the repo (package root = templateRoot for the scaffold).
    const repoCopy = path.join(root, 'repo');
    fs.cpSync(REPO_ROOT, repoCopy, {
      recursive: true,
      filter: (src) => path.basename(src) !== '.git'
    });

    // 2. Ignored artifacts inside the repo copy.
    makeFiles(path.join(repoCopy, 'ui', 'web', 'node_modules', 'big'), 200, 'pkg');
    makeFiles(path.join(repoCopy, 'aesop-fleet'), 50, 'fleet');

    // 3. Scaffold the repo copy into a fresh target, with a 45 s budget.
    const target = path.join(root, 'target');
    const t0 = Date.now();
    const res = spawnSync(process.execPath, [path.join(repoCopy, 'bin', 'cli.js'), target, '--no-skills'], {
      encoding: 'utf8',
      cwd: root,
      timeout: SCAFFOLD_TIMEOUT_MS,
      killSignal: 'SIGKILL'
    });
    const elapsed = Date.now() - t0;

    // 4. Must have finished under the timeout (a timed-out spawn reports signal SIGKILL).
    assert.notEqual(
      res.signal, 'SIGKILL',
      `scaffold did not finish within ${SCAFFOLD_TIMEOUT_MS} ms (elapsed ${elapsed} ms). stdout tail: ${String(res.stdout).slice(-800)}`
    );
    assert.ok(elapsed < SCAFFOLD_TIMEOUT_MS, `scaffold took ${elapsed} ms, budget ${SCAFFOLD_TIMEOUT_MS} ms (elapsed_ms=${elapsed})`);
    assert.equal(res.status, 0, `scaffold exited ${res.status} (elapsed_ms=${elapsed}). stderr tail: ${String(res.stderr).slice(-800)}`);

    // 5. Target must contain no node_modules and no aesop-fleet anywhere.
    const names = collectNames(target);
    const nodeModulesHits = names.filter((n) => n === 'node_modules');
    const fleetHits = names.filter((n) => n === 'aesop-fleet');
    assert.deepEqual(nodeModulesHits, [], `scaffolded target contains node_modules entries: ${nodeModulesHits.length}`);
    assert.deepEqual(fleetHits, [], `scaffolded target contains aesop-fleet entries: ${fleetHits.length}`);
  } finally {
    // Windows holds short-lived locks on freshly written trees (AV scan, indexer);
    // retry the teardown so a transient EBUSY/EPERM does not fail a green test.
    cleanupDir(root, { label: 'scaffold-ignores' });
  }
});
