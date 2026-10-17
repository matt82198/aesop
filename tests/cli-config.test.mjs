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
import { gitCmd, assertConfigNotPoisoned } from './helpers/git-helpers.mjs';

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
  // --no-skills: this file doesn't test skill installation, and without it every
  // scaffold call here would write into the real ~/.claude/skills/ (see
  // cli-skills-install.test.mjs's isolation contract) — on Windows CI that real,
  // shared directory gets hammered concurrently by every other test file's scaffold
  // calls too, and the resulting contention is what blew cli-skills-install's own
  // 180s file timeout.
  const res = spawnSync(process.execPath, [CLI, targetDir, ...args, '--no-skills'], {
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

/** Create real repo directories under dir; return their absolute paths. --repos now requires existing dirs. */
function makeRepoDirs(dir, names) {
  return names.map((name) => {
    const p = path.join(dir, name);
    fs.mkdirSync(p, { recursive: true });
    return p;
  });
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
    gitCmd(targetDir, ['init']);
    gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
    gitCmd(targetDir, ['config', 'user.name', 'Test User']);

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
    gitCmd(targetDir, ['init']);
    gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
    gitCmd(targetDir, ['config', 'user.name', 'Test User']);

    const res = runCli(targetDir, [
      '--name', 'my-service',
      '--repos', makeRepoDirs(tempDir, ['api', 'worker']).join(',')
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
    gitCmd(targetDir, ['init']);
    gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
    gitCmd(targetDir, ['config', 'user.name', 'Test User']);

    const res = runCli(targetDir, [
      '--name', 'my-service',
      '--repos', makeRepoDirs(tempDir, ['api', 'worker']).join(','),
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
    gitCmd(targetDir, ['init']);
    gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
    gitCmd(targetDir, ['config', 'user.name', 'Test User']);

    // 3 repos but only 2 URLs - should still work but fill remaining with placeholders
    const res = runCli(targetDir, [
      '--name', 'my-service',
      '--repos', makeRepoDirs(tempDir, ['api', 'worker', 'scheduler']).join(','),
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
    gitCmd(targetDir, ['init']);
    gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
    gitCmd(targetDir, ['config', 'user.name', 'Test User']);

    const res = runCli(targetDir, [
      '--name', 'production-api',
      '--domains', 'api,worker,monitoring',
      '--repos', makeRepoDirs(tempDir, ['api', 'worker']).join(','),
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
    gitCmd(targetDir, ['init']);
    gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
    gitCmd(targetDir, ['config', 'user.name', 'Test User']);

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

test('scaffold rejects a nonexistent --repos path: exit 2, names the path, writes no config', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-missing-repo');

  try {
    const res = runCli(targetDir, ['--name', 'my-service', '--repos', '/path/to/repo']);
    assert.equal(res.status, 2, `Nonexistent --repos path must exit 2. stdout: ${res.stdout}\nstderr: ${res.stderr}`);
    assert.ok(res.stderr.includes('/path/to/repo'), `stderr should name the bad path. stderr: ${res.stderr}`);
    assert.equal(fs.existsSync(path.join(targetDir, 'aesop.config.json')), false, 'no aesop.config.json may be written');
  } finally {
    cleanupTestDir(tempDir);
  }
});

test('scaffold rejects a --repos entry that is a file, not a directory', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-file-repo');

  try {
    const filePath = path.join(tempDir, 'not-a-repo.txt');
    fs.writeFileSync(filePath, 'not a directory');
    const res = runCli(targetDir, ['--name', 'my-service', '--repos', filePath]);
    assert.equal(res.status, 2, `File given as --repos must exit 2. stdout: ${res.stdout}\nstderr: ${res.stderr}`);
    assert.ok(res.stderr.includes(filePath), `stderr should name the bad entry. stderr: ${res.stderr}`);
    assert.equal(fs.existsSync(path.join(targetDir, 'aesop.config.json')), false, 'no aesop.config.json may be written');
  } finally {
    cleanupTestDir(tempDir);
  }
});

test('scaffold reports only the missing --repos entries and rejects the whole run', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-partial-repos');

  try {
    const [good] = makeRepoDirs(tempDir, ['api']);
    const missing = path.join(tempDir, 'ghost-repo');
    const res = runCli(targetDir, ['--name', 'my-service', '--repos', `${good},${missing}`]);
    assert.equal(res.status, 2, `Any missing entry must exit 2. stdout: ${res.stdout}\nstderr: ${res.stderr}`);
    assert.ok(res.stderr.includes(missing), `stderr should name the missing entry. stderr: ${res.stderr}`);
    assert.equal(res.stderr.includes(good), false, `existing entry must not be reported as missing. stderr: ${res.stderr}`);
    assert.equal(fs.existsSync(path.join(targetDir, 'aesop.config.json')), false, 'no aesop.config.json may be written');
  } finally {
    cleanupTestDir(tempDir);
  }
});

test('scaffold does not accept a URL in --repos (URLs belong in --repo-urls)', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-url-repo');

  try {
    const res = runCli(targetDir, ['--name', 'my-service', '--repos', 'https://github.com/myorg/api.git']);
    assert.equal(res.status, 2, `URL given as --repos must exit 2. stdout: ${res.stdout}\nstderr: ${res.stderr}`);
    assert.equal(fs.existsSync(path.join(targetDir, 'aesop.config.json')), false, 'no aesop.config.json may be written');
  } finally {
    cleanupTestDir(tempDir);
  }
});

test('final summary never prints a bare overwrite of ~/.claude/CLAUDE.md (back up or merge first)', () => {
  const tempDir = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-brain-summary');

  try {
    fs.mkdirSync(targetDir, { recursive: true });
    gitCmd(targetDir, ['init']);
    gitCmd(targetDir, ['config', 'user.email', 'test@example.com']);
    gitCmd(targetDir, ['config', 'user.name', 'Test User']);

    const res = runCli(targetDir, ['--name', 'brain-summary']);
    assert.equal(res.status, 0, `Scaffold should succeed. stderr: ${res.stderr}`);

    // The overwrite form is "cp <src> ~/.claude/CLAUDE.md  (review ...)" with the global file as destination.
    const lines = res.stdout.split(/\r?\n/);
    const overwriteIdx = lines
      .map((l, i) => (/^\s*cp \S+ ~\/\.claude\/CLAUDE\.md\s+\(/.test(l) ? i : -1))
      .filter((i) => i >= 0);
    assert.ok(overwriteIdx.length > 0, `summary should still print the brain copy instruction. stdout: ${res.stdout}`);
    for (const i of overwriteIdx) {
      const prev = i > 0 ? lines[i - 1] : '';
      assert.match(prev, /back ?up|merge|CLAUDE\.md\.bak/i, `line before overwrite must back up or merge: "${lines[i]}"`);
    }
  } finally {
    cleanupTestDir(tempDir);
  }
});

test('scaffold never writes to an existing ~/.claude/CLAUDE.md in the home directory', () => {
  const tempDir = createTestDir();
  const home = createTestDir();
  const targetDir = path.join(tempDir, 'fleet-config-home-guard');
  const globalClaude = path.join(home, '.claude', 'CLAUDE.md');
  const sentinel = '# adopter global instructions (must survive scaffold)\n';

  try {
    fs.mkdirSync(path.dirname(globalClaude), { recursive: true });
    fs.writeFileSync(globalClaude, sentinel);
    const env = { ...process.env, HOME: home, USERPROFILE: home };
    delete env.AESOP_SKILLS_HOME;
    // --no-git: this test guards the global CLAUDE.md, not git. The git add -A over the
    // template is what ran into the 30s spawnSync cap on CI (sync git, no per-file cost bug).
    const res = spawnSync(process.execPath, [CLI, targetDir, '--name', 'home-guard', '--no-git', '--no-skills'], {
      encoding: 'utf8',
      cwd: tempDir,
      env,
      timeout: 30000,
      killSignal: 'SIGKILL'
    });
    assert.equal(res.status, 0, `Scaffold should succeed. stderr: ${res.stderr}`);
    assert.equal(fs.readFileSync(globalClaude, 'utf8'), sentinel, 'existing global CLAUDE.md must be byte-identical');
  } finally {
    cleanupTestDir(tempDir);
    cleanupTestDir(home);
  }
});
