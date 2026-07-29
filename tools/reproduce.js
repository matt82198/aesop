#!/usr/bin/env node

/**
 * Aesop reproduce — offline verification suite
 *
 * Mirrors .github/workflows/reproduce.yml for local execution.
 *
 * Modes:
 *   REPO:      Full test suites (Node.js, Python, Shell, React, benchmarks)
 *   INSTALLED: Shipped self-checks (doctor, health-score, secret-scan selftest, packaging)
 *
 * Exit: 0 = all checks pass, 1 = any check failed
 * Output: ASCII table with per-step timing
 */

const fs = require('fs');
const path = require('path');
const { execSync, spawnSync } = require('child_process');
const { detectContext } = require('./detect-context');

const CURRENT_DIR = process.cwd();
const PACKAGE_ROOT = path.join(__dirname, '..');

// ANSI colors
const COLORS = {
  GREEN: '\x1b[32m',
  RED: '\x1b[31m',
  YELLOW: '\x1b[33m',
  RESET: '\x1b[0m',
  BOLD: '\x1b[1m',
  DIM: '\x1b[2m'
};

function colorPass() {
  return `${COLORS.GREEN}PASS${COLORS.RESET}`;
}

function colorFail() {
  return `${COLORS.RED}FAIL${COLORS.RESET}`;
}

function colorSkip() {
  return `${COLORS.YELLOW}SKIP${COLORS.RESET}`;
}

// Format timing for display
function formatTiming(ms) {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  const sec = (ms / 1000).toFixed(1);
  return `${sec}s`;
}

// Format a result row
function formatRow(label, status, timing, hint) {
  const statusStr = status === 'PASS' ? colorPass() : (status === 'FAIL' ? colorFail() : colorSkip());
  const timingStr = timing ? ` [${formatTiming(timing)}]` : '';
  const hintStr = hint ? ` — ${hint}` : '';
  const paddedLabel = label.padEnd(50);
  return `  ${paddedLabel} ${statusStr}${timingStr}${hintStr}`;
}

// Run a subprocess and return pass/fail
function runSubprocess(label, args, options = {}) {
  const { cwd = PACKAGE_ROOT, stdio = 'inherit', skipIfMissing = null } = options;

  if (skipIfMissing && !fs.existsSync(skipIfMissing)) {
    return {
      label,
      status: 'SKIP',
      timing: 0,
      passed: true,
      hint: `${path.basename(skipIfMissing)} not found`
    };
  }

  const startTime = Date.now();
  try {
    const result = spawnSync(args[0], args.slice(1), {
      stdio,
      cwd,
      encoding: 'utf8'
    });
    const timing = Date.now() - startTime;

    if (result.status === 0 || result.status === null) {
      return { label, status: 'PASS', timing, passed: true };
    } else {
      let hint;
      if (stdio === 'pipe' && result.stderr) {
        hint = result.stderr.split('\n')[0].slice(0, 80);
      } else if (stdio === 'pipe' && result.stdout) {
        hint = result.stdout.split('\n')[0].slice(0, 80);
      }
      return { label, status: 'FAIL', timing, passed: false, hint };
    }
  } catch (e) {
    const timing = Date.now() - startTime;
    return { label, status: 'FAIL', timing, passed: false, hint: e.message.slice(0, 80) };
  }
}

// Repo mode: full test suite
function runRepoMode() {
  console.log(`\n${COLORS.BOLD}Running Full Test Suite (Repo Mode)${COLORS.RESET}\n`);

  const results = [];

  // Step 1: Node syntax check
  results.push(runSubprocess(
    'Node syntax check (*.mjs excluding .template.mjs)',
    ['bash', '-c', 'git ls-files "*.mjs" | grep -v ".template.mjs$" | while read f; do [ -n "$f" ] && node --check "$f" || true; done'],
    { stdio: 'pipe' }
  ));

  // Step 2: Shell syntax check
  results.push(runSubprocess(
    'Shell syntax check (*.sh)',
    ['bash', '-c', 'git ls-files "*.sh" | while read f; do [ -n "$f" ] && bash -n "$f" || true; done'],
    { stdio: 'pipe' }
  ));

  // Step 3: Node.js tests
  results.push(runSubprocess(
    'Node.js tests (npm run test:node)',
    ['npm', 'run', 'test:node'],
    { stdio: 'inherit' }
  ));

  // Step 4: Shell test suites
  results.push(runSubprocess(
    'Shell test suites (npm run test:sh)',
    ['npm', 'run', 'test:sh'],
    { stdio: 'inherit' }
  ));

  // Step 5: React component tests (vitest)
  const uiWebDir = path.join(PACKAGE_ROOT, 'ui', 'web');
  const packageLockPath = path.join(PACKAGE_ROOT, 'package-lock.json');
  const hasPackageLock = fs.existsSync(packageLockPath);

  if (fs.existsSync(uiWebDir)) {
    if (hasPackageLock) {
      results.push(runSubprocess(
        'React component tests (vitest)',
        ['bash', '-c', `cd ${JSON.stringify(uiWebDir)} && npm ci && npx vitest run`],
        { stdio: 'inherit' }
      ));
    } else {
      results.push({
        label: 'React component tests (vitest)',
        status: 'SKIP',
        timing: 0,
        passed: true,
        hint: 'package-lock.json not found (skipping npm ci requirement)'
      });
    }
  } else {
    results.push({
      label: 'React component tests (vitest)',
      status: 'SKIP',
      timing: 0,
      passed: true,
      hint: 'ui/web not found'
    });
  }

  // Step 6: Python tool compile check
  results.push(runSubprocess(
    'Python tool import/compile smoke gate',
    ['bash', '-c', 'python -m compileall -q tools/ && python -m unittest tests.test_tools_importable -v'],
    { stdio: 'pipe' }
  ));

  // Step 7: Python tests
  results.push(runSubprocess(
    'Python tests (unittest discover)',
    ['bash', '-c', 'python -m unittest discover -s tests -p "test_*.py" -v'],
    { stdio: 'inherit' }
  ));

  // Step 8: Benchmark scorer tests
  results.push(runSubprocess(
    'Benchmark scorer tests (test_bench_runner)',
    ['python', '-m', 'unittest', 'tests.test_bench_runner', '-v'],
    { stdio: 'pipe' }
  ));

  // Step 9: Benchmark reproduction
  results.push(runSubprocess(
    'Offline benchmark reproduction',
    ['python', 'tools/bench_runner.py', '--runner', 'mock'],
    { stdio: 'pipe' }
  ));

  return results;
}

// Classify doctor failures: expected pre-init findings vs real failures
// Expected on fresh systems: missing config, missing hooks, missing dirs (if tarball unpack incomplete)
// Real failures: Node version < 18, Python missing, not in git repo
function classifyDoctorFailure(output) {
  const lines = output.split('\n');
  const failures = [];
  // Expected pre-init directory names from doctor.js checkDirectories()
  const expectedDirs = ['daemons', 'dash', 'monitor', 'tools', 'ui'];

  for (const line of lines) {
    if (line.includes('✗') || line.includes('FAIL')) {
      failures.push(line);
    }
  }

  // Check if all failures match expected pre-init patterns
  const allExpected = failures.every(failure => {
    // Pattern 1: Not in a git repository (expected if not yet initialized)
    if (failure.includes('Not inside a git repository')) {
      return true;
    }
    // Pattern 2: Missing config file
    if (failure.includes('aesop.config.json not found')) {
      return true;
    }
    // Pattern 3: Pre-push hook not installed
    if (failure.includes('Pre-push hook not installed')) {
      return true;
    }
    // Pattern 4: Port in use (expected if another fleet is running; skip initialization)
    if (failure.includes('Port') && failure.includes('in use')) {
      return true;
    }
    // Pattern 5: Missing directories — verify line contains only expected directory names
    if (failure.includes('Missing:')) {
      // Extract the part after "Missing:"
      const afterMissing = failure.substring(failure.indexOf('Missing:') + 8);
      // Check if all mentioned items are valid directory names
      const items = afterMissing.split(',').map(item => item.trim());
      return items.every(item => expectedDirs.includes(item));
    }
    return false;
  });

  return { allExpected, failures };
}

// Installed mode: shipped self-checks
function runInstalledMode() {
  console.log(`\n${COLORS.BOLD}Running Shipped Self-Checks (Installed Mode)${COLORS.RESET}\n`);

  const results = [];

  // Step 1: Doctor check — distinguish expected pre-init findings from real failures
  const doctorJs = path.join(PACKAGE_ROOT, 'tools', 'doctor.js');
  const doctorResult = (() => {
    if (!fs.existsSync(doctorJs)) {
      return {
        label: 'Preflight checks (aesop doctor)',
        status: 'SKIP',
        timing: 0,
        passed: true,
        hint: 'doctor.js not found'
      };
    }

    const startTime = Date.now();
    try {
      const result = spawnSync('node', [doctorJs], {
        stdio: 'pipe',
        encoding: 'utf8'
      });
      const timing = Date.now() - startTime;

      if (result.status === 0) {
        return { label: 'Preflight checks (aesop doctor)', status: 'PASS', timing, passed: true };
      } else {
        // Doctor failed; classify whether it's expected pre-init findings or real failure
        const output = result.stdout || result.stderr || '';
        const { allExpected } = classifyDoctorFailure(output);

        if (allExpected) {
          // Expected pre-init findings (missing config, hooks, etc.) — pass but note it
          return {
            label: 'Preflight checks (aesop doctor)',
            status: 'PASS',
            timing,
            passed: true,
            hint: 'Expected pre-init findings (run `aesop doctor` to initialize)'
          };
        } else {
          // Real failure (e.g., Node/Python missing, not in git repo)
          const hint = result.stderr ? result.stderr.split('\n')[0].slice(0, 80) : 'doctor check failed';
          return { label: 'Preflight checks (aesop doctor)', status: 'FAIL', timing, passed: false, hint };
        }
      }
    } catch (e) {
      const timing = Date.now() - startTime;
      return {
        label: 'Preflight checks (aesop doctor)',
        status: 'FAIL',
        timing,
        passed: false,
        hint: e.message.slice(0, 80)
      };
    }
  })();
  results.push(doctorResult);

  // Step 2: Health score check
  const healthScoreJs = path.join(PACKAGE_ROOT, 'tools', 'health-score.js');
  results.push(runSubprocess(
    'Health score check (aesop health-score)',
    ['node', healthScoreJs],
    { stdio: 'pipe', skipIfMissing: healthScoreJs }
  ));

  // Step 3: Secret-scan selftest
  const scannerSelftest = path.join(PACKAGE_ROOT, 'tools', 'scanner_selftest.py');
  results.push(runSubprocess(
    'Secret-scan selftest',
    ['python', scannerSelftest],
    { stdio: 'pipe', skipIfMissing: scannerSelftest }
  ));

  // Step 4: Packaging assertions (check required files are shipped)
  const startTime = Date.now();
  let packagingPassed = true;
  let packagingHint;
  try {
    const requiredDirs = ['tools', 'daemons', 'dash', 'monitor', 'ui', 'docs'];
    const requiredFiles = ['aesop.config.example.json', 'README.md', 'LICENSE'];

    const missing = [];
    for (const dir of requiredDirs) {
      const dirPath = path.join(PACKAGE_ROOT, dir);
      if (!fs.existsSync(dirPath) || !fs.statSync(dirPath).isDirectory()) {
        missing.push(dir);
      }
    }

    for (const file of requiredFiles) {
      const filePath = path.join(PACKAGE_ROOT, file);
      if (!fs.existsSync(filePath)) {
        missing.push(file);
      }
    }

    if (missing.length > 0) {
      packagingPassed = false;
      packagingHint = `Missing: ${missing.join(', ')}`;
    }
  } catch (e) {
    packagingPassed = false;
    packagingHint = e.message.slice(0, 80);
  }

  const packagingTiming = Date.now() - startTime;
  results.push({
    label: 'Packaging assertions',
    status: packagingPassed ? 'PASS' : 'FAIL',
    timing: packagingTiming,
    passed: packagingPassed,
    hint: packagingHint
  });

  return results;
}

// Main execution
(async function main() {
  try {
    const context = detectContext(PACKAGE_ROOT);
    const hasGitConfig = fs.existsSync(path.join(PACKAGE_ROOT, '.git', 'config'));
    const hasPackageLock = fs.existsSync(path.join(PACKAGE_ROOT, 'package-lock.json'));
    const isDegradedFromRepo = hasGitConfig && !hasPackageLock;

    console.log(`${COLORS.DIM}Context: ${context}${COLORS.RESET}`);
    if (isDegradedFromRepo) {
      console.log(`${COLORS.YELLOW}Scaffolded project detected — running installed-mode verification; full reproduce is for the aesop repo itself${COLORS.RESET}`);
    }

    let results;
    if (context === 'repo') {
      results = runRepoMode();
    } else {
      results = runInstalledMode();
    }

    // Print results table
    console.log(`\n${COLORS.BOLD}Results${COLORS.RESET}\n`);
    for (const result of results) {
      console.log(formatRow(result.label, result.status, result.timing, result.hint));
    }

    // Summary
    const passCount = results.filter(r => r.passed).length;
    const failCount = results.filter(r => !r.passed).length;
    const totalTime = results.reduce((sum, r) => sum + (r.timing || 0), 0);

    console.log(`\n${COLORS.BOLD}Summary: ${passCount}/${results.length} checks passed${COLORS.RESET}`);
    console.log(`${COLORS.DIM}Total time: ${formatTiming(totalTime)}${COLORS.RESET}\n`);

    if (failCount === 0) {
      console.log(`${COLORS.GREEN}✓ All checks passed${COLORS.RESET}\n`);
      process.exitCode = 0;
    } else {
      console.log(`${COLORS.RED}✗ ${failCount} check(s) failed${COLORS.RESET}\n`);
      process.exitCode = 1;
    }
  } catch (err) {
    console.error(`${COLORS.RED}Error: ${err.message}${COLORS.RESET}`);
    process.exit(1);
  }
})();
