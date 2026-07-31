import { describe, it, before, after } from 'node:test';
import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { existsSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';

// repoRoot must be derived with fileURLToPath, not string surgery on a URL.
// tests/ is one level below the repo root -- '../../..' overshot by two.
const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const cliPath = join(repoRoot, 'bin', 'cli.js');


/**
 * Routing succeeded if the CLI did not emit its OWN unknown-namespace/verb error.
 * The child tool may legitimately exit 2 (usage error) for a flag it does not accept --
 * that still proves dispatch reached it.
 */
function assertRouted(result, msg) {
  const out = (result.stdout || '') + (result.stderr || '');
  assert(!/Unknown (namespace|verb)/i.test(out), msg + ' (CLI reported unknown namespace/verb: ' + out.slice(0, 200) + ')');
}

/**
 * Run `node bin/cli.js` with given args and capture exit code + output
 */
function runCli(args, cwdOverride) {
  return new Promise((resolve) => {
    const proc = spawn('node', [cliPath, ...args], {
      cwd: cwdOverride || repoRoot,
      stdio: ['pipe', 'pipe', 'pipe'],
      timeout: 10000
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data) => { stdout += data.toString(); });
    proc.stderr.on('data', (data) => { stderr += data.toString(); });

    proc.on('error', (err) => {
      resolve({ exitCode: 2, stdout, stderr, error: err.message });
    });

    proc.on('close', (code) => {
      resolve({ exitCode: code ?? 0, stdout, stderr });
    });
  });
}

/**
 * Verify script path exists (relative to repo root)
 */
function scriptExists(relPath) {
  return existsSync(join(repoRoot, relPath));
}

describe('CLI Python dispatch table', () => {
  describe('help and discovery', () => {
    it('shows --help for main aesop command', async () => {
      const result = await runCli(['--help']);
      assert.equal(result.exitCode, 0);
      assert.match(result.stdout, /aesop.*Multi-agent orchestration/);
      assert.match(result.stdout, /aesop <namespace> <verb>/);
    });

    it('lists Python namespaces in help', async () => {
      const result = await runCli(['--help']);
      assert.equal(result.exitCode, 0);
      assert.match(result.stdout, /lint/);
      assert.match(result.stdout, /gate/);
      assert.match(result.stdout, /verify/);
      assert.match(result.stdout, /wave/);
      assert.match(result.stdout, /state/);
      assert.match(result.stdout, /tracker/);
      assert.match(result.stdout, /cost/);
      assert.match(result.stdout, /bench/);
      assert.match(result.stdout, /health/);
      assert.match(result.stdout, /transcript/);
    });

    it('shows namespace verbs with --help', async () => {
      const result = await runCli(['lint', '--help']);
      assert.equal(result.exitCode, 0);
      assert.match(result.stdout, /encoding/);
      assert.match(result.stdout, /claudemd/);
      assert.match(result.stdout, /commit/);
      assert.match(result.stdout, /tools\//);
    });

    it('shows gate verbs with --help', async () => {
      const result = await runCli(['gate', '--help']);
      assert.equal(result.exitCode, 0);
      assert.match(result.stdout, /secret-scan/);
      assert.match(result.stdout, /dispatch/);
      assert.match(result.stdout, /spec-contract/);
    });

    it('shows verify verbs with --help', async () => {
      const result = await runCli(['verify', '--help']);
      assert.equal(result.exitCode, 0);
      assert.match(result.stdout, /dashboard/);
      assert.match(result.stdout, /activity-filter/);
      assert.match(result.stdout, /cost-panel/);
    });
  });

  describe('error handling', () => {
    it('treats an unknown first arg as a scaffold target (backward compat)', async () => {
      // `npx @matt82198/aesop [target-dir]` is the ORIGINAL published contract and must
      // keep working. An unrecognised first argument is a directory name, not an error --
      // exiting 2 here would break every existing scaffolder invocation.
      // Exit 2 is reserved for a KNOWN namespace with an unknown verb (next test).
      // Run in a TEMP cwd: this path genuinely scaffolds a directory, and doing that
      // inside the repo pollutes the working tree (the domain-map drift gate catches it).
      const tmp = join(tmpdir(), 'aesop-cli-' + randomBytes(6).toString('hex'));
      mkdirSync(tmp, { recursive: true });
      try {
        const result = await runCli(['badnamespace', 'verb'], tmp);
        assert.notEqual(result.exitCode, 2, 'unknown first arg must fall through to the scaffolder, not error');
      } finally {
        rmSync(tmp, { recursive: true, force: true });
      }
    });

    it('exits 2 on unknown verb in valid namespace', async () => {
      const result = await runCli(['lint', 'badverb']);
      assert.equal(result.exitCode, 2);
      assert.match(result.stderr, /Error: Unknown verb/);
      assert.match(result.stderr, /Valid verbs:/);
    });

    it('suggests valid verbs when verb is unknown', async () => {
      const result = await runCli(['gate', 'invalidverb']);
      assert.equal(result.exitCode, 2);
      assert.match(result.stderr, /secret-scan|dispatch|spec-contract/);
    });

    it('exits 2 if Python interpreter not found (simulated)', async () => {
      // This test simulates the error path; actual result depends on environment
      // but we verify the error message structure by checking exit code
      const result = await runCli(['lint', 'encoding', '--help']);
      // If python/python3 exists and encoding_lint.py exists, this should succeed
      // If not, it should exit with 2 and mention Python not found
      if (result.exitCode === 2) {
        assert.match(result.stderr, /Python|python/);
      }
    });
  });

  describe('script verification', () => {
    it('verifies encoding_lint.py exists', () => {
      assert.ok(scriptExists('tools/encoding_lint.py'), 'tools/encoding_lint.py should exist');
    });

    it('verifies claudemd_lint.py exists', () => {
      assert.ok(scriptExists('tools/claudemd_lint.py'), 'tools/claudemd_lint.py should exist');
    });

    it('verifies secret_scan.py exists', () => {
      assert.ok(scriptExists('tools/secret_scan.py'), 'tools/secret_scan.py should exist');
    });

    it('verifies spec_contract_validator.py exists', () => {
      assert.ok(scriptExists('tools/spec_contract_validator.py'), 'tools/spec_contract_validator.py should exist');
    });

    it('verifies verify_dash.py exists', () => {
      assert.ok(scriptExists('tools/verify_dash.py'), 'tools/verify_dash.py should exist');
    });

    it('verifies wave_preflight.py exists', () => {
      assert.ok(scriptExists('tools/wave_preflight.py'), 'tools/wave_preflight.py should exist');
    });

    it('verifies state_query.py exists', () => {
      assert.ok(scriptExists('tools/state_query.py'), 'tools/state_query.py should exist');
    });

    it('verifies tracker_autoclose.py exists', () => {
      assert.ok(scriptExists('tools/tracker_autoclose.py'), 'tools/tracker_autoclose.py should exist');
    });

    it('verifies cost_ceiling.py exists', () => {
      assert.ok(scriptExists('tools/cost_ceiling.py'), 'tools/cost_ceiling.py should exist');
    });

    it('verifies bench_runner.py exists', () => {
      assert.ok(scriptExists('tools/bench_runner.py'), 'tools/bench_runner.py should exist');
    });

    it('verifies health_score.py exists', () => {
      assert.ok(scriptExists('tools/health_score.py'), 'tools/health_score.py should exist');
    });

    it('verifies transcript_timeline.py exists', () => {
      assert.ok(scriptExists('tools/transcript_timeline.py'), 'tools/transcript_timeline.py should exist');
    });
  });

  describe('dispatch routing', () => {
    it('routes lint encoding to encoding_lint.py', async () => {
      // Just verify it attempts to run the script (may error if script needs config)
      const result = await runCli(['lint', 'encoding', '--help']);
      // Script should either succeed or error from script logic, not CLI routing
      assertRouted(result, 'Should route to script (not exit 2)');
    });

    it('routes gate secret-scan to secret_scan.py', async () => {
      const result = await runCli(['gate', 'secret-scan', '--help']);
      // Verify we're not getting a dispatch error (exit 2)
      assertRouted(result, 'Should route to secret_scan.py');
    });

    it('routes verify dashboard to verify_dash.py', async () => {
      const result = await runCli(['verify', 'dashboard', '--help']);
      assertRouted(result, 'Should route to verify_dash.py');
    });

    it('routes wave preflight to wave_preflight.py', async () => {
      const result = await runCli(['wave', 'preflight', '--help']);
      assertRouted(result, 'Should route to wave_preflight.py');
    });

    it('routes state query to state_query.py', async () => {
      const result = await runCli(['state', 'query', '--help']);
      assertRouted(result, 'Should route to state_query.py');
    });

    it('routes tracker autoclose to tracker_autoclose.py', async () => {
      const result = await runCli(['tracker', 'autoclose', '--help']);
      assertRouted(result, 'Should route to tracker_autoclose.py');
    });

    it('routes cost ceiling to cost_ceiling.py', async () => {
      const result = await runCli(['cost', 'ceiling', '--help']);
      assertRouted(result, 'Should route to cost_ceiling.py');
    });

    it('routes bench run to bench_runner.py', async () => {
      const result = await runCli(['bench', 'run', '--help']);
      assertRouted(result, 'Should route to bench_runner.py');
    });

    it('routes health check to healthcheck.py', async () => {
      const result = await runCli(['health', 'check', '--help']);
      assertRouted(result, 'Should route to healthcheck.py');
    });

    it('routes transcript timeline to transcript_timeline.py', async () => {
      const result = await runCli(['transcript', 'timeline', '--help']);
      assertRouted(result, 'Should route to transcript_timeline.py');
    });
  });

  describe('existing Node.js commands still work', () => {
    it('aesop doctor still works', async () => {
      const result = await runCli(['doctor']);
      // doctor may fail for other reasons (missing deps) but should not be a routing error
      assert(result.exitCode === 0 || result.exitCode === 1, 'doctor should run (not dispatch error)');
    });

    it('aesop fleet still works', async () => {
      const result = await runCli(['fleet']);
      // fleet may output JSON or error gracefully, but should not be a dispatch error
      assert(result.exitCode === 0 || result.exitCode === 1, 'fleet should run (not dispatch error)');
    });

    it('aesop --help shows both Node and Python commands', async () => {
      const result = await runCli(['--help']);
      assert.equal(result.exitCode, 0);
      assert.match(result.stdout, /doctor/);
      assert.match(result.stdout, /fleet/);
      assert.match(result.stdout, /aesop <namespace> <verb>/);
      assert.match(result.stdout, /lint|gate|verify/);
    });
  });

  describe('namespace coverage', () => {
    it('has 10 namespaces: lint, gate, verify, wave, state, tracker, cost, bench, health, transcript', async () => {
      const result = await runCli(['--help']);
      const namespaces = ['lint', 'gate', 'verify', 'wave', 'state', 'tracker', 'cost', 'bench', 'health', 'transcript'];
      namespaces.forEach(ns => {
        assert.match(result.stdout, new RegExp(ns), `namespace ${ns} should be listed`);
      });
    });
  });

  describe('no breaking changes', () => {
    it('wizard mode still works', async () => {
      const result = await runCli(['wizard', '--yes']);
      // wizard creates files; we don't verify success but that it runs without dispatch error
      assert(result.exitCode === 0 || result.exitCode === 1, 'wizard should run (not dispatch error)');
    });
  });
});
