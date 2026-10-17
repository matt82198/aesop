#!/usr/bin/env node
// INDEX: Preflight checklist for adopter onboarding (diagnostic checks: Node/Python versions, git repo, aesop.config.json structure incl. the `ci` block via tools/ci_config.js, placeholder repo URLs, dirs, pre-push hook, port; exit 0=all pass, 1=failed) plus a REPORT-ONLY `CI capability` section from tools/ci_capability.py (OS, cores/RAM, Smart App Control / UMCI, WSL/Docker, gh auth, cloudflared, and the table mode -> runnable here: yes/no + why for hosted | self-hosted-runner | local-receipt-gate) that never fails the doctor; `--json` emits {checks, summary, ci_capability}. Layout detection (`layout` key in aesop.config.json, else inferred): an init'd repo (no scaffold directories) reports the five-directory check as n/a and fails on nothing else it does not promise. The `~/.claude/skills` check is FAIL-CLOSED (`power` and `buildsystem` must exist there) but runs only with `--global`, so the default verdict is repo-local

/**
 * Aesop doctor — preflight checklist for adopter onboarding
 *
 * Runs diagnostic checks and prints a readiness table.
 * Exit code 0 = all checks passed; 1 = at least one failed.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawnSync } = require('child_process');
const net = require('net');

const CURRENT_DIR = process.cwd();
const JSON_MODE = process.argv.includes('--json');
// --global also checks machine-level state (~/.claude/skills). The default run is repo-local,
// so its verdict does not depend on what is installed in the home directory.
const GLOBAL_MODE = process.argv.includes('--global');
const ciConfig = require('./ci_config.js');

// ANSI color helpers
const COLORS = {
  GREEN: '\x1b[32m',
  RED: '\x1b[31m',
  RESET: '\x1b[0m',
  BOLD: '\x1b[1m'
};

function colorPass() {
  return `${COLORS.GREEN}✓ PASS${COLORS.RESET}`;
}

function colorFail() {
  return `${COLORS.RED}✗ FAIL${COLORS.RESET}`;
}

// Check Node.js version >= 18
function checkNodeVersion() {
  const version = parseInt(process.versions.node.split('.')[0], 10);
  const passed = version >= 18;
  const hint = passed ? `v${process.versions.node}` : `Found Node.js v${process.versions.node}, need >=18`;
  return { passed, hint };
}

// Check Python available and version >= 3.10
function checkPython() {
  // Try python3 first
  let pythonBin = null;
  let result3 = spawnSync('python3', ['--version'], { encoding: 'utf8', timeout: 5000 });

  if (result3.error && result3.error.code === 'ENOENT') {
    // python3 not found, try python fallback
    const result = spawnSync('python', ['--version'], { encoding: 'utf8', timeout: 5000 });
    if (result.error && result.error.code === 'ENOENT') {
      // Neither python3 nor python found
      return { passed: false, hint: 'python3 or python not found on PATH' };
    }
    if (result.status !== 0) {
      // python exists but returned non-zero exit code
      return { passed: false, hint: 'python found but returned non-zero exit code' };
    }
    pythonBin = 'python';
  } else if (result3.status !== 0) {
    // python3 exists but returned non-zero exit code
    return { passed: false, hint: 'python3 found but returned non-zero exit code' };
  } else {
    pythonBin = 'python3';
  }

  // Extract version from python output
  let versionStr = '';
  try {
    const versionOutput = result3.stdout || result3.stderr || '';
    const match = versionOutput.match(/Python\s+(\d+)\.(\d+)/i);
    if (match) {
      const major = parseInt(match[1], 10);
      const minor = parseInt(match[2], 10);
      versionStr = `${major}.${minor}`;
      if (major > 3 || (major === 3 && minor >= 10)) {
        return { passed: true, hint: `v${versionStr}` };
      } else {
        return { passed: false, hint: `Found Python v${versionStr}, need >=3.10` };
      }
    }
  } catch (e) {
    // Version extraction failed but python runs
    return { passed: true, hint: 'Available (version check skipped)' };
  }

  return { passed: true, hint: 'Available' };
}

// Check git repo (.git directory exists)
function checkGitRepo() {
  const gitDir = path.join(CURRENT_DIR, '.git');
  const passed = fs.existsSync(gitDir);
  const hint = passed ? '' : 'Not inside a git repository';
  return { passed, hint };
}

// Check aesop.config.json exists and is valid JSON
function checkConfig() {
  const configPath = path.join(CURRENT_DIR, 'aesop.config.json');
  try {
    if (!fs.existsSync(configPath)) {
      return { passed: false, hint: 'aesop.config.json not found' };
    }
    const content = fs.readFileSync(configPath, 'utf8');
    const config = JSON.parse(content);

    // Validate repos array exists and is an array
    if (!('repos' in config)) {
      return { passed: false, hint: 'Missing required field: repos' };
    }
    if (!Array.isArray(config.repos)) {
      return { passed: false, hint: 'repos field must be an array' };
    }

    // Validate aesop_root exists on disk (if present in config)
    if (config.aesop_root && !fs.existsSync(config.aesop_root)) {
      return { passed: false, hint: `aesop_root does not exist: ${config.aesop_root}` };
    }

    // Validate repo paths exist (if specified)
    for (const repo of config.repos) {
      if (repo.path && !fs.existsSync(repo.path)) {
        return { passed: false, hint: `Repo path does not exist: ${repo.path}` };
      }
    }

    // Validate the optional `ci` block (same codes as tools/common.py validate_ci_config)
    const ciFindings = ciConfig.validateCiConfig(config, CURRENT_DIR);
    if (ciFindings.length > 0) {
      return { passed: false, hint: `ci block invalid: ${ciFindings.map(f => `${f.code} (${f.message})`).join('; ')}` };
    }

    return { passed: true, hint: '' };
  } catch (e) {
    return { passed: false, hint: `Config error: ${e.message}` };
  }
}

// Check for placeholder repo URLs and warn
function checkRepoURLs() {
  const configPath = path.join(CURRENT_DIR, 'aesop.config.json');
  try {
    if (!fs.existsSync(configPath)) {
      // Skip this check if config doesn't exist (main checkConfig will fail)
      return { passed: true, hint: '' };
    }
    const content = fs.readFileSync(configPath, 'utf8');
    const config = JSON.parse(content);

    // Check for placeholder URLs
    const placeholderPattern = /https:\/\/github\.com\/user\//i;
    if (config.repos && Array.isArray(config.repos)) {
      for (const repo of config.repos) {
        if (repo.url && placeholderPattern.test(repo.url)) {
          return {
            passed: false,
            hint: `Placeholder URL found: ${repo.url}. Replace with actual repo URL or remove the repo entry.`
          };
        }
      }
    }

    return { passed: true, hint: '' };
  } catch (e) {
    return { passed: true, hint: '' };  // Skip check on config parse error (main checkConfig handles it)
  }
}

// Layout detection (D32). A scaffold (daemons/, dash/, monitor/, tools/, ui/) keeps the
// five-directory rule. An init'd repo (aesop.config.json only, written by tools/init_project.py
// with "layout":"init") does not have those directories and must not fail for lacking them.
// An explicit "layout" key wins; an unknown value fails closed as a scaffold. Without the key:
// any scaffold directory means scaffold, a config alone means init, and neither means scaffold.
const SCAFFOLD_DIRS = ['daemons', 'dash', 'monitor', 'tools', 'ui'];

function isDirectory(rel) {
  const p = path.join(CURRENT_DIR, rel);
  return fs.existsSync(p) && fs.statSync(p).isDirectory();
}

function detectLayout() {
  const configPath = path.join(CURRENT_DIR, 'aesop.config.json');
  let config = null;
  try {
    config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  } catch (e) {
    config = null;  // missing or unparseable: checkConfig reports that; layout is inferred
  }
  if (config && typeof config === 'object' && 'layout' in config) {
    return config.layout === 'init'
      ? { name: 'init', reason: 'aesop.config.json "layout":"init"' }
      : { name: 'scaffold', reason: `aesop.config.json "layout":${JSON.stringify(config.layout)} (not "init", so scaffold rules apply)` };
  }
  if (['daemons', 'dash', 'monitor', 'ui'].some(isDirectory)) {
    return { name: 'scaffold', reason: 'scaffold directories present' };
  }
  if (fs.existsSync(configPath)) {
    return { name: 'init', reason: 'aesop.config.json present, no scaffold directories' };
  }
  return { name: 'scaffold', reason: 'no aesop.config.json and no scaffold directories (fail closed)' };
}

// Check if ~/.claude/skills/power/SKILL.md and ~/.claude/skills/buildsystem/SKILL.md exist.
// Claude Code only discovers skills under ~/.claude/skills/ (or a project's .claude/skills/);
// the scaffolded ./skills/ directory is never scanned, so an uncopied skill is an unusable one.
// Fail-closed: the orchestrator cannot run without these, so a green doctor must mean installed.
// Global: the home directory is read only under --global, never in the default repo-local run.
function checkSkillsFiles() {
  if (!GLOBAL_MODE) {
    return { passed: true, na: true, hint: 'n/a (global checks skipped; run doctor --global to check ~/.claude/skills)' };
  }
  const homeDir = os.homedir();
  const skillsDir = path.join(homeDir, '.claude', 'skills');
  const powerSkill = path.join(skillsDir, 'power', 'SKILL.md');
  const buildsystemSkill = path.join(skillsDir, 'buildsystem', 'SKILL.md');

  const powerExists = fs.existsSync(powerSkill);
  const buildsystemExists = fs.existsSync(buildsystemSkill);

  if (powerExists && buildsystemExists) {
    return { passed: true, hint: 'Both skills present' };
  }

  // Point at the scaffolded source only when it is actually there — otherwise the
  // hint would name a path the user does not have.
  const sourceDir = path.join(CURRENT_DIR, 'skills');
  const installHint = fs.existsSync(sourceDir)
    ? `Install: mkdir -p "${skillsDir}" && cp -r "${sourceDir}"/*/ "${skillsDir}"/`
    : `Install: copy the scaffolded skills/*/ directories into "${skillsDir}"`;
  const restartHint = 'Then restart Claude Code — skills are enumerated at startup.';

  const missing = [];
  if (!powerExists) missing.push('power');
  if (!buildsystemExists) missing.push('buildsystem');

  return {
    passed: false,
    hint: `Missing skill(s): ${missing.join(', ')} — not found under ${skillsDir}. ${installHint}. ${restartHint}`
  };
}

// Push-gate artifacts the pre-push hook reads (fail-closed when absent). Each FAIL names
// the file and its regenerate command. The same commands are printed by bin/cli.js at
// scaffold time and by the hook's BOOTSTRAP FATAL.
const PUSH_GATE_ARTIFACTS = [
  { file: 'tools/INDEX.md', regen: 'python tools/gen_tool_index.py --regenerate' },
  { file: '.pyflakes-baseline.json', regen: 'python tools/pyflakes_gate.py --root . --baseline .pyflakes-baseline.json --update-baseline' },
  { file: '.subprocess-guard-baseline.json', regen: 'python tools/subprocess_guard.py --baseline .subprocess-guard-baseline.json --update-baseline' },
  { file: '.portability-baseline.json', regen: 'python tools/portability_check.py --root . --baseline .portability-baseline.json --update-baseline' },
  { file: '.stateapi-baseline.json', regen: 'python tools/stateapi_lint.py --root . --baseline .stateapi-baseline.json --update-baseline' }
];

function checkPushGateArtifact(artifact) {
  const artifactPath = path.join(CURRENT_DIR, artifact.file);
  if (fs.existsSync(artifactPath) && fs.statSync(artifactPath).isFile()) {
    return { passed: true, hint: '' };
  }
  return { passed: false, hint: `Missing ${artifact.file}; regenerate with: ${artifact.regen}, then commit it` };
}

// Check required directories exist (scaffold layout only; an init'd repo reports n/a)
function checkDirectories(layout) {
  if (layout.name === 'init') {
    return { passed: true, na: true, hint: 'n/a (init layout)' };
  }
  const requiredDirs = SCAFFOLD_DIRS;
  const missing = requiredDirs.filter(dir => {
    const dirPath = path.join(CURRENT_DIR, dir);
    return !fs.existsSync(dirPath) || !fs.statSync(dirPath).isDirectory();
  });

  if (missing.length === 0) {
    return { passed: true, hint: '' };
  } else {
    return { passed: false, hint: `Missing: ${missing.join(', ')}` };
  }
}

// Check git pre-push hook installed
// Resolve the hooks directory the way git does. In a linked worktree .git is a FILE
// ("gitdir: <primary>/.git/worktrees/<name>") and hooks live in the common dir; core.hooksPath
// moves them elsewhere. `git rev-parse --git-path hooks` honours both; `--git-common-dir`
// is the fallback. If git cannot answer, return the git error (the caller FAILs closed).
function resolveHooksDir() {
  const runGit = (args) => spawnSync('git', args, {
    cwd: CURRENT_DIR,
    encoding: 'utf8',
    timeout: 5000
  });
  const firstLine = (res) => ((res.stderr || '').trim() || (res.error && res.error.message) || '').split('\n')[0];

  const hooksPath = runGit(['rev-parse', '--git-path', 'hooks']);
  if (hooksPath.status === 0 && hooksPath.stdout.trim()) {
    return { dir: path.resolve(CURRENT_DIR, hooksPath.stdout.trim()) };
  }
  const commonDir = runGit(['rev-parse', '--git-common-dir']);
  if (commonDir.status === 0 && commonDir.stdout.trim()) {
    return { dir: path.resolve(CURRENT_DIR, commonDir.stdout.trim(), 'hooks') };
  }
  return { error: firstLine(hooksPath) || firstLine(commonDir) || 'git rev-parse failed' };
}

function checkPrePushHook() {
  const resolved = resolveHooksDir();
  if (resolved.error) {
    return { passed: false, hint: `Could not resolve git hooks directory: ${resolved.error}` };
  }
  const hookPath = path.join(resolved.dir, 'pre-push');
  const passed = fs.existsSync(hookPath);
  const hint = passed ? '' : `Pre-push hook not installed at ${hookPath}`;
  return { passed, hint };
}

// Dashboard port, resolved exactly as `aesop dash` does (tools/dash.js): a valid PORT env wins
// (an invalid PORT falls back to 8770), else aesop.config.json dashboard.port, else 8770.
const DEFAULT_DASHBOARD_PORT = 8770;
function parsePortNumber(value) {
  const n = parseInt(value, 10);
  return !isNaN(n) && n > 0 && n < 65536 ? n : null;
}
function resolveDashboardPort() {
  if (process.env.PORT) {
    return parsePortNumber(process.env.PORT) || DEFAULT_DASHBOARD_PORT;
  }
  const { config } = ciConfig.loadAesopConfig(CURRENT_DIR);
  const configured = config && config.dashboard && config.dashboard.port
    ? parsePortNumber(config.dashboard.port)
    : null;
  return configured || DEFAULT_DASHBOARD_PORT;
}

// Check if the dashboard port is free (using socket connection test)
function checkDashboardPort(port) {
  return new Promise((resolve) => {
    let resolved = false;
    const sock = net.createConnection({ port, host: '127.0.0.1', timeout: 500 });

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
        resolve({ passed: false, hint: `Port ${port} in use — is another fleet running? Update dashboard.port in aesop.config.json` });
      }
    });

    sock.on('error', () => {
      // Connection failed, port is free
      if (!resolved) {
        resolved = true;
        cleanup();
        resolve({ passed: true, hint: '' });
      }
    });

    sock.on('timeout', () => {
      if (!resolved) {
        resolved = true;
        cleanup();
        resolve({ passed: true, hint: '' });
      }
    });

    // Fallback timeout to ensure we resolve within 2 seconds
    setTimeout(() => {
      if (!resolved) {
        resolved = true;
        cleanup();
        resolve({ passed: true, hint: '' });
      }
    }, 2000);
  });
}

// Resolve the Python interpreter the capability probe runs under (python3, then python)
function resolvePythonBin() {
  for (const candidate of ['python3', 'python']) {
    const res = spawnSync(candidate, ['--version'], { encoding: 'utf8', timeout: 5000 });
    if (!res.error && res.status === 0) return candidate;
  }
  return null;
}

// CI capability probe (REPORT-ONLY): delegates to tools/ci_capability.py --json.
// Never influences pass/fail -- a missing optional capability is a row, not a failure.
function probeCiCapability() {
  const pythonBin = resolvePythonBin();
  if (!pythonBin) {
    return { error: 'python3/python not found; capability probe skipped', modes: [] };
  }
  const script = path.join(__dirname, 'ci_capability.py');
  const res = spawnSync(pythonBin, [script, '--json', '--repo-root', CURRENT_DIR], {
    encoding: 'utf8', timeout: 90000, env: process.env
  });
  if (res.error || res.status !== 0) {
    const detail = res.error ? res.error.message : (res.stderr || '').trim().split('\n').pop();
    return { error: `capability probe failed: ${detail}`, modes: [] };
  }
  try {
    return JSON.parse(res.stdout);
  } catch (e) {
    return { error: `capability probe returned non-JSON: ${e.message}`, modes: [] };
  }
}

function yesNo(v) {
  return v ? 'yes' : 'no';
}

// Render the CI capability section as text (mirrors tools/ci_capability.py render_text)
function formatCiCapability(cap) {
  const lines = [`\n${COLORS.BOLD}CI capability (report-only)${COLORS.RESET}`];
  if (cap.error) {
    lines.push(`  ${cap.error}`);
    return lines.join('\n');
  }
  const probes = cap.probes || {};
  const osInfo = probes.os || {};
  const win = cap.windows || {};
  lines.push(`  ${'OS'.padEnd(20)} ${osInfo.system || '?'} ${osInfo.release || ''} (${osInfo.version || ''})`);
  lines.push(`  ${'CPU / RAM'.padEnd(20)} ${probes.cpu_cores ?? '?'} cores / ${probes.ram_gb ?? '?'} GB`);
  lines.push(`  ${'Smart App Control'.padEnd(20)} ${win.smart_app_control}`);
  lines.push(`  ${'UMCI'.padEnd(20)} ${win.umci}`);
  lines.push(`  ${'WSL / Docker'.padEnd(20)} ${yesNo((probes.wsl || {}).present)} / ${yesNo((probes.docker || {}).present)}`);
  const gh = probes.gh || {};
  lines.push(`  ${'gh auth'.padEnd(20)} ${gh.authenticated ? 'authenticated' : (gh.present ? 'present, not authenticated' : 'absent')}`);
  lines.push(`  ${'cloudflared'.padEnd(20)} ${(probes.cloudflared || {}).present ? 'present' : 'absent'}`);
  lines.push('');
  lines.push(`  ${'mode'.padEnd(22)} ${'runnable here'.padEnd(15)} why`);
  for (const row of cap.modes || []) {
    lines.push(`  ${row.mode.padEnd(22)} ${yesNo(row.runnable).padEnd(15)} ${row.why}`);
  }
  return lines.join('\n');
}

// Format a row in the readiness table. A n/a row prints its reason in place of a status.
function formatRow(label, status, hint, na = false) {
  // Pad label to 35 chars for alignment
  const paddedLabel = label.padEnd(35);
  if (na) {
    return `  ${paddedLabel} ${hint}`;
  }
  const statusStr = status ? colorPass() : colorFail();
  const hintStr = hint ? ` — ${hint}` : '';
  return `  ${paddedLabel} ${statusStr}${hintStr}`;
}

// Main execution
(async function main() {
  try {
    const layout = detectLayout();
    const syncChecks = [
      { label: 'Node.js version >=18', fn: checkNodeVersion },
      { label: 'Python version >=3.10', fn: checkPython },
      { label: 'Git repository', fn: checkGitRepo },
      { label: 'aesop.config.json (structure & required fields)', fn: checkConfig },
      { label: 'Repository URLs (no placeholders)', fn: checkRepoURLs },
      { label: 'Skills files (power & buildsystem)', fn: checkSkillsFiles },
      { label: 'Required directories (daemons, dash, monitor, tools, ui)', fn: () => checkDirectories(layout) },
      { label: 'Git pre-push hook installed', fn: checkPrePushHook },
      ...PUSH_GATE_ARTIFACTS.map(artifact => ({
        label: `Push-gate artifact: ${artifact.file}`,
        fn: () => checkPushGateArtifact(artifact)
      }))
    ];

    const results = [];
    for (const check of syncChecks) {
      const result = check.fn();
      results.push({ label: check.label, passed: result.passed, hint: result.hint || '', na: result.na === true });
    }

    const dashboardPort = resolveDashboardPort();
    try {
      const portResult = await checkDashboardPort(dashboardPort);
      results.push({ label: `Port ${dashboardPort} available`, passed: portResult.passed, hint: portResult.hint || '', na: false });
    } catch (e) {
      results.push({ label: `Port ${dashboardPort} available`, passed: false, hint: 'Port check failed', na: false });
    }

    // Report-only section: probed after the checks, counted in none of them.
    const ciCapability = probeCiCapability();

    // n/a rows are reported but neither pass nor fail: total = passed + failed + na.
    const counted = results.filter(r => !r.na);
    const allPassed = counted.every(r => r.passed);
    const passCount = counted.filter(r => r.passed).length;
    const failCount = counted.length - passCount;
    const naCount = results.length - counted.length;
    const summary = { total: results.length, passed: passCount, failed: failCount, na: naCount };

    if (JSON_MODE) {
      process.stdout.write(JSON.stringify({
        checks: results,
        summary,
        layout,
        global: GLOBAL_MODE,
        ci_capability: ciCapability
      }, null, 2) + '\n');
      process.exitCode = allPassed ? 0 : 1;
      return;
    }

    console.log(`\n${COLORS.BOLD}Aesop Readiness Check${COLORS.RESET}\n`);
    console.log(`  Layout: ${layout.name} (${layout.reason})`);
    if (!GLOBAL_MODE) {
      console.log('  Global checks skipped (default run is repo-local). Enable with: aesop doctor --global');
    }
    console.log('');
    for (const r of results) {
      console.log(formatRow(r.label, r.passed, r.hint, r.na));
    }
    console.log(formatCiCapability(ciCapability));

    const naNote = naCount > 0 ? ` (${naCount} n/a)` : '';
    console.log(`\n${COLORS.BOLD}Summary: ${passCount}/${counted.length} checks passed${naNote}${COLORS.RESET}`);

    if (allPassed) {
      console.log(`${COLORS.GREEN}\u2713 You are ready to run: bash daemons/run-watchdog.sh --once${COLORS.RESET}\n`);
      process.exitCode = 0;
    } else {
      console.log(`${COLORS.RED}\u2717 Fix the ${failCount} failed check(s) above and try again${COLORS.RESET}\n`);
      process.exitCode = 1;
    }
  } catch (err) {
    console.error(`Error running doctor: ${err.message}`);
    process.exit(1);
  }
})();
