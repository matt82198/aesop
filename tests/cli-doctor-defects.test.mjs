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
  const res = spawnSync(process.execPath, [CLI, targetDir, ...args], {
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
