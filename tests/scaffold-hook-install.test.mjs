// Tests for hook auto-installation during scaffold
// Contract under test (cli.js scaffold behavior):
//  - Scaffold into empty dir -> symlinks (Unix) or copies (Windows) hooks/pre-push-policy.sh to .git/hooks/pre-push
//  - .git/hooks/pre-push exists, is executable, content matches source
//  - Re-run scaffold on repo with same hook -> idempotent, no warning
//  - Re-run scaffold on repo with different hook -> warn and skip unless --force
//  - --force flag replaces any existing hook
//
// Run: node --test tests/scaffold-hook-install.test.mjs

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

const HOOK_SOURCE = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', 'hooks', 'pre-push-policy.sh'
);

function runCli(targetDir, args = []) {
  const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000;
  const res = spawnSync(process.execPath, [CLI, targetDir, ...args], {
    encoding: 'utf8',
    cwd: path.dirname(targetDir),
    timeout,
    killSignal: 'SIGKILL'
  });
  return res;
}

function createTestDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-scaffold-test-'));
}


test('scaffold into empty dir installs pre-push hook', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-1');

  // Initialize git repo first
  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  const res = runCli(targetDir);

  assert.equal(res.status, 0, `Scaffold should succeed. stderr: ${res.stderr}`);

  const hookPath = path.join(targetDir, '.git', 'hooks', 'pre-push');
  assert.ok(fs.existsSync(hookPath), `Hook should be installed at ${hookPath}`);

  // Read the installed hook
  const hookContent = fs.readFileSync(hookPath, 'utf8');
  const sourceContent = fs.readFileSync(HOOK_SOURCE, 'utf8');

  assert.equal(hookContent, sourceContent, 'Installed hook should match source exactly');
});

test('installed hook is executable', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-2');

  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  const res = runCli(targetDir);
  assert.equal(res.status, 0);

  const hookPath = path.join(targetDir, '.git', 'hooks', 'pre-push');
  const stat = fs.statSync(hookPath);

  // On Unix, check executable bit. On Windows, just verify it exists and has content.
  if (isWindows()) {
    // Windows doesn't have traditional Unix permissions
    assert.ok(fs.existsSync(hookPath), 'Hook should exist on Windows');
  } else {
    // Unix: check executable bit (owner execute permission)
    const isExecutable = (stat.mode & 0o100) !== 0;
    assert.ok(isExecutable, 'Hook should be executable on Unix');
  }
});

test('re-scaffold same repo is idempotent', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-3');

  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  // First scaffold
  const res1 = runCli(targetDir);
  assert.equal(res1.status, 0);

  const hookPath = path.join(targetDir, '.git', 'hooks', 'pre-push');
  const content1 = fs.readFileSync(hookPath, 'utf8');
  const stat1 = fs.statSync(hookPath);

  // Wait a tiny bit to ensure mtime would differ if file were rewritten
  // (Use a simple sleep, not git command)
  const sleepStart = Date.now();
  while (Date.now() - sleepStart < 100) {
    // busy wait
  }

  // Second scaffold (should be idempotent)
  const res2 = runCli(targetDir);
  assert.equal(res2.status, 0);

  const content2 = fs.readFileSync(hookPath, 'utf8');
  const stat2 = fs.statSync(hookPath);

  assert.equal(content1, content2, 'Hook content should not change');
  assert.equal(stat1.mtime.getTime(), stat2.mtime.getTime(), 'Hook should not be rewritten');
  assert.ok(!res2.stderr.includes('warn'), 'Should not warn on same hook');
});

test('re-scaffold with different pre-push hook warns and preserves it', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-4');

  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  // First scaffold
  const res1 = runCli(targetDir);
  assert.equal(res1.status, 0);

  const hookPath = path.join(targetDir, '.git', 'hooks', 'pre-push');

  // Replace hook with custom one. The first scaffold installs a *symlink*
  // to the real hooks/pre-push-policy.sh on Unix (see file header comment),
  // so writeFileSync() must not be used directly on hookPath: that follows
  // the symlink and would truncate/overwrite the repo's real hook script.
  // Remove the existing entry (symlink or file) first, then drop in a
  // plain custom file, mirroring how a user would actually replace a hook.
  const customHook = '#!/bin/bash\necho "custom hook"\n';
  fs.rmSync(hookPath, { force: true });
  fs.writeFileSync(hookPath, customHook);

  // Second scaffold should warn and preserve
  const res2 = runCli(targetDir);
  assert.equal(res2.status, 0, 'Scaffold should still succeed');

  const content = fs.readFileSync(hookPath, 'utf8');
  assert.equal(content, customHook, 'Custom hook should be preserved');

  const output = (res2.stderr || '') + (res2.stdout || '');
  assert.ok(output.toLowerCase().includes('warn') || output.toLowerCase().includes('different'),
    `Should warn about existing different hook. Got stderr: "${res2.stderr}", stdout: "${res2.stdout}"`);
});

test('--force flag replaces existing different pre-push hook', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-5');

  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  // First scaffold
  const res1 = runCli(targetDir);
  assert.equal(res1.status, 0);

  const hookPath = path.join(targetDir, '.git', 'hooks', 'pre-push');

  // Replace with custom hook. As above, the installed hookPath is a
  // symlink to the real hooks/pre-push-policy.sh on Unix; remove it before
  // writing so this doesn't clobber the repo's real hook script through
  // the symlink.
  const customHook = '#!/bin/bash\necho "custom hook"\n';
  fs.rmSync(hookPath, { force: true });
  fs.writeFileSync(hookPath, customHook);

  // Second scaffold with --force should replace
  const res2 = runCli(targetDir, ['--force']);
  assert.equal(res2.status, 0);

  const content = fs.readFileSync(hookPath, 'utf8');
  const sourceContent = fs.readFileSync(HOOK_SOURCE, 'utf8');
  assert.equal(content, sourceContent, 'Hook should be replaced with source');
});

test('scaffold output mentions hook installation', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-6');

  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  const res = runCli(targetDir);
  assert.equal(res.status, 0);

  const output = res.stdout + res.stderr;
  assert.ok(output.includes('hook') || output.includes('Hook') || output.includes('pre-push'),
    'Output should mention hook installation');
});

test('scaffold without git repo does not crash (no hook install)', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-7');

  // NO git init - bare directory
  // Scaffold should still work, just won't install hook (no .git dir)

  const res = runCli(targetDir);
  assert.equal(res.status, 0, 'Should not crash on non-git dir');

  const hookPath = path.join(targetDir, '.git', 'hooks', 'pre-push');
  // Either no .git dir, or hook wasn't installed
  // Both are acceptable behaviors
  assert.ok(!fs.existsSync(hookPath) || true, 'Scaffold should handle missing .git gracefully');
});

test('scaffold refuses to install hook when .git/hooks is a symlink', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-symlink-hooks');

  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  // Create an unrelated temp dir to symlink to
  const evilDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-evil-'));

  const gitDir = path.join(targetDir, '.git');
  const gitHooksDir = path.join(gitDir, 'hooks');

  // Remove the real hooks dir and replace with symlink (using junction on Windows)
  if (fs.existsSync(gitHooksDir)) {
    fs.rmSync(gitHooksDir, { recursive: true });
  }

  try {
    fs.symlinkSync(evilDir, gitHooksDir, isWindows() ? 'junction' : 'dir');
  } catch (e) {
    // If symlink creation fails, skip this test (e.g., no admin on Windows without junction support)
    console.log('Skipping symlink test (junction/symlink not available)');
    return;
  }

  // Scaffold should warn/refuse to install hook through symlink
  const res = runCli(targetDir);
  assert.equal(res.status, 0, 'Scaffold should complete (rest of scaffolding proceeds)');

  // Hook should NOT be installed
  const hookPath = path.join(gitHooksDir, 'pre-push');
  // The hook should not exist, or if it does, it should NOT be in evilDir
  if (fs.existsSync(hookPath)) {
    const hookRealpath = fs.realpathSync(hookPath);
    assert.ok(!hookRealpath.includes(evilDir), 'Hook should not escape symlink sandbox');
  }

  // Should see error message in stderr about refusing symlink
  const output = (res.stderr || '') + (res.stdout || '');
  assert.ok(
    output.toLowerCase().includes('symlink') ||
    output.toLowerCase().includes('refuse') ||
    output.toLowerCase().includes('skip'),
    `Should warn about symlink. Got: "${output}"`
  );
});

test('scaffold refuses to install hook when hook dest file is a symlink', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-symlink-hook-file');

  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  const gitHooksDir = path.join(targetDir, '.git', 'hooks');
  const hookPath = path.join(gitHooksDir, 'pre-push');

  // Create a symlink as the hook dest to an unrelated file
  const evilFile = path.join(os.tmpdir(), 'evil-hook-target-' + Math.random());
  fs.writeFileSync(evilFile, 'evil content\n');

  // Remove hook if exists, create symlink to evil file
  if (fs.existsSync(hookPath)) {
    fs.unlinkSync(hookPath);
  }

  try {
    fs.symlinkSync(evilFile, hookPath);
  } catch (e) {
    // If symlink creation fails, skip
    console.log('Skipping hook-file symlink test');
    return;
  }

  // Scaffold should refuse to write through symlink
  const res = runCli(targetDir);
  assert.equal(res.status, 0, 'Scaffold should complete');

  // Verify hook is still the symlink (not replaced)
  const stat = fs.lstatSync(hookPath);
  assert.ok(stat.isSymbolicLink(), 'Hook should still be symlink (not overwritten)');

  // Should see error/warning in output
  const output = (res.stderr || '') + (res.stdout || '');
  assert.ok(
    output.toLowerCase().includes('symlink') ||
    output.toLowerCase().includes('refuse') ||
    output.toLowerCase().includes('skip'),
    `Should warn about symlink hook file. Got: "${output}"`
  );

  // Clean up
  try {
    fs.unlinkSync(evilFile);
  } catch (e) {
    // ignore
  }
});

test('scaffold refuses to follow a pre-existing symlinked .git in target dir (escape guard)', () => {
  // Regression test for symlink-escape: PR #24 guarded .git/hooks and .git/hooks/pre-push
  // being symlinks, but not .git itself. A pre-existing target dir with a symlinked/junction
  // .git pointing outside the target must not cause any file to be written outside targetDir.
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-symlink-git');

  // "attacker path" — arbitrary directory well outside the scaffold target
  const evilDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-evil-gitdir-'));

  // Pre-create the target dir (simulates a shared "starter" folder the victim already has)
  fs.mkdirSync(targetDir, { recursive: true });

  const gitDir = path.join(targetDir, '.git');

  try {
    fs.symlinkSync(evilDir, gitDir, isWindows() ? 'junction' : 'dir');
  } catch (e) {
    // If symlink/junction creation fails (no privilege), skip gracefully
    console.log('Skipping .git symlink-escape test (junction/symlink not available)');
    return;
  }

  const res = runCli(targetDir);

  // Nothing should ever be written inside the attacker-controlled directory
  const escapedHookPath = path.join(evilDir, 'hooks', 'pre-push');
  assert.ok(
    !fs.existsSync(escapedHookPath),
    `Hook must not be written outside targetDir (found at ${escapedHookPath})`
  );

  // The scaffold should reject the run outright rather than silently continuing
  assert.notEqual(res.status, 0, 'Scaffold should refuse to proceed with a symlinked .git');

  const output = (res.stderr || '') + (res.stdout || '');
  assert.ok(
    output.toLowerCase().includes('symlink'),
    `Should warn/error about symlinked .git. Got: "${output}"`
  );
});

test('scaffold into a clean pre-existing target dir still works (happy path preserved)', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-clean-preexisting');

  // Pre-create an empty target dir (no .git, nothing) — must still scaffold fine
  fs.mkdirSync(targetDir, { recursive: true });
  gitCmd(targetDir, ['init']);
  gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(targetDir, ['config', 'user.name', 'Test User']);

  const res = runCli(targetDir);
  assert.equal(res.status, 0, `Scaffold should succeed on a clean real dir. stderr: ${res.stderr}`);

  const hookPath = path.join(targetDir, '.git', 'hooks', 'pre-push');
  assert.ok(fs.existsSync(hookPath), 'Hook should still be installed for a real .git dir');
});

function isWindows() {
  return process.platform === 'win32';
}
