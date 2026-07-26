// Shared scaffold fixture for reducing child process load during concurrent testing
// Contract:
//  - Creates a single scaffolded fleet per test file (reused across all tests)
//  - Returns paths to generated files (CLAUDE.md, aesop.config.json, etc.)
//  - Reduces spawn count from 55+ child processes to ~4
//
// Usage:
//   import { scaffoldOnce } from './helpers/scaffold-fixture.mjs';
//
//   let scaffoldedDir, scaffoldedConfig, scaffoldedClaude;
//
//   before(async () => {
//     const result = scaffoldOnce('test-fleet', { name: 'test-service' });
//     scaffoldedDir = result.targetDir;
//     scaffoldedConfig = result.configPath;
//     scaffoldedClaude = result.claudePath;
//   });
//
//   test('assertion 1', () => {
//     const config = JSON.parse(fs.readFileSync(scaffoldedConfig, 'utf8'));
//     // assert on config
//   });
//
//   test('assertion 2', () => {
//     const claude = fs.readFileSync(scaffoldedClaude, 'utf8');
//     // assert on claude
//   });

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';
import { gitCmd } from './git-helpers.mjs';

const CLI = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', '..', 'bin', 'cli.js'
);

// Per-test-file fixture cache: key = dirName, value = result
const fixtureCache = new Map();

/**
 * Compute a content manifest for key scaffold files.
 * Returns a map of relPath -> { size, mtime, hash } for files < 64KB.
 * Used to detect mutations of the shared fixture.
 *
 * @param {string} targetDir - The scaffold target directory
 * @returns {object} Map of relPath -> { size, mtime, hash }
 */
function computeFixtureManifest(targetDir) {
  const manifest = {};
  const keyFiles = ['aesop.config.json', 'CLAUDE.md'];

  for (const filename of keyFiles) {
    const filePath = path.join(targetDir, filename);
    if (fs.existsSync(filePath)) {
      const stat = fs.statSync(filePath);
      const content = fs.readFileSync(filePath);

      if (stat.size < 64 * 1024) {
        const hash = crypto.createHash('sha256').update(content).digest('hex');
        manifest[filename] = {
          size: stat.size,
          mtime: stat.mtime.getTime(),
          hash
        };
      }
    }
  }

  return manifest;
}

/**
 * Run scaffold once per test file, cache results, return paths.
 * Safe for concurrent test suites (each suite gets its own dir).
 *
 * @param {string} dirName - Directory name for this fixture (e.g. 'wizard-fixture', 'onboard-fixture')
 * @param {object} opts - Scaffold options
 *   - mode: 'wizard' (interactive wizard mode) or 'scaffold' (headless scaffold mode, default)
 *   - name, domains, repos, yes, force: arguments based on mode
 * @returns {object} { targetDir, configPath, claudePath, statePath, result }
 */
export function scaffoldOnce(dirName, opts = {}) {
  // Return cached fixture if already scaffolded
  if (fixtureCache.has(dirName)) {
    return fixtureCache.get(dirName);
  }

  const mode = opts.mode || 'scaffold';
  const tempBase = fs.mkdtempSync(path.join(os.tmpdir(), `aesop-fixture-${dirName}-`));
  const targetDir = path.join(tempBase, 'aesop-fleet');

  // Initialize git repo in tempBase (for wizard mode) or targetDir (for scaffold mode)
  const gitRepoDir = mode === 'wizard' ? tempBase : targetDir;
  fs.mkdirSync(gitRepoDir, { recursive: true });

  // Initialize git repo in the fixture directory
  // Use explicit cwd instead of shell cd to prevent accidental config leakage
  spawnSync('git', ['init'], {
    cwd: gitRepoDir,
    encoding: 'utf8',
    timeout: 30000,
    killSignal: 'SIGKILL'
  });

  // Configure test identity in the fixture repo (NOT global or in parent repos)
  // Using gitCmd which enforces cwd isolation and error checking
  gitCmd(gitRepoDir, ['config', 'user.email', 'test@example.com']);
  gitCmd(gitRepoDir, ['config', 'user.name', 'Test User']);

  // Build args based on mode
  const args = [];
  if (mode === 'wizard') {
    // Wizard mode: use 'wizard' subcommand with optional flags
    args.push('wizard');
    if (opts.yes) args.push('--yes');
    if (opts.force) args.push('--force');
  } else {
    // Scaffold mode: use targetDir as positional, followed by flags
    args.push(targetDir);
    if (opts.name) args.push('--name', opts.name);
    if (opts.domains) args.push('--domains', opts.domains);
    if (opts.repos) args.push('--repos', opts.repos);
    if (opts.yes) args.push('--yes');
    if (opts.force) args.push('--force');
  }

  // Run scaffold (use tunable timeout)
  const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 120000;
  const cwd = mode === 'wizard' ? tempBase : path.dirname(targetDir);
  const result = spawnSync(process.execPath, [CLI, ...args], {
    encoding: 'utf8',
    cwd,
    timeout,
    killSignal: 'SIGKILL'
  });

  if (result.status !== 0) {
    throw new Error(`Scaffold failed (${mode} mode): exit ${result.status}\nstderr: ${result.stderr}\nstdout: ${result.stdout}`);
  }

  // Cache the result
  const fixture = {
    tempBase,
    targetDir,
    configPath: path.join(targetDir, 'aesop.config.json'),
    claudePath: path.join(targetDir, 'CLAUDE.md'),
    statePath: path.join(targetDir, 'state'),
    hookPath: path.join(targetDir, '.git', 'hooks', 'pre-push'),
    result,
    // Compute manifest for mutation detection
    manifest: computeFixtureManifest(targetDir)
  };

  fixtureCache.set(dirName, fixture);
  return fixture;
}

/**
 * Get the shared fixture directory for a test file.
 * Call only after scaffoldOnce has been called.
 */
export function getFixture(dirName) {
  const fixture = fixtureCache.get(dirName);
  if (!fixture) {
    throw new Error(`Fixture "${dirName}" not scaffolded yet. Call scaffoldOnce first.`);
  }
  return fixture;
}

/**
 * Assert that a fixture has not been mutated since creation.
 * Throws descriptively if any key files were changed.
 * Call this before cleanupFixtures() to catch test mutations.
 *
 * @param {object} fixture - The fixture object returned from scaffoldOnce
 * @throws {Error} If fixture files were modified
 */
export function assertFixturePristine(fixture) {
  if (!fixture.manifest) {
    return; // No manifest recorded, skip check
  }

  const changedFiles = [];
  const currentManifest = computeFixtureManifest(fixture.targetDir);

  // Check for changed or deleted files
  for (const [relPath, originalEntry] of Object.entries(fixture.manifest)) {
    const currentEntry = currentManifest[relPath];

    if (!currentEntry) {
      changedFiles.push(`${relPath}: DELETED`);
    } else if (currentEntry.hash !== originalEntry.hash) {
      changedFiles.push(`${relPath}: MODIFIED (size ${originalEntry.size} -> ${currentEntry.size})`);
    }
  }

  // Check for newly added files
  for (const relPath of Object.keys(currentManifest)) {
    if (!fixture.manifest[relPath]) {
      changedFiles.push(`${relPath}: ADDED`);
    }
  }

  if (changedFiles.length > 0) {
    throw new Error(`Shared fixture was mutated:\n  ${changedFiles.join('\n  ')}`);
  }
}

/**
 * Clean up all fixtures (call from after() hook if needed).
 */
export function cleanupFixtures() {
  for (const [dirName, fixture] of fixtureCache.entries()) {
    try {
      if (fixture.tempBase && fs.existsSync(fixture.tempBase)) {
        fs.rmSync(fixture.tempBase, { recursive: true, force: true });
      }
    } catch (e) {
      console.warn(`Failed to cleanup fixture ${dirName}:`, e.message);
    }
  }
  fixtureCache.clear();
}
