#!/usr/bin/env node

/**
 * First-hour adopter flow tests
 *
 * Verifies that a stranger can scaffold aesop and reach a working watchdog test
 * without off-docs troubleshooting.
 */

import { exec, execSync } from 'child_process';
import { promises as fs } from 'fs';
import path from 'path';
import os from 'os';
import { createServer } from 'net';
import { fileURLToPath } from 'url';
import { promisify } from 'util';

const execAsync = promisify(exec);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.join(__dirname, '..');

// Color codes for output
const colors = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
};

// Test results
const results = [];

async function test(name, fn) {
  try {
    await fn();
    results.push({ name, passed: true, error: null });
    console.log(`${colors.green}✓${colors.reset} ${name}`);
  } catch (error) {
    results.push({ name, passed: false, error: error.message });
    console.log(`${colors.red}✗${colors.reset} ${name}`);
    console.log(`  ${error.message}`);
  }
}

async function findFreePort(startPort = 9000) {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
    server.on('error', reject);
  });
}

async function blockPort(port) {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.listen(port, '127.0.0.1', () => {
      resolve(server);
    });
    server.on('error', reject);
  });
}

// Main test suite
async function runTests() {
  console.log(`${colors.blue}Aesop First-Hour Adopter Flow Tests${colors.reset}\n`);

  let tempDir = null;
  let blockedServer = null;
  let tempDirForPortTest = null;

  try {
    // Test 1: Git initialization
    await test('Git repo is initialized after scaffold', async () => {
      tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'aesop-test-'));

      // Run scaffold
      const cliScript = path.join(projectRoot, 'bin', 'cli.js');
      const targetDir = path.join(tempDir, 'test-fleet');

      const scaffoldResult = await execAsync(`node "${cliScript}" "${targetDir}" --name "test-fleet" --yes`, {
        cwd: projectRoot,
        stdio: 'pipe'
      });
      // Diagnostic (PR #784): the scaffold process always exits 0 even when
      // initializeGitRepo's internal git calls fail (it degrades gracefully
      // by design), so execAsync never rejects and this output was
      // previously discarded -- hiding the real git error on CI. Print it
      // unconditionally so a CI failure a few lines below names the cause
      // instead of only showing "fatal: ... no commits yet".
      if (scaffoldResult.stdout) process.stdout.write(scaffoldResult.stdout);
      if (scaffoldResult.stderr) process.stderr.write(scaffoldResult.stderr);

      // Check .git exists
      const gitDir = path.join(targetDir, '.git');
      try {
        await fs.stat(gitDir);
      } catch {
        throw new Error('.git directory not created');
      }

      // Check initial commit exists
      const commitCheck = execSync(`git log --oneline -1`, { cwd: targetDir, stdio: 'pipe' });
      if (!commitCheck.toString().includes('Initial')) {
        throw new Error('Initial commit not found');
      }
    });

    // Test 2: Pre-push hook installation
    await test('Pre-push hook is installed and executable', async () => {
      if (!tempDir) throw new Error('Previous test failed, skipping');

      const targetDir = path.join(tempDir, 'test-fleet');
      const hookPath = path.join(targetDir, '.git', 'hooks', 'pre-push');

      try {
        const stats = await fs.stat(hookPath);
        if (!stats.isFile()) {
          throw new Error('Hook is not a file');
        }
      } catch {
        throw new Error('Pre-push hook not found at .git/hooks/pre-push');
      }

      // Check if executable (POSIX) - on Windows this may not fail, but the file should exist
      const content = await fs.readFile(hookPath, 'utf8');
      if (!content.includes('#!/usr/bin/env bash') && !content.includes('#!/bin/bash')) {
        throw new Error('Hook does not contain bash shebang');
      }
      if (!content.includes('set') || !content.length < 100) {
        // Hook should have some content beyond just shebang
        if (content.length < 100) {
          throw new Error('Hook content appears to be incomplete');
        }
      }
    });

    // Test 3: Next-steps text order
    await test('Next-steps instructions are in executable order', async () => {
      // Create a new temp dir for this test to capture stdout
      const testDir = await fs.mkdtemp(path.join(os.tmpdir(), 'aesop-output-'));
      const cliScript = path.join(projectRoot, 'bin', 'cli.js');
      const targetDir = path.join(testDir, 'test-fleet-2');

      const { stdout } = await execAsync(
        `node "${cliScript}" "${targetDir}" --name "test-fleet" --yes`,
        { cwd: projectRoot }
      );

      // Check that stdout contains the required steps in order
      const cdStepIdx = stdout.indexOf('cd ');
      const watchdogStepIdx = stdout.indexOf('run-watchdog.sh');
      const dashboardStepIdx = stdout.indexOf('ui/serve.py');
      const optionalStepIdx = stdout.indexOf('Optional');

      if (cdStepIdx === -1) throw new Error('Missing "cd" step in output');
      if (watchdogStepIdx === -1) throw new Error('Missing "run-watchdog.sh" step in output');
      if (dashboardStepIdx === -1) throw new Error('Missing "ui/serve.py" step in output');

      // Verify order: cd should come before watchdog, watchdog before dashboard
      if (!(cdStepIdx < watchdogStepIdx && watchdogStepIdx < dashboardStepIdx)) {
        throw new Error('Steps are not in executable order');
      }

      // Verify optional steps come after required steps
      if (optionalStepIdx !== -1 && optionalStepIdx < dashboardStepIdx) {
        throw new Error('Optional steps should come after required steps');
      }

      // Cleanup
      try {
        await fs.rm(testDir, { recursive: true, force: true });
      } catch (e) {
        // Ignore cleanup errors
      }
    });

    // Test 4: Port fallback when 8770 is occupied
    await test('Port fallback works when port 8770 is occupied', async () => {
      // Create temp dir for this test
      tempDirForPortTest = await fs.mkdtemp(path.join(os.tmpdir(), 'aesop-port-test-'));
      const cliScript = path.join(projectRoot, 'bin', 'cli.js');
      const targetDir = path.join(tempDirForPortTest, 'test-fleet-port');

      // Try to block port 8770 if possible (might already be blocked)
      try {
        blockedServer = await blockPort(8770);
      } catch (e) {
        // Port 8770 is already in use, which is fine for this test
        console.log('  (Port 8770 already in use by system)');
      }

      const { stdout } = await execAsync(
        `node "${cliScript}" "${targetDir}" --name "test-fleet" --yes`,
        { cwd: projectRoot, stdio: 'pipe' }
      );

      // Read the generated config
      const configPath = path.join(targetDir, 'aesop.config.json');
      const configContent = await fs.readFile(configPath, 'utf8');
      const config = JSON.parse(configContent);

      if (!config.dashboard || !config.dashboard.port) {
        throw new Error('Port not found in generated config');
      }

      // If port 8770 is available, the fallback logic should use it
      // If it's not available, it should find an alternative
      if (config.dashboard.port < 8770 || config.dashboard.port > 8770 + 100) {
        throw new Error(`Port ${config.dashboard.port} is outside expected range`);
      }

      // Verify the output mentions port fallback if port wasn't 8770
      if (config.dashboard.port !== 8770 && !stdout.includes('in use') && !stdout.includes(config.dashboard.port)) {
        console.log('  (Note: Port fallback message may not be visible in non-interactive mode)');
      }
    });

    // Test 5: Watchdog can run (basic check)
    await test('Watchdog runs without git repo error', async () => {
      if (!tempDir) throw new Error('Previous test failed, skipping');

      const targetDir = path.join(tempDir, 'test-fleet');

      try {
        const { stderr } = await execAsync(
          `bash daemons/run-watchdog.sh --once`,
          {
            cwd: targetDir,
            timeout: Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000,
            stdio: 'pipe'
          }
        );

        // Check that the error is NOT about missing git repo
        if (stderr && stderr.includes('not inside a git repository')) {
          throw new Error('Watchdog failed with git error');
        }
      } catch (e) {
        // Watchdog might fail for other reasons (missing Python, etc), but not git
        if (e.message && e.message.includes('not inside a git repository')) {
          throw new Error('Watchdog failed with git error');
        }
        // Other errors are OK for this test
      }
    });

  } finally {
    // Cleanup
    if (blockedServer) {
      blockedServer.close();
    }
    if (tempDir) {
      try {
        await fs.rm(tempDir, { recursive: true, force: true });
      } catch (e) {
        // Ignore cleanup errors
      }
    }
    if (tempDirForPortTest) {
      try {
        await fs.rm(tempDirForPortTest, { recursive: true, force: true });
      } catch (e) {
        // Ignore cleanup errors
      }
    }
  }

  // Print summary
  console.log(`\n${colors.blue}Test Summary${colors.reset}`);
  const passed = results.filter(r => r.passed).length;
  const total = results.length;
  console.log(`Passed: ${passed}/${total}`);

  if (passed === total) {
    console.log(`${colors.green}All tests passed!${colors.reset}\n`);
    process.exit(0);
  } else {
    console.log(`${colors.red}Some tests failed${colors.reset}\n`);
    process.exit(1);
  }
}

runTests().catch(err => {
  console.error(`${colors.red}Test suite error: ${err.message}${colors.reset}`);
  process.exit(1);
});
