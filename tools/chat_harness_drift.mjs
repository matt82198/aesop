#!/usr/bin/env node
// Spec-drift meter for the model-agnostic chat harness (stdlib only).
// INDEX: chat harness spec-drift meter (H-R test pass rate, plan drift vs frozen original, anti-weakening)
// Usage: node tools/chat_harness_drift.mjs --baseline <sha>
// Prints JSON: {total, passing, failing, skipped, impl_drift_pct, plan_drift_pct, original_changed, tests_weakened}
//   total/passing/failing count only H-R tests that were not skipped (skipped tests, e.g. H-R20 waiting on F1, are excluded).
//   impl_drift_pct = 100*(total-passing)/total over tests/chat-harness-spec.test.mjs (TAP).
//   plan_drift_pct = 100*(1 - LCS-line ratio(chat-model-harness.md, chat-model-harness.original.md)).
//   original_changed = the frozen original differs from the baseline commit.
//   tests_weakened = H-R tests removed, renamed or newly skipped relative to the baseline commit.
import { execFileSync, spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const SPEC = 'docs/specs/chat-model-harness.md';
const ORIG = 'docs/specs/chat-model-harness.original.md';
const TESTS = 'tests/chat-harness-spec.test.mjs';
const args = process.argv.slice(2);
const bi = args.indexOf('--baseline');
const baseline = bi >= 0 ? args[bi + 1] : undefined;
if (!baseline) {
  process.stderr.write('usage: node tools/chat_harness_drift.mjs --baseline <sha>\n');
  process.exit(2);
}

const git = (...a) => execFileSync('git', ['-C', root, ...a], { encoding: 'utf8', maxBuffer: 1 << 26 });
const norm = (t) => t.replace(/\r\n/g, '\n');
const lines = (t) => norm(t).split('\n').map((l) => l.trimEnd()).filter((l) => l !== '');

/** Line-level LCS ratio: 2*LCS/(len(a)+len(b)); 1 for two empty inputs. */
export function lcsRatio(a, b) {
  if (a.length + b.length === 0) return 1;
  let prev = new Array(b.length + 1).fill(0);
  for (let i = 1; i <= a.length; i += 1) {
    const cur = new Array(b.length + 1).fill(0);
    for (let j = 1; j <= b.length; j += 1) {
      cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
    }
    prev = cur;
  }
  return (2 * prev[b.length]) / (a.length + b.length);
}

// Map of H-R test name -> skipped? parsed from source (test('H-R1: ...', { skip: ... }, ...)).
function hTests(src) {
  const out = new Map();
  const re = /test\(\s*(['"`])(H-R\d+:[^'"`]*)\1\s*,\s*(\{[^}]*\})?/g;
  for (const m of src.matchAll(re)) out.set(m[2], /skip\s*:/.test(m[3] ?? ''));
  return out;
}

// 1. Run the spec tests and parse TAP (top-level result lines only).
const run = spawnSync(
  process.execPath,
  ['--import', './tests/helpers/isolated-env.mjs', '--test', '--test-force-exit', '--test-reporter=tap', TESTS],
  { cwd: root, encoding: 'utf8', timeout: 600000, maxBuffer: 1 << 26 },
);
const results = [];
let skipped = 0;
for (const line of (run.stdout ?? '').split('\n')) {
  const m = /^(not ok|ok) \d+ - (H-R\d+):(.*)$/.exec(line);
  if (!m) continue;
  if (/# SKIP/i.test(m[3])) { skipped += 1; continue; }
  results.push({ id: m[2], ok: m[1] === 'ok' });
}
const total = results.length;
const passing = results.filter((r) => r.ok).length;
const failing = results.filter((r) => !r.ok).map((r) => r.id);

// 2. Plan drift against the frozen original.
const spec = lines(readFileSync(join(root, SPEC), 'utf8'));
const original = lines(readFileSync(join(root, ORIG), 'utf8'));
const planDrift = 100 * (1 - lcsRatio(spec, original));

// 3. Frozen-original and anti-weakening checks against the baseline commit.
let originalChanged = false;
try {
  originalChanged = norm(git('show', `${baseline}:${ORIG}`)) !== norm(readFileSync(join(root, ORIG), 'utf8'));
} catch {
  originalChanged = true;
}
let testsWeakened = [];
try {
  const before = hTests(git('show', `${baseline}:${TESTS}`));
  const now = hTests(readFileSync(join(root, TESTS), 'utf8'));
  for (const [name, wasSkipped] of before) {
    const id = name.split(':')[0];
    if (!now.has(name)) testsWeakened.push(`${id} removed or renamed`);
    else if (now.get(name) && !wasSkipped) testsWeakened.push(`${id} newly skipped`);
  }
} catch {
  testsWeakened = ['<baseline test file unreadable>'];
}

process.stdout.write(`${JSON.stringify({
  total,
  passing,
  failing,
  skipped,
  impl_drift_pct: total === 0 ? 100 : Math.round((10000 * (total - passing)) / total) / 100,
  plan_drift_pct: Math.round(planDrift * 100) / 100,
  original_changed: originalChanged,
  tests_weakened: testsWeakened,
}, null, 2)}\n`);
