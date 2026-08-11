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
const { execSync } = require('child_process');

const CURRENT_DIR = process.cwd();
const HEARTBEAT_STALE_THRESHOLD = 300; // seconds

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

    // Watchdog heartbeat
    const watchdogPath = path.join(CURRENT_DIR, 'state', '.watchdog-heartbeat');
    const watchdogTimestamp = readHeartbeat(watchdogPath);
    const watchdogStatus = getHeartbeatStatus(watchdogTimestamp);
    const watchdogLine = watchdogStatus.age !== null
      ? `${watchdogStatus.age}s (${watchdogStatus.status})`
      : watchdogStatus.status;
    console.log(
      formatStatusLine('Watchdog heartbeat', watchdogLine, watchdogStatus.color)
    );

    // Monitor heartbeat
    const monitorPath = path.join(CURRENT_DIR, 'state', 'monitor', '.monitor-heartbeat');
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
