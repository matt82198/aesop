#!/usr/bin/env node
/**
 * Conformance contract for the native stdin/stdout pipeline (stdio/cli.mjs, stdio/pipe.mjs).
 *
 * Behavioural tests only: every case drives the real CLI (or runPipe) and asserts on observable
 * bytes, exit codes and stderr. Nothing here greps source text.
 *
 * Node stdlib only, node:test runner. Run with: node --test tests/stdio-pipe.test.mjs
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import { Readable, Writable } from 'node:stream';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLI = path.resolve(HERE, '..', 'stdio', 'cli.mjs');
const PIPE = path.resolve(HERE, '..', 'stdio', 'pipe.mjs');

const MAGIC = 'AESOP-PIPE/1\n';
const END = 'E 0\n';

// ============================================================================
// Helpers
// ============================================================================

/** Deterministic pseudo-random bytes (xorshift32). */
function detBytes(n, seed = 0x2545f491) {
  const out = Buffer.alloc(n);
  let x = seed >>> 0 || 1;
  for (let i = 0; i < n; i++) {
    x ^= x << 13;
    x >>>= 0;
    x ^= x >>> 17;
    x ^= x << 5;
    x >>>= 0;
    out[i] = x & 0xff;
  }
  return out;
}

function sha256Hex(buf) {
  return createHash('sha256').update(buf).digest('hex');
}

/** Run the CLI as a child process with the given argv and optional stdin bytes. */
function runCli(args, input) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [CLI, ...args], {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    const out = [];
    const err = [];
    child.stdout.on('data', (d) => out.push(d));
    child.stderr.on('data', (d) => err.push(d));
    // The child may exit before reading all of stdin (bad input, EPIPE); ignore the write error.
    child.stdin.on('error', () => {});
    child.on('error', reject);
    child.on('close', (code, signal) => {
      resolve({
        code,
        signal,
        stdout: Buffer.concat(out),
        stderr: Buffer.concat(err).toString('utf8'),
      });
    });
    child.stdin.end(input && input.length ? input : undefined);
  });
}

/** Build one frame: `F <channel> <content-type> <length>\n` + payload + `\n`. */
function frame(channel, contentType, payload) {
  const body = Buffer.isBuffer(payload) ? payload : Buffer.from(payload, 'utf8');
  return Buffer.concat([
    Buffer.from(`F ${channel} ${contentType} ${body.length}\n`, 'latin1'),
    body,
    Buffer.from('\n', 'latin1'),
  ]);
}

/**
 * Parse a complete frames-format stream. Asserts magic, well-formed headers, exact payload lengths,
 * payload terminators, and that the stream ends with the end frame with no trailing bytes.
 */
function parseFrames(buf) {
  assert.equal(
    buf.subarray(0, MAGIC.length).toString('latin1'),
    MAGIC,
    'frames output must begin with the AESOP-PIPE/1 magic line',
  );
  let pos = MAGIC.length;
  const frames = [];
  for (;;) {
    const nl = buf.indexOf(0x0a, pos);
    assert.notEqual(nl, -1, `frame header at offset ${pos} has no newline`);
    const header = buf.subarray(pos, nl).toString('latin1');
    pos = nl + 1;
    if (header === 'E 0') break;
    const m = /^F (\S+) (\S+) (\d+)$/.exec(header);
    assert.ok(m, `malformed frame header: ${JSON.stringify(header)}`);
    const len = Number(m[3]);
    const payload = buf.subarray(pos, pos + len);
    assert.equal(payload.length, len, 'frame payload truncated');
    pos += len;
    assert.equal(buf[pos], 0x0a, 'frame payload must be followed by a newline');
    pos += 1;
    frames.push({ channel: m[1], contentType: m[2], payload });
  }
  assert.equal(pos, buf.length, 'no bytes may follow the end frame');
  return frames;
}

function parseJsonLines(buf) {
  const text = buf.toString('utf8');
  assert.ok(text.endsWith('\n'), 'JSON Lines output must end with a newline');
  return text
    .split('\n')
    .filter((l) => l.length > 0)
    .map((l) => JSON.parse(l));
}

/** A failing run must carry exactly one diagnostic line on stderr, prefixed `aesop-pipe: `. */
function assertOneLineDiagnostic(stderr, label = '') {
  const lines = stderr.replace(/\r?\n$/, '').split(/\r?\n/);
  assert.equal(lines.length, 1, `${label}stderr must be exactly one line, got: ${JSON.stringify(stderr)}`);
  assert.match(lines[0], /^aesop-pipe: /, `${label}stderr must start with "aesop-pipe: "`);
}

// ============================================================================
// Passthrough: raw -> raw is byte exact
// ============================================================================

test('passthrough_byte_exact_for_json_csv_xml_text', async () => {
  const samples = [
    '{"name":"Matt","tags":["a","b"],"n":1.5e3}\n',
    'id,name,note\n1,"Smith, J",ok\n2,Ann,"multi\nline"\n',
    '<?xml version="1.0" encoding="UTF-8"?>\n<root><item id="1">café &amp; tea</item></root>\n',
    'plain text with unicode: 日本語 ☃ and a tab\there\n',
  ];
  for (const s of samples) {
    const input = Buffer.from(s, 'utf8');
    const r = await runCli([], input);
    assert.equal(r.code, 0, `exit code for sample ${JSON.stringify(s.slice(0, 20))}`);
    assert.equal(r.stderr, '');
    assert.ok(r.stdout.equals(input), 'stdout must equal stdin byte-for-byte');
  }
});

test('passthrough_byte_exact_binary_with_embedded_newlines_and_nul', async () => {
  const input = Buffer.concat([
    Buffer.from([0x00, 0x0a, 0x0d, 0x00, 0x0a, 0x0a, 0x00]),
    detBytes(70000, 0x1234),
    Buffer.from([0x0d, 0x0a, 0x00, 0x00, 0xff, 0x0a]),
  ]);
  const r = await runCli([], input);
  assert.equal(r.code, 0);
  assert.equal(r.stderr, '');
  assert.equal(r.stdout.length, input.length);
  assert.ok(r.stdout.equals(input), 'NUL and newline bytes must pass through unchanged');
});

test('passthrough_byte_exact_mixed_crlf_and_lf', async () => {
  const input = Buffer.from('alpha\r\nbeta\ngamma\r\n\r\ndelta\n\r\r\n', 'latin1');
  const r = await runCli([], input);
  assert.equal(r.code, 0);
  assert.ok(r.stdout.equals(input), 'CRLF and LF must not be translated');
});

test('passthrough_byte_exact_utf16_with_bom', async () => {
  // UTF-16LE with a leading BOM (FF FE). Any decoding or newline translation would corrupt it.
  const input = Buffer.from('﻿hello\r\nworld\n', 'utf16le');
  assert.equal(input[0], 0xff);
  assert.equal(input[1], 0xfe);
  const r = await runCli([], input);
  assert.equal(r.code, 0);
  assert.ok(r.stdout.equals(input), 'UTF-16 bytes must pass through unchanged');
});

test('passthrough_byte_exact_protobuf_like_binary_all_256_byte_values', async () => {
  const allBytes = Buffer.from(Array.from({ length: 256 }, (_, i) => i));
  const protoish = Buffer.from([0x08, 0x96, 0x01, 0x12, 0x05, 0x68, 0x65, 0x6c, 0x6c, 0x6f, 0x1a, 0x00]);
  const input = Buffer.concat([allBytes, protoish, allBytes]);
  const r = await runCli([], input);
  assert.equal(r.code, 0);
  assert.equal(r.stderr, '');
  assert.ok(r.stdout.equals(input), 'all 256 byte values must survive passthrough');
});

test('passthrough_empty_input_gives_empty_output_exit_0', async () => {
  const r = await runCli([], Buffer.alloc(0));
  assert.equal(r.code, 0);
  assert.equal(r.stdout.length, 0);
  assert.equal(r.stderr, '');
});

// ============================================================================
// Framing
// ============================================================================

test('raw_to_frames_to_raw_roundtrip_all_formats', async () => {
  const cases = [
    { ct: 'application/json', body: Buffer.from('{"a":[1,2,{"b":"c"}]}\n', 'utf8') },
    { ct: 'text/csv', body: Buffer.from('id,name\n1,"x, y"\n', 'utf8') },
    { ct: 'application/xml', body: Buffer.from('<?xml version="1.0"?><r><i>é</i></r>\n', 'utf8') },
    { ct: 'text/plain', body: Buffer.from('hello world\r\n', 'utf8') },
    { ct: 'application/octet-stream', body: Buffer.from(Array.from({ length: 256 }, (_, i) => i)) },
  ];
  for (const c of cases) {
    const framed = await runCli(['--output-format', 'frames', '--content-type', c.ct], c.body);
    assert.equal(framed.code, 0, `raw->frames exit for ${c.ct}`);
    assert.equal(framed.stderr, '');
    assert.equal(framed.stdout.subarray(0, MAGIC.length).toString('latin1'), MAGIC);
    assert.equal(framed.stdout.subarray(framed.stdout.length - END.length).toString('latin1'), END);
    const frames = parseFrames(framed.stdout);
    const data = frames.filter((f) => f.channel === 'data');
    assert.ok(data.length >= 1, `at least one data frame for ${c.ct}`);
    for (const f of data) assert.equal(f.contentType, c.ct, 'data frames carry the declared content type');
    const restored = await runCli(['--input-format', 'frames'], framed.stdout);
    assert.equal(restored.code, 0, `frames->raw exit for ${c.ct}`);
    assert.equal(restored.stderr, '');
    assert.ok(restored.stdout.equals(c.body), `frames->raw must reproduce ${c.ct} exactly`);
  }
});

test('frames_split_at_max_frame', async () => {
  const MAX = 1024;
  const input = detBytes(10000, 0xabc);
  const framed = await runCli(['--output-format', 'frames', '--max-frame', String(MAX)], input);
  assert.equal(framed.code, 0);
  assert.equal(framed.stderr, '');
  const frames = parseFrames(framed.stdout);
  const data = frames.filter((f) => f.channel === 'data');
  // ceil(10000 / 1024) = 10 chunks is the minimum; no data frame may exceed the limit.
  assert.ok(data.length >= 10, `expected at least 10 data frames, got ${data.length}`);
  for (const f of data) {
    assert.ok(f.payload.length <= MAX, `data frame of ${f.payload.length} bytes exceeds --max-frame ${MAX}`);
    assert.ok(f.payload.length > 0, 'no empty data frames');
  }
  assert.ok(Buffer.concat(data.map((f) => f.payload)).equals(input), 'concatenated frame payloads must equal input');

  const restored = await runCli(['--input-format', 'frames', '--max-frame', String(MAX)], framed.stdout);
  assert.equal(restored.code, 0);
  assert.ok(restored.stdout.equals(input), 'split frames must reassemble to the original bytes');
});

test('raw_to_stream_json_to_raw_roundtrip_binary', async () => {
  const input = detBytes(300 * 1024 + 17, 0x11);
  const sj = await runCli(['--output-format', 'stream-json', '--content-type', 'application/x-test'], input);
  assert.equal(sj.code, 0);
  assert.equal(sj.stderr, '');
  const records = parseJsonLines(sj.stdout);
  assert.ok(records.length >= 2, 'at least one data record plus the result record');

  const last = records[records.length - 1];
  assert.equal(last.type, 'result');
  assert.equal(last.exit, 0);
  assert.equal(last.bytes, input.length);
  assert.equal(last.sha256, sha256Hex(input));

  const dataRecords = records.slice(0, -1);
  for (const rec of dataRecords) {
    assert.equal(rec.type, 'data');
    assert.equal(rec.content_type, 'application/x-test');
    assert.equal(rec.encoding, 'base64');
  }
  const decoded = Buffer.concat(dataRecords.map((rec) => Buffer.from(rec.data, 'base64')));
  assert.ok(decoded.equals(input), 'base64 data records must decode to the original bytes');

  const back = await runCli(['--input-format', 'stream-json'], sj.stdout);
  assert.equal(back.code, 0);
  assert.equal(back.stderr, '');
  assert.ok(back.stdout.equals(input), 'stream-json->raw must reproduce the original bytes');
});

test('json_summary_matches_sha256_and_length', async () => {
  const input = detBytes(4321, 0x77);
  const r = await runCli(['--output-format', 'json', '--content-type', 'application/octet-stream'], input);
  assert.equal(r.code, 0);
  assert.equal(r.stderr, '');
  const text = r.stdout.toString('utf8');
  assert.equal(text.indexOf('\n'), text.length - 1, 'summary is exactly one JSON line');
  const obj = JSON.parse(text);
  assert.deepEqual(Object.keys(obj).sort(), ['bytes', 'content_type', 'sha256']);
  assert.equal(obj.content_type, 'application/octet-stream');
  assert.equal(obj.bytes, input.length);
  assert.equal(obj.sha256, sha256Hex(input));
});

// ============================================================================
// Large streams and backpressure
// ============================================================================

test('large_stream_64MiB_completes_exact_sha256_via_child_process', { timeout: 60000 }, async () => {
  const CHUNK = 64 * 1024;
  const TOTAL = 64 * 1024 * 1024;
  const base = detBytes(CHUNK, 0x5eed);
  const expected = createHash('sha256');
  const actual = createHash('sha256');

  const child = spawn(process.execPath, [CLI], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  let stderr = '';
  let bytesOut = 0;
  child.stdout.on('data', (d) => {
    actual.update(d);
    bytesOut += d.length;
  });
  child.stderr.on('data', (d) => {
    stderr += d;
  });
  child.stdin.on('error', () => {});
  const closed = once(child, 'close');

  let sent = 0;
  for (let i = 0; sent < TOTAL; i++) {
    const chunk = Buffer.from(base);
    chunk.writeUInt32BE(i >>> 0, 0); // make every chunk distinct
    expected.update(chunk);
    sent += chunk.length;
    if (!child.stdin.write(chunk)) await once(child.stdin, 'drain');
  }
  child.stdin.end();

  const [code] = await closed;
  assert.equal(code, 0, 'child must exit 0');
  assert.equal(stderr, '');
  assert.equal(bytesOut, TOTAL, 'child must emit exactly the bytes sent');
  assert.equal(actual.digest('hex'), expected.digest('hex'), 'sha256 of child stdout must match input');
});

test('large_stream_memory_bounded', { timeout: 120000 }, async () => {
  const { runPipe } = await import(pathToFileURL(PIPE).href);
  assert.equal(typeof runPipe, 'function', 'stdio/pipe.mjs must export runPipe');

  const CHUNK = 64 * 1024;
  const TOTAL = 128 * 1024 * 1024;
  const MAX_BUFFERED = 8 * 1024 * 1024;
  const base = detBytes(CHUNK, 0xbeef);

  let produced = 0;
  let consumed = 0;
  let peakOutstanding = 0;
  const expected = createHash('sha256');
  const actual = createHash('sha256');

  const source = Readable.from(
    (async function* generate() {
      let left = TOTAL;
      for (let i = 0; left > 0; i++) {
        const n = Math.min(CHUNK, left);
        const chunk = Buffer.from(base.subarray(0, n));
        chunk.writeUInt32BE(i >>> 0, 0);
        expected.update(chunk);
        produced += n;
        peakOutstanding = Math.max(peakOutstanding, produced - consumed);
        left -= n;
        yield chunk;
      }
    })(),
  );

  const sink = new Writable({
    write(chunk, _enc, cb) {
      actual.update(chunk);
      consumed += chunk.length;
      setImmediate(cb); // slow consumer so backpressure actually engages
    },
  });

  let stderrText = '';
  const stderr = new Writable({
    write(chunk, _enc, cb) {
      stderrText += chunk.toString('utf8');
      cb();
    },
  });

  const code = await runPipe({ stdin: source, stdout: sink, stderr, argv: [] });
  assert.equal(code, 0, 'runPipe must resolve with exit code 0');
  assert.equal(stderrText, '', 'successful run must not write to stderr');
  assert.equal(consumed, TOTAL, 'sink must consume every generated byte');
  assert.equal(actual.digest('hex'), expected.digest('hex'), 'sha256 of sink bytes must match generated bytes');
  assert.ok(
    peakOutstanding < MAX_BUFFERED,
    `peak bytes produced but not yet consumed was ${peakOutstanding}, must stay under ${MAX_BUFFERED} (backpressure)`,
  );
});

// ============================================================================
// Failure modes
// ============================================================================

const MALFORMED = [
  {
    name: 'missing magic line',
    input: Buffer.from('NOT-A-PIPE\nF data application/octet-stream 1\nx\nE 0\n', 'latin1'),
    firstFrame: true,
  },
  {
    name: 'bad header with missing length field',
    input: Buffer.concat([Buffer.from(MAGIC), Buffer.from('F data application/octet-stream\n')]),
    firstFrame: true,
  },
  {
    name: 'non-numeric length',
    input: Buffer.concat([Buffer.from(MAGIC), Buffer.from('F data application/octet-stream abc\nxx\nE 0\n')]),
    firstFrame: true,
  },
  {
    name: 'negative length',
    input: Buffer.concat([Buffer.from(MAGIC), Buffer.from('F data application/octet-stream -5\nE 0\n')]),
    firstFrame: true,
  },
  {
    name: 'length over --max-frame',
    args: ['--max-frame', '4'],
    input: Buffer.concat([Buffer.from(MAGIC), frame('data', 'application/octet-stream', '0123456789'), Buffer.from(END)]),
    firstFrame: true,
  },
  {
    name: 'truncated payload (EOF mid-frame)',
    input: Buffer.concat([Buffer.from(MAGIC), Buffer.from('F data application/octet-stream 10\nabc')]),
    firstFrame: true,
  },
  {
    name: 'missing end frame after valid data',
    input: Buffer.concat([Buffer.from(MAGIC), frame('data', 'application/octet-stream', 'hello')]),
    firstFrame: false,
  },
  {
    name: 'empty input (EOF before magic)',
    input: Buffer.alloc(0),
    firstFrame: true,
  },
  {
    name: 'unknown frame kind letter',
    input: Buffer.concat([Buffer.from(MAGIC), Buffer.from('X data application/octet-stream 1\nx\n'), Buffer.from(END)]),
    firstFrame: true,
  },
  {
    name: 'payload not followed by a newline terminator',
    input: Buffer.concat([Buffer.from(MAGIC), Buffer.from('F data application/octet-stream 3\nabcXE 0\n')]),
    firstFrame: true,
  },
];

test('malformed_frames_fail_closed', async () => {
  assert.ok(MALFORMED.length >= 8, 'table must hold at least 8 malformed inputs');
  for (const c of MALFORMED) {
    const r = await runCli(['--input-format', 'frames', ...(c.args || [])], c.input);
    assert.equal(r.code, 2, `${c.name}: exit code must be 2`);
    assert.equal(r.signal, null, `${c.name}: must exit normally, not by signal`);
    assertOneLineDiagnostic(r.stderr, `${c.name}: `);
    if (c.firstFrame) {
      assert.equal(r.stdout.length, 0, `${c.name}: stdout must be empty when the first frame is bad`);
    }
  }
});

test('control_unknown_critical_message_exits_2', async () => {
  const unknownCritical = frame('control', 'application/json', '{"type":"mystery","critical":true}');
  const r = await runCli(
    ['--input-format', 'frames'],
    Buffer.concat([Buffer.from(MAGIC), unknownCritical, frame('data', 'application/octet-stream', 'x'), Buffer.from(END)]),
  );
  assert.equal(r.code, 2);
  assertOneLineDiagnostic(r.stderr);

  // A control frame of unknown type without an explicit "critical": false is also fail-closed.
  const noFlag = frame('control', 'application/json', '{"type":"mystery"}');
  const r2 = await runCli(
    ['--input-format', 'frames'],
    Buffer.concat([Buffer.from(MAGIC), noFlag, Buffer.from(END)]),
  );
  assert.equal(r2.code, 2, 'unknown control type without "critical": false must exit 2');
  assertOneLineDiagnostic(r2.stderr);
});

test('control_unknown_noncritical_ignored', async () => {
  const input = Buffer.concat([
    Buffer.from(MAGIC),
    frame('control', 'application/json', '{"type":"hello","version":1}'),
    frame('control', 'application/json', '{"type":"meta","k":"v"}'),
    frame('control', 'application/json', '{"type":"mystery","critical":false}'),
    frame('data', 'application/octet-stream', 'hello'),
    frame('control', 'application/json', '{"type":"done"}'),
    Buffer.from(END),
  ]);
  const r = await runCli(['--input-format', 'frames'], input);
  assert.equal(r.code, 0, 'non-critical unknown control frames are accepted');
  assert.equal(r.stderr, '');
  assert.equal(r.stdout.toString('utf8'), 'hello', 'control frames must never be written to stdout');
});

test('stream_json_invalid_line_exits_2', async () => {
  const validThenGarbage = Buffer.from(
    '{"type":"data","content_type":"text/plain","encoding":"utf8","data":"hi"}\nnot json at all\n',
    'utf8',
  );
  const r = await runCli(['--input-format', 'stream-json'], validThenGarbage);
  assert.equal(r.code, 2, 'a line that is not JSON must exit 2');
  assertOneLineDiagnostic(r.stderr);

  const arrayLine = await runCli(['--input-format', 'stream-json'], Buffer.from('[1,2,3]\n', 'utf8'));
  assert.equal(arrayLine.code, 2, 'a JSON line that is not an object must exit 2');
  assertOneLineDiagnostic(arrayLine.stderr);

  const unknownType = await runCli(['--input-format', 'stream-json'], Buffer.from('{"type":"weird"}\n', 'utf8'));
  assert.equal(unknownType.code, 2, 'an unknown record type must exit 2');
  assertOneLineDiagnostic(unknownType.stderr);
});

test('unknown_flag_exits_2', async () => {
  const cases = [
    ['--no-such-flag'],
    ['--input-format', 'yaml'],
    ['--output-format', 'xml'],
  ];
  for (const args of cases) {
    const r = await runCli(args, Buffer.alloc(0));
    assert.equal(r.code, 2, `argv ${JSON.stringify(args)} must exit 2`);
    assert.equal(r.stdout.length, 0, `argv ${JSON.stringify(args)} must leave stdout empty`);
    assertOneLineDiagnostic(r.stderr, `argv ${JSON.stringify(args)}: `);
  }
});

test('stdout_has_no_diagnostics_on_success', async () => {
  const input = Buffer.from('payload\n', 'utf8');
  const raw = await runCli([], input);
  assert.equal(raw.code, 0);
  assert.equal(raw.stderr, '', 'successful raw run writes nothing to stderr');
  assert.ok(raw.stdout.equals(input), 'stdout is exactly the payload');

  const framed = await runCli(['--output-format', 'frames'], input);
  assert.equal(framed.code, 0);
  assert.equal(framed.stderr, '', 'successful frames run writes nothing to stderr');
  parseFrames(framed.stdout); // throws if any non-protocol text leaked into stdout
});

test('stdout_stays_clean_on_failure', async () => {
  const badFirstFrame = Buffer.concat([Buffer.from(MAGIC), Buffer.from('F data application/octet-stream abc\n')]);
  const r = await runCli(['--input-format', 'frames'], badFirstFrame);
  assert.equal(r.code, 2);
  assert.equal(r.stdout.length, 0, 'nothing may reach stdout when the first frame is malformed');
  assertOneLineDiagnostic(r.stderr);
});

test('epipe_downstream_close_exits_0', { timeout: 60000 }, async () => {
  // Close our read end of the CLI's stdout after the first bytes arrive, so the CLI's next write
  // hits EPIPE. The CLI must exit 0 quietly.
  const input = detBytes(8 * 1024 * 1024, 0xe1);
  const child = spawn(process.execPath, [CLI], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  let stderr = '';
  child.stderr.on('data', (d) => {
    stderr += d;
  });
  child.stdin.on('error', () => {});
  // The flag proves the destroy actually ran: an implementation that writes nothing never fires
  // 'data', so without it the test would pass vacuously.
  let dataSeen = false;
  child.stdout.once('data', () => {
    dataSeen = true;
    child.stdout.destroy();
  });
  const closed = once(child, 'close');
  child.stdin.end(input);

  const [code, signal] = await closed;
  assert.ok(dataSeen, 'the CLI must write to stdout before the downstream close can produce EPIPE');
  assert.equal(signal, null, 'CLI must not be killed by a signal');
  assert.equal(code, 0, 'closed downstream must exit 0');
  assert.equal(stderr, '', 'EPIPE must be handled quietly');
});

test('chained_two_instances_via_shell_pipe', { timeout: 60000 }, async () => {
  const input = detBytes(200 * 1024 + 3, 0xc4);
  const node = process.execPath;
  const cmd = `"${node}" "${CLI}" --output-format frames | "${node}" "${CLI}" --input-format frames`;
  const child = spawn(cmd, { shell: true, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  const out = [];
  const err = [];
  child.stdout.on('data', (d) => out.push(d));
  child.stderr.on('data', (d) => err.push(d));
  child.stdin.on('error', () => {});
  const closed = once(child, 'close');
  child.stdin.end(input);

  const [code] = await closed;
  assert.equal(code, 0, 'pipeline exits with the last stage status');
  assert.equal(Buffer.concat(err).toString('utf8'), '', 'no stderr from either stage');
  assert.ok(Buffer.concat(out).equals(input), 'frames -> frames pipeline must reproduce the input exactly');
});

// ============================================================================
// S5 contract: an unknown op after an answered op (bin/cli.js pipe, stream-json on both sides)
// ============================================================================

const BIN_CLI = path.resolve(HERE, '..', 'bin', 'cli.js');

/** Spawn `node bin/cli.js <args>` in a given cwd and env; resolves with code, signal and both streams. */
function runBinCli(args, input, { cwd, env }) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [BIN_CLI, ...args], { cwd, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    const out = [];
    const err = [];
    child.stdout.on('data', (d) => out.push(d));
    child.stderr.on('data', (d) => err.push(d));
    child.stdin.on('error', () => {});
    child.on('error', reject);
    child.on('close', (code, signal) => {
      resolve({ code, signal, stdout: Buffer.concat(out).toString('utf8'), stderr: Buffer.concat(err).toString('utf8') });
    });
    child.stdin.end(input);
  });
}

// R-CONTROL-1 (ruling, event store): an op failure is an ok:false envelope and the pipe keeps running, exit 0.
// The expectation below (exit 0, not 2) is the R-CONTROL-1 change from the earlier exit-2 contract.
test('unknown_op_after_answered_op_gets_error_envelope_and_pipe_keeps_running_exit_0', { timeout: 60000 }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-pipe-unknown-op-'));
  try {
    const home = path.join(root, 'home');
    const cwd = path.join(root, 'cwd');
    fs.mkdirSync(home);
    fs.mkdirSync(cwd);
    const env = {
      ...process.env,
      HOME: home,
      USERPROFILE: home,
      AESOP_CONFIG_DIR: path.join(root, 'config'),
      ANTHROPIC_API_KEY: '',
      AESOP_API_KEY: '',
    };
    const input =
      JSON.stringify({ type: 'control', op: 'model.list', id: 'a' }) + '\n' +
      JSON.stringify({ type: 'control', op: 'nope', id: 'b' }) + '\n';
    const r = await runBinCli(['pipe', '--input-format', 'stream-json', '--output-format', 'stream-json'], input, { cwd, env });
    assert.equal(r.signal, null, 'must exit normally, not by signal');
    // S5 item 5: an op failure is an envelope on stdout (ok:false, same op and id), not a stop.
    // The pipe keeps running, so the record after the failure is still read and the exit is 0.
    assert.equal(r.code, 0, `an unknown op is an op failure: the pipe must keep running and exit 0; stderr:\n${r.stderr}`);
    const lines = r.stdout.split(/\r?\n/).filter((l) => l.trim() !== '');
    assert.equal(lines.length, 2, `one reply for model.list and one error envelope for nope:\n${r.stdout}`);
    const reply = JSON.parse(lines[0]);
    assert.equal(reply.type, 'control');
    assert.equal(reply.op, 'model.list');
    assert.equal(reply.id, 'a');
    assert.equal(reply.ok, true);
    const envelope = JSON.parse(lines[1]);
    assert.equal(envelope.type, 'control', `envelope type:\n${r.stdout}`);
    assert.equal(envelope.id, 'b', `envelope must carry the request id:\n${r.stdout}`);
    assert.equal(envelope.ok, false, `envelope must report ok:false:\n${r.stdout}`);
    assert.equal(envelope.error, 'unknown op', `error text must be exactly "unknown op":\n${r.stdout}`);
    assert.ok(!('op' in envelope), `unknown-op envelope must omit the op field:\n${r.stdout}`);
    assert.ok(!(r.stdout + r.stderr).includes('"nope"') && !(r.stdout + r.stderr).includes('nope'),
      `caller-supplied op name must not appear on stdout or stderr:\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

// ============================================================================
// S5 review (items 2a, 2b, 5): the pipe must not echo a record type or a flag name, and a control op
// error on the stream-json channel is an envelope (ok:false, same op and id), exit 0.
// Probe words are assembled at runtime so no literal looks like a secret to the scanner.
// ============================================================================

/** Sandbox for one `node bin/cli.js pipe ...` run: isolated HOME, config dir and cwd, no credentials. */
function makePipeSandbox() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-pipe-s5-'));
  const home = path.join(root, 'home');
  const cwd = path.join(root, 'cwd');
  fs.mkdirSync(home);
  fs.mkdirSync(cwd);
  const env = {
    ...process.env,
    HOME: home,
    USERPROFILE: home,
    AESOP_CONFIG_DIR: path.join(root, 'config'),
    ANTHROPIC_API_KEY: '',
    AESOP_API_KEY: '',
  };
  return { root, cwd, env };
}

test('S5-2a unknown stream-json record type exits 2 and does not echo the type word to stderr', { timeout: 60000 }, async () => {
  const probe = ['qzv', 'mrtlhow', 'kpydsxa', 'nbgjf'].join('');
  const sb = makePipeSandbox();
  try {
    const input = JSON.stringify({ type: probe }) + '\n';
    const r = await runBinCli(['pipe', '--input-format', 'stream-json', '--output-format', 'stream-json'], input, { cwd: sb.cwd, env: sb.env });
    assert.equal(r.code, 2, `unknown record type must exit 2; stderr:\n${r.stderr}`);
    assert.ok(!(r.stdout + r.stderr).includes(probe), `record type word echoed in output:\n${r.stderr}`);
  } finally {
    fs.rmSync(sb.root, { recursive: true, force: true });
  }
});

test('S5-2b pipe with an unknown all-lowercase flag exits 2 and does not echo the flag name', { timeout: 60000 }, async () => {
  const probe = ['vmqkzthe', 'wrlbxonj', 'ufgyaspc', 'dihqtmzv'].join('');
  const sb = makePipeSandbox();
  try {
    const r = await runBinCli(['pipe', `--${probe}`], '', { cwd: sb.cwd, env: sb.env });
    assert.equal(r.code, 2, `unknown flag must exit 2; stderr:\n${r.stderr}`);
    assert.ok(!(r.stdout + r.stderr).includes(probe), `flag name echoed in output:\n${r.stderr}`);
  } finally {
    fs.rmSync(sb.root, { recursive: true, force: true });
  }
});

test('S5-5 control model.use with an unknown model answers ok:false envelope with the same op and id, exit 0', { timeout: 60000 }, async () => {
  const sb = makePipeSandbox();
  try {
    const input = JSON.stringify({ type: 'control', op: 'model.use', id: 'e1', model: 'nosuch/model' }) + '\n';
    const r = await runBinCli(['pipe', '--input-format', 'stream-json', '--output-format', 'stream-json'], input, { cwd: sb.cwd, env: sb.env });
    assert.equal(r.signal, null, 'must exit normally, not by signal');
    assert.equal(r.code, 0, `an op failure must not end the pipe: exit 0 expected; stderr:\n${r.stderr}`);
    const lines = r.stdout.split(/\r?\n/).filter((l) => l.trim() !== '');
    assert.equal(lines.length, 1, `exactly one envelope on stdout:\n${r.stdout}`);
    const envelope = JSON.parse(lines[0]);
    assert.equal(envelope.type, 'control', `envelope type:\n${r.stdout}`);
    assert.equal(envelope.op, 'model.use', `envelope must carry the failing op:\n${r.stdout}`);
    assert.equal(envelope.id, 'e1', `envelope must carry the request id:\n${r.stdout}`);
    assert.equal(envelope.ok, false, `envelope must report ok:false:\n${r.stdout}`);
    assert.equal(typeof envelope.error, 'string', `envelope must carry an error message:\n${r.stdout}`);
    assert.notEqual(envelope.error, '', 'error message must not be empty');
    // Known op with a bad argument: the error names the problem, never echoes the caller-supplied selector.
    assert.ok(!envelope.error.includes('nosuch/model') && !envelope.error.includes('nosuch'),
      `error text must not echo the model selector:\n${r.stdout}`);
  } finally {
    fs.rmSync(sb.root, { recursive: true, force: true });
  }
});

// ============================================================================
// S5 round 5 (R-DECIDE-1, R1, R2, R4): decided rules for the stdio contract. Each probe is assembled
// at runtime so no literal in this file looks key-shaped. Red cases are the decided behaviour.
// ============================================================================

test('R1a control frame of type error: caller message is not echoed on stderr or stdout (exit 2)', { timeout: 60000 }, async () => {
  const probe = ['zqw', 'plmrt', 'vbxho', 'kdyea'].join('');
  const errFrame = frame('control', 'application/json', JSON.stringify({ type: 'error', message: probe }));
  const input = Buffer.concat([Buffer.from(MAGIC), errFrame, Buffer.from(END)]);
  const r = await runCli(['--input-format', 'frames'], input);
  assert.equal(r.code, 2, `upstream error frame must exit 2; stderr:\n${r.stderr}`);
  assert.ok(!r.stderr.includes(probe), `caller control message echoed on stderr:\n${r.stderr}`);
  assert.ok(!r.stdout.toString('utf8').includes(probe), `caller control message echoed on stdout:\n${r.stdout.toString('utf8')}`);
});

test('R1b stream-json result record with a string bytes value: caller value is not echoed (exit 2)', { timeout: 60000 }, async () => {
  const probe = ['qzw', 'plmrt', 'vbxho', 'kdyea'].join('');
  const input = Buffer.from(JSON.stringify({ type: 'result', bytes: probe, sha256: '0'.repeat(64) }) + '\n', 'utf8');
  const r = await runCli(['--input-format', 'stream-json'], input);
  assert.equal(r.code, 2, `result record with a bad bytes value must exit 2; stderr:\n${r.stderr}`);
  assert.ok(!r.stderr.includes(probe), `caller bytes value echoed on stderr:\n${r.stderr}`);
  assert.ok(!r.stdout.toString('utf8').includes(probe), `caller bytes value echoed on stdout:\n${r.stdout.toString('utf8')}`);
});

const R2_CONTROL_LINE = JSON.stringify({ type: 'control', op: 'auth.status', id: 'r2' }) + '\n';

test('R2a control record on stream-json input with raw output is refused: exit 2, nothing on stdout', async () => {
  const r = await runCli(['--input-format', 'stream-json', '--output-format', 'raw'], Buffer.from(R2_CONTROL_LINE, 'utf8'));
  assert.equal(r.code, 2, `control record without stream-json output must exit 2; stderr:\n${r.stderr}`);
  assert.equal(r.stdout.length, 0, `nothing may be written to stdout:\n${r.stdout.toString('utf8')}`);
  assertOneLineDiagnostic(r.stderr, 'R2a: ');
});

test('R2b control record on stream-json input with frames output is refused: exit 2, no control reply', async () => {
  const r = await runCli(['--input-format', 'stream-json', '--output-format', 'frames'], Buffer.from(R2_CONTROL_LINE, 'utf8'));
  assert.equal(r.code, 2, `control record without stream-json output must exit 2; stderr:\n${r.stderr}`);
  const out = r.stdout.toString('utf8');
  assert.ok(!out.includes('"ok"') && !out.includes('"type":"control"'), `no control reply may reach stdout:\n${out}`);
});

test('R2c raw input with a control-shaped line is passed through as bytes, byte-exact, exit 0', async () => {
  const input = Buffer.from(R2_CONTROL_LINE, 'utf8');
  const r = await runCli([], input);
  assert.equal(r.code, 0, `raw input is never parsed as control; stderr:\n${r.stderr}`);
  assert.equal(Buffer.compare(r.stdout, input), 0, `raw passthrough must be byte-exact:\n${r.stdout.toString('utf8')}`);
});

test('R4 control record with no id field is malformed framing: exit 2, no envelope on stdout', { timeout: 60000 }, async () => {
  const sb = makePipeSandbox();
  try {
    const input = JSON.stringify({ type: 'control', op: 'auth.status' }) + '\n';
    const r = await runBinCli(['pipe', '--input-format', 'stream-json', '--output-format', 'stream-json'], input, { cwd: sb.cwd, env: sb.env });
    assert.equal(r.signal, null, 'must exit normally, not by signal');
    assert.equal(r.code, 2, `a control record without id is a FramingError: exit 2 expected; stderr:\n${r.stderr}`);
    assert.equal(r.stdout, '', `no envelope may be written without an id to correlate it:\n${r.stdout}`);
  } finally {
    fs.rmSync(sb.root, { recursive: true, force: true });
  }
});

// R4b (S5 round 5, R-CONTROL-1): a framing fault is the only class that exits 2 with no stdout record.
// A control record with no id on the stream-json channel: exit 2, exactly one `aesop-pipe:` stderr line,
// and zero bytes on stdout.
test('R4b control record with no id: exit 2, exactly one aesop-pipe stderr line, zero stdout bytes', { timeout: 60000 }, async () => {
  const sb = makePipeSandbox();
  try {
    const input = JSON.stringify({ type: 'control', op: 'auth.status' }) + '\n';
    const r = await runBinCli(['pipe', '--input-format', 'stream-json', '--output-format', 'stream-json'], input, { cwd: sb.cwd, env: sb.env });
    assert.equal(r.code, 2, `R4b: a control record without id is a FramingError: exit 2 expected; stderr:\n${r.stderr}`);
    assertOneLineDiagnostic(r.stderr, 'R4b: ');
    assert.equal(Buffer.byteLength(r.stdout, 'utf8'), 0, `R4b: stdout must be zero bytes for a framing fault:\n${r.stdout}`);
  } finally {
    fs.rmSync(sb.root, { recursive: true, force: true });
  }
});
