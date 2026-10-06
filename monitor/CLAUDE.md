# monitor/ — Orchestration monitor

**Purpose**: Continuous background signal collector and refinement proposer — watches fleet machinery health deterministically (Node.js, no LLM), emits cycle snapshots, and proposes rule changes via append-only PROPOSALS.md. **GOAL IS FIXED**: improve machinery, never mission; if monitor thinks goal should change, it writes to PROPOSALS.md and stops.

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Files & Ownership

- **collect-signals.mjs** — Deterministic signal collector (Node.js built-ins only, no Python/LLM); runs each cycle deterministically + idempotently; emits BRIEF.md + SIGNALS.json; updates .monitor-heartbeat (epoch) and .signal-state.json.
- **BRIEF.md** — Human-readable cycle snapshot; overwritten each cycle; runtime/gitignored. Format: heartbeat status, git state, memory freshness, logs, isolation violations, agent stalls, main CI status, extended signals checks (if enabled), security alerts, respawn watch, cost tracking, unreviewed prompts.
- **SIGNALS.json** — Machine-readable signal metrics (same keys as BRIEF); JSON structure; overwritten each cycle; runtime/gitignored.
- **PROPOSALS.md** — Append-only inbox for user-approval rule changes (config, policy, deletions); never edited by monitor after emission; gitignored.
- **ACTIONS.log** — Append-only log of AUTO tier actions taken (heartbeat updates, log rotation, junk quarantine); runtime/gitignored.
- **.monitor-heartbeat** — Epoch timestamp (line 1 only) for single-instance liveness guard; if <300s old, skip cycle; runtime/gitignored.
- **.signal-state.json** — Sidecar state (cycleCount, last-seen hashes, etc.); runtime/gitignored.

## Deterministic Collector Cycle (Node.js only)

Each cycle:
1. Check single-instance guard: read .monitor-heartbeat; if <300s old, skip cycle.
2. Load config from env (AESOP_ROOT, BRAIN_ROOT, SCRIPTS_ROOT, TEMP_ROOT, AESOP_EXTENDED_SIGNALS) or aesop.config.json; fallback to safe defaults.
3. Deterministically collect signals (read-only filesystem ops, no calls to external services or LLMs).
4. Emit BRIEF.md (human format) and SIGNALS.json (machine format); both overwritten, idempotent.
5. Apply AUTO tier actions (heartbeat write, log rotation, quarantine).
6. Update .monitor-heartbeat (epoch timestamp) and .signal-state.json.
7. Append any AUTO actions to ACTIONS.log (timestamped).

**Robustness**: Treat missing files/dirs as empty; never crash; config errors fallback to safe defaults.

## Signal Contract

**Signal keys collected** (15 total): heartbeats (watchdog, monitor, other loops), git (branches, unpushed, dirty), memory (stale files >30d), logs (rotation triggers), junk (script sprawl), strayRepo (scripts outside ~/scripts), alerts (SECURITY-ALERTS.log), respawnWatch (agent respawn loops), unreviewedPrompts (new spawns), isolationViolations (FIXED GOAL drift), main_ci (main-full workflow status via `gh run list`), agentStalls (silent-hang detection via stall_check.py), and others per governance.

**Output formats**:
- **BRIEF.md**: Human-readable notes, status lines, warnings; overwritten each cycle.
- **SIGNALS.json**: Flat JSON object with signal key → {value, timestamp, details}; overwritten each cycle.

**Extended signals** (opt-in, default OFF): junk, strayRepo, respawnWatch, unreviewedPrompts checks; skip-step if disabled.
- Config key: `monitor.extended_signals` (boolean, default `false`) in aesop.config.json (check monitor section).
- Env override: `AESOP_EXTENDED_SIGNALS` (`'true'` or `'1'` string to enable).
- Precedence: env var > config file > default (false).
- Behavior when disabled: emit `{"skipped": true}` for each extended check in SIGNALS.json; BRIEF.md notes "extended (off)" for those sections; directories are not walked.

## Action Tiers & Idempotency

**AUTO** (apply immediately, log to ACTIONS.log):
- Heartbeat checks (read-only).
- Log rotation (invoke rotate_logs.py if available; fail-open if unavailable).
- Heartbeat write (.monitor-heartbeat epoch update).
- Junk script quarantine (move old .py/.mjs from temp dirs to monitor/quarantine/ + MANIFEST.tsv; only when extended_signals is ON).

**PROPOSE** (write to PROPOSALS.md, await user approval):
- Rule/config/policy changes.
- Deletions or quarantines outside monitor/quarantine/.
- Orchestration behavior changes.

**Idempotency rule**: Proposal emission keyed on signal key (e.g., 'respawn-watch-breach'); only emitted once per cycle if not already present in PROPOSALS.md (check: `**Signal:** <key>` line exists). Safe to run repeatedly; append-only preserves audit trail.

## Invariants & Gotchas

- **Goal is FIXED**: Improve machinery (heartbeat health, cost, security), never mission (project scope, architecture direction). Cardinal Rule enforcement only.
- **Single-instance guard**: .monitor-heartbeat <300s old = skip cycle. Prevents concurrent collectors.
- **Single-writer discipline**: Only monitor writes BRIEF.md, SIGNALS.json, ACTIONS.log, .monitor-heartbeat, .signal-state.json. External tools read-only.
- **Config sourcing**: Env vars (AESOP_ROOT, BRAIN_ROOT, SCRIPTS_ROOT, TEMP_ROOT, AESOP_EXTENDED_SIGNALS, heartbeat thresholds from config) override aesop.config.json (monitor section: log_max_lines, log_max_kb, extended_signals, heartbeat_thresholds). Safe defaults if missing.
- **Robustness to missing files**: Missing logs/dirs are treated as empty; no crash; graceful degradation.
- **Node.js only**: No Python, no LLM, no cloud calls; deterministic + cheap. Any orchestration-level actions (Python scripts, LLM review, etc.) are run by external agents, not the collector.
- **CHARTER.md governs behavior**: Define new signals by proposing via PROPOSALS.md; never edit CHARTER or collect-signals.mjs directly.
- **agentStalls is FAIL-CLOSED (GAP 3)**: "graceful degradation" above applies to missing *inputs*, never to a *check that did not run*. Every failure path of `checkAgentStalls()` (tool absent, unspawnable `python`, timeout, kill signal, nonzero exit, non-JSON or non-array stdout, unexpected throw) routes through the single `degradedStallSignal(reason)` helper and emits `{available:false, state:'UNKNOWN', degraded:true, count:null, reason}`. `count` is **null, never 0** — the old `{available:false, count:0}` shape made a broken detector indistinguishable from "no agents are stalled", and BRIEF.md's neutral `NOT-AVAILABLE:` line hid it. A check that ran emits `state:'OK'|'STALLED'` with `degraded:false` and a numeric `count`; BRIEF.md now flags the degraded case as a DEGRADED/UNKNOWN warning. **Consumers must key on `state`/`degraded`, not on `count`** (falsy `count` no longer means healthy). Regression suite: `tests/monitor-stall-failclosed.test.mjs` (static guard against the `available:false, count:0` literal returning, plus a behavioral run with an emptied PATH so `python` cannot resolve).
- **Runtime lane liveness lives in tools/, not the collector**: the deterministic gate for "a lane the orchestrator believes is live has gone silent" is `tools/lane_liveness.py --check` (exit 1 names the lanes to TaskStop + relaunch, exit 2 = undetermined). It is deliberately NOT wired into the cycle: it walks every registered worktree, so on a long-lived box the standing verdict is dominated by abandoned trees and would drown BRIEF.md. Run it on demand, or with `--claims` narrowed to the lanes actually dispatched.

## Dropped (reason)
- Detailed 11-signal-check list names/numbers (governance reference; see CHARTER.md for check definitions and extended-signal details).
- End-of-day wipe-survival sweep (separate orchestration flow, not core deterministic collector).
- Mirror refresh cadence and asset sync (orchestration-level; not collector responsibility).
- Prompt semantic review (LLM-based; runs as separate agent, not in deterministic collector).

Map of all domains: /CLAUDE.md
