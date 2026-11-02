// Pins the S4 SHIP Mode 2 contradiction in LANE-CONTRACT.md (aesop row f-80a9d70f, docs half).
// Section 6 says a Mode 2 repo (no CI) lane stops at PR open and never arms auto-merge.
// The S4 SHIP stage step 7 arms `gh pr merge <N> --auto` with no Mode 2 exception, and the
// Mode 1 arming text says arming equals merging is acceptable. These cases fail until S4 and the
// arming text agree with section 6. G1 guards that the section 6 Mode 2 rule text stays put.
// Uses only Node.js built-ins (node:test, node:assert, node:fs, node:path, node:url).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const contractPath = path.join(repoRoot, 'LANE-CONTRACT.md');
const SECTION_6_HEADING = '## 6. Boundaries';
const S4_MARK = 'S4 SHIP:';
const S5_MARK = 'S5 REVIEW:';
const MODE2_LIST_RE = /Mode 2 - repos WITHOUT CI \(([^)]*)\)/;
const ARMING_EQUALS_MERGING = 'arming equals merging';

function contractText() {
  return fs.readFileSync(contractPath, 'utf8');
}

// Returns section 6 text: from its heading to the next heading of level 1-3.
function section6() {
  const text = contractText();
  const start = text.indexOf(SECTION_6_HEADING);
  assert.notEqual(start, -1, `LANE-CONTRACT.md must keep the "${SECTION_6_HEADING}" heading`);
  const rest = text.slice(start + SECTION_6_HEADING.length);
  const next = rest.search(/\n#{1,3} /);
  return next === -1 ? rest : rest.slice(0, next);
}

// Returns the S4 SHIP stage bullet block: from its "S4 SHIP:" line up to the "S5 REVIEW:" bullet.
function s4Block() {
  const text = contractText();
  const start = text.indexOf(S4_MARK);
  assert.notEqual(start, -1, `LANE-CONTRACT.md must keep the "${S4_MARK}" stage bullet`);
  const end = text.indexOf(S5_MARK, start);
  assert.notEqual(end, -1, `LANE-CONTRACT.md must keep the "${S5_MARK}" stage bullet after S4`);
  return text.slice(start, end);
}

function parseRepoList(source, where) {
  const match = source.match(MODE2_LIST_RE);
  assert.ok(match, `${where} must name the Mode 2 repo list as "Mode 2 - repos WITHOUT CI (<repos>)"`);
  return match[1].split(',').map((s) => s.trim()).filter(Boolean).sort();
}

test('P1 S4 SHIP section names Mode 2 and says do not arm (stop at PR open)', () => {
  const s4 = s4Block();
  assert.ok(
    s4.includes('Mode 2'),
    'S4 SHIP must name the Mode 2 exception (repos without CI) for step 7 arming',
  );
  assert.ok(
    s4.includes('do not arm'),
    'S4 SHIP must say "do not arm" for a Mode 2 repo',
  );
  assert.ok(
    s4.includes('stop at PR open'),
    'S4 SHIP must say a Mode 2 lane stops at PR open',
  );
});

test('P2 no line in LANE-CONTRACT.md says arming equals merging is acceptable', () => {
  const offending = contractText()
    .split('\n')
    .map((line, i) => ({ line, n: i + 1 }))
    .filter(({ line }) => line.includes(ARMING_EQUALS_MERGING));
  assert.equal(
    offending.length,
    0,
    `no line may permit "${ARMING_EQUALS_MERGING}"; offending lines: ${offending
      .map(({ n }) => n)
      .join(', ')}`,
  );
});

test('P3 Mode 2 repo list in S4 SHIP equals the section 6 Mode 2 list', () => {
  const sec6 = parseRepoList(section6(), 'section 6');
  const s4 = parseRepoList(s4Block(), 'S4 SHIP');
  assert.deepEqual(
    s4,
    sec6,
    `S4 SHIP Mode 2 repo list ${JSON.stringify(s4)} must equal section 6 list ${JSON.stringify(sec6)}`,
  );
});

test('G1 section 6 keeps the Mode 2 rule: the lane stops at PR open', () => {
  const sec6 = section6();
  assert.ok(
    sec6.includes('Mode 2 - repos WITHOUT CI'),
    'section 6 must keep the Mode 2 rule heading text',
  );
  assert.ok(
    sec6.includes('the lane stops at PR open'),
    'section 6 must keep "the lane stops at PR open" for Mode 2 repos',
  );
});
