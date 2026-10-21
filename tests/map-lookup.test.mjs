#!/usr/bin/env node
/**
 * `aesop map <term...>` end-to-end tests.
 *
 * Drives the real CLI (bin/cli.js) in a temp fixture directory and asserts the
 * observable output: which INDEX.md / CLAUDE.md entry lines are returned, their
 * ranking, the 30-row cap, the --json shape, --root, the no-match message, and
 * usage errors. Nothing here greps source text.
 */

import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CLI = join(__dirname, '..', 'bin', 'cli.js');
const NO_MATCH = 'no index entry for';
const FALLBACK = 'grep is the fallback, record it as a Discovery line';

function runMap(cwd, args) {
  const r = spawnSync(process.execPath, [CLI, 'map', ...args], {
    cwd,
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'pipe'],
    timeout: 30000,
    windowsHide: true,
  });
  return { status: r.status, stdout: r.stdout || '', stderr: r.stderr || '' };
}

function makeFixture() {
  const dir = mkdtempSync(join(tmpdir(), 'aesop-map-test-'));
  writeFileSync(join(dir, 'INDEX.md'), [
    '# index',
    '- [hooks](hooks/INDEX.md) - 0 entries',
    '- [daemons](daemons/INDEX.md) - 6 entries',
  ].join('\n') + '\n');
  writeFileSync(join(dir, 'CLAUDE.md'), [
    '# Root',
    '## Domain map',
    '- **tools/** - Build utilities (secret scanning, verification gates)',
    '- **ci-runner/** - runs the CI shard matrix',
    '| If you need X | read Y |',
    '|---|---|',
    '| how CI runs game-e2e | LANE-CONTRACT.md CI section |',
  ].join('\n') + '\n');
  // Not an index file: its term must never be returned.
  writeFileSync(join(dir, 'notes.md'), '- zebrafish lives only in a notes file\n');
  mkdirSync(join(dir, 'hooks'));
  writeFileSync(join(dir, 'hooks', 'CLAUDE.md'), '- pre-push policy for branch protection\n');
  return dir;
}

function cleanup(dir) {
  rmSync(dir, { recursive: true, force: true });
}

// ---------------------------------------------------------------------------

test('prints path: one-liner rows for an indexed term and exits 0', () => {
  const dir = makeFixture();
  try {
    const r = runMap(dir, ['daemons']);
    assert.strictEqual(r.status, 0, r.stderr);
    assert.match(r.stdout, /^INDEX\.md: .*daemons/m);
    assert.doesNotMatch(r.stdout, /zebrafish/);
  } finally {
    cleanup(dir);
  }
});

test('searches only INDEX.md and CLAUDE.md files (notes.md term is not found)', () => {
  const dir = makeFixture();
  try {
    const r = runMap(dir, ['zebrafish']);
    assert.strictEqual(r.status, 0, r.stderr);
    assert.ok(r.stdout.includes(NO_MATCH), r.stdout);
  } finally {
    cleanup(dir);
  }
});

test('no match prints the exact fallback message and exits 0', () => {
  const dir = makeFixture();
  try {
    const r = runMap(dir, ['nosuchthinganywhere']);
    assert.strictEqual(r.status, 0, r.stderr);
    assert.ok(
      r.stdout.includes(`${NO_MATCH} nosuchthinganywhere; ${FALLBACK}`),
      `expected the no-match message, got: ${JSON.stringify(r.stdout)}`,
    );
  } finally {
    cleanup(dir);
  }
});

test('matching is case-insensitive', () => {
  const dir = makeFixture();
  try {
    const r = runMap(dir, ['PRE-PUSH']);
    assert.strictEqual(r.status, 0, r.stderr);
    assert.match(r.stdout, /hooks[\\/]CLAUDE\.md: .*pre-push/);
  } finally {
    cleanup(dir);
  }
});

test('whole-word match outranks a bare substring match', () => {
  const dir = makeFixture();
  try {
    writeFileSync(join(dir, 'CLAUDE.md'), [
      '- cinema listings for the lobby',
      '- the CI runner shard matrix',
    ].join('\n') + '\n');
    const r = runMap(dir, ['ci']);
    assert.strictEqual(r.status, 0, r.stderr);
    const rows = r.stdout.split('\n').filter((l) => l.startsWith('CLAUDE.md: '));
    assert.strictEqual(rows.length, 2, r.stdout);
    assert.match(rows[0], /CI runner/, 'whole-word line must rank first');
    assert.match(rows[1], /cinema/);
  } finally {
    cleanup(dir);
  }
});

test('lines matching more distinct terms rank above lines matching fewer', () => {
  const dir = makeFixture();
  try {
    writeFileSync(join(dir, 'CLAUDE.md'), [
      '- shard runner only',
      '- shard runner matrix for CI gates',
    ].join('\n') + '\n');
    const r = runMap(dir, ['shard', 'runner', 'gates']);
    assert.strictEqual(r.status, 0, r.stderr);
    const rows = r.stdout.split('\n').filter((l) => l.startsWith('CLAUDE.md: '));
    assert.match(rows[0], /matrix for CI gates/);
  } finally {
    cleanup(dir);
  }
});

test('--json returns matches with path, line, matched_terms, whole_word_hits, score', () => {
  const dir = makeFixture();
  try {
    const r = runMap(dir, ['--json', 'daemons']);
    assert.strictEqual(r.status, 0, r.stderr);
    const doc = JSON.parse(r.stdout);
    assert.ok(Array.isArray(doc.matches), r.stdout);
    assert.ok(doc.matches.length >= 1);
    const m = doc.matches[0];
    assert.strictEqual(m.path, 'INDEX.md');
    assert.strictEqual(typeof m.line, 'number');
    assert.deepStrictEqual(m.matched_terms, ['daemons']);
    assert.strictEqual(typeof m.whole_word_hits, 'number');
    assert.strictEqual(typeof m.score, 'number');
    assert.strictEqual(typeof m.text, 'string');
  } finally {
    cleanup(dir);
  }
});

test('--json no-match is valid JSON with an empty matches array and the message', () => {
  const dir = makeFixture();
  try {
    const r = runMap(dir, ['--json', 'nosuchthinganywhere']);
    assert.strictEqual(r.status, 0, r.stderr);
    const doc = JSON.parse(r.stdout);
    assert.deepStrictEqual(doc.matches, []);
    assert.ok(doc.message.includes(NO_MATCH), doc.message);
  } finally {
    cleanup(dir);
  }
});

test('text output is capped at 30 rows and reports how many were not shown', () => {
  const dir = makeFixture();
  try {
    const lines = [];
    for (let i = 0; i < 40; i++) lines.push(`- widget entry number ${i}`);
    writeFileSync(join(dir, 'CLAUDE.md'), lines.join('\n') + '\n');
    const r = runMap(dir, ['widget']);
    assert.strictEqual(r.status, 0, r.stderr);
    const rows = r.stdout.split('\n').filter((l) => l.startsWith('CLAUDE.md: '));
    assert.strictEqual(rows.length, 30);
    assert.ok(r.stdout.includes('10 more'), r.stdout);
  } finally {
    cleanup(dir);
  }
});

test('--root adds another project root to the search', () => {
  const dir = makeFixture();
  const other = mkdtempSync(join(tmpdir(), 'aesop-map-other-'));
  try {
    writeFileSync(join(other, 'CLAUDE.md'), '- quokka habitat notes for the other project\n');
    const without = runMap(dir, ['quokka']);
    assert.ok(without.stdout.includes(NO_MATCH), without.stdout);
    const withRoot = runMap(dir, ['--root', other, 'quokka']);
    assert.strictEqual(withRoot.status, 0, withRoot.stderr);
    assert.match(withRoot.stdout, /quokka habitat/);
  } finally {
    cleanup(dir);
    cleanup(other);
  }
});

test('no terms is a usage error (exit 2)', () => {
  const dir = makeFixture();
  try {
    const r = runMap(dir, []);
    assert.strictEqual(r.status, 2);
    assert.match(r.stderr, /Usage: aesop map/);
  } finally {
    cleanup(dir);
  }
});
