// Shared git command execution utilities with proper isolation
// CRITICAL: uses cwd option instead of shell cd to prevent config leakage into parent repos

import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';

/**
 * Execute a git command in a specific directory with proper error handling.
 * Uses Node's cwd option instead of shell cd to prevent accidental repo poisoning.
 * NEVER uses stdio: 'ignore' to catch path-related failures.
 *
 * @param {string} cwd - The working directory for git execution
 * @param {string|string[]} args - Git args: 'init', 'config user.email test@example.com', or ['config', 'user.email', 'test@example.com']
 * @returns {object} spawnSync result object
 * @throws {Error} if git command fails
 */
export function gitCmd(cwd, args) {
  const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000;

  // Parse args: either string 'init' or 'config user.email test@ex' or array ['config', 'user.email', 'test@ex']
  // For strings, simple split works for 'init', 'config key value' (as long as value has no spaces)
  const gitArgs = typeof args === 'string' ? args.split(/\s+/) : args;

  const result = spawnSync('git', gitArgs, {
    cwd,
    encoding: 'utf8',
    timeout,
    killSignal: 'SIGKILL'
    // Deliberately NOT stdio: 'ignore' — we need to see failures
  });

  // Fail if git command failed
  if (result.status !== 0) {
    throw new Error(
      `git ${gitArgs.join(' ')} failed in ${cwd}\n` +
      `Exit code: ${result.status}\n` +
      `stderr: ${result.stderr}\n` +
      `stdout: ${result.stdout}`
    );
  }

  return result;
}

/**
 * Verify that a git repository's config in a specific directory has NOT been poisoned
 * by "Test User <test@example.com>" from test fixtures.
 *
 * @param {string} repoPath - Path to the .git directory or root of a git repo
 * @throws {Error} if config contains Test User identity
 */
export function assertConfigNotPoisoned(repoPath) {
  const result = spawnSync('git', ['config', '--get-all', 'user.name'], {
    cwd: repoPath,
    encoding: 'utf8'
  });

  const userName = result.stdout.trim();
  assert.notStrictEqual(
    userName,
    'Test User',
    `Git config in ${repoPath} was poisoned with "Test User" identity`
  );
}
