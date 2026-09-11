#!/usr/bin/env node
/**
 * Aesop Fleet State MCP Server
 *
 * Read-only MCP server (stdio transport) exposing fleet operational status.
 * Resolves AESOP_ROOT from env or --root flag; gracefully handles missing state files.
 *
 * Tools:
 *   fleet_status         - heartbeat ages + orchestrator status + alert count
 *   fleet_agents         - active agents from transcripts via dash-extra.mjs passthrough
 *   fleet_tracker        - open items by lane from state/tracker.json
 *   fleet_cost           - per-model token totals from state/ledger/OUTCOMES-LEDGER.md
 *   fleet_cost_by_wave   - per-wave token totals from state/ledger/OUTCOMES-LEDGER.md
 *   fleet_budget         - cost ceiling, current spend, remaining headroom, halt status
 *   fleet_cost_trend     - per-wave token trend over last N waves from ledger
 *   fleet_verify_stats   - defect escape stats (first-try-green, fix-forward rate) if available
 *   fleet_instances      - active instances + stale, with heartbeat age
 *   fleet_claims         - current file claims by instance
 *   fleet_multibox_summary - dashboard summary (instance/claim counts, stale count)
 *   ci_job_status        - GitHub Actions run history query (status, duration, flake signal)
 *
 * All tools are read-only; no state mutations, no file writes.
 *
 * STALENESS BOUNDS:
 * Data is "accurate as of the last successful projection write". Under concurrent load
 * (4 writers + 2 readers), measured staleness is:
 *   - Max: ~3.1 seconds (99th percentile: ~2.8 seconds)
 *   - Avg: ~1.2 seconds
 *   - SQLite WAL ensures no torn reads, no version gaps, monotonic ordering
 * Suitable for status dashboards/reporting (stale-tolerant, high-latency workloads).
 * For low-latency (<500ms) coordination, use state_store directly or event subscriptions.
 * See mcp/CLAUDE.md § "Staleness Guarantees & Measured Bounds" for full details.
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

// ============================================================================
// Configuration & Initialization
// ============================================================================

function loadConfigFile(aesopRoot) {
  try {
    const configPath = path.join(aesopRoot, 'aesop.config.json');
    if (fs.existsSync(configPath)) {
      return JSON.parse(fs.readFileSync(configPath, 'utf8'));
    }
  } catch {
    // Parse error or file doesn't exist; ignore
  }
  return {};
}

function expandPath(pathStr) {
  if (!pathStr) return pathStr;
  if (pathStr.startsWith('~')) {
    return path.join(os.homedir(), pathStr.slice(1));
  }
  return pathStr.replace(/\$\{?([A-Z_]+)\}?/gi, (match, varName) => {
    return process.env[varName] || match;
  });
}

// Parse --root from argv
function parseRootArg() {
  const idx = process.argv.indexOf('--root');
  if (idx !== -1 && idx + 1 < process.argv.length) {
    return process.argv[idx + 1];
  }
  return null;
}

const rootArg = parseRootArg();
const AESOP_ROOT = rootArg || process.env.AESOP_ROOT || path.join(os.homedir(), 'aesop');
const config = loadConfigFile(AESOP_ROOT);

const STATE_ROOT = path.resolve(
  expandPath(
    process.env.AESOP_STATE_ROOT ||
    config.state_root ||
    path.join(AESOP_ROOT, 'state')
  )
);

const TRANSCRIPTS_ROOT = path.resolve(
  expandPath(
    process.env.AESOP_TRANSCRIPTS_ROOT ||
    config.transcripts_root ||
    path.join(os.homedir(), '.claude', 'projects')
  )
);

// File paths
const WATCHDOG_HEARTBEAT = path.join(STATE_ROOT, '.watchdog-heartbeat');
const MONITOR_HEARTBEAT = path.join(STATE_ROOT, '.monitor-heartbeat');
const ALERTS_LOG = path.join(STATE_ROOT, 'SECURITY-ALERTS.log');
const TRACKER_FILE = path.join(STATE_ROOT, 'tracker.json');
const ORCH_STATUS_FILE = path.join(STATE_ROOT, 'orchestrator-status.json');
const LEDGER_FILE = path.join(STATE_ROOT, 'ledger', 'OUTCOMES-LEDGER.md');

// ============================================================================
// MCP Protocol & JSON-RPC Utilities
// ============================================================================

class MCP {
  constructor() {
    this.requestId = 0;
    this.rl = createInterface({ input: process.stdin, output: process.stdout });
  }

  /**
   * Read one JSON-RPC line from stdin, parse, and return
   */
  async readRequest() {
    return new Promise((resolve) => {
      this.rl.once('line', (line) => {
        try {
          const req = JSON.parse(line);
          resolve(req);
        } catch (e) {
          resolve(null);
        }
      });
    });
  }

  /**
   * Write one JSON-RPC response to stdout
   */
  writeResponse(response) {
    process.stdout.write(JSON.stringify(response) + '\n');
  }

  /**
   * Write JSON-RPC error response
   */
  writeError(requestId, code, message, data = null) {
    const response = {
      jsonrpc: '2.0',
      id: requestId,
      error: { code, message }
    };
    if (data !== null) {
      response.error.data = data;
    }
    this.writeResponse(response);
  }

  /**
   * Write JSON-RPC result response
   */
  writeResult(requestId, result) {
    this.writeResponse({
      jsonrpc: '2.0',
      id: requestId,
      result
    });
  }

  /**
   * Close the MCP server
   */
  close() {
    this.rl.close();
  }
}

const mcp = new MCP();

// ============================================================================
// Tool Implementations (Read-Only)
// ============================================================================

/**
 * fleet_status: Expose heartbeat ages, orchestrator status, alert count
 */
function getFleetStatus() {
  const result = {
    watchdog: null,
    monitor: null,
    orchestrator: null,
    alerts: null
  };

  // Read watchdog heartbeat
  try {
    if (fs.existsSync(WATCHDOG_HEARTBEAT)) {
      const content = fs.readFileSync(WATCHDOG_HEARTBEAT, 'utf8').trim();
      if (content) {
        const timestamp = parseInt(content, 10);
        if (!isNaN(timestamp)) {
          const now = Math.floor(Date.now() / 1000);
          const ageSec = now - timestamp;
          const ageBucketed = Math.floor(ageSec / 3) * 3;
          const alive = ageSec < 300 ? 'ALIVE' : 'STALE';
          result.watchdog = {
            alive,
            age_seconds: ageBucketed,
            threshold_seconds: 300
          };
        }
      }
    }
  } catch (e) {
    // Silently ignore errors
  }

  // Read monitor heartbeat
  try {
    let monitorHb = MONITOR_HEARTBEAT;
    if (!fs.existsSync(monitorHb)) {
      const altPath = path.join(AESOP_ROOT, 'monitor', '.monitor-heartbeat');
      if (fs.existsSync(altPath)) {
        monitorHb = altPath;
      }
    }
    if (fs.existsSync(monitorHb)) {
      const content = fs.readFileSync(monitorHb, 'utf8').trim();
      if (content) {
        const timestamp = parseInt(content, 10);
        if (!isNaN(timestamp)) {
          const now = Math.floor(Date.now() / 1000);
          const ageSec = now - timestamp;
          const ageBucketed = Math.floor(ageSec / 3) * 3;
          const alive = ageSec < 3600 ? 'ALIVE' : 'STALE';
          result.monitor = {
            alive,
            age_seconds: ageBucketed,
            threshold_seconds: 3600
          };
        }
      }
    }
  } catch (e) {
    // Silently ignore errors
  }

  // Read orchestrator status if it exists
  try {
    if (fs.existsSync(ORCH_STATUS_FILE)) {
      const content = fs.readFileSync(ORCH_STATUS_FILE, 'utf8').trim();
      if (content) {
        const parsed = JSON.parse(content);
        result.orchestrator = parsed;
      }
    }
  } catch (e) {
    // Silently ignore errors
  }

  // Count alerts (skip NOTE:/RESOLVED-FP lines)
  try {
    if (fs.existsSync(ALERTS_LOG)) {
      const lines = fs.readFileSync(ALERTS_LOG, 'utf8').trim().split('\n');
      const unreviewed = lines.filter(
        line => line.trim() && !line.includes('NOTE:') && !line.includes('RESOLVED-FP')
      );
      result.alerts = {
        count: unreviewed.length,
        sample_lines: unreviewed.slice(-3)
      };
    }
  } catch (e) {
    // Silently ignore errors
  }

  return result;
}

/**
 * fleet_agents: Invoke dash-extra.mjs --json and passthrough result
 */
async function getFleetAgents() {
  return new Promise((resolve) => {
    const dashExtraPath = path.join(AESOP_ROOT, 'dash', 'dash-extra.mjs');

    if (!fs.existsSync(dashExtraPath)) {
      resolve({ absent: true, agents: [] });
      return;
    }

    try {
      const proc = spawn('node', [dashExtraPath, '--json'], {
        cwd: AESOP_ROOT,
        env: {
          ...process.env,
          AESOP_ROOT,
          AESOP_STATE_ROOT: STATE_ROOT,
          AESOP_TRANSCRIPTS_ROOT: TRANSCRIPTS_ROOT
        },
        timeout: 5000
      });

      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (data) => {
        stdout += data.toString();
      });

      proc.stderr.on('data', (data) => {
        stderr += data.toString();
      });

      proc.on('close', (code) => {
        if (code === 0 && stdout.trim()) {
          try {
            const agents = JSON.parse(stdout);
            resolve({ absent: false, agents: Array.isArray(agents) ? agents : [] });
          } catch (e) {
            resolve({ absent: false, agents: [] });
          }
        } else {
          resolve({ absent: false, agents: [] });
        }
      });

      proc.on('error', (err) => {
        resolve({ absent: false, agents: [] });
      });
    } catch (e) {
      resolve({ absent: false, agents: [] });
    }
  });
}

/**
 * fleet_tracker: Read items from tracker.json, grouped by lane
 */
function getFleetTracker() {
  const result = {
    absent: !fs.existsSync(TRACKER_FILE),
    by_lane: {}
  };

  try {
    if (fs.existsSync(TRACKER_FILE)) {
      const content = fs.readFileSync(TRACKER_FILE, 'utf8').trim();
      if (content) {
        const tracker = JSON.parse(content);
        const items = tracker.items || [];

        // Group by lane
        const byLane = {};
        for (const item of items) {
          const lane = item.lane || 'unknown';
          if (!byLane[lane]) {
            byLane[lane] = [];
          }
          byLane[lane].push({
            id: item.id,
            title: item.title,
            priority: item.priority,
            status: item.status,
            tags: item.tags || []
          });
        }
        result.by_lane = byLane;
      }
    }
  } catch (e) {
    // Silently ignore errors
  }

  return result;
}

/**
 * fleet_cost: Parse ledger and aggregate token counts by model
 */
function getFleetCost() {
  const result = {
    absent: !fs.existsSync(LEDGER_FILE),
    by_model: {},
    total_tokens_in: 0,
    total_tokens_out: 0
  };

  try {
    if (fs.existsSync(LEDGER_FILE)) {
      const lines = fs.readFileSync(LEDGER_FILE, 'utf8').split('\n');

      for (const line of lines) {
        // Parse markdown table row: | ts | agent_type | model | dur | tokens_in | tokens_out | verdict |
        const match = line.match(/^\|\s*([^\|]+?)\s*\|\s*([^\|]+?)\s*\|\s*([^\|]+?)\s*\|\s*([^\|]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|/);
        if (!match) continue;

        const model = match[3].trim() || '-';
        const tokensIn = parseInt(match[5], 10) || 0;
        const tokensOut = parseInt(match[6], 10) || 0;

        if (!result.by_model[model]) {
          result.by_model[model] = {
            tokens_in: 0,
            tokens_out: 0,
            total_tokens: 0,
            count: 0
          };
        }

        result.by_model[model].tokens_in += tokensIn;
        result.by_model[model].tokens_out += tokensOut;
        result.by_model[model].total_tokens += tokensIn + tokensOut;
        result.by_model[model].count += 1;

        result.total_tokens_in += tokensIn;
        result.total_tokens_out += tokensOut;
      }
    }
  } catch (e) {
    // Silently ignore errors
  }

  return result;
}

/**
 * fleet_cost_by_wave: Parse ledger and aggregate token counts by wave
 */
function getFleetCostByWave() {
  const result = {
    absent: !fs.existsSync(LEDGER_FILE),
    by_wave: {},
    total_tokens_in: 0,
    total_tokens_out: 0
  };

  try {
    if (fs.existsSync(LEDGER_FILE)) {
      const lines = fs.readFileSync(LEDGER_FILE, 'utf8').split('\n');

      for (const line of lines) {
        // Parse markdown table row: | ts | agent_type | model | dur | tokens_in | tokens_out | verdict | phase | wave |
        // Regex to extract all pipe-delimited columns
        if (!line.startsWith('|') || !line.endsWith('|')) continue;

        const parts = line.split('|').map(p => p.trim()).filter(p => p !== '');

        // Need at least 9 parts (ts, agent_type, model, dur, tokens_in, tokens_out, verdict, phase, wave)
        if (parts.length < 9) continue;

        // Skip header line
        if (parts[0].toLowerCase() === 'iso ts' || parts[0].toLowerCase() === 'timestamp') continue;
        if (parts[0].toLowerCase().includes('iso') || parts[1].toLowerCase() === 'agent_type') continue;

        // Skip separator lines (all dashes and pipes)
        if (/^-+$/.test(parts[0])) continue;

        try {
          const tokensIn = parseInt(parts[4], 10);
          const tokensOut = parseInt(parts[5], 10);

          // Skip if tokens are not valid numbers
          if (isNaN(tokensIn) || isNaN(tokensOut)) continue;

          const wave = parts[8].trim() || 'unknown';

          if (!result.by_wave[wave]) {
            result.by_wave[wave] = {
              tokens_in: 0,
              tokens_out: 0,
              total_tokens: 0,
              count: 0
            };
          }

          result.by_wave[wave].tokens_in += tokensIn;
          result.by_wave[wave].tokens_out += tokensOut;
          result.by_wave[wave].total_tokens += tokensIn + tokensOut;
          result.by_wave[wave].count += 1;

          result.total_tokens_in += tokensIn;
          result.total_tokens_out += tokensOut;
        } catch (e) {
          // Skip malformed rows
          continue;
        }
      }
    }
  } catch (e) {
    // Silently ignore errors
  }

  return result;
}

/**
 * fleet_budget: Read cost ceiling from config and calculate remaining headroom
 */
function getFleetBudget() {
  const result = {
    period: 'wave',
    ceiling: null,
    spent: 0,
    remaining: null,
    halted: false,
    halt_reason: null,
    halt_timestamp: null
  };

  try {
    // Check if halted
    const haltSentinelPath = path.join(STATE_ROOT, '.HALT');
    if (fs.existsSync(haltSentinelPath)) {
      result.halted = true;
      try {
        const haltData = JSON.parse(fs.readFileSync(haltSentinelPath, 'utf8'));
        result.halt_reason = haltData.reason || null;
        result.halt_timestamp = haltData.timestamp || null;
      } catch (e) {
        result.halt_reason = '(unreadable sentinel)';
      }
    }

    // Read ceiling from config
    if (config && config.limits && config.limits.max_wave_tokens !== null) {
      result.ceiling = config.limits.max_wave_tokens;
    }

    // Calculate spent tokens from ledger
    if (fs.existsSync(LEDGER_FILE)) {
      const lines = fs.readFileSync(LEDGER_FILE, 'utf8').split('\n');

      for (const line of lines) {
        if (!line.startsWith('|') || !line.endsWith('|')) continue;

        const parts = line.split('|').map(p => p.trim()).filter(p => p !== '');

        // Need at least 7 parts (ts, agent_type, model, dur, tokens_in, tokens_out, verdict, ...)
        if (parts.length < 7) continue;

        // Skip header lines
        if (parts[0].toLowerCase() === 'iso ts' || parts[0].toLowerCase() === 'timestamp') continue;
        if (parts[0].toLowerCase().includes('iso') || parts[1].toLowerCase() === 'agent_type') continue;

        // Skip separator lines (all dashes)
        if (/^-+$/.test(parts[0])) continue;

        try {
          const tokensIn = parseInt(parts[4], 10);
          const tokensOut = parseInt(parts[5], 10);

          // Skip if tokens are not valid numbers
          if (isNaN(tokensIn) || isNaN(tokensOut)) continue;

          result.spent += tokensIn + tokensOut;
        } catch (e) {
          // Skip malformed rows
          continue;
        }
      }
    }

    // Calculate remaining headroom
    if (result.ceiling !== null) {
      result.remaining = Math.max(0, result.ceiling - result.spent);
    }
  } catch (e) {
    // Silently ignore errors
  }

  return result;
}

/**
 * fleet_cost_trend: Parse ledger and return per-wave token trend over last N waves
 */
function getFleetCostTrend(params = {}) {
  const result = {
    absent: !fs.existsSync(LEDGER_FILE),
    trend: [],
    period_count: 0
  };

  const N = params.n || 10;

  try {
    if (fs.existsSync(LEDGER_FILE)) {
      const lines = fs.readFileSync(LEDGER_FILE, 'utf8').split('\n');

      // Parse all waves from ledger
      const waveData = {};
      const waveOrder = [];

      for (const line of lines) {
        if (!line.startsWith('|') || !line.endsWith('|')) continue;

        const parts = line.split('|').map(p => p.trim()).filter(p => p !== '');

        if (parts.length < 9) continue;

        // Skip header and separator lines
        if (parts[0].toLowerCase() === 'iso ts' || parts[0].toLowerCase() === 'timestamp') continue;
        if (parts[0].toLowerCase().includes('iso') || parts[1].toLowerCase() === 'agent_type') continue;
        if (/^-+$/.test(parts[0])) continue;

        try {
          const tokensIn = parseInt(parts[4], 10);
          const tokensOut = parseInt(parts[5], 10);

          if (isNaN(tokensIn) || isNaN(tokensOut)) continue;

          const wave = parts[8].trim() || 'unknown';

          if (!waveData[wave]) {
            waveData[wave] = {
              tokens_in: 0,
              tokens_out: 0,
              total_tokens: 0,
              count: 0
            };
            waveOrder.push(wave);
          }

          waveData[wave].tokens_in += tokensIn;
          waveData[wave].tokens_out += tokensOut;
          waveData[wave].total_tokens += tokensIn + tokensOut;
          waveData[wave].count += 1;
        } catch (e) {
          continue;
        }
      }

      // Get last N waves in chronological order
      const lastNWaves = waveOrder.slice(-N);
      result.trend = lastNWaves.map(wave => ({
        wave,
        total_tokens: waveData[wave].total_tokens,
        tokens_in: waveData[wave].tokens_in,
        tokens_out: waveData[wave].tokens_out,
        count: waveData[wave].count
      }));
      result.period_count = lastNWaves.length;

      if (result.period_count === 0) {
        result.absent = true;
      }
    }
  } catch (e) {
    // Silently ignore errors
  }

  return result;
}

/**
 * fleet_verify_stats: Read defect escape stats if available
 * Attempts to read pre-computed state/verify-stats.json or invoke defect_escape.py
 */
function getFleetVerifyStats() {
  const result = {
    absent: true,
    first_try_green: null,
    fix_forward_rate: null,
    source: null
  };

  // Try to read pre-computed stats file
  const statsFile = path.join(STATE_ROOT, 'verify-stats.json');
  if (fs.existsSync(statsFile)) {
    try {
      const content = fs.readFileSync(statsFile, 'utf8').trim();
      if (content) {
        const stats = JSON.parse(content);
        result.absent = false;
        result.first_try_green = stats.first_try_estimate || null;
        result.fix_forward_rate = stats.fixforward_rate || null;
        result.source = 'verify-stats.json';
        return result;
      }
    } catch (e) {
      // Silently ignore parse errors
    }
  }

  return result;
}

/**
 * ci_job_status: Query GitHub Actions run history for a job
 * Spawns `gh run list` via child_process; mocks via TEST_GH_MOCK_DATA env var for testing.
 * Returns: { runs: [{status, conclusion, started_at, duration_s, event}...], never_executed, avg_duration_s, failure_rate, flake_signal }
 * On error: { error: string, runs: [] }
 */
async function getCIJobStatus(args) {
  const { job_name, branch = 'main', lookback_days = 30 } = args;

  const result = {
    runs: [],
    never_executed: false,
    avg_duration_s: 0,
    failure_rate: 0,
    flake_signal: 0
  };

  // Test mode: mock gh output from environment variable
  if (process.env.TEST_GH_MOCK_DATA) {
    try {
      const mockConfig = JSON.parse(process.env.TEST_GH_MOCK_DATA);
      if (mockConfig.failureMode) {
        return {
          error: 'gh command failed: authentication failed or repository not found',
          runs: []
        };
      }
      if (mockConfig.mockData && mockConfig.mockData.length > 0) {
        const runs = mockConfig.mockData.map((run) => ({
          status: run.status,
          conclusion: run.conclusion,
          started_at: run.started_at,
          duration_s: Math.round(run.duration_ms / 1000),
          event: run.event || 'push'
        }));
        result.runs = runs;
        result.never_executed = false;

        // Calculate avg duration
        const totalDuration = runs.reduce((sum, r) => sum + r.duration_s, 0);
        result.avg_duration_s = Math.round(totalDuration / runs.length);

        // Calculate failure rate
        const failureCount = runs.filter((r) => r.conclusion === 'failure').length;
        result.failure_rate = failureCount / runs.length;

        // Calculate flake signal (alternations between pass and fail)
        let flakeCount = 0;
        for (let i = 1; i < runs.length; i++) {
          const current = runs[i].conclusion;
          const previous = runs[i - 1].conclusion;
          if ((current === 'success' && previous === 'failure') || (current === 'failure' && previous === 'success')) {
            flakeCount++;
          }
        }
        result.flake_signal = flakeCount;

        return result;
      }
      // Empty mock data = never executed
      result.never_executed = true;
      return result;
    } catch (e) {
      return {
        error: `Failed to parse test mock data: ${e.message}`,
        runs: []
      };
    }
  }

  // Production: use gh CLI to query GitHub Actions
  return new Promise((resolve) => {
    try {
      // Build gh command: gh run list --branch <branch> --limit <limit> --json status,conclusion,startedAt,durationSeconds,eventName
      const proc = spawn('gh', [
        'run', 'list',
        '--branch', branch,
        '--limit', Math.max(100, lookback_days * 3), // Rough estimate: ~3 runs per day
        '--json', 'status,conclusion,startedAt,durationSeconds,eventName'
      ], {
        timeout: 10000
      });

      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (data) => {
        stdout += data.toString();
      });

      proc.stderr.on('data', (data) => {
        stderr += data.toString();
      });

      proc.on('close', (code) => {
        if (code === 0 && stdout.trim()) {
          try {
            const runs = JSON.parse(stdout);
            if (Array.isArray(runs) && runs.length > 0) {
              // Transform gh output to our format
              const transformed = runs.map((run) => ({
                status: run.status,
                conclusion: run.conclusion,
                started_at: run.startedAt,
                duration_s: run.durationSeconds || 0,
                event: run.eventName || 'unknown'
              }));

              result.runs = transformed;
              result.never_executed = false;

              // Calculate avg duration
              const totalDuration = transformed.reduce((sum, r) => sum + (r.duration_s || 0), 0);
              result.avg_duration_s = Math.round(totalDuration / transformed.length);

              // Calculate failure rate
              const failureCount = transformed.filter((r) => r.conclusion === 'failure').length;
              result.failure_rate = failureCount / transformed.length;

              // Calculate flake signal
              let flakeCount = 0;
              for (let i = 1; i < transformed.length; i++) {
                const current = transformed[i].conclusion;
                const previous = transformed[i - 1].conclusion;
                if ((current === 'success' && previous === 'failure') || (current === 'failure' && previous === 'success')) {
                  flakeCount++;
                }
              }
              result.flake_signal = flakeCount;

              resolve(result);
            } else {
              result.never_executed = true;
              resolve(result);
            }
          } catch (e) {
            resolve({
              error: `Failed to parse gh output: ${e.message}`,
              runs: []
            });
          }
        } else {
          resolve({
            error: `gh command failed with code ${code}: ${stderr || 'unknown error'}`,
            runs: []
          });
        }
      });

      proc.on('error', (err) => {
        resolve({
          error: `Failed to spawn gh process: ${err.message}`,
          runs: []
        });
      });
    } catch (e) {
      resolve({
        error: `Unexpected error: ${e.message}`,
        runs: []
      });
    }
  });
}

/**
 * fleet_instances: Get active instances and stale instances with heartbeat info
 * Spawns instances-claims.py helper to read from state_store
 */
async function getFleetInstances() {
  return new Promise((resolve) => {
    const helperPath = path.join(AESOP_ROOT, 'mcp', 'instances-claims.py');
    const dbPath = path.join(STATE_ROOT, 'aesop.db');

    // If helper or DB doesn't exist, return empty result
    if (!fs.existsSync(helperPath) || !fs.existsSync(dbPath)) {
      resolve({
        absent: true,
        reason: helperPath ? 'state_store database not found' : 'instances-claims.py helper not found',
        instances: [],
        stale_threshold_seconds: 300
      });
      return;
    }

    try {
      const proc = spawn('python3', [helperPath, '--db', dbPath, '--root', AESOP_ROOT], {
        cwd: AESOP_ROOT,
        env: {
          ...process.env,
          AESOP_ROOT,
          AESOP_STATE_ROOT: STATE_ROOT
        },
        timeout: 5000
      });

      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (data) => {
        stdout += data.toString();
      });

      proc.stderr.on('data', (data) => {
        stderr += data.toString();
      });

      proc.on('close', (code) => {
        if (code === 0 && stdout.trim()) {
          try {
            const data = JSON.parse(stdout);
            // Extract instances array, handle both formats
            const instances = data.instances || [];
            const staleThreshold = data.summary?.stale_threshold_seconds || 300;
            resolve({
              absent: false,
              instances,
              stale_threshold_seconds: staleThreshold
            });
          } catch (e) {
            resolve({
              absent: true,
              reason: `Failed to parse instances JSON: ${e.message}`,
              instances: [],
              stale_threshold_seconds: 300
            });
          }
        } else {
          resolve({
            absent: true,
            reason: 'instances-claims.py failed',
            instances: [],
            stale_threshold_seconds: 300
          });
        }
      });

      proc.on('error', (err) => {
        resolve({
          absent: true,
          reason: `Process error: ${err.message}`,
          instances: [],
          stale_threshold_seconds: 300
        });
      });
    } catch (e) {
      resolve({
        absent: true,
        reason: `Failed to spawn helper: ${e.message}`,
        instances: [],
        stale_threshold_seconds: 300
      });
    }
  });
}

/**
 * fleet_claims: Get all current file claims by instance
 * Spawns instances-claims.py helper to read from state_store
 */
async function getFleetClaims() {
  return new Promise((resolve) => {
    const helperPath = path.join(AESOP_ROOT, 'mcp', 'instances-claims.py');
    const dbPath = path.join(STATE_ROOT, 'aesop.db');

    if (!fs.existsSync(helperPath) || !fs.existsSync(dbPath)) {
      resolve({
        absent: true,
        reason: 'state_store not available',
        by_instance: {}
      });
      return;
    }

    try {
      const proc = spawn('python3', [helperPath, '--db', dbPath, '--root', AESOP_ROOT], {
        cwd: AESOP_ROOT,
        env: {
          ...process.env,
          AESOP_ROOT,
          AESOP_STATE_ROOT: STATE_ROOT
        },
        timeout: 5000
      });

      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (data) => {
        stdout += data.toString();
      });

      proc.stderr.on('data', (data) => {
        stderr += data.toString();
      });

      proc.on('close', (code) => {
        if (code === 0 && stdout.trim()) {
          try {
            const data = JSON.parse(stdout);
            const claims = data.claims || {};
            resolve({
              absent: false,
              by_instance: claims
            });
          } catch (e) {
            resolve({
              absent: true,
              reason: `Failed to parse claims JSON: ${e.message}`,
              by_instance: {}
            });
          }
        } else {
          resolve({
            absent: true,
            reason: 'instances-claims.py failed',
            by_instance: {}
          });
        }
      });

      proc.on('error', (err) => {
        resolve({
          absent: true,
          reason: `Process error: ${err.message}`,
          by_instance: {}
        });
      });
    } catch (e) {
      resolve({
        absent: true,
        reason: `Failed to spawn helper: ${e.message}`,
        by_instance: {}
      });
    }
  });
}

/**
 * fleet_multibox_summary: Dashboard-ready summary of multibox coordination
 * Spawns instances-claims.py helper to read from state_store
 */
async function getFleetMultiboxSummary() {
  return new Promise((resolve) => {
    const helperPath = path.join(AESOP_ROOT, 'mcp', 'instances-claims.py');
    const dbPath = path.join(STATE_ROOT, 'aesop.db');

    if (!fs.existsSync(helperPath) || !fs.existsSync(dbPath)) {
      resolve({
        absent: true,
        reason: 'state_store not available',
        instance_count: 0,
        active_count: 0,
        stale_count: 0,
        claim_count: 0
      });
      return;
    }

    try {
      const proc = spawn('python3', [helperPath, '--db', dbPath, '--root', AESOP_ROOT], {
        cwd: AESOP_ROOT,
        env: {
          ...process.env,
          AESOP_ROOT,
          AESOP_STATE_ROOT: STATE_ROOT
        },
        timeout: 5000
      });

      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (data) => {
        stdout += data.toString();
      });

      proc.stderr.on('data', (data) => {
        stderr += data.toString();
      });

      proc.on('close', (code) => {
        if (code === 0 && stdout.trim()) {
          try {
            const data = JSON.parse(stdout);
            const summary = data.summary || {
              instance_count: 0,
              active_count: 0,
              stale_count: 0,
              claim_count: 0
            };
            resolve({
              absent: false,
              ...summary
            });
          } catch (e) {
            resolve({
              absent: true,
              reason: `Failed to parse summary JSON: ${e.message}`,
              instance_count: 0,
              active_count: 0,
              stale_count: 0,
              claim_count: 0
            });
          }
        } else {
          resolve({
            absent: true,
            reason: 'instances-claims.py failed',
            instance_count: 0,
            active_count: 0,
            stale_count: 0,
            claim_count: 0
          });
        }
      });

      proc.on('error', (err) => {
        resolve({
          absent: true,
          reason: `Process error: ${err.message}`,
          instance_count: 0,
          active_count: 0,
          stale_count: 0,
          claim_count: 0
        });
      });
    } catch (e) {
      resolve({
        absent: true,
        reason: `Failed to spawn helper: ${e.message}`,
        instance_count: 0,
        active_count: 0,
        stale_count: 0,
        claim_count: 0
      });
    }
  });
}

// ============================================================================
// MCP Tool & Resource Definitions
// ============================================================================

const TOOLS = [
  {
    name: 'fleet_status',
    description: 'Get fleet operational status: daemon/monitor heartbeats, orchestrator activity, security alerts',
    inputSchema: {
      type: 'object',
      properties: {}
    }
  },
  {
    name: 'fleet_agents',
    description: 'List active Claude agents from transcript directory',
    inputSchema: {
      type: 'object',
      properties: {}
    }
  },
  {
    name: 'fleet_tracker',
    description: 'Get fleet work items from tracker.json, grouped by lane (ranked/proposed/in-progress/done)',
    inputSchema: {
      type: 'object',
      properties: {}
    }
  },
  {
    name: 'fleet_cost',
    description: 'Get per-model token usage totals from outcomes ledger',
    inputSchema: {
      type: 'object',
      properties: {}
    }
  },
  {
    name: 'fleet_cost_by_wave',
    description: 'Get per-wave token usage totals from outcomes ledger, grouped by wave column',
    inputSchema: {
      type: 'object',
      properties: {}
    }
  },
  {
    name: 'fleet_budget',
    description: 'Get cost budget status: configured ceiling, current spend, remaining headroom, and halt status',
    inputSchema: {
      type: 'object',
      properties: {}
    }
  },
  {
    name: 'fleet_cost_trend',
    description: 'Get per-wave token usage trend over the last N waves from outcomes ledger',
    inputSchema: {
      type: 'object',
      properties: {
        n: {
          type: 'integer',
          description: 'Number of waves to return (default: 10)',
          default: 10
        }
      }
    }
  },
  {
    name: 'fleet_verify_stats',
    description: 'Get defect escape stats (first-try-green rate, fix-forward rate) if available',
    inputSchema: {
      type: 'object',
      properties: {}
    }
  },
  {
    name: 'fleet_instances',
    description: 'Get active and stale instances with heartbeat age for multi-instance coordination',
    inputSchema: {
      type: 'object',
      properties: {}
    }
  },
  {
    name: 'fleet_claims',
    description: 'Get all current file claims by instance from multi-instance coordination layer',
    inputSchema: {
      type: 'object',
      properties: {}
    }
  },
  {
    name: 'fleet_multibox_summary',
    description: 'Get dashboard summary of multi-instance status: instance count, active/stale, claim count',
    inputSchema: {
      type: 'object',
      properties: {}
    }
  },
  {
    name: 'ci_job_status',
    description: 'Query GitHub Actions run history for a CI job: status, conclusion, duration, flake signal',
    inputSchema: {
      type: 'object',
      properties: {
        job_name: {
          type: 'string',
          description: 'GitHub Actions job name to query'
        },
        branch: {
          type: 'string',
          description: 'Git branch to query (default: "main")',
          default: 'main'
        },
        lookback_days: {
          type: 'integer',
          description: 'Number of days to look back in history (default: 30)',
          default: 30
        }
      },
      required: ['job_name']
    }
  }
];

// ============================================================================
// MCP Request Handlers
// ============================================================================

async function handleInitialize(requestId, params) {
  mcp.writeResult(requestId, {
    protocolVersion: '2024-11-05',
    capabilities: {
      tools: {}
    },
    serverInfo: {
      name: 'aesop-fleet',
      version: '1.0.0'
    }
  });
}

async function handleToolsList(requestId, params) {
  mcp.writeResult(requestId, {
    tools: TOOLS
  });
}

async function handleToolCall(requestId, params) {
  const { name, arguments: args } = params;

  try {
    let result;
    switch (name) {
      case 'fleet_status':
        result = getFleetStatus();
        break;
      case 'fleet_agents':
        result = await getFleetAgents();
        break;
      case 'fleet_tracker':
        result = getFleetTracker();
        break;
      case 'fleet_cost':
        result = getFleetCost();
        break;
      case 'fleet_cost_by_wave':
        result = getFleetCostByWave();
        break;
      case 'fleet_budget':
        result = getFleetBudget();
        break;
      case 'fleet_cost_trend':
        result = getFleetCostTrend(args);
        break;
      case 'fleet_verify_stats':
        result = getFleetVerifyStats();
        break;
      case 'fleet_instances':
        result = await getFleetInstances();
        break;
      case 'fleet_claims':
        result = await getFleetClaims();
        break;
      case 'fleet_multibox_summary':
        result = await getFleetMultiboxSummary();
        break;
      case 'ci_job_status':
        result = await getCIJobStatus(args);
        break;
      default:
        mcp.writeError(requestId, -32601, `Unknown tool: ${name}`);
        return;
    }

    mcp.writeResult(requestId, {
      content: [
        {
          type: 'text',
          text: JSON.stringify(result, null, 2)
        }
      ]
    });
  } catch (err) {
    mcp.writeError(requestId, -32603, `Tool execution error: ${err.message}`);
  }
}

// ============================================================================
// Main Loop
// ============================================================================

async function main() {
  try {
    while (true) {
      const request = await mcp.readRequest();

      if (!request) {
        continue;
      }

      const { jsonrpc, id, method, params } = request;

      if (jsonrpc !== '2.0') {
        mcp.writeError(id, -32600, 'Invalid JSON-RPC version');
        continue;
      }

      switch (method) {
        case 'initialize':
          await handleInitialize(id, params || {});
          break;
        case 'tools/list':
          await handleToolsList(id, params || {});
          break;
        case 'tools/call':
          await handleToolCall(id, params || {});
          break;
        default:
          mcp.writeError(id, -32601, `Unknown method: ${method}`);
      }
    }
  } catch (err) {
    process.stderr.write(`Fatal error: ${err.message}\n`);
    process.exit(1);
  }
}

main().catch((err) => {
  process.stderr.write(`Fatal error: ${err.message}\n`);
  process.exit(1);
});
