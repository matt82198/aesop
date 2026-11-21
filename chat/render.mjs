/**
 * Pure frame renderer for the interactive chat TUI.
 *
 * renderFrame(state, opts) turns a state snapshot into a string of lines joined by '\n'.
 * It has no I/O and no clock reads: elapsed time and the spinner tick come from state.status.
 * ANSI escapes are hand-written constants and only appear when color is true.
 *
 * Node stdlib only. The package.json version is read relative to this module.
 *
 * @module chat/render
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const PACKAGE_JSON = join(dirname(fileURLToPath(import.meta.url)), '..', 'package.json');
const VERSION = JSON.parse(readFileSync(PACKAGE_JSON, 'utf8')).version;

const SGR = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  reverse: '\x1b[7m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  cyan: '\x1b[36m',
};

const ASCII_SPINNER = ['|', '/', '-', '\\'];
const USER_PREFIX = 'you > ';
const ASSISTANT_PREFIX = 'aesop > ';
const SYSTEM_PREFIX = '  * ';
export const INPUT_PREFIX = '> ';

// Terminal columns one code point occupies: CJK, fullwidth and emoji blocks take two, combining marks zero.
function codePointWidth(cp) {
  if (cp >= 0x300 && cp <= 0x36f) return 0;
  const wide =
    (cp >= 0x1100 && cp <= 0x115f) ||
    (cp >= 0x2e80 && cp <= 0xa4cf) ||
    (cp >= 0xac00 && cp <= 0xd7a3) ||
    (cp >= 0xf900 && cp <= 0xfaff) ||
    (cp >= 0xfe30 && cp <= 0xfe4f) ||
    (cp >= 0xff00 && cp <= 0xff60) ||
    (cp >= 0xffe0 && cp <= 0xffe6) ||
    (cp >= 0x1f300 && cp <= 0x1f64f) ||
    (cp >= 0x1f900 && cp <= 0x1f9ff) ||
    (cp >= 0x20000 && cp <= 0x3fffd);
  return wide ? 2 : 1;
}

/**
 * Visible width of a string in terminal columns (wide characters count two).
 * @param {string} s
 * @returns {number}
 */
export function displayWidth(s) {
  let w = 0;
  for (const ch of String(s)) w += codePointWidth(ch.codePointAt(0));
  return w;
}

/**
 * Wraps `text` to `width` display columns. Existing newlines are kept; words longer than
 * the width are hard-broken on character boundaries. Always returns at least one line.
 * @param {string} text
 * @param {number} width visible columns available (>= 1)
 * @returns {string[]}
 */
export function wrapText(text, width) {
  const out = [];
  for (const para of String(text).split('\n')) {
    let cur = '';
    let curW = 0;
    for (let word of para.split(' ')) {
      let wordW = displayWidth(word);
      while (wordW > width) {
        if (cur !== '') {
          out.push(cur);
          cur = '';
          curW = 0;
        }
        const chars = [...word];
        let piece = '';
        let pieceW = 0;
        let i = 0;
        for (; i < chars.length; i++) {
          const cw = codePointWidth(chars[i].codePointAt(0));
          if (pieceW + cw > width) break;
          piece += chars[i];
          pieceW += cw;
        }
        if (i === 0) {
          // A single character wider than the column: emit it whole rather than loop forever.
          piece = chars[0];
          i = 1;
        }
        out.push(piece);
        word = chars.slice(i).join('');
        wordW = displayWidth(word);
      }
      if (cur === '') {
        cur = word;
        curW = wordW;
      } else if (curW + 1 + wordW <= width) {
        cur += ` ${word}`;
        curW += 1 + wordW;
      } else {
        out.push(cur);
        cur = word;
        curW = wordW;
      }
    }
    out.push(cur);
  }
  return out;
}

/**
 * Builds wrapped lines for one message. The prefix is plain text for width accounting;
 * `paint` styles each finished line (identity when color is off).
 * @returns {string[]}
 */
function block(firstPrefix, restPrefix, text, columns, paint) {
  // The prefix gives way before the text does: it is clipped so at least one column stays for body.
  const prefixLen = Math.min(Math.max(firstPrefix.length, restPrefix.length), Math.max(0, columns - 1));
  const width = columns - prefixLen;
  return wrapText(text, width).map((body, i) => {
    const prefix = i === 0 ? firstPrefix : restPrefix;
    return paint(`${prefix.slice(0, prefixLen)}${body}`);
  });
}

/**
 * @param {string} style one of the SGR keys, or '' for no style
 * @param {boolean} color
 * @returns {(s: string) => string}
 */
function painter(style, color) {
  if (!color || style === '') return (s) => s;
  return (s) => `${SGR[style]}${s}${SGR.reset}`;
}

function spinnerGlyph(tick) {
  const n = ASCII_SPINNER.length;
  return ASCII_SPINNER[((Math.trunc(tick) % n) + n) % n];
}

function formatElapsed(ms) {
  return `${(Math.max(0, ms) / 1000).toFixed(1)}s`;
}

/**
 * Decides whether ANSI color is on. NO_COLOR (non-empty) and --no-color both force it off and win
 * over FORCE_COLOR. FORCE_COLOR (non-empty, not "0") forces it on even without a TTY.
 * @param {{env?: Record<string, string|undefined>, argv?: string[], isTTY?: boolean}} [opts]
 * @returns {boolean}
 */
export function resolveColor({ env = {}, argv = [], isTTY = false } = {}) {
  if (env.NO_COLOR !== undefined && env.NO_COLOR !== '') return false;
  if (argv.includes('--no-color')) return false;
  if (env.FORCE_COLOR !== undefined && env.FORCE_COLOR !== '' && env.FORCE_COLOR !== '0') return true;
  return isTTY === true;
}

/**
 * Decides whether box-drawing characters are safe. An explicit boolean wins; TERM=dumb and an
 * unknown Windows console (no TERM, no Windows Terminal session) fall back to ASCII. Otherwise the
 * first non-empty locale variable among LC_ALL, LC_CTYPE, LANG decides: UTF-8 (any case, "utf8"
 * or "UTF-8") is unicode, and any other value such as "C" or "POSIX" is not.
 * @param {{env?: Record<string, string|undefined>, unicode?: boolean}} [opts]
 * @returns {boolean}
 */
export function resolveUnicode({ env = {}, unicode } = {}) {
  if (typeof unicode === 'boolean') return unicode;
  if (env.TERM === 'dumb') return false;
  if (process.platform === 'win32' && !env.TERM && !env.WT_SESSION) return false;
  const locale = [env.LC_ALL, env.LC_CTYPE, env.LANG].find((v) => typeof v === 'string' && v !== '');
  if (locale !== undefined) return /utf-?8/i.test(locale);
  return true;
}

function transcriptLines(transcript, columns, color, unicode) {
  const lines = [];
  for (const msg of transcript) {
    if (msg.role === 'user') {
      lines.push(...block(USER_PREFIX, ' '.repeat(USER_PREFIX.length), msg.text, columns, painter('bold', color)));
    } else if (msg.role === 'assistant') {
      let text = msg.text ?? '';
      if (msg.streaming) text = `${text}${text ? ' ' : ''}${msg.spinnerGlyph ?? ''}`;
      lines.push(...block(ASSISTANT_PREFIX, ' '.repeat(ASSISTANT_PREFIX.length), text, columns, painter('green', color)));
    } else if (msg.role === 'tool') {
      const border = `    ${unicode ? '│' : '|'} `;
      lines.push(...block(border, border, msg.text, columns, painter('yellow', color)));
    } else {
      // Errors stand out in red so a failed turn is never mistaken for normal output.
      const style = /^(stream error:|error:)/.test(msg.text) ? 'red' : 'dim';
      lines.push(...block(SYSTEM_PREFIX, ' '.repeat(SYSTEM_PREFIX.length), msg.text, columns, painter(style, color)));
    }
  }
  return lines;
}

/**
 * Renders one full frame. Layout: banner, transcript, optional hint, divider, status bar,
 * input. When the frame is taller than `rows`, the oldest transcript lines are dropped first.
 * The streaming spinner sits on the streaming assistant line only, so a tick change never
 * touches any other line.
 *
 * @param {object} state {transcript, inputBuffer?, cursor?, status, mode?, hint?}
 * @param {{columns?: number, rows?: number, color?: boolean, unicode?: boolean}} [opts]
 * @returns {string}
 */
export function renderFrame(state, opts = {}) {
  const columns = Math.max(1, Math.trunc(opts.columns ?? 80));
  const rows = Math.max(1, Math.trunc(opts.rows ?? 24));
  const color = opts.color === true;
  const unicode = opts.unicode === true;
  const status = state.status ?? {};
  const divider = (unicode ? '─' : '-').repeat(columns);

  const banner = block('', '', `aesop ${VERSION} - chat (type /help for commands)`, columns, painter('bold', color));

  const spinner = spinnerGlyph(status.spinnerTick ?? 0);
  const transcript = (state.transcript ?? []).map((msg) =>
    msg.role === 'assistant' && msg.streaming ? { ...msg, spinnerGlyph: spinner } : msg,
  );
  const body = transcriptLines(transcript, columns, color, unicode);

  const footer = [];
  if (state.hint) footer.push(...block('  ', '  ', state.hint, columns, painter('dim', color)));
  footer.push(painter('dim', color)(divider));
  const statusText = [
    `${status.model ?? ''} via ${status.provider ?? ''}`,
    `cwd ${status.cwd ?? ''}`,
    `tokens ${status.tokens ?? 0}`,
    formatElapsed(status.elapsedMs ?? 0),
  ].join(' | ');
  footer.push(...block('', '', statusText, columns, painter('reverse', color)));

  const buffer = state.inputBuffer ?? '';
  const cursor = Math.min(Math.max(0, state.cursor ?? buffer.length), buffer.length);
  const marked = `${buffer.slice(0, cursor)}_${buffer.slice(cursor)}`;
  marked.split('\n').forEach((seg, idx) => {
    footer.push(...block(idx === 0 ? INPUT_PREFIX : '  ', '  ', seg, columns, (s) => s));
  });

  const header = [...banner];
  const room = rows - header.length - footer.length;
  // Oldest transcript lines go first. With no room left for the transcript, the banner goes too,
  // so the frame never exceeds rows and the input line (the last footer line) always stays.
  if (room > 0) {
    if (body.length <= room) return [...header, ...body, ...footer].join('\n');
    // One row of the room goes to the marker that says how much transcript is hidden above.
    const kept = room - 1;
    const hidden = body.length - kept;
    const marker = painter('dim', color)(`(${hidden} earlier lines hidden)`.slice(0, columns));
    return [...header, marker, ...body.slice(body.length - kept), ...footer].join('\n');
  }
  return [...header, ...footer].slice(-rows).join('\n');
}
