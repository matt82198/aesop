// Tests for onboarding flag handling in the scaffolder (bin/cli.js).
// Contract under test:
//  1. `--ci-mode X` and `--self-hosted-labels L` are value-taking flags: their
//     values are never taken as the target directory. A scaffold run with them
//     creates only the default directory (`aesop-fleet`), never a directory named
//     after the flag value.
//  2. `wizard --yes --name N --repos P` honours --name and --repos instead of the
//     built-in defaults (`my-fleet` / no repos): the generated aesop.config.json
//     lists P as a repo.
//  3. The brain-setup output never suggests overwriting ~/.claude/CLAUDE.md or
//     ~/.claude/MEMORY.md with a bare `cp`. The only write onto those paths that
//     may be printed is the backup-first step; the template is staged to a `.new`
//     file that the user merges by hand.
//
// Every spawn runs with cwd and HOME/USERPROFILE pointed at fresh temp dirs and
// --no-skills, so nothing touches the real ~/.claude.
//
// Run: node --test tests/cli-flags-onboarding.test.mjs

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

/**
 * Create a fresh sandbox (cwd + fake HOME) and run the CLI in it.
 * @param {string[]} args CLI arguments; --no-skills is appended.
 * @param {string[]} [dirs] directories (relative to cwd) to create before the run.
 * @returns {{status: number, stdout: string, stderr: string, cwd: string, base: string}}
 */
function sandboxRun(args, dirs = []) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-cliflags-'));
  const cwd = path.join(base, 'cwd');
  const home = path.join(base, 'home');
  fs.mkdirSync(home, { recursive: true });
  fs.mkdirSync(cwd, { recursive: true });
  for (const d of dirs) fs.mkdirSync(path.join(cwd, d), { recursive: true });
  const env = { ...process.env, HOME: home, USERPROFILE: home };
  delete env.AESOP_SKILLS_HOME;
  const res = spawnSync(process.execPath, [CLI, ...args, '--no-skills'], {
    cwd,
    env,
    input: '',
    encoding: 'utf8',
    timeout: Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000,
    killSignal: 'SIGKILL',
  });
  return { status: res.status, stdout: res.stdout || '', stderr: res.stderr || '', cwd, base };
}

test('scaffold with --ci-mode and --self-hosted-labels creates no directory named after the flag values', () => {
  const run = sandboxRun(['--ci-mode', 'self-hosted-runner', '--self-hosted-labels', 'lab-one,lab-two']);
  try {
    assert.equal(run.status, 0, `scaffold exited ${run.status}: ${run.stderr}`);
    const entries = fs.readdirSync(run.cwd).sort();
    assert.deepEqual(entries, ['aesop-fleet'], `unexpected directories created: ${entries.join(', ')}`);
  } finally {
    fs.rmSync(run.base, { recursive: true, force: true });
  }
});

test('wizard --yes honours --name and --repos instead of the my-fleet defaults', () => {
  // The repo directory must exist under cwd before the run so the repo path is real.
  const sandbox = sandboxRun(['wizard', '--yes', '--name', 'api', '--repos', 'api'], ['api']);
  try {
    assert.equal(sandbox.status, 0, `wizard exited ${sandbox.status}: ${sandbox.stderr}`);
    const configPath = path.join(sandbox.cwd, 'aesop-fleet', 'aesop.config.json');
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    assert.equal(config.repos.length, 1, `expected one repo, got ${JSON.stringify(config.repos)}`);
    assert.equal(config.repos[0].name, 'api', `repo name was ${config.repos[0].name}`);
    assert.match(config.repos[0].path, /api$/, `repo path was ${config.repos[0].path}`);
  } finally {
    fs.rmSync(sandbox.base, { recursive: true, force: true });
  }
});

test('captured onboarding output guards every ~/.claude/CLAUDE.md or MEMORY.md overwrite with a backup-first line', () => {
  const runs = [
    ['wizard', '--yes', '--name', 'api'],
    ['--name', 'api'],
    [], // plain scaffold, no --name: no CLAUDE.md is generated, so the raw template is the source
  ];
  // A bare overwrite is a cp whose DESTINATION is exactly ~/.claude/CLAUDE.md or
  // ~/.claude/MEMORY.md. The destination is followed by the step annotation "  (...)"
  // or the end of the line, so a .bak destination and a merge hint that mentions
  // the path later in the line do not match.
  const overwrite = /^\s*cp\s+\S.*\s~\/\.claude\/(CLAUDE|MEMORY)\.md(?=\s+\(|\s*$)/;
  for (const args of runs) {
    const run = sandboxRun(args);
    try {
      assert.equal(run.status, 0, `${args.join(' ')} exited ${run.status}: ${run.stderr}`);
      assert.match(run.stdout, /Set up your brain/, `brain-setup section missing for: ${args.join(' ')}`);
      const lines = run.stdout.split(/\r?\n/);
      const overwriteIdx = lines.map((l, i) => (overwrite.test(l) ? i : -1)).filter((i) => i >= 0);
      // Both the CLAUDE.md and MEMORY.md copy instructions must still be printed (no vacuous pass).
      assert.equal(overwriteIdx.length, 2, `expected CLAUDE.md and MEMORY.md copy steps for: ${args.join(' ')}`);
      for (const i of overwriteIdx) {
        const prev = i > 0 ? lines[i - 1] : '';
        assert.match(prev, /back ?up|merge|\.bak/i, `line before overwrite must back up or merge: "${lines[i]}"`);
      }
      // A raw template copy (still holding {{PROJECT_NAME}} tokens) must tell the user to replace them.
      const rawTemplate = lines.filter((l) => /CLAUDE-TEMPLATE\.md\s+~\/\.claude\/CLAUDE\.md(?=\s+\()/.test(l));
      for (const l of rawTemplate) {
        assert.match(l, /PROJECT_NAME/, `raw template copy must call out the tokens: "${l}" for: ${args.join(' ')}`);
      }
    } finally {
      fs.rmSync(run.base, { recursive: true, force: true });
    }
  }
});
