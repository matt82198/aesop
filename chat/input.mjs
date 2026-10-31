/**
 * Pure line-editor reducer and slash-command parser for the chat TUI.
 *
 * applyKey(state, key) takes a readline-shaped key {name, ctrl, meta, sequence} and returns
 * {state, effect} without touching the terminal. Effects:
 *   null | {type:'submit', line} | {type:'quit'} | {type:'hint', text}
 *
 * Node stdlib only; no I/O.
 *
 * @module chat/input
 */

export const SLASH_COMMANDS = ['help', 'model', 'auth', 'clear', 'exit', 'quit'];

const HISTORY_LIMIT = 500;

/**
 * @returns {{inputBuffer: string, cursor: number, history: string[], historyIndex: number, draft: string, ctrlCPending: boolean}}
 */
export function createInputState() {
  return {
    inputBuffer: '',
    cursor: 0,
    history: [],
    historyIndex: 0,
    draft: '',
    ctrlCPending: false,
  };
}

function isPrintable(seq) {
  return !/[\u0000-\u001f\u007f]/.test(seq);
}

function withBuffer(s, buffer, cursor) {
  return { ...s, inputBuffer: buffer, cursor };
}

// A trailing backslash continues the line; anything else submits the whole buffer.
function onEnter(s) {
  const buf = s.inputBuffer;
  if (buf.endsWith('\\')) {
    const next = `${buf.slice(0, -1)}\n`;
    return { state: withBuffer(s, next, next.length), effect: null };
  }
  if (buf.trim() === '') {
    return { state: withBuffer(s, '', 0), effect: null };
  }
  const history = [...s.history, buf].slice(-HISTORY_LIMIT);
  const state = { ...withBuffer(s, '', 0), history, historyIndex: history.length, draft: '' };
  return { state, effect: { type: 'submit', line: buf } };
}

function insertText(s, text) {
  const buf = s.inputBuffer.slice(0, s.cursor) + text + s.inputBuffer.slice(s.cursor);
  return withBuffer(s, buf, s.cursor + text.length);
}

function historyUp(s) {
  if (s.history.length === 0 || s.historyIndex === 0) return s;
  const atNewest = s.historyIndex === s.history.length;
  const index = s.historyIndex - 1;
  const buffer = s.history[index];
  return {
    ...withBuffer(s, buffer, buffer.length),
    historyIndex: index,
    draft: atNewest ? s.inputBuffer : s.draft,
  };
}

function historyDown(s) {
  if (s.historyIndex >= s.history.length) return s;
  const index = s.historyIndex + 1;
  if (index === s.history.length) {
    return { ...withBuffer(s, s.draft, s.draft.length), historyIndex: index };
  }
  const buffer = s.history[index];
  return { ...withBuffer(s, buffer, buffer.length), historyIndex: index };
}

/**
 * Applies one key press to the input state.
 * @param {ReturnType<typeof createInputState>} state
 * @param {{name?: string, ctrl?: boolean, meta?: boolean, sequence?: string}} key
 * @returns {{state: object, effect: null | {type: string, line?: string, text?: string}}}
 */
export function applyKey(state, key = {}) {
  const name = key.name;

  if (key.ctrl && name === 'c') {
    if (state.ctrlCPending) {
      return { state: { ...state, ctrlCPending: false }, effect: { type: 'quit' } };
    }
    return {
      state: { ...state, ctrlCPending: true },
      effect: { type: 'hint', text: 'press ctrl-c again to quit' },
    };
  }

  const s = { ...state, ctrlCPending: false };
  const { cursor, inputBuffer: buf } = s;

  if (name === 'return' || name === 'enter') return onEnter(s);
  if (name === 'backspace') {
    if (cursor === 0) return { state: s, effect: null };
    return { state: withBuffer(s, buf.slice(0, cursor - 1) + buf.slice(cursor), cursor - 1), effect: null };
  }
  if (name === 'delete') {
    return { state: withBuffer(s, buf.slice(0, cursor) + buf.slice(cursor + 1), cursor), effect: null };
  }
  if (name === 'left') return { state: withBuffer(s, buf, Math.max(0, cursor - 1)), effect: null };
  if (name === 'right') return { state: withBuffer(s, buf, Math.min(buf.length, cursor + 1)), effect: null };
  if (name === 'home' || (key.ctrl && name === 'a')) return { state: withBuffer(s, buf, 0), effect: null };
  if (name === 'end' || (key.ctrl && name === 'e')) return { state: withBuffer(s, buf, buf.length), effect: null };
  if (key.ctrl && name === 'u') return { state: withBuffer(s, '', 0), effect: null };
  if (name === 'up') return { state: historyUp(s), effect: null };
  if (name === 'down') return { state: historyDown(s), effect: null };

  if (!key.ctrl && !key.meta && typeof key.sequence === 'string' && key.sequence.length > 0 && isPrintable(key.sequence)) {
    return { state: insertText(s, key.sequence), effect: null };
  }
  return { state: s, effect: null };
}

/**
 * Parses a slash command line.
 * @param {string} line
 * @returns {null | {command: string, arg?: string} | {error: string}} null when not a slash command
 */
export function parseSlash(line) {
  if (typeof line !== 'string' || !line.startsWith('/')) return null;
  const body = line.slice(1).trim();
  const space = body.search(/\s/);
  const name = space === -1 ? body : body.slice(0, space);
  const arg = space === -1 ? '' : body.slice(space + 1).trim();
  if (name === '') return { error: 'empty slash command; try /help' };
  if (!SLASH_COMMANDS.includes(name)) return { error: `unknown command: /${name} (try /help)` };
  if (name === 'model') {
    if (arg === '') return { error: 'usage: /model <provider>/<model>' };
    return { command: 'model', arg };
  }
  return arg === '' ? { command: name } : { command: name, arg };
}
