import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

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

const chatCliPath = join(repoRoot, 'chat', 'cli.mjs');

test('chat_default_seam_should_enter_chat_truth_table', async () => {
  const mod = await import(pathToFileURL(chatCliPath).href);
  assert.equal(
    typeof mod.shouldEnterChat,
    'function',
    'chat/cli.mjs must export shouldEnterChat({interactive, args, env}) as the --chat-default dispatch seam',
  );
  const { shouldEnterChat } = mod;
  assert.equal(
    shouldEnterChat({ interactive: true, args: ['--chat-default'], env: {} }),
    true,
    '--chat-default on a TTY must enter chat',
  );
  assert.equal(
    shouldEnterChat({ interactive: true, args: [], env: { AESOP_DEFAULT: 'chat' } }),
    true,
    'AESOP_DEFAULT=chat on a TTY must enter chat',
  );
  assert.equal(
    shouldEnterChat({ interactive: false, args: ['--chat-default'], env: {} }),
    false,
    '--chat-default on a non-TTY must not enter chat (pipes stay the filter)',
  );
  assert.equal(
    shouldEnterChat({ interactive: true, args: [], env: {} }),
    false,
    'a bare TTY invocation with neither opt-in must not enter chat',
  );
});

// bin/cli.js has no documented override for the TTY decision: detectMode (stdio/tty-decision.js)
// reads process.stdin and process.stdout directly. This test therefore makes both streams
// TTY-shaped with a preload module (NODE_OPTIONS --require) and drives the real entry: a bare
// `aesop --chat-default` must reach shouldEnterChat -> runChat and print the chat banner. Ctrl-d on
// the empty line then leaves chat with exit 0. No regex over source text.
function writeTtyShim() {
  const shimDir = freshTempDir();
  const shim = join(shimDir, 'tty-shim.cjs');
  writeFileSync(shim, [
    'process.stdin.isTTY = true;',
    'process.stdout.isTTY = true;',
    'process.stdin.setRawMode = () => {};',
  ].join('\n'));
  return { shimDir, shimPath: shim.replace(/\\/g, '/') };
}

test('bin_cli_bare_chat_default_on_tty_enters_chat_and_prints_banner', async () => {
  const dir = freshTempDir();
  const { shimDir, shimPath } = writeTtyShim();
  try {
    const r = await runCli(['--chat-default'], {
      cwd: dir,
      input: Buffer.from('\x04', 'utf8'),
      env: { NODE_OPTIONS: `--require "${shimPath}"` },
    });
    const out = r.stdout.toString('utf8');
    assert.equal(r.code, 0, `bare aesop --chat-default on a TTY must exit 0 after ctrl-d; got ${r.code}; stderr: ${r.stderr}`);
    assert.match(
      out,
      /aesop \S+ - chat/,
      `bare aesop --chat-default on a TTY must enter chat and print the chat banner; stdout:\n${out}`,
    );
  } finally {
    safeRm(dir);
    safeRm(shimDir);
  }
});

test('bin_cli_bare_chat_default_on_tty_does_not_scaffold_into_cwd', async () => {
  const dir = freshTempDir();
  const { shimDir, shimPath } = writeTtyShim();
  try {
    await runCli(['--chat-default'], {
      cwd: dir,
      input: Buffer.from('\x04', 'utf8'),
      env: { NODE_OPTIONS: `--require "${shimPath}"` },
    });
    const entries = readdirSync(dir);
    assert.deepEqual(entries, [], `entering chat must not create the default aesop-fleet target or anything else in cwd; found: ${entries.join(', ')}`);
  } finally {
    safeRm(dir);
    safeRm(shimDir);
  }
});

// ---------------------------------------------------------------------------------------------
// B: one chat-entry decider (review round 2, finding B). bin/cli.js must take the bare-TTY entry
// decision from chat/cli.mjs shouldEnterChat. bareChatEntry (about bin/cli.js:219) re-implements the
// predicate, and the shouldEnterChat block near bin/cli.js:1738 is unreachable. The stub hook swaps
// chat/cli.mjs for tests/helpers/chat-entry-stub.mjs in the child, so the test can flip the decision
// and observe whether bin/cli.js follows it.
// ---------------------------------------------------------------------------------------------

const STUB_MARKER = 'STUB-CHAT-ENTERED';
const stubRegisterUrl = pathToFileURL(join(repoRoot, 'tests', 'helpers', 'chat-entry-stub-register.mjs')).href;

/**
 * Runs `node bin/cli.js <args>` on a shimmed TTY with chat/cli.mjs stubbed. stubEnter is what the
 * stub shouldEnterChat returns ('1' = enter, '0' = do not enter). AESOP_DEFAULT is cleared so only
 * the args decide.
 */
async function runBareTtyWithStubbedDecider(args, stubEnter) {
  const dir = freshTempDir();
  const { shimDir, shimPath } = writeTtyShim();
  try {
    const r = await runCli(args, {
      cwd: dir,
      input: Buffer.from('\x04', 'utf8'),
      env: {
        NODE_OPTIONS: `--require "${shimPath}" --import "${stubRegisterUrl}"`,
        AESOP_TEST_STUB_SHOULD_ENTER: stubEnter,
        AESOP_DEFAULT: '',
      },
    });
    return { r, stdout: r.stdout.toString('utf8') };
  } finally {
    safeRm(dir);
    safeRm(shimDir);
  }
}

function countOccurrences(haystack, needle) {
  return haystack.split(needle).length - 1;
}

test('bare_tty_entry_does_not_enter_chat_when_shouldEnterChat_says_no', async () => {
  const { r, stdout } = await runBareTtyWithStubbedDecider(['--chat-default'], '0');
  assert.ok(
    !stdout.includes(STUB_MARKER),
    `bin/cli.js must take the bare-TTY entry decision from shouldEnterChat: with the decider returning false it must not enter chat even with --chat-default; exit ${r.code}; stdout:\n${stdout}`,
  );
});

test('bare_tty_entry_enters_chat_when_shouldEnterChat_says_yes', async () => {
  const { r, stdout } = await runBareTtyWithStubbedDecider([], '1');
  assert.ok(
    stdout.includes(STUB_MARKER),
    `bin/cli.js must take the bare-TTY entry decision from shouldEnterChat: with the decider returning true and no flags it must enter chat; exit ${r.code}; stdout:\n${stdout}`,
  );
});

// Item 2 (review round 3): bin/cli.js attaches its error handler only to the import step. A synchronous
// throw from shouldEnterChat (or from startScaffold) inside the onOk continuation becomes an unhandled
// rejection. The file's contract is an "Error: ..." line on stderr with exit code 2, no stack trace.
test('bare_tty_entry_sync_throw_from_shouldEnterChat_exits_2_with_error_line_and_no_stack', async () => {
  const dir = freshTempDir();
  const { shimDir, shimPath } = writeTtyShim();
  try {
    const r = await runCli(['--chat-default'], {
      cwd: dir,
      input: Buffer.from('\x04', 'utf8'),
      env: {
        NODE_OPTIONS: `--require "${shimPath}" --import "${stubRegisterUrl}"`,
        AESOP_TEST_STUB_THROW: '1',
        AESOP_TEST_STUB_SHOULD_ENTER: '1',
        AESOP_DEFAULT: '',
      },
    });
    assert.equal(
      r.code,
      2,
      `a synchronous throw from shouldEnterChat must exit 2 (the bin/cli.js error contract), not crash with an unhandled rejection; got ${r.code}; stderr:\n${r.stderr}`,
    );
    assert.match(
      r.stderr,
      /^Error:/,
      `stderr must start with "Error:" (one-line error contract); stderr:\n${r.stderr}`,
    );
    assert.doesNotMatch(
      r.stderr,
      /^ {4}at /m,
      `stderr must carry no stack frame lines ("    at "); stderr:\n${r.stderr}`,
    );
  } finally {
    safeRm(dir);
    safeRm(shimDir);
  }
});

test('bin_cli_has_one_chat_entry_decider_before_the_scaffold_path', async () => {
  const src = readFileSync(cliPath, 'utf8');
  assert.equal(
    countOccurrences(src, "AESOP_DEFAULT === 'chat'"),
    0,
    "bin/cli.js must not re-derive the chat predicate; AESOP_DEFAULT === 'chat' belongs only in chat/cli.mjs shouldEnterChat",
  );
  assert.equal(
    countOccurrences(src, "includes('--chat-default')"),
    0,
    "bin/cli.js must not re-derive the chat predicate; args.includes('--chat-default') belongs only in chat/cli.mjs shouldEnterChat",
  );
  const calls = countOccurrences(src, 'shouldEnterChat(');
  assert.equal(calls, 1, `bin/cli.js must call shouldEnterChat exactly once (the single chat-entry decider); found ${calls} call sites`);
  const callAt = src.indexOf('shouldEnterChat(');
  const scaffoldAt = src.indexOf('function resolvePythonInterpreter');
  assert.ok(
    scaffoldAt > callAt && callAt >= 0,
    `the shouldEnterChat call (offset ${callAt}) must come before the scaffold path (offset ${scaffoldAt}); a second chat-entry block after the scaffold is dead code`,
  );
});
