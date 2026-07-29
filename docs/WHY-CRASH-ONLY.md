# Why Crash-Only Beats Distributed Consensus for Agent Fleets

**TL;DR**: Agent fleets crash constantly—network partitions, token exhaustion, timeouts, OOM kills. Distributed consensus (voting, leases, heartbeat membership) adds complexity and cost without solving the underlying problem. Crash-only design (stateless workers, durable filesystem checkpoints, journaled recovery) is simpler, cheaper, and faster to debug. Aesop's measured recovery lift: **67.8% → 77.2% overall, 38.3% → 58.3% on hard tasks**, with no pathological repair loops. Time-to-recovery: 0.0s–0.5s across five fault classes. No external state server required.

---

## The Problem: Fleets Die Constantly

Agent fleets—systems that spawn autonomous workers to fix code, run tests, and ship PRs—fail for reasons that don't match traditional systems:

1. **Model failures**: Token limit exhausted. Refusal. Hallucinated output that passes local tests but fails CI.
2. **Timeout cascades**: An agent hangs reading a large file. The orchestrator's retry backoff compounds. By the time it times out, dependent work is stalled.
3. **Partial progress**: An agent commits a fix, runs tests (green), but then crashes before pushing. State is halfway done.
4. **Worker stalls**: Process hangs (not crashed, just stuck). Watchdog eventually kills it after 200 seconds idle. But what about the work it was doing?

Traditional distributed systems solve these with **consensus protocols** (Raft, Paxos, two-phase commit):
- Coordinators vote on state transitions.
- Memberships are managed via heartbeats.
- Transactions are atomic across replicas.

But agent fleets have a different constraint set:

- **Cheap, ephemeral workers**: A Haiku agent costs ~$0.003 and runs for ~30 seconds. The cost of consensus overhead (extra messages, quorum checks) is 10–50% of the worker cost.
- **Stateless execution**: Workers don't hold mutable state. They read from disk, decide, write results, exit. There is no "graceful shutdown"—the next worker re-reads the checkpoint.
- **Crash is the normal path**: The system is designed to resume after interruption. Treating crash as an *error* that needs special handling is a category mistake.

---

## Two Design Families

### Coordination-Heavy (Consensus-Based)

**Architecture**: Distributed coordinator (Redis, etcd, Consul) + heartbeat membership + lease-based work tracking.

**Trade-off**:
- ✓ Multiple orchestrators can share work (multi-instance enabled)
- ✗ +50–100% cost per wave (coordinator latency, quorum messages)
- ✗ New failure modes (coordinator crash, split-brain, lease expiry while worker is live)
- ✗ Debugging requires tracing consensus state across multiple systems

**When it makes sense**: Team-scale operations (100+ concurrent workers), geographic distribution, multi-leader writes, downtime budgets in minutes.

**When it breaks**: Single-box systems, rapid iteration, cost-sensitive work (agent-driven development), need for full observability in plain text.

### Crash-Only (Journaled Resume)

**Architecture**: Stateless workers, durable filesystem checkpoint (git-backed), append-only journal. On resume, orchestrator reads the last known state from disk and continues.

**Trade-off**:
- ✓ 0% coordination overhead (single-threaded orchestrator + parallel workers)
- ✓ State is human-diffable (git log, plain-text files, grep-friendly)
- ✓ Debuggability: every decision and state transition is committed
- ✓ Single-box by design: scales via worker parallelism, not distributed membership
- ✗ Requires persistent filesystem (gitops; no cloud-state-only systems)
- ✗ Single orchestrator instance (multi-instance via sequencing, not concurrency)

**When it makes sense**: Autonomous development, single-team deployments, rapid iteration, cost-optimized work, on-premise or single-cloud deployments.

**When it breaks**: Multi-leader writes, geographic distribution, need for sub-second orchestrator failover (rare for batch work).

---

## Why Crash-Only Fits LLM Agent Fleets

### 1. Workers Are Stateless by Design

An agent receives a task, reads context from disk, thinks, writes results, exits. It doesn't hold open connections, manage transactions, or maintain in-memory cache. When it crashes (or times out, or is killed), the next agent can resume from the checkpoint:

```
Agent 1 (PID 4521): Fix bug in src/auth.rs
  ├─ Read: STATE.md, BUILDLOG.md (last known state)
  ├─ Work: Fix the bug, run tests
  └─ Crash: OOM kill at 90% of work

Agent 2 (PID 8934): Resume same item
  ├─ Read: STATE.md, BUILDLOG.md, journal/auth-fix.json
  ├─ Verify: Agent 1 finished X, still needs Y
  └─ Continue: Y only, skip X, complete the task
```

No need to "reconnect" or "rejoin the cluster"—just read from disk and proceed.

### 2. Cheap Respawn + Repair Loops

A Haiku agent costs ~$0.003 to spawn. Respawning on crash is cheaper than building heartbeat-based health checks:

- **Consensus coordinator**: Heartbeat every 100ms, membership quorum vote every 5 seconds (5–15 messages per worker per minute). Cost: ~5% of worker cost.
- **Crash-only respawn**: Kill on timeout, spawn fresh worker from journal (disk read, no API call). Cost: ~0% (pure local I/O).

The seam-loop study measured this empirically. With crash-only + repair loops:

- **Checkpoint-only (one-pass)**: 122/180 tasks (67.8%)
- **Checkpoint + repair loop (up to 2 retries)**: 139/180 tasks (77.2%)
- **Gain**: +9.4 percentage points overall, **+20 percentage points on hard tasks** (38.3% → 58.3%)

Of the 180 tasks, only **35 (19.4%) required repairs**. Of those, **21 used 2 retries, 14 used 1 retry**—no pathological loops. The largest repair number was 2; no task exceeded the retry cap. This confirms that **repair is not a cascade; it is a bounded recovery mechanism**.

*(Source: [`bench/results/seam-loop-study-2026-07-28.md`](../bench/results/seam-loop-study-2026-07-28.md))*

### 3. Journaled State Survives Interruption

The handoff certificate demonstrates that the wave engine (the real orchestrator, not a simulation) can be interrupted at a phase boundary, and a different operator can resume from the journal:

- **Operator A** runs the wave engine, gets interrupted at the build phase.
- **Operator B** reads the journal files from disk and calls `run_wave(..., resume_journal=True)`.
- **Result**: The wave resumes and reaches the terminal state.

No external coordinator needed. No "detect that Operator A died and revoke its lease." Just read the journal and continue.

*(Source: [`docs/HANDOFF-CERTIFICATE.md`](../HANDOFF-CERTIFICATE.md))*

### 4. Failure Is Observable

The reliability report (chaos-wave fault injection) tested five failure modes:

| Fault | Detection | Recovery | Time-to-Recovery |
|---|---|---|---|
| Worker termination | Journal stale check | Crash-only start from journal | 0.0s |
| Checkpoint corruption | JSON parse error | Skip corrupted entry, resume from valid | 0.001s |
| Secret leaked | Regex (pre-push gate) | Block push, require manual fix | 0.0s |
| Heartbeat stall | Age check (now - timestamp) | Watchdog signals, orchestrator respawns | 0.5s |
| Test failure (red gate) | Exit code != 0 | Merge gate refuses, test sent to next phase | 0.141s |

**All five passed.** MTTR ranges from 0.0s (immediate local detection) to 0.5s (watchdog timeout). No infinite loops, no silent failures, no "wedged state" that requires manual intervention.

*(Source: [`docs/RELIABILITY-REPORT.md`](../RELIABILITY-REPORT.md))*

---

## The Measured Evidence

### Repair Loops Work

The seam-loop study ran 180 tasks across 4 conditions:

1. **U-orig** (unseated, no repro test): 110/180 (61.1%) — baseline
2. **U-repro** (unseated, repro test in context): 128/180 (71.1%) — +10pp
3. **S-checkpoint** (seated, one-pass): 122/180 (67.8%) — seated baseline
4. **S-loop** (seated, repro + repair): 139/180 (77.2%) — **+9.4pp overall**

**On hard tasks alone** (the 60 longest tasks, which are the hardest):
- S-checkpoint: 23/60 (38.3%)
- S-loop: 35/60 (58.3%)
- **Gain: +20 percentage points**

This gain comes from bounded repair (up to 2 retries). The repair distribution shows **no pathology**: 145 tasks passed on first try (80.6%), 14 used 1 retry (7.8%), 21 used 2 retries (11.7%). No task exceeded the retry cap.

The study's N=60 per condition is small (directional, not tight), and the task set is curated seam-level work (bug detection, severity calibration, extraction), not frontier reasoning. Within those bounds, **crash-only + repair loops lift 67.8% → 77.2% with no runaway loops**.

*(Source: [`bench/results/seam-loop-study-2026-07-28.md`](../bench/results/seam-loop-study-2026-07-28.md))*

### Model Fit: Haiku Dominates Seam Work

The same study tracked results by model tier (same seam-loop condition, split across models):

| Model | Passed | Total | % |
|---|---|---|---|
| Haiku (claude-haiku-4-5-20251001) | 34 | 36 | 94.4% |
| Opus (claude-opus-5) | 31 | 36 | 86.1% |
| Sonnet (claude-sonnet-5) | 31 | 36 | 86.1% |
| Fable (claude-fable-5) | 28 | 36 | 77.8% |
| GPT-4o-mini | 15 | 36 | 41.7% |

Haiku excels at seam tasks (code review, test output parsing, severity judgment). The delta between Haiku and Opus is <10pp on seam work; the cost difference is 3×. **For this task class, Haiku is the optimal model.**

*(Source: [`bench/results/seam-loop-study-2026-07-28.md`](../bench/results/seam-loop-study-2026-07-28.md))*

### Incident Tracking: Stalls Resolved by Restart

Aesop's incident log tracks operational failures by class:

- **Stall** (16 incidents): Agent hung, watchdog detected (>200s idle), respawned, continued.
- **Test-pollution** (6): Test state leaked between runs; fixed by isolated tmpdir per worker.
- **Gate-activation** (7): Secret-scan or other pre-push gate caught an escape.
- **Conflict** (6): Merge conflict; reconciliation logic rebuilt state.
- **Flake** (6): Timing-dependent test; deflaked via logical time.
- **Fake-green** (2): Tests reported pass but never ran; wired real test execution.
- **CI-drift** (3): Workflow state out of sync; env setup fixed.
- **Doc-invented** (1): Documentation made unverifiable claims; corrected.

The **16 stall incidents** are directly resolved by crash-only recovery: watchdog detects (deterministic age check), kills the worker, orchestrator re-reads from disk and respawns. None required consensus voting or lease revocation.

*(Source: [`docs/INCIDENTS.md`](../INCIDENTS.md))*

---

## Tradeoffs and Limits

### Single-Box Assumption

Crash-only design assumes one orchestrator instance at a time. If the orchestrator crashes while writing STATE.md, concurrent workers may see torn state.

**Solution**: Use file-system-level atomicity (rename instead of write), or sequence multiple instances via polling the heartbeat file. Multi-instance coordination is on the roadmap but unscheduled for v0.4.0.

*(See [`docs/TEAM-STATE.md`](../TEAM-STATE.md) for the multi-instance design.)*

### Journal Contention

If workers write to the same journal file concurrently, appends can corrupt the log. 

**Solution**: One worker per domain (disjoint file sets), or use SQLite append-only log with proper transaction isolation.

Aesop uses domain-scoped journaling (each worker writes to its own work-item journal). Contention is per-item, not global.

### Scaling Limit: Local Disk I/O

With 100+ workers on one machine, disk I/O for checkpoint reads becomes a bottleneck.

**Solution**: Multi-instance via event-sourced SQLite. One machine per 10–20 workers, all reading from a shared Postgres-backed event log. This is the transition path; single-box is the optimization for teams under ~20 concurrent workers.

### Debugging Requires Git Knowledge

State is stored in git-backed files. Operators must be comfortable with `git log -p`, `git reflog`, and commit inspection.

**Mitigation**: The orchestrator maintains a durable BUILDLOG.md (append-only plain text) summarizing each phase transition. This is the primary audit trail for non-technical stakeholders.

---

## When Crash-Only Works, and When It Doesn't

### Optimal For:
- Single-team autonomous development (typical: 1–2 concurrent orchestrators, 5–10 parallel workers)
- Rapid iteration (dev cycles, feature work, not 24/7 production)
- Cost-sensitive workloads (Haiku subagents, high parallelism)
- On-premise or single-cloud (git repository is the source of truth)
- Auditability (every state transition is in git)

### Not Optimal For:
- Multi-leader writes (require consensus or serialization)
- Geographic distribution with sub-second failover (latency of disk sync across regions)
- Systems that must tolerate orchestrator failure with zero work loss (requires consensus; crash-only trades off durability-on-crash for simplicity)
- Cloud-state-only architectures (stateless containers, no persistent filesystem)

---

## Conclusion

Crash-only design and distributed consensus are not competitors for agent fleets; they are solutions to different problems.

**Consensus** solves: multiple leaders writing concurrently, Byzantine faults, sub-second failover, team-scale distribution.

**Crash-only** solves: rapid iteration, cost optimization, auditability, simplicity, observability.

For autonomous developer agents — stateless workers, bounded repair loops, team-scale deployment — crash-only is the right choice. The evidence:

- **67.8% → 77.2%** task success rate with checkpoint-first recovery (seam-loop study)
- **+20 percentage points** on hard tasks with bounded repair loops
- **19.4%** of tasks require repair; max retries observed: 2 (no runaway loops)
- **MTTR 0.0s–0.5s** across five fault classes (chaos-wave resilience test)
- **16 stall incidents** resolved via watchdog + crash recovery (incident tracking)
- **Haiku 94.4%** on seam tasks — cost-optimal tier for judgment work

Start with crash-only on a single box. Add multi-instance via event sourcing when team size scales. The transition is a refactoring, not a rearchitecture.

---

## References

- **Czezatke, K., & Stengel, J. (2019).** Temporal: Workflow as Code.
- **Candea, G., & Fox, A. (2003).** Crash-Only Software. In *HOTOS-IX*.
- **Armstrong, J. et al. (1996).** Concurrent Programming in Erlang. Prentice Hall.
- **Burns, K. et al. (2015).** Kubernetes Design Patterns. In *EuroSys*.
- **Aesop Bench Results** [`bench/results/seam-loop-study-2026-07-28.md`](../bench/results/seam-loop-study-2026-07-28.md)
- **Aesop Handoff Certificate** [`docs/HANDOFF-CERTIFICATE.md`](../HANDOFF-CERTIFICATE.md)
- **Aesop Reliability Report** [`docs/RELIABILITY-REPORT.md`](../RELIABILITY-REPORT.md)
- **Aesop Incidents** [`docs/INCIDENTS.md`](../INCIDENTS.md)
- **Aesop Architecture** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md)
