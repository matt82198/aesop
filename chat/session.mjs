/**
 * Chat session: owns the transcript and status, runs slash commands, and streams backend chunks.
 *
 * submit(line, chunks) returns a promise. Each chunk of an async-iterable backend is appended
 * to the streaming assistant message and onFrame is called with a fresh frame right away, so
 * the frame for chunk N is emitted while the stream is still open (true streaming, no buffering).
 *
 * Node stdlib only; the backend is injected. The default echo source makes no network call.
 *
 * @module chat/session
 */

import { renderFrame } from './render.mjs';
import { parseSlash } from './input.mjs';

const HELP_LINES = [
  'Slash commands:',
  '  /help                  list these commands',
  '  /model <prov>/<model>  switch provider and model (transcript is kept)',
  '  /auth                  show credential status (no network call)',
  '  /clear                 clear the transcript',
  '  /exit, /quit           leave chat (ctrl-c twice also quits)',
];

const DEFAULT_STATUS = {
  model: 'claude-haiku-5-5',
  provider: 'anthropic',
  cwd: process.cwd(),
  tokens: 0,
  elapsedMs: 0,
  spinnerTick: 0,
};

/**
 * Local echo backend: streams the line back one word per chunk. No network.
 * @param {string} line
 * @returns {AsyncGenerator<string>}
 */
export async function* echoSource(line) {
  const words = String(line).split(/\s+/).filter((w) => w !== '');
  for (let i = 0; i < words.length; i += 1) {
    yield i === 0 ? words[i] : ` ${words[i]}`;
  }
}

/**
 * Stateful chat session. Rendering is delegated to renderFrame; this class never writes to a stream.
 */
export class Session {
  /**
   * @param {{columns?: number, rows?: number, color?: boolean, unicode?: boolean,
   *          status?: object, onFrame?: (frame: string) => void}} [opts]
   */
  constructor({ columns = 80, rows = 24, color = false, unicode = false, status = {}, onFrame = () => {} } = {}) {
    this.opts = { columns, rows, color, unicode };
    this.onFrame = onFrame;
    this.state = {
      transcript: [],
      inputBuffer: '',
      cursor: 0,
      mode: 'idle',
      hint: null,
      exit: false,
      status: { ...DEFAULT_STATUS, ...status },
    };
  }

  /** Renders the current state and hands the frame to onFrame. */
  emit() {
    this.onFrame(renderFrame(this.state, this.opts));
  }

  /**
   * Records a new terminal size. The next emitted frame uses it; resize itself emits nothing,
   * so the caller re-renders (cli.mjs does this on the stdout resize event).
   * @param {number} columns
   * @param {number} rows
   */
  resize(columns, rows) {
    this.opts.columns = Math.max(1, Math.trunc(columns) || 1);
    this.opts.rows = Math.max(1, Math.trunc(rows) || 1);
  }

  /**
   * Copies the line-editor buffer and cursor into the session state, then emits a frame.
   * @param {{inputBuffer: string, cursor: number}} input
   */
  setInput(input) {
    this.state.inputBuffer = input.inputBuffer;
    this.state.cursor = input.cursor;
    this.emit();
  }

  /**
   * Sets or clears the one-line hint shown above the status bar, then emits a frame.
   * @param {string|null} text
   */
  setHint(text) {
    this.state.hint = text ?? null;
    this.emit();
  }

  /**
   * Submits one line. Slash commands run locally; anything else is a turn whose reply streams
   * from `chunks` (default: the local echo source).
   * @param {string} line
   * @param {AsyncIterable<string>|Iterable<string>} [chunks]
   * @returns {Promise<void>}
   */
  async submit(line, chunks) {
    const text = String(line ?? '');
    const cmd = parseSlash(text);
    if (cmd !== null) {
      this.runCommand(cmd);
      this.emit();
      return;
    }
    if (text.trim() === '') return;
    await this.runTurn(text, chunks ?? echoSource(text));
  }

  runCommand(cmd) {
    if (cmd.error) {
      this.state.transcript.push({ role: 'system', text: cmd.error });
    } else if (cmd.command === 'help') {
      for (const text of HELP_LINES) this.state.transcript.push({ role: 'system', text });
    } else if (cmd.command === 'model') {
      this.setModel(cmd.arg);
    } else if (cmd.command === 'clear') {
      this.state.transcript = [];
    } else if (cmd.command === 'auth') {
      this.state.transcript.push({
        role: 'system',
        text: 'auth: no credentials are configured in this build; no network call is made',
      });
    } else if (cmd.command === 'exit' || cmd.command === 'quit') {
      this.state.exit = true;
    }
  }

  /**
   * Sets status.provider and status.model from "provider/model" (or a bare model name, which
   * keeps the current provider). The transcript is never touched.
   * @param {string} arg
   */
  setModel(arg) {
    const slash = arg.indexOf('/');
    if (slash > 0) {
      this.state.status.provider = arg.slice(0, slash);
      this.state.status.model = arg.slice(slash + 1);
    } else {
      this.state.status.model = arg;
    }
  }

  async runTurn(text, chunks) {
    const reply = { role: 'assistant', text: '', streaming: true };
    this.state.transcript.push({ role: 'user', text }, reply);
    this.state.mode = 'streaming';
    this.state.hint = null;
    const started = Date.now();
    this.emit();
    try {
      for await (const chunk of chunks) {
        const piece = String(chunk);
        reply.text += piece;
        this.state.status.tokens += piece.split(/\s+/).filter((w) => w !== '').length;
        this.state.status.spinnerTick += 1;
        this.state.status.elapsedMs = Date.now() - started;
        this.emit();
      }
    } catch (err) {
      this.state.transcript.push({ role: 'system', text: `stream error: ${err.message}` });
    } finally {
      reply.streaming = false;
      this.state.mode = 'idle';
      this.state.status.elapsedMs = Date.now() - started;
    }
    this.emit();
  }
}
