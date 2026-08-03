#!/usr/bin/env node
// INDEX: Launch the watchdog daemon (spawns bash daemons/run-watchdog.sh with inherited stdio in foreground mode)

/**
 * Aesop watch — Launch the watchdog daemon
 *
 * Spawns bash daemons/run-watchdog.sh with inherited stdio (foreground mode).
 * The daemon will run continuously and inherit the parent's stdin/stdout/stderr.
 */

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const CURRENT_DIR = process.cwd();

(async function main() {
  try {
    // Verify we're in an aesop repo
    const watchdogScript = path.join(CURRENT_DIR, 'daemons', 'run-watchdog.sh');
    if (!fs.existsSync(watchdogScript)) {
      console.error(`Error: watchdog script not found at ${watchdogScript}`);
      console.error('Are you running this from the aesop root directory?');
      process.exitCode = 1;
      return;
    }

    console.log('Starting aesop watchdog daemon...\n');

    // Spawn the watchdog script in foreground (inherit stdio)
    const proc = spawn('bash', [watchdogScript], {
      cwd: CURRENT_DIR,
      stdio: 'inherit',
      shell: true
    });

    // Exit with the watchdog process's exit code
    proc.on('exit', (code) => {
      process.exitCode = code || 0;
    });

    proc.on('error', (err) => {
      console.error(`Error spawning watchdog: ${err.message}`);
      process.exitCode = 1;
    });
  } catch (err) {
    console.error(`Error launching watchdog: ${err.message}`);
    process.exitCode = 1;
  }
})();
