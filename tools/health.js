#!/usr/bin/env node

/**
 * Aesop health — Fleet health aggregator
 *
 * Aggregates fleet health signals:
 * - Heartbeat ages (watchdog, monitor)
 * - Tracker open-item counts by lane
 * - Security-alert count and severity
 * - Orchestrator status age and phase
 *
 * Output: One line "HEALTH: 🟢|🟡|🔴 <reason>" + compact bullet list of non-green contributors.
 *
 * Exit code: 0 if all OK, 1 if any STALE/MISSING/HIGH alerts
 * Flags: --json for machine-readable output
 */

const { spawnSync } = require('child_process');
const path = require('path');
const pythonScript = path.join(__dirname, 'healthcheck.py');

// Resolve Python interpreter portably (prefer python3, fallback to python)
function resolvePythonInterpreter() {
  if (spawnSync('python3', ['--version'], { stdio: 'ignore' }).error === undefined) {
    return 'python3';
  }
  if (spawnSync('python', ['--version'], { stdio: 'ignore' }).error === undefined) {
    return 'python';
  }
  return null;
}

// Get arguments: process.argv includes [node, script, health, --json, ...]
let args = process.argv.slice(2);

// If first arg is 'health' (command name), remove it
if (args[0] === 'health') {
  args = args.slice(1);
}

const pythonInterpreter = resolvePythonInterpreter();
if (!pythonInterpreter) {
  console.error('Error running health: Python interpreter not found (tried python3, python)');
  process.exit(1);
}

const result = spawnSync(pythonInterpreter, [pythonScript, ...args], {
  stdio: 'inherit',
  timeout: 30000
});

if (result.error) {
  console.error(`Error running health: ${result.error.message}`);
  process.exit(1);
}

process.exitCode = result.status != null ? result.status : 1;
