import { open } from 'node:fs/promises';
import { bytes, guarded, result } from './runtime.mjs';

const READ_LIMIT = 1024 * 1024;
const ROW_BUDGET = 14000;

async function tail(spec, peerOnly) {
  const { path, offset } = spec;
  if (!Number.isSafeInteger(offset) || offset < 0) throw new Error('invalid byte offset');
  const file = await open(path, 'r');
  try {
    const { size } = await file.stat();
    if (size < offset) throw new Error('file shrank; reset offset');
    const start = Math.max(0, offset - 1);
    const length = Math.min(size - start, READ_LIMIT + offset - start);
    const buf = Buffer.alloc(length);
    let got = 0;
    while (got < length) {
      const { bytesRead } = await file.read(buf, got, length - got, start + got);
      if (!bytesRead) throw new Error('file shrank during read');
      got += bytesRead;
    }
    if (offset && buf[0] !== 10) throw new Error('offset must follow a newline');
    let begin = offset - start, used = 0;
    const rows = [];
    for (;;) {
      const end = buf.indexOf(10, begin);
      if (end < 0) break;
      const line = buf.toString('utf8', begin, end).replace(/\r$/, '');
      let row = line;
      if (peerOnly) {
        const event = JSON.parse(line);
        row = event.kind === 'peer' ? event : undefined;
        if (row) {
          // The source already identifies this as a peer event.
          delete row.kind;
        }
      }
      if (row !== undefined) {
        const cost = bytes(row) + 1;
        if (cost > ROW_BUDGET && rows.length === 0) throw new Error('single row exceeds output budget at byte ' + (start + begin));
        if (used + cost > ROW_BUDGET) break;
        used += cost;
        rows.push(row);
      }
      begin = end + 1;
    }
    const next = start + begin;
    if (next === offset && size - next >= READ_LIMIT) throw new Error('single line exceeds read limit');
    return { next, size, rows, more: next < size };
  } finally {
    await file.close();
  }
}

export async function feeds(c) {
  const parts = await Promise.all(['inbox', 'activity'].map(key =>
    guarded(key, () => tail(c[key], key === 'activity'))));
  return result(parts.filter(p => !p.error), parts.filter(p => p.error), parts.some(p => p.more));
}
