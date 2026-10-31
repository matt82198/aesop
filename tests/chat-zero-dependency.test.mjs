// Zero-dependency gate for the chat/ package (interactive TUI, S1 spec).
//
// Contract under test:
//  - every module specifier chat/ imports, requires, or re-exports is a Node built-in
//    (bare name in require('node:module').builtinModules, or a node:-prefixed built-in) or a
//    relative specifier that resolves inside the repo;
//  - chat/ never imports ui/ (the web dashboard): the TUI stays standalone;
//  - a specifier the scanner cannot classify (non-literal import()/require() argument,
//    unknown declaration shape, unterminated token) is a violation (fail closed);
//  - chat/ carries no package.json, node_modules/, *.node addon or binding.gyp.
//
// Lexer and token matcher follow tests/stdio-zero-dependency.test.mjs. Scanner self-tests are
// gated on chat/ existing: they must not pass vacuously while the package is absent.
//
// Run: node --test --test-reporter=tap tests/chat-zero-dependency.test.mjs
// Must pass on Windows and Linux: all paths go through path.join / path.posix, no shell.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import nodeModule from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CHAT_DIR = path.join(REPO_ROOT, 'chat');
const REQUIRED_FILES = ['render.mjs', 'input.mjs', 'session.mjs', 'cli.mjs'];
const BUILTINS = new Set(nodeModule.builtinModules);
const REGEX_AFTER_KEYWORDS = new Set([
  'return', 'typeof', 'instanceof', 'in', 'of', 'new', 'delete', 'void',
  'throw', 'case', 'do', 'else', 'yield', 'await',
]);
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

// Tokens: {type, value, pos, end, subst?}. Types: ident, string, template, punct, number,
// regex, unterminated. Whitespace and comments are dropped.
function tokenize(src) {
  const toks = [];
  const stack = []; // 'brace' | 'tmpl'
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
  return typeof nodeModule.isBuiltin === 'function' && nodeModule.isBuiltin(spec);
}

// Returns null when allowed, otherwise the offending specifier.
// relDir is the importing file's directory relative to the repo root (e.g. 'chat').
function violationFor(spec, relDir) {
  if (spec === '' || spec.includes('\\') || /[\u0000-\u001f]/.test(spec)) return spec || '<empty>';
  if (spec.startsWith('node:')) return isNodeBuiltin(spec) ? null : spec;
  if (spec === '.' || spec === '..' || spec.startsWith('./') || spec.startsWith('../')) {
    const resolved = path.posix.normalize(path.posix.join(relDir, spec));
    const escapesRepo = resolved === '..' || resolved.startsWith('../') || path.posix.isAbsolute(resolved);
    if (escapesRepo) return spec;
    const intoWebUi = resolved === 'ui' || resolved.startsWith('ui/');
    return intoWebUi ? spec : null;
  }
  return BUILTINS.has(spec) || isNodeBuiltin(spec) ? null : spec;
}

// Returns violations in one module's source text: bare offending specifiers, or
// '<unclassifiable: ...>' markers for specifiers the scanner cannot read statically.
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

  const callLiteral = (openIdx) => {
    const lit = literalOf(toks[openIdx + 1]);
    const after = toks[openIdx + 2];
    if (lit !== null && (isP(after, ')') || isP(after, ','))) return lit;
    return null;
  };

  const fromClause = (from) => {
    for (let j = from; j < toks.length; j += 1) {
      const t = toks[j];
      if (isP(t, ';')) return null;
      if (t.type === 'ident' && DECLARATION_STOP.has(t.value)) return null;
      if (isI(t, 'from')) return literalOf(toks[j + 1]);
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
      const lit = fromClause(k + 1);
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

// Scans every source file under dir. Specifiers resolve against `prefix` (the directory's
// repo-relative path, 'chat' for the real package).
function scanPackageDir(dir, prefix = 'chat') {
  const violations = [];
  for (const rel of listSourceFiles(dir)) {
    const src = fs.readFileSync(path.join(dir, rel), 'utf8');
    const fileDir = path.posix.join(prefix, path.posix.dirname(toPosix(rel)));
    for (const spec of findNonBuiltinImports(src, fileDir)) {
      violations.push(`${toPosix(rel)}: ${spec}`);
    }
  }
  return violations;
}

// Scanner self-tests and package gates need the package. Gating the self-tests on it keeps
// them from passing vacuously while chat/ is absent.
function assertChatPackageExists() {
  assert.ok(
    fs.existsSync(CHAT_DIR) && fs.statSync(CHAT_DIR).isDirectory(),
    `chat/ package directory missing at ${CHAT_DIR}`,
  );
}

// ---------------------------------------------------------------------------
// Scanner self-tests (gated, see assertChatPackageExists)
// ---------------------------------------------------------------------------

test('scanner_flags_planted_third_party_import', () => {
  assertChatPackageExists();
  const src = [
    "import yaml from 'yaml';",
    "const lodash = require('lodash');",
    "const chalk = await import('chalk');",
    "export * from 'zod';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(src, 'chat').sort(), ['chalk', 'lodash', 'yaml', 'zod']);
});

test('scanner_accepts_node_builtins', () => {
  assertChatPackageExists();
  const src = [
    "import readline from 'node:readline';",
    "import { stdin } from 'node:process';",
    "import legacyFs from 'fs';",
    "import { pipeline } from 'stream/promises';",
    "const path = require('node:path');",
    "export { isTTY } from 'node:tty';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(src, 'chat'), []);
});

test('scanner_accepts_in_repo_relative_import_but_not_ui', () => {
  assertChatPackageExists();
  const inChat = [
    "import { renderFrame } from './render.mjs';",
    "import { helper } from '../tests/fixtures-helper.mjs';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(inChat, 'chat'), []);

  const intoUi = [
    "import { server } from '../ui/server.mjs';",
    "export * from '../ui/collector.mjs';",
  ].join('\n');
  assert.deepEqual(
    findNonBuiltinImports(intoUi, 'chat'),
    ['../ui/server.mjs', '../ui/collector.mjs'],
    'chat/ must not import the web UI',
  );
});

test('scanner_flags_relative_import_escaping_repo', () => {
  assertChatPackageExists();
  const src = [
    "import secret from '../../outside.mjs';",
    "import inner from './../render.mjs';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(src, 'chat'), ['../../outside.mjs']);
});

test('scanner_fail_closed_on_unclassifiable_specifier', () => {
  assertChatPackageExists();
  assert.deepEqual(
    findNonBuiltinImports('const m = await import(variable);', 'chat'),
    ['<unclassifiable: import() with non-literal argument>'],
  );
  assert.deepEqual(
    findNonBuiltinImports('const m = require(expr);', 'chat'),
    ['<unclassifiable: require() with non-literal argument>'],
  );
  assert.deepEqual(
    findNonBuiltinImports('const m = await import(`pkg-${name}`);', 'chat'),
    ['<unclassifiable: import() with non-literal argument>'],
  );
  assert.deepEqual(
    findNonBuiltinImports('import x from someVariable;', 'chat'),
    ['<unclassifiable: import declaration without literal from-specifier>'],
  );
  assert.deepEqual(findNonBuiltinImports("import y from 'y\\x61ml';", 'chat'), ['y\\x61ml']);
});

test('scanner_ignores_comments_and_strings_but_finds_real_imports', () => {
  assertChatPackageExists();
  const inert = [
    "// import x from 'yaml'",
    "/* require('lodash') and import('chalk') */",
    "const s = \"import y from 'zod'\";",
    "const t = `import('zod')`;",
    "import readline from 'node:readline';",
  ].join('\n');
  assert.deepEqual(findNonBuiltinImports(inert, 'chat'), []);

  const real = "/* header: import nothing here */\nimport yaml from 'yaml';\n";
  assert.deepEqual(findNonBuiltinImports(real, 'chat'), ['yaml']);
});

// ---------------------------------------------------------------------------
// Package gates (read chat/)
// ---------------------------------------------------------------------------

test('chat_package_has_required_modules', () => {
  assertChatPackageExists();
  for (const required of REQUIRED_FILES) {
    assert.ok(
      fs.existsSync(path.join(CHAT_DIR, required)),
      `chat/${required} missing (package is not yet implemented)`,
    );
  }
});

test('chat_package_has_only_builtin_or_in_repo_imports', () => {
  assertChatPackageExists();
  assert.ok(listSourceFiles(CHAT_DIR).length > 0, 'chat/ contains no .mjs/.js sources');
  assert.deepEqual(scanPackageDir(CHAT_DIR, 'chat'), []);
});

test('chat_package_never_imports_web_ui', () => {
  assertChatPackageExists();
  const offenders = [];
  for (const rel of listSourceFiles(CHAT_DIR)) {
    const src = fs.readFileSync(path.join(CHAT_DIR, rel), 'utf8');
    const fileDir = path.posix.join('chat', path.posix.dirname(toPosix(rel)));
    for (const spec of findNonBuiltinImports(src, fileDir)) {
      if (spec.includes('ui/')) offenders.push(`${toPosix(rel)}: ${spec}`);
    }
  }
  assert.deepEqual(offenders, [], 'chat/ must not import ui/ (the web dashboard)');
});

test('chat_package_has_no_manifest_or_native_addons', () => {
  assertChatPackageExists();
  const offenders = listAllEntries(CHAT_DIR).filter((e) =>
    e.name === 'package.json'
    || (e.isDir && e.name === 'node_modules')
    || e.name.endsWith('.node')
    || e.name === 'binding.gyp',
  );
  assert.deepEqual(offenders.map((e) => `chat/${e.rel}`), []);
});

test('gate_goes_red_when_third_party_import_is_planted_in_a_copy', () => {
  assertChatPackageExists();
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'chat-zero-dep-'));
  try {
    const copy = path.join(tmp, 'chat');
    fs.cpSync(CHAT_DIR, copy, { recursive: true });
    const target = path.join(copy, 'session.mjs');
    assert.ok(fs.existsSync(target), 'chat/session.mjs missing, nothing to plant into');
    fs.appendFileSync(target, "\nimport yaml from 'yaml';\n");
    assert.deepEqual(scanPackageDir(copy, 'chat'), ['session.mjs: yaml']);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('gate_goes_red_when_web_ui_import_is_planted_in_a_copy', () => {
  assertChatPackageExists();
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'chat-zero-dep-ui-'));
  try {
    const copy = path.join(tmp, 'chat');
    fs.cpSync(CHAT_DIR, copy, { recursive: true });
    const target = path.join(copy, 'render.mjs');
    fs.appendFileSync(target, "\nimport { server } from '../ui/server.mjs';\n");
    assert.deepEqual(scanPackageDir(copy, 'chat'), ['render.mjs: ../ui/server.mjs']);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
