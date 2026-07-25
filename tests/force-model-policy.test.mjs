// Tests for hooks/claude/force-model-policy.mjs — the Claude Code PreToolUse
// hook that enforces the "subagents are always Haiku" cardinal rule as code.
//
// Contract under test (stdin -> stdout JSON, exit 0 always):
//  - Agent/Task dispatch with absent or non-haiku model  -> rewritten to policy model
//  - prompt containing [[ALLOW-NON-HAIKU]]               -> no rewrite, but the bypass
//    is announced via permissionDecisionReason AND appended to
//    state/MODEL-POLICY-ESCAPES.log under the AESOP_ROOT state root
//  - malformed stdin                                     -> no output, exit 0 (fail-open)
//  - never-closing stdin                                 -> exits 0 within ~2s (fail-open)
//  - aesop.config.json cardinal_rules.subagent_model     -> overrides the "haiku" default
//
// Run: node --test tests/force-model-policy.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HOOK = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', 'hooks', 'claude', 'force-model-policy.mjs'
);

// Every run gets an isolated AESOP_ROOT (and cwd) so the hook never picks up a
// real aesop.config.json from the developer's machine.
function makeRoot(config) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-hook-test-'));
  if (config !== undefined) {
    fs.writeFileSync(path.join(root, 'aesop.config.json'), JSON.stringify(config));
  }
  return root;
}

// Honors AESOP_TEST_CHILD_TIMEOUT_MS override (same pattern as collect-signals.test.mjs /
// wizard.test.mjs) so CI can widen the window under Windows contention.
function getTestTimeout() {
  return Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000;
}

function runHook(stdinText, { config } = {}) {
  const root = makeRoot(config);
  const res = spawnSync(process.execPath, [HOOK], {
    input: stdinText,
    cwd: root,
    env: { ...process.env, AESOP_ROOT: root },
    encoding: 'utf8',
    timeout: getTestTimeout(),
    killSignal: 'SIGKILL'
  });
  res.root = root;
  return res;
}

const ESCAPE_LOG = path.join('state', 'MODEL-POLICY-ESCAPES.log');

function payload(toolName, toolInput) {
  return JSON.stringify({
    hook_event_name: 'PreToolUse',
    tool_name: toolName,
    tool_input: toolInput
  });
}

test('non-haiku model on an Agent dispatch is rewritten to haiku', () => {
  const res = runHook(payload('Agent', {
    description: 'do a thing',
    prompt: 'Implement the feature.',
    subagent_type: 'general-purpose',
    model: 'opus'
  }));
  assert.equal(res.status, 0, 'hook must exit 0');
  const out = JSON.parse(res.stdout);
  const hso = out.hookSpecificOutput;
  assert.equal(hso.hookEventName, 'PreToolUse');
  assert.equal(hso.permissionDecision, 'allow');
  assert.equal(hso.updatedInput.model, 'haiku');
  // Everything else in tool_input must be preserved verbatim.
  assert.equal(hso.updatedInput.prompt, 'Implement the feature.');
  assert.equal(hso.updatedInput.description, 'do a thing');
  assert.equal(hso.updatedInput.subagent_type, 'general-purpose');
});

test('absent model on a Task dispatch is rewritten to haiku', () => {
  const res = runHook(payload('Task', {
    description: 'search',
    prompt: 'Find all callers.'
  }));
  assert.equal(res.status, 0);
  const out = JSON.parse(res.stdout);
  assert.equal(out.hookSpecificOutput.updatedInput.model, 'haiku');
});

test('model already compliant passes through unchanged (no output)', () => {
  const res = runHook(payload('Agent', {
    description: 'cheap work',
    prompt: 'Grep for a symbol.',
    model: 'haiku'
  }));
  assert.equal(res.status, 0);
  assert.equal(res.stdout.trim(), '', 'compliant dispatch must not be rewritten');
});

test('escape hatch [[ALLOW-NON-HAIKU]] suppresses the rewrite but emits a visible reason', () => {
  const res = runHook(payload('Agent', {
    description: 'heavy reasoning',
    prompt: 'Design the architecture. [[ALLOW-NON-HAIKU]]',
    model: 'opus'
  }));
  assert.equal(res.status, 0);
  const out = JSON.parse(res.stdout);
  const hso = out.hookSpecificOutput;
  assert.equal(hso.hookEventName, 'PreToolUse');
  assert.equal(hso.permissionDecision, 'allow');
  assert.match(
    hso.permissionDecisionReason,
    /ALLOW-NON-HAIKU/,
    'bypass must be announced in permissionDecisionReason'
  );
  assert.equal(hso.updatedInput, undefined, 'escape hatch must not rewrite tool_input');
});

test('escape hatch use is appended to state/MODEL-POLICY-ESCAPES.log', () => {
  const res = runHook(JSON.stringify({
    hook_event_name: 'PreToolUse',
    tool_name: 'Agent',
    session_id: 'sess-123',
    cwd: 'C:\\somewhere',
    tool_input: {
      description: 'heavy reasoning',
      prompt: 'Design the architecture. [[ALLOW-NON-HAIKU]]',
      model: 'opus'
    }
  }));
  assert.equal(res.status, 0);
  const logPath = path.join(res.root, ESCAPE_LOG);
  assert.ok(fs.existsSync(logPath), 'escape-hatch use must be logged to ' + ESCAPE_LOG);
  const lines = fs.readFileSync(logPath, 'utf8').trim().split('\n');
  assert.equal(lines.length, 1);
  const rec = JSON.parse(lines[0]);
  assert.equal(rec.event, 'model_policy_escape');
  assert.equal(rec.tool, 'Agent');
  assert.equal(rec.session_id, 'sess-123');
  assert.equal(rec.requested_model, 'opus');
  assert.ok(rec.ts, 'log record must carry a timestamp');
});

test('escape-hatch log is append-only across uses', () => {
  const body = payload('Task', {
    description: 'big design',
    prompt: 'x [[ALLOW-NON-HAIKU]]',
    model: 'sonnet'
  });
  const first = runHook(body);
  // Re-run against the same root so the second use appends.
  const second = spawnSync(process.execPath, [HOOK], {
    input: body,
    cwd: first.root,
    env: { ...process.env, AESOP_ROOT: first.root },
    encoding: 'utf8',
    timeout: getTestTimeout(),
    killSignal: 'SIGKILL'
  });
  assert.equal(first.status, 0);
  assert.equal(second.status, 0);
  const lines = fs.readFileSync(path.join(first.root, ESCAPE_LOG), 'utf8').trim().split('\n');
  assert.equal(lines.length, 2, 'each escape-hatch use must append one record');
});

test('normal dispatch (no escape hatch) writes no escape log', () => {
  const res = runHook(payload('Agent', {
    description: 'do a thing',
    prompt: 'Implement the feature.',
    model: 'opus'
  }));
  assert.equal(res.status, 0);
  assert.ok(!fs.existsSync(path.join(res.root, ESCAPE_LOG)));
});

test('never-closing stdin: hook exits 0 within the timeout window (fail-open)', async () => {
  const root = makeRoot();
  const child = spawn(process.execPath, [HOOK], {
    cwd: root,
    env: { ...process.env, AESOP_ROOT: root },
    stdio: ['pipe', 'pipe', 'pipe'],
    timeout: getTestTimeout(),
    killSignal: 'SIGKILL'
  });
  child.stdin.on('error', () => {}); // child may exit first; ignore EPIPE
  let stdout = '';
  child.stdout.on('data', (d) => { stdout += d; });
  // Pipe a full, valid payload but NEVER close stdin.
  child.stdin.write(payload('Agent', { description: 'x', prompt: 'y', model: 'opus' }));
  const exitCode = await new Promise((resolve) => {
    const killer = setTimeout(() => { child.kill('SIGKILL'); resolve('HUNG'); }, 5000);
    child.on('exit', (code) => {
      clearTimeout(killer);
      // Clean up streams to prevent lingering handles on Linux
      child.stdout?.destroy();
      child.stderr?.destroy();
      child.stdin?.destroy();
      resolve(code);
    });
  });
  assert.notEqual(exitCode, 'HUNG', 'hook must not hang when stdin never closes');
  assert.equal(exitCode, 0, 'timeout path must exit 0');
  assert.equal(stdout.trim(), '', 'timeout path must fail-open with no rewrite');
});

test('malformed stdin: no output, exit 0 (fail-open, never crash)', () => {
  const res = runHook('this is { not json');
  assert.equal(res.status, 0, 'hook must never crash the harness');
  assert.equal(res.stdout.trim(), '');
});

test('unrelated tool names are ignored', () => {
  const res = runHook(payload('Bash', { command: 'ls', model: 'opus' }));
  assert.equal(res.status, 0);
  assert.equal(res.stdout.trim(), '');
});

test('aesop.config.json cardinal_rules.subagent_model overrides the default', () => {
  const res = runHook(
    payload('Agent', { description: 'x', prompt: 'y', model: 'opus' }),
    { config: { cardinal_rules: { subagent_model: 'haiku-4-5' } } }
  );
  assert.equal(res.status, 0);
  const out = JSON.parse(res.stdout);
  assert.equal(out.hookSpecificOutput.updatedInput.model, 'haiku-4-5');
});

test('config model is also honored as compliant (no rewrite when it matches)', () => {
  const res = runHook(
    payload('Agent', { description: 'x', prompt: 'y', model: 'haiku-4-5' }),
    { config: { cardinal_rules: { subagent_model: 'haiku-4-5' } } }
  );
  assert.equal(res.status, 0);
  assert.equal(res.stdout.trim(), '');
});
