import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';
import fs from 'fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Use createRequire to import CommonJS modules
const require = createRequire(import.meta.url);

// Import detectContext from detect-context.js (exported via require)
const { detectContext } = require(path.join(__dirname, '..', 'tools', 'detect-context.js'));

// Read and parse the reproduce.js file to extract classifyDoctorFailure function
const reproduceFilePath = path.join(__dirname, '..', 'tools', 'reproduce.js');
const reproduceContent = fs.readFileSync(reproduceFilePath, 'utf8');

// Extract the classifyDoctorFailure function
// It's defined as: function name(args) { ... }
// We'll use a regex to find and extract it, then eval it (safe in test context)
const classifyFunctionMatch = reproduceContent.match(
  /function classifyDoctorFailure\(output\)\s*\{[\s\S]*?\n\}/
);

let classifyDoctorFailure;
if (classifyFunctionMatch) {
  // Evaluate the function in isolation
  eval(`classifyDoctorFailure = ${classifyFunctionMatch[0]}`);
} else {
  throw new Error('Could not extract classifyDoctorFailure function from reproduce.js');
}

test('classifyDoctorFailure - genuine pre-init findings should be classified as expected', (t) => {
  // Test case 1: Missing config file (genuine pre-init finding)
  const output1 = '✗ aesop.config.json not found';
  const result1 = classifyDoctorFailure(output1);
  assert.equal(result1.allExpected, true, 'Missing config should be expected pre-init finding');

  // Test case 2: Missing pre-push hook (genuine pre-init finding)
  const output2 = '✗ Pre-push hook not installed at .git/hooks/pre-push';
  const result2 = classifyDoctorFailure(output2);
  assert.equal(result2.allExpected, true, 'Missing hook should be expected pre-init finding');

  // Test case 3: Missing directories (genuine pre-init finding)
  const output3 = '✗ Missing: daemons, tools, ui';
  const result3 = classifyDoctorFailure(output3);
  assert.equal(result3.allExpected, true, 'Missing directories should be expected pre-init finding');
});

test('classifyDoctorFailure - real failures should NOT be classified as expected', (t) => {
  // Test case 1: Missing Python is a REAL failure, not pre-init
  const output1 = 'FAIL python3 or python not found on PATH';
  const result1 = classifyDoctorFailure(output1);
  assert.equal(result1.allExpected, false, 'Missing Python should NOT be expected pre-init finding');

  // Test case 2: Missing Node version is a REAL failure
  const output2 = 'FAIL Found Node.js v16.0.0, need >=18';
  const result2 = classifyDoctorFailure(output2);
  assert.equal(result2.allExpected, false, 'Node version issue should NOT be expected pre-init finding');

  // Test case 3: Custom "Missing:" message not matching directory names
  // This is the critical test case that reveals the defect
  const output3 = '✗ Missing: Python 3.8+';
  const result3 = classifyDoctorFailure(output3);
  assert.equal(result3.allExpected, false, 'Missing Python should NOT match bare "Missing:" pattern');
});

test('classifyDoctorFailure - mixed output with one real failure should fail', (t) => {
  // Real scenario: some pre-init findings + one real failure = overall FAIL
  const output = `
✗ aesop.config.json not found
✗ Pre-push hook not installed at .git/hooks/pre-push
✗ Missing: Python 3.8+
`;
  const result = classifyDoctorFailure(output);
  assert.equal(result.allExpected, false, 'Mixed output with real failure should not be all expected');
});

test('classifyDoctorFailure - multiple pre-init findings should all be expected', (t) => {
  const output = `
✗ aesop.config.json not found
✗ Pre-push hook not installed at .git/hooks/pre-push
✗ Missing: daemons, dash, monitor, tools, ui
`;
  const result = classifyDoctorFailure(output);
  assert.equal(result.allExpected, true, 'All pre-init findings should be expected');
});

test('classifyDoctorFailure - empty failures should be expected', (t) => {
  const output = '✓ All checks passed';
  const result = classifyDoctorFailure(output);
  assert.equal(result.allExpected, true, 'Empty failures should be expected (all pass case)');
});
