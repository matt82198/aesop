import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { applyFeedEvent, createFeedWriter, feedPath, lastJobs, parseLines } from '../chat/activity_feed.mjs';

// One shared activity feed: default path is per-user (HOME), not per-checkout.
// Run: node --import ./tests/helpers/isolated-env.mjs --test --test-force-exit --test-timeout=60000 tests/chat-activity-feed-path.test.mjs

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dirs = [];
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'aesop-feedpath-')); dirs.push(d); return d; };
process.on('exit', () => { for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch { /* best effort */ } } });

const PY = process.env.AESOP_PYTHON || 'python';

test('feedPath default is the shared per-user location under HOME', () => {
  const home = tmp();
  const env = { HOME: home, USERPROFILE: home };
  assert.equal(feedPath(env), join(home, '.aesop', 'state', 'chat', 'activity.jsonl'));
});

test('feedPath ignores AESOP_STATE_ROOT (no per-checkout split)', () => {
  const home = tmp();
  const env = { HOME: home, USERPROFILE: home, AESOP_STATE_ROOT: join(tmp(), 'checkout-state') };
  assert.equal(feedPath(env), join(home, '.aesop', 'state', 'chat', 'activity.jsonl'));
});

test('feedPath falls back to USERPROFILE when HOME is unset', () => {
  const home = tmp();
  assert.equal(feedPath({ USERPROFILE: home }), join(home, '.aesop', 'state', 'chat', 'activity.jsonl'));
});

test('AESOP_ACTIVITY_FEED overrides the default', () => {
  const home = tmp();
  const override = join(tmp(), 'custom.jsonl');
  assert.equal(feedPath({ HOME: home, AESOP_ACTIVITY_FEED: override }), override);
});

test('writer and reader agree on the path: ask writer file equals TUI tail file', () => {
  const home = tmp();
  const env = { HOME: home, USERPROFILE: home };
  const writer = createFeedWriter({ env, job: 'agree-1' });
  writer.start({ from: 'probe', model: 'm', prompt: 'hi' });
  assert.equal(writer.file, feedPath(env));
  const events = lastJobs(feedPath(env), 5);
  assert.equal(events.length, 1);
  assert.equal(events[0].job, 'agree-1');
});

test('a Python-written event is parsed by the JS reader and rendered in the transcript', () => {
  const home = tmp();
  const feed = join(home, '.aesop', 'state', 'chat', 'activity.jsonl');
  // The Python emitter resolves the same default path under this HOME, then writes start/chunk/end.
  const code = [
    'import os, sys',
    `sys.path.insert(0, ${JSON.stringify(join(root, 'driver'))})`,
    'import activity_feed as af',
    `env = {"HOME": ${JSON.stringify(home)}, "AESOP_FROM": "codex"}`,
    'print(af.feed_path(env))',
    'w = af.ActivityFeed(env=env, job="py-job-1")',
    'w.start(model="ollama/qwen2.5-coder:14b", prompt="hello from python", stdin="")',
    'w.chunk("pong from python")',
    'w.end(status="ok", exit_code=0, elapsed_ms=3)',
  ].join('\n');
  const out = execFileSync(PY, ['-I', '-c', code], {
    encoding: 'utf-8', errors: 'replace', env: { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, HOME: home, USERPROFILE: home }, timeout: 60000,
  });
  assert.equal(out.split(/\r?\n/)[0].trim(), feed);
  assert.ok(existsSync(feed), 'python wrote the shared feed file');

  const events = parseLines(readFileSync(feed, 'utf8'));
  assert.deepEqual(events.map((e) => e.type), ['start', 'chunk', 'end']);
  const jobs = new Map();
  const transcript = [];
  for (const ev of events) applyFeedEvent(jobs, transcript, ev);
  assert.equal(transcript.length, 1);
  const entry = transcript[0];
  assert.equal(entry.role, 'feed');
  assert.equal(entry.from, 'codex');
  assert.equal(entry.model, 'ollama/qwen2.5-coder:14b');
  assert.equal(entry.prompt, 'hello from python');
  assert.equal(entry.text, 'pong from python');
  assert.equal(entry.status, 'ok');
  assert.equal(entry.exit, 0);
});
