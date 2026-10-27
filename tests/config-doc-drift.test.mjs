// Config/doc drift detection test.
// Ensures that config keys documented in docs/ and read by code consumers
// are all present in aesop.config.example.json — a tripwire against
// silent orphaning of live config keys during refactoring.
//
// Run: node --test tests/config-doc-drift.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, '..');

// List of config key paths that are documented and/or read by live code.
// Format: { file: string (relative path to consumer), keyPath: string (dot-notation path) }
// keyPath 'a.b.c' means config.a.b.c must be present (and non-null for required keys)
const REQUIRED_KEYS = [
  // From hooks/claude/force-model-policy.mjs (line 79):
  //   const m = cfg && cfg.cardinal_rules && cfg.cardinal_rules.subagent_model;
  // Documented at docs/archive/HOOK-INSTALL.md:245-247
  { file: 'hooks/claude/force-model-policy.mjs', keyPath: 'cardinal_rules.subagent_model' },

  // From monitor/collect-signals.mjs (lines 32-46):
  //   config.brain_root, config.scripts_root, config.temp_root, config.state_root
  { file: 'monitor/collect-signals.mjs', keyPath: 'brain_root' },
  { file: 'monitor/collect-signals.mjs', keyPath: 'scripts_root' },
  { file: 'monitor/collect-signals.mjs', keyPath: 'temp_root' },
  { file: 'monitor/collect-signals.mjs', keyPath: 'state_root' },

  // From monitor/collect-signals.mjs (lines 55-71, heartbeat thresholds at ~148-155):
  //   config.repos, config.monitor.log_max_lines, config.monitor.log_max_kb, config.monitor.extended_signals
  //   config.monitor.heartbeat_thresholds.{monitor,watchdog,default}
  { file: 'monitor/collect-signals.mjs', keyPath: 'repos' },
  { file: 'monitor/collect-signals.mjs', keyPath: 'monitor.log_max_lines' },
  { file: 'monitor/collect-signals.mjs', keyPath: 'monitor.log_max_kb' },
  { file: 'monitor/collect-signals.mjs', keyPath: 'monitor.extended_signals' },
  { file: 'monitor/collect-signals.mjs', keyPath: 'monitor.heartbeat_thresholds' },

  // From dash/dash-extra.mjs (lines 26-30):
  //   config.transcripts_root
  { file: 'dash/dash-extra.mjs', keyPath: 'transcripts_root' },

  // From ui/serve.py (lines 49, 57):
  //   config.get("state_root"), config.get("transcripts_root")
  { file: 'ui/serve.py', keyPath: 'state_root' },
  { file: 'ui/serve.py', keyPath: 'transcripts_root' }
];

// Helper: safely get a nested property by dot-notation path
function getByPath(obj, path) {
  const parts = path.split('.');
  let current = obj;
  for (const part of parts) {
    if (current == null) return undefined;
    current = current[part];
  }
  return current;
}

// Helper: check if a string value looks like prose (invalid config example)
function isProseLike(value) {
  if (typeof value !== 'string') return false;
  // Check for prose markers: parentheses, " or ", " env var", commas in descriptions
  const proseMarkers = [
    /\s+or\s+/i,        // "or override"
    /\s+env\s+var/i,    // "env var"
    /\(.*\)/,           // Contains parentheses with text
    /;\s*default:/i,    // Semicolon separating instructions
    /optional[;,]/i,    // "optional;" or "optional,"
  ];
  return proseMarkers.some(marker => marker.test(value));
}

// Helper: validate a config value based on key context
function isValidConfigValue(keyPath, value) {
  // Collections (arrays/objects) are always valid
  if (Array.isArray(value) || (typeof value === 'object' && value !== null)) {
    return true;
  }
  // Booleans are always valid
  if (typeof value === 'boolean') {
    return true;
  }
  // Numbers are always valid
  if (typeof value === 'number') {
    return true;
  }
  // Strings: must be non-empty and not prose-like
  if (typeof value === 'string') {
    if (!value.trim()) {
      return false; // Empty string
    }
    if (isProseLike(value)) {
      return false; // Looks like prose description, not config value
    }
    return true;
  }
  // null/undefined are not valid
  return false;
}

test('config/doc drift: all documented keys exist in aesop.config.example.json', () => {
  const examplePath = path.join(PROJECT_ROOT, 'aesop.config.example.json');
  assert.ok(fs.existsSync(examplePath), `aesop.config.example.json must exist at ${examplePath}`);

  let example;
  try {
    example = JSON.parse(fs.readFileSync(examplePath, 'utf8'));
  } catch (err) {
    throw new Error(`Failed to parse aesop.config.example.json: ${err.message}`);
  }

  const failures = [];
  for (const { file, keyPath } of REQUIRED_KEYS) {
    const value = getByPath(example, keyPath);
    // Check 1: path must exist (value is not undefined)
    if (value === undefined) {
      failures.push(`  ${keyPath} (read by ${file}) — missing key`);
      continue;
    }
    // Check 2: value must be a valid config example (not prose, not empty string)
    if (!isValidConfigValue(keyPath, value)) {
      failures.push(
        `  ${keyPath} (read by ${file}) — invalid value: "${value}" (must be non-empty, non-prose)`
      );
    }
  }

  if (failures.length > 0) {
    throw new Error(
      `Config validation failed in aesop.config.example.json:\n${failures.join('\n')}\n` +
      `Config values must be valid examples (non-empty, not prose descriptions). ` +
      `Use _paths section to document override options.`
    );
  }
});
