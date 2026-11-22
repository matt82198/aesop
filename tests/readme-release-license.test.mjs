import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..');

test('README.md Status version matches package.json version', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'package.json'), 'utf8'));
  const readme = fs.readFileSync(path.join(REPO_ROOT, 'README.md'), 'utf8');

  const statusMatch = readme.match(/>\s*\*\*Status\.\*\*\s+Version\s+([0-9]+\.[0-9]+\.[0-9]+)\s+\(`package\.json:3`\)/);
  assert.ok(statusMatch, 'README.md must contain a Status line citing package.json:3');
  assert.equal(
    statusMatch[1],
    pkg.version,
    `README.md Status version (${statusMatch[1]}) must match package.json version (${pkg.version})`
  );
});

test('README.md CDN links match package.json version', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'package.json'), 'utf8'));
  const readme = fs.readFileSync(path.join(REPO_ROOT, 'README.md'), 'utf8');

  const cdnMatches = [...readme.matchAll(/cdn\.jsdelivr\.net\/npm\/@matt82198\/aesop@([0-9]+\.[0-9]+\.[0-9]+)\//g)];
  assert.ok(cdnMatches.length > 0, 'README.md should reference @matt82198/aesop CDN assets');
  for (const m of cdnMatches) {
    assert.equal(
      m[1],
      pkg.version,
      `README.md CDN URL version (${m[1]}) must match package.json version (${pkg.version})`
    );
  }
});

test('README.md and CLAUDE.md state current license as MIT', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'package.json'), 'utf8'));
  const readme = fs.readFileSync(path.join(REPO_ROOT, 'README.md'), 'utf8');
  const claudeMd = fs.readFileSync(path.join(REPO_ROOT, 'CLAUDE.md'), 'utf8');

  assert.equal(pkg.license, 'MIT', 'package.json license must be MIT');
  assert.match(readme, /licensed under the \*\*\[MIT License\]\(\.\/LICENSE\)\*\*/);
  assert.match(claudeMd.split(/\r?\n/)[2], /^\*\*What\*\*:\s+MIT-licensed/);
});
