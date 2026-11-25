import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { promisify } from 'node:util';

const execute = promisify(execFile);
export const bytes = value => Buffer.byteLength(JSON.stringify(value));
export const MAX_BYTES = 32768;
export const BOARD_BYTES = 1920;

export function positive(value, name) {
  if (!Number.isSafeInteger(value) || value < 1) throw new Error('invalid ' + name);
  return value;
}

export function repo(value) {
  if (typeof value !== 'string' || !/^[\w.][\w.-]*\/[\w.][\w.-]*$/.test(value)) {
    throw new Error('expected owner/repository');
  }
  return value;
}

export async function command(file, args, config) {
  try {
    const { stdout } = await execute(file, args, {
      encoding: 'utf8', timeout: positive(config.timeoutMs ?? 30000, 'timeoutMs'),
      maxBuffer: 64 * 1024 * 1024, windowsHide: true, shell: false,
      env: { ...process.env, GH_PROMPT_DISABLED: '1', GIT_OPTIONAL_LOCKS: '0' },
    });
    return stdout;
  } catch (e) {
    const reason = e.killed ? 'timeout' : String(e.code ?? e.signal ?? 'command failure');
    const detail = String(e.stderr || '').trim().slice(0, 180);
    throw new Error([file, reason, detail].filter(Boolean).join(' '));
  }
}

export async function guarded(source, read) {
  try {
    return { source, ...await read() };
  } catch (error) {
    return { source, error: String(error.message).slice(0, 240) };
  }
}

export function result(data, errors = [], truncated = false) {
  return { exitCode: errors.length ? 1 : 0, truncated, errors, data };
}

// Remove whole rows. Never parse or return a partial JSON document.
export function cappedRows(rows, errors, truncated = false, metadata = {}) {
  const out = { ...result([], errors, truncated), ...metadata };
  for (const row of rows) {
    out.data.push(row);
    if (bytes(out) + 1 > MAX_BYTES) {
      out.data.pop();
      out.truncated = true;
      break;
    }
  }
  return out;
}

export async function main(task, budget = MAX_BYTES) {
  let out;
  try {
    const config = JSON.parse(await readFile(process.argv[2], 'utf8'));
    out = await task(config, command);
    if (bytes(out) + 1 > budget) {
      out = result([], ['output exceeds byte budget; narrow the input'], true);
    }
  } catch (error) {
    out = result([], [String(error.message).slice(0, 240)]);
  }
  process.stdout.write(JSON.stringify(out) + '\n');
  process.exitCode = out.exitCode;
}
