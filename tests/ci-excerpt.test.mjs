import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const cli = fileURLToPath(new URL('../bin/cli.js', import.meta.url));
const cases = JSON.parse(readFileSync(new URL('./fixtures/ci-excerpt.json', import.meta.url)));

function invoke(args, input, cwd = tmpdir()) {
  return spawnSync(process.execPath, [cli, 'excerpt', ...args], {
    input, cwd, encoding: 'utf8', timeout: 10000,
  });
}

function records(args, input, cwd) {
  const result = invoke(args, input, cwd);
  assert.equal(result.status, 0, result.stderr.split('\n')[0]);
  return JSON.parse(result.stdout);
}

test('50 frozen gold fields across ten CI failures, plus job, step, full ID and parent', (t) => {
  const cwd = mkdtempSync(join(tmpdir(), 'ci-excerpt-'));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  assert.equal(cases.length, 10);
  for (const entry of cases) {
    // Restore physical line positions omitted from the sanitized log windows.
    writeFileSync(join(cwd, 'step.txt'), '\n'.repeat(entry.first_line - 1) + entry.lines.join('\n'));
    const hits = records(['--log', 'step.txt', '--job', entry.job, '--step', entry.step], '', cwd);
    assert.equal(hits.length, 1, entry.job);
    const [hit] = hits;
    const [timestamp, ...message] = hit.assertion.text.split(' ');
    assert.deepEqual({
      suite: hit.suite, subcase: hit.subcase, line: hit.assertion.line,
      timestamp, assertion: message.join(' '),
    }, entry.gold, entry.job);
    assert.equal(hit.job, entry.job);
    assert.equal(hit.step, entry.step);
    assert.equal(hit.test_id, entry.test_id);
    assert.deepEqual(hit.parent, entry.parent);
  }
});

test('stdin keeps separate shell failures and resets context at a new suite', () => {
  const lines = ['[TEST] Running: abc.sh', '=== Test 1: first ===', 'FAIL: one  ',
    '[TEST] Test 2: second', 'FAIL: two', '[TEST] Running: new.sh', 'FAIL: no heading'];
  const hits = records(['--log', '-'], '\uFEFF' + lines.join('\r\n'));
  assert.deepEqual(hits.map(h => [h.job, h.step, h.test_id, h.parent, h.assertion]), [
    ['unknown', 'unknown', 'abc.sh :: Test 1: first', { line: 2, text: lines[1] }, { line: 3, text: lines[2] }],
    ['unknown', 'unknown', 'abc.sh :: Test 2: second', { line: 4, text: lines[3] }, { line: 5, text: lines[4] }],
    ['unknown', 'unknown', 'unknown', 'unknown', { line: 7, text: lines[6] }],
  ]);
});

test('unittest blocks cannot borrow assertions from siblings or choose between two candidates', () => {
  const lines = ['FAIL: test_a (pkg.C.test_a)', '-----', '=====',
    'FAIL: test_b (pkg.C.test_b)', '-----', 'AssertionError: one',
    'AssertionError: two', '=====', 'FAIL: test_c (pkg.C.test_c)', '-----', 'AssertionError: three'];
  const hits = records(['--log', '-'], lines.join('\n'));
  assert.deepEqual(hits.map(h => [h.test_id, h.assertion]), [
    ['pkg.C.test_a', 'unknown'], ['pkg.C.test_b', 'unknown'],
    ['pkg.C.test_c', { line: 11, text: lines[10] }],
  ]);
});

test('gh run view prefixes supply metadata and prevent cross-step attribution', () => {
  const stamp = '2026-11-01T01:02:03.1234567Z ';
  const lines = ['ci A\tShell\t' + stamp + '[TEST] Running: abc.sh',
    'ci A\tShell\t' + stamp + '=== Test 1: first ===',
    'ci B\tOther\t' + stamp + 'FAIL: unscoped'];
  const [hit] = records(['--log', '-'], lines.join('\n'));
  assert.deepEqual([hit.job, hit.step, hit.test_id, hit.parent, hit.assertion],
    ['ci B', 'Other', 'unknown', 'unknown', { line: 3, text: lines[2] }]);
});

test('empty result, help, bad arguments and unreadable input have distinct outcomes', () => {
  assert.deepEqual(records(['--log', '-'], 'PASS: all tests passed'), []);
  const help = invoke(['--help'], '');
  assert.equal(help.status, 0);
  assert.match(help.stdout, /excerpt --log/);
  for (const args of [['--log'], ['--log', '-', '--typo'], ['--log', 'no-such-excerpt/input.txt']]) {
    const result = invoke(args, '');
    assert.equal(result.status, 2);
    assert.equal(result.stdout, '');
    assert.ok(result.stderr.trim());
  }
});

test('runner-only failures keep missing evidence unknown and completed suites cannot lend a heading', () => {
  const lines = ['[TEST] Running: abc.sh', '=== Test 7: previous ===', '[PASS] abc.sh',
    'FAIL: orphan', '[TEST] Running: def.sh', '[FAIL] def.sh (exit code: 1)'];
  const hits = records(['--log', '-'], lines.join('\n'));
  assert.deepEqual(hits.map(h => [h.suite, h.test_id, h.parent, h.assertion]), [
    ['unknown', 'unknown', 'unknown', { line: 4, text: lines[3] }],
    ['def.sh', 'unknown', 'unknown', 'unknown'],
  ]);
});

test('application [FAIL] messages inside a Python job are not shell suite results', () => {
  const lines = ['2026-11-21T23:29:11.5465207Z [FAIL] No PRs could be merged into integration branch',
    '2026-11-21T23:29:11.6122228Z [FAIL] Integration PR CI did not pass',
    'FAIL: test_real (pkg.C.test_real)', '-----', 'AssertionError: real'];
  const hits = records(['--log', '-'], lines.join('\n'));
  assert.deepEqual(hits.map(hit => hit.test_id), ['pkg.C.test_real']);
});

test('a truncated shell log keeps an observed parent even when the suite is missing', () => {
  const [hit] = records(['--log', '-'], '=== Test 9: child ===\nFAIL: mismatch');
  assert.deepEqual([hit.suite, hit.test_id, hit.subcase, hit.parent],
    ['unknown', 'unknown', 'Test 9: child', { line: 1, text: '=== Test 9: child ===' }]);
});

test('ordinary parenthesized shell text cannot invent a unittest ID', () => {
  const text = 'FAIL: timeout (retry)';
  const [hit] = records(['--log', '-'], text);
  assert.deepEqual([hit.test_id, hit.parent, hit.assertion], ['unknown', 'unknown', { line: 1, text }]);
});
