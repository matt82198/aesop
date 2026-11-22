// `aesop ask`: one-shot, non-interactive prompt to a local open-weight model. The registry, selection and
// provider gate are the chat's (chat/harness.mjs); the turn itself runs in the aesop turn engine, the driver
// child (chat/driver_source.mjs -> driver/turn.py --serve, spec H-R26). It holds no conversation state and
// writes no history file.
// Exit codes: 0 ok, 2 usage, 3 provider unreachable, 4 provider/model error, 5 timeout, 6 boundary refused.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { prepareChangeSummary } from './change_summary.mjs';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { driverSource } from './driver_source.mjs';
import { callerLabel, createFeedWriter } from './activity_feed.mjs';
import { acceptsProofReceipt, proofReceiptSchema } from './proof_receipt.mjs';
import { applyDefaultProvider, assertProviderAllowed, harnessSource, loadRegistry, ProviderRefused, resolveSelection } from './harness.mjs';

export const EXIT = Object.freeze({ OK: 0, USAGE: 2, UNREACHABLE: 3, PROVIDER: 4, TIMEOUT: 5, REFUSED: 6 });

const USAGE = 'usage: aesop ask [--model <provider>/<model>] [--system <text>] [--from <name>] [--json] [--split-claudemd-line] [--proof-receipt] [--change-summary] [--timeout <s>] "<prompt>"';
const DEFAULT_TIMEOUT_S = 300;

/**
 * Parses ask argv. Everything after `--` is prompt text.
 * @param {string[]} argv
 * @returns {{error?: string, model?: string, system?: string, proofReceipt?: boolean, json: boolean, timeoutS: number, prompt: string}}
 */
export function parseAskArgs(argv) {
  const out = { json: false, timeoutS: DEFAULT_TIMEOUT_S, prompt: '' };
  const words = [];
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--') { words.push(...argv.slice(i + 1)); break; }
    if (a === '--json') out.json = true;
    else if (a === '--split-claudemd-line') out.splitClaudeMdLine = true;
    else if (a === '--proof-receipt') out.proofReceipt = true;
    else if (a === '--change-summary') out.changeSummary = true;
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
  if (out.prompt === '' && !out.splitClaudeMdLine) return { ...out, error: 'no prompt given' };
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
      if (code === EXIT.USAGE && !writer) return;
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
      const ok = code === EXIT.OK;
      const obj = { ok, provider: fields.provider ?? null, model: fields.model ?? null, text: !ok && args.proofReceipt ? '' : fields.text ?? '', elapsed_ms: Date.now() - started };
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

  if (args.splitClaudeMdLine) {
    const context = stdin && !stdin.isTTY ? await readAll(stdin) : '';
    const route = { provider: 'claudemd_split', model: null };
    activity.start(route, context);
    const script = fileURLToPath(new URL('../tools/claudemd_split.py', import.meta.url));
    const run = spawnSync(process.platform === 'win32' ? 'python' : 'python3', [script, '--stdin-line'], {
      input: context, encoding: 'utf8', windowsHide: true, timeout: Math.ceil(args.timeoutS * 1000),
    });
    if (run.error || run.status !== 0) {
      const code = run.error?.code === 'ETIMEDOUT' ? EXIT.TIMEOUT : run.status === 2 ? EXIT.USAGE : EXIT.PROVIDER;
      return finish(code, { ...route, error: run.error?.message || run.stderr.trim() || 'splitter failed' });
    }
    const text = run.stdout.trimEnd();
    activity.chunk(text);
    if (!args.json) stdout.write(text + '\n');
    return finish(EXIT.OK, { ...route, text });
  }

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

  // The driver assembles the messages (driver/turn.py _build_messages, the one builder): a system message,
  // then one user message with piped stdin first and the prompt after it. ask sends the three parts as they
  // are. The legacy harness takes a single user line, so that path joins the same parts in the same order.
  let context = '';
  if (stdin && !stdin.isTTY) context = await readAll(stdin);
  // Proof-receipt mode: the JSON packet on stdin is consumed by proofReceiptSchema and never sent as a context part.
  // The schema-constrained turn needs the native harness (driverSource has no responseSchema), so it goes there.
  // Change-summary mode: the packet on stdin is consumed by prepareChangeSummary and never sent as a context part;
  // the model receives the prompt plus the summary instructions, and the serialized facts are emitted afterwards.
  if (args.proofReceipt && args.changeSummary) {
    return finish(EXIT.USAGE, { provider, model, error: 'aesop ask: --proof-receipt and --change-summary cannot be combined' });
  }
  let schema;
  let summary;
  let promptText = args.prompt;
  if (args.proofReceipt) {
    try {
      schema = proofReceiptSchema(context);
    } catch (err) {
      return finish(EXIT.USAGE, { provider, model, error: `invalid proof-receipt input: ${err.message}` });
    }
    promptText += `\nReturn one eligible receipt ID as a JSON string. Choose "NONE" if there are no eligible IDs.\nResponse schema: ${JSON.stringify(schema)}`;
  }
  if (args.changeSummary) {
    try {
      summary = prepareChangeSummary(context);
    } catch (err) {
      return finish(EXIT.USAGE, { provider, model, error: `aesop ask: invalid change-summary input: ${err.message}` });
    }
    promptText += `\n\n${summary.prompt}`;
  }
  const partContext = schema || summary ? '' : context;
  const useHarness = backendFlag === 'harness' || schema !== undefined;
  const line = useHarness
    ? [args.system, partContext.trim() !== '' ? partContext.trimEnd() : '', promptText].filter(Boolean).join('\n\n')
    : promptText;
  const turnOptions = useHarness ? {} : { oneShot: { context: partContext, system: args.system } };

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
  const source = schema
    ? harnessSource(registry, () => ({ provider, model }), env, { notify, flash: () => {}, usage: () => {}, responseSchema: schema })
    : makeSource(registry, () => ({ provider, model }), env, { notify, flash: () => {}, usage: () => {} });

  let text = '';
  let reply = '';
  const emit = chunk => {
    text += chunk;
    activity.chunk(chunk);
    if (!args.json) stdout.write(chunk);
  };
  try {
    for await (const chunk of source(line, { signal: controller.signal, ...turnOptions })) {
      // Proof-receipt replies are buffered until validated; change-summary replies are buffered until serialized.
      if (schema) text += chunk;
      else if (summary) reply += chunk;
      else emit(chunk);
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
  if (schema) {
    if (!acceptsProofReceipt(schema, text)) {
      return finish(EXIT.PROVIDER, { provider, model, error: 'invalid proof-receipt response: expected a JSON string from the eligible enum' });
    }
    activity.chunk(text);
    if (!args.json) stdout.write(text);
  }
  if (summary) emit(summary.serialize(reply));
  if (!args.json && text !== '' && !text.endsWith('\n')) stdout.write('\n');
  return finish(EXIT.OK, { provider, model, text });
}
