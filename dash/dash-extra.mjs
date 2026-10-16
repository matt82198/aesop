// Detect and render running Claude agents from transcripts.
// Usage: node dash-extra.mjs [--json]
//   --json: output JSON array of agents with rich metadata (for web dashboard)
//   (default): render TUI text output (for terminal dashboard)
//
// Enhanced JSONL parsing: extracts dispatch prompt, token counts, runtime, task label
// Robust: tolerates malformed lines, caps read size on large files

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

// Configuration: env var > config file > built-in default
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
  // Expand environment variables like $VAR or %VAR%
  return pathStr.replace(/\$\{?([A-Z_]+)\}?/gi, (match, varName) => {
    return process.env[varName] || match;
  });
}

// Home comes from os.homedir() (USERPROFILE on Windows, HOME on POSIX, passwd
// fallback), never from process.env.HOME alone: HOME is unset on native Windows
// when launched from PowerShell or a scheduled task, and a '.' fallback silently
// points every default at the cwd.
const AESOP_ROOT = process.env.AESOP_ROOT || path.join(os.homedir(), 'aesop');
const config = loadConfigFile(AESOP_ROOT);

const TRANSCRIPTS_ROOT = path.resolve(
  expandPath(
    process.env.AESOP_TRANSCRIPTS_ROOT ||
    config.transcripts_root ||
    path.join(os.homedir(), '.claude', 'projects')
  )
);

// Resolve state_root: env var > config > default
const STATE_ROOT = path.resolve(
  expandPath(
    process.env.AESOP_STATE_ROOT ||
    config.state_root ||
    path.join(AESOP_ROOT, 'state')
  )
);
const ALERTS_LOG = path.join(STATE_ROOT, 'SECURITY-ALERTS.log');

// ANSI colors for TUI output
const c = {
  R: '\x1b[31m',
  G: '\x1b[32m',
  Y: '\x1b[33m',
  M: '\x1b[35m',
  C: '\x1b[36m',
  B: '\x1b[1m',
  D: '\x1b[2m',
  X: '\x1b[0m'
};

const now = Date.now();
const out = [];

// Activity window: only include files modified within the last 12 minutes
const ACTIVITY_WINDOW = 12 * 60 * 1000;

// Depth limit to match monitor's equivalent (prevent unbounded recursion on growing tree)
const MAX_DEPTH = 6;

// Cache file for transcript metadata (path, size, mtime -> token counts, etc.)
const CACHE_FILE = path.join(STATE_ROOT, '.dash-extra-cache.json');

// Load persistent cache from disk (keyed by "path|size|mtime")
function loadCache() {
  try {
    if (fs.existsSync(CACHE_FILE)) {
      const data = fs.readFileSync(CACHE_FILE, 'utf8');
      return JSON.parse(data);
    }
  } catch {
    // Parse error or file doesn't exist; start with empty cache
  }
  return {};
}

// Save cache to disk (safe: ignore write errors, cache is optimization only)
function saveCache(cache) {
  try {
    const dir = path.dirname(CACHE_FILE);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    fs.writeFileSync(CACHE_FILE, JSON.stringify(cache, null, 2), 'utf8');
  } catch {
    // Ignore write errors; cache loss is non-fatal
  }
}

// Parse JSONL agent transcript and extract rich metadata.
// Uses cache keyed by (path, size, mtime) to avoid re-reading unchanged files.
function parseAgentJsonl(filePath, cache) {
  const metadata = {
    promptFull: '',
    taskLabel: '',
    tokensUsed: 0,
    startedAt: null,
    lastActivity: null,
    runtimeSeconds: 0,
    project: ''
  };

  try {
    // Derive project from path: transcripts_root/PROJECT/session/...
    const relPath = path.relative(TRANSCRIPTS_ROOT, filePath);
    const parts = relPath.split(path.sep);
    if (parts.length > 0) {
      metadata.project = parts[0];
    }

    // Get file stat for cache lookup
    let stat;
    try {
      stat = fs.statSync(filePath);
    } catch {
      return metadata; // File disappeared; return empty
    }

    // Cache key combines path, size, and mtime for exact identity
    const cacheKey = `${filePath}|${stat.size}|${stat.mtimeMs}`;

    // Check if we have cached metadata for this exact file state
    if (cache[cacheKey]) {
      const cached = cache[cacheKey];
      return {
        ...metadata,
        ...cached
      };
    }

    // File is new or changed; parse it
    const content = fs.readFileSync(filePath, 'utf8');
    const lines = content.split('\n');

    // Cap reading: first 50 + last 50 lines to avoid expensive parsing on huge files
    const linesToRead = [];
    if (lines.length <= 100) {
      linesToRead.push(...lines);
    } else {
      linesToRead.push(...lines.slice(0, 50));
      linesToRead.push(...lines.slice(-50));
    }

    let firstTimestamp = null;
    let lastTimestamp = null;

    for (const line of linesToRead) {
      if (!line.trim()) continue;

      try {
        const obj = JSON.parse(line);

        // Extract dispatch prompt from first user message
        if (obj.type === 'user' && obj.message && !metadata.promptFull) {
          const content = obj.message.content;
          if (typeof content === 'string') {
            metadata.promptFull = content;
            // Extract task label: first line of prompt, capped at 80 chars
            const firstLine = content.split('\n')[0];
            metadata.taskLabel = firstLine.substring(0, 80);
          }
        }

        // Track timestamps
        if (obj.timestamp) {
          const ts = new Date(obj.timestamp).getTime();
          if (!firstTimestamp) {
            firstTimestamp = ts;
            metadata.startedAt = obj.timestamp;
          }
          lastTimestamp = ts;
          metadata.lastActivity = obj.timestamp;
        }

        // (token accumulation is a separate FULL-file pass below — sampling
        //  only 100 lines under-counts tokens badly on long transcripts)
      } catch {
        // Silently skip malformed JSON lines
        continue;
      }
    }

    // Token total: scan EVERY line (usage records are spread throughout the
    // transcript, not just the sampled head/tail). Cheap substring pre-filter
    // keeps this fast on huge files by JSON-parsing only candidate lines.
    for (const line of lines) {
      if (!line.includes('"usage"')) continue;
      try {
        const obj = JSON.parse(line);
        if (obj.type === 'assistant' && obj.usage) {
          const { input_tokens = 0, output_tokens = 0 } = obj.usage;
          metadata.tokensUsed += input_tokens + output_tokens;
        }
      } catch {
        continue;
      }
    }

    // Calculate runtime in seconds
    if (firstTimestamp && lastTimestamp) {
      metadata.runtimeSeconds = Math.floor((lastTimestamp - firstTimestamp) / 1000);
    }

    // Store in cache for next invocation
    cache[cacheKey] = {
      tokensUsed: metadata.tokensUsed,
      taskLabel: metadata.taskLabel,
      promptFull: metadata.promptFull,
      startedAt: metadata.startedAt,
      lastActivity: metadata.lastActivity,
      runtimeSeconds: metadata.runtimeSeconds,
      project: metadata.project
    };
  } catch {
    // If file read fails, return empty metadata
  }

  return metadata;
}

// Load persistent cache (keyed by path|size|mtime)
const cache = loadCache();

// Read alerts log if present
let slog = [];
try {
  if (fs.existsSync(ALERTS_LOG)) {
    slog = fs.readFileSync(ALERTS_LOG, 'utf8').split('\n');
  }
} catch {}

// Recursively walk directory tree to find agent-*.jsonl files.
// Respects depth limit. Activity window filtering happens per-file, not per-directory,
// to avoid hiding active agents in dirs whose mtime was frozen at creation.
function walk(dir, accumulator, depth = 0) {
  // Stop recursion if depth exceeds limit
  if (depth > MAX_DEPTH) return;

  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        // Always descend into subdirectories (respecting depth limit only)
        // Do NOT prune based on directory mtime, as directories don't update
        // when files inside are appended to.
        walk(fullPath, accumulator, depth + 1);
      } else if (/^agent-.*\.jsonl$/.test(entry.name)) {
        accumulator.push(fullPath);
      }
    }
  } catch {}
}

// Find all agent transcript files
let files = [];
if (fs.existsSync(TRANSCRIPTS_ROOT)) {
  walk(TRANSCRIPTS_ROOT, files);
}

// Filter: active agents in last 12 minutes, sorted by recency
files = files
  .map(f => {
    let mtime = 0;
    try {
      mtime = fs.statSync(f).mtimeMs;
    } catch {}
    return { f, mtime };
  })
  .filter(x => now - x.mtime < 12 * 60 * 1000)
  .sort((a, b) => b.mtime - a.mtime);

// True active total. The 8 below is a TUI terminal-space limit ONLY — not the
// real count. The web (--json) emits ALL active agents; the TUI shows 8 + "N more".
const totalActive = files.length;
const runningCount = files.filter(x => now - x.mtime < 120 * 1000).length;
const TUI_ROW_CAP = 8;

// Extract description/hint from first ~60KB of agent transcript
function label(filePath) {
  try {
    const fd = fs.openSync(filePath, 'r');
    const buf = Buffer.alloc(60000);
    const n = fs.readSync(fd, buf, 0, 60000, 0);
    fs.closeSync(fd);
    const content = buf.toString('utf8', 0, n);

    // Try to find description or subagent_type in JSON
    let match = content.match(/"description":"([^"]{2,60})"/);
    if (!match) {
      match = content.match(/"subagent_type":"([^"]{2,40})"/);
    }
    return match ? match[1] : '';
  } catch {}
  return '';
}

// TUI output: render heading
out.push(`${c.B}  FLEET AGENTS${c.X} ${c.D}(green=running · severity-colored if flagged)${c.X}`);

if (totalActive === 0) {
  out.push(`    ${c.D}(no active fleet agents in last 12 min)${c.X}`);
}

// Render each agent (TUI caps rows to TUI_ROW_CAP for terminal space; the counts
// above are the true totals across all active agents)
for (const { f, mtime } of files.slice(0, TUI_ROW_CAP)) {
  const basename = path.basename(f);
  const ageSeconds = Math.round((now - mtime) / 1000);

  // Check if agent is referenced in alerts log
  const alertsForAgent = slog.filter(line => line.includes(basename));

  let statusColor = ageSeconds < 120 ? c.G : c.D;
  let statusText = ageSeconds < 120 ? 'running' : 'idle';

  // Recolor based on alert severity
  if (alertsForAgent.some(l => l.includes('SUSPICIOUS'))) {
    statusColor = c.R;
    statusText = 'SUSPICIOUS';
  } else if (alertsForAgent.some(l => / HIGH /.test(l))) {
    statusColor = c.R;
    statusText = 'HIGH';
  } else if (alertsForAgent.some(l => / DRIFT /.test(l))) {
    statusColor = c.M;
    statusText = 'DRIFT';
  } else if (alertsForAgent.some(l => / MED /.test(l))) {
    statusColor = c.Y;
    statusText = 'MED';
  }

  // Extract agent ID from filename (agent-<id>.jsonl)
  const agentId = basename
    .replace(/^agent-/, '')
    .replace(/\.jsonl$/, '')
    .slice(0, 13);

  const hint = label(f).slice(0, 38);

  out.push(
    `    ${statusColor}●${c.X} ${agentId.padEnd(14)} ${c.D}${String(ageSeconds).padStart(4)}s${c.X}  ${statusColor}${statusText.padEnd(11)}${c.X}${c.D}${hint}${c.X}`
  );
}

if (totalActive > TUI_ROW_CAP) {
  out.push(`    ${c.D}… +${totalActive - TUI_ROW_CAP} more active (showing ${TUI_ROW_CAP} of ${totalActive})${c.X}`);
}

if (totalActive > 0) {
  out.push(`    ${c.D}${runningCount} running, ${totalActive - runningCount} idle (last 12 min)${c.X}`);
}

// Output
if (process.argv.includes('--json')) {
  // JSON mode: emit agents array for web dashboard
  const agents = [];
  for (const { f, mtime } of files) {
    const basename = path.basename(f);
    const ageSeconds = Math.round((now - mtime) / 1000);
    const alertsForAgent = slog.filter(line => line.includes(basename));

    let status = ageSeconds < 120 ? 'running' : 'idle';

    if (alertsForAgent.some(l => l.includes('SUSPICIOUS'))) {
      status = 'SUSPICIOUS';
    } else if (alertsForAgent.some(l => / HIGH /.test(l))) {
      status = 'HIGH';
    } else if (alertsForAgent.some(l => / DRIFT /.test(l))) {
      status = 'DRIFT';
    } else if (alertsForAgent.some(l => / MED /.test(l))) {
      status = 'MED';
    }

    const agentId = basename
      .replace(/^agent-/, '')
      .replace(/\.jsonl$/, '')
      .slice(0, 13);

    // Parse JSONL for rich metadata (uses cache to skip unchanged files)
    const metadata = parseAgentJsonl(f, cache);

    agents.push({
      id: agentId,
      project: metadata.project,
      status: status,
      age_s: ageSeconds,
      hint: label(f).slice(0, 60),
      // New enriched fields
      startedAt: metadata.startedAt,
      lastActivity: metadata.lastActivity,
      runtimeSeconds: metadata.runtimeSeconds,
      tokensUsed: metadata.tokensUsed,
      taskLabel: metadata.taskLabel,
      promptFull: metadata.promptFull
    });
  }
  // Persist cache for next invocation
  saveCache(cache);
  process.stdout.write(JSON.stringify(agents) + '\n');
} else {
  // TUI mode: emit colored text
  // Persist cache for next invocation
  saveCache(cache);
  process.stdout.write(out.join('\n') + '\n');
}
