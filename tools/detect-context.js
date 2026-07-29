#!/usr/bin/env node

/**
 * detectContext — Identify whether aesop is running in repo or installed mode.
 *
 * Exported as a module for testing. Also usable as a CLI tool.
 *
 * Returns: 'repo' | 'installed'
 *
 * Repo mode requires positive identification as the aesop repo:
 *  - package.json with name === "@matt82198/aesop"
 *  - .git (directory or gitlink file) for development checkouts or worktrees
 *  - tests/ directory with test files
 *  - package.json scripts include both test:node and test:py
 *
 * This guards against false positives when aesop is extracted in a directory
 * with a parent git repo or a parent package.json.
 */

const fs = require('fs');
const path = require('path');

/**
 * Detect context: repo checkout vs installed package.
 *
 * @param {string} packageRoot - Path to check (defaults to tools/..)
 * @returns {string} - 'repo' or 'installed'
 */
function detectContext(packageRoot = null) {
  if (!packageRoot) {
    // Default: tools/.. (parent of this script's directory)
    packageRoot = path.join(__dirname, '..');
  }

  // Repo checkout must be positively identified as the aesop repo itself:
  // - package.json with name === "@matt82198/aesop"
  // - .git (directory or gitlink file) for development checkouts or worktrees
  // - tests/ directory with test files
  // - package.json scripts include both test:node and test:py
  //
  // This guards against false positives: a parent directory with git + package.json
  // will not be mistaken for repo mode.

  const gitPath = path.join(packageRoot, '.git');
  const hasGit = fs.existsSync(gitPath);  // Works for both .git dir and .git gitlink file
  const hasTestsDir = fs.existsSync(path.join(packageRoot, 'tests'));
  const hasPackageJson = fs.existsSync(path.join(packageRoot, 'package.json'));

  let isAesopRepo = false;
  if (hasPackageJson) {
    try {
      const pkg = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
      const isCorrectPackage = pkg.name === '@matt82198/aesop';
      const hasTestScripts = pkg.scripts && pkg.scripts['test:node'] && pkg.scripts['test:py'];
      isAesopRepo = isCorrectPackage && hasGit && hasTestsDir && hasTestScripts;
    } catch {
      isAesopRepo = false;
    }
  }

  // Repo mode requires positive identification of the aesop repo itself
  // Anything else is treated as installed (safer than guessing)
  return isAesopRepo ? 'repo' : 'installed';
}

// CLI support: if run directly, print the context
if (require.main === module) {
  const args = process.argv.slice(2);
  const packageRoot = args[0] || undefined;
  const context = detectContext(packageRoot);
  console.log(context);
  process.exit(0);
}

// Export for testing and programmatic use
module.exports = { detectContext };
