// Tests for dash/dash-extra.mjs security-alerts path resolution.
// Contract: alerts live in state/SECURITY-ALERTS.log (canonical location used by
// watchdog-gui.sh, monitor/collect-signals.mjs, and the daemons) — NOT scan/.
// Run: node --test tests/dash-extra.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'dash', 'dash-extra.mjs');
const AGENT_BASENAME = 'agent-fixture0001.jsonl';

// Build a temp fixture: a fake AESOP_ROOT and a fake transcripts root containing
// one fresh agent transcript, so the agent shows up in --json output.
function makeFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-dash-test-'));
  const aesopRoot = path.join(root, 'aesop');
  const transcriptsRoot = path.join(root, 'projects');
  fs.mkdirSync(aesopRoot, { recursive: true });
  fs.mkdirSync(transcriptsRoot, { recursive: true });
  fs.writeFileSync(
    path.join(transcriptsRoot, AGENT_BASENAME),
    '{"description":"fixture agent for path test"}\n'
  );
  return { root, aesopRoot, transcriptsRoot };
}

function runScript(fixture) {
  const stdout = execFileSync(process.execPath, [SCRIPT, '--json'], {
    env: {
      ...process.env,
      AESOP_ROOT: fixture.aesopRoot,
      AESOP_TRANSCRIPTS_ROOT: fixture.transcriptsRoot
    },
    encoding: 'utf8',
    timeout: 30000,
    killSignal: 'SIGKILL'
  });
  return JSON.parse(stdout);
}

test('alerts in state/SECURITY-ALERTS.log are applied to agent status', () => {
  const fixture = makeFixture();
  try {
    const stateDir = path.join(fixture.aesopRoot, 'state');
    fs.mkdirSync(stateDir, { recursive: true });
    fs.writeFileSync(
      path.join(stateDir, 'SECURITY-ALERTS.log'),
      `2026-07-12T00:00:00Z SUSPICIOUS ${AGENT_BASENAME} test alert\n`
    );

    const agents = runScript(fixture);
    assert.equal(agents.length, 1, 'fixture agent should be detected');
    assert.equal(
      agents[0].status,
      'SUSPICIOUS',
      'alert written to state/SECURITY-ALERTS.log must flag the agent (canonical path is state/, not scan/)'
    );
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

test('scan/SECURITY-ALERTS.log is never read (non-canonical path)', () => {
  const fixture = makeFixture();
  try {
    const scanDir = path.join(fixture.aesopRoot, 'scan');
    fs.mkdirSync(scanDir, { recursive: true });
    fs.writeFileSync(
      path.join(scanDir, 'SECURITY-ALERTS.log'),
      `2026-07-12T00:00:00Z SUSPICIOUS ${AGENT_BASENAME} decoy alert\n`
    );

    const agents = runScript(fixture);
    assert.equal(agents.length, 1, 'fixture agent should be detected');
    assert.equal(
      agents[0].status,
      'running',
      'a decoy alert in scan/ must have no effect — scan/ is not a canonical location'
    );
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

test('degrades gracefully when no alerts log exists', () => {
  const fixture = makeFixture();
  try {
    const agents = runScript(fixture);
    assert.equal(agents.length, 1, 'fixture agent should be detected');
    assert.equal(agents[0].status, 'running');
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

test('config precedence: AESOP_TRANSCRIPTS_ROOT from config file honored when env var unset', () => {
  const fixture = makeFixture();
  try {
    // Create a separate config transcripts directory
    const configTranscriptsRoot = path.join(fixture.root, 'config-transcripts');
    fs.mkdirSync(configTranscriptsRoot, { recursive: true });

    // Create a fresh agent transcript in config-specified location
    const configAgentPath = path.join(configTranscriptsRoot, 'agent-config-fixture.jsonl');
    fs.writeFileSync(configAgentPath, '{"description":"agent from config"}\n');

    // Create aesop.config.json with custom transcripts_root
    const configPath = path.join(fixture.aesopRoot, 'aesop.config.json');
    fs.writeFileSync(configPath, JSON.stringify({
      transcripts_root: configTranscriptsRoot,
      repos: []
    }), 'utf8');

    // Run script WITHOUT AESOP_TRANSCRIPTS_ROOT env var; should use config file value
    const stdout = execFileSync(process.execPath, [SCRIPT, '--json'], {
      env: {
        ...process.env,
        AESOP_ROOT: fixture.aesopRoot,
        // NOTE: NOT setting AESOP_TRANSCRIPTS_ROOT - should fall back to config
      },
      encoding: 'utf8',
      timeout: 30000,
      killSignal: 'SIGKILL'
    });

    const agents = JSON.parse(stdout);
    // Should find the agent in config-specified transcripts directory
    assert.ok(
      agents.length > 0,
      'Config-specified transcripts_root should be scanned for agents (config precedence must work)'
    );
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

test('walk() respects depth limit of 6 without skipping based on directory mtime', () => {
  const fixture = makeFixture();
  try {
    const transcriptsRoot = fixture.transcriptsRoot;
    const now = Date.now();
    const activityWindow = 12 * 60 * 1000; // 12 minutes

    // Create a deep/wide nested structure with both fresh and stale files
    // Depth structure: d0/d1/d2/d3/d4/d5/d6/d7 (depth 7, should be pruned beyond 6)
    let currentPath = transcriptsRoot;
    for (let depth = 0; depth < 8; depth++) {
      currentPath = path.join(currentPath, `d${depth}`);
      fs.mkdirSync(currentPath, { recursive: true });

      // Write a fresh file at this depth
      const freshAgent = `agent-fresh-d${depth}.jsonl`;
      fs.writeFileSync(path.join(currentPath, freshAgent), `{"description":"fresh d${depth}"}\n`);

      // Write a stale file at this depth (older than activity window)
      const staleAgent = `agent-stale-d${depth}.jsonl`;
      fs.writeFileSync(
        path.join(currentPath, staleAgent),
        `{"description":"stale d${depth}"}\n`
      );
      // Set mtime to 15 minutes ago (outside activity window)
      fs.utimesSync(path.join(currentPath, staleAgent), now / 1000 - 900, now / 1000 - 900);

      // Set directory mtime to very stale (15 min old) for all depths >= 1 to test
      // that directory-mtime pruning is NOT used: fresh files inside old dirs must still appear
      if (depth >= 1) {
        fs.utimesSync(currentPath, now / 1000 - 900, now / 1000 - 900);
      }
    }

    const startTime = Date.now();
    const agents = runScript(fixture);
    const elapsed = Date.now() - startTime;

    // Performance assertion: walk should complete in under 2 seconds even on deep tree
    assert.ok(elapsed < 2000, `walk should complete in <2s but took ${elapsed}ms`);

    // Should find only fresh agents within activity window
    const freshAgents = agents.filter(a => a.hint.includes('fresh'));
    assert.ok(freshAgents.length > 0, 'should find fresh agents in shallow depths');

    // Stale agents should be filtered out (mtime > 12min old)
    const staleAgents = agents.filter(a => a.hint.includes('stale'));
    assert.equal(staleAgents.length, 0, 'stale agents (>12min old) must be excluded from output');

    // Depth should be limited: files deeper than depth 6 should not appear
    // (fresh-d7, fresh-d6, ... fresh-d0 should only have up to d6 available in tree walk)
    const maxDepthFound = Math.max(
      ...freshAgents.map(a => parseInt(a.hint.match(/d(\d+)/)?.[1] || '0', 10))
    );
    assert.ok(maxDepthFound <= 6, `depth should be capped at 6, found agents at depth ${maxDepthFound}`);
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

test('P1: old subagent-dir with fresh agent-*.jsonl inside shows agent (dir-mtime pruning removed)', () => {
  const fixture = makeFixture();
  try {
    const transcriptsRoot = fixture.transcriptsRoot;
    const now = Date.now();

    // Simulate real layout: projects/project/session/subagents/agent-*.jsonl
    // The subagents/ dir mtime freezes at creation (doesn't update when files appended to)
    const subagentsDir = path.join(transcriptsRoot, 'myproject', 'session-abc', 'subagents');
    fs.mkdirSync(subagentsDir, { recursive: true });

    // Create fresh agent file (modified 30 seconds ago = within 12 min activity window)
    const agentPath = path.join(subagentsDir, 'agent-fresh.jsonl');
    fs.writeFileSync(agentPath, '{"description":"fresh agent in old dir"}\n');
    fs.utimesSync(agentPath, now / 1000 - 30, now / 1000 - 30);

    // Set subagents/ dir mtime to 15 minutes old (outside activity window)
    // This simulates: session created 15 min ago, agent recently updated inside
    fs.utimesSync(subagentsDir, now / 1000 - 900, now / 1000 - 900);

    const agents = runScript(fixture);
    assert.ok(
      agents.some(a => a.hint.includes('fresh agent in old dir')),
      'agent with fresh mtime must appear even if parent dir is 15 min old (dir-mtime pruning must be removed)'
    );
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

test('P2: AESOP_STATE_ROOT env var is honored for alerts path', () => {
  const fixture = makeFixture();
  try {
    // Create a custom state directory separate from fixture.aesopRoot
    const customStateDir = path.join(fixture.root, 'custom-state');
    fs.mkdirSync(customStateDir, { recursive: true });

    // Write alert to custom state location
    fs.writeFileSync(
      path.join(customStateDir, 'SECURITY-ALERTS.log'),
      `2026-07-12T00:00:00Z HIGH ${AGENT_BASENAME} alert in custom state\n`
    );

    // Run script with AESOP_STATE_ROOT pointing to custom location
    const stdout = execFileSync(process.execPath, [SCRIPT, '--json'], {
      env: {
        ...process.env,
        AESOP_ROOT: fixture.aesopRoot,
        AESOP_TRANSCRIPTS_ROOT: fixture.transcriptsRoot,
        AESOP_STATE_ROOT: customStateDir  // Override state location
      },
      encoding: 'utf8',
      timeout: 30000,
      killSignal: 'SIGKILL'
    });

    const agents = JSON.parse(stdout);
    assert.equal(agents.length, 1, 'fixture agent should be detected');
    assert.equal(
      agents[0].status,
      'HIGH',
      'alert in AESOP_STATE_ROOT must be honored (not hardcoded path)'
    );
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

test('P2: config.state_root is honored when AESOP_STATE_ROOT env var not set', () => {
  const fixture = makeFixture();
  try {
    // Create a custom state directory
    const customStateDir = path.join(fixture.root, 'config-state');
    fs.mkdirSync(customStateDir, { recursive: true });

    // Write alert to config-specified state location
    fs.writeFileSync(
      path.join(customStateDir, 'SECURITY-ALERTS.log'),
      `2026-07-12T00:00:00Z MED ${AGENT_BASENAME} alert in config state\n`
    );

    // Create aesop.config.json with custom state_root
    const configPath = path.join(fixture.aesopRoot, 'aesop.config.json');
    fs.writeFileSync(configPath, JSON.stringify({
      state_root: customStateDir,
      repos: []
    }), 'utf8');

    // Run script WITHOUT AESOP_STATE_ROOT env var; should use config file value
    const stdout = execFileSync(process.execPath, [SCRIPT, '--json'], {
      env: {
        ...process.env,
        AESOP_ROOT: fixture.aesopRoot,
        AESOP_TRANSCRIPTS_ROOT: fixture.transcriptsRoot
        // NOTE: NOT setting AESOP_STATE_ROOT - should fall back to config
      },
      encoding: 'utf8',
      timeout: 30000,
      killSignal: 'SIGKILL'
    });

    const agents = JSON.parse(stdout);
    assert.equal(agents.length, 1, 'fixture agent should be detected');
    assert.equal(
      agents[0].status,
      'MED',
      'alert in config.state_root must be honored when AESOP_STATE_ROOT env var is not set'
    );
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

// Cache contract (tracker 44ce0f022454): the persisted transcript cache must stay
// bounded (entries only for transcripts seen in the current run) and must not
// persist full dispatch prompts, while the emitted promptFull stays populated.
function cacheFilePath(fixture) {
  return path.join(fixture.aesopRoot, 'state', '.dash-extra-cache.json');
}

function transcriptLine(prompt) {
  return JSON.stringify({
    type: 'user',
    message: { content: prompt },
    timestamp: new Date().toISOString()
  }) + '\n';
}

test('dash-extra cache is pruned to transcripts seen in the current run (bounded growth)', () => {
  const fixture = makeFixture();
  try {
    const transcript = path.join(fixture.transcriptsRoot, AGENT_BASENAME);
    runScript(fixture);
    // Change the file so its (path|size|mtime) cache key changes.
    fs.appendFileSync(transcript, transcriptLine('second revision of the fixture'));
    runScript(fixture);

    const cache = JSON.parse(fs.readFileSync(cacheFilePath(fixture), 'utf8'));
    const keys = Object.keys(cache);
    assert.equal(
      keys.length,
      1,
      `cache must hold only the current transcript state, got ${keys.length} entries: ${keys.join(' , ')}`
    );
    assert.ok(keys[0].startsWith(transcript + '|'), 'the surviving key must be the live transcript');
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});

test('dash-extra cache does not persist full dispatch prompts, yet output keeps promptFull', () => {
  const fixture = makeFixture();
  try {
    const transcript = path.join(fixture.transcriptsRoot, AGENT_BASENAME);
    // Multi-line prompt: the first line becomes taskLabel (an 80-char label), while
    // the marker sits past it, so it only appears if promptFull is persisted.
    const marker = 'PROMPT-MARKER-44ce0f022454 full dispatch text';
    const prompt = `Dispatch header line\n${marker}\n` + 'body '.repeat(40);
    fs.writeFileSync(transcript, transcriptLine(prompt));

    const first = runScript(fixture);
    const cacheText = fs.readFileSync(cacheFilePath(fixture), 'utf8');
    assert.ok(!cacheText.includes(marker), 'cache file must not contain the full prompt text');

    // Second run takes the cache-hit path; the full prompt must still be emitted.
    const second = runScript(fixture);
    assert.equal(first[0].promptFull, prompt, 'first run emits full promptFull');
    assert.equal(second[0].promptFull, prompt, 'cache-hit run must still emit full promptFull');
  } finally {
    fs.rmSync(fixture.root, { recursive: true, force: true });
  }
});
