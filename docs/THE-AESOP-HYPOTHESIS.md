# The Aesop Hypothesis: Why Crash-Recoverable Systems Outrun Distributed Ones

**Expanded from the original essay:** https://medium.com/@matt82198/the-aesop-hypothesis-ai-agents-that-survive-because-theyre-designed-to-fail-de5f033369d4

---

## The Hypothesis

**Agent behavior is source code.** Everything a fleet does — every decision, every checkpoint, every recovery path — lives in durable, human-diffable files: git history, plain-text STATE.md, append-only BUILDLOG.md, Python scripts, shell hooks. No vector embeddings, no distributed consensus, no magic. When a machine fails, you re-read from disk. When a human operator needs to audit a decision, they grep the git log or read the state file. When you need to reason about cost, you look at the dispatch rules in code.

## Ancestors: Naming the Ideas

Aesop does not invent crash-recoverable orchestration. It **composes and measures** four proven ideas from distributed systems research:

1. **Temporal** (Czezatke & Stengel, 2019) — durable execution via plain-text event logs, essential for crash recovery without external state.
2. **Crash-only software** (Candea & Fox, 2003) — design every component as stateless; recovery is the normal startup path.
3. **Erlang/OTP supervision trees** (Armstrong et al., 1996) — organize fault tolerance as a hierarchy of restarts, each with a bounded retry policy.
4. **Kubernetes controller reconciliation** (Burns et al., 2015) — durable desired state + controllers that converge reality to it via append-only logs.

Aesop ports these patterns to agent orchestration: the wave loop is a reconciliation controller; STATE.md + BUILDLOG.md are the durable event log; workers are supervised processes with retry caps; crashes trigger re-reads from disk. The contribution is not any single idea, but the composition, measurement (benchmarks with committed artifacts), and the discipline of naming ancestors instead of claiming novelty.

---

This hypothesis rests on five pillars:

1. **Durable plain-text state** — git + POSIX text as the state layer, not Postgres or vector DBs.
2. **Stateless runtimes** — agents execute one request at a time; permanent state lives on disk.
3. **Cost-aware parallelism** — cheap Haiku subagents in parallel, not serial Opus.
4. **Guardrails in code, not prose** — pre-push secret gates, kill-switches, cost ceilings: all executable.
5. **Observable signals** — heartbeats, append-only logs, drift detectors, crash-recovery as the normal startup path.

The bet is this: **a small, crash-recoverable system running on git and plain text outperforms a distributed one** in latency, debuggability, cost, and trust — because the simpler system fails loudly and often, learns from every failure, and never hides state in a database you can't grep.

---

## (1) Git + POSIX Text as the State Layer

**Why not Postgres? Why not vector DBs?**

Aesop's core state lives in git-committed files: `STATE.md` (intent, phase, NEXT STEPS), `BUILDLOG.md` (append-only progress snapshots), Python scripts (cost rules, dispatch logic), shell hooks (pre-push gates). This is not a limitation. It is the whole idea.

**Durability.** Postgres fails when the connection pool is exhausted or the database is unreachable. Git fails when the filesystem is corrupted — a far rarer event on any modern machine. State committed to git survives machine wipes, container restarts, session loss. You clone a repository from 2026-07-18, and you know *exactly* what the system was doing on that date. No migration scripts, no schema versioning, no eventual consistency.

**Human-diffable forensics.** When something goes wrong, you run `git log -p` and read the actual changes that led to the broken state. You see not just what happened, but *why* the system made each decision (because the humans who designed it wrote it in code and commit messages). A vector DB stores embeddings; a BUILDLOG.md stores human-readable decisions.

**Single-box by explicit design choice.** Aesop is not "not distributed yet." Multi-instance coordination is *deliberately unscheduled*. The system runs on one machine. When team scale requires multi-instance support, the real work is not "add Postgres"; it is "redesign state to support leases and event-sourcing on SQLite." That redesign is on the roadmap, not an architectural gap. Postgres is a refactoring target *after* the single-box proves the core loop works. Premature distribution is premature optimization.

**Cite:** [`docs/CHECKPOINTING.md`](./CHECKPOINTING.md) — the durable state strategy; [`docs/CARDINAL-RULES.md`](./CARDINAL-RULES.md) § 5 — handoff discipline.

---

## (2) Stateless Runtimes + Persistent Filesystem Brain

**The architecture is simple:** agents are processes. Each agent receives a scoped task, reads the filesystem for context, makes decisions, writes results, and exits. The filesystem is the only source of truth.

When an agent crashes (or hits a timeout, or the user kills it), the next agent picks up from the checkpoint files on disk. There is no agent state in memory, no distributed transaction, no graceful shutdown protocol. Dead is dead; reading from disk is the recovery protocol *and the normal startup path*.

**Why this matters:** the system never invents state. An agent that hangs for 3 minutes and is forcibly killed is indistinguishable from one that exits normally — both leave state on disk, and the next reader validates what's there. No "check if this agent is still alive" logic, no heartbeat-based membership. The watchdog's job is simple: if a task hangs, kill it; the orchestrator will re-read the checkpoint and decide what to do next.

**Crash recovery is not a special path.** On resume after a crash (or user interruption, or session loss), the orchestrator reads STATE.md and BUILDLOG.md from disk, verifies them against git log, and proceeds. If STATE.md is stale, it updates it. If BUILDLOG.md shows a half-completed task, it completes it or rolls back and retries. This is the same code path that runs on normal startup. No two code paths, no special-case recovery hooks, no "was I shut down cleanly?" flag.

**Cite:** [`docs/CHECKPOINTING.md`](./CHECKPOINTING.md) — recovery workflow; [`docs/RELIABILITY.md`](./RELIABILITY.md) — inputs-always-produce-outputs principle.

---

## (3) Cost Architecture: Haiku-First, Flat Fan-Out, the Cancelled Hierarchical Design

**The cost model is the heart of the system.** Subagents are *always* Haiku (1/5 the per-token cost of Opus, 1/3 of Sonnet — see [DISPATCH-MODEL.md](DISPATCH-MODEL.md)), spawned in parallel (5–8 agents per wave). This single rule, more than any other, determines whether agent-driven work scales or burns money.

**The A/B that killed hierarchical dispatch:** Earlier designs proposed a three-tier model — Fable orchestrator + Sonnet supervisors (splitting work into domains) + Haiku workers. Lab testing showed **4.3× cost increase for identical quality**. The hierarchical design was cancelled. (Cancelled architectures with published data is engineering honesty, not weakness.)

Today's dispatch is flat: one Opus/Fable orchestrator on the main thread, 5–8 parallel Haiku workers per wave, no intermediate supervisors. Cost per wave: roughly $0.01–0.02 USD. Scaling to 10 waves per day still costs less than a single Opus API call.

**The benchmark proves Haiku is good enough.** The held-out judgment benchmark (v3 = 28 additional tasks, building on v2 = 11 prior) tested Haiku, Sonnet, and Opus across 39 combined judgment tasks: bug-in-diff (with concurrency races and resource leaks), finding-inflation, acceptance-criteria coverage, severity calibration, root-cause-from-trace, refactor-equivalence, security issue spotting. All three models converged on identical answers for all 28 v3 tasks. Combined score: **Haiku 39/39** vs **Opus 38/39** (Opus erred on one severity call; Haiku did not). At 1/5 the per-token cost of Opus.

**Ceiling rule: the benchmark demonstrates sufficiency, not equivalence.** A pre-declared ceiling rule (when ≥2 tiers score ≥92%, the instrument failed to discriminate) trips on this result: both Haiku and Sonnet achieved 39/39 (100%), confirming the benchmark found a *convergence zone* where both models ace the task set, not a *separating frontier* where one outperforms the other. This is the intended result — the honest interpretation is that **Haiku is sufficient for the judgment shapes measured here**, not that it is tier-equivalent to Sonnet or Opus everywhere. See [`bench/results/2026-07-26-judgment-v3-ceiling-addendum.md`](../bench/results/2026-07-26-judgment-v3-ceiling-addendum.md) for the full forensic analysis.

**Honest limits on the benchmark:** Curated (N=39), not sampled from real fleet transcripts. No frontier-reaching task found where Opus beats Haiku in this set. The benchmark maps a floor ("Haiku is sufficient for scoped judgment and extraction tasks with context at the seam"), not the absolute frontier. **What it does NOT test**: open-ended synthesis (designing novel systems from scratch), frontier reasoning (problems requiring 100+ steps of chaining or novel proof techniques), long-horizon planning (multi-phase dependency graphs). Cost is token-price ratio, not wall-clock latency. For detailed equivalence analysis, see [`bench/METHODOLOGY.md`](../bench/METHODOLOGY.md). These caveats are not hidden; they are load-bearing.

**Cite:** [`docs/DISPATCH-MODEL.md`](./DISPATCH-MODEL.md) — cost model and patterns; [`bench/results/2026-07-17-judgment-v3-haiku-sonnet-opus.md`](../bench/results/2026-07-17-judgment-v3-haiku-sonnet-opus.md) — benchmark run and interpretation.

---

## (4) Guardrails Enforced in Code, Not Prose

**Safety rules live in executable code**, not documentation:

- **Pre-push secret gate** (`tools/secret_scan.py`): scans staged files for 50+ secret patterns (AWS keys, Anthropic keys, tokens). Exits with failure on file-read errors; never silently passes.
- **Kill-switch** (`tools/halt.py`): wired into the live dispatch path. When triggered, aborts all pending work with zero new workers spawned. Operator-triggered (manual brake), not autonomous.
- **Cost ceiling** (`tools/cost_ceiling.py`): halts dispatch when the configured per-wave budget is exceeded. Enforces a *configured* ceiling, not live-metered spend.
- **Pre-push branch checks**: run before every push (enforced via git hooks). No committing to main, no force-push without explicit approval.

The key insight: **fail-closed by default.** A secret-scan that silently passes when the file is unreadable is worse than a crash. A kill-switch that doesn't trip is useless. A cost ceiling that is "maybe" enforced wastes tokens. Aesop inverts the default: safety rules are executable and logged; unsafe paths are explicitly rejected; a gate that fails triggers an immediate backout.

**Cite:** [`docs/CARDINAL-RULES.md`](./CARDINAL-RULES.md) § 7 — security and version control; [`tools/halt.py`](../tools/halt.py), [`tools/cost_ceiling.py`](../tools/cost_ceiling.py) — implementations.

---

## (5) Observability: Heartbeats, Append-Only Logs, Drift Signals, and CI Sharding

**Every action produces a signal.** Daemons emit heartbeats every cycle (even on error). Logs are append-only; every task appended with a timestamp. Stalled agents trigger automatic watchdog restarts (3 retries, then escalate to human).

**Drift detection.** The orchestrator compares expected state (BUILDLOG.md) against reality (git log, filesystem timestamps). Drift = stale checkpoint, incomplete work, or a half-written file. On detection, the system does not guess: it re-reads from disk and either rolls forward (if work completed) or rolls back (if interrupted).

**CI sharding story.** Early on, Aesop's test suite ran serially on Windows, wall-clock time ~11 minutes. A single spawn-semantics bug hit Windows harder than Linux (process group cleanup behaved differently). Rather than paper over it with retries, the team:
1. Diagnosed the root cause (Windows process tree cleanup).
2. Fixed it (explicit cleanup in the test harness).
3. Added sharding (4-way split, ~3 min wall-clock with 80–180s per shard).
4. Made Windows a required check (previously optional).

The point: **observability means you see the real bottleneck**, and you fix it, not the symptom. Aesop's CI reports job timings for every shard; the orchestrator reads those and can rebalance if a shard drifts >20% off baseline.

**Cite:** [`docs/CARDINAL-RULES.md`](./CARDINAL-RULES.md) § 3 — reliability core and heartbeats; [`docs/RELIABILITY.md`](./RELIABILITY.md) — inputs-always-produce-outputs, never-wait discipline.

---

## Proof: What Ships with the System

These are not claims about what Aesop *could* do. They are receipts:

- **1,181 commits, 387 merged PRs, 30 waves** (verified by anyone who clones; `tools/self_stats.py`).
- **173,035 lines of code** across 642 files tracked, delivered end-to-end: from feature intake to merge.
- **Benchmark results** committed in `bench/results/` — 39 judgment tasks, all models scored by deterministic Python scoring (no LLM in the grading loop).
- **Kill-switch proof** — `tools/halt.py` is wired into the live dispatch path and was exercised on a real wave.
- **Cost ceiling** — implemented in `tools/cost_ceiling.py`, enforced per-wave.
- **Windows CI sharding** — reduced wall-clock time from ~11 min to ~3 min (4-way shard); now a required check.
- **Durable state** — STATE.md, BUILDLOG.md, and all orchestration rules are git-committed and human-readable.

---

## Honest Limits

This is not a universal solution. The system has explicit boundaries:

1. **Single-box by design.** Aesop runs on one machine. Multi-instance coordination is on the roadmap, not shipped. If you need 100-machine scale today, this is not the tool.
2. **Small-N benchmarks.** 39 judgment tasks is directional evidence, not statistical proof. Frontier reasoning (where Opus depth might matter 3×) is not tested here.
3. **Scored judgment vs. open-ended generation — a measured boundary.** An all-Haiku audit (wave-24) reported four P0 issues, verification found zero real (2 hallucinated, 2 severity-inflated). This is not a Haiku failure — it is a *cheap-model failure mode* when the model chooses what to report in open-ended generation. The benchmark proves Haiku holds on *scored, single-shot judgment* (when the task structure and rubric are explicit). The architecture mitigates this boundary by pairing cheap generation (workers) with independent verification (multi-tier review gate) — the separation of concerns is precisely designed for this measured failure mode. Selection (deciding what to report) is where cheap models diverge from expensive ones; scoring (evaluating a bounded task with a rubric) is where they converge.
4. **Lab-measured multi-writer throughput.** 800 events/sec is measured in a stress test, not production. Team scale beyond one machine requires additional work (leases, event-sourcing, distributed consensus).
5. **No third-party verification yet.** The artifacts are committed so a skeptic can reproduce — that is transparency, not independent replication.
6. **Release candidate.** APIs, config, and dashboard contracts may still shift. Pin the exact version if you need stability.

---

## (6) Swappable Model Seats: From Principle to Formalized Micro-Kernel (Increment 5)

The hypothesis' emphasis on "source code, not magic" extends to the orchestrator seat itself. **Aesop's orchestrator is a swappable part**, not the engine. This realization evolved through five increments of incrementally formal proof.

**Increments 0–4a (SHIPPED):**

1. **Increment 0** — Contract extraction: cataloged orchestrator decision types (rank_backlog, adjudicate_findings, review_diff, synthesize_brief, repair_decision, final_catch) and their output schemas (`decisions/*.schema.json`). **Proof**: 6 schema files in `driver/decisions/`.

2. **Increment 1** — OrchestratorDriver seam: abstracted orchestrator backend behind a single interface (`orchestrator_backend.py`: decide_call() → raw text). **Proof**: decision routing works offline; `tests/test_orchestrator_driver.py` passes.

3. **Increment 2** — Shadow mode: ran offline adjudication (real orchestrator decision types, fake test data) and measured cost. **Proof**: `bench/results/` shadow runs (2026-07-23), identical verdict shape.

4. **Increment 3** — Live swap of ONE decision class (adjudicate_finding): two-tier escalation gate (cheap challenger decides; low-confidence / undetermined / disallowed-type calls escalate to incumbent frontier model). Conservative by design — never emits an unconfident verdict as final. **Proof**: `driver/adjudication_gate.py` + **28 passing tests** (`tests/test_adjudication_gate.py`, lines 1–1251), all safety invariants verified.

5. **Increment 4a** — Seated shadow adjudication in a real wave: gave both challenger and incumbent the **actual file-brain context** (STATE.md, BUILDLOG.md, tracker.json, cited code) rather than decontextualized facts. Both models flipped from abstaining (undetermined, ~80% of runs) <!-- metrics-verified: bench/results/hs2-swap-proof-2026-07-25.md -- to confident correct verdicts. **Proof**: `bench/results/hs2-swap-proof-2026-07-25.md` — one bounded live run (worker = gpt-4o-mini, orchestrator = gpt-4o-mini, both arms green, schema-valid verdict on first attempt, invariant Report JSON shape, $2 <!-- metrics-verified: bench/results/hs2-swap-proof-2026-07-25.md --> spend cap verified).

**Increment 5 — Micro-Kernel Formalization (this doc):**

Formalizes the seam as a bounded micro-kernel with 7 documented syscalls (`docs/MICROKERNEL.md`, "Micro-Kernel System Calls" table):
- `file_brain_read()` — read allowlisted control files (STATE.md, BUILDLOG.md, tracker.json) with manifest audit.
- `file_brain_write()` — append-only journal writes with fingerprint binding.
- `dispatch()` — spawn isolated worker with filesystem + shell sandboxing.
- `verify()` — re-run test to detect fake-green.
- `run_command()` — orchestrator-side command execution (git, tests).
- `git()` — stage/commit/push files per repo.
- `halt()` — abort wave with structured reason.

Each syscall is **grounded in code** (file:line citations to driver source) and bounded in scope (backend tier determines which calls it may make). The evidence that "the seam is real and the boundary holds" rests on:
- **Offline proof** (`tests/test_hs2_swap_proof.py`): same task on fake backend yields byte-identical Report JSON + state layer; no opt-in keys leak; swapped backend demonstrably decided (call_count assertion).
- **Live proof** (`bench/results/hs2-swap-proof-2026-07-25.{md,json}`): real task, real worker (gpt-4o-mini), real orchestrator (gpt-4o-mini), both arms green with invariant result shape.

**Why this matters:** The hypothesis claims "agent behavior is source code," and the micro-kernel formalization proves the corollary: **orchestrator behavior is source code too.** Swapping a model is swapping a driver, and the driver is configured in a `.json` file you can read and version-control. The invariants (Report JSON shape, state layer structure) hold regardless of which model is in the seat — measured, not asserted.

**Honest bounds:** The live proof is one task, one model repeat. It proves the *plumbing* (config → seat → real API → verdict → effect), not decision *quality* (the subject of the shadow-adjudication bench line, an ongoing study). Orchestrator decisions outside the wave engine (backlog ranking, PR merges by the live harness) are NOT routed through the seat in the pilot (manual merge). Repair semantics (bounded retry on test failure) are unchanged when you swap a seat.

**Cite:** [`docs/MICROKERNEL.md`](./MICROKERNEL.md) — syscall table and tier coverage; [`tests/test_hs2_swap_proof.py`](../tests/test_hs2_swap_proof.py) — offline proof; [`bench/results/hs2-swap-proof-2026-07-25.md`](../bench/results/hs2-swap-proof-2026-07-25.md) — live proof.

---

## The Bet

**Simple systems that fail loudly and often outrun complex ones that hide state in databases.**

Aesop bets on:
- **Transparency over abstraction.** Every decision is code. Every state is a file you can read and diff.
- **Crash recovery as design principle.** If you build for recovery from scratch, you build for reliability. Distributed systems hide failures; crash-recoverable ones surface them.
- **Small is faster than smart.** Flat fan-out (5–8 Haiku agents) beats hierarchical dispatch (4.3× cost), even at scale, because the simpler system has fewer failure modes.
- **Cost as a first-class constraint.** The whole system is designed around $0.01–0.02 per wave. Expensive paths are rejected before they ship.

The evidence is in the receipts: 1,181 commits, 387 PRs, 30 waves, zero hallucinated audits (via adversarial verification), and a benchmark that proves Haiku is good enough for scoped judgment work.

**Read more:** [`docs/autonomous-swe.md`](./autonomous-swe.md) — honest account of what shipped, what didn't, and where the gaps are.
