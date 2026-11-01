// Pins the S3 test-log recipe in LANE-CONTRACT.md (aesop row f-7186fd43).
// The harness backgrounds any lane Bash call at 180 s, so the S3 recipe must
// run the suite in foreground chunks, sum per-chunk footers, and never relaunch
// or poll a backgrounded chunk. These cases fail until the S3 section says so.
// Uses only Node.js built-ins (node:test, node:assert, node:fs, node:path, node:url).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const contractPath = path.join(repoRoot, 'LANE-CONTRACT.md');
const S3_HEADING = '### S3 test-log recipe';
const INFO_MARK = 'ℹ';

// Returns the text of the S3 section: from its heading to the next heading of level 1-3.
function s3Section() {
  const text = fs.readFileSync(contractPath, 'utf8');
  const start = text.indexOf(S3_HEADING);
  assert.notEqual(start, -1, `LANE-CONTRACT.md must keep the "${S3_HEADING}" heading`);
  const rest = text.slice(start + S3_HEADING.length);
  const next = rest.search(/\n#{1,3} /);
  return next === -1 ? rest : rest.slice(0, next);
}

function assertContains(section, phrase, why) {
  assert.ok(section.includes(phrase), `S3 section must contain: ${phrase}\n(${why})`);
}

test('T1 S3 section states the 180 s harness background fact', () => {
  const s3 = s3Section();
  assertContains(
    s3,
    'the harness backgrounds a Bash call at 180 s regardless of the timeout parameter.',
    'fact line: a timeout of 600000 does not stop the harness from backgrounding at 180 s',
  );
});

test('T2 S3 section gives the chunk rule with full redirect and reporter destination flag', () => {
  const s3 = s3Section();
  assertContains(
    s3,
    'tests/*.test.mjs run in chunks of at most 8 files',
    'chunk size cap so each foreground call fits under 180 s',
  );
  assertContains(
    s3,
    'each chunk one foreground Bash call that must finish under 170 s',
    'one foreground call per chunk with a 170 s budget under the 180 s harness limit',
  );
  assertContains(
    s3,
    '> .s3-chunk-N.log 2>&1',
    'full redirect of stdout and stderr into a per-chunk log',
  );
  assert.ok(
    s3.includes('--test-reporter-destination'),
    'S3 section must pass the --test-reporter-destination flag in the chunk command',
  );
});

test('T3 S3 section runs tests/collect-signals.test.mjs alone, split into subsets by name pattern', () => {
  const s3 = s3Section();
  assertContains(
    s3,
    'tests/collect-signals.test.mjs runs alone',
    'the long suite must never share a chunk with other files',
  );
  assertContains(
    s3,
    '`--test-name-pattern`',
    'the long suite is split by the --test-name-pattern flag',
  );
  assertContains(
    s3,
    'into subsets',
    'the split produces subsets, each its own foreground call',
  );
});

test('T4 S3 section sums per-chunk footers and treats a missing footer as a recipe defect', () => {
  const s3 = s3Section();
  assertContains(
    s3,
    `per-chunk ${INFO_MARK} tests/pass/fail footers are summed and reported per chunk`,
    'footer rule: per-chunk ℹ tests/pass/fail counts are summed and reported per chunk',
  );
  assertContains(
    s3,
    'a chunk with no footer is a recipe defect, never a code verdict',
    'a footerless chunk is a recipe problem, not a test failure verdict',
  );
});

test('T5 S3 section reads a backgrounded chunk from its log after the notification, never relaunch or poll', () => {
  const s3 = s3Section();
  assertContains(
    s3,
    'a chunk the harness backgrounds is read from its log after the harness notification',
    'backgrounded chunk is read from its log once the harness notifies',
  );
  assertContains(
    s3,
    'never relaunched, never polled',
    'a backgrounded chunk is never rerun and never polled',
  );
});
