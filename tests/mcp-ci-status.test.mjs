#!/usr/bin/env node
/**
 * MCP CI Job Status Tool Test
 *
 * Tests the ci_job_status tool: GitHub Actions history query.
 * Validates: normal history, empty history (never_executed=true), gh failure → structured error.
 * Uses test mocks via TEST_GH_MOCK_DATA environment variable.
 */

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createInterface } from 'node:readline';

// ============================================================================
// Test Harness
// ============================================================================

class MCPTestClient {
  constructor(process) {
    this.process = process;
    this.requestId = 0;
    this.pendingResponses = new Map();

    this.rl = createInterface({
      input: this.process.stdout
    });

    this.process.stderr.on('data', (data) => {
      console.error(`[server stderr] ${data}`);
    });

    this.rl.on('line', (line) => {
      try {
        const response = JSON.parse(line);
        const id = response.id;
        const callbacks = this.pendingResponses.get(id);
        if (callbacks) {
          callbacks.resolve(response);
          this.pendingResponses.delete(id);
        }
      } catch (e) {
        console.error(`Failed to parse server response: ${line}`);
      }
    });
  }

  async request(method, params = {}) {
    const id = ++this.requestId;
    const request = {
      jsonrpc: '2.0',
      id,
      method,
      params
    };

    const responsePromise = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error(`Timeout waiting for response to request ${id}`));
      }, 5000);

      this.pendingResponses.set(id, {
        resolve: (response) => {
          clearTimeout(timeout);
          resolve(response);
        }
      });
    });

    this.process.stdin.write(JSON.stringify(request) + '\n');
    const response = await responsePromise;
    return response;
  }

  close() {
    this.rl.close();
    this.process.kill();
  }
}

// ============================================================================
// Test Suite
// ============================================================================

async function runTests() {
  console.log('Starting MCP CI Status Tool Tests...\n');

  const fixtureRoot = mkdtempSync(join(tmpdir(), 'aesop-mcp-ci-test-'));
  const stateRoot = join(fixtureRoot, 'state');
  const ledgerDir = join(stateRoot, 'ledger');

  console.log(`Fixture root: ${fixtureRoot}`);

  // Set up minimal test state files
  const fs = await import('node:fs');
  fs.mkdirSync(stateRoot, { recursive: true });
  fs.mkdirSync(ledgerDir, { recursive: true });

  // Create heartbeat file
  const now = Math.floor(Date.now() / 1000);
  fs.writeFileSync(join(stateRoot, '.watchdog-heartbeat'), `${now}`);

  // Create tracker.json (minimal)
  const tracker = { version: 1, items: [] };
  fs.writeFileSync(join(stateRoot, 'tracker.json'), JSON.stringify(tracker));

  // Create ledger (minimal)
  const ledgerContent = `| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |
|--------|------------|-------|--------------|-----------|------------|--------|-------|------|
`;
  fs.writeFileSync(join(ledgerDir, 'OUTCOMES-LEDGER.md'), ledgerContent);

  // Spawn server once for all tests
  console.log('Spawning server...');
  const serverProcess = spawn('node', ['./mcp/server.mjs', '--root', fixtureRoot], {
    env: {
      ...process.env,
      AESOP_ROOT: fixtureRoot,
      AESOP_STATE_ROOT: stateRoot,
      TEST_GH_MOCK_DATA: ''
    }
  });

  // Give server a moment to start
  await new Promise(r => setTimeout(r, 100));

  const client = new MCPTestClient(serverProcess);

  let testsPassed = 0;
  let testsFailed = 0;

  try {
    // Test 1: Initialize
    console.log('Test 1: Initialize...');
    try {
      const initResp = await client.request('initialize', {
        protocolVersion: '2024-11-05',
        capabilities: {},
        clientInfo: { name: 'test-client', version: '1.0.0' }
      });

      if (initResp.result && initResp.result.serverInfo) {
        console.log('✓ Initialize succeeded\n');
        testsPassed++;
      } else {
        console.log('✗ Initialize failed\n');
        testsFailed++;
      }
    } catch (err) {
      console.log(`✗ Initialize failed: ${err.message}\n`);
      testsFailed++;
    }

    // Test 2: ci_job_status with normal history
    console.log('Test 2: ci_job_status - normal history...');
    try {
      const mockData = JSON.stringify({
        callCount: 0,
        failureMode: false,
        mockData: [
          { status: 'completed', conclusion: 'success', started_at: '2024-12-15T10:00:00Z', duration_ms: 45000 },
          { status: 'completed', conclusion: 'failure', started_at: '2024-12-15T09:00:00Z', duration_ms: 32000 },
          { status: 'completed', conclusion: 'success', started_at: '2024-12-15T08:00:00Z', duration_ms: 48000 }
        ]
      });

      // Re-spawn server with mock data
      client.close();
      const server2 = spawn('node', ['./mcp/server.mjs', '--root', fixtureRoot], {
        env: {
          ...process.env,
          AESOP_ROOT: fixtureRoot,
          AESOP_STATE_ROOT: stateRoot,
          TEST_GH_MOCK_DATA: mockData
        }
      });
      await new Promise(r => setTimeout(r, 100));
      const client2 = new MCPTestClient(server2);

      await client2.request('initialize');

      const response = await client2.request('tools/call', {
        name: 'ci_job_status',
        arguments: {
          job_name: 'test-suite',
          branch: 'main',
          lookback_days: 30
        }
      });

      const result = response.result.content[0];
      const data = JSON.parse(result.text);

      if (
        data.runs &&
        Array.isArray(data.runs) &&
        data.runs.length === 3 &&
        data.never_executed === false &&
        typeof data.avg_duration_s === 'number' &&
        data.failure_rate >= 0 && data.failure_rate <= 1 &&
        typeof data.flake_signal === 'number'
      ) {
        console.log('✓ Normal history test passed\n');
        testsPassed++;
      } else {
        console.log(`✗ Normal history test failed: unexpected data structure\n`);
        testsFailed++;
      }

      client2.close();
    } catch (err) {
      console.log(`✗ Normal history test failed: ${err.message}\n`);
      testsFailed++;
    }

    // Test 3: ci_job_status with empty history
    console.log('Test 3: ci_job_status - empty history (never_executed)...');
    try {
      const mockData = JSON.stringify({
        callCount: 0,
        failureMode: false,
        mockData: []
      });

      const server3 = spawn('node', ['./mcp/server.mjs', '--root', fixtureRoot], {
        env: {
          ...process.env,
          AESOP_ROOT: fixtureRoot,
          AESOP_STATE_ROOT: stateRoot,
          TEST_GH_MOCK_DATA: mockData
        }
      });
      await new Promise(r => setTimeout(r, 100));
      const client3 = new MCPTestClient(server3);

      await client3.request('initialize');

      const response = await client3.request('tools/call', {
        name: 'ci_job_status',
        arguments: { job_name: 'never-ran', branch: 'main' }
      });

      const result = response.result.content[0];
      const data = JSON.parse(result.text);

      if (
        data.never_executed === true &&
        Array.isArray(data.runs) &&
        data.runs.length === 0 &&
        data.failure_rate === 0 &&
        data.flake_signal === 0
      ) {
        console.log('✓ Empty history test passed\n');
        testsPassed++;
      } else {
        console.log(`✗ Empty history test failed: unexpected data\n`);
        testsFailed++;
      }

      client3.close();
    } catch (err) {
      console.log(`✗ Empty history test failed: ${err.message}\n`);
      testsFailed++;
    }

    // Test 4: ci_job_status with gh failure
    console.log('Test 4: ci_job_status - gh command failure...');
    try {
      const mockData = JSON.stringify({
        callCount: 0,
        failureMode: true,
        mockData: []
      });

      const server4 = spawn('node', ['./mcp/server.mjs', '--root', fixtureRoot], {
        env: {
          ...process.env,
          AESOP_ROOT: fixtureRoot,
          AESOP_STATE_ROOT: stateRoot,
          TEST_GH_MOCK_DATA: mockData
        }
      });
      await new Promise(r => setTimeout(r, 100));
      const client4 = new MCPTestClient(server4);

      await client4.request('initialize');

      const response = await client4.request('tools/call', {
        name: 'ci_job_status',
        arguments: { job_name: 'test', branch: 'main' }
      });

      const result = response.result.content[0];
      const data = JSON.parse(result.text);

      if (data.error && typeof data.error === 'string') {
        console.log('✓ Error handling test passed\n');
        testsPassed++;
      } else {
        console.log(`✗ Error handling test failed: expected error field\n`);
        testsFailed++;
      }

      client4.close();
    } catch (err) {
      console.log(`✗ Error handling test failed: ${err.message}\n`);
      testsFailed++;
    }
  } finally {
    try {
      client.close();
    } catch (e) {}
    try {
      rmSync(fixtureRoot, { recursive: true, force: true });
    } catch (e) {}
  }

  console.log('='.repeat(60));
  console.log(`Tests passed: ${testsPassed}`);
  console.log(`Tests failed: ${testsFailed}`);
  console.log('='.repeat(60) + '\n');

  if (testsFailed > 0) {
    process.exit(1);
  }
}

runTests().catch((err) => {
  console.error(`Fatal error: ${err.message}`);
  process.exit(1);
});
