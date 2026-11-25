import { channel } from 'node:diagnostics_channel';
import { pulls } from './github.mjs';
import { feeds } from './feeds-lib.mjs';
import { worktrees } from './worktrees-lib.mjs';
import { BOARD_BYTES, bytes, result } from './runtime.mjs';

const timing = channel('chain.board.elapsed');
function summarize(name, out) {
  if (name === 'pulls') {
    const groups = new Map((out.emptyRepos ?? []).map(repo => [repo, []]));
    for (const [repo, n, , state, , checks] of out.data) {
      if (!groups.has(repo)) groups.set(repo, []);
      groups.get(repo).push([n, state, checks.length]);
    }
    const rows = Array.from(groups, ([repo, prs]) => {
      const flagged = prs.filter(([, state, checks]) => checks || state !== 'CLEAN');
      return [repo, prs.length, flagged.length, flagged.slice(0, 3)];
    });
    return { open: out.data.length, rows, truncated: rows.some(r => r[2] > 3) };
  }
  if (name === 'feeds') {
    let shortened = false;
    const rows = out.data.map(p => {
      const last = p.rows.at(-1);
      const text = typeof last === 'string' ? last : last?.prompt ?? last?.status ?? '';
      if (p.rows.length > 1 || String(text).length > 100) shortened = true;
      return [p.source, p.rows.length, String(text).slice(0, 100)];
    });
    return { rows, truncated: shortened };
  }
  const changed = out.data.filter(w => w.dirty || w.ahead || w.behind);
  return { inspected: out.data.length, changed: changed.length,
    rows: changed.map(w => [w.branch, w.ahead, w.behind, w.dirty]) };
}

export async function board(c, run) {
  const sections = await Promise.all(Object.entries({ pulls, feeds, worktrees }).map(async ([name, task]) => {
    const start = performance.now();
    let out;
    try {
      out = await task(c, run);
    } catch (e) {
      out = result([], [String(e.message)]);
    }
    const section = summarize(name, out);
    section.truncated = Boolean(section.truncated || out.truncated);
    const errors = out.errors.map(e => typeof e === 'string' ? e : e.source + ': ' + e.error);
    if (errors.length) {
      section.errorCount = errors.length;
      section.firstError = errors[0].slice(0, 100);
      if (errors.length > 1 || errors[0].length > 100) section.truncated = true;
    }
    const totalRows = section.rows.length;
    // Reserve room for the other two tasks, even when this one has a long queue.
    const candidates = section.rows;
    section.rows = [];
    for (const row of candidates) {
      section.rows.push(row);
      if (bytes(section) > 545) {
        section.rows.pop();
        break;
      }
    }
    if (totalRows > section.rows.length) section.truncated = true;
    timing.publish({ name, ms: performance.now() - start });
    return [name, section];
  }));
  const data = Object.fromEntries(sections);
  const failed = sections.filter(([, s]) => s.errorCount).map(([name]) => name);
  const out = result(data, failed, sections.some(([, s]) => s.truncated));
  if (bytes(out) + 1 > BOARD_BYTES) return result([], ['board exceeds byte budget'], true);
  return out;
}
