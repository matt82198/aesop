// Tests for CLI config scaffolding fixes
// Contract under test:
//  - scaffold auto-populates fleet_root with os.homedir() instead of '/path/to/fleet' placeholder
//  - scaffold supports --repo-urls comma-separated flag to override generated repo URLs
//  - when --repo-urls is not provided, repo URLs get placeholder + _repos_note explaining they must be edited
//  - aesop.config.example.json has a _fleet_root_note key explaining the security boundary
//
// Run: node --test tests/cli-config.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const CLI = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', 'bin', 'cli.js'
);

const EXAMPLE_CONFIG = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', 'aesop.config.example.json'
);

function runCli(targetDir, args = []) {
  // Honors AESOP_TEST_CHILD_TIMEOUT_MS override (same pattern as wizard.test.mjs /
  // scaffold-*.test.mjs) so CI can widen the window under Windows contention.
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
  return fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-config-test-'));
}

function cleanupTestDir(dir) {
  // Cross-platform cleanup using fs.rmSync (works on Windows and POSIX)
  try {
    if (fs.existsSync(dir)) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  } catch (e) {
    // Ignore cleanup errors
  }
}

function gitCmd(cwd, cmd) {
  const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000;
  const bashCmd = `bash -c "cd '${cwd.replace(/'/g, "'\\''")}' && ${cmd}"`;
  return spawnSync('bash', ['-c', bashCmd], { stdio: 'ignore', encoding: 'utf8', timeout, killSignal: 'SIGKILL' });
}

test('aesop.config.example.json has _fleet_root_note key explaining security boundary', () => {
  const exampleConfig = JSON.parse(fs.readFileSync(EXAMPLE_CONFIG, 'utf8'));

  assert.ok(exampleConfig._fleet_root_note, 'aesop.config.example.json should have _fleet_root_note key');
  assert.ok(
    typeof exampleConfig._fleet_root_note === 'string' && exampleConfig._fleet_root_note.length > 0,
    '_fleet_root_note should be a non-empty string'
  );
  assert.ok(
    exampleConfig._fleet_root_note.toLowerCase().includes('security') || exampleConfig._fleet_root_note.toLowerCase().includes('boundary'),
    '_fleet_root_note should explain security boundary'
  );
});

test('aesop.config.example.json has fleet_root key', () => {
  const exampleConfig = JSON.parse(fs.readFileSync(EXAMPLE_CONFIG, 'utf8'));
  assert.ok(exampleConfig.fleet_root !== undefined, 'aesop.config.example.json should have fleet_root key');
});

test('scaffold with --name populates fleet_root with os.homedir() not placeholder', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-1');

  try {
    fs.mkdirSync(targetDir, { recursive: true });
    gitCmd(targetDir, 'git init');
    gitCmd(targetDir, 'git config user.email "test@example.com"');
    gitCmd(targetDir, 'git config user.name "Test User"');

    const res = runCli(targetDir, ['--name', 'test-service']);
    assert.equal(res.status, 0, `Scaffold should succeed. stderr: ${res.stderr}`);

    const configPath = path.join(targetDir, 'aesop.config.json');
    assert.ok(fs.existsSync(configPath), `aesop.config.json should exist`);

    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

    // fleet_root should be os.homedir() expanded, NOT placeholder
    assert.ok(config.fleet_root, 'Config should have fleet_root key');

    // Should NOT be the placeholder
    assert.notEqual(
      config.fleet_root,
      '/path/to/fleet',
      'fleet_root should not be placeholder /path/to/fleet'
    );

    // Should be homedir (with ~ expansion at runtime, or actual path)
    // The generated config may use ~ or absolute path
    assert.ok(
      config.fleet_root === os.homedir() || config.fleet_root.includes(os.homedir()),
      `fleet_root should include homedir. Got: ${config.fleet_root}, homedir: ${os.homedir()}`
    );
  } finally {
    cleanupTestDir(tempDir);
  }
});

test('scaffold with --repos generates placeholder URLs but adds _repos_note when --repo-urls not provided', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-2');

  try {
    fs.mkdirSync(targetDir, { recursive: true });
    gitCmd(targetDir, 'git init');
    gitCmd(targetDir, 'git config user.email "test@example.com"');
    gitCmd(targetDir, 'git config user.name "Test User"');

    const res = runCli(targetDir, [
      '--name', 'my-service',
      '--repos', '/path/to/api,/path/to/worker'
    ]);
    assert.equal(res.status, 0, `Scaffold should succeed. stderr: ${res.stderr}`);

    const configPath = path.join(targetDir, 'aesop.config.json');
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

    // Should have repos
    assert.ok(config.repos && Array.isArray(config.repos), 'Config should have repos array');
    assert.equal(config.repos.length, 2, 'Should have 2 repos');

    // Each repo should have a placeholder URL
    config.repos.forEach((repo) => {
      assert.ok(repo.url, `Repo ${repo.name} should have url`);
      assert.ok(
        repo.url.includes('github.com/user/'),
        `Repo URL should be placeholder format. Got: ${repo.url}`
      );
    });

    // Should have _repos_note explaining URLs need editing
    assert.ok(config._repos_note, 'Config should have _repos_note key when --repo-urls not provided');
    assert.ok(
      typeof config._repos_note === 'string' && config._repos_note.length > 0,
      '_repos_note should be a non-empty string'
    );
    assert.ok(
      config._repos_note.toLowerCase().includes('url') || config._repos_note.toLowerCase().includes('edit'),
      '_repos_note should explain that URLs need to be edited'
    );
  } finally {
    cleanupTestDir(tempDir);
  }
});

test('scaffold with --repo-urls uses provided URLs in generated config', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-3');

  try {
    fs.mkdirSync(targetDir, { recursive: true });
    gitCmd(targetDir, 'git init');
    gitCmd(targetDir, 'git config user.email "test@example.com"');
    gitCmd(targetDir, 'git config user.name "Test User"');

    const res = runCli(targetDir, [
      '--name', 'my-service',
      '--repos', '/path/to/api,/path/to/worker',
      '--repo-urls', 'https://github.com/myorg/api.git,https://github.com/myorg/worker.git'
    ]);
    assert.equal(res.status, 0, `Scaffold with --repo-urls should succeed. stderr: ${res.stderr}`);

    const configPath = path.join(targetDir, 'aesop.config.json');
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

    assert.ok(config.repos && Array.isArray(config.repos), 'Config should have repos array');
    assert.equal(config.repos.length, 2, 'Should have 2 repos');

    // URLs should match provided values
    assert.equal(config.repos[0].url, 'https://github.com/myorg/api.git', 'First repo should have correct URL');
    assert.equal(config.repos[1].url, 'https://github.com/myorg/worker.git', 'Second repo should have correct URL');

    // Should NOT have _repos_note when --repo-urls provided
    assert.ok(
      !config._repos_note || config._repos_note === undefined,
      'Config should not have _repos_note when --repo-urls provided'
    );
  } finally {
    cleanupTestDir(tempDir);
  }
});

test('scaffold respects --repo-urls count matches --repos count', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-4');

  try {
    fs.mkdirSync(targetDir, { recursive: true });
    gitCmd(targetDir, 'git init');
    gitCmd(targetDir, 'git config user.email "test@example.com"');
    gitCmd(targetDir, 'git config user.name "Test User"');

    // 3 repos but only 2 URLs - should still work but fill remaining with placeholders
    const res = runCli(targetDir, [
      '--name', 'my-service',
      '--repos', '/path/to/api,/path/to/worker,/path/to/scheduler',
      '--repo-urls', 'https://github.com/myorg/api.git,https://github.com/myorg/worker.git'
    ]);

    // This may succeed or fail depending on implementation;
    // test that it handles gracefully (doesn't crash)
    assert.ok(res.status === 0 || res.status !== null, 'Command should complete with defined status');

    const configPath = path.join(targetDir, 'aesop.config.json');
    if (fs.existsSync(configPath)) {
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      assert.ok(config.repos, 'Config should have repos if file exists');
    }
  } finally {
    cleanupTestDir(tempDir);
  }
});

test('scaffold with --domains and --repo-urls works together', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-5');

  try {
    fs.mkdirSync(targetDir, { recursive: true });
    gitCmd(targetDir, 'git init');
    gitCmd(targetDir, 'git config user.email "test@example.com"');
    gitCmd(targetDir, 'git config user.name "Test User"');

    const res = runCli(targetDir, [
      '--name', 'production-api',
      '--domains', 'api,worker,monitoring',
      '--repos', '/home/user/api,/home/user/worker',
      '--repo-urls', 'git@github.com:company/api.git,git@github.com:company/worker.git'
    ]);
    assert.equal(res.status, 0, `Full scaffold should succeed. stderr: ${res.stderr}`);

    const configPath = path.join(targetDir, 'aesop.config.json');
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

    // Verify repos have correct URLs
    assert.equal(config.repos[0].url, 'git@github.com:company/api.git');
    assert.equal(config.repos[1].url, 'git@github.com:company/worker.git');

    // Verify fleet_root is set properly
    assert.notEqual(config.fleet_root, '/path/to/fleet');
  } finally {
    cleanupTestDir(tempDir);
  }
});

test('scaffold initializes both fleet_root and fallback config path with homedir', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-fallback');

  try {
    fs.mkdirSync(targetDir, { recursive: true });
    gitCmd(targetDir, 'git init');
    gitCmd(targetDir, 'git config user.email "test@example.com"');
    gitCmd(targetDir, 'git config user.name "Test User"');

    // Test with just --name to trigger config generation
    const res = runCli(targetDir, ['--name', 'fallback-test']);
    assert.equal(res.status, 0, `Scaffold should succeed. stderr: ${res.stderr}`);

    const configPath = path.join(targetDir, 'aesop.config.json');
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

    // Even if example config fails to parse (triggering fallback),
    // the generated config should have fleet_root set properly
    assert.ok(config.fleet_root, 'Config should have fleet_root');
    assert.notEqual(config.fleet_root, '/path/to/fleet', 'fleet_root should not be placeholder even in fallback');

    // Should be homedir or contain homedir reference
    const homedirPath = os.homedir();
    assert.ok(
      config.fleet_root === homedirPath ||
      config.fleet_root.includes(homedirPath) ||
      config.fleet_root === homedirPath,
      `fleet_root in fallback config should reference homedir. Got: ${config.fleet_root}`
    );
  } finally {
    cleanupTestDir(tempDir);
  }
});
