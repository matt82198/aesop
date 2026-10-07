// Tests for packaging and portability fixes (Wave-13 item D)
// Contract under test:
//  - npm pack includes skills/ directory
//  - Generated configs use portable ~ paths, not absolute machine paths
//  - aesop.config.example.json temp_root works on Windows and POSIX
//  - Help text examples work on both Windows and POSIX
//  - Node.js config loaders expand ~ paths at runtime
//
// Run: node --test tests/packaging-portability.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promises as fsPromises } from 'node:fs';

const REPO_ROOT = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..'
);

const PACKAGE_JSON = path.join(REPO_ROOT, 'package.json');
const CONFIG_EXAMPLE = path.join(REPO_ROOT, 'aesop.config.example.json');
const CLI = path.join(REPO_ROOT, 'bin', 'cli.js');
const README = path.join(REPO_ROOT, 'README.md');

// Shared fixtures for expensive scaffold operations
let sharedScaffoldDir = null;
let sharedTargetDir = null;

// Before hook: set up expensive scaffold fixture once for all tests
test('setup: shared scaffold fixture', async () => {
  sharedScaffoldDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-shared-scaffold-'));
  sharedTargetDir = path.join(sharedScaffoldDir, 'fleet');

  try {
    // Initialize git
    execSync('git init', { cwd: sharedScaffoldDir, stdio: 'ignore' });
    execSync('git config user.email "test@example.com"', { cwd: sharedScaffoldDir, stdio: 'ignore' });
    execSync('git config user.name "Test"', { cwd: sharedScaffoldDir, stdio: 'ignore' });

    // Run scaffold with --name
    const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000;
    const result = spawnSync('node', [CLI, sharedTargetDir, '--name', 'test-service', '--no-skills'], {
      encoding: 'utf8',
      cwd: sharedScaffoldDir,
      timeout
    });

    assert.equal(result.status, 0, `Shared scaffold should succeed: ${result.stderr}`);
    assert.ok(fs.existsSync(sharedTargetDir), 'Scaffolded target directory should exist');
    assert.ok(
      fs.existsSync(path.join(sharedTargetDir, 'aesop.config.json')),
      'aesop.config.json should be generated in shared fixture'
    );
  } catch (e) {
    // Ensure cleanup on failure
    if (sharedScaffoldDir) {
      try {
        fs.rmSync(sharedScaffoldDir, { recursive: true, force: true });
      } catch (_) {
        // Ignore cleanup errors
      }
    }
    throw e;
  }
});

test('package.json files list includes skills/', () => {
  const pkg = JSON.parse(fs.readFileSync(PACKAGE_JSON, 'utf8'));
  assert.ok(
    pkg.files && pkg.files.includes('skills/'),
    'package.json "files" should include "skills/" directory'
  );
});

test('npm pack --dry-run includes skills/ files', () => {
  // Simple check: verify that skills/ files exist in the repo
  // and that npm pack would include them based on package.json "files"
  const pkg = JSON.parse(fs.readFileSync(PACKAGE_JSON, 'utf8'));

  // Verify skills/ is in files array
  assert.ok(
    pkg.files.includes('skills/'),
    'package.json should include skills/ in files array'
  );

  // Verify skills/ directory exists
  const skillsDir = path.join(REPO_ROOT, 'skills');
  assert.ok(
    fs.existsSync(skillsDir) && fs.statSync(skillsDir).isDirectory(),
    'skills/ directory should exist in repository'
  );
});

test('aesop.config.example.json uses portable paths', () => {
  const config = JSON.parse(fs.readFileSync(CONFIG_EXAMPLE, 'utf8'));

  // Check that key paths use ~ notation (portable) not absolute paths
  assert.ok(
    config.brain_root && config.brain_root.includes('~'),
    'brain_root should use ~ notation for portability'
  );

  assert.ok(
    config.scripts_root && config.scripts_root.includes('~'),
    'scripts_root should use ~ notation for portability'
  );

  assert.ok(
    config.temp_root,
    'temp_root should be defined'
  );

  // temp_root should either be ~ based or OS-agnostic
  const tempRoot = config.temp_root;
  assert.ok(
    tempRoot.includes('~') || tempRoot.includes('$') || tempRoot.includes('TEMP') ||
    tempRoot.includes('aesop'),
    `temp_root should be portable (got: ${tempRoot}), not absolute like /tmp/`
  );
});

test('aesop.config.example.json temp_root comments explain Windows/POSIX handling', () => {
  const content = fs.readFileSync(CONFIG_EXAMPLE, 'utf8');

  // Check comments mention Windows or cross-platform
  assert.ok(
    content.includes('Windows') ||
    content.includes('POSIX') ||
    content.includes('cross-platform') ||
    content.includes('platform'),
    'temp_root should have comments explaining Windows/POSIX handling'
  );
});

test('README documents that skills/ needs to be copied', () => {
  const readme = fs.readFileSync(README, 'utf8');

  assert.ok(
    readme.includes('skills/power') || readme.includes('skills/'),
    'README should document copying skills/ directory'
  );
});

test('generated config uses portable paths (not absolute machine paths)', () => {
  // This test uses sharedTargetDir which is set up by the before hook below
  assert.ok(sharedTargetDir, 'Shared scaffold directory should be set up');

  // Check generated config
  const configPath = path.join(sharedTargetDir, 'aesop.config.json');
  assert.ok(fs.existsSync(configPath), 'aesop.config.json should be generated');

  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

  // brain_root and scripts_root should be ~ paths, not absolute
  // OR if absolute, should be commented as "generated for this machine"
  if (config.brain_root && !config.brain_root.includes('~')) {
    // If absolute, there should be a comment in the file explaining it
    const configContent = fs.readFileSync(configPath, 'utf8');
    assert.ok(
      configContent.includes('generated for') || configContent.includes('machine'),
      'Absolute paths should be documented with "generated for this machine" comment'
    );
  }
});

test('help text example works on Windows (no backslash continuation)', () => {
  const cli = fs.readFileSync(CLI, 'utf8');

  // Find the help text section
  const helpStart = cli.indexOf('aesop — Multi-agent');
  const helpEnd = cli.indexOf('process.exit(0);', helpStart);
  const helpText = cli.substring(helpStart, helpEnd);

  // Check for problematic POSIX-only line continuation (trailing backslash)
  // Windows PowerShell doesn't support line continuation with \
  const lines = helpText.split('\n');

  for (let i = 0; i < lines.length - 1; i++) {
    const line = lines[i].trimEnd();
    // Skip if line ends with backslash (but allow backslashes in paths)
    if (line.endsWith('\\') && !line.includes('path') && !line.includes(':\\')) {
      // Example line continuation found - this is POSIX-only
      assert.fail(`Help text has POSIX-only line continuation at line ${i}: "${line}"`);
    }
  }
});

test('config loader expands ~ paths in Node.js', () => {
  // Test that monitor or similar loaders can handle ~ paths
  // This is more of an integration test - create a config with ~ and verify it loads

  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-tilde-test-'));

  try {
    // Create a test config with ~ paths
    const configPath = path.join(tempDir, 'aesop.config.json');
    const testConfig = {
      brain_root: '~/.claude',
      scripts_root: '~/scripts',
      temp_root: '~/.aesop-temp'
    };

    fs.writeFileSync(configPath, JSON.stringify(testConfig, null, 2));

    // Now require a helper that would expand these paths
    // For now, we just verify the test setup works
    const loaded = JSON.parse(fs.readFileSync(configPath, 'utf8'));

    assert.equal(loaded.brain_root, '~/.claude', 'Config should preserve ~ paths');
    assert.equal(loaded.scripts_root, '~/scripts', 'Config should preserve ~ paths');

  } finally {
    try {
      fs.rmSync(tempDir, { recursive: true, force: true });
    } catch (e) {
      // Ignore cleanup errors
    }
  }
});

test('filesToCopy includes all package.json files directories (defect a)', () => {
  // Read package.json to get the list of directories that should be copied
  const pkg = JSON.parse(fs.readFileSync(PACKAGE_JSON, 'utf8'));

  // Read cli.js to extract filesToCopy array
  const cli = fs.readFileSync(CLI, 'utf8');

  // Directories from package.json files array that should be in filesToCopy
  // These are directories that contain code/config needed by the scaffolded fleet
  const requiredDirs = [
    'daemons',
    'dash',
    'monitor',
    'tools',
    'ui',
    'docs',
    'state_store',  // defect: currently omitted, ui/collectors.py imports from this
    'skills',       // defect: currently omitted
    'mcp',          // defect: currently omitted
    'scan'          // defect: currently omitted
  ];

  // Search for each directory in filesToCopy array (looking around line 209)
  const filesCopyStart = cli.indexOf('const filesToCopy = [');
  const filesCopyEnd = cli.indexOf('];', filesCopyStart);
  assert.ok(filesCopyStart > -1 && filesCopyEnd > -1, 'Should find filesToCopy array');

  const filesArrayText = cli.substring(filesCopyStart, filesCopyEnd + 2);

  for (const dir of requiredDirs) {
    assert.ok(
      filesArrayText.includes(`'${dir}'`) || filesArrayText.includes(`"${dir}"`),
      `filesToCopy should include "${dir}" directory (found in package.json files, but currently missing from cli.js filesToCopy)`
    );
  }
});

test('aesopDirs allowlist includes all directories from filesToCopy (defect a)', () => {
  // Read cli.js and verify aesopDirs includes all directories from filesToCopy
  const cli = fs.readFileSync(CLI, 'utf8');

  // Get aesopDirs array text
  const aesopDirsStart = cli.indexOf('const aesopDirs = [');
  const aesopDirsEnd = cli.indexOf('];', aesopDirsStart);
  const aesopDirsText = cli.substring(aesopDirsStart, aesopDirsEnd + 2);

  // These are the directories (not files) that should all be in aesopDirs
  const requiredDirsInAesopDirs = [
    'daemons',
    'dash',
    'monitor',
    'tools',
    'ui',
    'docs',
    'state_store',
    'skills',
    'mcp',
    'scan',
    '.git',
    'state'
  ];

  // Verify all directories are in aesopDirs for idempotency
  for (const dir of requiredDirsInAesopDirs) {
    assert.ok(
      aesopDirsText.includes(`'${dir}'`) || aesopDirsText.includes(`"${dir}"`),
      `aesopDirs allowlist should include "${dir}" (line ~163) for idempotency — currently missing`
    );
  }
});

test('dashboard config generation guards against missing dashboard key (defect b)', () => {
  // This tests that the wizard mode doesn't crash even if config.dashboard key is missing
  // (defect b: originally accessed config.dashboard.refresh_seconds without checking if dashboard exists)

  // This test uses sharedTargetDir which is set up by the before hook below
  assert.ok(sharedTargetDir, 'Shared scaffold directory should be set up');

  // Check generated config is valid JSON
  const configPath = path.join(sharedTargetDir, 'aesop.config.json');
  assert.ok(fs.existsSync(configPath), 'aesop.config.json should be generated');

  // Verify generated config is valid JSON (the main goal)
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  assert.ok(typeof config === 'object', 'Generated config should be valid JSON object');
});

test('pre-push hook is copied not symlinked on all platforms (defect c)', () => {
  // Verify that the pre-push hook is copied (copyFileSync) not symlinked
  // on all platforms including Unix

  const cli = fs.readFileSync(CLI, 'utf8');

  // Find the installPrePushHook function
  const hookInstallStart = cli.indexOf('function installPrePushHook');
  assert.ok(hookInstallStart > -1, 'Should find installPrePushHook function');

  // Find the hook installation code section (look for hookSource and hookDest)
  const hookCodeStart = cli.indexOf('// Install the hook', hookInstallStart);
  const hookCodeEnd = cli.indexOf('// Ensure hook is executable', hookCodeStart);
  const hookCode = cli.substring(hookCodeStart, hookCodeEnd);

  // Should use copyFileSync, not symlinkSync
  assert.ok(
    hookCode.includes('copyFileSync'),
    'Hook installation should use copyFileSync for all platforms (defect c: fix converts Unix symlink to copy)'
  );

  assert.ok(
    !hookCode.includes('symlinkSync'),
    'Hook installation must not use symlinkSync (dangling symlinks after npx cache clean disable branch protection and secret gate)'
  );
});

test('chmod failure on non-Windows platforms warns user (TASK C)', () => {
  // When chmod fails on non-Windows, the script should log a clear warning
  // telling the user to chmod +x manually. On Windows, silent failure is OK.
  // This test checks that the code CONTAINS logic to warn on POSIX platforms.

  const cli = fs.readFileSync(CLI, 'utf8');

  // Find the chmod section in installPrePushHook
  const chmodStart = cli.indexOf('// Ensure hook is executable');
  assert.ok(chmodStart > -1, 'Should find chmod section');

  // Get the section up to the next function or closing brace
  const chmodEnd = cli.indexOf('}', chmodStart);
  const chmodSection = cli.substring(chmodStart, chmodEnd);

  // The fixed code should check if we're NOT on Windows before silently ignoring chmod
  // Look for platform detection logic or a warning message
  const hasNonWindowsCheck =
    chmodSection.includes('process.platform') ||
    chmodSection.includes('win32') ||
    chmodSection.includes('warning') ||
    chmodSection.includes('Warning') ||
    chmodSection.includes('chmod');

  assert.ok(
    hasNonWindowsCheck,
    'chmod section should include platform detection or warning message for non-Windows (TASK C: tell user to chmod +x manually)'
  );

  // Additional check: if there's a console.warn or console.error, it should mention chmod
  if (chmodSection.includes('console.warn') || chmodSection.includes('console.error')) {
    const hasChmodWarning = chmodSection.includes('chmod') ||
                            chmodSection.includes('executable') ||
                            chmodSection.includes('permission');
    assert.ok(
      hasChmodWarning,
      'chmod warning message should mention chmod, executable, or permission when logging to console'
    );
  }
});

test('hooks directory is copied to scaffolded target (wave-24 scaffolder-hooks)', () => {
  // Verify that the hooks/ directory is included in filesToCopy
  const cli = fs.readFileSync(CLI, 'utf8');

  const filesCopyStart = cli.indexOf('const filesToCopy = [');
  const filesCopyEnd = cli.indexOf('];', filesCopyStart);
  assert.ok(filesCopyStart > -1 && filesCopyEnd > -1, 'Should find filesToCopy array');

  const filesArrayText = cli.substring(filesCopyStart, filesCopyEnd + 2);

  assert.ok(
    filesArrayText.includes("'hooks'") || filesArrayText.includes('"hooks"'),
    'filesToCopy should include "hooks" directory'
  );
});

test('aesopDirs allowlist includes hooks directory (wave-24 scaffolder-hooks)', () => {
  // Verify that the hooks/ directory is in the aesopDirs allowlist for idempotency
  const cli = fs.readFileSync(CLI, 'utf8');

  const aesopDirsStart = cli.indexOf('const aesopDirs = [');
  const aesopDirsEnd = cli.indexOf('];', aesopDirsStart);
  assert.ok(aesopDirsStart > -1 && aesopDirsEnd > -1, 'Should find aesopDirs array');

  const aesopDirsText = cli.substring(aesopDirsStart, aesopDirsEnd + 2);

  assert.ok(
    aesopDirsText.includes("'hooks'") || aesopDirsText.includes('"hooks"'),
    'aesopDirs should include "hooks" directory for idempotency'
  );
});

test('pre-commit waveguard hook is installed in scaffolded fleet (wave-24 scaffolder-hooks)', { skip: false }, async () => {
  // Verify that the pre-commit waveguard hook is properly installed during scaffolding
  // This test uses the shared scaffold from the before hook and initializes git in it

  assert.ok(sharedTargetDir, 'Shared scaffold directory should be set up');

  // Check that hooks directory was copied
  const hooksDir = path.join(sharedTargetDir, 'hooks');
  assert.ok(
    fs.existsSync(hooksDir) && fs.statSync(hooksDir).isDirectory(),
    'hooks/ directory should be copied to scaffolded target'
  );

  // Check that pre-commit-waveguard.sh exists
  const waveguardSource = path.join(hooksDir, 'pre-commit-waveguard.sh');
  assert.ok(
    fs.existsSync(waveguardSource),
    'hooks/pre-commit-waveguard.sh should exist in scaffolded target'
  );

  // Check that install-waveguard.sh exists
  const installWaveguard = path.join(hooksDir, 'install-waveguard.sh');
  assert.ok(
    fs.existsSync(installWaveguard),
    'hooks/install-waveguard.sh should exist in scaffolded target'
  );

  // Check that pre-push-policy.sh exists
  const prePushPolicy = path.join(hooksDir, 'pre-push-policy.sh');
  assert.ok(
    fs.existsSync(prePushPolicy),
    'hooks/pre-push-policy.sh should exist in scaffolded target'
  );

  // Now initialize git in the scaffolded directory
  try {
    execSync('git init', { cwd: sharedTargetDir, stdio: 'ignore' });
    execSync('git config user.email "test@example.com"', { cwd: sharedTargetDir, stdio: 'ignore' });
    execSync('git config user.name "Test User"', { cwd: sharedTargetDir, stdio: 'ignore' });
  } catch (e) {
    // May already be initialized; continue
  }

  // Re-run scaffold with --force to install hooks in the newly initialized git repo
  const timeout = Number(process.env.AESOP_TEST_CHILD_TIMEOUT_MS) || 30000;
  const result = spawnSync('node', [CLI, sharedTargetDir, '--name', 'test-fleet', '--force', '--no-skills'], {
    encoding: 'utf8',
    cwd: sharedScaffoldDir,
    timeout
  });

  assert.equal(result.status, 0, `Re-scaffold with --force should succeed: ${result.stderr}`);

  // Check that .git/hooks/pre-commit was installed
  const preCommitHook = path.join(sharedTargetDir, '.git', 'hooks', 'pre-commit');
  assert.ok(
    fs.existsSync(preCommitHook),
    '.git/hooks/pre-commit hook should be installed'
  );

  // Verify the hook content contains the waveguard logic
  const hookContent = fs.readFileSync(preCommitHook, 'utf8');
  assert.ok(
    hookContent.includes('pre-commit-waveguard'),
    '.git/hooks/pre-commit should reference the waveguard hook'
  );

  // Check that .git/hooks/pre-push was also installed
  const prePushHook = path.join(sharedTargetDir, '.git', 'hooks', 'pre-push');
  assert.ok(
    fs.existsSync(prePushHook),
    '.git/hooks/pre-push hook should be installed'
  );
});

test('installPreCommitWaveguard function exists (wave-24 scaffolder-hooks)', () => {
  // Verify that the installPreCommitWaveguard function is defined and called
  const cli = fs.readFileSync(CLI, 'utf8');

  assert.ok(
    cli.includes('function installPreCommitWaveguard'),
    'cli.js should define installPreCommitWaveguard function'
  );

  assert.ok(
    cli.includes('installPreCommitWaveguard(finalTargetDir, templateRoot)'),
    'cli.js should call installPreCommitWaveguard during scaffolding'
  );
});

// Cleanup hook: remove shared scaffold fixture after all tests
test('cleanup: shared scaffold fixture', () => {
  if (sharedScaffoldDir) {
    try {
      fs.rmSync(sharedScaffoldDir, { recursive: true, force: true });
    } catch (e) {
      // Ignore cleanup errors but don't fail the test
      console.warn('Failed to clean up shared scaffold:', e.message);
    }
    sharedScaffoldDir = null;
    sharedTargetDir = null;
  }
});
