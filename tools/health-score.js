#!/usr/bin/env node
// INDEX: Readiness score for primed projects (0-100 weighted score: config, git hooks, CLAUDE.md, state writable, daemon heartbeats, git identity, secret-scan runnable)

/**
 * Aesop health-score — readiness score for primed projects
 *
 * Calculates a weighted 0-100 score based on:
 * - Config validity (15 points)
 * - Git pre-push hook installed (15 points)
 * - CLAUDE.md present (15 points)
 * - State directory writable (10 points)
 * - Daemon heartbeats fresh (15 points)
 * - Git identity configured (15 points)
 * - Secret-scan runnable (15 points)
 *
 * Exit code 0 = success (always produces a score).
 */

const { spawnSync } = require('child_process');
const path = require('path');
const pythonScript = path.join(__dirname, 'health_score.py');

// Get arguments: process.argv includes [node, script, health-score, --cwd, .]
let args = process.argv.slice(2);

// If first arg is 'health-score' (command name), remove it
if (args[0] === 'health-score') {
  args = args.slice(1);
}

const result = spawnSync('python3', [pythonScript, ...args], {
  stdio: 'inherit',
  timeout: 30000
});

if (result.error) {
  console.error(`Error running health-score: ${result.error.message}`);
  process.exit(1);
}

process.exitCode = result.status || 0;
