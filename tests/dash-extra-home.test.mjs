// Tests for dash/dash-extra.mjs home-directory resolution.
// Contract: with no HOME in the environment (native Windows PowerShell or a
// scheduled task), the module must resolve the user's home via os.homedir()
// and find transcripts under it, not under the process cwd.
// Run: node --test tests/dash-extra-home.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'dash', 'dash-extra.mjs');
const AGENT_ID = 'fixture0002';

// Fake home holding a fresh agent transcript at the real layout:
// <home>/.claude/projects/<project>/<session>/agent-<id>.jsonl
function makeHome() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-dash-home-'));
  const sessionDir = path.join(home, '.claude', 'projects', 'C--fixture-project', 'sess1');
  fs.mkdirSync(sessionDir, { recursive: true });
  fs.writeFileSync(
    path.join(sessionDir, `agent-${AGENT_ID}.jsonl`),
    '{"type":"user","message":{"role":"user","content":"fixture agent for home test"}}\n'
  );
  return home;
}

// Run the module with no HOME in the environment. The home directory is
// supplied through the OS-native variable that os.homedir() reads:
// USERPROFILE on Windows (HOME unset, as in a PowerShell/scheduled-task launch),
// HOME on POSIX (where os.homedir() ignores USERPROFILE).
function runWithoutHome(home, cwd) {
  const env = { ...process.env };
  delete env.HOME;
  delete env.AESOP_ROOT;
  delete env.AESOP_TRANSCRIPTS_ROOT;
  delete env.AESOP_STATE_ROOT;
  if (process.platform === 'win32') {
    env.USERPROFILE = home;
  } else {
    env.HOME = home;
  }
  const stdout = execFileSync(process.execPath, [SCRIPT, '--json'], {
    env,
    cwd,
    encoding: 'utf8',
    timeout: 30000,
    killSignal: 'SIGKILL'
  });
  return JSON.parse(stdout);
}

test('agent under the OS home is listed when HOME is unset', () => {
  const home = makeHome();
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-dash-cwd-'));
  try {
    const agents = runWithoutHome(home, cwd);
    assert.equal(agents.length, 1, `expected 1 agent, got ${agents.length}`);
    assert.equal(agents[0].id, AGENT_ID);
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
    fs.rmSync(cwd, { recursive: true, force: true });
  }
});

test('explicit AESOP_TRANSCRIPTS_ROOT still overrides the home default', () => {
  const home = makeHome();
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-dash-cwd-'));
  const override = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-dash-override-'));
  try {
    const sessionDir = path.join(override, 'proj', 'sess9');
    fs.mkdirSync(sessionDir, { recursive: true });
    fs.writeFileSync(path.join(sessionDir, 'agent-override0003.jsonl'), '{}\n');
    const env = { ...process.env, AESOP_TRANSCRIPTS_ROOT: override };
    delete env.HOME;
    if (process.platform === 'win32') env.USERPROFILE = home; else env.HOME = home;
    const stdout = execFileSync(process.execPath, [SCRIPT, '--json'], {
      env, cwd, encoding: 'utf8', timeout: 30000, killSignal: 'SIGKILL'
    });
    const agents = JSON.parse(stdout);
    assert.deepEqual(agents.map((a) => a.id), ['override0003']);
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
    fs.rmSync(cwd, { recursive: true, force: true });
    fs.rmSync(override, { recursive: true, force: true });
  }
});
