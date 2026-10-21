#!/usr/bin/env node
// INDEX: `aesop map <term...>` — deterministic lookup over INDEX.md and CLAUDE.md entry lines (Node STDLIB only)

/**
 * Aesop map — search the index files, not the source tree.
 *
 * Only files whose basename is exactly INDEX.md or CLAUDE.md are read, under the
 * search roots: the enclosing git repo root of the current directory (or the
 * current directory when it is not inside a repo), plus every --root DIR given.
 * Only entry lines are matched: list items ("- ", "* "), table rows ("|") and
 * headings ("#"). Table separator rows ("|---|") are skipped.
 *
 * Matching is case-insensitive substring per term. A term that also matches as a
 * whole word (letters, digits and underscore are word characters) counts as a
 * whole-word hit. Each line's score is (distinct terms matched) + (whole-word hits);
 * rows are ranked by score descending, then by path and line.
 *
 * Output: `path: one-liner` per row, at most MAX_ROWS rows. Exit 0 whether or not
 * anything matched; exit 2 for usage errors. A miss prints the fallback message so
 * the worker records a Discovery line instead of silently grepping.
 */

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const MAX_ROWS = 30;
const ONE_LINER_MAX = 160;
const INDEX_NAMES = new Set(['INDEX.md', 'CLAUDE.md']);
const SKIP_DIRS = new Set(['node_modules']);

function noMatchMessage(terms) {
  return `no index entry for ${terms.join(' ')}; grep is the fallback, record it as a Discovery line`;
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Repo root of `cwd` via git, or `cwd` itself when not inside a repo. */
function defaultRoot(cwd) {
  const r = spawnSync('git', ['rev-parse', '--show-toplevel'], {
    cwd,
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'ignore'],
    windowsHide: true,
  });
  const top = r.status === 0 ? (r.stdout || '').trim() : '';
  return top || cwd;
}

/** Every INDEX.md / CLAUDE.md file under `root`, skipping dot-dirs and node_modules. */
function findIndexFiles(root) {
  const found = [];
  const stack = [root];
  while (stack.length > 0) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (_err) {
      continue;
    }
    for (const ent of entries) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        if (ent.name.startsWith('.') || SKIP_DIRS.has(ent.name)) continue;
        stack.push(full);
      } else if (ent.isFile() && INDEX_NAMES.has(ent.name)) {
        found.push(full);
      }
    }
  }
  return found.sort();
}

/** Entry lines of one file: { line, text } where text is the cleaned one-liner. */
function entryLines(content) {
  const out = [];
  const lines = content.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i].trim();
    if (!raw) continue;
    const isEntry = raw.startsWith('- ') || raw.startsWith('* ') || raw.startsWith('|') || raw.startsWith('#');
    if (!isEntry) continue;
    if (/^\|[\s|:-]*\|?$/.test(raw)) continue; // markdown table separator
    const text = raw
      .replace(/^#+\s*/, '')
      .replace(/^[-*]\s+/, '')
      .replace(/^\|\s*/, '')
      .replace(/\s*\|$/, '')
      .replace(/\s+/g, ' ')
      .trim();
    if (!text) continue;
    out.push({ line: i + 1, text: text.length > ONE_LINER_MAX ? `${text.slice(0, ONE_LINER_MAX - 3)}...` : text });
  }
  return out;
}

/**
 * Search `roots` for `terms`. Returns { total, matches } with matches sorted by
 * score (desc), then path, then line. `matches` is uncapped; callers cap for display.
 */
function searchIndexes(roots, terms) {
  const lowered = terms.map((t) => t.toLowerCase());
  const wordRes = terms.map((t) => new RegExp(`(?<![A-Za-z0-9_])${escapeRegex(t)}(?![A-Za-z0-9_])`, 'i'));
  const matches = [];
  const seen = new Set();
  for (const root of roots) {
    for (const file of findIndexFiles(root)) {
      const abs = path.resolve(file);
      if (seen.has(abs)) continue;
      seen.add(abs);
      let content;
      try {
        content = fs.readFileSync(abs, 'utf-8');
      } catch (_err) {
        continue;
      }
      for (const entry of entryLines(content)) {
        const hay = entry.text.toLowerCase();
        const matched = [];
        let wholeWordHits = 0;
        terms.forEach((term, idx) => {
          if (!hay.includes(lowered[idx])) return;
          matched.push(term);
          if (wordRes[idx].test(entry.text)) wholeWordHits += 1;
        });
        if (matched.length === 0) continue;
        matches.push({
          path: abs,
          line: entry.line,
          text: entry.text,
          matched_terms: matched,
          whole_word_hits: wholeWordHits,
          score: matched.length + wholeWordHits,
        });
      }
    }
  }
  matches.sort((a, b) => b.score - a.score || a.path.localeCompare(b.path) || a.line - b.line);
  return { total: matches.length, matches };
}

/** Display path: relative to cwd (forward slashes) when inside it, else absolute. */
function displayPath(abs, cwd) {
  const rel = path.relative(cwd, abs);
  const shown = rel && !rel.startsWith('..') && !path.isAbsolute(rel) ? rel : abs;
  return shown.split(path.sep).join('/');
}

function parseArgs(argv) {
  const terms = [];
  const extraRoots = [];
  let json = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--json') {
      json = true;
    } else if (a === '--root') {
      if (i + 1 >= argv.length) return { error: '--root needs a directory' };
      extraRoots.push(path.resolve(argv[i + 1]));
      i++;
    } else if (a.startsWith('-')) {
      return { error: `unknown option: ${a}` };
    } else {
      terms.push(a);
    }
  }
  return { terms, extraRoots, json };
}

function usage(write) {
  write('Usage: aesop map <term...> [--root DIR]... [--json]');
  write('  Searches only INDEX.md and CLAUDE.md entry lines (list items, table rows, headings).');
  write('  Case-insensitive substring match; whole-word hits rank higher. At most 30 rows.');
  write('  --root DIR adds another project root to search (repeatable).');
}

function main(argv, cwd) {
  const parsed = parseArgs(argv);
  if (parsed.error) {
    process.stderr.write(`${parsed.error}\n`);
    usage((s) => process.stderr.write(`${s}\n`));
    return 2;
  }
  const { terms, extraRoots, json } = parsed;
  if (terms.length === 0) {
    usage((s) => process.stderr.write(`${s}\n`));
    return 2;
  }
  const roots = [defaultRoot(cwd), ...extraRoots];
  const { total, matches } = searchIndexes(roots, terms);
  const shown = matches.slice(0, MAX_ROWS);

  if (json) {
    const doc = {
      terms,
      roots: roots.map((r) => displayPath(r, cwd)),
      total,
      matches: shown.map((m) => ({ ...m, path: displayPath(m.path, cwd) })),
    };
    if (total === 0) doc.message = noMatchMessage(terms);
    process.stdout.write(`${JSON.stringify(doc, null, 2)}\n`);
    return 0;
  }

  if (total === 0) {
    process.stdout.write(`${noMatchMessage(terms)}\n`);
    return 0;
  }
  for (const m of shown) {
    process.stdout.write(`${displayPath(m.path, cwd)}: ${m.text}\n`);
  }
  if (total > shown.length) {
    process.stdout.write(`(${total - shown.length} more matches not shown; narrow the terms or use --json)\n`);
  }
  return 0;
}

module.exports = { main, searchIndexes, entryLines, parseArgs, noMatchMessage, MAX_ROWS };

if (require.main === module) {
  process.exitCode = main(process.argv.slice(2), process.cwd());
}
