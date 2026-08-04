// Domain-map drift detection test.
// Ensures that every top-level directory containing code has an entry in
// the domain map section of CLAUDE.md — a tripwire against orphaning
// documentation when new directories are created.
//
// Run: node --test tests/domain-map-drift.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, '..');

// Directories to exclude from domain-map check (non-code or generated)
const EXCLUDED_DIRS = new Set([
  'node_modules',    // npm dependencies
  '.git',            // git internals
  '.github',         // CI config (meta, not code)
  'assets',          // static files/media
  'state',           // runtime state (documented in CLAUDE.md as git-ignored)
  '.claude',         // Claude Code config (meta)
]);

// Helper: check if a directory contains code
function containsCode(dirPath) {
  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue; // skip hidden files
      
      // Recognize code by extension or name
      const isCodeFile = /\.(js|mjs|cjs|py|sh|json|yml|yaml|md)$/.test(entry.name) ||
                         entry.name === 'Makefile' ||
                         entry.name === 'package.json' ||
                         entry.name === 'README.md' ||
                         entry.name === 'CLAUDE.md';
      
      if (entry.isFile() && isCodeFile) {
        return true;
      }
      
      // Recurse into subdirs (shallow check)
      if (entry.isDirectory() && !EXCLUDED_DIRS.has(entry.name)) {
        if (containsCode(path.join(dirPath, entry.name))) {
          return true;
        }
      }
    }
  } catch (err) {
    // Ignore read errors (permission, etc.)
  }
  
  return false;
}

test('domain-map drift: all code directories have domain-map entries', () => {
  const claudeMdPath = path.join(PROJECT_ROOT, 'CLAUDE.md');
  assert.ok(fs.existsSync(claudeMdPath), `CLAUDE.md must exist at ${claudeMdPath}`);
  
  const claudeMdContent = fs.readFileSync(claudeMdPath, 'utf8');
  
  // Extract domain-map entries: look for lines matching `- **<dir>/**`
  const domainMapRegex = /^- \*\*([a-zA-Z0-9_-]+)\//gm;
  const documentedDirs = new Set();
  
  let match;
  while ((match = domainMapRegex.exec(claudeMdContent)) !== null) {
    documentedDirs.add(match[1]);
  }
  
  // Find all top-level directories that are TRACKED IN GIT.
  // Reading the filesystem directly made this gate fail on transient test output:
  // scaffolder tests create ./aesop-fleet/ in the repo root, and the gate then demanded
  // documentation for a directory that is not part of the source tree. A documentation
  // drift gate must reason about tracked source, not whatever happens to be on disk.
  const trackedTop = new Set(
    execFileSync('git', ['ls-files'], { cwd: PROJECT_ROOT, encoding: 'utf-8' })
      .split(String.fromCharCode(10))
      .filter(Boolean)
      .map((f) => f.split('/')[0])
  );
  const entries = fs
    .readdirSync(PROJECT_ROOT, { withFileTypes: true })
    .filter((e) => trackedTop.has(e.name));
  
  const failures = [];
  for (const entry of entries) {
    // Skip hidden dirs and known exclusions
    if (entry.name.startsWith('.') || EXCLUDED_DIRS.has(entry.name)) {
      continue;
    }
    
    if (!entry.isDirectory()) {
      continue;
    }
    
    // Check if this directory contains code
    const fullPath = path.join(PROJECT_ROOT, entry.name);
    if (!containsCode(fullPath)) {
      continue; // Skip empty or non-code directories
    }
    
    // Verify it's documented in domain map
    if (!documentedDirs.has(entry.name)) {
      failures.push(`  ${entry.name}/ (contains code but missing from domain map)`);
    }
  }
  
  if (failures.length > 0) {
    throw new Error(
      `Code directories missing from CLAUDE.md domain map:\n${failures.join('\n')}\n` +
      `These directories contain code but lack documentation. ` +
      `Add them to the "## Domain map" section in CLAUDE.md with a brief description ` +
      `(format: "- **<dir>/** — Description — see § <dir>/ below"), ` +
      `then add a "## <dir>/" contract section below documenting the directory's purpose and invariants.`
    );
  }
});

test('tools index drift: all tools/*.{py,mjs,sh} files documented in tools/INDEX.md', () => {
  // The per-tool one-liner index was extracted out of tools/CLAUDE.md into the
  // generated tools/INDEX.md (A2 of the merge-pipeline debottleneck) to kill the
  // merge-queue conflict surface. tools/gen_tool_index.py fails closed on any tool
  // missing its INDEX: line, and claudemd_lint.py enforces byte-identity; this test
  // is the Node-side tripwire that every tool still appears in the generated index.
  const indexPath = path.join(PROJECT_ROOT, 'tools', 'INDEX.md');
  assert.ok(fs.existsSync(indexPath), `tools/INDEX.md must exist at ${indexPath} (run: python tools/gen_tool_index.py --regenerate)`);

  const indexContent = fs.readFileSync(indexPath, 'utf8');

  // Extract backtick-quoted filenames: `filename.ext`
  const filesRegex = /`([a-zA-Z0-9_-]+\.(py|mjs|sh))`/g;
  const documentedFiles = new Set();

  let match;
  while ((match = filesRegex.exec(indexContent)) !== null) {
    documentedFiles.add(match[1]);
  }

  // Find all .py, .mjs, .sh files in tools/
  const toolsDir = path.join(PROJECT_ROOT, 'tools');
  const entries = fs.readdirSync(toolsDir, { withFileTypes: true });

  const failures = [];
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    if (!/\.(py|mjs|sh)$/.test(entry.name)) continue;
    if (!documentedFiles.has(entry.name)) {
      failures.push(`  tools/${entry.name}`);
    }
  }

  if (failures.length > 0) {
    throw new Error(
      `tools/*.{py,mjs,sh} files missing from tools/INDEX.md:\n${failures.join('\n')}\n` +
      `Add an "INDEX: <one-line purpose>" line to each file's module docstring/header, ` +
      `then run: python tools/gen_tool_index.py --regenerate`
    );
  }
});
