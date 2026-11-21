// `aesop ask`: one-shot, non-interactive prompt to a local open-weight model. The registry, selection and
// provider gate are the chat's (chat/harness.mjs); the turn itself runs in the aesop turn engine, the driver
// child (chat/driver_source.mjs -> driver/turn.py --serve, spec H-R26). It holds no conversation state and
// writes no history file.
// Exit codes: 0 ok, 2 usage, 3 provider unreachable, 4 provider/model error, 5 timeout, 6 boundary refused.
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { driverSource } from './driver_source.mjs';
import { callerLabel, createFeedWriter } from './activity_feed.mjs';
import { applyDefaultProvider, assertProviderAllowed, harnessSource, loadRegistry, ProviderRefused, resolveSelection } from './harness.mjs';

export const EXIT = Object.freeze({ OK: 0, USAGE: 2, UNREACHABLE: 3, PROVIDER: 4, TIMEOUT: 5, REFUSED: 6 });

const USAGE = 'usage: aesop ask [--model <provider>/<model>] [--system <text>] [--from <name>] [--json] [--timeout <s>] "<prompt>"';
const DEFAULT_TIMEOUT_S = 300;

/**
 * Parses ask argv. Everything after `--` is prompt text.
 * @param {string[]} argv
 * @returns {{error?: string, model?: string, system?: string, json: boolean, timeoutS: number, prompt: string}}
 */
export function parseAskArgs(argv) {
  const out = { json: false, timeoutS: DEFAULT_TIMEOUT_S, prompt: '' };
  const words = [];
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--') { words.push(...argv.slice(i + 1)); break; }
    if (a === '--json') out.json = true;
    else if (a === '--model' || a === '--system' || a === '--from' || a === '--timeout') {
      const v = argv[i + 1];
      if (v === undefined) return { ...out, error: `${a} needs a value` };
      i += 1;
      if (a === '--model') out.model = v;
      else if (a === '--system') out.system = v;
      else if (a === '--from') out.from = v;
      else {
        const n = Number(v);
        if (!Number.isFinite(n) || n <= 0) return { ...out, error: '--timeout needs a positive number of seconds' };
        out.timeoutS = n;
      }
    } else if (a.startsWith('--') || a === '-h') {
      return { ...out, error: 'unknown option' };
    } else words.push(a);
  }
  out.prompt = words.join(' ').trim();
  if (out.prompt === '') return { ...out, error: 'no prompt given' };
  return out;
}

async function readAll(stream) {
  const chunks = [];
  for await (const c of stream) chunks.push(typeof c === 'string' ? Buffer.from(c) : c);
  return Buffer.concat(chunks).toString('utf8');
}

/** Maps a harness `error: <provider>: <class>: <reason>` line to an exit code. */
function codeForClass(cls) {
  if (cls === 'connect') return EXIT.UNREACHABLE;
  if (cls === 'timeout') return EXIT.TIMEOUT;
  if (cls === 'refused') return EXIT.REFUSED;
  return EXIT.PROVIDER;
}

/** Feed lifecycle shared by the streaming ask and early exits. Diagnostics stay on the feed. */
function askActivityFeed(args, env, stderr, started) {
  let writer;
  const start = ({ provider, model }, stdin = '') => {
    if (writer) return;
    writer = createFeedWriter({ env, stderr });
    writer.start({ from: callerLabel(args.from, env), model: [provider, model].filter(Boolean).join('/'), prompt: args.prompt, stdin });
  };
  return {
    start,
    chunk(text) { writer?.chunk(text); },
    end(code, fields) {
      if (code === EXIT.USAGE) return;
      start(fields);
      const status = { [EXIT.OK]: 'ok', [EXIT.TIMEOUT]: 'timeout', [EXIT.REFUSED]: 'refused' }[code] ?? 'error';
      if (fields.error) writer.error(fields.error);
      writer.end({ status, exit: code, elapsed_ms: Date.now() - started, error: fields.error });
    },
  };
}

/**
 * Runs one ask. Never prompts, never needs a TTY.
 * @param {{argv: string[], stdin?: NodeJS.ReadableStream, stdout: {write: Function}, stderr: {write: Function}, env?: Record<string,string|undefined>, cwd?: string}} io
 * @returns {Promise<number>} exit code
 */
export async function runAsk({ argv, stdin, stdout, stderr, env = process.env, cwd = process.cwd() }) {
  const started = Date.now();
  const args = parseAskArgs(argv);
  const activity = askActivityFeed(args, env, stderr, started);
  const finish = (code, fields) => {
    activity.end(code, fields);
    if (args.json) {
      const obj = { ok: code === EXIT.OK, provider: fields.provider ?? null, model: fields.model ?? null, text: fields.text ?? '', elapsed_ms: Date.now() - started };
      if (fields.error) obj.error = fields.error;
      stdout.write(`${JSON.stringify(obj)}\n`);
    }
    if (fields.error) stderr.write(`${fields.error}\n`);
    return code;
  };
  if (args.error) {
    stderr.write(`aesop ask: ${args.error}\n${USAGE}\n`);
    return EXIT.USAGE;
  }
  // The turn runs in the aesop turn engine (the driver child). AESOP_ASK_BACKEND=harness keeps the
  // in-process Node harness as a way back; any other value is a usage error, never a silent default.
  const backendFlag = env.AESOP_ASK_BACKEND;
  if (backendFlag !== undefined && backendFlag !== '' && backendFlag !== 'driver' && backendFlag !== 'harness') {
    stderr.write(`aesop ask: AESOP_ASK_BACKEND: unknown value "${backendFlag}" (use driver or harness)\n`);
    return EXIT.USAGE;
  }
  const makeSource = backendFlag === 'harness' ? harnessSource : driverSource;

  let configPath = env.AESOP_CHAT_CONFIG;
  if (!configPath) {
    const local = join(cwd, 'aesop.config.json');
    if (existsSync(local)) configPath = local;
  }
  const registry = loadRegistry(configPath);
  applyDefaultProvider(registry);
  for (const n of registry.notices) stderr.write(`${n}\n`);

  // Selection. An id that names a refused provider or a Claude model is a boundary refusal, not a usage error.
  let selection = registry.defaultSelection;
  if (args.model !== undefined) {
    const slash = args.model.indexOf('/');
    const pid = slash > 0 ? args.model.slice(0, slash) : '';
    const mid = slash > 0 ? args.model.slice(slash + 1) : '';
    if (/^(claude|anthropic)$/i.test(pid) || /^(claude|anthropic\/)/i.test(mid)) {
      return finish(EXIT.REFUSED, { provider: pid || null, model: mid || null, error: `${pid}: refused: model "${mid}" resolves to Claude` });
    }
    const entry = registry.providers.get(pid);
    if (entry && mid !== '') {
      try {
        assertProviderAllowed({ ...entry.raw, id: pid }, mid);
      } catch (err) {
        if (!(err instanceof ProviderRefused)) throw err;
        return finish(EXIT.REFUSED, { provider: pid, model: mid, error: `error: ${pid}: refused: ${err.reason}` });
      }
    }
    const picked = resolveSelection(registry, args.model);
    if (picked.error) return finish(EXIT.USAGE, { error: `aesop ask: ${picked.error}` });
    selection = { provider: picked.provider, model: picked.model };
  }
  if (!selection) return finish(EXIT.USAGE, { error: 'aesop ask: no provider selected (set "default" in the config or pass --model <provider>/<model>)' });
  const { provider, model } = selection;
  const entry = registry.providers.get(provider);
  if (entry?.refused) return finish(EXIT.REFUSED, { provider, model, error: `error: ${provider}: refused: ${entry.refused}` });

  // Prompt = system text, the prompt, then piped stdin as context. The harness takes one user line.
  let context = '';
  if (stdin && !stdin.isTTY) context = await readAll(stdin);
  let line = args.prompt;
  if (context.trim() !== '') line = `${line}\n\n--- context (stdin) ---\n${context}`;
  if (args.system) line = `${args.system}\n\n${line}`;

  activity.start({ provider, model }, context);
  const timeoutMs = Math.round(args.timeoutS * 1000);
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const failures = [];
  // Warnings (e.g. the Ollama context-window notice) are diagnostics: stderr now, exit code unchanged.
  // Only error/stream lines feed the failure classification below.
  const notify = (text) => {
    if (/^warning: /.test(text)) stderr.write(`${text}\n`);
    else failures.push(text);
  };
  const source = makeSource(registry, () => ({ provider, model }), env, { notify, flash: () => {}, usage: () => {} });

  let text = '';
  try {
    for await (const chunk of source(line, { signal: controller.signal })) {
      text += chunk;
      activity.chunk(chunk);
      if (!args.json) stdout.write(chunk);
    }
  } catch (err) {
    clearTimeout(timer);
    return finish(EXIT.PROVIDER, { provider, model, text, error: `error: ${provider}: ${err && err.message ? err.message : err}` });
  } finally {
    // One ask, one turn: end the driver child now so nothing outlives this process.
    source.close?.();
  }
  clearTimeout(timer);

  if (timedOut) return finish(EXIT.TIMEOUT, { provider, model, text, error: `error: ${provider}: timeout: timed out after ${args.timeoutS} s` });
  const failed = failures.find((f) => /^error: /.test(f)) ?? failures.find((f) => /^stream interrupted/.test(f));
  if (failed) {
    const m = /^error: [^:]+: ([a-z-]+): /.exec(failed);
    return finish(m ? codeForClass(m[1]) : EXIT.PROVIDER, { provider, model, text, error: failed });
  }
  if (!args.json && text !== '' && !text.endsWith('\n')) stdout.write('\n');
  return finish(EXIT.OK, { provider, model, text });
}
