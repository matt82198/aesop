/**
 * Frame grammar for the AESOP-PIPE/1 stream (see stdio/CLAUDE.md).
 *
 *   AESOP-PIPE/1\n
 *   F <channel> <content-type> <length>\n<payload>\n
 *   E <exit>\n
 *
 * Headers are LF-terminated ASCII. Payloads are binary-safe and are followed by exactly one LF.
 * The decoder is streaming: it holds at most one partial header plus one partial frame payload
 * (never more than --max-frame bytes of payload) and reports every frame as soon as it is complete.
 *
 * Node stdlib only. Importing this module performs no I/O.
 *
 * @module stdio/frames
 */

/** Magic first line of every frames-format stream. */
export const MAGIC = 'AESOP-PIPE/1\n';

/** Default upper bound on one frame payload, in bytes. */
export const DEFAULT_MAX_FRAME = 1048576;

/** Largest accepted --max-frame value, in bytes. */
export const MAX_FRAME_LIMIT = 268435456;

/** Control message types the pipeline recognises. Unknown types need "critical": false to be skipped. */
export const KNOWN_CONTROL_TYPES = new Set(['hello', 'meta', 'event', 'receipt', 'seal', 'error', 'done']);

const MAGIC_BYTES = Buffer.from(MAGIC, 'latin1');
const LF_BYTE = 0x0a;
const LF_BUFFER = Buffer.from('\n', 'latin1');
const EMPTY = Buffer.alloc(0);
const MAX_HEADER_BYTES = 512;
const CHANNEL_RE = /^[a-z0-9._-]{1,32}$/;
const CONTENT_TYPE_RE = /^[!-~]+$/;
const FRAME_HEADER_RE = /^F ([a-z0-9._-]{1,32}) ([!-~]+) ([0-9]+)$/;
const END_HEADER_RE = /^E ([0-9]+)$/;

/** Raised for any input that violates the frame grammar. The caller maps it to exit 2. */
export class FramingError extends Error {
  /** @param {string} message one-line reason, without the aesop-pipe prefix */
  constructor(message) {
    super(message);
    this.name = 'FramingError';
  }
}

/**
 * @param {unknown} channel
 * @returns {boolean} true when the value is a legal channel name
 */
export function isValidChannel(channel) {
  return typeof channel === 'string' && CHANNEL_RE.test(channel);
}

/**
 * @param {unknown} contentType
 * @returns {boolean} true when the value is a non-empty token without spaces or control bytes
 */
export function isValidContentType(contentType) {
  return typeof contentType === 'string' && CONTENT_TYPE_RE.test(contentType);
}

/**
 * Converts a payload value to a Buffer without copying Buffers or typed-array views.
 * @param {Buffer|Uint8Array|string} payload
 * @returns {Buffer}
 */
function toPayloadBuffer(payload) {
  if (Buffer.isBuffer(payload)) return payload;
  if (typeof payload === 'string') return Buffer.from(payload, 'utf8');
  if (payload instanceof Uint8Array) {
    return Buffer.from(payload.buffer, payload.byteOffset, payload.byteLength);
  }
  throw new TypeError('frame payload must be a Buffer, Uint8Array or string');
}

/**
 * Encodes one data or control frame.
 * @param {{channel: string, contentType: string, payload: Buffer|Uint8Array|string}} frame
 * @returns {Buffer} `F <channel> <content-type> <length>\n<payload>\n`
 * @throws {TypeError} when the channel, content type or payload is invalid
 */
export function encodeFrame({ channel, contentType, payload }) {
  if (!isValidChannel(channel)) {
    throw new TypeError(`invalid frame channel ${JSON.stringify(channel)}`);
  }
  if (!isValidContentType(contentType)) {
    throw new TypeError(`invalid frame content type ${JSON.stringify(contentType)}`);
  }
  const body = toPayloadBuffer(payload);
  const header = Buffer.from(`F ${channel} ${contentType} ${body.length}\n`, 'latin1');
  return Buffer.concat([header, body, LF_BUFFER]);
}

/**
 * Encodes the end frame.
 * @param {number} exit non-negative integer exit code
 * @returns {Buffer} `E <exit>\n`
 * @throws {TypeError} when exit is not a non-negative safe integer
 */
export function encodeEnd(exit) {
  if (!Number.isSafeInteger(exit) || exit < 0) {
    throw new TypeError(`end frame exit must be a non-negative integer, got ${JSON.stringify(exit)}`);
  }
  return Buffer.from(`E ${exit}\n`, 'latin1');
}

/**
 * Short, single-line rendering of a bad header for error messages.
 * @param {string} line
 * @returns {string}
 */
function describeHeader(line) {
  const shown = line.length > 80 ? `${line.slice(0, 80)}...` : line;
  return JSON.stringify(shown);
}

/**
 * Parses one LF-stripped header line.
 * @param {string} line
 * @param {number} maxFrame
 * @returns {{kind: 'end', exit: number} | {kind: 'frame', channel: string, contentType: string, length: number}}
 * @throws {FramingError} on any grammar violation or a length above maxFrame
 */
function parseHeader(line, maxFrame) {
  const end = END_HEADER_RE.exec(line);
  if (end) return { kind: 'end', exit: Number(end[1]) };
  const m = FRAME_HEADER_RE.exec(line);
  if (!m) throw new FramingError(`malformed frame header: ${describeHeader(line)}`);
  const length = Number(m[3]);
  if (!Number.isSafeInteger(length)) {
    throw new FramingError(`frame length is out of range: ${describeHeader(line)}`);
  }
  if (length > maxFrame) {
    throw new FramingError(`frame payload of ${length} bytes exceeds --max-frame ${maxFrame}`);
  }
  return { kind: 'frame', channel: m[1], contentType: m[2], length };
}

/**
 * Streaming decoder. Feed chunks with push(); each call returns the items completed by that chunk.
 *
 * Items are `{kind: 'frame', channel, contentType, payload}` for each data or control frame and
 * `{kind: 'end', exit}` for the end frame. After the end frame any further byte is an error.
 */
export class FrameDecoder {
  /**
   * @param {{maxFrame?: number}} [options]
   */
  constructor({ maxFrame = DEFAULT_MAX_FRAME } = {}) {
    if (!Number.isSafeInteger(maxFrame) || maxFrame < 1) {
      throw new TypeError('maxFrame must be a positive integer');
    }
    this.maxFrame = maxFrame;
    this.state = 'magic';
    this.buf = EMPTY;
    this.pending = null;
  }

  /**
   * @param {Buffer|Uint8Array} chunk next bytes of the stream
   * @returns {Array<object>} frames and the end item completed by this chunk, in order
   * @throws {FramingError} on malformed input
   */
  push(chunk) {
    if (!(chunk instanceof Uint8Array)) throw new TypeError('push() expects bytes');
    if (this.state === 'closed') {
      if (chunk.length > 0) throw new FramingError('bytes after the end frame');
      return [];
    }
    const input = this.buf.length === 0 ? Buffer.from(chunk.buffer, chunk.byteOffset, chunk.byteLength) : Buffer.concat([this.buf, chunk]);
    const out = [];
    let pos = 0;
    for (;;) {
      if (this.state === 'magic') {
        const avail = input.length - pos;
        const cmp = Math.min(avail, MAGIC_BYTES.length);
        if (!input.subarray(pos, pos + cmp).equals(MAGIC_BYTES.subarray(0, cmp))) {
          throw new FramingError('missing AESOP-PIPE/1 magic line');
        }
        if (avail < MAGIC_BYTES.length) break;
        pos += MAGIC_BYTES.length;
        this.state = 'header';
      } else if (this.state === 'header') {
        const nl = input.indexOf(LF_BYTE, pos);
        if (nl === -1) {
          if (input.length - pos > MAX_HEADER_BYTES) {
            throw new FramingError(`frame header longer than ${MAX_HEADER_BYTES} bytes without a newline`);
          }
          break;
        }
        if (nl - pos > MAX_HEADER_BYTES) {
          throw new FramingError(`frame header longer than ${MAX_HEADER_BYTES} bytes`);
        }
        const header = input.toString('latin1', pos, nl);
        pos = nl + 1;
        const parsed = parseHeader(header, this.maxFrame);
        if (parsed.kind === 'end') {
          if (input.length - pos > 0) throw new FramingError('bytes after the end frame');
          out.push({ kind: 'end', exit: parsed.exit });
          this.state = 'closed';
          this.buf = EMPTY;
          return out;
        }
        this.pending = parsed;
        this.state = 'payload';
      } else if (this.state === 'payload') {
        const need = this.pending.length + 1;
        if (input.length - pos < need) break;
        if (input[pos + need - 1] !== LF_BYTE) {
          throw new FramingError('frame payload is not followed by a newline');
        }
        const { channel, contentType } = this.pending;
        out.push({ kind: 'frame', channel, contentType, payload: input.subarray(pos, pos + need - 1) });
        pos += need;
        this.pending = null;
        this.state = 'header';
      } else {
        break;
      }
    }
    this.buf = input.subarray(pos);
    return out;
  }

  /**
   * Declares end of input. Throws unless the stream ended exactly after its end frame.
   * @throws {FramingError} for empty input, truncation, or a missing end frame
   */
  finish() {
    if (this.state === 'closed') return;
    if (this.state === 'magic') {
      if (this.buf.length === 0) throw new FramingError('empty input: no AESOP-PIPE/1 magic line');
      throw new FramingError('truncated AESOP-PIPE/1 magic line');
    }
    if (this.state === 'payload') {
      throw new FramingError(`truncated frame payload: expected ${this.pending.length} bytes and a newline`);
    }
    if (this.buf.length > 0) throw new FramingError('truncated frame header: input ended before a newline');
    throw new FramingError('missing end frame: input ended between frames');
  }
}
