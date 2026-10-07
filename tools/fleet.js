#!/usr/bin/env node
// INDEX: One-shot fleet snapshot (JSON: agents, heartbeats, tracker, orchestrator status; Node STDLIB only)

/**
 * Aesop fleet — One-shot fleet snapshot
 *
 * Prints a comprehensive fleet status snapshot:
 *  - Agents (from dash-extra.mjs --json passthrough)
 *  - Heartbeat ages (watchdog + monitor, seconds, with STALE status if >threshold)
 *  - Tracker lane counts (from state/tracker.json)
 *  - Orchestrator status (from state/orchestrator-status.json)
 *
 * Graceful degradation: any missing state file produces explicit 'unavailable: <why>'
 * rather than crashing or leaving blank output. Never crashes; always exits cleanly.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawn } = require('child_process');

const CURRENT_DIR = process.cwd();
const WATCHDOG_STALE_THRESHOLD = 300; // seconds
const MONITOR_STALE_THRESHOLD = 3600; // seconds

// ============================================================================
// Utility Functions
// ============================================================================

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

function getHeartbeatStatus(timestamp, threshold) {
  if (timestamp === null) {
    return { age: null, status: 'MISSING' };
  }
  const now = Math.floor(Date.now() / 1000);
  const age = now - timestamp;
  const isStale = age > threshold;
  const status = isStale ? 'STALE' : 'OK';
  return { age, status };
}

function loadJSON(filePath) {
  try {
    if (!fs.existsSync(filePath)) {
      return null;
    }
    const content = fs.readFileSync(filePath, 'utf8').trim();
    if (!content) {
      return null;
    }
    return JSON.parse(content);
  } catch (e) {
    return null;
  }
}

/**
 * Invoke dash-extra.mjs --json to get active agents
 * Returns { agents: [...] } or { unavailable: "reason" }
 */
function getFleetAgents(aesopRoot) {
  return new Promise((resolve) => {
    const dashExtraPath = path.join(aesopRoot, 'dash', 'dash-extra.mjs');

    if (!fs.existsSync(dashExtraPath)) {
      resolve({ unavailable: 'dash-extra.mjs not found' });
      return;
    }

    try {
      const proc = spawn('node', [dashExtraPath, '--json'], {
        cwd: aesopRoot,
        env: {
          ...process.env,
          AESOP_ROOT: aesopRoot
        },
        timeout: 5000,
        stdio: ['ignore', 'pipe', 'pipe']
      });

      let stdout = '';
      let stderr = '';
      let resolved = false;

      const cleanup = () => {
        if (!resolved) {
          resolved = true;
          if (proc && !proc.killed) {
            try {
              proc.kill();
            } catch (e) {
              // ignore
            }
          }
        }
      };

      const timeoutHandle = setTimeout(() => {
        cleanup();
        resolve({ unavailable: 'dash-extra.mjs timeout' });
      }, 6000);

      proc.stdout.on('data', (data) => {
        stdout += data.toString();
      });

      proc.stderr.on('data', (data) => {
        stderr += data.toString();
      });

      proc.on('close', (code) => {
        clearTimeout(timeoutHandle);
        if (!resolved) {
          resolved = true;
          if (code === 0 && stdout.trim()) {
            try {
              const agents = JSON.parse(stdout);
              resolve({
                agents: Array.isArray(agents) ? agents : [],
                count: Array.isArray(agents) ? agents.length : 0
              });
            } catch (e) {
              resolve({ unavailable: 'failed to parse dash-extra output' });
            }
          } else {
            resolve({ unavailable: 'dash-extra.mjs failed' });
          }
        }
      });

      proc.on('error', (err) => {
        clearTimeout(timeoutHandle);
        cleanup();
        if (!resolved) {
          resolved = true;
          resolve({ unavailable: 'dash-extra.mjs spawn error' });
        }
      });
    } catch (e) {
      resolve({ unavailable: 'exception spawning dash-extra.mjs' });
    }
  });
}

function getTrackerLaneCounts(trackerPath) {
  const tracker = loadJSON(trackerPath);
  if (!tracker) {
    return { unavailable: 'tracker.json not found or malformed' };
  }

  const items = tracker.items || [];
  const byLane = {};

  for (const item of items) {
    const lane = item.lane || 'unknown';
    byLane[lane] = (byLane[lane] || 0) + 1;
  }

  return {
    total_items: items.length,
    by_lane: byLane
  };
}

function getOrchestratorStatus(orchStatusPath) {
  const status = loadJSON(orchStatusPath);
  if (!status) {
    return { unavailable: 'orchestrator-status.json not found or malformed' };
  }
  return status;
}

// ============================================================================
// Main
// ============================================================================

(async function main() {
  try {
    // Determine AESOP_ROOT
    const aesopRoot = process.env.AESOP_ROOT || CURRENT_DIR;

    const stateDir = path.join(aesopRoot, 'state');
    const watchdogHeartbeatPath = path.join(stateDir, '.watchdog-heartbeat');
    const monitorHeartbeatPath = path.join(stateDir, '.monitor-heartbeat');
    const trackerPath = path.join(stateDir, 'tracker.json');
    const orchStatusPath = path.join(stateDir, 'orchestrator-status.json');

    // Gather all data concurrently
    const [watchdogTs, monitorTs, agentsData, trackerData, orchStatusData] = await Promise.all([
      Promise.resolve(readHeartbeat(watchdogHeartbeatPath)),
      Promise.resolve(readHeartbeat(monitorHeartbeatPath)),
      getFleetAgents(aesopRoot),
      Promise.resolve(getTrackerLaneCounts(trackerPath)),
      Promise.resolve(getOrchestratorStatus(orchStatusPath))
    ]);

    // Build result object
    const result = {
      timestamp: new Date().toISOString(),
      aesop_root: aesopRoot,
      heartbeats: {
        watchdog: null,
        monitor: null
      },
      agents: agentsData,
      tracker: trackerData,
      orchestrator: orchStatusData
    };

    // Process watchdog heartbeat
    const watchdogStatus = getHeartbeatStatus(watchdogTs, WATCHDOG_STALE_THRESHOLD);
    if (watchdogStatus.age !== null) {
      result.heartbeats.watchdog = {
        age_seconds: watchdogStatus.age,
        status: watchdogStatus.status,
        threshold_seconds: WATCHDOG_STALE_THRESHOLD
      };
    } else {
      result.heartbeats.watchdog = {
        unavailable: watchdogStatus.status
      };
    }

    // Process monitor heartbeat
    const monitorStatus = getHeartbeatStatus(monitorTs, MONITOR_STALE_THRESHOLD);
    if (monitorStatus.age !== null) {
      result.heartbeats.monitor = {
        age_seconds: monitorStatus.age,
        status: monitorStatus.status,
        threshold_seconds: MONITOR_STALE_THRESHOLD
      };
    } else {
      result.heartbeats.monitor = {
        unavailable: monitorStatus.status
      };
    }

    // Output JSON
    console.log(JSON.stringify(result, null, 2));

    process.exitCode = 0;
  } catch (err) {
    console.error(`Error gathering fleet snapshot: ${err.message}`);
    process.exitCode = 1;
  }
})();
