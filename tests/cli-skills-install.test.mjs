// Tests for automatic skill installation during scaffold
// Contract under test:
//  - scaffold installs skills/*/ into the skills home (Claude Code only scans
//    ~/.claude/skills/; a skill left in the scaffolded ./skills/ is undiscoverable)
//  - re-scaffolding is idempotent: identical skills are reported, not recopied blindly
//  - a locally modified skill is PRESERVED unless --force is passed
//  - --no-skills opts out entirely
//  - dependency manifests ship into the target so --install-deps has something to read
//
// Every test ALSO redirects AESOP_SKILLS_HOME into its own per-test temp dir on
// top of that -- belt-and-suspenders, since this file is literally the one whose
// forgotten redirect caused the real-~/.claude-overwrite incident (#831) the
// isolated-env fixture exists to prevent structurally.
//
// Scaffold budget (PR #784 follow-up): a full scaffold (template copy + git
// init/add/commit + skill install) costs 15-40s+ on CI's windows-shard(0)
// (Windows Defender real-time scanning on every file touched), and this file
// used to run 10 of them serially, summing past the file-level 180s timeout.
// Two structural changes keep it well under budget without touching that
// 180s number: (1) every scaffold here passes --no-git except exactly ONE
// (the shared `before` fixture below), since no test in this file asserts on
// git state -- --no-git skips the git init/add/commit path entirely
// (bin/cli.js's initializeGitRepo short-circuits on the flag); (2) tests that
// only READ an already-installed skills home (the "installs skills" and
// "does not write to the real home" checks) share that one `before`-built
// scaffold instead of each building their own. Tests that MUTATE a skills
// home (writing a local edit, re-scaffolding with --force) keep independent
// scaffolds, since sharing would make them interfere with each other.
//
// Run: node --test tests/cli-skills-install.test.mjs

// Harness-level HOME isolation (tests/helpers/isolated-env.mjs, #831): a
// side-effect import, not merely a package.json/ci.yml --import flag, so this
// file is structurally isolated even when run directly (as above) and not only
// through the npm test/test:node scripts.
import './helpers/isolated-env.mjs';

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const CLI = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', 'bin', 'cli.js'
);

function createTestDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-skills-test-'));
}

function cleanupTestDir(dir) {
  try {
    if (fs.existsSync(dir)) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  } catch (e) {
    // Ignore cleanup errors
  }
}

// Kill the WHOLE process tree rooted at pid, not just the immediate child.
// Windows class of bug (PR #784): a grandchild (e.g. a git subprocess cli.js
// spawned) can hold the parent's inherited stdio pipes open even after the
// immediate child is signalled, which previously left spawnSync's own
// `timeout`+`killSignal` blocked waiting on those pipes well past its own
// deadline -- observed as a full 180000ms node --test file-level timeout
// with zero subtest output. `taskkill /T /F` terminates the whole tree in
// one call so no descendant can keep a pipe open.
function killProcessTree(pid) {
  if (process.platform === 'win32') {
    try {
      spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
    } catch (e) {
      // best-effort; the deadline promise resolves regardless
    }
  } else {
    try {
      process.kill(pid, 'SIGKILL');
    } catch (e) {
      // process may already be gone
    }
  }
}

// Scaffold into `targetDir` with the skills home redirected at `skillsHome`.
//
// Diagnostic + mechanism fix (PR #784): this file was observed on CI's
// windows-shard(0) as a full 180000ms file-level timeout with ZERO subtest
// output. node's TAP reporter only prints a "# Subtest:" line once that
// test's callback RETURNS, and the old spawnSync-based scaffold() only
// handed stdout/stderr back to the caller once the child EXITED -- so a
// hung first call produced literally no signal anywhere. This async
// `spawn`-based version (a) streams stdout/stderr incrementally as it
// arrives instead of only on exit, so partial output survives a hang,
// (b) never inherits a pipe nothing reads (`stdio: ['ignore','pipe','pipe']`),
// (c) enforces its own bounded deadline (<=60s) and on expiry kills the
// WHOLE process tree via taskkill /T /F (not just the immediate child) so a
// pipe-holding grandchild can never freeze the test file, and (d) keeps the
// scaffold#N start/done stderr markers so a CI log names which call and how
// long it ran even when everything else is silent.
let scaffoldCallIndex = 0;
function scaffold(targetDir, skillsHome, extraArgs = []) {
  const timeoutMs = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 60000;
  const deadlineMs = Math.min(timeoutMs, 60000);
  const callId = ++scaffoldCallIndex;
  const t0 = Date.now();
  process.stderr.write(`[cli-skills-install] scaffold#${callId} start targetDir=${targetDir} deadline=${deadlineMs}ms args=${extraArgs.join(' ')}\n`);

  return new Promise((resolve) => {
    const child = spawn(
      process.execPath,
      [CLI, targetDir, '--name', 'skills-test', '--yes', ...extraArgs],
      {
        cwd: path.dirname(targetDir),
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, AESOP_SKILLS_HOME: skillsHome }
      }
    );

    let stdout = '';
    let stderr = '';
    let settled = false;
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      process.stderr.write(`[cli-skills-install] scaffold#${callId} DEADLINE EXCEEDED at ${Date.now() - t0}ms pid=${child.pid}; killing process tree\n`);
      killProcessTree(child.pid);
    }, deadlineMs);

    child.stdout.on('data', (chunk) => {
      stdout += chunk;
      process.stdout.write(`[cli-skills-install] scaffold#${callId} stdout> ${chunk}`);
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
      process.stderr.write(`[cli-skills-install] scaffold#${callId} stderr> ${chunk}`);
    });

    const finish = (status, signal, error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const elapsed = Date.now() - t0;
      process.stderr.write(`[cli-skills-install] scaffold#${callId} done elapsed=${elapsed}ms status=${status} signal=${signal} timedOut=${timedOut}\n`);
      resolve({ status, signal, stdout, stderr, error });
    };

    child.on('error', (err) => {
      finish(null, null, err);
    });

    child.on('close', (code, signal) => {
      if (timedOut) {
        finish(null, signal || 'SIGKILL', Object.assign(new Error('scaffold deadline exceeded'), { code: 'ETIMEDOUT' }));
      } else {
        finish(code, signal, null);
      }
    });
  });
}

// Shared fixture: the ONE full scaffold in this file that keeps git ENABLED
// (no --no-git), so the installer+git integration path stays covered by
// something. Built once; the two tests below that only READ its result
// (never mutate it) reuse it instead of each building their own.
let shared = null;

before(async () => {
  const base = createTestDir();
  const skillsHome = path.join(base, 'skills-home');
  const target = path.join(base, 'fleet');
  const sentinel = path.join(os.homedir(), '.claude', 'skills');
  const sentinelBefore = fs.existsSync(sentinel)
    ? fs.readdirSync(sentinel).sort().join(',')
    : '<absent>';

  const res = await scaffold(target, skillsHome);

  const sentinelAfter = fs.existsSync(sentinel)
    ? fs.readdirSync(sentinel).sort().join(',')
    : '<absent>';

  shared = { base, skillsHome, target, res, sentinel, sentinelBefore, sentinelAfter };
});

after(() => {
  if (shared) cleanupTestDir(shared.base);
});

test('scaffold installs skills into the skills home', () => {
  const { res, skillsHome } = shared;
  assert.equal(res.status, 0, `CLI exited ${res.status}: ${res.stderr}`);
  assert.ok(fs.existsSync(skillsHome), 'skills home should be created');

  // power and buildsystem are the two the orchestrator cannot run without
  for (const skill of ['power', 'buildsystem']) {
    const skillFile = path.join(skillsHome, skill, 'SKILL.md');
    assert.ok(fs.existsSync(skillFile), `${skill}/SKILL.md should be installed`);
    assert.ok(
      fs.readFileSync(skillFile, 'utf8').length > 0,
      `${skill}/SKILL.md should not be empty`
    );
  }
});

test('--no-skills skips installation entirely', async () => {
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    const res = await scaffold(path.join(base, 'fleet'), skillsHome, ['--no-skills', '--no-git']);

    assert.equal(res.status, 0, `CLI exited ${res.status}: ${res.stderr}`);
    assert.ok(
      !fs.existsSync(path.join(skillsHome, 'power')),
      'no skill should be installed when --no-skills is passed'
    );
    assert.match(res.stdout, /--no-skills/, 'should report that it skipped');
  } finally {
    cleanupTestDir(base);
  }
});

test('re-scaffolding over identical skills is idempotent', async () => {
  // Reuses the shared fixture's already-populated skillsHome as the "first"
  // scaffold (built once in `before`, with real skills installed) -- only
  // one NEW scaffold call is needed here to exercise the idempotent path.
  const base = createTestDir();
  try {
    const res = await scaffold(path.join(base, 'fleet-b'), shared.skillsHome, ['--no-git']);

    assert.equal(res.status, 0, `CLI exited ${res.status}: ${res.stderr}`);
    assert.match(
      res.stdout,
      /already installed and identical/,
      'second run should recognize identical skills'
    );
  } finally {
    cleanupTestDir(base);
  }
});

test('a locally modified skill is preserved without --force', async () => {
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    await scaffold(path.join(base, 'fleet-a'), skillsHome, ['--no-git']);

    const powerSkill = path.join(skillsHome, 'power', 'SKILL.md');
    const mine = '# my own power skill\n';
    fs.writeFileSync(powerSkill, mine);

    const res = await scaffold(path.join(base, 'fleet-b'), skillsHome, ['--no-git']);

    assert.equal(res.status, 0, `CLI exited ${res.status}: ${res.stderr}`);
    assert.equal(
      fs.readFileSync(powerSkill, 'utf8'),
      mine,
      'local edits must not be silently overwritten'
    );
    // The divergence warning goes to stderr (console.warn)
    assert.match(res.stderr, /Kept your existing version of: .*power/, 'should name the skill it kept');
    assert.doesNotMatch(
      res.stdout,
      /already installed and identical: .*power/,
      'a modified skill must not be reported as identical'
    );
  } finally {
    cleanupTestDir(base);
  }
});

test('--force overwrites a locally modified skill', async () => {
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    await scaffold(path.join(base, 'fleet-a'), skillsHome, ['--no-git']);

    const powerSkill = path.join(skillsHome, 'power', 'SKILL.md');
    fs.writeFileSync(powerSkill, '# my own power skill\n');

    const res = await scaffold(path.join(base, 'fleet-b'), skillsHome, ['--force', '--no-git']);

    assert.equal(res.status, 0, `CLI exited ${res.status}: ${res.stderr}`);
    assert.notEqual(
      fs.readFileSync(powerSkill, 'utf8'),
      '# my own power skill\n',
      '--force should restore the shipped skill'
    );
  } finally {
    cleanupTestDir(base);
  }
});

test('dependency manifests ship into the scaffolded target', async () => {
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    const target = path.join(base, 'fleet');
    const res = await scaffold(target, skillsHome, ['--no-skills', '--no-git']);

    assert.equal(res.status, 0, `CLI exited ${res.status}: ${res.stderr}`);
    for (const manifest of ['requirements.txt', 'requirements-dev.txt']) {
      assert.ok(
        fs.existsSync(path.join(target, manifest)),
        `${manifest} must ship so --install-deps has something to read`
      );
    }
  } finally {
    cleanupTestDir(base);
  }
});

test('scaffold does not write to the real home when AESOP_SKILLS_HOME is set', () => {
  // Reuses the shared fixture's before/after sentinel snapshot (taken around
  // its one scaffold call in `before`) instead of running a second scaffold
  // purely to re-check the same thing.
  assert.equal(shared.sentinelAfter, shared.sentinelBefore, 'real ~/.claude/skills must be untouched');
});
