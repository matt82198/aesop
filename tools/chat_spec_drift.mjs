#!/usr/bin/env node
// Spec-drift meter for the chat TUI (stdlib only).
// Usage: node tools/chat_spec_drift.mjs --baseline <sha>
// Prints JSON: {total, passing, failing, impl_drift_pct, plan_drift_pct, original_changed, tests_weakened}
//   impl_drift_pct = 100*(total-passing)/total over tests/chat-spec.test.mjs (TAP).
//   plan_drift_pct = 100*(1 - LCS-line ratio(chat/SPEC.md, chat/SPEC.original.md)).
//   original_changed = SPEC.original.md differs from the baseline commit.
//   tests_weakened = R-test names present at the baseline commit but absent now.
import { execFileSync, spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const bi = args.indexOf('--baseline');
const baseline = bi >= 0 ? args[bi + 1] : undefined;
if (!baseline) {
  process.stderr.write('usage: node tools/chat_spec_drift.mjs --baseline <sha>\n');
  process.exit(2);
}

const git = (...a) => execFileSync('git', ['-C', root, ...a], { encoding: 'utf8', maxBuffer: 1 << 26 });
const lines = (t) => t.replace(/\r\n/g, '\n').split('\n').map((l) => l.trimEnd()).filter((l) => l !== '');

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

const rNames = (src) => [...src.matchAll(/test\(\s*(['"`])(R\d+:[^'"`]*)\1/g)].map((m) => m[2]);

// 1. Run the spec tests and parse TAP (top-level result lines only).
const run = spawnSync(
  process.execPath,
  ['--import', './tests/helpers/isolated-env.mjs', '--test', '--test-force-exit', '--test-reporter=tap', 'tests/chat-spec.test.mjs'],
  { cwd: root, encoding: 'utf8', timeout: 240000, maxBuffer: 1 << 26 },
);
const results = [];
for (const line of (run.stdout ?? '').split('\n')) {
  const m = /^(not ok|ok) \d+ - (R\d+):/.exec(line);
  if (m) results.push({ id: m[2], ok: m[1] === 'ok' });
}
const total = results.length;
const passing = results.filter((r) => r.ok).length;
const failing = results.filter((r) => !r.ok).map((r) => r.id);

// 2. Plan drift against the frozen original.
const spec = lines(readFileSync(join(root, 'chat', 'SPEC.md'), 'utf8'));
const original = lines(readFileSync(join(root, 'chat', 'SPEC.original.md'), 'utf8'));
const planDrift = 100 * (1 - lcsRatio(spec, original));

// 3. Frozen-original and anti-weakening checks against the baseline commit.
let originalChanged = false;
try {
  originalChanged = git('show', `${baseline}:chat/SPEC.original.md`).replace(/\r\n/g, '\n') !== readFileSync(join(root, 'chat', 'SPEC.original.md'), 'utf8').replace(/\r\n/g, '\n');
} catch {
  originalChanged = true;
}
let testsWeakened = [];
try {
  const before = rNames(git('show', `${baseline}:tests/chat-spec.test.mjs`));
  const now = new Set(rNames(readFileSync(join(root, 'tests', 'chat-spec.test.mjs'), 'utf8')));
  testsWeakened = before.filter((n) => !now.has(n));
} catch {
  testsWeakened = ['<baseline test file unreadable>'];
}

process.stdout.write(`${JSON.stringify({
  total,
  passing,
  failing,
  impl_drift_pct: total === 0 ? 100 : (100 * (total - passing)) / total,
  plan_drift_pct: Math.round(planDrift * 100) / 100,
  original_changed: originalChanged,
  tests_weakened: testsWeakened,
}, null, 2)}\n`);
