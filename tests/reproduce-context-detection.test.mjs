// Behavioral tests for detect-context.js — repo vs installed mode detection
// Tests the actual detectContext() function with constructed layouts in temp directories
//
// Contract under test:
//  - Aesop repo layout (has .git, tests/, correct package.json) → "repo"
//  - Scaffolded project (different package.json name) → "installed"
//  - Parent git + extracted tarball → "installed" (no false positives)
//  - No test scripts in package.json → "installed"
//
// Run: node --test tests/reproduce-context-detection.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const REPO_ROOT = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..'
);

// Import detectContext using CommonJS require wrapper
const require_context = createRequire(import.meta.url);
const { detectContext } = require_context(path.join(REPO_ROOT, 'tools', 'detect-context.js'));

function createTempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-detect-test-'));
}

function cleanup(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch (e) {
    // Ignore cleanup errors
  }
}

// === Behavioral Tests ===

test('REPO MODE: Aesop checkout with all required conditions', () => {
  const tempDir = createTempDir();
  try {
    // Create a proper repo structure
    fs.mkdirSync(path.join(tempDir, '.git'), { recursive: true });
    fs.mkdirSync(path.join(tempDir, 'tests'), { recursive: true });

    // Create proper aesop package.json with test scripts
    const pkg = {
      name: '@matt82198/aesop',
      version: '0.1.0',
      scripts: {
        'test:node': 'node --test tests/*.test.mjs',
        'test:py': 'python -m unittest discover -s tests'
      }
    };
    fs.writeFileSync(
      path.join(tempDir, 'package.json'),
      JSON.stringify(pkg, null, 2)
    );

    const result = detectContext(tempDir);
    assert.strictEqual(result, 'repo', 'Should detect as repo with all conditions');
  } finally {
    cleanup(tempDir);
  }
});

test('INSTALLED MODE: Scaffolded project with different package name', () => {
  const tempDir = createTempDir();
  try {
    // Create scaffolded project structure (may have .git and tests from parent)
    fs.mkdirSync(path.join(tempDir, '.git'), { recursive: true });
    fs.mkdirSync(path.join(tempDir, 'tests'), { recursive: true });

    // Create scaffolded package.json with DIFFERENT name
    const pkg = {
      name: 'my-fleet-service',
      version: '1.0.0',
      scripts: {
        'test:node': 'node --test tests/*.test.mjs',
        'test:py': 'python -m unittest discover -s tests'
      }
    };
    fs.writeFileSync(
      path.join(tempDir, 'package.json'),
      JSON.stringify(pkg, null, 2)
    );

    const result = detectContext(tempDir);
    assert.strictEqual(result, 'installed', 'Should detect as installed when package name is not @matt82198/aesop');
  } finally {
    cleanup(tempDir);
  }
});

test('INSTALLED MODE: Missing .git directory', () => {
  const tempDir = createTempDir();
  try {
    fs.mkdirSync(path.join(tempDir, 'tests'), { recursive: true });

    const pkg = {
      name: '@matt82198/aesop',
      version: '0.1.0',
      scripts: {
        'test:node': 'node --test tests/*.test.mjs',
        'test:py': 'python -m unittest discover -s tests'
      }
    };
    fs.writeFileSync(
      path.join(tempDir, 'package.json'),
      JSON.stringify(pkg, null, 2)
    );

    const result = detectContext(tempDir);
    assert.strictEqual(result, 'installed', 'Should be installed without .git');
  } finally {
    cleanup(tempDir);
  }
});

test('INSTALLED MODE: Missing tests/ directory', () => {
  const tempDir = createTempDir();
  try {
    fs.mkdirSync(path.join(tempDir, '.git'), { recursive: true });

    const pkg = {
      name: '@matt82198/aesop',
      version: '0.1.0',
      scripts: {
        'test:node': 'node --test tests/*.test.mjs',
        'test:py': 'python -m unittest discover -s tests'
      }
    };
    fs.writeFileSync(
      path.join(tempDir, 'package.json'),
      JSON.stringify(pkg, null, 2)
    );

    const result = detectContext(tempDir);
    assert.strictEqual(result, 'installed', 'Should be installed without tests/ directory');
  } finally {
    cleanup(tempDir);
  }
});

test('INSTALLED MODE: Missing test scripts in package.json', () => {
  const tempDir = createTempDir();
  try {
    fs.mkdirSync(path.join(tempDir, '.git'), { recursive: true });
    fs.mkdirSync(path.join(tempDir, 'tests'), { recursive: true });

    // Package has correct name but missing test:node or test:py scripts
    const pkg = {
      name: '@matt82198/aesop',
      version: '0.1.0',
      scripts: {
        'build': 'npm run compile'
        // Missing test:node and test:py
      }
    };
    fs.writeFileSync(
      path.join(tempDir, 'package.json'),
      JSON.stringify(pkg, null, 2)
    );

    const result = detectContext(tempDir);
    assert.strictEqual(result, 'installed', 'Should be installed without test scripts');
  } finally {
    cleanup(tempDir);
  }
});

test('INSTALLED MODE: Missing package.json', () => {
  const tempDir = createTempDir();
  try {
    fs.mkdirSync(path.join(tempDir, '.git'), { recursive: true });
    fs.mkdirSync(path.join(tempDir, 'tests'), { recursive: true });
    // No package.json

    const result = detectContext(tempDir);
    assert.strictEqual(result, 'installed', 'Should be installed without package.json');
  } finally {
    cleanup(tempDir);
  }
});

test('INSTALLED MODE: Malformed package.json', () => {
  const tempDir = createTempDir();
  try {
    fs.mkdirSync(path.join(tempDir, '.git'), { recursive: true });
    fs.mkdirSync(path.join(tempDir, 'tests'), { recursive: true });

    // Write invalid JSON
    fs.writeFileSync(
      path.join(tempDir, 'package.json'),
      '{ invalid json...'
    );

    const result = detectContext(tempDir);
    assert.strictEqual(result, 'installed', 'Should handle malformed package.json gracefully');
  } finally {
    cleanup(tempDir);
  }
});

test('FALSE POSITIVE GUARD: Extracted tarball under parent repo', () => {
  // Scenario: aesop tarball extracted in a directory that has:
  //  - A parent git repo (.git exists at parent level)
  //  - But the extracted tarball has its own different package.json name
  // This should detect as "installed", not falsely as "repo"

  const tempDir = createTempDir();
  try {
    // Create parent repo structure (the "host" repo)
    const parentGit = path.join(tempDir, '.git');
    fs.mkdirSync(parentGit, { recursive: true });

    // Create subdirectory for extracted aesop
    const extractedDir = path.join(tempDir, 'aesop-extracted');
    fs.mkdirSync(extractedDir, { recursive: true });

    // Extracted tarball: has its own .git (worktree) and tests/
    fs.mkdirSync(path.join(extractedDir, '.git'), { recursive: true });
    fs.mkdirSync(path.join(extractedDir, 'tests'), { recursive: true });

    // Extracted tarball: but different package name (scaffolded)
    const pkg = {
      name: 'my-scaffolded-fleet',
      version: '1.0.0',
      scripts: {
        'test:node': 'node --test tests/*.test.mjs',
        'test:py': 'python -m unittest discover -s tests'
      }
    };
    fs.writeFileSync(
      path.join(extractedDir, 'package.json'),
      JSON.stringify(pkg, null, 2)
    );

    // The test: detectContext should check the extracted dir's package name first
    // and NOT mistake it for the aesop repo just because parent has .git
    const result = detectContext(extractedDir);
    assert.strictEqual(
      result,
      'installed',
      'Should detect as installed (scaffolded) even with parent .git, because package name is wrong'
    );
  } finally {
    cleanup(tempDir);
  }
});

test('POSITIVE IDENTIFICATION: Requires ALL conditions', () => {
  // Only .git + tests/ + correct name but NO test scripts -> should still be installed
  const tempDir = createTempDir();
  try {
    fs.mkdirSync(path.join(tempDir, '.git'), { recursive: true });
    fs.mkdirSync(path.join(tempDir, 'tests'), { recursive: true });

    const pkg = {
      name: '@matt82198/aesop',
      version: '0.1.0'
      // Missing scripts
    };
    fs.writeFileSync(
      path.join(tempDir, 'package.json'),
      JSON.stringify(pkg, null, 2)
    );

    const result = detectContext(tempDir);
    assert.strictEqual(result, 'installed', 'Should require ALL conditions for repo detection (missing test scripts)');
  } finally {
    cleanup(tempDir);
  }
});

test('REAL REPO: Actual aesop repo detection', () => {
  // Test against the actual REPO_ROOT
  const result = detectContext(REPO_ROOT);
  assert.strictEqual(result, 'repo', 'Should detect actual aesop repo as repo mode');
});

test('CLI USAGE: detect-context can be run from command line', () => {
  // Test that detect-context.js works as a CLI tool
  // by checking it has both require.main === module and module.exports

  const detectContextSource = fs.readFileSync(
    path.join(REPO_ROOT, 'tools', 'detect-context.js'),
    'utf8'
  );

  assert.ok(
    detectContextSource.includes('require.main === module'),
    'detect-context should support CLI usage'
  );

  assert.ok(
    detectContextSource.includes('module.exports'),
    'detect-context should export detectContext function'
  );
});
