#!/usr/bin/env node
// INDEX: One-shot fleet status snapshot (watchdog/monitor heartbeat age, dashboard port reachability, git branch and working tree state)

/**
 * Aesop status — One-shot fleet status snapshot
 *
 * Prints:
 *  - Watchdog heartbeat age (seconds) + STALE if >300s
 *  - Monitor heartbeat age (seconds) + STALE if >300s
 *  - Dashboard port reachability (default 8770)
 *  - Git branch and working tree state (clean/dirty)
 *
 * Exits immediately after printing status.
 */

const fs = require('fs');
const path = require('path');
const net = require('net');
const os = require('os');
const { execSync } = require('child_process');

const CURRENT_DIR = process.cwd();
const HEARTBEAT_STALE_THRESHOLD = 300; // seconds

/**
 * Check if a conductor root is live (contains the watchdog heartbeat file).
 * @param {string} candidatePath - Path to test
 * @returns {boolean} True if the path contains state/.watchdog-heartbeat
 */
function isConductorRootLive(candidatePath) {
  if (!candidatePath) return false;
  const heartbeatPath = path.join(candidatePath, 'state', '.watchdog-heartbeat');
  return fs.existsSync(heartbeatPath);
}

/**
 * Resolve conductor root with the same logic as daemons/conductor-root.sh.
 *
 * Precedence (mirrors shell script):
 * 1. Explicit CONDUCTOR_ROOT environment variable
 * 2. AESOP_CONDUCTOR3_ROOT for backward compatibility (no liveness check)
 * 3. Default sibling conductor3 (only when repo root basename is exactly "aesop")
 * 4. Default canonical $HOME/conductor3
 *
 * Each candidate from steps 3-4 is accepted only if it contains state/.watchdog-heartbeat.
 *
 * @param {string} aesopRoot - The aesop repository root directory
 * @returns {string|null} The resolved conductor root path, or null if none found
 */
function resolveConductorRoot(aesopRoot) {
  // 1. Explicit CONDUCTOR_ROOT env var always wins
  if (process.env.CONDUCTOR_ROOT) {
    return process.env.CONDUCTOR_ROOT;
  }

  // 2. AESOP_CONDUCTOR3_ROOT for backward compatibility (return if set, regardless of liveness)
  if (process.env.AESOP_CONDUCTOR3_ROOT) {
    return process.env.AESOP_CONDUCTOR3_ROOT;
  }

  // 3. Default sibling conductor3 (only when repo basename is exactly "aesop")
  if (aesopRoot && path.basename(aesopRoot) === 'aesop') {
    const siblingCandidate = path.join(path.dirname(aesopRoot), 'conductor3'); // default sibling (canonical tree only)
    if (isConductorRootLive(siblingCandidate)) {
      return siblingCandidate;
    }
  }

  // 4. Default canonical conductor3 on this box
  const homeDir = os.homedir();
  const homeCandidate = path.join(homeDir, 'conductor3'); // default canonical conductor3 on this box
  if (isConductorRootLive(homeCandidate)) {
    return homeCandidate;
  }

  return null;
}

/**
 * Resolve heartbeat path with fallback chain.
 *
 * Precedence:
 * 1. Environment variable (explicit path override)
 * 2. External orchestration root location (shared state)
 * 3. Local fallback (aesop state directory)
 *
 * @param {string} envVar - Environment variable name to check
 * @param {string} orchestrationPath - Path in external orchestration root
 * @param {string} fallbackPath - Local fallback path
 * @returns {string} Resolved heartbeat path
 */
function resolveHeartbeatPath(envVar, orchestrationPath, fallbackPath) {
  // 1. Environment variable takes highest priority
  const envValue = process.env[envVar];
  if (envValue) {
    const expandedPath = envValue.replace('~', os.homedir());
    if (fs.existsSync(expandedPath)) {
      return expandedPath;
    }
  }

  // 2. Try external orchestration location (preferred for multi-instance state)
  if (orchestrationPath && fs.existsSync(orchestrationPath)) {
    return orchestrationPath;
  }

  // 3. Try fallback location (local aesop state)
  if (fallbackPath && fs.existsSync(fallbackPath)) {
    return fallbackPath;
  }

  // Return preferred location even if it doesn't exist (will report as MISSING)
  return orchestrationPath || fallbackPath;
}

// ANSI color helpers
const COLORS = {
  GREEN: '\x1b[32m',
  RED: '\x1b[31m',
  YELLOW: '\x1b[33m',
  RESET: '\x1b[0m',
  BOLD: '\x1b[1m'
};

function readHeartbeat(filePath) {
  try {
    if (!fs.existsSync(filePath)) {
      return null;
    }
    const content = fs.readFileSync(filePath, 'utf8').trim();
    const timestamp = parseInt(content, 10);
    if (isNaN(timestamp)) {
      return null;
    }
    return timestamp;
  } catch (e) {
    return null;
  }
}

function getHeartbeatStatus(timestamp) {
  if (timestamp === null) {
    return { age: null, status: 'MISSING', color: COLORS.RED };
  }
  const now = Math.floor(Date.now() / 1000);
  const age = now - timestamp;
  const isStale = age > HEARTBEAT_STALE_THRESHOLD;
  const status = isStale ? 'STALE' : 'OK';
  const color = isStale ? COLORS.YELLOW : COLORS.GREEN;
  return { age, status, color };
}

function checkPortReachable(port) {
  return new Promise((resolve) => {
    let resolved = false;
    const sock = net.createConnection(
      { port, host: '127.0.0.1', timeout: 500 }
    );

    const cleanup = () => {
      try {
        sock.destroy();
      } catch (e) {
        // Ignore cleanup errors
      }
    };

    sock.on('connect', () => {
      if (!resolved) {
        resolved = true;
        cleanup();
        resolve({ reachable: true, color: COLORS.GREEN, status: 'OK' });
      }
    });

    sock.on('error', () => {
      if (!resolved) {
        resolved = true;
        cleanup();
        resolve({ reachable: false, color: COLORS.RED, status: 'NOT REACHABLE' });
      }
    });

    sock.on('timeout', () => {
      if (!resolved) {
        resolved = true;
        cleanup();
        resolve({ reachable: false, color: COLORS.YELLOW, status: 'TIMEOUT' });
      }
    });

    // Fallback timeout
    setTimeout(() => {
      if (!resolved) {
        resolved = true;
        cleanup();
        resolve({ reachable: false, color: COLORS.YELLOW, status: 'TIMEOUT' });
      }
    }, 2000);
  });
}

function getGitStatus() {
  try {
    // Get current branch
    const branch = execSync('git rev-parse --abbrev-ref HEAD', {
      cwd: CURRENT_DIR,
      encoding: 'utf8',
      stdio: 'pipe'
    }).trim();

    // Check if working tree is clean
    const status = execSync('git status --porcelain', {
      cwd: CURRENT_DIR,
      encoding: 'utf8',
      stdio: 'pipe'
    });

    const isClean = status.length === 0;
    const state = isClean ? 'clean' : 'dirty';
    const color = isClean ? COLORS.GREEN : COLORS.YELLOW;

    return { branch, state, color };
  } catch (e) {
    return { branch: 'unknown', state: 'unknown', color: COLORS.RED };
  }
}

function formatStatusLine(label, value, color = COLORS.RESET) {
  const paddedLabel = label.padEnd(30);
  return `  ${paddedLabel} ${color}${value}${COLORS.RESET}`;
}

(async function main() {
  try {
    console.log(`\n${COLORS.BOLD}Aesop Fleet Status${COLORS.RESET}\n`);

    // Resolve orchestration root using the same logic as daemons/conductor-root.sh
    const orchestrationRoot = resolveConductorRoot(CURRENT_DIR);

    // Watchdog heartbeat: prefer external orchestration state, fallback to local aesop/state
    const watchdogOrchestrationPath = orchestrationRoot ? path.join(orchestrationRoot, 'state', '.watchdog-heartbeat') : null;
    const watchdogLocalPath = path.join(CURRENT_DIR, 'state', '.watchdog-heartbeat');
    const watchdogPath = resolveHeartbeatPath('AESOP_WATCHDOG_HEARTBEAT', watchdogOrchestrationPath, watchdogLocalPath);

    const watchdogTimestamp = readHeartbeat(watchdogPath);
    const watchdogStatus = getHeartbeatStatus(watchdogTimestamp);
    const watchdogLine = watchdogStatus.age !== null
      ? `${watchdogStatus.age}s (${watchdogStatus.status})`
      : watchdogStatus.status;
    console.log(
      formatStatusLine('Watchdog heartbeat', watchdogLine, watchdogStatus.color)
    );

    // Monitor heartbeat: prefer external orchestration root, fallback to local aesop/state/monitor
    const monitorOrchestrationPath = orchestrationRoot ? path.join(orchestrationRoot, 'monitor', '.monitor-heartbeat') : null;
    const monitorLocalPath = path.join(CURRENT_DIR, 'state', 'monitor', '.monitor-heartbeat');
    const monitorPath = resolveHeartbeatPath('AESOP_MONITOR_HEARTBEAT', monitorOrchestrationPath, monitorLocalPath);

    const monitorTimestamp = readHeartbeat(monitorPath);
    const monitorStatus = getHeartbeatStatus(monitorTimestamp);
    const monitorLine = monitorStatus.age !== null
      ? `${monitorStatus.age}s (${monitorStatus.status})`
      : monitorStatus.status;
    console.log(
      formatStatusLine('Monitor heartbeat', monitorLine, monitorStatus.color)
    );

    // Dashboard port reachability
    const portResult = await checkPortReachable(8770);
    const dashboardLine = `localhost:8770 (${portResult.status})`;
    console.log(
      formatStatusLine('Dashboard port', dashboardLine, portResult.color)
    );

    // Git status
    const gitStatus = getGitStatus();
    const gitBranchLine = `${gitStatus.branch} (${gitStatus.state})`;
    console.log(
      formatStatusLine('Git branch', gitBranchLine, gitStatus.color)
    );

    console.log(`\n`);
    process.exitCode = 0;
  } catch (err) {
    console.error(`Error gathering status: ${err.message}`);
    process.exitCode = 1;
  }
})();
