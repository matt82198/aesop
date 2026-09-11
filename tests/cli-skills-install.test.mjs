// Tests for automatic skill installation during scaffold
// Contract under test:
//  - scaffold installs skills/*/ into the skills home (Claude Code only scans
//    ~/.claude/skills/; a skill left in the scaffolded ./skills/ is undiscoverable)
//  - re-scaffolding is idempotent: identical skills are reported, not recopied blindly
//  - a locally modified skill is PRESERVED unless --force is passed
//  - --no-skills opts out entirely
//  - dependency manifests ship into the target so --install-deps has something to read
//
// Every test redirects AESOP_SKILLS_HOME into a temp dir; the real ~/.claude is
// never touched.
//
// Run: node --test tests/cli-skills-install.test.mjs

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

// Scaffold into `targetDir` with the skills home redirected at `skillsHome`.
function scaffold(targetDir, skillsHome, extraArgs = []) {
  const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 60000;
  return spawnSync(
    process.execPath,
    [CLI, targetDir, '--name', 'skills-test', '--yes', ...extraArgs],
    {
      encoding: 'utf8',
      cwd: path.dirname(targetDir),
      timeout,
      killSignal: 'SIGKILL',
      env: { ...process.env, AESOP_SKILLS_HOME: skillsHome }
    }
  );
}

test('scaffold installs skills into the skills home', () => {
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    const res = scaffold(path.join(base, 'fleet'), skillsHome);

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
  } finally {
    cleanupTestDir(base);
  }
});

test('--no-skills skips installation entirely', () => {
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    const res = scaffold(path.join(base, 'fleet'), skillsHome, ['--no-skills']);

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

test('re-scaffolding over identical skills is idempotent', () => {
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    scaffold(path.join(base, 'fleet-a'), skillsHome);
    const res = scaffold(path.join(base, 'fleet-b'), skillsHome);

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

test('a locally modified skill is preserved without --force', () => {
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    scaffold(path.join(base, 'fleet-a'), skillsHome);

    const powerSkill = path.join(skillsHome, 'power', 'SKILL.md');
    const mine = '# my own power skill\n';
    fs.writeFileSync(powerSkill, mine);

    const res = scaffold(path.join(base, 'fleet-b'), skillsHome);

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

test('--force overwrites a locally modified skill', () => {
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    scaffold(path.join(base, 'fleet-a'), skillsHome);

    const powerSkill = path.join(skillsHome, 'power', 'SKILL.md');
    fs.writeFileSync(powerSkill, '# my own power skill\n');

    const res = scaffold(path.join(base, 'fleet-b'), skillsHome, ['--force']);

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

test('dependency manifests ship into the scaffolded target', () => {
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    const target = path.join(base, 'fleet');
    const res = scaffold(target, skillsHome, ['--no-skills']);

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
  const base = createTestDir();
  try {
    const skillsHome = path.join(base, 'skills-home');
    const sentinel = path.join(os.homedir(), '.claude', 'skills');
    const before = fs.existsSync(sentinel)
      ? fs.readdirSync(sentinel).sort().join(',')
      : '<absent>';

    scaffold(path.join(base, 'fleet'), skillsHome);

    const after = fs.existsSync(sentinel)
      ? fs.readdirSync(sentinel).sort().join(',')
      : '<absent>';
    assert.equal(after, before, 'real ~/.claude/skills must be untouched');
  } finally {
    cleanupTestDir(base);
  }
});
