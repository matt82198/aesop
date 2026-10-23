// Tests that .gitattributes pins line endings so Windows runners and the
// box commit identical bytes (R-CI-2 (b)):
//   1. a `*` rule sets `text=auto` and `eol=lf` (catch-all text normalization)
//   2. text-like extensions (md, json, jsonl, yml, yaml) resolve eol=lf
//   3. binary extensions (png, gif, mp4) resolve as binary (text unset)
//
// Run: node --test tests/gitattributes-eol.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');

function checkAttr(attr, file) {
  const out = execFileSync('git', ['check-attr', attr, '--', file], {
    cwd: repoRoot,
    encoding: 'utf8'
  });
  // Output form: "<path>: <attr>: <value>"
  const line = out.trim().split('\n')[0] || '';
  const prefix = `${file}: ${attr}: `;
  return line.startsWith(prefix) ? line.slice(prefix.length) : line;
}

function starRuleLines() {
  const text = fs.readFileSync(path.join(repoRoot, '.gitattributes'), 'utf8');
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
    .filter((l) => l.split(/\s+/)[0] === '*');
}

test('.gitattributes has a * rule with text=auto and eol=lf', () => {
  const rules = starRuleLines();
  assert.ok(
    rules.some((l) => /(^|\s)text=auto(\s|$)/.test(l) && /(^|\s)eol=lf(\s|$)/.test(l)),
    `expected a "* text=auto eol=lf" rule in .gitattributes; found star rules: ${JSON.stringify(rules)}`
  );
});

for (const ext of ['md', 'json', 'jsonl', 'yml', 'yaml']) {
  test(`sample.${ext} resolves eol=lf`, () => {
    const file = `sample.${ext}`;
    const value = checkAttr('eol', file);
    assert.equal(
      value,
      'lf',
      `git check-attr eol -- ${file} resolved to "${value}", expected "lf"`
    );
  });
}

for (const ext of ['png', 'gif', 'mp4']) {
  test(`sample.${ext} resolves as binary (binary set or text unset)`, () => {
    const file = `sample.${ext}`;
    const binary = checkAttr('binary', file);
    const text = checkAttr('text', file);
    assert.ok(
      binary === 'set' || text === 'unset',
      `git check-attr for ${file}: binary="${binary}", text="${text}"; expected binary=set or text=unset`
    );
  });
}
