/**
 * Chat session: owns the transcript and status, runs slash commands, and streams backend chunks.
 *
 * submit(line, chunks) returns a promise. Each chunk of an async-iterable backend is appended
 * to the streaming assistant message and onFrame is called with a fresh frame right away, so
 * the frame for chunk N is emitted while the stream is still open. That holds for an injected
 * source that yields incrementally. The default backend, pipeSource, runs the whole pipe turn
 * before it yields its single chunk, so with the default the reply is buffered and arrives in
 * one frame.
 *
 * Node stdlib only. The default backend is the stdio pipe runtime (raw passthrough, no network
 * call); a test or embedder can inject another backend through the `source` option.
 *
 * @module chat/session
 */

import { Readable, Writable } from 'node:stream';
import { renderFrame } from './render.mjs';
import { parseSlash } from './input.mjs';
import { runPipe } from '../stdio/pipe.mjs';

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
 * Default backend: runs the stdio pipe runtime in raw passthrough mode for one turn. The turn text
 * is the pipe's stdin and the reply is the pipe's stdout bytes, decoded once and yielded as one
 * chunk, so the reply equals what `aesop pipe` writes for the same input. No network call.
 * A non-zero pipe exit throws, which the caller renders as a stream error.
 * @param {string} line
 * @returns {AsyncGenerator<string>}
 */
export async function* pipeSource(line) {
  const out = [];
  const err = [];
  const stdin = Readable.from([Buffer.from(String(line), 'utf8')]);
  const stdout = new Writable({
    write(chunk, _enc, cb) {
      out.push(Buffer.from(chunk));
      cb();
    },
  });
  const stderr = new Writable({
    write(chunk, _enc, cb) {
      err.push(Buffer.from(chunk));
      cb();
    },
  });
  const code = await runPipe({ stdin, stdout, stderr, argv: [] });
  if (code !== 0) {
    const detail = Buffer.concat(err).toString('utf8').trim();
    throw new Error(`pipe exited with code ${code}${detail ? `: ${detail}` : ''}`);
  }
  yield Buffer.concat(out).toString('utf8');
}

/**
 * Stateful chat session. Rendering is delegated to renderFrame; this class never writes to a stream.
 */
export class Session {
  /**
   * @param {{columns?: number, rows?: number, color?: boolean, unicode?: boolean,
   *          status?: object, onFrame?: (frame: string) => void,
   *          source?: (line: string) => AsyncIterable<string>|Iterable<string>}} [opts]
   *   `source` is the default backend for plain turns (default: pipeSource).
   */
  constructor({
    columns = 80,
    rows = 24,
    color = false,
    unicode = false,
    status = {},
    onFrame = () => {},
    source = pipeSource,
  } = {}) {
    this.opts = { columns, rows, color, unicode };
    this.onFrame = onFrame;
    this.source = source;
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
   * from `chunks` (default: this.source, which is the pipe runtime).
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
    await this.runTurn(text, chunks ?? this.source(text));
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
