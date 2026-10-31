// Tests for CLI and doctor defect fixes
// DEFECT 1: aesop_root should be absolute path, not relative
// DEFECT 2: doctor should validate config structure (required fields, paths exist)
// DEFECT 3: doctor should warn on placeholder repo URLs
// DEFECT 4: doctor should check skills files exist
// DEFECT 5: doctor should verify Node >= 18 and Python >= 3.10
// DEFECT 6: scaffold should print NEXT STEPS and actual dashboard port
//
// Run: node --test tests/cli-doctor-defects.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gitCmd, assertConfigNotPoisoned } from './helpers/git-helpers.mjs';

const CLI = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', 'bin', 'cli.js'
);

const DOCTOR = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', 'tools', 'doctor.js'
);

function runCli(targetDir, args = []) {
  const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000;
  // --no-skills: this file doesn't test skill installation; without it every
  // scaffold call would write into the real ~/.claude/skills/ (isolation contract
  // documented in cli-skills-install.test.mjs) and contend with it on CI Windows.
  const res = spawnSync(process.execPath, [CLI, targetDir, ...args, '--no-skills'], {
    encoding: 'utf8',
    cwd: path.dirname(targetDir),
    timeout,
    killSignal: 'SIGKILL'
  });
  return res;
}

function runDoctor(cwd = process.cwd()) {
  const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000;
  const res = spawnSync(process.execPath, [DOCTOR], {
    encoding: 'utf8',
    cwd,
    timeout,
    killSignal: 'SIGKILL',
    stdio: ['pipe', 'pipe', 'pipe']  // Explicitly capture stdin, stdout, stderr
  });
  return res;
}

function createTestDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-doctor-defects-'));
}


// DEFECT 1: aesop_root must be absolute, not relative
test('DEFECT 1: aesop_root in generated config is absolute path', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-1');

  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  const res = runCli(targetDir, ['--name', 'test-fleet']);
  assert.equal(res.status, 0, `Scaffold should succeed. stderr: ${res.stderr}`);

  const configPath = path.join(targetDir, 'aesop.config.json');
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

  // aesop_root must be an absolute path
  assert.ok(path.isAbsolute(config.aesop_root),
    `aesop_root should be absolute. Got: ${config.aesop_root}`);
});

// DEFECT 2: doctor should validate config structure
test('DEFECT 2: doctor validates aesop.config.json structure (repos array)', () => {
  const tempDir = createTestDir();

  fs.mkdirSync(tempDir, { recursive: true });
  gitCmd(tempDir, ['init']);
  gitCmd(tempDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(tempDir, ['config', 'user.name', 'Test User']);

  // Create config without repos array
  const badConfig = {
    description: 'test',
    aesop_root: tempDir,
    brain_root: path.join(os.homedir(), '.claude'),
    dashboard: { port: 8770 }
  };
  fs.writeFileSync(
    path.join(tempDir, 'aesop.config.json'),
    JSON.stringify(badConfig, null, 2)
  );

  const res = runDoctor(tempDir);
  assert.ok(res.stderr.includes('repos') || res.stdout.includes('repos'),
    `Doctor should report missing repos array. stderr: ${res.stderr}, stdout: ${res.stdout}`);
  assert.notEqual(res.status, 0, 'Doctor should fail for config without repos array');
});

// DEFECT 2: doctor should validate aesop_root exists
test('DEFECT 2: doctor warns when aesop_root does not exist on disk', () => {
  const tempDir = createTestDir();

  fs.mkdirSync(tempDir, { recursive: true });
  gitCmd(tempDir, ['init']);
  gitCmd(tempDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(tempDir, ['config', 'user.name', 'Test User']);

  // Create config with non-existent aesop_root
  const badConfig = {
    description: 'test',
    aesop_root: '/nonexistent/path/to/aesop',
    brain_root: path.join(os.homedir(), '.claude'),
    repos: [],
    dashboard: { port: 8770 }
  };
  fs.writeFileSync(
    path.join(tempDir, 'aesop.config.json'),
    JSON.stringify(badConfig, null, 2)
  );

  const res = runDoctor(tempDir);
  assert.ok(res.stderr.includes('aesop_root') || res.stdout.includes('aesop_root') || res.stderr.includes('does not exist') || res.stdout.includes('does not exist'),
    `Doctor should warn about missing aesop_root. stderr: ${res.stderr}, stdout: ${res.stdout}`);
});

// DEFECT 2: doctor should validate repo.path exists
test('DEFECT 2: doctor warns when repo.path does not exist on disk', () => {
  const tempDir = createTestDir();

  fs.mkdirSync(tempDir, { recursive: true });
  gitCmd(tempDir, ['init']);
  gitCmd(tempDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(tempDir, ['config', 'user.name', 'Test User']);

  // Create config with non-existent repo.path
  const badConfig = {
    description: 'test',
    aesop_root: tempDir,
    brain_root: path.join(os.homedir(), '.claude'),
    repos: [
      { name: 'test-repo', path: '/nonexistent/repo', url: 'https://example.com/test.git' }
    ],
    dashboard: { port: 8770 }
  };
  fs.writeFileSync(
    path.join(tempDir, 'aesop.config.json'),
    JSON.stringify(badConfig, null, 2)
  );

  const res = runDoctor(tempDir);
  assert.ok(res.stderr.includes('/nonexistent/repo') || res.stdout.includes('/nonexistent/repo') || res.stderr.includes('does not exist') || res.stdout.includes('does not exist'),
    `Doctor should warn about missing repo path. stderr: ${res.stderr}, stdout: ${res.stdout}`);
});

// DEFECT 3: doctor should warn on placeholder repo URLs
test('DEFECT 3: doctor warns on placeholder repo URLs (github.com/user/<name>.git)', () => {
  const tempDir = createTestDir();

  fs.mkdirSync(tempDir, { recursive: true });
  gitCmd(tempDir, ['init']);
  gitCmd(tempDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(tempDir, ['config', 'user.name', 'Test User']);

  // Create a real repo directory
  const repoDir = path.join(tempDir, 'test-repo');
  fs.mkdirSync(repoDir, { recursive: true });

  // Create config with placeholder URL
  const badConfig = {
    description: 'test',
    aesop_root: tempDir,
    brain_root: path.join(os.homedir(), '.claude'),
    repos: [
      { name: 'test-repo', path: repoDir, url: 'https://github.com/user/test-repo.git' }
    ],
    dashboard: { port: 8770 }
  };
  fs.writeFileSync(
    path.join(tempDir, 'aesop.config.json'),
    JSON.stringify(badConfig, null, 2)
  );

  const res = runDoctor(tempDir);
  assert.ok(res.stderr.includes('placeholder') || res.stdout.includes('placeholder') || res.stderr.includes('github.com/user') || res.stdout.includes('github.com/user'),
    `Doctor should warn about placeholder URLs. stderr: ${res.stderr}, stdout: ${res.stdout}`);
});

// DEFECT 5: doctor should verify Python version
test('DEFECT 5: doctor checks Python version >= 3.10', () => {
  const tempDir = createTestDir();

  fs.mkdirSync(tempDir, { recursive: true });
  gitCmd(tempDir, ['init']);
  gitCmd(tempDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(tempDir, ['config', 'user.name', 'Test User']);

  // Create minimal valid config
  const config = {
    description: 'test',
    aesop_root: tempDir,
    brain_root: path.join(os.homedir(), '.claude'),
    repos: [],
    dashboard: { port: 8770 }
  };
  fs.writeFileSync(
    path.join(tempDir, 'aesop.config.json'),
    JSON.stringify(config, null, 2)
  );

  const res = runDoctor(tempDir);
  // Should mention Python version in output (either PASS or version requirement)
  assert.ok(res.stdout.includes('Python') || res.stdout.includes('python'),
    `Doctor should report Python version. stdout: ${res.stdout}`);
});

// DEFECT 5: doctor should verify Node.js version
test('DEFECT 5: doctor checks Node.js version >= 18', () => {
  const tempDir = createTestDir();

  fs.mkdirSync(tempDir, { recursive: true });
  gitCmd(tempDir, ['init']);
  gitCmd(tempDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(tempDir, ['config', 'user.name', 'Test User']);

  // Create minimal valid config
  const config = {
    description: 'test',
    aesop_root: tempDir,
    brain_root: path.join(os.homedir(), '.claude'),
    repos: [],
    dashboard: { port: 8770 }
  };
  fs.writeFileSync(
    path.join(tempDir, 'aesop.config.json'),
    JSON.stringify(config, null, 2)
  );

  const res = runDoctor(tempDir);
  // Should mention Node version, and since we're running on Node >= 18, should be PASS
  assert.ok(res.stdout.includes('Node') || res.stdout.includes('node'),
    `Doctor should report Node version. stdout: ${res.stdout}`);
});

// DEFECT 6: scaffold should print actual dashboard port
test('DEFECT 6: scaffold prints actual dashboard port in NEXT STEPS', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-6');

  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  const res = runCli(targetDir, ['--name', 'test-fleet', '--yes']);
  assert.equal(res.status, 0, `Scaffold should succeed. stderr: ${res.stderr}`);

  // Should print NEXT STEPS
  const output = res.stdout + res.stderr;
  assert.ok(output.includes('NEXT') || output.includes('Next') || output.includes('next'),
    `Output should mention NEXT STEPS. stdout: ${res.stdout}`);

  // Should print actual port (8770 is default, might be different if in use)
  assert.ok(output.includes('localhost:') || output.includes('port '),
    `Output should mention port. stdout: ${res.stdout}`);
});

// DEFECT 6: scaffold should mention config, skills, and URLs in NEXT STEPS
test('DEFECT 6: scaffold NEXT STEPS should guide customization (domains, URLs, skills)', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-6b');

  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  const res = runCli(targetDir, ['--name', 'test-fleet', '--yes']);
  assert.equal(res.status, 0, `Scaffold should succeed. stderr: ${res.stderr}`);

  const output = res.stdout + res.stderr;
  // Should mention CLAUDE.md customization
  assert.ok(output.includes('CLAUDE.md') || output.includes('domains'),
    `Output should guide on CLAUDE.md/domains. stdout: ${res.stdout}`);
});

// DEFECT 6b: the scaffold copy must never ship Python bytecode, and must not
// race concurrent writers of ui/__pycache__. The CLI copies its own package
// root (templateRoot = bin/..), so the source tree is a temp package holding
// bin/cli.js plus a ui/ that contains __pycache__. A dangling symlink named
// *.pyc stands in for a .pyc that vanishes between readdir and stat: the old
// copy statted every listed entry and died with ENOENT on it.
test('DEFECT 6b: scaffold copy skips __pycache__ and *.pyc (no ENOENT race)', () => {
  const tempDir = createTestDir();
  const pkgDir = path.join(tempDir, 'pkg');
  const targetDir = path.join(tempDir, 'fleet-6b-pyc');
  const uiDir = path.join(pkgDir, 'ui');
  const pycDir = path.join(uiDir, '__pycache__');

  fs.mkdirSync(path.join(pkgDir, 'bin'), { recursive: true });
  fs.copyFileSync(CLI, path.join(pkgDir, 'bin', 'cli.js'));
  // bin/cli.js requires stdio/tty-decision.js at load time; a real install ships stdio/.
  const stdioSrc = path.join(path.dirname(CLI), '..', 'stdio');
  fs.mkdirSync(path.join(pkgDir, 'stdio'), { recursive: true });
  for (const f of fs.readdirSync(stdioSrc)) {
    if (/\.m?js$/.test(f)) fs.copyFileSync(path.join(stdioSrc, f), path.join(pkgDir, 'stdio', f));
  }
  fs.writeFileSync(path.join(pkgDir, 'package.json'), JSON.stringify({ name: 'aesop-fixture', version: '0.0.0-test' }));
  fs.mkdirSync(pycDir, { recursive: true });
  fs.writeFileSync(path.join(uiDir, 'app.py'), 'print("ui")\n');
  fs.writeFileSync(path.join(pycDir, 'app.cpython-312.pyc'), Buffer.from([0, 1, 2, 3]));
  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  // Deterministic race: a preload shim makes statSync of any *.pyc throw ENOENT,
  // exactly what a file deleted between readdir and stat produces. Dangling
  // symlinks are not usable here (EPERM on Windows without developer mode).
  const shimPath = path.join(tempDir, 'vanish-shim.cjs');
  fs.writeFileSync(shimPath, [
    "const fs = require('fs');",
    'const realStat = fs.statSync;',
    'fs.statSync = function (p, ...rest) {',
    "  if (String(p).endsWith('.pyc')) { const e = new Error('ENOENT: vanished, ' + p); e.code = 'ENOENT'; throw e; }",
    '  return realStat.call(this, p, ...rest);',
    '};',
    ''
  ].join('\n'));

  const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000;
  const res = spawnSync(process.execPath, [path.join(pkgDir, 'bin', 'cli.js'), targetDir, '--name', 'test-fleet', '--yes', '--no-skills'], {
    encoding: 'utf8',
    cwd: tempDir,
    timeout,
    killSignal: 'SIGKILL',
    env: { ...process.env, NODE_OPTIONS: `--require "${shimPath.split(path.sep).join("/")}"` }
  });
  assert.equal(res.status, 0, `Scaffold must succeed while ui/__pycache__ holds a vanishing pyc. stderr: ${res.stderr}`);
  assert.ok(fs.existsSync(path.join(targetDir, 'ui', 'app.py')), 'ui/app.py must be copied');

  const shipped = [];
  const walk = dir => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(p);
      else if (entry.name.endsWith('.pyc')) shipped.push(p);
    }
  };
  walk(path.join(targetDir, 'ui'));
  assert.deepEqual(shipped, [], 'scaffold must not ship .pyc bytecode');
  assert.equal(fs.existsSync(path.join(targetDir, 'ui', '__pycache__')), false,
    'scaffold must not create ui/__pycache__ in the target');
});

// DEFECT 7: the pre-push hook check must resolve the hooks directory through git.
// In a linked worktree .git is a FILE ("gitdir: <primary>/.git/worktrees/<name>") and
// hooks live in the common dir; core.hooksPath moves them elsewhere entirely.
function runDoctorJsonHookRow(cwd) {
  const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000;
  const res = spawnSync(process.execPath, [DOCTOR, '--json'], {
    encoding: 'utf8',
    cwd,
    timeout,
    killSignal: 'SIGKILL'
  });
  let parsed;
  try {
    parsed = JSON.parse(res.stdout);
  } catch (e) {
    assert.fail(`doctor --json must emit JSON. stdout: ${res.stdout} stderr: ${res.stderr}`);
  }
  const row = parsed.checks.find(c => c.label === 'Git pre-push hook installed');
  assert.ok(row, `hook check row missing from doctor output: ${JSON.stringify(parsed.checks)}`);
  return row;
}

function makePrimaryWithLinkedWorktree() {
  const root = fs.realpathSync.native(createTestDir());
  const primary = path.join(root, 'primary');
  const wt = path.join(root, 'wt');
  fs.mkdirSync(primary, { recursive: true });
  gitCmd(primary, ['init']);
  gitCmd(primary, ['config', 'user.email', 'test@example.com']);
  gitCmd(primary, ['config', 'user.name', 'Test User']);
  gitCmd(primary, ['commit', '--allow-empty', '-m', 'init']);
  gitCmd(primary, ['worktree', 'add', wt, '-b', 'wt-branch']);
  return { root, primary, wt };
}

test('DEFECT 7: doctor finds the pre-push hook from a linked worktree (hook in common hooks dir)', () => {
  const { primary, wt } = makePrimaryWithLinkedWorktree();
  // Precondition: a linked worktree's .git is a file, not a directory.
  assert.ok(fs.statSync(path.join(wt, '.git')).isFile(), 'linked worktree .git must be a file');

  fs.mkdirSync(path.join(primary, '.git', 'hooks'), { recursive: true });
  fs.writeFileSync(path.join(primary, '.git', 'hooks', 'pre-push'), '#!/bin/sh\nexit 0\n');

  const wtRow = runDoctorJsonHookRow(wt);
  assert.equal(wtRow.passed, true,
    `worktree doctor must PASS the hook check. hint: ${wtRow.hint}`);

  const primaryRow = runDoctorJsonHookRow(primary);
  assert.equal(primaryRow.passed, true,
    `primary doctor must PASS the hook check. hint: ${primaryRow.hint}`);
});

test('DEFECT 7: doctor honours core.hooksPath for a linked worktree', () => {
  const { root, primary, wt } = makePrimaryWithLinkedWorktree();
  const custom = path.join(root, 'custom-hooks');
  fs.mkdirSync(custom, { recursive: true });
  fs.writeFileSync(path.join(custom, 'pre-push'), '#!/bin/sh\nexit 0\n');
  gitCmd(primary, ['config', 'core.hooksPath', custom]);

  const row = runDoctorJsonHookRow(wt);
  assert.equal(row.passed, true,
    `worktree doctor must PASS when core.hooksPath holds the hook. hint: ${row.hint}`);
});

test('DEFECT 7: core.hooksPath set to an empty dir FAILS even if .git/hooks has a hook (no blind common-dir lookup)', () => {
  const { root, primary, wt } = makePrimaryWithLinkedWorktree();
  fs.mkdirSync(path.join(primary, '.git', 'hooks'), { recursive: true });
  fs.writeFileSync(path.join(primary, '.git', 'hooks', 'pre-push'), '#!/bin/sh\nexit 0\n');
  const empty = path.join(root, 'empty-hooks');
  fs.mkdirSync(empty, { recursive: true });
  gitCmd(primary, ['config', 'core.hooksPath', empty]);

  const row = runDoctorJsonHookRow(wt);
  assert.equal(row.passed, false,
    'doctor must FAIL: git will not run .git/hooks/pre-push once core.hooksPath points elsewhere');
});

test('DEFECT 7: when git cannot answer the hook check FAILS with the git error (fail closed)', () => {
  const root = fs.realpathSync.native(createTestDir());
  // A .git FILE pointing at nothing: checkGitRepo sees a .git entry, git cannot resolve it.
  fs.writeFileSync(path.join(root, '.git'), `gitdir: ${path.join(root, 'missing-gitdir')}\n`);

  // Derive the expectation from git itself: run the same command the product runs
  // (tools/doctor.js resolveHooksDir -> `git rev-parse --git-path hooks`) in the broken
  // fixture and take its stderr. Git's wording varies by platform and version, so the
  // test must not hard-code it.
  const gitSays = spawnSync('git', ['rev-parse', '--git-path', 'hooks'], {
    cwd: root,
    encoding: 'utf8',
    timeout: 5000
  });
  assert.notEqual(gitSays.status, 0, 'fixture must make git fail to answer');
  const gitError = (gitSays.stderr || '').trim().split('\n')[0];
  assert.ok(gitError.length > 0, 'git must write an error to stderr for the broken fixture');

  const row = runDoctorJsonHookRow(root);
  assert.equal(row.passed, false, 'an unanswerable git lookup must FAIL, never PASS');
  assert.ok(row.hint.startsWith('Could not resolve git hooks directory: '),
    `hint must start with the product prefix. hint: ${row.hint}`);
  assert.ok(row.hint.includes(gitError),
    `hint must carry git's own error text (${JSON.stringify(gitError)}). hint: ${row.hint}`);
});
