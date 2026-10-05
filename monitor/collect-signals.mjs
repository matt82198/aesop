// Orchestration Refinement Monitor — deterministic signal collector (no LLM).
// Walks known roots and emits a compact BRIEF.md + SIGNALS.json the Haiku monitor reads
// each cycle, so its reasoning stays cheap and focused. Node built-ins only.

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execSync, execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { acquireLock, releaseLock } from '../tools/lock.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// === Configuration ===
// Helper: load aesop.config.json if it exists
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

// Helper: expand ~ and environment variables in paths for portability
function expandPath(pathStr) {
  if (!pathStr) return pathStr;
  // Expand ~ to home directory
  if (pathStr.startsWith('~')) {
    return path.join(os.homedir(), pathStr.slice(1));
  }
  // Expand environment variables like $VAR, $VAR_1, or ${myVar}
  return pathStr.replace(/\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?/g, (match, varName) => {
    return process.env[varName] || match;
  });
}

// Precedence: env var > config file > built-in default
const AESOP_ROOT = process.env.AESOP_ROOT || '.';
const config = loadConfigFile(AESOP_ROOT);

const BRAIN_ROOT = expandPath(
  process.env.BRAIN_ROOT ||
  config.brain_root ||
  path.join(AESOP_ROOT, '..', '.claude')
);

const SCRIPTS_ROOT = expandPath(
  process.env.SCRIPTS_ROOT ||
  config.scripts_root ||
  path.join(AESOP_ROOT, '..', 'scripts')
);

const TEMP_ROOT = expandPath(
  process.env.TEMP_ROOT ||
  config.temp_root ||
  path.join(os.tmpdir(), 'claude')
);

const STATE_DIR = expandPath(
  process.env.AESOP_STATE_ROOT ||
  config.state_root ||
  path.join(AESOP_ROOT, 'state')
);

const FLEET_LEDGER = expandPath(
  process.env.AESOP_FLEET_LEDGER ||
  config.fleet_ledger ||
  path.join(BRAIN_ROOT, 'FLEET-LEDGER.md')
);

const MON = path.join(AESOP_ROOT, 'monitor');

// Config-driven thresholds and feature flags
let repos = [];
let logThresholds = { maxLines: 500, maxKb: 40 };
let extendedSignals = false;

if (config.repos && Array.isArray(config.repos)) {
  repos = config.repos.map(r => r.path);
}
if (config.monitor && config.monitor.log_max_lines) {
  logThresholds.maxLines = config.monitor.log_max_lines;
}
if (config.monitor && config.monitor.log_max_kb) {
  logThresholds.maxKb = config.monitor.log_max_kb;
}

// Precedence: env > config > default
// AESOP_EXTENDED_SIGNALS env var takes precedence
if (process.env.AESOP_EXTENDED_SIGNALS !== undefined) {
  extendedSignals = process.env.AESOP_EXTENDED_SIGNALS === 'true' || process.env.AESOP_EXTENDED_SIGNALS === '1';
} else if (config.monitor && config.monitor.extended_signals !== undefined) {
  extendedSignals = config.monitor.extended_signals;
}
// else default is false (already set above)

const now = Date.now();
const HOUR = 3600e3;
const DAY = 24 * HOUR;

// === Single-instance guard: check own heartbeat at startup ===
// If heartbeat is <300s old and AESOP_MONITOR_FORCE is not explicitly set to 'true' or '1', skip this cycle
// (another instance is running). Match the AESOP_EXTENDED_SIGNALS truthiness pattern.
if (process.env.AESOP_MONITOR_FORCE !== 'true' && process.env.AESOP_MONITOR_FORCE !== '1') {
  const heartbeatPath = path.join(MON, '.monitor-heartbeat');
  try {
    const content = fs.readFileSync(heartbeatPath, 'utf8').trim();
    const epoch = parseInt(content.split('\n')[0], 10);
    if (epoch) {
      const beatAge = now - epoch * 1000;
      const MONITOR_THRESHOLD = 300e3; // 300 seconds
      if (beatAge < MONITOR_THRESHOLD) {
        // Heartbeat is recent; another instance is running. Skip this cycle.
        console.log(`[skip] Monitor already running (heartbeat: ${(beatAge / 1000).toFixed(0)}s ago, threshold: ${MONITOR_THRESHOLD / 1000}s)`);
        process.exit(0);
      }
    }
  } catch {
    // Heartbeat file doesn't exist or is unreadable; proceed with cycle
  }
}

// === Utilities ===
const sh = (cmd, cwd) => {
  try {
    return execSync(cmd, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch {
    return '';
  }
};

const stat = (p) => {
  try {
    return fs.statSync(p);
  } catch {
    return null;
  }
};

const age = (ms) => {
  if (ms < HOUR) return `${Math.round(ms / 60e3)}m`;
  if (ms < DAY) return `${(ms / HOUR).toFixed(1)}h`;
  return `${(ms / DAY).toFixed(1)}d`;
};

const walk = (dir, test, out = [], depth = 0) => {
  if (depth > 6) return out;
  let ents;
  try {
    ents = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const e of ents) {
    const fp = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (/node_modules|\.git|target|\.venv|__pycache__/.test(e.name)) continue;
      walk(fp, test, out, depth + 1);
    } else if (test(e.name, fp)) {
      out.push(fp);
    }
  }
  return out;
};

// === Signal Collectors ===

// 1) Heartbeat check
function checkHeartbeats() {
  const staleLoops = [];
  const beatsDir = path.join(MON, '.heartbeats');
  // Load thresholds from config or use defaults (in seconds; convert to milliseconds)
  const hbThresholds = (config.monitor && config.monitor.heartbeat_thresholds) || {};
  const thresholds = {
    watchdog: (hbThresholds.watchdog !== undefined ? hbThresholds.watchdog : 300) * 1000,
    monitor: (hbThresholds.monitor !== undefined ? hbThresholds.monitor : 3600) * 1000,
    default: (hbThresholds.default !== undefined ? hbThresholds.default : 1800) * 1000,
  };
  let beatFiles = [];
  try {
    beatFiles = fs.readdirSync(beatsDir).map(f => path.join(beatsDir, f));
  } catch {
    // no .heartbeats dir
  }
  const legacyBeats = [
    path.join(MON, '.monitor-heartbeat'),
    path.join(STATE_DIR, '.watchdog-heartbeat'),
  ];
  beatFiles = beatFiles.concat(legacyBeats.filter(f => fs.existsSync(f)));
  for (const fp of beatFiles) {
    try {
      const content = fs.readFileSync(fp, 'utf8').trim();
      const epoch = parseInt(content.split('\n')[0], 10);
      if (!epoch) continue;
      const beatAge = now - epoch * 1000;
      const name = path.basename(fp);
      let threshold = thresholds.default;
      if (name.includes('watchdog')) threshold = thresholds.watchdog;
      if (name.includes('monitor')) threshold = thresholds.monitor;
      if (beatAge > threshold) {
        staleLoops.push({ name, ageMs: beatAge, threshold });
      }
    } catch {
      // skip invalid heartbeat file
    }
  }
  return staleLoops;
}

// 2) Git state check
function checkGitState() {
  const gitState = [];
  for (const repoPath of repos) {
    if (!fs.existsSync(repoPath)) continue;
    const branch = sh('git rev-parse --abbrev-ref HEAD', repoPath);
    const lastCommit = sh('git log -1 --pretty=%h·%cr·%s', repoPath);
    const dirty = sh('git status --porcelain', repoPath)
      .split('\n')
      .filter(l => l.trim()).length;
    const ahead = sh('git rev-list --count @{u}..HEAD', repoPath) || '?';
    gitState.push({
      repo: path.basename(repoPath),
      branch,
      lastCommit,
      dirty,
      ahead,
    });
  }
  return gitState;
}

// 3) Memory freshness check
function checkMemoryFreshness() {
  const memoryDir = path.join(BRAIN_ROOT, 'projects', '*', 'memory');
  const staleMemories = [];
  let memoryCount = 0;
  try {
    const baseDir = path.join(BRAIN_ROOT, 'projects');
    if (!fs.existsSync(baseDir)) return { count: 0, staleMemories: [], staleCount: 0 };
    const projDirs = fs.readdirSync(baseDir, { withFileTypes: true })
      .filter(d => d.isDirectory())
      .map(d => path.join(baseDir, d.name, 'memory'));
    for (const memDir of projDirs) {
      if (!fs.existsSync(memDir)) continue;
      const memFiles = fs.readdirSync(memDir)
        .filter(f => f.endsWith('.md') && f !== 'MEMORY.md' && f !== 'INBOX.md')
        .map(f => path.join(memDir, f));
      memoryCount += memFiles.length;
      for (const fp of memFiles) {
        const st = stat(fp);
        if (st && now - st.mtimeMs > 30 * DAY) {
          staleMemories.push(path.basename(fp));
        }
      }
    }
  } catch {
    // no memory dir
  }
  return { count: memoryCount, staleMemories, staleCount: staleMemories.length };
}

// 4) Log file status check
// Optional: accepts pre-read content for SECURITY-ALERTS.log to avoid redundant read
function checkLogFiles(securityAlertsContent = null) {
  const logFiles = [
    path.join(STATE_DIR, 'FLEET-BACKUP.log'),
    path.join(STATE_DIR, 'SECURITY-ALERTS.log'),
    path.join(MON, 'ACTIONS.log'),
  ];
  const logs = [];
  for (const logPath of logFiles) {
    const st = stat(logPath);
    let sizeKb = 0;
    let lineCount = 0;
    if (st) {
      sizeKb = (st.size / 1024).toFixed(1);
      try {
        // Use pre-read content for SECURITY-ALERTS.log if provided
        const isSecurityAlerts = logPath === path.join(STATE_DIR, 'SECURITY-ALERTS.log');
        const content = isSecurityAlerts && securityAlertsContent !== null
          ? securityAlertsContent
          : fs.readFileSync(logPath, 'utf8');
        lineCount = content.split('\n').filter(l => l.trim()).length;
      } catch {
        // skip
      }
    }
    logs.push({
      name: path.basename(logPath),
      exists: !!st,
      sizeKb,
      lineCount,
      needsRotation: st && (lineCount > logThresholds.maxLines || st.size / 1024 > logThresholds.maxKb),
    });
  }
  return logs;
}

// 5) Junk-script sprawl detection
function detectJunkScripts() {
  if (!fs.existsSync(TEMP_ROOT)) return { total: 0, quarantinable: 0, bytes: 0, oldest: [], recentCount: 0 };
  const sessionDirs = (() => {
    try {
      return fs.readdirSync(TEMP_ROOT, { withFileTypes: true })
        .filter(d => d.isDirectory())
        .map(d => path.join(TEMP_ROOT, d.name));
    } catch {
      return [];
    }
  })();
  const liveDirs = new Set();
  for (const root of sessionDirs) {
    const files = walk(root, () => true);
    const newest = Math.max(0, ...files.map(fp => (stat(fp) || { mtimeMs: 0 }).mtimeMs));
    if (now - newest < 2 * HOUR) {
      liveDirs.add(root.replace(/\\/g, '/'));
    }
  }
  const inLiveDir = (fp) => [...liveDirs].some(d => fp.replace(/\\/g, '/').startsWith(d));
  const tempScripts = walk(TEMP_ROOT, n => /\.(py|mjs|js)$/.test(n))
    .map(fp => ({ fp, st: stat(fp) }))
    .filter(x => x.st)
    .map(x => ({
      fp: x.fp,
      ageMs: now - x.st.mtimeMs,
      size: x.st.size,
      quarantinable: now - x.st.mtimeMs > DAY && !inLiveDir(x.fp),
    }));
  const junk = {
    total: tempScripts.length,
    quarantinable: tempScripts.filter(x => x.quarantinable).length,
    bytes: tempScripts.reduce((a, x) => a + x.size, 0),
    oldest: tempScripts
      .sort((a, b) => b.ageMs - a.ageMs)
      .slice(0, 8)
      .map(x => `${age(x.ageMs)} ${x.fp.split(/[/\\]/).slice(-2).join('/')}`),
    recentCount: tempScripts.filter(x => now - x.ageMs < HOUR).length,
    // Store all temp scripts for AUTO quarantine action
    _scripts: tempScripts,
  };
  return junk;
}

// 6) Stray scripts in repo roots
function detectStrayRepoScripts() {
  const strayRepo = [];
  for (const repoPath of repos) {
    if (!fs.existsSync(repoPath)) continue;
    const recent = sh('git log --since="7 days ago" --name-only --pretty=format: --diff-filter=A', repoPath)
      .split('\n')
      .map(s => s.trim())
      .filter(Boolean);
    for (const f of new Set(recent)) {
      // Normalize git-output paths to forward slashes immediately after read
      const normalized = f.replace(/\\/g, '/');
      if (/^[^/]+\.(py|mjs|js|sql)$/.test(normalized)) {
        strayRepo.push(`${path.basename(repoPath)}: ${normalized}`);
      }
    }
  }
  return strayRepo;
}

// 7) Security alert review
// Optional: accepts pre-read content to avoid redundant file read
function checkSecurityAlerts(securityAlertsContent = null) {
  const alertLog = path.join(STATE_DIR, 'SECURITY-ALERTS.log');
  const st = stat(alertLog);
  if (!st) return { count: 0, highMedCount: 0 };
  try {
    // Use pre-read content if provided, otherwise read file
    const content = securityAlertsContent !== null ? securityAlertsContent : fs.readFileSync(alertLog, 'utf8');
    const lines = content.split('\n');
    const highMedCount = lines.filter(l => /HIGH|MED/.test(l) && !l.includes('SUPPRESSED-FP')).length;
    return { count: lines.filter(l => l.trim()).length, highMedCount };
  } catch {
    return { count: 0, highMedCount: 0 };
  }
}

// === Cursor tracking utilities (for ledger incremental read) ===
// Helper: Load cursor state (byte offset + line hash of last processed line)
function loadCursor() {
  const cursorPath = path.join(MON, '.ledger-cursor.json');
  try {
    if (fs.existsSync(cursorPath)) {
      return JSON.parse(fs.readFileSync(cursorPath, 'utf8'));
    }
  } catch {
    // Parse error; treat as missing cursor
  }
  return { byteOffset: 0, lineHash: '' };
}

// Helper: Save cursor state (byte offset + line hash)
function saveCursor(byteOffset, lineHash) {
  const cursorPath = path.join(MON, '.ledger-cursor.json');
  try {
    fs.mkdirSync(MON, { recursive: true });
    fs.writeFileSync(cursorPath, JSON.stringify({ byteOffset, lineHash }, null, 2), 'utf8');
  } catch (e) {
    // Fail-open: log warning but don't crash
    console.error(`Warning: Failed to save ledger cursor: ${e.message}`);
  }
}

// Helper: Compute a simple hash of a string (for integrity check)
function simpleHash(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) - h) + str.charCodeAt(i);
    h |= 0; // Convert to 32-bit integer
  }
  return Math.abs(h).toString(16);
}

// 8) Respawn watch (Rule 6 retry cap) — incremental read with cursor
function detectRespawnWatch() {
  const respawnWatch = [];
  if (!fs.existsSync(FLEET_LEDGER)) return respawnWatch;

  try {
    const buffer = fs.readFileSync(FLEET_LEDGER);
    const content = buffer.toString('utf8');

    // Load cursor to find starting point
    const cursor = loadCursor();
    let startOffset = cursor.byteOffset;

    // If cursor points beyond file, reset to start (file was truncated or rotated)
    if (startOffset > buffer.length) {
      startOffset = 0;
    }

    // Extract only new content since last cursor position
    const newContent = content.substring(startOffset);

    // Split new content into lines, filtering empty and header lines
    // Keep data rows (start with | and contain data), skip header rows (| --- | or column names)
    const newLines = newContent.split('\n')
      .filter(l => {
        const trimmed = l.trim();
        if (!trimmed) return false; // Skip empty lines
        // Skip header separator rows (contain --- between pipes)
        if (/\|\s*---/.test(trimmed)) return false;
        // Skip column name row (has 'timestamp' or 'agent' or 'dispatch' or 'description')
        if (trimmed.includes('timestamp') && trimmed.includes('agent')) return false;
        // Keep all other rows starting with |
        return trimmed.startsWith('|');
      });

    // If no new lines, return early (nothing to process)
    if (newLines.length === 0) {
      return respawnWatch;
    }

    // Process new lines to detect respawn violations
    const windowSize = 50;
    const recentLines = newLines.slice(Math.max(0, newLines.length - windowSize));
    const signatures = {};
    const normalize = (desc) => (desc || '').substring(0, 40).toLowerCase().trim();

    for (const line of recentLines) {
      const parts = line.split('|').map(s => s.trim());
      if (parts.length >= 4) {
        const description = parts[3];
        const sig = normalize(description);
        if (sig) signatures[sig] = (signatures[sig] || 0) + 1;
      }
    }

    // Check for violations (count > 3)
    for (const [sig, count] of Object.entries(signatures)) {
      if (count > 3) {
        respawnWatch.push({
          signature: sig,
          count,
          warning: `RESPAWN CAP: '${sig}' dispatched ${count} times — investigate hang or mark BLOCKED`,
        });
      }
    }

    // Update cursor to end of file
    const lastLine = newLines[newLines.length - 1] || '';
    const newByteOffset = buffer.length;
    const newLineHash = simpleHash(lastLine);
    saveCursor(newByteOffset, newLineHash);
  } catch (e) {
    // Fail-open on error
    console.error(`Warning: Failed to read FLEET-LEDGER: ${e.message}`);
  }

  return respawnWatch;
}

// 9) Cost cadence tracking
function trackCostCadence() {
  const prevStateFile = path.join(MON, '.signal-state.json');
  let prevState = {};

  // Check file existence first; don't silently fail on parse errors
  if (fs.existsSync(prevStateFile)) {
    try {
      prevState = JSON.parse(fs.readFileSync(prevStateFile, 'utf8'));
    } catch (e) {
      // Parse failure: log warning and preserve a .corrupt copy for evidence
      console.error(`Warning: Failed to parse .signal-state.json: ${e.message}`);
      try {
        const corruptPath = prevStateFile + '.corrupt';
        const content = fs.readFileSync(prevStateFile, 'utf8');
        fs.writeFileSync(corruptPath, content, 'utf8');
        console.error(`Corrupt state preserved to ${corruptPath}`);
      } catch (copyErr) {
        console.error(`Failed to preserve corrupt state: ${copyErr.message}`);
      }
      // Reset to empty state and continue (graceful recovery)
      prevState = {};
    }
  }

  const cycleCount = (prevState.cycleCount || 0) + 1;
  let costTick = null;
  if (cycleCount % 3 === 0) {
    costTick = {
      cycle: cycleCount,
      ts: new Date(now).toISOString(),
      summary: 'Cost cycle tick recorded',
    };
  }
  return { cycleCount, costTick };
}

// 10) Unreviewed prompts count
function checkUnreviewedPrompts() {
  const seenFile = path.join(MON, '.fleet-prompts-seen.json');
  let prevSeen = {};
  try {
    prevSeen = JSON.parse(fs.readFileSync(seenFile, 'utf8'));
  } catch {
    // no prev state
  }
  // Without fleet_prompt_extractor.py available, emit 0
  // In production, invoke spawnSync('python', [...fleet_prompt_extractor.py...])
  return 0;
}

// 11) Isolation violation detection (rec #5: git-tracked dirs don't flag)
// Detects worktrees created INSIDE repo root (violation) vs sanctioned ../sibling-wt-* (OK)
// FP fix: git-tracked source directories (new modules) do NOT flag as violations
function detectIsolationViolations() {
  if (!fs.existsSync(AESOP_ROOT)) return { violations: [], count: 0 };

  const violations = [];
  const trackedFiles = new Set();

  // Get list of git-tracked files to exclude legitimate source dirs
  try {
    const tracked = sh('git ls-files', AESOP_ROOT);
    tracked.split('\n').forEach(f => {
      if (f) {
        const dir = f.split('/')[0];
        trackedFiles.add(dir);
      }
    });
  } catch {
    // If git ls-files fails, proceed without tracked set (less precise but fail-open)
  }

  // Walk AESOP_ROOT (shallow) looking for dirs with nested .git
  try {
    const entries = fs.readdirSync(AESOP_ROOT, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      if (entry.name.startsWith('.')) continue;
      if (entry.name === 'node_modules') continue;

      const dirPath = path.join(AESOP_ROOT, entry.name);
      const gitPath = path.join(dirPath, '.git');

      // Check if this directory has a .git file or .git dir (worktree indicator)
      try {
        const gitStat = fs.statSync(gitPath);
        // It has .git — check if it's a worktree (contains "gitdir:" or is a symlink)
        const isWorktree = gitStat.isFile() || gitStat.isSymbolicLink();

        if (isWorktree) {
          // This looks like a worktree. Check if it's git-tracked (FP fix)
          const isTracked = trackedFiles.has(entry.name);

          if (!isTracked) {
            // Untracked worktree inside repo root = VIOLATION
            violations.push({
              path: entry.name,
              type: 'untracked-worktree-in-root',
              message: `Worktree '${entry.name}' found inside repo root (untracked). Should use sanctioned ../aesop-wt-* sibling instead.`,
            });
          }
          // If tracked, it's a legitimate source directory; don't flag
        }
      } catch (e) {
        // Entry doesn't have .git or is inaccessible; skip
      }
    }
  } catch (e) {
    // Directory read failed; fail-open with no violations
  }

  return { violations, count: violations.length };
}

// 11b) Main CI status polling
// Calls `gh run list` to get latest main-full workflow run status; never throws
function checkMainCiStatus() {
  try {
    // Execute gh command to get latest main-full workflow run
    const result = execSync(
      'gh run list --workflow=main-full.yml --branch main --limit 1 --json status,conclusion,headSha,url',
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }
    ).trim();

    if (!result) {
      // No runs found (workflow may not have run yet)
      return {
        state: 'unknown',
        reason: 'No main-full workflow runs found',
        checked_at: new Date(now).toISOString(),
      };
    }

    const runs = JSON.parse(result);
    if (!Array.isArray(runs) || runs.length === 0) {
      return {
        state: 'unknown',
        reason: 'No main-full workflow runs found',
        checked_at: new Date(now).toISOString(),
      };
    }

    const latestRun = runs[0];
    const status = latestRun.status || '';
    const conclusion = latestRun.conclusion || '';
    const sha = latestRun.headSha || '';
    const url = latestRun.url || '';

    // Map status and conclusion to state
    let state = 'unknown';
    if (status === 'completed') {
      if (conclusion === 'success') {
        state = 'pass';
      } else if (conclusion === 'failure') {
        state = 'fail';
      } else {
        state = 'unknown';
      }
    } else if (status === 'in_progress') {
      state = 'running';
    } else {
      state = 'unknown';
    }

    return {
      state,
      sha,
      url,
      checked_at: new Date(now).toISOString(),
    };
  } catch (e) {
    // gh command failed, missing, or unauthed — return unknown gracefully
    return {
      state: 'unknown',
      reason: e.message || 'gh command unavailable or failed',
      checked_at: new Date(now).toISOString(),
    };
  }
}

// 12) Agent stall detection
// Calls tools/stall_check.py --json with a bounded timeout.
// FAIL-CLOSED (GAP 3): an error running the check is NOT silence and NOT a
// clean bill of health. Every failure path emits state:'UNKNOWN' with
// degraded:true and count:null so no consumer can read a broken check as
// "0 stalls". Only a check that actually ran yields state 'OK'/'STALLED'.
function degradedStallSignal(reason) {
  return {
    available: false,
    state: 'UNKNOWN',
    degraded: true,
    count: null,
    total: null,
    reason,
    summary: `UNKNOWN (stall check did not run): ${reason}`,
    stalls: [],
  };
}

function checkAgentStalls() {
  let stallCheckPy = path.join(path.dirname(MON), 'tools', 'stall_check.py');

  // Fallback: look in SCRIPTS_ROOT if not found in tools
  if (!fs.existsSync(stallCheckPy)) {
    stallCheckPy = path.join(SCRIPTS_ROOT, 'stall_check.py');
  }

  // Fallback: look in the actual aesop source directory (for tests/CI environments)
  if (!fs.existsSync(stallCheckPy)) {
    const realMonitorCharter = path.join(__dirname, 'CHARTER.md');
    if (fs.existsSync(realMonitorCharter)) {
      stallCheckPy = path.join(__dirname, '..', 'tools', 'stall_check.py');
    }
  }

  if (!fs.existsSync(stallCheckPy)) {
    return degradedStallSignal('stall_check.py not found');
  }

  try {
    // Invoke stall_check.py with a bounded timeout (fail-closed on any failure)
    const result = spawnSync('python', [stallCheckPy, '--json'], {
      encoding: 'utf8',
      timeout: 5000,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    if (result.error) {
      const spawnReason = result.error.code === 'ETIMEDOUT'
        ? 'stall_check.py timed out after 5000ms'
        : `stall_check.py could not be spawned: ${result.error.message}`;
      return degradedStallSignal(spawnReason);
    }
    if (result.signal) {
      return degradedStallSignal(`stall_check.py killed by signal ${result.signal}`);
    }
    if (result.status !== 0) {
      const stderrTail = (result.stderr || '').trim().split('\n').slice(-1)[0] || '';
      return degradedStallSignal(
        `stall_check.py exited ${result.status}${stderrTail ? `: ${stderrTail}` : ''}`);
    }

    let stalls;
    try {
      stalls = JSON.parse(result.stdout || '');
    } catch (parseErr) {
      return degradedStallSignal(`stall_check.py output was not JSON: ${parseErr.message}`);
    }
    if (!Array.isArray(stalls)) {
      return degradedStallSignal('stall_check.py output was not a JSON array');
    }

    const stalledCount = stalls.filter(s => s.verdict && ['stale', 'dead'].includes(s.verdict)).length;

    return {
      available: true,
      state: stalledCount > 0 ? 'STALLED' : 'OK',
      degraded: false,
      count: stalledCount,
      total: stalls.length,
      reason: '',
      summary: stalledCount > 0 ? `${stalledCount} agent(s) stalled` : 'No stalls detected',
      stalls: stalls,
    };
  } catch (e) {
    return degradedStallSignal(`unexpected error: ${e.message}`);
  }
}

// === AUTO Actions ===
// Log rotation: invoke rotate_logs.py if available and log needs rotation
function performAutoLogRotation(logFiles, actionsLogPath) {
  // rotate_logs.py is in tools directory (sibling to monitor)
  let rotateLogsPy = path.join(path.dirname(MON), 'tools', 'rotate_logs.py');

  // Fallback: look in SCRIPTS_ROOT if not found in tools
  if (!fs.existsSync(rotateLogsPy)) {
    rotateLogsPy = path.join(SCRIPTS_ROOT, 'rotate_logs.py');
  }

  // Fallback: look in the actual aesop source directory (for tests/CI environments)
  if (!fs.existsSync(rotateLogsPy)) {
    // Try to find the real aesop tools directory by looking for the real monitor/CHARTER.md
    const realMonitorCharter = path.join(__dirname, 'CHARTER.md');
    if (fs.existsSync(realMonitorCharter)) {
      rotateLogsPy = path.join(__dirname, '..', 'tools', 'rotate_logs.py');
    }
  }

  if (!fs.existsSync(rotateLogsPy)) {
    // rotate_logs.py not available; skip (fail-open per CHARTER.md)
    return [];
  }

  const rotatedLogs = [];
  const logsNeedingRotation = logFiles.filter(l => l.needsRotation);

  for (const log of logsNeedingRotation) {
    if (!log.exists) continue;
    let logPath;
    if (log.name === 'ACTIONS.log') {
      logPath = path.join(MON, log.name);
    } else {
      logPath = log.name.startsWith('/') ? log.name : path.join(STATE_DIR, log.name);
    }

    try {
      // Invoke rotate_logs.py with thresholds from config
      // Use execFileSync instead of execSync for command injection protection (no shell)
      execFileSync('python', [
        rotateLogsPy,
        logPath,
        '--max-lines',
        String(logThresholds.maxLines),
        '--max-bytes',
        String(Math.floor(logThresholds.maxKb * 1024)),
      ], { stdio: ['ignore', 'pipe', 'pipe'] });
      rotatedLogs.push(log.name);

      // Log the AUTO action
      const timestamp = new Date(now).toISOString();
      fs.appendFileSync(actionsLogPath, `[${timestamp}] AUTO action: Log rotation invoked for ${log.name}\n`, 'utf8');
    } catch (e) {
      // Log rotation failed; fail-open (log the error but continue)
      const timestamp = new Date(now).toISOString();
      fs.appendFileSync(actionsLogPath, `[${timestamp}] AUTO action FAILED: Log rotation for ${log.name}: ${e.message}\n`, 'utf8');
    }
  }

  return rotatedLogs;
}

// Junk quarantine: move old temp scripts to monitor/quarantine/ with manifest
function performAutoJunkQuarantine(junkScripts, quarantineDir, manifestPath) {
  if (!Array.isArray(junkScripts) || junkScripts.length === 0) {
    return { quarantined: 0 };
  }

  let quarantinedCount = 0;
  const manifestLines = [];

  // Read existing manifest if it exists
  let existingManifest = '';
  if (fs.existsSync(manifestPath)) {
    try {
      existingManifest = fs.readFileSync(manifestPath, 'utf8');
    } catch {
      // Ignore read errors
    }
  }

  // Create quarantine directory if needed
  try {
    fs.mkdirSync(quarantineDir, { recursive: true });
  } catch {
    // Directory creation failed; skip quarantine
    return { quarantined: 0 };
  }

  // Quarantine each old junk script
  for (const junkItem of junkScripts) {
    if (junkItem.quarantinable && fs.existsSync(junkItem.fp)) {
      try {
        const basename = path.basename(junkItem.fp);
        const quarantinePath = path.join(quarantineDir, basename);

        // Copy (not move) to quarantine to avoid issues with long paths or multiple instances
        fs.copyFileSync(junkItem.fp, quarantinePath);

        // Record in manifest
        const timestamp = new Date(now).toISOString();
        const manifestLine = `${timestamp}\t${basename}\t${junkItem.fp}\t${junkItem.size}\tbytes\n`;
        manifestLines.push(manifestLine);

        quarantinedCount++;
      } catch (e) {
        // Quarantine failed for this item; continue with others
      }
    }
  }

  // Append new entries to manifest
  if (manifestLines.length > 0) {
    try {
      if (!existingManifest) {
        // Write header
        fs.writeFileSync(manifestPath, 'timestamp\tfilename\tsource_path\tsize_bytes\tunit\n', 'utf8');
      }
      fs.appendFileSync(manifestPath, manifestLines.join(''), 'utf8');
    } catch {
      // Manifest write failed; continue
    }
  }

  return { quarantined: quarantinedCount };
}

// === Locking utilities (imported from tools/lock.mjs with fail-closed behavior) ===
// acquireLock now throws on timeout (fail-closed) instead of proceeding unlocked (P0 wave-8 fix)
// For emitProposal (monitor context), we wrap it to log errors and skip emission rather than crashing the cycle
function safeAcquireLock(proposalsFile) {
  try {
    return acquireLock(proposalsFile);
  } catch (e) {
    // Lock acquisition failed; log error and return null
    // Caller (emitProposal) will skip emission in this case
    console.error(`Warning: ${e.message}; skipping proposal emission for this cycle`);
    return null;
  }
}


// === Proposal Emission ===
// Append PROPOSE-tier signals to monitor/PROPOSALS.md (idempotent per signal key, with atomic locking)
function emitProposal(signalKey, problem, suggestedChange) {
  const proposalsPath = path.join(MON, 'PROPOSALS.md');
  const timestamp = new Date(now).toISOString();

  // Acquire lock before read-check-append
  const lockDir = safeAcquireLock(proposalsPath);

  // If lock acquisition failed (fail-closed), skip emission for this cycle
  if (!lockDir) {
    // On lock timeout, append a one-line MISSED-PROPOSAL record to ACTIONS.log (append-only)
    // so the condition surfaces next cycle
    const actionsLogPath = path.join(MON, 'ACTIONS.log');
    try {
      fs.appendFileSync(actionsLogPath, `[${timestamp}] MISSED-PROPOSAL: ${signalKey} (lock timeout)\n`, 'utf8');
    } catch (e) {
      // Fail-open: ignore write errors to ACTIONS.log
    }
    return;
  }

  try {
    // Read existing proposals to check for duplicate
    let existingContent = '';
    try {
      existingContent = fs.readFileSync(proposalsPath, 'utf8');
    } catch {
      // File doesn't exist yet; start fresh
      if (!fs.existsSync(MON)) {
        fs.mkdirSync(MON, { recursive: true });
      }
    }

    // Check if this signal key already has an entry (idempotency check)
    if (existingContent.includes(`**Signal:** ${signalKey}`)) {
      // Entry already exists; skip to avoid duplicates
      return;
    }

    // Append new proposal entry
    const proposal = `
## ${signalKey} — ${timestamp}

**Signal:** ${signalKey}

**Problem:**
${problem}

**Suggested change:**
${suggestedChange}

---
`;

    try {
      fs.appendFileSync(proposalsPath, proposal, 'utf8');
    } catch (e) {
      // Fail-open: log to BRIEF instead of crashing
      console.error(`Failed to write PROPOSALS.md: ${e.message}`);
    }
  } finally {
    releaseLock(lockDir);
  }
}

// === Main ===
// Read SECURITY-ALERTS.log once and share the content with both consumers to avoid redundant file read
let securityAlertsContent = null;
const securityAlertsPath = path.join(STATE_DIR, 'SECURITY-ALERTS.log');
try {
  if (fs.existsSync(securityAlertsPath)) {
    securityAlertsContent = fs.readFileSync(securityAlertsPath, 'utf8');
  }
} catch {
  // File exists but is unreadable; consumers will handle gracefully
}

const staleLoops = checkHeartbeats();
const gitState = checkGitState();
const memory = checkMemoryFreshness();
const logFiles = checkLogFiles(securityAlertsContent);
const isolationViolations = detectIsolationViolations();
const mainCi = checkMainCiStatus();
const agentStalls = checkAgentStalls();

// Extended signal checks (5, 6, 8, 10) — skipped if extended_signals is OFF
const junk = extendedSignals ? detectJunkScripts() : { skipped: true };
const strayRepo = extendedSignals ? detectStrayRepoScripts() : { skipped: true };

const alerts = checkSecurityAlerts(securityAlertsContent);

const respawnWatch = extendedSignals ? detectRespawnWatch() : { skipped: true };
const { cycleCount, costTick } = trackCostCadence();
const unreviewedPrompts = extendedSignals ? checkUnreviewedPrompts() : { skipped: true };

// === Perform AUTO Actions ===
// (Executed before emitting signals, so outputs reflect actions taken)
const actionsLogPath = path.join(MON, 'ACTIONS.log');

// Ensure MON directory and ACTIONS.log exist
try {
  fs.mkdirSync(MON, { recursive: true });
  if (!fs.existsSync(actionsLogPath)) {
    fs.writeFileSync(actionsLogPath, '', 'utf8');
  }
} catch {
  // Ignore directory creation errors
}

// AUTO: Log rotation
performAutoLogRotation(logFiles, actionsLogPath);

// AUTO: Junk quarantine (only run if junk check was not skipped)
const quarantineDir = path.join(MON, 'quarantine');
const manifestPath = path.join(quarantineDir, 'MANIFEST.tsv');
if (!junk.skipped && junk._scripts && junk.quarantinable > 0) {
  performAutoJunkQuarantine(junk._scripts, quarantineDir, manifestPath);
}

const signals = {
  timestamp: new Date(now).toISOString(),
  cycleCount,
  heartbeats: { staleCount: staleLoops.length, details: staleLoops },
  git: gitState,
  memory,
  logs: logFiles,
  junk,
  strayRepo,
  alerts,
  respawnWatch,
  costTick,
  unreviewedPrompts,
  isolationViolations,
  main_ci: mainCi,
  agentStalls,
};

const brief = [];
brief.push(`# Aesop Monitor Brief — ${signals.timestamp}`);
brief.push('');
brief.push('## Heartbeat check');
if (staleLoops.length === 0) {
  brief.push('✓ All heartbeats fresh (watchdog 300s, monitor 3600s, others 1800s).');
} else {
  brief.push(`✗ **${staleLoops.length} stale loop(s)** detected:`);
  for (const sl of staleLoops) {
    brief.push(`  - ${sl.name}: ${age(sl.ageMs)} (threshold: ${age(sl.threshold)})`);
  }
}
brief.push('');

brief.push('## Git state');
if (gitState.length === 0) {
  brief.push('No repos configured.');
} else {
  for (const g of gitState) {
    const status = g.dirty === 0 && g.ahead === '0' ? '✓' : '⚠';
    brief.push(`- ${status} **${g.repo}** [${g.branch}] — dirty: ${g.dirty}, ahead: ${g.ahead}`);
    if (g.lastCommit) brief.push(`  ${g.lastCommit}`);
  }
}
brief.push('');

brief.push('## Memory');
brief.push(`- ${memory.count} memory file(s)`);
if (memory.staleCount > 0) {
  brief.push(`  ⚠ **${memory.staleCount} stale** (>30d): ${memory.staleMemories.join(', ')}`);
}
brief.push('');

brief.push('## Log files');
const needsRotation = logFiles.filter(l => l.needsRotation);
if (needsRotation.length === 0) {
  brief.push('✓ All logs within thresholds.');
} else {
  brief.push(`⚠ **${needsRotation.length} log(s) need rotation:**`);
  for (const l of needsRotation) {
    brief.push(`  - ${l.name}: ${l.lineCount} lines, ${l.sizeKb}kb`);
  }
}
brief.push('');

brief.push('## Isolation violations');
if (isolationViolations.count === 0) {
  brief.push('✓ No worktrees detected inside repo root (sanctioned siblings OK).');
} else {
  brief.push(`🚨 **${isolationViolations.count} isolation violation(s)** detected (worktrees inside repo root):`);
  for (const v of isolationViolations.violations) {
    brief.push(`  - ${v.path}: ${v.message}`);
  }
}
brief.push('');

brief.push('## Agent stalls');
if (!agentStalls.available) {
  // Fail-closed: a check that did not run is a warning, never a quiet skip.
  brief.push(`🚨 **UNKNOWN — stall detection DEGRADED**: ${agentStalls.reason || agentStalls.summary}`);
  brief.push('  Liveness is undetermined; treat as unverified, not as "no stalls".');
} else if (agentStalls.count === 0) {
  brief.push('✓ No agent stalls detected.');
} else {
  brief.push(`🚨 **${agentStalls.count} agent(s) stalled** (${agentStalls.total} total scanned):`);
  for (const stall of agentStalls.stalls) {
    if (['stale', 'dead'].includes(stall.verdict)) {
      brief.push(`  - ${stall.agent}: ${stall.verdict} (${stall.mtime_age_s}s old)`);
    }
  }
}
brief.push('');

brief.push('## Main CI status (main-full workflow)');
if (mainCi.state === 'pass') {
  brief.push('✓ Latest main-full run passed.');
} else if (mainCi.state === 'fail') {
  brief.push(`🚨 **Latest main-full run FAILED:**`);
  if (mainCi.sha) brief.push(`  SHA: ${mainCi.sha}`);
  if (mainCi.url) brief.push(`  URL: ${mainCi.url}`);
} else if (mainCi.state === 'running') {
  brief.push('⏳ Main-full workflow currently running...');
  if (mainCi.url) brief.push(`  URL: ${mainCi.url}`);
} else {
  brief.push(`⚠ Main-full status unknown: ${mainCi.reason || 'no data available'}`);
}
brief.push('');

// Extended signals section (if disabled, just note they're off; if enabled, show details)
if (extendedSignals) {
  brief.push('## Junk-script sprawl (temp/scratch)');
  brief.push(`- ${junk.total} total scripts, ${(junk.bytes / 1024).toFixed(0)}kb`);
  brief.push(`  Quarantinable (>24h, not live): ${junk.quarantinable}`);
  if (junk.oldest.length > 0) {
    brief.push('  Oldest:');
    for (const o of junk.oldest) {
      brief.push(`    ${o}`);
    }
  }
  if (strayRepo.length > 0) {
    brief.push('');
    brief.push('## Stray repo scripts (7d)');
    for (const s of strayRepo) {
      brief.push(`- ${s}`);
    }
  }
  brief.push('');
} else {
  brief.push('## Extended signal checks');
  brief.push('Checks 5 (junk-script sprawl), 6 (stray-repo scripts), 8 (respawn-watch), 10 (unreviewed-prompts) are **extended (off)** — enable via `monitor.extended_signals: true` in aesop.config.json or `AESOP_EXTENDED_SIGNALS=true`.');
  brief.push('');
}

brief.push('## Security');
brief.push(`- Alert log: ${alerts.count} entries, ${alerts.highMedCount} HIGH/MED`);
brief.push('');

// Respawn watch (check 8 — extended)
if (extendedSignals) {
  brief.push('## Respawn watch (Rule 6 retry cap)');
  if (respawnWatch.length === 0) {
    brief.push('✓ No retry-cap breaches (all signatures ≤3 occurrences).');
  } else {
    brief.push(`⚠ **${respawnWatch.length} signature(s) exceeded 3-attempt limit:**`);
    for (const rw of respawnWatch) {
      brief.push(`  - ${rw.warning}`);
    }
    brief.push('  (Note: distinguish legitimate fan-outs from identical retries; manual review recommended.)');
  }
  brief.push('');
}

brief.push('## Cost tracking');
brief.push(`- Cycle: ${cycleCount}${costTick ? ' — tick recorded' : ''}`);
if (costTick) {
  brief.push(`  ${costTick.ts}`);
}
brief.push('');

// Unreviewed prompts (check 10 — extended)
if (extendedSignals) {
  brief.push('## Unreviewed prompts');
  brief.push(`- ${unreviewedPrompts} new prompt(s) awaiting semantic review`);
  brief.push('');
}

brief.push('_Refinement points → act per CHARTER.md (AUTO safe, PROPOSE rule changes). Goal is fixed._');

// === Emit PROPOSE-tier proposals ===
// Only emit for signals that warrant user review per CHARTER.md action tiers

// Proposals for extended checks (only if extended_signals is ON)
if (extendedSignals) {
  if (respawnWatch.length > 0) {
    emitProposal(
      'respawn-watch-breach',
      `Rule 6 retry cap breached: ${respawnWatch.length} agent signature(s) appeared >3 times in recent spawn history. This indicates either an intentional parallel fan-out or a hung-agent loop.`,
      `Review FLEET-LEDGER.md to distinguish legitimate concurrent spawns from identical retries. If retries are unintentional, investigate root cause and add guardrails to prevent re-dispatch. Consider updating monitoring thresholds or retry strategy.`
    );
  }

  if (strayRepo.length > 0) {
    emitProposal(
      'stray-repo-scripts',
      `${strayRepo.length} script file(s) committed to repo root in past 7 days: ${strayRepo.join(', ')}. Scripts should live in dedicated src/ or scripts/ paths, not repo root.`,
      `Move stray scripts to proper paths per project discipline. Update CONTRIBUTING.md if repo structure is ambiguous. Add pre-commit hook or CI check to enforce.`
    );
  }
}

// Core proposals (always emitted)
if (isolationViolations.count > 0) {
  emitProposal(
    'isolation-violation-detected',
    `${isolationViolations.count} worktree(s) detected inside repo root. Agents should create worktrees as sanctioned siblings (../aesop-wt-*), not inside the repo root to maintain isolation.`,
    `Review the violation(s): ${isolationViolations.violations.map(v => v.path).join(', ')}. Move worktrees to ../aesop-wt-<name> location outside repo root. Add pre-commit hook or monitoring to prevent future in-root worktrees.`
  );
}

if (alerts.highMedCount > 0) {
  emitProposal(
    'security-alerts-high-med',
    `${alerts.highMedCount} HIGH/MED security alert(s) in SECURITY-ALERTS.log. These may indicate real vulnerabilities, credential exposure, or false positives requiring review.`,
    `Review each HIGH/MED entry in SECURITY-ALERTS.log. Distinguish real issues (fix immediately) from false positives (mark SUPPRESSED-FP). Update scanning rules if needed to reduce noise.`
  );
}

if (memory.staleCount > 0) {
  emitProposal(
    'stale-memory-files',
    `${memory.staleCount} memory file(s) older than 30 days: ${memory.staleMemories.join(', ')}. Stale memory may indicate obsolete project context or abandoned projects.`,
    `Review stale memory files in keeper. Consolidate, archive, or delete per project lifecycle. Update memory refresh schedule if projects are active but infrequently updated.`
  );
}

// Helper: Atomic rename with EPERM/EBUSY retry and cleanup on failure
function atomicRename(tmpPath, targetPath) {
  const maxRetries = 5;
  const baseDelayMs = 50;

  for (let i = 0; i < maxRetries; i++) {
    try {
      fs.renameSync(tmpPath, targetPath);
      return true; // Success
    } catch (e) {
      if ((e.code === 'EPERM' || e.code === 'EBUSY') && i < maxRetries - 1) {
        // Retry on Windows EPERM or EBUSY (file held by reader)
        const delayMs = baseDelayMs * (i + 1); // Exponential backoff: 50ms, 100ms, 150ms, 200ms
        const start = Date.now();
        while (Date.now() - start < delayMs) {
          // Busy-wait to avoid scheduling overhead
        }
      } else {
        // Final failure or non-retryable error; clean up .tmp file and return false
        try {
          fs.unlinkSync(tmpPath);
        } catch {
          // Cleanup failed; best effort
        }
        return false;
      }
    }
  }

  // Final failure after all retries; clean up and return false
  try {
    fs.unlinkSync(tmpPath);
  } catch {
    // Cleanup failed; best effort
  }
  return false;
}

// Write outputs atomically with per-file retry on EPERM
try {
  fs.mkdirSync(MON, { recursive: true });

  // Atomic write for BRIEF.md: write to .tmp, then rename with retry
  const briefPath = path.join(MON, 'BRIEF.md');
  const briefTmpPath = briefPath + '.tmp';
  let briefSuccess = false;
  try {
    fs.writeFileSync(briefTmpPath, brief.join('\n'), 'utf8');
    briefSuccess = atomicRename(briefTmpPath, briefPath);
    if (!briefSuccess) {
      console.error(`Warning: Failed to write BRIEF.md after retries; keeping prior file`);
    }
  } catch (e) {
    console.error(`Warning: Failed to write BRIEF.md: ${e.message}`);
  }

  // Atomic write for SIGNALS.json: write to .tmp, then rename with retry
  const signalsPath = path.join(MON, 'SIGNALS.json');
  const signalsTmpPath = signalsPath + '.tmp';
  let signalsSuccess = false;
  try {
    fs.writeFileSync(signalsTmpPath, JSON.stringify(signals, null, 2), 'utf8');
    signalsSuccess = atomicRename(signalsTmpPath, signalsPath);
    if (!signalsSuccess) {
      console.error(`Warning: Failed to write SIGNALS.json after retries; keeping prior file`);
    }
  } catch (e) {
    console.error(`Warning: Failed to write SIGNALS.json: ${e.message}`);
  }

  // Always write heartbeat and signal state (these use direct write, no rename)
  try {
    fs.writeFileSync(path.join(MON, '.monitor-heartbeat'), String(Math.floor(now / 1000)), 'utf8');
  } catch (e) {
    console.error(`Warning: Failed to write heartbeat: ${e.message}`);
  }
  try {
    fs.writeFileSync(path.join(MON, '.signal-state.json'), JSON.stringify({ cycleCount }, null, 2), 'utf8');
  } catch (e) {
    console.error(`Warning: Failed to write signal state: ${e.message}`);
  }

  // Normalize skipped signals to 0 for summary display
  const junkQuarantinable = junk.skipped ? 0 : (junk.quarantinable || 0);
  const strayRepoCount = strayRepo.skipped ? 0 : (Array.isArray(strayRepo) ? strayRepo.length : 0);
  const respawnWatchCount = respawnWatch.skipped ? 0 : (Array.isArray(respawnWatch) ? respawnWatch.length : 0);

  const summaryLine = `stale-loops: ${staleLoops.length}, repos-dirty: ${gitState.filter(g => g.dirty > 0).length}, stale-mem: ${memory.staleCount}, logs-need-rotation: ${needsRotation.length}, junk-quarantinable: ${junkQuarantinable}, stray-repo-scripts: ${strayRepoCount}, alerts-high-med: ${alerts.highMedCount}, respawn-watch: ${respawnWatchCount}, cycle: ${cycleCount}`;
  console.log(summaryLine);
} catch (e) {
  console.error('Unexpected error during output write:', e.message);
  process.exit(1);
}
