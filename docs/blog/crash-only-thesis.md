# The Crash-Only Thesis: Why AI Agent Orchestration Needs to Embrace Failure

You've never tried to build a system where the workers crash constantly. AI agents do.

When I started building Aesop, I made a decision that looked backwards: instead of adding distributed consensus, heartbeat detection, and graceful shutdown protocols, I built the opposite. Stateless workers. Persistent filesystem state. Restart IS recovery. Every operation is idempotent. The crash-only design principle, ported from decades of distributed systems research (Candea & Fox, 2003; Temporal, 2019) but applied to AI agent orchestration.

The thesis: **For single-team autonomous development, crash-only design beats distributed consensus because the failure modes are different.**

## Why Crashes Are Normal for Agents

Traditional distributed systems assume failures are rare and Byzantine (arbitrary, adversarial). Agent fleets have different failure classes:

1. **Model failures**: Token limit exhausted mid-task. Refusal on safety grounds. Hallucinated output that passes local tests but fails CI. The model doesn't crash; it produces invalid output.

2. **Timeout cascades**: An agent hangs reading a large file. Retry backoff compounds. Dependent work stalls for minutes. The process doesn't exit; it gets stuck.

3. **Partial progress**: An agent commits a fix, runs tests (green), then crashes before pushing. State is halfway done. A graceful shutdown protocol won't help here.

4. **Worker stalls**: A process hangs (not crashed, just stuck). Watchdog detects idle >200 seconds, kills it. But what about the work it was doing?

Traditional solutions add distributed consensus: coordinators vote on state transitions, memberships managed via heartbeats, transactions atomic across replicas. But a Haiku agent costs ~$0.003. A heartbeat quorum check costs ~$0.0015. The coordination overhead approaches the cost of the worker itself.

The insight: agent workers are already stateless by design. They read context from disk, decide, write results, exit. When a worker crashes, the next worker can resume from the checkpoint without detecting the failure or revoking a lease. The state is on disk; the next reader will see it.

## The Architecture: Five Pillars

### 1. Durable Plain-Text State

State lives in git-committed files: `STATE.md` (intent, phase, next steps), `BUILDLOG.md` (append-only progress snapshots), Python guardrails (cost rules, dispatch logic), shell hooks (pre-push gates). This is not a limitation. It is the design.

Postgres fails when the connection pool exhausts. Git fails when the filesystem is corrupted—far rarer. A cloned repository from any date reveals exactly what the system was doing. No migration scripts, no eventual consistency. When something goes wrong, you run `git log -p` and read the exact decisions that led to failure.

### 2. Stateless Runtimes + Persistent Filesystem Brain

Each agent receives a task, reads filesystem context, decides, writes results, exits. Crash recovery is not a special path. On resume, the orchestrator reads `STATE.md` and `BUILDLOG.md` from disk, verifies them against git log, and proceeds. If a task is half-completed, it finishes or rolls back and retries. This is the same code path that runs on normal startup.

### 3. Cost Architecture: Haiku-First, Flat Fan-Out

Subagents are always Haiku (1/5 the per-token cost of Opus), spawned in parallel (5-8 per wave). A three-tier proposal—Fable orchestrator + Sonnet supervisors + Haiku workers—was A/B tested and showed **4.3× cost increase for identical quality**. It was cancelled. Today: flat dispatch, $0.01-0.02 per wave.

### 4. Guardrails Enforced in Code

Safety rules are executable: pre-push secret-scan (fails hard on leak or file-read error), kill-switches, cost ceilings, branch checks. The principle: fail-closed by default.

### 5. Observable Signals

Every action produces a signal. Heartbeats, append-only logs with timestamps, stalled-agent detection. The orchestrator detects drift (expected state vs git log), and on detection, re-reads from disk.

## Measured Evidence

The held-out benchmark ran 180 judgment tasks under four conditions:

| Condition | Passed | Rate |
|---|---|---|
| **Seated, repair loop** | **139/180** | **77.2%** |

One-pass alone: 67.8%. Repair loops: 77.2%. **That's a 9.4-percentage-point gain.**

On hard tasks alone (60 longest):
- One-pass: 38.3%
- Repair loop: 58.3%
- **Gain: +20 percentage points**

The repair distribution is clean: 145/180 passed on first try (80.6%), 14 used 1 retry (7.8%), 21 used 2 retries (11.7%). No task exceeded the retry cap. The largest repair count observed was 2.

For model choice: Haiku scored 39/39 on 39 judgment tasks (code review, severity calibration, root-cause analysis, refactor equivalence). Opus scored 38/39. At 1/5 the per-token cost.

**Five fault classes tested in chaos injection:**

| Fault | Detection | Recovery | MTTR |
|---|---|---|---|
| Worker termination | Journal stale check | Crash-only start | 0.0s |
| Checkpoint corruption | JSON parse | Skip, resume valid | 0.001s |
| Secret leak | Regex | Block push | 0.0s |
| Heartbeat stall | Age check | Watchdog respawn | 0.5s |
| Test failure | Exit code | Gate refuses | 0.141s |

All five passed. No infinite loops, no wedged state.

## Operational Proof

Over 18 days:
- **1,429 commits, 514 merged PRs, 31 waves** (git-verifiable)
- **16 stall incidents resolved by watchdog + crash recovery** (no consensus voting, no lease revocation)
- **Convergence loop: 52 → 0 verified defects over 5 rounds** (`docs/convergence-log-wave1.md`)
- **Zero hallucinated audits** (via adversarial verification discipline)

The system has actually crashed. Machines died mid-wave. The recovery was automatic: re-read from disk, continue.

## When Crash-Only Wins and Loses

### Wins:
- Single-team autonomous development (1-2 orchestrators, 5-10 workers)
- Rapid iteration (dev cycles, feature work)
- Cost-sensitive workloads (Haiku subagents, parallelism)
- On-premise, single-cloud (git as source of truth)
- Full auditability (every decision in git)

### Loses:
- Multi-leader writes (require consensus or serialization)
- Geographic distribution with sub-second failover
- Systems that must tolerate orchestrator failure with zero work loss
- 100-machine scale (requires event-sourcing + distributed leases)

## Honest Limits

1. **Small-N benchmark.** 39 judgment tasks is directional, not proof. Frontier reasoning (where Opus depth matters 3×) is not tested.

2. **Cheap-model failure mode on open-ended work.** An all-Haiku audit reported four P0 issues; verification found zero real. This is a *selection failure* (model choosing what to report), not a judgment failure. The architecture mitigates: pair cheap generation with independent verification.

3. **Single-box.** Multi-instance coordination is on the roadmap, not shipped. If you need 100-machine scale today, this is not it.

## The Bet

Simple systems that fail loudly outrun complex ones that hide state in databases.

Crash-only wins because:
- **67.8% to 77.2%** task success with checkpoint-first recovery
- **+20 percentage points** on hard tasks with repair loops
- **0.0s-0.5s** MTTR across five fault classes
- **16 stall incidents** resolved via watchdog + crash recovery
- **Haiku 39/39** on seam tasks at 1/5 Opus cost
- **No distributed consensus overhead** (no heartbeat quorum, no lease voting)

The entire system and every decision lives in git-committed, human-diffable files. When it breaks, you read the history and see why.

## Try It Yourself

Clone the repo and verify:

```bash
git clone https://github.com/matt82198/aesop.git
cd aesop

# Regenerate all metrics from git
bash scripts/verify-stats.sh --check

# Run the benchmark yourself
python tools/bench_runner.py --runner haiku

# Read the convergence log (5 rounds, 52→0 defects)
cat docs/convergence-log-wave1.md

# Check the incident log (16 stall recoveries)
cat docs/INCIDENTS.md
```

All numbers regenerable. All incidents in git log. No telemetry, no closed-source black boxes.

The system is not theoretical. It shipped 514 PRs, 31 waves, 1,429 commits. Built by itself. Rebuilt by itself every wave. Crashed and recovered, in production, documented.

Restart is the only recovery path. And it works.

---

**References:**
- Candea, G., & Fox, A. (2003). Crash-Only Software. *HOTOS-IX*.
- Czezatke, K., & Stengel, J. (2019). Temporal: Workflow as Code.
- `docs/crash-only-whitepaper.md` — Full design and evidence
- `docs/convergence-log-wave1.md` — 5-round audit to zero defects
- `docs/INCIDENTS.md` — Operational incident log with root causes
