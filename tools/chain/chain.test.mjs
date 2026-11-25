import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { board } from './board-lib.mjs';
import { feeds } from './feeds-lib.mjs';
import { detail, failures, pulls } from './github.mjs';
import { worktrees } from './worktrees-lib.mjs';
import { BOARD_BYTES, MAX_BYTES, bytes, command } from './runtime.mjs';

async function fixture(t) {
  const dir = await mkdtemp(join(tmpdir(), 'chain-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const inbox = { path: join(dir, 'inbox'), offset: 0 };
  const activity = { path: join(dir, 'activity'), offset: 0 };
  await Promise.all([writeFile(inbox.path, ''), writeFile(activity.path, '')]);
  return { dir, inbox, activity, repos: ['example/one', 'example/two'], checkout: dir,
    pr: { repo: 'example/one', number: 7 }, run: { repo: 'example/one', id: 9 } };
}

const pr = { number: 7, headRefOid: 'f'.repeat(40), mergeStateStatus: 'BLOCKED',
  statusCheckRollup: [{ name: 'lint', conclusion: 'SUCCESS' },
    { name: 'build', status: 'IN_PROGRESS' }, { context: 'legacy', state: 'FAILURE' }] };
const listing = JSON.stringify([pr]);

test('T1 retains SHA, merge state and non-success checks across check types', async t => {
  const c = await fixture(t);
  const out = await pulls(c, async (file, args) => {
    assert.equal(file, 'gh');
    assert.deepEqual(args.slice(0, 3), ['pr', 'list', '--repo']);
    return args[3] === c.repos[0] ? listing : '[]';
  });
  assert.deepEqual(out.data, [[c.repos[0], 7, pr.headRefOid, 'BLOCKED', 3,
    [['build', 'IN_PROGRESS'], ['legacy', 'FAILURE']]]]);
  assert.deepEqual(out.emptyRepos, [c.repos[1]]);
});

test('one failed repo remains distinguishable from a successful empty repo', async t => {
  const c = await fixture(t);
  for (const diagnostic of ['gh timeout', 'gh 4 authentication required', 'gh 1 service unavailable']) {
    const out = await pulls(c, async (_, args) => {
      if (args[3] === c.repos[0]) throw new Error(diagnostic);
      return '[]';
    });
    assert.equal(out.exitCode, 1);
    assert.deepEqual(out.emptyRepos, [c.repos[1]]);
    assert.deepEqual(out.errors, [{ source: c.repos[0], error: diagnostic }]);
  }
  assert.equal((await pulls(c, async () => 'not JSON')).errors.length, 2);
});

test('T2 retains merged commit, named checks and the first line of the latest three comments', async t => {
  const c = await fixture(t);
  const out = await detail(c, async (file, args) => {
    assert.equal(file, 'gh');
    assert.equal(args[2], '7');
    return JSON.stringify({ state: 'MERGED', mergeCommit: { oid: 'abcd' },
      statusCheckRollup: pr.statusCheckRollup, comments: ['old', 'one\r\nmore', '', 'three\nrest'].map(body => ({ body })) });
  });
  assert.deepEqual(out.data, { state: 'MERGED', mergeCommit: 'abcd',
    checks: [['lint', 'SUCCESS'], ['build', 'IN_PROGRESS'], ['legacy', 'FAILURE']],
    comments: ['one', '', 'three'] });
});

test('T3 uses byte offsets and replays a partial line only after newline', async t => {
  const c = await fixture(t);
  const prefix = '🦊\r\n';
  c.inbox.offset = Buffer.byteLength(prefix);
  await writeFile(c.inbox.path, prefix + 'seen\r\npending');
  await writeFile(c.activity.path, '{"kind":"chunk"}\n{"kind":"peer","prompt":"🦊"}\n');
  const first = await feeds(c);
  assert.equal(first.exitCode, 0);
  assert.equal(first.truncated, true);
  assert.deepEqual(first.data.map(s => s.rows), [['seen'], [{ prompt: '🦊' }]]);
  c.inbox.offset = first.data[0].next;
  await writeFile(c.inbox.path, prefix + 'seen\r\npending\n');
  assert.deepEqual((await feeds(c)).data[0].rows, ['pending']);
});

test('T3 pages complete rows without advancing over hidden messages, exposes bad sources', async t => {
  const c = await fixture(t);
  const line = '中'.repeat(3000);
  await writeFile(c.inbox.path, line + '\n' + line + '\n');
  await writeFile(c.activity.path, 'broken\n');
  const first = await feeds(c);
  assert.equal(first.exitCode, 1);
  assert.equal(first.truncated, true);
  assert.equal(first.data[0].next, Buffer.byteLength(line + '\n'));
  assert.equal(first.errors[0].source, 'activity');
  c.inbox.offset = first.data[0].next;
  assert.deepEqual((await feeds(c)).data[0].rows, [line]);
  for (const offset of [1, 100000]) {
    c.inbox.offset = offset;
    assert.equal((await feeds(c)).errors.length, 2);
  }
});

test('T4 counts a rename once, detached, absent upstream, inaccessible and bare trees', async t => {
  const c = await fixture(t);
  const out = await worktrees(c, async (file, args) => {
    assert.equal(file, 'git');
    assert.ok(args.includes('--no-optional-locks'));
    if (args.includes('worktree')) return 'worktree A B\0\0worktree C\0detached\0\0worktree D\0bare\0\0worktree missing\0\0';
    if (args.includes('A B')) return '# branch.head topic\0# branch.upstream origin/topic\0# branch.ab +2 -3\0' + '2 R. new\0old\0? one\0';
    if (args.includes('C')) return '# branch.head (detached)\0';
    throw new Error('git 128 unavailable');
  });
  assert.equal(out.exitCode, 1);
  assert.deepEqual(out.data, [
    { source: 'A B', branch: 'topic', upstream: 'origin/topic', ahead: 2, behind: 3, dirty: 2 },
    { source: 'C', branch: '(detached)', upstream: null, ahead: null, behind: null, dirty: 0 },
    { source: 'D', bare: true }]);
  assert.equal(out.errors[0].source, 'missing');
});

test('T5 keeps a late failure before cap, skips passing tests, marks clipping', async t => {
  const c = await fixture(t);
  const lines = ['ok 1 - good', ...Array(30).fill('setup'), 'not ok 2 - broken', '##[error]' + '中'.repeat(2000)];
  const out = await failures(c, async (_, args) => {
    assert.ok(args.includes('--log'));
    return lines.join('\n');
  });
  assert.equal(out.truncated, true);
  assert.equal(out.data.length, 16);
  assert.deepEqual(out.data.at(-2), [32, lines[31]]);
  assert.equal(out.data.at(-1)[1].length, 1024);
  assert.ok(!out.data.some(([n]) => n === 1));
});

test('board starts independent work concurrently and keeps successful sections on timeout', async t => {
  const c = await fixture(t);
  let enteredGit;
  const gitStarted = new Promise(resolve => { enteredGit = resolve; });
  const run = async (file, args) => {
    if (file === 'git') {
      enteredGit();
      return args.includes('worktree') ? 'worktree A\0\0' : '# branch.head topic\0? dirty\0';
    }
    await gitStarted;
    if (args.includes(c.repos[1])) throw new Error('gh timeout');
    return listing;
  };
  const out = await board(c, run);
  assert.equal(out.exitCode, 1);
  assert.equal(out.data.pulls.open, 1);
  assert.match(out.data.pulls.firstError, /example\/two: gh timeout/);
  assert.equal(out.data.worktrees.changed, 1);
  assert.equal(out.data.feeds.rows.length, 2);
  assert.ok(bytes(out) + 1 <= BOARD_BYTES);
});

test('multibyte output stays inside both byte caps', async t => {
  const c = await fixture(t);
  const many = JSON.stringify(Array.from({ length: 1200 }, (_, i) => ({ ...pr, number: i + 1,
    statusCheckRollup: [{ name: '中'.repeat(60), status: 'QUEUED' }] })));
  const run = async (file, args) => file === 'gh' ? many :
    args.includes('worktree') ? 'worktree A\0\0' : '# branch.head main\0';
  const out = await pulls(c, run);
  assert.equal(out.truncated, true);
  assert.ok(out.data.length > 0 && bytes(out) + 1 <= MAX_BYTES);
  const summary = await board(c, run);
  assert.equal(summary.truncated, true);
  assert.ok(bytes(summary) + 1 <= BOARD_BYTES);
});

test('real execFile reports timeout and child exit code with diagnostic', async () => {
  await assert.rejects(command(process.execPath, ['-e', 'setTimeout(() => {}, 5000)'], { timeoutMs: 50 }), /timeout/);
  await assert.rejects(command(process.execPath, ['-e', 'console.error("login required");process.exit(4)'], {}), /4 login required/);
});

test('CLI exits nonzero with bounded JSON on invalid config and missing executable', async t => {
  const c = await fixture(t);
  const config = join(c.dir, 'config.json');
  const cli = fileURLToPath(new URL('./pulls.mjs', import.meta.url));
  for (const input of ['invalid', JSON.stringify(c)]) {
    await writeFile(config, input);
    assert.throws(() => execFileSync(process.execPath, [cli, config], {
      env: { ...process.env, PATH: '' }, stdio: 'pipe' }), err => {
      const out = JSON.parse(err.stdout);
      return err.status === 1 && out.exitCode === 1 && out.errors.length > 0 &&
        typeof out.truncated === 'boolean' && err.stdout.length <= MAX_BYTES;
    });
  }
});
