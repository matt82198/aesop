import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { PassThrough } from 'node:stream';

// Behavioural tests for the TTY-vs-pipe seam (stdio/tty.mjs) and the CLI wiring of the
// stdio pipeline (`aesop pipe`, and the bare `aesop` no-argument filter). Nothing here
// greps source text: every assertion drives the real module or the real CLI process.

const testDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(testDir, '..');
const cliPath = join(repoRoot, 'bin', 'cli.js');
const ttyPath = join(repoRoot, 'stdio', 'tty.mjs');
const pkgVersion = JSON.parse(readFileSync(join(repoRoot, 'package.json'), 'utf8')).version;

// Deterministic binary payload: NUL, CR, LF, ESC, high bytes, a UTF-8 sequence, and a
// 4 KiB ramp covering every byte value. Any text-mode or newline translation corrupts it.
const BINARY_PAYLOAD = Buffer.concat([
  Buffer.from([0x00, 0x01, 0x0d, 0x0a, 0x1b, 0x5b, 0x30, 0x6d, 0x7f, 0x80, 0xfe, 0xff, 0xe2, 0x82, 0xac, 0x00]),
  Buffer.from(Array.from({ length: 4096 }, (_, i) => (i * 131 + 7) & 0xff)),
]);

/** Import the seam module lazily so a missing file fails only the test that needs it. */
async function loadTty() {
  return import(pathToFileURL(ttyPath).href);
}

/** Remove a path; cleanup is best-effort and never fails a test (Windows holds handles briefly). */
function safeRm(p) {
  try {
    rmSync(p, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  } catch {
    // ignore
  }
}

/** Fresh empty temp directory, unique per call. */
function freshTempDir() {
  return mkdtempSync(join(tmpdir(), 'aesop-stdio-wire-'));
}

/**
 * Run `node bin/cli.js <args>` with the given stdin bytes. stdio is always piped, so the
 * child sees stdin/stdout as non-TTY. Unless a cwd is given, the child runs in a fresh
 * temp directory that is removed afterwards, so a scaffolding regression cannot pollute
 * the repo tree.
 */
async function runCli(args, { cwd, input = Buffer.alloc(0) } = {}) {
  const ownCwd = cwd === undefined;
  const runDir = ownCwd ? freshTempDir() : cwd;
  try {
    return await new Promise((resolve, reject) => {
      const proc = spawn(process.execPath, [cliPath, ...args], {
        cwd: runDir,
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      });
      const out = [];
      const err = [];
      const killTimer = setTimeout(() => proc.kill(), 20000);
      proc.stdout.on('data', (chunk) => out.push(chunk));
      proc.stderr.on('data', (chunk) => err.push(chunk));
      // The child may exit before reading stdin (EPIPE); that is not a failure of the test.
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
        });
      });
      proc.stdin.end(input);
    });
  } finally {
    if (ownCwd) safeRm(runDir);
  }
}

// ---------------------------------------------------------------------------
// (a) The TTY seam: stdio/tty.mjs
// ---------------------------------------------------------------------------

test('tty_isTTY_false_for_pipes_and_missing_streams', async () => {
  const { isTTY } = await loadTty();
  assert.equal(isTTY(undefined), false, 'undefined stream is not a TTY');
  assert.equal(isTTY(null), false, 'null stream is not a TTY');
  assert.equal(isTTY({}), false, 'object without isTTY is not a TTY');
  assert.equal(isTTY({ isTTY: false }), false, 'isTTY:false is not a TTY');
  assert.equal(isTTY(new PassThrough()), false, 'a non-TTY stream is not a TTY');

  // A real child pipe: spawn with stdio pipes and check the parent-side streams.
  const child = spawn(process.execPath, ['-e', ''], { stdio: ['pipe', 'pipe', 'pipe'] });
  try {
    assert.equal(isTTY(child.stdin), false, 'child stdin pipe is not a TTY');
    assert.equal(isTTY(child.stdout), false, 'child stdout pipe is not a TTY');
  } finally {
    child.stdin.on('error', () => {});
    child.stdin.end();
    await new Promise((resolve) => child.on('close', resolve));
  }
});

test('tty_isTTY_true_only_for_stream_with_isTTY_true', async () => {
  const { isTTY } = await loadTty();
  assert.equal(isTTY({ isTTY: true }), true, 'stream with isTTY === true is a TTY');
  assert.equal(isTTY({ isTTY: 1 }), false, 'truthy non-boolean isTTY must not count (strict === true)');
  assert.equal(isTTY({ isTTY: 'true' }), false, 'string "true" must not count');
});

test('tty_detectMode_interactive_only_when_both_are_tty', async () => {
  const { detectMode } = await loadTty();
  assert.equal(
    detectMode({ stdin: { isTTY: true }, stdout: { isTTY: true } }),
    'interactive',
    'both streams TTY must select interactive mode',
  );
});

test('tty_detectMode_filter_when_stdout_piped', async () => {
  const { detectMode } = await loadTty();
  assert.equal(detectMode({ stdin: { isTTY: true }, stdout: {} }), 'filter', 'piped stdout must select filter');
  assert.equal(
    detectMode({ stdin: { isTTY: true }, stdout: { isTTY: false } }),
    'filter',
    'explicit isTTY:false stdout must select filter',
  );
});

test('tty_detectMode_filter_when_stdin_piped', async () => {
  const { detectMode } = await loadTty();
  assert.equal(detectMode({ stdin: {}, stdout: { isTTY: true } }), 'filter', 'piped stdin must select filter');
  assert.equal(
    detectMode({ stdin: { isTTY: false }, stdout: { isTTY: true } }),
    'filter',
    'explicit isTTY:false stdin must select filter',
  );
});

// ---------------------------------------------------------------------------
// (b) `node bin/cli.js pipe [stdio flags]`
// ---------------------------------------------------------------------------

test('cli_pipe_verb_passes_binary_bytes_exact', async () => {
  const r = await runCli(['pipe'], { input: BINARY_PAYLOAD });
  assert.equal(r.code, 0, `pipe verb must exit 0 on valid input; stderr: ${r.stderr}`);
  assert.ok(
    r.stdout.equals(BINARY_PAYLOAD),
    `pipe verb must forward bytes untouched: expected ${BINARY_PAYLOAD.length} bytes, got ${r.stdout.length}`,
  );
});

test('cli_pipe_verb_frames_roundtrip_via_two_instances', async () => {
  // Instance A encodes the payload; instance B decodes A's output. The end-to-end result
  // must equal the original bytes exactly, proving the pair is a lossless pipeline.
  const a = await runCli(['pipe'], { input: BINARY_PAYLOAD });
  assert.equal(a.code, 0, `first pipe instance must exit 0; stderr: ${a.stderr}`);
  const b = await runCli(['pipe'], { input: a.stdout });
  assert.equal(b.code, 0, `second pipe instance must exit 0; stderr: ${b.stderr}`);
  assert.ok(
    b.stdout.equals(BINARY_PAYLOAD),
    `two-instance roundtrip must reproduce the original ${BINARY_PAYLOAD.length} bytes, got ${b.stdout.length}`,
  );
});

test('cli_pipe_verb_malformed_frames_exit_2_stderr_one_line', async () => {
  // A frame header declaring a length (0xFFFFFFFF) far beyond --max-frame 16 is malformed
  // under any framing scheme that honours --max-frame.
  const malformed = Buffer.from([0xff, 0xff, 0xff, 0xff, 0x00, 0x01, 0x02, 0x03]);
  const r = await runCli(['pipe', '--input-format', 'frames', '--max-frame', '16'], { input: malformed });
  assert.equal(r.code, 2, `malformed framing must exit 2; got ${r.code}; stderr: ${r.stderr}`);
  const lines = r.stderr.replace(/\r?\n$/, '').split(/\r?\n/);
  assert.equal(lines.length, 1, `stderr must be exactly one line; got ${lines.length}: ${r.stderr}`);
  assert.notEqual(lines[0].trim(), '', 'the single stderr line must not be empty');
  // The exit-2 must come from the malformed frame, not from rejecting the format name itself.
  assert.doesNotMatch(
    lines[0],
    /unknown format|invalid format/i,
    `the exit-2 must come from the malformed frame, not a rejected format name; stderr: ${lines[0]}`,
  );
  // Control: the same format name with a valid empty frame stream must succeed.
  const valid = await runCli(['pipe', '--input-format', 'frames'], { input: Buffer.from('AESOP-PIPE/1\nE 0\n') });
  assert.equal(valid.code, 0, `valid frames stream must exit 0; got ${valid.code}; stderr: ${valid.stderr}`);
});

test('cli_pipe_verb_accepts_stdio_flags_without_unknown_option_error', async () => {
  // The pipe verb's arguments bypass the Node-level unknown-flag whitelist. Whatever the
  // filter then does with the values, it must never reject a stdio flag as an unknown option.
  const r = await runCli(
    ['pipe', '--input-format', 'raw', '--output-format', 'raw', '--content-type', 'application/octet-stream', '--max-frame', '1024'],
    { input: Buffer.from('hello') },
  );
  assert.doesNotMatch(r.stderr, /unknown option/i, `stdio flags must reach the filter, not the whitelist; stderr: ${r.stderr}`);
  assert.doesNotMatch(r.stderr, /unknown (namespace|verb)/i, 'pipe must be a known verb');
});

// ---------------------------------------------------------------------------
// (c) `node bin/cli.js` with no arguments, stdin/stdout not both TTY
// ---------------------------------------------------------------------------

test('cli_no_args_non_tty_acts_as_filter_and_does_not_scaffold', async () => {
  const dir = freshTempDir();
  try {
    const r = await runCli([], { cwd: dir, input: BINARY_PAYLOAD });
    assert.equal(r.code, 0, `no-arg filter must exit 0; stderr: ${r.stderr}`);
    assert.ok(
      r.stdout.equals(BINARY_PAYLOAD),
      `no-arg filter must pass bytes through unchanged: expected ${BINARY_PAYLOAD.length}, got ${r.stdout.length}`,
    );
    const entries = readdirSync(dir);
    assert.deepEqual(entries, [], `no-arg filter must not scaffold into cwd; found: ${entries.join(', ')}`);
  } finally {
    safeRm(dir);
  }
});

// ---------------------------------------------------------------------------
// (d) Unchanged behaviour
// ---------------------------------------------------------------------------

test('cli_version_flag_unchanged', async () => {
  const r = await runCli(['--version']);
  assert.equal(r.code, 0, `--version must exit 0; stderr: ${r.stderr}`);
  assert.equal(r.stdout.toString('utf8').trim(), pkgVersion, '--version must print the package.json version');
});
