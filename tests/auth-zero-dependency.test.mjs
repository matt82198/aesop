// S1 SPEC: the auth surface must stay zero-third-party-dependency.
// The future implementation lives in auth/{store,providers,cli}.mjs. Every import in auth/
// must be a node: builtin or a relative repo file, and the modules must load under node alone.
// Style follows the zero-dependency assertions in the repo's other Node suites.
//
// Expected to FAIL now: auth/ does not exist yet.
//
// Run: node --test tests/auth-zero-dependency.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { isBuiltin } from 'node:module';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const AUTH_DIR = path.join(REPO_ROOT, 'auth');
const REQUIRED_MODULES = ['store.mjs', 'providers.mjs', 'cli.mjs'];

/** Module specifiers from import/export-from/dynamic-import statements in one source file. */
function importSpecifiers(source) {
  const specs = [];
  const re = /(?:^|[\s;{}])(?:import|export)\s[^'"`;]*?from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)|import\s*['"]([^'"]+)['"]/g;
  let m;
  while ((m = re.exec(source)) !== null) specs.push(m[1] ?? m[2] ?? m[3]);
  return specs;
}

test('auth/ contains the three planned modules store.mjs, providers.mjs and cli.mjs', () => {
  assert.equal(fs.existsSync(AUTH_DIR), true, `missing directory ${AUTH_DIR}`);
  for (const name of REQUIRED_MODULES) {
    assert.equal(fs.existsSync(path.join(AUTH_DIR, name)), true, `missing auth/${name}`);
  }
});

test('every import in auth/*.mjs is a node: builtin or a relative repo file (no third-party packages)', () => {
  const files = fs.readdirSync(AUTH_DIR).filter((f) => f.endsWith('.mjs'));
  assert.ok(files.length >= REQUIRED_MODULES.length, `expected auth/*.mjs modules, found: ${files.join(', ')}`);
  for (const file of files) {
    const abs = path.join(AUTH_DIR, file);
    for (const spec of importSpecifiers(fs.readFileSync(abs, 'utf8'))) {
      if (spec.startsWith('node:')) {
        assert.ok(isBuiltin(spec), `auth/${file} imports unknown node builtin ${spec}`);
        continue;
      }
      assert.ok(
        spec.startsWith('./') || spec.startsWith('../'),
        `auth/${file} imports third-party specifier "${spec}"; only node: builtins and relative repo files are allowed`,
      );
      const resolved = path.resolve(AUTH_DIR, spec);
      const candidates = [resolved, `${resolved}.mjs`, `${resolved}.js`, path.join(resolved, 'index.mjs')];
      assert.ok(candidates.some((c) => fs.existsSync(c) && fs.statSync(c).isFile()),
        `auth/${file} imports "${spec}" which is not a repo file`);
    }
  }
});

test('each auth module loads under plain node with no node_modules (dynamic import succeeds)', async () => {
  for (const name of REQUIRED_MODULES) {
    const url = pathToFileURL(path.join(AUTH_DIR, name)).href;
    const mod = await import(url);
    assert.equal(typeof mod, 'object', `auth/${name} must load as an ES module`);
  }
});
