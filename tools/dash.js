#!/usr/bin/env node
// INDEX: Launch the web dashboard (spawns python ui/serve.py with configured port from PORT env var, aesop.config.json, or default 8770)

/**
 * Aesop dash — Launch the web dashboard
 *
 * Spawns python ui/serve.py with the configured port.
 * Honors PORT environment variable, aesop.config.json dashboard.port, or defaults to 8770.
 * Prints the dashboard URL before launching.
 */

const fs = require('fs');
const path = require('path');
const { spawn, spawnSync } = require('child_process');

const CURRENT_DIR = process.cwd();
const PACKAGE_DIR = path.resolve(__dirname, '..');

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

function loadConfig() {
  const configPath = path.join(CURRENT_DIR, 'aesop.config.json');
  try {
    if (!fs.existsSync(configPath)) {
      return null;
    }
    const content = fs.readFileSync(configPath, 'utf8');
    return JSON.parse(content);
  } catch (e) {
    return null;
  }
}

(async function main() {
  try {
    // The dashboard ships inside the aesop package (ui/serve.py beside tools/),
    // not inside the user's repo: init does not scaffold ui/. Resolve it from
    // the package, and point state at the user's repo (F-09).
    const dashScript = path.join(PACKAGE_DIR, 'ui', 'serve.py');
    if (!fs.existsSync(dashScript)) {
      console.error(`Error: dashboard script not found at ${dashScript}`);
      console.error('The aesop package install is incomplete; reinstall @matt82198/aesop.');
      process.exitCode = 1;
      return;
    }

    // Determine port: env var > config > default
    let port = 8770;

    if (process.env.PORT) {
      const envPort = parseInt(process.env.PORT, 10);
      if (!isNaN(envPort) && envPort > 0 && envPort < 65536) {
        port = envPort;
      }
    } else {
      const config = loadConfig();
      if (config && config.dashboard && config.dashboard.port) {
        const configPort = parseInt(config.dashboard.port, 10);
        if (!isNaN(configPort) && configPort > 0 && configPort < 65536) {
          port = configPort;
        }
      }
    }

    // Zero-key demo mode: `aesop dash --demo` serves a seeded, self-identifying
    // fleet snapshot so a fresh scaffold isn't a dead shell. Passed straight
    // through to ui/serve.py, which owns the flag.
    const demoMode = process.argv.includes('--demo');

    // State lives in the user's repo: AESOP_STATE_ROOT env > config state_root > <cwd>/state.
    const repoConfig = loadConfig();
    const stateRoot = process.env.AESOP_STATE_ROOT
      || path.resolve(CURRENT_DIR, (repoConfig && repoConfig.state_root) || 'state');

    console.log(`Launching aesop dashboard on http://localhost:${port}${demoMode ? ' (DEMO DATA)' : ''}\n`);

    // Resolve Python interpreter
    const pythonExe = resolvePythonInterpreter();
    if (!pythonExe) {
      console.error('Error: python3 or python not found on PATH');
      console.error('Please install Python 3 to run the dashboard');
      process.exitCode = 1;
      return;
    }

    // Spawn the dashboard script with PORT env var set
    const serveArgs = demoMode ? [dashScript, '--demo'] : [dashScript];
    const proc = spawn(pythonExe, serveArgs, {
      cwd: CURRENT_DIR,
      stdio: 'inherit',
      env: { ...process.env, PORT: port.toString(), AESOP_STATE_ROOT: stateRoot },
      shell: process.platform === 'win32'
    });

    // Exit with the dashboard process's exit code
    proc.on('exit', (code) => {
      process.exitCode = code || 0;
    });

    proc.on('error', (err) => {
      console.error(`Error spawning dashboard: ${err.message}`);
      process.exitCode = 1;
    });
  } catch (err) {
    console.error(`Error launching dashboard: ${err.message}`);
    process.exitCode = 1;
  }
})();
