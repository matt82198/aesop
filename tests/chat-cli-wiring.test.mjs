import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// S1 spec for the `aesop chat` wiring in bin/cli.js. These tests spawn the real CLI, which is
// the only file in the chat suite allowed to do so. Every spawn uses a fresh temp cwd that
// must still be empty afterwards, because an unknown verb scaffolds into cwd.
//
// Pinned surface:
//   `aesop chat --help` exits 0 and prints the slash command list on stdout.
//   `aesop chat` with stdout not a TTY exits 2 with exactly one stderr line naming `aesop pipe`.
//   bare `aesop` with --chat-default or AESOP_DEFAULT=chat, on a non-TTY, stays the pipe filter
//     (chat is only entered on a real TTY, so pipes are never hijacked).
//
// Run: node --test --test-reporter=tap tests/chat-cli-wiring.test.mjs

const testDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(testDir, '..');
const cliPath = join(repoRoot, 'bin', 'cli.js');
const SLASH_COMMANDS = ['/help', '/model', '/auth', '/clear', '/exit', '/quit'];

function freshTempDir() {
  return mkdtempSync(join(tmpdir(), 'aesop-chat-wire-'));
}

function safeRm(p) {
  try {
    rmSync(p, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  } catch {
    // Windows can hold handles briefly; cleanup never fails a test.
  }
}

/**
 * Spawn `node bin/cli.js <args>` with stdio piped (so the child sees non-TTY streams).
 * Runs in a fresh temp cwd unless one is given; the caller asserts emptiness via readdirSync.
 * Optional env entries are merged over the parent env.
 */
async function runCli(args, { cwd, input = Buffer.alloc(0), env = {} } = {}) {
  const runDir = cwd ?? freshTempDir();
  return new Promise((resolve, reject) => {
    const proc = spawn(process.execPath, [cliPath, ...args], {
      cwd: runDir,
      env: { ...process.env, ...env },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    const out = [];
    const err = [];
    const killTimer = setTimeout(() => proc.kill(), 20000);
    proc.stdout.on('data', (c) => out.push(c));
    proc.stderr.on('data', (c) => err.push(c));
    proc.stdin.on('error', () => {});
    proc.on('error', (e) => {
      clearTimeout(killTimer);
      reject(e);
    });
    proc.on('close', (code, signal) => {
      clearTimeout(killTimer);
      resolve({
        code,
        signal,
        stdout: Buffer.concat(out),
        stderr: Buffer.concat(err).toString('utf8'),
        runDir,
      });
    });
    proc.stdin.end(input);
  });
}

test('chat_help_exits_0_and_lists_every_slash_command', async () => {
  const dir = freshTempDir();
  try {
    const r = await runCli(['chat', '--help'], { cwd: dir });
    assert.equal(r.code, 0, `aesop chat --help must exit 0; got ${r.code}; stderr: ${r.stderr}`);
    for (const cmd of SLASH_COMMANDS) {
      assert.ok(r.stdout.includes(cmd), `aesop chat --help stdout must list ${cmd}; stdout:\n${r.stdout.toString('utf8')}`);
    }
    const entries = readdirSync(dir);
    assert.deepEqual(entries, [], `aesop chat --help must not scaffold into cwd; found: ${entries.join(', ')}`);
  } finally {
    safeRm(dir);
  }
});

test('chat_non_tty_stdout_exits_2_with_one_line_naming_aesop_pipe', async () => {
  const dir = freshTempDir();
  try {
    const r = await runCli(['chat'], { cwd: dir });
    assert.equal(r.code, 2, `aesop chat with a non-TTY stdout must exit 2; got ${r.code}; stderr: ${r.stderr}`);
    assert.equal(r.stdout.length, 0, 'aesop chat refusing a non-TTY must write nothing to stdout');
    const lines = r.stderr.replace(/\r?\n$/, '').split(/\r?\n/);
    assert.equal(lines.length, 1, `stderr must be exactly one line; got ${lines.length}: ${r.stderr}`);
    assert.match(lines[0], /aesop pipe/, `the single stderr line must name \`aesop pipe\`; got: ${lines[0]}`);
    const entries = readdirSync(dir);
    assert.deepEqual(entries, [], `aesop chat must not scaffold into cwd; found: ${entries.join(', ')}`);
  } finally {
    safeRm(dir);
  }
});

test('bare_aesop_chat_default_flag_on_non_tty_stays_pipe_filter', async () => {
  const dir = freshTempDir();
  const payload = Buffer.from([0x00, 0x41, 0x0a, 0x7f, 0xff]);
  try {
    const flagged = await runCli(['--chat-default'], { cwd: dir, input: payload });
    assert.doesNotMatch(flagged.stderr, /unknown option/i, '--chat-default must be a documented flag, not an unknown option');
    assert.equal(flagged.code, 0, `bare aesop --chat-default on a pipe must exit 0; got ${flagged.code}; stderr: ${flagged.stderr}`);
    assert.ok(
      flagged.stdout.equals(payload),
      'bare aesop --chat-default on a pipe must still pass bytes through (chat is TTY-only)',
    );

    const viaEnv = await runCli([], { cwd: dir, input: payload, env: { AESOP_DEFAULT: 'chat' } });
    assert.equal(viaEnv.code, 0, `bare aesop with AESOP_DEFAULT=chat on a pipe must exit 0; got ${viaEnv.code}; stderr: ${viaEnv.stderr}`);
    assert.ok(
      viaEnv.stdout.equals(payload),
      'bare aesop with AESOP_DEFAULT=chat on a pipe must still pass bytes through',
    );

    const entries = readdirSync(dir);
    assert.deepEqual(entries, [], `neither form may scaffold into cwd; found: ${entries.join(', ')}`);
  } finally {
    safeRm(dir);
  }
});
