// INDEX: Node mirror of tools/common.py validate_ci_config()/load_aesop_config(): validates the `ci` block of aesop.config.json (mode list over hosted|self-hosted-runner|local-receipt-gate, windowsMatrix hosted|self-hosted|skip-on-pr, receiptGate off|alongside|required, selfHostedLabels) with the SAME finding codes as the Python validator -- tests/test_ci_config.py drives both on identical fixtures; consumed by tools/doctor.js (config check) and bin/cli.js
'use strict';

const fs = require('fs');
const path = require('path');

const CI_MODES = ['hosted', 'self-hosted-runner', 'local-receipt-gate'];
const CI_WINDOWS_MATRIX = ['hosted', 'self-hosted', 'skip-on-pr'];
const CI_RECEIPT_GATES = ['off', 'alongside', 'required'];
const CI_DEFAULT_LABELS = ['self-hosted', 'windows', 'aesop-box'];
const CI_KNOWN_KEYS = ['mode', 'windowsMatrix', 'receiptGate', 'selfHostedLabels'];
const VERIFY_RECEIPT_WORKFLOW = path.join('.github', 'workflows', 'verify-receipt.yml');

function defaultCiConfig() {
  return {
    mode: ['hosted'],
    windowsMatrix: 'hosted',
    receiptGate: 'off',
    selfHostedLabels: CI_DEFAULT_LABELS.slice()
  };
}

function finding(code, message) {
  return { code, message };
}

/**
 * Validate the optional `ci` block. Mirrors tools/common.py validate_ci_config()
 * rule-for-rule; any divergence fails tests/test_ci_config.py parity.
 *
 * @param {object} config parsed aesop.config.json
 * @param {string} repoRoot directory the config lives in
 * @returns {Array<{code: string, message: string}>}
 */
function validateCiConfig(config, repoRoot) {
  const findings = [];
  if (!config || typeof config !== 'object' || Array.isArray(config) || !('ci' in config)) return findings;
  const ci = config.ci;
  if (!ci || typeof ci !== 'object' || Array.isArray(ci)) {
    return [finding('CI_NOT_OBJECT', 'ci must be an object')];
  }

  for (const key of Object.keys(ci)) {
    if (!CI_KNOWN_KEYS.includes(key)) {
      findings.push(finding('CI_UNKNOWN_KEY', `ci.${key} is not a known key (known: ${CI_KNOWN_KEYS.join(', ')})`));
    }
  }

  let modes = new Set();
  const mode = 'mode' in ci ? ci.mode : ['hosted'];
  if (!Array.isArray(mode)) {
    findings.push(finding('CI_MODE_NOT_LIST', 'ci.mode must be a list of modes'));
  } else if (mode.length === 0) {
    findings.push(finding('CI_MODE_EMPTY', 'ci.mode must name at least one mode'));
  } else {
    const unknown = mode.filter((m) => typeof m !== 'string' || !CI_MODES.includes(m));
    if (unknown.length) {
      findings.push(finding('CI_MODE_UNKNOWN', `ci.mode has unknown entries ${JSON.stringify(unknown)} (known: ${CI_MODES.join(', ')})`));
    }
    if (new Set(mode.map((m) => String(m))).size !== mode.length) {
      findings.push(finding('CI_MODE_DUPLICATE', 'ci.mode lists a mode more than once'));
    }
    modes = new Set(mode.filter((m) => typeof m === 'string' && CI_MODES.includes(m)));
  }

  const windowsMatrix = 'windowsMatrix' in ci ? ci.windowsMatrix : 'hosted';
  if (!CI_WINDOWS_MATRIX.includes(windowsMatrix)) {
    findings.push(finding('CI_WINDOWS_MATRIX_UNKNOWN', `ci.windowsMatrix must be one of ${CI_WINDOWS_MATRIX.join(', ')}`));
  } else if (windowsMatrix === 'self-hosted' && !modes.has('self-hosted-runner')) {
    findings.push(finding('CI_WINDOWS_MATRIX_NEEDS_RUNNER_MODE', "ci.windowsMatrix=self-hosted requires 'self-hosted-runner' in ci.mode"));
  }

  const receiptGate = 'receiptGate' in ci ? ci.receiptGate : 'off';
  if (!CI_RECEIPT_GATES.includes(receiptGate)) {
    findings.push(finding('CI_RECEIPT_GATE_UNKNOWN', `ci.receiptGate must be one of ${CI_RECEIPT_GATES.join(', ')}`));
  } else {
    if (receiptGate !== 'off' && !modes.has('local-receipt-gate')) {
      findings.push(finding('CI_RECEIPT_GATE_NEEDS_RECEIPT_MODE', `ci.receiptGate=${receiptGate} requires 'local-receipt-gate' in ci.mode`));
    }
    if (receiptGate === 'required') {
      let present = false;
      try { present = fs.statSync(path.join(repoRoot, VERIFY_RECEIPT_WORKFLOW)).isFile(); } catch (e) { present = false; }
      if (!present) {
        findings.push(finding('CI_RECEIPT_GATE_REQUIRES_WORKFLOW',
          `ci.receiptGate=required but ${VERIFY_RECEIPT_WORKFLOW} is not present; scaffold it with \`aesop init --ci-mode local-receipt-gate\` first`));
      }
    }
  }

  const labels = 'selfHostedLabels' in ci ? ci.selfHostedLabels : CI_DEFAULT_LABELS.slice();
  if (!Array.isArray(labels)) {
    findings.push(finding('CI_LABELS_NOT_LIST', 'ci.selfHostedLabels must be a list of strings'));
  } else {
    if (labels.some((x) => typeof x !== 'string' || !x.trim())) {
      findings.push(finding('CI_LABELS_ITEM_INVALID', 'ci.selfHostedLabels entries must be non-empty strings'));
    }
    if (modes.has('self-hosted-runner') && !labels.includes('self-hosted')) {
      findings.push(finding('CI_LABELS_MISSING_SELF_HOSTED', "ci.selfHostedLabels must include 'self-hosted' (GitHub routes on it)"));
    }
  }
  return findings;
}

/**
 * Read aesop.config.json under repoRoot and validate its `ci` block.
 * @returns {{config: object|null, findings: Array<{code: string, message: string}>}}
 */
function loadAesopConfig(repoRoot) {
  const file = path.join(repoRoot, 'aesop.config.json');
  if (!fs.existsSync(file)) {
    return { config: null, findings: [finding('CONFIG_MISSING', `aesop.config.json not found in ${repoRoot}`)] };
  }
  let config;
  try {
    config = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    return { config: null, findings: [finding('CONFIG_INVALID_JSON', `aesop.config.json unreadable: ${e.message}`)] };
  }
  return { config, findings: validateCiConfig(config, repoRoot) };
}

module.exports = {
  CI_MODES, CI_WINDOWS_MATRIX, CI_RECEIPT_GATES, CI_DEFAULT_LABELS, CI_KNOWN_KEYS, VERIFY_RECEIPT_WORKFLOW,
  defaultCiConfig, validateCiConfig, loadAesopConfig
};
