// Doctor layout detection (D32):
//  (a) an init'd repo (aesop.config.json, no scaffold directories) is not a scaffold: the
//      five-directory check reports "n/a (init layout)" and does not fail;
//  (b) a scaffold layout still fails when a required directory is missing;
//  (c) the ~/.claude/skills check is global: the default run never reads the home skills
//      directory, and --global does.
//
// Run: node --test tests/doctor-layout-mode.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gitCmd } from './helpers/git-helpers.mjs';

const REPO_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const DOCTOR = path.join(REPO_ROOT, 'tools', 'doctor.js');
const CLI = path.join(REPO_ROOT, 'bin', 'cli.js');

// Push-gate artifacts the doctor requires (tools/doctor.js PUSH_GATE_ARTIFACTS).
const PUSH_GATE_FILES = [
  'tools/INDEX.md',
  '.pyflakes-baseline.json',
  '.subprocess-guard-baseline.json',
  '.portability-baseline.json',
  '.stateapi-baseline.json'
];

function makeTempDir(prefix) {
  return fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
}

// A git repo with the given config (undefined = no aesop.config.json), directories, the
// push-gate artifacts, and a pre-push hook, so the only rows that can fail are layout rows.
function makeRepo({ config, dirs = [] }) {
  const root = makeTempDir('aesop-doctor-layout-');
  gitCmd(root, ['init']);
  gitCmd(root, ['config', 'user.email', 'test@example.com']);
  gitCmd(root, ['config', 'user.name', 'Test User']);
  if (config !== undefined) {
    fs.writeFileSync(path.join(root, 'aesop.config.json'), JSON.stringify(config, null, 2));
  }
  for (const dir of dirs) fs.mkdirSync(path.join(root, dir), { recursive: true });
  for (const file of PUSH_GATE_FILES) {
    fs.mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
    fs.writeFileSync(path.join(root, file), '{}\n');
  }
  fs.mkdirSync(path.join(root, '.git', 'hooks'), { recursive: true });
  fs.writeFileSync(path.join(root, '.git', 'hooks', 'pre-push'), '#!/bin/sh\nexit 0\n');
  return root;
}

function runDoctor(cwd, home, args = []) {
  const env = { ...process.env, HOME: home, USERPROFILE: home };
  return spawnSync(process.execPath, [DOCTOR, ...args], {
    cwd,
    encoding: 'utf8',
    env,
    timeout: 60000,
    killSignal: 'SIGKILL'
  });
}

function runDoctorJson(cwd, home, args = []) {
  const res = runDoctor(cwd, home, ['--json', ...args]);
  try {
    return { res, report: JSON.parse(res.stdout) };
  } catch (e) {
    assert.fail(`doctor --json must emit JSON. stdout: ${res.stdout} stderr: ${res.stderr}`);
  }
}

// Port 8770 is a machine-wide socket probe tools/doctor.js hardcodes; another process or a
// parallel test holding the port must not turn a layout test red. So the exit code is checked
// against the port row's own verdict, and every other row must pass.
const PORT_LABEL = 'Port 8770';
function failingRowsExcludingPort(report) {
  return report.checks.filter(c => !c.passed && !c.label.startsWith(PORT_LABEL)).map(c => c.label);
}
function expectedExit(report) {
  const port = report.checks.find(c => c.label.startsWith(PORT_LABEL));
  assert.ok(port, 'doctor must report the port row');
  return port.passed ? 0 : 1;
}

function rowFor(report, labelPrefix) {
  const row = report.checks.find(c => c.label.startsWith(labelPrefix));
  assert.ok(row, `doctor must report a "${labelPrefix}" row. labels: ${report.checks.map(c => c.label).join(' | ')}`);
  return row;
}

test('init layout: config-only repo with no five directories exits 0 and prints "n/a (init layout)"', () => {
  const root = makeRepo({ config: { description: 'init', repos: [], layout: 'init' } });
  const home = makeTempDir('aesop-doctor-home-');
  const { res, report } = runDoctorJson(root, home);

  const dirs = rowFor(report, 'Required directories');
  assert.equal(dirs.passed, true, `init layout must not fail the directory check. hint: ${dirs.hint}`);
  assert.equal(dirs.hint, 'n/a (init layout)');
  assert.equal(report.layout.name, 'init', `layout must be reported as init: ${JSON.stringify(report.layout)}`);
  const failing = failingRowsExcludingPort(report);
  assert.deepEqual(failing, [], `no check may fail on an init'd repo: ${failing.join(' | ')}`);
  assert.equal(res.status, expectedExit(report), `exit must follow the port row. stdout: ${res.stdout} stderr: ${res.stderr}`);

  const text = runDoctor(root, home);
  assert.ok(text.stdout.includes('n/a (init layout)'), `text output must print the n/a line: ${text.stdout}`);
  assert.ok(text.stdout.includes('init'), `text output must name the detected layout: ${text.stdout}`);
});

test('init layout is inferred when aesop.config.json has no "layout" key and no scaffold directory exists', () => {
  const root = makeRepo({ config: { description: 'init', repos: [] } });
  const home = makeTempDir('aesop-doctor-home-');
  const { res, report } = runDoctorJson(root, home);

  assert.equal(report.layout.name, 'init');
  assert.equal(rowFor(report, 'Required directories').passed, true);
  assert.deepEqual(failingRowsExcludingPort(report), []);
  assert.equal(res.status, expectedExit(report), `inferred init layout exit must follow the port row. stdout: ${res.stdout}`);
});

test('scaffold layout (explicit key) still FAILS when a required directory is missing', () => {
  // "dash" is absent; the five-directory rule applies to a scaffold.
  const root = makeRepo({
    config: { description: 'scaffold', repos: [], layout: 'scaffold' },
    dirs: ['daemons', 'monitor', 'tools', 'ui']
  });
  const home = makeTempDir('aesop-doctor-home-');
  const { res, report } = runDoctorJson(root, home);

  const dirs = rowFor(report, 'Required directories');
  assert.equal(dirs.passed, false, 'scaffold with a missing directory must FAIL');
  assert.ok(dirs.hint.includes('dash'), `hint must name the missing directory: ${dirs.hint}`);
  assert.equal(report.layout.name, 'scaffold');
  assert.equal(res.status, 1, 'scaffold with a missing directory must exit 1');
});

test('scaffold layout is inferred from a scaffold directory and still FAILS when one is missing', () => {
  // No "layout" key, but daemons/ exists, so this is a scaffold; "monitor" is absent.
  const root = makeRepo({
    config: { description: 'scaffold', repos: [] },
    dirs: ['daemons', 'dash', 'tools', 'ui']
  });
  const home = makeTempDir('aesop-doctor-home-');
  const { res, report } = runDoctorJson(root, home);

  assert.equal(report.layout.name, 'scaffold');
  const dirs = rowFor(report, 'Required directories');
  assert.equal(dirs.passed, false, 'inferred scaffold with a missing directory must FAIL');
  assert.ok(dirs.hint.includes('monitor'), `hint must name the missing directory: ${dirs.hint}`);
  assert.equal(res.status, 1);
});

test('default run does not read the home skills directory; --global does', () => {
  const root = makeRepo({ config: { description: 'init', repos: [], layout: 'init' } });

  // Home with no ~/.claude/skills at all.
  const emptyHome = makeTempDir('aesop-doctor-home-');
  const def = runDoctorJson(root, emptyHome);
  const skillsDefault = rowFor(def.report, 'Skills files');
  assert.equal(skillsDefault.passed, true, `default run must not fail on the global skills check: ${skillsDefault.hint}`);
  assert.ok(skillsDefault.hint.startsWith('n/a'), `default skills row must be n/a: ${skillsDefault.hint}`);
  assert.deepEqual(failingRowsExcludingPort(def.report), [], 'default run on an init repo must fail nothing but the port, regardless of the home skills');
  assert.equal(def.res.status, expectedExit(def.report), 'default run exit must follow the port row');
  const defText = runDoctor(root, emptyHome);
  assert.ok(defText.stdout.includes('--global'), `default text output must say global checks were skipped and how to enable them: ${defText.stdout}`);

  // --global with the same empty home: the skills check runs and fails.
  const globalEmpty = runDoctorJson(root, emptyHome, ['--global']);
  assert.equal(rowFor(globalEmpty.report, 'Skills files').passed, false, '--global must check ~/.claude/skills and FAIL when absent');
  assert.equal(globalEmpty.res.status, 1, '--global with missing skills must exit 1');

  // --global with both skills installed under the home: PASS.
  const fullHome = makeTempDir('aesop-doctor-home-');
  for (const skill of ['power', 'buildsystem']) {
    fs.mkdirSync(path.join(fullHome, '.claude', 'skills', skill), { recursive: true });
    fs.writeFileSync(path.join(fullHome, '.claude', 'skills', skill, 'SKILL.md'), '# skill\n');
  }
  const globalFull = runDoctorJson(root, fullHome, ['--global']);
  assert.equal(rowFor(globalFull.report, 'Skills files').passed, true, '--global must PASS with both skills installed');
  assert.equal(globalFull.res.status, expectedExit(globalFull.report), '--global exit must follow the port row');
});

test('the CLI accepts `doctor --global` (no "unknown option", exit != 2)', () => {
  const root = makeRepo({ config: { description: 'init', repos: [], layout: 'init' } });
  const home = makeTempDir('aesop-doctor-home-');
  const env = { ...process.env, HOME: home, USERPROFILE: home };
  const res = spawnSync(process.execPath, [CLI, 'doctor', '--global'], {
    cwd: root,
    encoding: 'utf8',
    env,
    timeout: 60000,
    killSignal: 'SIGKILL'
  });
  assert.ok(!res.stderr.includes('unknown option'), `doctor --global must be a documented flag. stderr: ${res.stderr}`);
  assert.notEqual(res.status, 2, `doctor --global must not exit 2 (usage error). stderr: ${res.stderr}`);
});
