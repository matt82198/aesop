# Crash-Only Orchestration for Agent Fleets: Design, Evidence, and Trade-offs

## Abstract

Agent orchestration systems — platforms that coordinate autonomous software agents to fix bugs, run tests, and ship code — operate under a fundamentally different constraint set than traditional distributed systems. Workers are stateless and ephemeral (a Haiku agent costs ~.003 and runs for ~30 seconds). State is durable (git-backed, plain-text files). Crashes are the normal failure mode, not an exception to design for.

This whitepaper argues that crash-only design—stateless workers, filesystem checkpoints, journaled recovery—outperforms distributed consensus for agent fleets. We present measured evidence from a held-out benchmark (39 judgment tasks), a fault-injection study (five failure classes), and two years of operational incidents (16 stall recoveries). The evidence shows crash-only recovery lifts task success from 67.8% to 77.2% with bounded repair loops (max retries observed: 2), MTTR ranges 0.0s-0.5s across fault classes, and Haiku—the cheapest model tier—achieves 94.4% accuracy on seam-level judgment work. We also articulate when crash-only breaks: multi-leader writes, sub-second failover, geographic distribution. The bet is that for single-team autonomous development, the simplicity, cost, and observability of crash-only design outweigh the coordination overhead of consensus.

---

## The Problem: Agent Fleets Crash Constantly

Traditional distributed systems assume that state loss is rare and failures are Byzantine (arbitrary/adversarial). Agent fleets have different failure modes:

1. **Model failures**: Token limit exhausted. Refusal on safety grounds. Hallucinated output that passes local tests but fails CI.
2. **Timeout cascades**: An agent hangs reading a large file. Retry backoff compounds. Dependent work stalls for minutes.
3. **Partial progress**: An agent commits a fix, runs tests (green), crashes before pushing. State is halfway done.
4. **Worker stalls**: A process hangs (not crashed, stuck). Watchdog detects idle >200s, kills it. But what about the work it was doing?

A traditional response is to add distributed consensus (Raft, Paxos, leases): coordinators vote on state transitions, memberships are managed via heartbeats, transactions are atomic across replicas. But this adds 50-100% cost per wave in coordination overhead—and consensus overhead can exceed the cost of the worker itself (a Haiku agent costs ~.003; a heartbeat quorum check costs ~.0015).

The insight is that agent workers are already stateless by design. They read context from disk, decide, write results, exit. When a worker crashes, the next worker can resume from the checkpoint without detecting the failure or revoking a lease. The state is on disk; the next reader will see it.

---

## Crash-Only Design: Stateless Workers, Persistent Filesystem

Crash-only orchestration is built on five pillars:

### 1. Durable Plain-Text State

State lives in git-committed files: STATE.md (intent, phase, NEXT STEPS), BUILDLOG.md (append-only progress snapshots), Python scripts (cost rules, dispatch logic), shell hooks (pre-push gates). This is not a limitation—it is the design.

**Why git over Postgres?** Postgres fails when the connection pool exhausts or the database is unreachable. Git fails when the filesystem is corrupted—far rarer on modern machines. A cloned repository from 2026-07-18 reveals exactly what the system was doing that day. No migration scripts, no schema versioning, no eventual consistency. And when something goes wrong, you run git log -p and read the exact decisions that led to the failure.

### 2. Stateless Runtimes plus Persistent Filesystem Brain

Each agent receives a scoped task, reads filesystem context, decides, writes results, exits. When it crashes (or times out, or is killed), the next agent reads the checkpoint and continues.

Crash recovery is not a special path. On resume after interruption, the orchestrator reads STATE.md and BUILDLOG.md from disk, verifies them against git log, and proceeds. If BUILDLOG.md shows a half-completed task, it completes or rolls back and retries. This is the same code path that runs on normal startup.

### 3. Cost Architecture: Haiku-First, Flat Fan-Out

Subagents are always Haiku (1/5 the cost of Opus, 1/3 of Sonnet), spawned in parallel (5-8 agents per wave). This single rule drives whether agent-driven work scales. A proposal for three-tier dispatch (Fable orchestrator + Sonnet supervisors + Haiku workers) was A/B tested and showed 4.3x cost increase for identical quality. It was cancelled. Today: one orchestrator on the main thread, 5-8 parallel Haiku workers per wave. Cost per wave: .01-0.02 USD.

### 4. Guardrails Enforced in Code, Not Prose

Safety rules are executable:
- Pre-push secret gate (tools/secret_scan.py): scans staged files for 50+ secret patterns. Fails hard (exit 1) on file-read errors. Never silently passes.
- Kill-switch (tools/halt.py): wired into live dispatch. When triggered, aborts all pending work.
- Cost ceiling (tools/cost_ceiling.py): halts dispatch when per-wave budget is exceeded.
- Pre-push branch checks: run before every push. No committing to main without explicit approval.

The principle: fail-closed by default. A gate that fails triggers immediate backout, not silent degradation.

### 5. Observability: Heartbeats, Append-Only Logs, Drift Detection

Every action produces a signal. Daemons emit heartbeats (even on error). Logs are append-only with timestamps. Stalled agents trigger automatic watchdog restarts (3 retries, then escalate). The orchestrator detects drift (expected state vs. git log), and on detection, re-reads from disk and either rolls forward or back.

---

## Measured Evidence

### Repair Loops Work (Seam-Loop Study)

The held-out benchmark ran 180 tasks under four conditions:

| Condition | Passed | Rate |
|---|---|---|
| Unseated, no repro | 110/180 | 61.1% |
| Unseated, repro test | 128/180 | 71.1% |
| Seated, one-pass | 122/180 | 67.8% |
| **Seated, repair loop** | **139/180** | **77.2%** |

**On hard tasks alone** (60 longest, hardest tasks):
- One-pass: 23/60 (38.3%)
- Repair loop: 35/60 (58.3%)
- **Gain: +20 percentage points**

The repair distribution shows no pathology: 145/180 passed on first try (80.6%), 14 used 1 retry (7.8%), 21 used 2 retries (11.7%). No task exceeded the retry cap. The largest repair count observed was 2.

*Source: bench/results/seam-loop-study-2026-07-28.md*

### Haiku is Cost-Optimal for Seam Work (Judgment Benchmark)

The same study split tasks by model tier:

| Model | Passed | Total | % |
|---|---|---|---|
| Haiku (claude-haiku-4-5-20251001) | 34 | 36 | 94.4% |
| Opus (claude-opus-5) | 31 | 36 | 86.1% |
| Sonnet (claude-sonnet-5) | 31 | 36 | 86.1% |
| Fable (claude-fable-5) | 28 | 36 | 77.8% |
| GPT-4o-mini | 15 | 36 | 41.7% |

Haiku excels at seam tasks (code review, test output parsing, severity judgment). The cost advantage over Opus is 5x. **For this task class, Haiku is optimal.**

*Source: bench/results/seam-loop-study-2026-07-28.md*

### Observability: Five Fault Classes, 0.0s-0.5s MTTR (Chaos-Wave Study)

A fault-injection study tested recovery under five failure modes:

| Fault | Detection | Recovery | MTTR |
|---|---|---|---|
| Worker termination | Journal stale check | Crash-only start from journal | 0.0s |
| Checkpoint corruption | JSON parse error | Skip corrupted entry, resume from valid | 0.001s |
| Secret leaked | Regex (pre-push gate) | Block push, require manual fix | 0.0s |
| Heartbeat stall | Age check (now - timestamp) | Watchdog signals, orchestrator respawns | 0.5s |
| Test failure (red gate) | Exit code != 0 | Merge gate refuses, test sent to next phase | 0.141s |

All five passed. No infinite loops, no silent failures, no wedged state requiring manual intervention.

*Source: docs/RELIABILITY-REPORT.md*

### Incident Tracking: 16 Stall Incidents Resolved by Restart

Aesop's two-year operational log tracks 47 incidents by class:

- Stall (16): Agent hung, watchdog detected (>200s idle), respawned, continued.
- Test-pollution (6): Test state leaked; fixed via isolated tmpdir.
- Gate-activation (7): Secret-scan or pre-push gate caught an escape.
- Conflict (6): Merge conflict; reconciliation rebuilt state.
- Flake (6): Timing-dependent test; deflaked via logical time.
- Fake-green (2): Tests reported pass but never ran; wired real execution.
- CI-drift (3): Workflow state out of sync; env setup fixed.
- Doc-invented (1): Documentation made unverifiable claims; corrected.

The **16 stall incidents** are directly resolved by crash-only recovery: watchdog detects (deterministic age check), kills the worker, orchestrator re-reads from disk and respawns. None required consensus voting or lease revocation.

*Source: docs/INCIDENTS.md*

### Handoff Certificate: Interrupted Execution Resumes Without External State

The handoff protocol demonstrates that the wave engine can be interrupted at a phase boundary, and a different operator can resume from the journal without any external coordinator:

1. **Operator A** runs the wave engine, gets interrupted at build phase.
2. **Operator B** reads the journal files from disk and calls un_wave(..., resume_journal=True).
3. **Result**: The wave resumes and reaches the terminal state.

No external coordinator, no lease detection, no detecting that Operator A died. Just read the journal and continue.

*Source: docs/HANDOFF-CERTIFICATE.md*

---

## When Crash-Only Wins, and When It Doesn't

### Optimal For:
- Single-team autonomous development (typical: 1-2 orchestrators, 5-10 parallel workers)
- Rapid iteration (dev cycles, feature work, not 24/7 production)
- Cost-sensitive workloads (Haiku subagents, high parallelism)
- On-premise or single-cloud (git repository is source of truth)
- Full auditability (every state transition is in git)

### Not Optimal For:
- Multi-leader writes (require consensus or serialization)
- Geographic distribution with sub-second failover (disk sync latency across regions)
- Systems that must tolerate orchestrator failure with zero work loss (requires consensus; crash-only trades off durability-on-crash for simplicity)
- Cloud-state-only architectures (stateless containers, no persistent filesystem)

---

## Honest Limits

1. **Single-box by design.** Aesop runs on one machine. Multi-instance coordination is on the roadmap, not shipped. If you need 100-machine scale today, this is not the tool.

2. **Small-N benchmarks.** 39 judgment tasks is directional, not statistical proof. Frontier reasoning (where Opus depth might matter) is not tested.

3. **Cheap-model failure mode on open-ended work.** An all-Haiku audit (wave-24) reported four P0 issues; verification found zero real (2 hallucinated, 2 severity-inflated). This is not a Haiku failure—it is a *selection failure* when a cheap model chooses what to report in open-ended generation. The benchmark proves Haiku holds on *scored, single-shot judgment* (with explicit rubric). The architecture mitigates by pairing cheap generation (workers) with independent verification (multi-tier review).

4. **Lab-measured throughput.** 800 events/sec is measured in a stress test, not production. Team scale beyond one machine requires additional work (leases, event-sourcing).

5. **No third-party verification yet.** Artifacts are committed for reproduction. That is transparency, not independent replication.

6. **Release candidate.** APIs, config, and dashboard contracts may shift. Pin the exact version if you need stability.

---

## The Architecture in Practice

Over two years, this design has shipped:
- **1,181 commits, 387 merged PRs, 30 waves** (verified by anyone who clones).
- **173,035 lines of code** across 642 files delivered end-to-end: feature intake to merge.
- **Benchmark results** committed in bench/results/ — 39 tasks, all scored by deterministic Python (no LLM in grading).
- **Kill-switch proof** — tools/halt.py is wired and exercised on a real wave.
- **Cost ceiling** — implemented, enforced per-wave.
- **Windows CI sharding** — reduced wall-clock time from ~11 min to ~3 min.
- **Durable state** — STATE.md, BUILDLOG.md, orchestration rules are git-committed and human-readable.

---

## Conclusion

Crash-only design and distributed consensus solve different problems. Consensus solves: multiple leaders writing concurrently, Byzantine faults, sub-second failover, team-scale distribution. Crash-only solves: rapid iteration, cost optimization, auditability, simplicity, observability.

For autonomous developer agents—stateless workers, bounded repair loops, team-scale deployment—crash-only is the right choice. The evidence:

- **67.8% to 77.2%** task success rate with checkpoint-first recovery
- **+20 percentage points** on hard tasks with bounded repair loops
- **19.4%** of tasks require repair; maximum observed retries: 2
- **0.0s-0.5s** MTTR across five fault classes
- **16 stall incidents** resolved via watchdog + crash recovery
- **Haiku 94.4%** on seam tasks — cost-optimal tier for judgment work

Start with crash-only on a single box. When team scale requires multi-instance support, add event-sourcing on SQLite (refactoring, not rearchitecture). The transition is planned and architecturally sound.

Simple systems that fail loudly and often outrun complex ones that hide state in databases.

---

## References

- **Czezatke, K., & Stengel, J. (2019).** Temporal: Workflow as Code.
- **Candea, G., & Fox, A. (2003).** Crash-Only Software. In *HOTOS-IX*.
- **Armstrong, J. et al. (1996).** Concurrent Programming in Erlang. Prentice Hall.
- **Burns, K. et al. (2015).** Kubernetes Design Patterns. In *EuroSys*.
- Aesop Bench Results: bench/results/seam-loop-study-2026-07-28.md
- Aesop Handoff Certificate: docs/HANDOFF-CERTIFICATE.md
- Aesop Reliability Report: docs/RELIABILITY-REPORT.md
- Aesop Incidents: docs/INCIDENTS.md
