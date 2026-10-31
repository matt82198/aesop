/**
 * Core of the generic stdin/stdout pipeline. See stdio/CLAUDE.md for the contract.
 *
 * Data flows as units: `{kind: 'data', contentType, payload}` or `{kind: 'control', type, payload}`.
 * An input reader produces units from stdin one chunk at a time; an output writer consumes them.
 * Every write is awaited and honours 'drain', so backpressure reaches the producer and only one
 * input chunk (plus one frame) is ever held in memory.
 *
 * Raw input with raw or text output is a byte-exact passthrough with no parsing.
 *
 * Node stdlib only. Importing this module performs no I/O: runPipe reads nothing until called.
 *
 * @module stdio/pipe
 */

import { createHash } from 'node:crypto';
import {
  DEFAULT_MAX_FRAME,
  FrameDecoder,
  FramingError,
  KNOWN_CONTROL_TYPES,
  MAGIC,
  MAX_FRAME_LIMIT,
  encodeEnd,
  encodeFrame,
  isValidContentType,
} from './frames.mjs';

/** Raised for bad command-line usage. The CLI maps it to exit 2 with one stderr line. */
export class UsageError extends Error {
  /** @param {string} message */
  constructor(message) {
    super(message);
    this.name = 'UsageError';
  }
}

const INPUT_FORMATS = new Set(['raw', 'text', 'stream-json', 'frames']);
const OUTPUT_FORMATS = new Set(['raw', 'text', 'json', 'stream-json', 'frames']);
const VALUE_FLAGS = new Set(['--input-format', '--output-format', '--content-type', '--max-frame']);
const DEFAULT_CONTENT_TYPE = 'application/octet-stream';
const MAGIC_BYTES = Buffer.from(MAGIC, 'latin1');
const BASE64_RE = /^[A-Za-z0-9+/]*={0,2}$/;
const DOWNSTREAM_CLOSED = new Set([
  'EPIPE',
  'EOF',
  'ERR_STREAM_DESTROYED',
  'ERR_STREAM_CLOSED_EARLY',
  'ERR_STREAM_WRITE_AFTER_END',
]);

const USAGE = [
  'Usage: node stdio/cli.mjs [flags]',
  'Copies stdin to stdout. The default raw mode is byte-exact.',
  '',
  'Flags:',
  '  --input-format raw|text|stream-json|frames          (default raw)',
  '  --output-format raw|text|json|stream-json|frames    (default raw)',
  '  --content-type TAG    content type for raw payloads (default application/octet-stream)',
  '  --max-frame BYTES     upper bound on one frame payload (default 1048576)',
  '  --help                print this message and exit 0',
  '',
].join('\n');

/**
 * @param {unknown} value
 * @returns {boolean} true for a non-null, non-array object
 */
function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Views any chunk as a Buffer without copying.
 * @param {Buffer|Uint8Array|string} chunk
 * @returns {Buffer}
 */
function toBuffer(chunk) {
  if (Buffer.isBuffer(chunk)) return chunk;
  if (typeof chunk === 'string') return Buffer.from(chunk, 'utf8');
  if (chunk instanceof Uint8Array) return Buffer.from(chunk.buffer, chunk.byteOffset, chunk.byteLength);
  throw new TypeError('stdin produced a value that is not bytes');
}

/**
 * Splits a payload into pieces no larger than maxFrame.
 * @param {Buffer} payload
 * @param {number} maxFrame
 * @returns {Generator<Buffer>} non-empty pieces in order
 */
function* splitPayload(payload, maxFrame) {
  for (let off = 0; off < payload.length; off += maxFrame) {
    yield payload.subarray(off, Math.min(off + maxFrame, payload.length));
  }
}

/**
 * Parses command-line flags.
 * @param {string[]} argv arguments after the program name
 * @returns {{inputFormat: string, outputFormat: string, contentType: string, maxFrame: number, help: boolean}}
 * @throws {UsageError} for an unknown flag, a missing value, or a bad format, type or size
 */
export function parseArgs(argv) {
  const opts = {
    inputFormat: 'raw',
    outputFormat: 'raw',
    contentType: DEFAULT_CONTENT_TYPE,
    maxFrame: DEFAULT_MAX_FRAME,
    help: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = String(argv[i]);
    if (arg === '--help') {
      opts.help = true;
      continue;
    }
    const eq = arg.indexOf('=');
    const flag = eq === -1 ? arg : arg.slice(0, eq);
    if (!VALUE_FLAGS.has(flag)) {
      throw new UsageError(arg.startsWith('-') ? `unknown flag ${JSON.stringify(flag)}` : `unexpected argument ${JSON.stringify(arg)}`);
    }
    let value;
    if (eq !== -1) {
      value = arg.slice(eq + 1);
    } else {
      const next = argv[i + 1];
      if (next === undefined || String(next).startsWith('--')) throw new UsageError(`missing value for ${flag}`);
      value = String(next);
      i += 1;
    }
    applyValue(opts, flag, value);
  }
  return opts;
}

/**
 * Validates one flag value and stores it in opts.
 * @param {object} opts
 * @param {string} flag
 * @param {string} value
 * @throws {UsageError}
 */
function applyValue(opts, flag, value) {
  if (flag === '--input-format') {
    if (!INPUT_FORMATS.has(value)) throw new UsageError(`unknown input format ${JSON.stringify(value)}`);
    opts.inputFormat = value;
  } else if (flag === '--output-format') {
    if (!OUTPUT_FORMATS.has(value)) throw new UsageError(`unknown output format ${JSON.stringify(value)}`);
    opts.outputFormat = value;
  } else if (flag === '--content-type') {
    if (!isValidContentType(value)) throw new UsageError(`invalid content type ${JSON.stringify(value)}`);
    opts.contentType = value;
  } else {
    const size = Number(value);
    if (!/^[1-9][0-9]*$/.test(value) || size > MAX_FRAME_LIMIT) {
      throw new UsageError(`--max-frame must be a byte count from 1 to ${MAX_FRAME_LIMIT}`);
    }
    opts.maxFrame = size;
  }
}

/**
 * Wraps stdout so that every write waits for drain when the stream asks for it.
 * A failed or closed stdout rejects the pending write instead of hanging.
 * @param {NodeJS.WritableStream} stdout
 * @returns {{write: (buf: Buffer) => Promise<void>}}
 */
function createSink(stdout) {
  let broken = null;
  let wake = null;
  const notify = () => {
    const resume = wake;
    wake = null;
    if (resume) resume();
  };
  stdout.on('error', (err) => {
    if (!broken) broken = err;
    notify();
  });
  stdout.on('close', () => {
    if (!broken) {
      const err = new Error('stdout closed before the pipe finished');
      err.code = 'ERR_STREAM_CLOSED_EARLY';
      broken = err;
    }
    notify();
  });
  stdout.on('drain', notify);
  return {
    async write(buf) {
      if (broken) throw broken;
      if (buf.length === 0) return;
      if (!stdout.write(buf)) {
        await new Promise((resolve) => {
          wake = resolve;
        });
      }
      if (broken) throw broken;
    },
  };
}

/**
 * Turns a control frame payload into a unit, skips it, or fails closed.
 * @param {Buffer} payload
 * @returns {{kind: 'control', type: string, payload: Buffer} | null} null when skipped
 * @throws {FramingError} for bad JSON, an upstream error, or an unknown critical type
 */
function controlUnit(payload) {
  let message;
  try {
    message = JSON.parse(payload.toString('utf8'));
  } catch {
    throw new FramingError('control frame payload is not valid JSON');
  }
  if (!isPlainObject(message) || typeof message.type !== 'string') {
    throw new FramingError('control frame must be a JSON object with a string type');
  }
  if (KNOWN_CONTROL_TYPES.has(message.type)) {
    if (message.type === 'error') {
      const detail = typeof message.message === 'string' ? message.message : 'no message';
      throw new FramingError(`upstream reported an error: ${detail}`);
    }
    return { kind: 'control', type: message.type, payload };
  }
  if (message.critical === false) return null;
  throw new FramingError(`unknown critical control message type ${JSON.stringify(message.type.slice(0, 64))}`);
}

/**
 * Reads raw bytes. Each stdin chunk becomes one data unit, passed through unchanged.
 * @param {AsyncIterable<Buffer>} stdin
 * @param {string} contentType
 * @returns {AsyncGenerator<object>}
 */
async function* rawUnits(stdin, contentType) {
  for await (const chunk of stdin) {
    const payload = toBuffer(chunk);
    if (payload.length > 0) yield { kind: 'data', contentType, payload };
  }
}

/**
 * Reads the frame grammar and yields data and known control units.
 * @param {AsyncIterable<Buffer>} stdin
 * @param {number} maxFrame
 * @returns {AsyncGenerator<object>}
 */
async function* frameUnits(stdin, maxFrame) {
  const decoder = new FrameDecoder({ maxFrame });
  for await (const chunk of stdin) {
    for (const item of decoder.push(toBuffer(chunk))) {
      if (item.kind !== 'frame') continue;
      if (item.channel === 'control') {
        const unit = controlUnit(item.payload);
        if (unit) yield unit;
      } else {
        yield { kind: 'data', contentType: item.contentType, payload: item.payload };
      }
    }
  }
  decoder.finish();
}

/**
 * Decodes one stream-json record line.
 * Updates the running hash and byte count, and verifies the result record when it arrives.
 * @param {Buffer} line one line without its LF
 * @param {{hash: import('node:crypto').Hash, bytes: number, done: boolean}} state
 * @param {{maxFrame: number, contentType: string}} opts
 * @returns {object|null} a data unit, or null for blank lines and the result record
 * @throws {FramingError} on malformed records
 */
function recordUnit(line, state, opts) {
  const text = line.toString('utf8').trim();
  if (text === '') return null;
  let record;
  try {
    record = JSON.parse(text);
  } catch {
    throw new FramingError('stream-json line is not valid JSON');
  }
  if (!isPlainObject(record)) throw new FramingError('stream-json record must be a JSON object');

  if (record.type === 'data') {
    if (state.done) throw new FramingError('stream-json data record after the result record');
    if (typeof record.data !== 'string') throw new FramingError('stream-json data record has no string data field');
    const encoding = record.encoding === undefined ? 'base64' : record.encoding;
    let payload;
    if (encoding === 'base64') {
      if (record.data.length % 4 !== 0 || !BASE64_RE.test(record.data)) {
        throw new FramingError('stream-json data record is not valid base64');
      }
      payload = Buffer.from(record.data, 'base64');
    } else if (encoding === 'utf8') {
      payload = Buffer.from(record.data, 'utf8');
    } else {
      throw new FramingError(`unsupported stream-json encoding ${JSON.stringify(String(encoding).slice(0, 32))}`);
    }
    if (payload.length > opts.maxFrame) {
      throw new FramingError(`stream-json data record of ${payload.length} bytes exceeds --max-frame ${opts.maxFrame}`);
    }
    const contentType = record.content_type === undefined ? opts.contentType : record.content_type;
    if (!isValidContentType(contentType)) throw new FramingError('stream-json data record has an invalid content_type');
    state.hash.update(payload);
    state.bytes += payload.length;
    return { kind: 'data', contentType, payload };
  }

  if (record.type === 'result') {
    if (state.done) throw new FramingError('more than one stream-json result record');
    if (record.bytes !== state.bytes) {
      throw new FramingError(`stream-json result reports ${JSON.stringify(record.bytes)} bytes, data carried ${state.bytes}`);
    }
    if (record.sha256 !== state.hash.digest('hex')) {
      throw new FramingError('stream-json result sha256 does not match the data records');
    }
    state.done = true;
    return null;
  }

  throw new FramingError(`unknown stream-json record type ${JSON.stringify(String(record.type).slice(0, 32))}`);
}

/**
 * Reads JSON Lines records. Each line is parsed on its own; a partial line is held until its LF arrives.
 * @param {AsyncIterable<Buffer>} stdin
 * @param {{maxFrame: number, contentType: string}} opts
 * @returns {AsyncGenerator<object>}
 */
async function* streamJsonUnits(stdin, opts) {
  const lineLimit = 2 * opts.maxFrame + 65536;
  const state = { hash: createHash('sha256'), bytes: 0, done: false };
  let carry = Buffer.alloc(0);
  for await (const chunk of stdin) {
    const buf = toBuffer(chunk);
    let start = 0;
    let nl = buf.indexOf(0x0a, start);
    while (nl !== -1) {
      const piece = buf.subarray(start, nl);
      const line = carry.length === 0 ? piece : Buffer.concat([carry, piece]);
      carry = Buffer.alloc(0);
      if (line.length > lineLimit) throw new FramingError('stream-json line exceeds the record size limit');
      start = nl + 1;
      nl = buf.indexOf(0x0a, start);
      const unit = recordUnit(line, state, opts);
      if (unit) yield unit;
    }
    const rest = buf.subarray(start);
    if (carry.length + rest.length > lineLimit) throw new FramingError('stream-json line exceeds the record size limit');
    if (rest.length > 0) carry = carry.length === 0 ? rest : Buffer.concat([carry, rest]);
  }
  if (carry.length > 0) {
    const unit = recordUnit(carry, state, opts);
    if (unit) yield unit;
  }
}

/**
 * Chooses the reader for the requested input format.
 * @param {NodeJS.ReadableStream} stdin
 * @param {object} opts parsed options
 * @returns {AsyncGenerator<object>}
 */
function readUnits(stdin, opts) {
  if (opts.inputFormat === 'frames') return frameUnits(stdin, opts.maxFrame);
  if (opts.inputFormat === 'stream-json') return streamJsonUnits(stdin, opts);
  return rawUnits(stdin, opts.contentType);
}

/**
 * Builds the writer for the requested output format. Writers are stateful and single-use.
 * @param {object} opts parsed options
 * @param {{write: (buf: Buffer) => Promise<void>}} sink
 * @returns {{unit: (u: object) => Promise<void>, finish: () => Promise<void>}}
 */
function createOutput(opts, sink) {
  const { outputFormat, maxFrame, contentType } = opts;
  const needsDigest = outputFormat === 'json' || outputFormat === 'stream-json';
  const hash = createHash('sha256');
  let bytes = 0;
  let summaryType = null;
  let started = false;

  // The magic line goes out lazily, so a stream that fails on its first frame leaves stdout empty.
  const begin = async () => {
    if (started) return;
    started = true;
    await sink.write(MAGIC_BYTES);
  };

  return {
    async unit(u) {
      if (u.kind === 'control') {
        if (outputFormat === 'frames') {
          await begin();
          await sink.write(encodeFrame({ channel: 'control', contentType: 'application/json', payload: u.payload }));
        }
        return;
      }
      if (needsDigest) {
        hash.update(u.payload);
        bytes += u.payload.length;
      }
      if (summaryType === null) summaryType = u.contentType;
      switch (outputFormat) {
        case 'raw':
        case 'text':
          await sink.write(u.payload);
          return;
        case 'json':
          return;
        case 'stream-json':
          for (const piece of splitPayload(u.payload, maxFrame)) {
            const record = { type: 'data', content_type: u.contentType, encoding: 'base64', data: piece.toString('base64') };
            await sink.write(Buffer.from(`${JSON.stringify(record)}\n`, 'utf8'));
          }
          return;
        case 'frames':
          for (const piece of splitPayload(u.payload, maxFrame)) {
            await begin();
            await sink.write(encodeFrame({ channel: 'data', contentType: u.contentType, payload: piece }));
          }
          return;
        default:
          throw new Error(`unhandled output format ${outputFormat}`);
      }
    },

    async finish() {
      if (outputFormat === 'frames') {
        await begin();
        await sink.write(encodeEnd(0));
      } else if (outputFormat === 'stream-json') {
        const record = { type: 'result', exit: 0, bytes, sha256: hash.digest('hex') };
        await sink.write(Buffer.from(`${JSON.stringify(record)}\n`, 'utf8'));
      } else if (outputFormat === 'json') {
        const summary = {
          content_type: summaryType === null ? contentType : summaryType,
          bytes,
          sha256: hash.digest('hex'),
        };
        await sink.write(Buffer.from(`${JSON.stringify(summary)}\n`, 'utf8'));
      }
    },
  };
}

/**
 * Moves every unit from the reader to the writer.
 * @param {NodeJS.ReadableStream} stdin
 * @param {{write: (buf: Buffer) => Promise<void>}} sink
 * @param {object} opts parsed options
 */
async function transfer(stdin, sink, opts) {
  const out = createOutput(opts, sink);
  for await (const unit of readUnits(stdin, opts)) {
    await out.unit(unit);
  }
  await out.finish();
}

/**
 * Writes one diagnostic line to stderr and returns the exit code.
 * @param {NodeJS.WritableStream} stderr
 * @param {number} code
 * @param {string} message
 * @returns {number} code
 */
function fail(stderr, code, message) {
  const line = String(message).replace(/[\r\n]+/g, ' ');
  try {
    stderr.write(`aesop-pipe: ${line}\n`);
  } catch {
    // Diagnostics are best effort; the exit code still reports the failure.
  }
  return code;
}

/**
 * Runs the pipeline.
 * Exit codes: 0 success (including a downstream that closed early), 2 usage or malformed input, 1 internal error.
 * @param {{stdin?: NodeJS.ReadableStream, stdout?: NodeJS.WritableStream, stderr?: NodeJS.WritableStream, argv?: string[]}} [streams]
 * @returns {Promise<number>} exit code
 */
export async function runPipe({
  stdin = process.stdin,
  stdout = process.stdout,
  stderr = process.stderr,
  argv = [],
} = {}) {
  let opts;
  try {
    opts = parseArgs(argv);
  } catch (err) {
    if (err instanceof UsageError) return fail(stderr, 2, err.message);
    throw err;
  }
  const sink = createSink(stdout);
  try {
    if (opts.help) {
      await sink.write(Buffer.from(USAGE, 'utf8'));
      return 0;
    }
    await transfer(stdin, sink, opts);
    return 0;
  } catch (err) {
    if (err && DOWNSTREAM_CLOSED.has(err.code)) return 0;
    if (err instanceof FramingError) return fail(stderr, 2, err.message);
    return fail(stderr, 1, `internal error: ${err && err.message ? err.message : String(err)}`);
  }
}
