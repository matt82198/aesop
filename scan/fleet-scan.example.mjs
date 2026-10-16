// Example IOC/secret scanner — copy to fleet-scan.mjs and configure paths.
// Scans committed code and fleet transcripts for security/alignment red-flags.
// Runs each watchdog cycle; marks findings in SECURITY-ALERTS.log. Never blocks the fleet.
//
// SETUP:
// 1. Copy this file to fleet-scan.mjs (in the same directory)
// 2. Edit the REPOS and PROJECT_ROOTS configuration below to match your fleet setup
// 3. Configure paths via aesop.config.json or environment variables:
//    - AESOP_FLEET_ROOT: root directory containing your project repositories
//    - AESOP_TRANSCRIPTS_ROOT: root directory containing ~/.claude/projects transcripts
// 4. Ensure aesop.config.json contains repo definitions (see aesop.config.example.json)

import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

// Load configuration from aesop.config.json or use environment variable defaults
function loadConfig() {
  const configPath = process.env.AESOP_CONFIG || path.join(process.cwd(), 'aesop.config.json');
  let config = {};
  if (fs.existsSync(configPath)) {
    try {
      config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    } catch (e) {
      console.error(`Warning: Failed to load config from ${configPath}: ${e.message}`);
    }
  }
  return config;
}

const config = loadConfig();

// Environment variable overrides (precedence: env > config > defaults)
const FLEET_ROOT = process.env.AESOP_FLEET_ROOT || config.fleet_root || process.env.HOME;
const TRANSCRIPTS_ROOT = process.env.AESOP_TRANSCRIPTS_ROOT || config.transcripts_root || path.join(process.env.HOME, '.claude', 'projects');

// === CONFIGURE YOUR FLEET ===
// Edit REPOS array to match your monitored repositories
// Example: { path: '/path/to/project1', name: 'project1', branch: 'main' }
// Paths can be absolute or relative to FLEET_ROOT
const REPOS = config.repos || [
  // Example:
  // { path: 'project-a', name: 'project-a', branch: 'main' },
  // { path: 'project-b', name: 'project-b', branch: 'main' },
];

// Transcript project roots to scan for fleet prompts
// Default: ~/.claude/projects (set via AESOP_TRANSCRIPTS_ROOT or config)
const PROJECT_ROOTS = [
  TRANSCRIPTS_ROOT,
];

// Handoff/alerts directory (where SECURITY-ALERTS.log is stored)
// Set via config: alerts.alerts_root or alerts_root
const ALERTS_ROOT = config.alerts?.alerts_root || config.alerts_root || path.join(FLEET_ROOT, '..', 'conductor3', 'state');
const ALERTS = path.join(ALERTS_ROOT, 'SECURITY-ALERTS.log');
const SEENF = path.join(ALERTS_ROOT, '.fleet-scan-seen.json');
const MARKERF = path.join(ALERTS_ROOT, '.fleet-scan-lastcommit');

// Session ID to exclude (optional: set via EXCLUDE_SESSION env var or config)
const MY_SESSION = process.env.EXCLUDE_SESSION || config.exclude_session || '';

// ---- Utility functions ----
const git = (a, cwd) => {
  try {
    return execSync(`git ${a}`, {
      cwd,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
  } catch {
    return '';
  }
};
const now = () => new Date().toISOString().replace('T', ' ').slice(0, 19);
const seen = fs.existsSync(SEENF) ? new Set(JSON.parse(fs.readFileSync(SEENF, 'utf8'))) : new Set();
const findings = [];

const add = (sev, kind, where, detail) => {
  const key = crypto
    .createHash('sha1')
    .update(sev + kind + where + detail)
    .digest('hex')
    .slice(0, 16);
  if (seen.has(key)) return;
  seen.add(key);
  findings.push(`[${now()}] ${sev} ${kind} | ${where} | ${detail}`);
};

// ---- IOC patterns for ADDED code lines ----
const CODE_IOC = [
  ['HIGH', 'exec/shell', /Runtime\.getRuntime\(\)\.exec|new\s+ProcessBuilder|\bos\.system\(|\bsubprocess\.|\bpopen\(/i],
  ['HIGH', 'reverse-shell', /\/dev\/tcp\/|\bnc\s+-e\b|bash\s+-i\b|sh\s+-i\b|socket\.SOCK_STREAM.*connect/i],
  ['HIGH', 'pipe-to-shell', /\b(curl|wget)\b[^\n]*\|\s*(sh|bash)\b/i],
  ['HIGH', 'b64-exec', /base64\s+(-d|--decode)[^\n]*\|\s*(sh|bash)|Base64.*decode[^\n]*exec/i],
  ['HIGH', 'secret-literal', /AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|-----BEGIN[A-Z ]*PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,}|ghp_[A-Za-z0-9]{20,}/],
  ['HIGH', 'cred-access', /\.ssh\/(id_|authorized)|\.aws\/credentials|\/etc\/(passwd|shadow)|secrets\.toml|\.env\b/i],
  ['HIGH', 'destructive', /\brm\s+-rf\b|DROP\s+TABLE|TRUNCATE\s+TABLE|DELETE\s+FROM\s+\w+\s*;/i],
  ['MED', 'deserialization', /\bObjectInputStream\b|\.readObject\(|XMLDecoder|@JsonTypeInfo|enableDefaultTyping/],
  ['MED', 'reflection', /Class\.forName\(|Method\.invoke\(|getDeclaredMethod\(|setAccessible\(true\)/],
  ['MED', 'raw-network', /new\s+Socket\(|URLConnection|\.openConnection\(|InetSocketAddress\(/i],
  ['MED', 'hardcoded-cred', /(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*["'][^"']{6,}["']/i],
];

function scanCodeAddedLines(diff, label) {
  if (!diff) return;
  let file = label;
  for (const line of diff.split('\n')) {
    if (line.startsWith('+++ ')) {
      file = line.slice(4).replace(/^b\//, '');
      continue;
    }
    if (!line.startsWith('+') || line.startsWith('+++')) continue;
    const code = line.slice(1);
    for (const [sev, kind, re] of CODE_IOC) {
      if (re.test(code)) add(sev, 'CODE:' + kind, file, code.trim().slice(0, 160));
    }
  }
}

// Scan all configured repos
for (const repo of REPOS) {
  const repoPath = path.isAbsolute(repo.path) ? repo.path : path.join(FLEET_ROOT, repo.path);
  if (!fs.existsSync(repoPath)) {
    console.warn(`Warning: Repo not found: ${repoPath}`);
    continue;
  }

  const head = git('rev-parse HEAD', repoPath).trim();
  const markerFile = path.join(repoPath, '.fleet-scan-lastcommit-' + repo.name);
  const last = fs.existsSync(markerFile) ? fs.readFileSync(markerFile, 'utf8').trim() : '';

  if (head) {
    if (last && last !== head) {
      scanCodeAddedLines(git(`diff ${last}..${head}`, repoPath), `${repo.name}:commit-range`);
    } else if (!last) {
      scanCodeAddedLines(git(`show ${head}`, repoPath), `${repo.name}:${head.slice(0, 8)}`);
    }
    scanCodeAddedLines(git('diff HEAD', repoPath), `${repo.name}:working-tree`);
    fs.writeFileSync(markerFile, head);
  }
}

// ---- Fleet transcript scan (injection / exfil / off-goal) ----
const CRED_HARVEST_ALLOWLIST = [
  'secret_scan.py',
  'secret/credential gate',
  'scripts/CLAUDE.md',
  'env_assignment',
  'env_suspicious_keys',
  'scanner_selftest',
  'allow-pattern-docs',
  'CRED_HARVEST_ALLOWLIST',
  'generic_secret_assignment',
];

const EXFILTRATION_ALLOWLIST = [
  'semantic-prompt-review',
  'semantic review',
  'OK/DRIFT/SUSPICIOUS',
  'drift_escalator',
  'prompt_classifier',
  'alert-review',
  'triage',
  'EXFILTRATION_ALLOWLIST',
  'power_selftest',
  'secret_scan.py',
  'fleet-scan.mjs',
  'scanner_selftest',
  'SECRETS-PERMITTED',
  'resolve_alerts',
];

// Transmission indicators: a message carrying any of these is never a benign
// defensive reference, even when an allowlisted word is also present.
const EXFIL_TRANSMISSION_INDICATORS = /\bPOST\b|https?:\/\/|\b[Uu]pload(s|ed|ing)?\b|\b(curl|wget)\b/;

// Whole-token containment: `ref` must not be glued to a word character, hyphen,
// or dotted suffix (so "triage" does not match "re-triage", "triaged" or "triage.txt").
function containsWholeToken(text, ref) {
  const escaped = ref.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?<![\\w.-])${escaped}(?![\\w-]|\\.\\w)`).test(text);
}

// The clause holding index `idx`: text bounded by newline, ; ! ? or a comma/period
// followed by whitespace or end of text.
function clauseAround(text, idx) {
  const boundary = /[\n;!?]|[.,](?=\s|$)/g;
  let start = 0;
  let end = text.length;
  let m;
  while ((m = boundary.exec(text)) !== null) {
    if (m.index < idx) start = m.index + 1;
    else {
      end = m.index;
      break;
    }
  }
  return text.slice(start, end);
}

// Suppress an exfiltration match only when BOTH hold: (1) an allowlisted reference
// appears as a whole token in the same clause as the match, and (2) the message
// carries no transmission indicator anywhere. Substring hits elsewhere never suppress.
function isDefensiveExfilReference(text, match) {
  const clause = clauseAround(text, match.index);
  if (!EXFILTRATION_ALLOWLIST.some((ref) => containsWholeToken(clause, ref))) return false;
  return !EXFIL_TRANSMISSION_INDICATORS.test(text);
}

const PROMPT_IOC = [
  ['HIGH', 'prompt-injection', /ignore\s+(all\s+)?(previous|prior|the\s+above)\s+instructions|disregard\s+(the|your)\s+(rules|instructions)|you\s+are\s+now\s+|new\s+system\s+prompt/i],
  ['HIGH', 'exfiltration', /exfiltrat|send\s+(it|them|the\s+\w+)\s+to\s+https?:|upload\s+to\s+|POST\s+.*to\s+http|leak\s+(the\s+)?(secret|cred|token|key)/i],
  ['HIGH', 'cred-harvest', /read\s+.*secrets\.toml|\.env\b|\.ssh\/|\.aws\/credentials|environment\s+variables?\s+.*(send|post|exfil)|harvest\s+(cred|token|key)/i],
  ['HIGH', 'evade-security', /disable\s+(the\s+)?(sentinel|antivirus|edr|defender|firewall)|bypass\s+(the\s+)?(security|scan|review)|hide\s+(this\s+)?from\s+the\s+user|don.?t\s+tell\s+the\s+(user|owner)/i],
  ['MED', 'danger-shell', /\b(curl|wget)\b.*\|\s*(sh|bash)|base64\s+-d|\/dev\/tcp\/|rm\s+-rf\s+(\/|~)|chmod\s+\+s/i],
];

function extractTexts(obj) {
  const out = [];
  const c = obj?.message?.content;
  if (typeof c === 'string') out.push(c);
  else if (Array.isArray(c)) {
    for (const x of c) {
      if (typeof x === 'string') out.push(x);
      else if (x?.type === 'text' && x.text) out.push(x.text);
      else if (x?.type === 'tool_use' && (x.name === 'Agent' || x.name === 'Task')) {
        if (x.input?.prompt) out.push('[SPAWN] ' + x.input.prompt);
        if (x.input?.description) out.push('[SPAWN] ' + x.input.description);
      }
    }
  }
  return out;
}

const SINCE = Date.now() - 25 * 60 * 1000;
const tFiles = [];

function walk(d) {
  try {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith('.jsonl')) tFiles.push(p);
    }
  } catch {}
}

for (const r of PROJECT_ROOTS) {
  if (fs.existsSync(r)) walk(r);
}

for (const fp of tFiles) {
  if (MY_SESSION && fp.includes(MY_SESSION)) continue; // skip excluded session
  let st;
  try {
    st = fs.statSync(fp);
  } catch {
    continue;
  }
  if (st.mtimeMs < SINCE) continue; // only recent fleet activity
  const base = path.basename(fp);
  const isAgentFile = base.startsWith('agent-'); // agent files: scan all; main session: [SPAWN] only
  let lines;
  try {
    lines = fs.readFileSync(fp, 'utf8').split('\n');
  } catch {
    continue;
  }
  for (const line of lines) {
    if (!line.trim()) continue;
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    for (const text of extractTexts(obj)) {
      if (!text || text.length < 8) continue;
      const isSpawnedPrompt = text.startsWith('[SPAWN]');
      const shouldScanPromptIOC = isAgentFile || isSpawnedPrompt;
      let flagged = false;
      if (shouldScanPromptIOC) {
        for (const [sev, kind, re] of PROMPT_IOC) {
          if (re.test(text)) {
            if (kind === 'cred-harvest') {
              const isDefensiveToolRef = CRED_HARVEST_ALLOWLIST.some((ref) => text.includes(ref));
              if (isDefensiveToolRef) {
                const detail = text.replace(/\s+/g, ' ').slice(0, 180);
                add('SUPPRESSED-FP', 'cred-harvest (defensive-tool reference)', base, detail);
                flagged = true;
                continue;
              }
            }
            if (kind === 'exfiltration') {
              if (isDefensiveExfilReference(text, re.exec(text))) {
                const detail = text.replace(/\s+/g, ' ').slice(0, 180);
                add('SUPPRESSED-FP', 'exfiltration (defensive-review reference)', base, detail);
                flagged = true;
                continue;
              }
            }
            add(sev, 'PROMPT:' + kind, base, text.replace(/\s+/g, ' ').slice(0, 180));
            flagged = true;
          }
        }
      }
      void flagged;
    }
  }
}

// ---- emit ----
// Ensure output directory exists
if (!fs.existsSync(ALERTS_ROOT)) {
  fs.mkdirSync(ALERTS_ROOT, { recursive: true });
}

if (findings.length) {
  fs.appendFileSync(ALERTS, findings.join('\n') + '\n');
  fs.writeFileSync(SEENF, JSON.stringify([...seen]));
  console.log(`${findings.length} new finding(s) -> ${ALERTS}`);
  for (const f of findings) console.log(f);
} else {
  fs.writeFileSync(SEENF, JSON.stringify([...seen]));
  console.log('no new findings');
}
