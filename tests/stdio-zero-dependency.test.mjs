// Zero-dependency gate for the stdio/ pipeline package (CLAUDE.md invariant 5).
//
// Contract under test:
//  - every module specifier that stdio/ imports, requires, or re-exports is either a
//    Node built-in (bare name in require('node:module').builtinModules, or a node:-prefixed
//    built-in) or a relative specifier that resolves inside stdio/;
//  - a specifier the scanner cannot classify (non-literal import()/require() argument,
//    unknown declaration shape, unterminated token) is a violation (fail closed);
//  - stdio/ carries no package.json, node_modules/, *.node addon or binding.gyp.
//
// The scanner is a small lexer plus token-sequence matcher. The lexer drops comments and
// keeps string/template literals as single tokens, so the word "import" inside a comment or
// a string never produces a finding, while a real import statement is always found.
//
// Run: node --test tests/stdio-zero-dependency.test.mjs
// Must pass on Windows and Linux: all paths go through path.join / path.posix, no shell.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import nodeModule from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const STDIO_DIR = path.join(REPO_ROOT, 'stdio');
const REQUIRED_FILES = ['cli.mjs', 'pipe.mjs', 'frames.mjs', 'tty.mjs'];
const BUILTINS = new Set(nodeModule.builtinModules);
const REGEX_AFTER_KEYWORDS = new Set([
  'return', 'typeof', 'instanceof', 'in', 'of', 'new', 'delete', 'void',
  'throw', 'case', 'do', 'else', 'yield', 'await',
]);
// Tokens that end a declaration-shaped clause (import/export) when scanning for `from`.
const DECLARATION_STOP = new Set([
  'import', 'export', 'function', 'class', 'const', 'let', 'var', 'async',
]);

// ---------------------------------------------------------------------------
// Lexer
// ---------------------------------------------------------------------------

function isIdentStart(c) {
  return /[A-Za-z_$]/.test(c) || c.charCodeAt(0) > 127;
}

function isIdentPart(c) {
  return /[A-Za-z0-9_$]/.test(c) || c.charCodeAt(0) > 127;
}

function regexAllowedAfter(prev) {
  if (!prev) return true;
  if (prev.type === 'punct') return !(prev.value === ')' || prev.value === ']' || prev.value === '}');
  if (prev.type === 'ident') return REGEX_AFTER_KEYWORDS.has(prev.value);
  return false;
}

// Returns tokens {type, value, pos, end, subst?}. Types: ident, string, template, punct,
// number, regex, unterminated. Whitespace and comments are dropped. A template token
// carries subst=true when it is followed by a ${...} substitution.
function tokenize(src) {
  const toks = [];
  const stack = []; // 'brace' | 'tmpl' -- open braces and template substitutions
  const n = src.length;
  let i = 0;
  let prev = null;

  const add = (tok) => {
    toks.push(tok);
    prev = tok;
  };

  // i sits just past a backtick, or just past the } that closes a substitution.
  const readTemplate = (start) => {
    let text = '';
    while (i < n) {
      const c = src[i];
      if (c === '\\') {
        text += src.slice(i, i + 2);
        i += 2;
        continue;
      }
      if (c === '`') {
        i += 1;
        add({ type: 'template', value: text, subst: false, pos: start, end: i });
        return;
      }
      if (c === '$' && src[i + 1] === '{') {
        i += 2;
        stack.push('tmpl');
        add({ type: 'template', value: text, subst: true, pos: start, end: i });
        return;
      }
      text += c;
      i += 1;
    }
    add({ type: 'unterminated', value: '`', pos: start, end: n });
    i = n;
  };

  while (i < n) {
    const c = src[i];
    const d = src[i + 1];
    const start = i;

    if (/\s/.test(c)) {
      i += 1;
      continue;
    }
    if (c === '/' && d === '/') {
      const e = src.indexOf('\n', i);
      i = e === -1 ? n : e;
      continue;
    }
    if (c === '/' && d === '*') {
      const e = src.indexOf('*/', i + 2);
      if (e === -1) {
        add({ type: 'unterminated', value: '/*', pos: start, end: n });
        i = n;
        break;
      }
      i = e + 2;
      continue;
    }
    if (c === '"' || c === "'") {
      i += 1;
      let text = '';
      let closed = false;
      while (i < n) {
        const ch = src[i];
        if (ch === '\\') {
          text += src.slice(i, i + 2);
          i += 2;
          continue;
        }
        if (ch === c) {
          i += 1;
          closed = true;
          break;
        }
        if (ch === '\n') break;
        text += ch;
        i += 1;
      }
      add(closed
        ? { type: 'string', value: text, pos: start, end: i }
        : { type: 'unterminated', value: c, pos: start, end: i });
      continue;
    }
    if (c === '`') {
      i += 1;
      readTemplate(start);
      continue;
    }
    if (c === '/' && regexAllowedAfter(prev)) {
      let j = i + 1;
      let inClass = false;
      let closed = false;
      while (j < n && src[j] !== '\n') {
        const ch = src[j];
        if (ch === '\\') {
          j += 2;
          continue;
        }
        if (inClass) {
          if (ch === ']') inClass = false;
        } else if (ch === '[') {
          inClass = true;
        } else if (ch === '/') {
          closed = true;
          break;
        }
        j += 1;
      }
      if (closed) {
        j += 1;
        while (j < n && isIdentPart(src[j])) j += 1;
        i = j;
        add({ type: 'regex', value: src.slice(start, i), pos: start, end: i });
        continue;
      }
      // Not a regex after all: a division operator.
      i += 1;
      add({ type: 'punct', value: '/', pos: start, end: i });
      continue;
    }
    if (c === '{') {
      stack.push('brace');
      i += 1;
      add({ type: 'punct', value: '{', pos: start, end: i });
      continue;
    }
    if (c === '}') {
      i += 1;
      const top = stack[stack.length - 1];
      if (top === 'tmpl') {
        stack.pop();
        readTemplate(start);
        continue;
      }
      if (top === 'brace') stack.pop();
      add({ type: 'punct', value: '}', pos: start, end: i });
      continue;
    }
    if (isIdentStart(c)) {
      i += 1;
      while (i < n && isIdentPart(src[i])) i += 1;
      add({ type: 'ident', value: src.slice(start, i), pos: start, end: i });
      continue;
    }
    if (/[0-9]/.test(c)) {
      i += 1;
      while (i < n && /[0-9A-Za-z_.]/.test(src[i])) i += 1;
      add({ type: 'number', value: src.slice(start, i), pos: start, end: i });
      continue;
    }
    i += 1;
    add({ type: 'punct', value: c, pos: start, end: i });
  }
  return toks;
}

// ---------------------------------------------------------------------------
// Classification
// ---------------------------------------------------------------------------

function isNodeBuiltin(spec) {
  const bare = spec.slice('node:'.length);
  if (BUILTINS.has(bare)) return true;
  // Scheme-only built-ins (node:test, node:sea, node:sqlite) are not in builtinModules
  // on every Node version; isBuiltin() knows them.
  return typeof nodeModule.isBuiltin === 'function' && nodeModule.isBuiltin(spec);
}

// Returns null when the specifier is allowed, otherwise the specifier itself.
// relDir is the importing file's directory relative to the package root ('' at the root).
function violationFor(spec, relDir) {
  if (spec === '' || spec.includes('\\') || /[\u0000-\u001f]/.test(spec)) return spec || '<empty>';
  if (spec.startsWith('node:')) return isNodeBuiltin(spec) ? null : spec;
  if (spec === '.' || spec === '..' || spec.startsWith('./') || spec.startsWith('../')) {
    const resolved = path.posix.normalize(path.posix.join(relDir, spec));
    const escapes = resolved === '..' || resolved.startsWith('../') || path.posix.isAbsolute(resolved);
    return escapes ? spec : null;
  }
  return BUILTINS.has(spec) ? null : spec;
}

// Returns the list of violations found in one module's source text.
// Entries are either a bare offending specifier (e.g. 'yaml') or a
// '<unclassifiable: ...>' marker for a specifier the scanner cannot read statically.
function findNonBuiltinImports(sourceText, relDir = '') {
  const toks = tokenize(sourceText);
  const findings = [];
  const isP = (t, v) => Boolean(t) && t.type === 'punct' && t.value === v;
  const isI = (t, v) => Boolean(t) && t.type === 'ident' && t.value === v;
  const literalOf = (t) => {
    if (!t) return null;
    if (t.type === 'string') return t.value;
    if (t.type === 'template' && !t.subst) return t.value;
    return null;
  };
  const check = (spec) => {
    const bad = violationFor(spec, relDir);
    if (bad !== null) findings.push(bad);
  };
  const unclassifiable = (what) => findings.push(`<unclassifiable: ${what}>`);

  // Literal first argument of a call whose '(' is at index openIdx, or null.
  const callLiteral = (openIdx) => {
    const lit = literalOf(toks[openIdx + 1]);
    const after = toks[openIdx + 2];
    if (lit !== null && (isP(after, ')') || isP(after, ','))) return lit;
    return null;
  };

  // Scans a declaration clause from index `from` for `from <string>`.
  const fromClause = (from) => {
    for (let j = from; j < toks.length; j += 1) {
      const t = toks[j];
      if (isP(t, ';')) return null;
      if (t.type === 'ident' && DECLARATION_STOP.has(t.value)) return null;
      if (isI(t, 'from')) {
        const lit = literalOf(toks[j + 1]);
        if (lit !== null) return lit;
        return null;
      }
    }
    return null;
  };

  for (let k = 0; k < toks.length; k += 1) {
    const t = toks[k];
    if (t.type !== 'ident') continue;
    const afterDot = isP(toks[k - 1], '.');
    const next = toks[k + 1];

    if (t.value === 'require' && isP(next, '(')) {
      const lit = callLiteral(k + 1);
      if (lit === null) unclassifiable('require() with non-literal argument');
      else check(lit);
      continue;
    }

    if (t.value === 'createRequire') {
      // Loads modules through a runtime-built require; the call site cannot be checked.
      unclassifiable('createRequire()');
      continue;
    }

    if (afterDot) continue;

    if (t.value === 'import') {
      if (isP(next, '.')) continue; // import.meta
      if (isP(next, '(')) {
        const lit = callLiteral(k + 1);
        if (lit === null) unclassifiable('import() with non-literal argument');
        else check(lit);
        continue;
      }
      const direct = literalOf(next); // side-effect import 'x'
      if (direct !== null) {
        check(direct);
        continue;
      }
      const lit = fromClause(k + 1);
      if (lit === null) unclassifiable('import declaration without literal from-specifier');
      else check(lit);
      continue;
    }

    if (t.value === 'export') {
      const lit = fromClause(k + 1); // export ... from 'x'
      if (lit !== null) check(lit);
    }
  }
  return findings;
}

// ---------------------------------------------------------------------------
// Package walking
// ---------------------------------------------------------------------------

function toPosix(p) {
  return p.split(path.sep).join('/');
}

function listSourceFiles(dir, rel = '') {
  const out = [];
  for (const ent of fs.readdirSync(path.join(dir, rel), { withFileTypes: true })) {
    const entRel = rel ? path.join(rel, ent.name) : ent.name;
    if (ent.isDirectory()) out.push(...listSourceFiles(dir, entRel));
    else if (/\.(mjs|cjs|js)$/.test(ent.name)) out.push(entRel);
  }
  return out.sort();
}

function listAllEntries(dir, rel = '') {
  const out = [];
  for (const ent of fs.readdirSync(path.join(dir, rel), { withFileTypes: true })) {
    const entRel = rel ? path.join(rel, ent.name) : ent.name;
    out.push({ rel: toPosix(entRel), name: ent.name, isDir: ent.isDirectory() });
    if (ent.isDirectory()) out.push(...listAllEntries(dir, entRel));
  }
  return out;
}

// Returns 'file: specifier' strings for every violation in every source file under dir.
function scanPackageDir(dir) {
  const violations = [];
  for (const rel of listSourceFiles(dir)) {
    const src = fs.readFileSync(path.join(dir, rel), 'utf8');
    const relDir = path.posix.dirname(toPosix(rel));
    for (const spec of findNonBuiltinImports(src, relDir === '.' ? '' : relDir)) {
      violations.push(`${toPosix(rel)}: ${spec}`);
    }
  }
  return violations;
}

function assertPackageDirExists() {
  assert.ok(
    fs.existsSync(STDIO_DIR) && fs.statSync(STDIO_DIR).isDirectory(),
    `stdio/ package directory missing at ${STDIO_DIR}`,
  );
}

// ---------------------------------------------------------------------------
// Scanner unit tests (no stdio/ needed)
// ---------------------------------------------------------------------------

test('scanner_flags_planted_third_party_import', () => {
  const src = [
    "import yaml from 'yaml';",
    "const lodash = require('lodash');",
    "const chalk = await import('chalk');",
    "export * from 'zod';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(src).sort(), ['chalk', 'lodash', 'yaml', 'zod']);
});

test('scanner_accepts_node_builtins', () => {
  const src = [
    "import fs from 'node:fs';",
    "import { Readable } from 'node:stream';",
    "import legacyFs from 'fs';",
    "import { pipeline } from 'stream/promises';",
    "import { randomBytes } from 'node:crypto';",
    "const path = require('node:path');",
    "const test = await import('node:test');",
    "export { readFileSync } from 'node:fs';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(src), []);
});

test('scanner_accepts_in_package_relative_import', () => {
  const atRoot = [
    "import { runPipe } from './pipe.mjs';",
    "import { encode } from './lib/../frames.mjs';",
    "export * from './tty.mjs';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(atRoot), []);

  const inSubdir = [
    "import { encode } from '../frames.mjs';",
    "import { helper } from './util.mjs';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(inSubdir, 'lib'), []);
});

test('scanner_flags_relative_import_escaping_package', () => {
  const atRoot = [
    "import secret from '../secret.mjs';",
    "import outside from './lib/../../outside.mjs';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(atRoot), ['../secret.mjs', './lib/../../outside.mjs']);

  // From stdio/lib/, '../../x' climbs out of the package; '../frames.mjs' stays inside.
  const fromSubdir = [
    "import outside from '../../outside.mjs';",
    "import inside from '../frames.mjs';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(fromSubdir, 'lib'), ['../../outside.mjs']);
});

test('scanner_fail_closed_on_unclassifiable_specifier', () => {
  assert.deepEqual(
    findNonBuiltinImports('const m = await import(variable);'),
    ['<unclassifiable: import() with non-literal argument>'],
  );
  assert.deepEqual(
    findNonBuiltinImports('const m = require(expr);'),
    ['<unclassifiable: require() with non-literal argument>'],
  );
  assert.deepEqual(
    findNonBuiltinImports('const m = await import(`pkg-${name}`);'),
    ['<unclassifiable: import() with non-literal argument>'],
  );
  assert.deepEqual(
    findNonBuiltinImports("import x from someVariable;"),
    ['<unclassifiable: import declaration without literal from-specifier>'],
  );
  // A literal that is an escaped, obfuscated name is not a builtin and must not pass.
  assert.deepEqual(findNonBuiltinImports("import y from 'y\\x61ml';"), ["y\\x61ml"]);
});

test('scanner_ignores_comments_and_strings_but_finds_real_imports', () => {
  const inert = [
    "// import x from 'yaml'",
    "/* require('lodash') and import('chalk') */",
    "const s = \"import y from 'zod'\";",
    "const t = `import('zod')`;",
    "import fs from 'node:fs';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(inert), []);

  const real = "/* header: import nothing here */\nimport yaml from 'yaml';\n";
  assert.deepEqual(findNonBuiltinImports(real), ['yaml']);
});

// ---------------------------------------------------------------------------
// Package gate (reads stdio/)
// ---------------------------------------------------------------------------

test('stdio_package_has_only_builtin_imports', () => {
  assertPackageDirExists();
  for (const required of REQUIRED_FILES) {
    assert.ok(
      fs.existsSync(path.join(STDIO_DIR, required)),
      `stdio/${required} missing (package is not yet implemented)`,
    );
  }
  assert.ok(listSourceFiles(STDIO_DIR).length > 0, 'stdio/ contains no .mjs/.js sources');
  assert.deepEqual(scanPackageDir(STDIO_DIR), []);
});

test('stdio_package_has_no_manifest_or_native_addons', () => {
  assertPackageDirExists();
  const offenders = listAllEntries(STDIO_DIR).filter((e) =>
    e.name === 'package.json'
    || (e.isDir && e.name === 'node_modules')
    || e.name.endsWith('.node')
    || e.name === 'binding.gyp',
  );
  assert.deepEqual(offenders.map((e) => `stdio/${e.rel}`), []);
});

test('gate_goes_red_when_third_party_import_is_planted_in_a_copy', () => {
  assertPackageDirExists();
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'stdio-zero-dep-'));
  try {
    const copy = path.join(tmp, 'stdio');
    fs.cpSync(STDIO_DIR, copy, { recursive: true });
    const target = path.join(copy, 'pipe.mjs');
    assert.ok(fs.existsSync(target), 'stdio/pipe.mjs missing, nothing to plant into');
    fs.appendFileSync(target, "\nimport yaml from 'yaml';\n");

    const violations = scanPackageDir(copy);
    assert.deepEqual(violations, ['pipe.mjs: yaml']);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
