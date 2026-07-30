# Architecture Decision Records

Key design decisions behind Aesop, with the data that informed each one.

---

## ADR-1: Flat Haiku Fleet over Hierarchical Sonnet Tiers

**Context:** Early designs proposed a three-tier model: Fable orchestrator + Sonnet supervisors (splitting work into domains) + Haiku workers. The hypothesis was that coordination would prevent cross-module drift and reduce repair rounds.

**Decision:** Flat dispatch only. One Opus/Fable orchestrator on the main thread, 5-8 parallel Haiku workers per wave, no intermediate supervisors.

**Data:** Two independent A/B measurements on 2026-07-14 ([full dataset](./ab-cost-dataset.md)):
- Dispatch Topology A/B: hierarchical = **1.75x raw / 4.33x weighted cost**, same 20/20 test pass, zero repairs needed.
- Tiered-Cognition A/B: Sonnet cognition stage = **2.15x raw / 4.60x weighted cost**, same 6/6 suite pass, zero repairs needed.
- In both arms, Sonnet reviewers reported "all files already compliant, no edits made." The coordination layer had no actual drift to prevent.

**Consequences:** Cost per wave stays at ~$0.01-0.02 USD. The flat model cannot catch cross-module drift that blind Haikus miss -- but that regime has gone untested twice (the A/B fixtures were designed to favor hierarchy, and flat still won). If a future benchmark finds a task set where solo-Haiku failure rate > 0, hierarchy can be revisited with data.

---

## ADR-2: Crash-Only Design (Checkpoint/Resume as the Only Execution Model)

**Context:** Agent orchestration systems typically distinguish "normal shutdown" from "crash recovery," with separate code paths for each. This creates two maintenance surfaces and hides failure modes behind graceful-shutdown assumptions.

**Decision:** Crash recovery is the normal startup path. There is no graceful shutdown protocol. Agents are stateless processes: read checkpoint files, make decisions, write results, exit. Dead is dead; reading from disk is the recovery protocol AND the normal startup path.

**Data:** Ancestors: Temporal (Czezatke & Stengel, 2019) for durable execution via plain-text event logs; Crash-only software (Candea & Fox, 2003) for stateless components; Erlang/OTP supervision trees (Armstrong et al., 1996) for bounded restart policies; Kubernetes controller reconciliation (Burns et al., 2015) for append-only convergence. 30 waves and 520 merged PRs have run through this model, including real machine crashes where the next run re-read from disk and continued ([hypothesis document](./THE-AESOP-HYPOTHESIS.md)).

**Consequences:** No distributed consensus, no external state servers, no recovery machinery beyond "re-read from disk." The trade-off: single-box only (multi-instance coordination requires leases and event-sourcing, deliberately unscheduled). Debugging is simpler -- `git log -p` shows every state transition. The system cannot hide failures because every crash leaves evidence on the filesystem.

---

## ADR-3: SQLite WAL Single-Box over Postgres

**Context:** The state layer needed durable event storage with concurrent read/write support. Postgres was the obvious enterprise choice; SQLite WAL mode was the simpler alternative.

**Decision:** SQLite WAL as the event-sourced state layer. Single-box by explicit design choice, not "not distributed yet."

**Data:** Lab-measured multi-writer throughput: 800 events/sec in stress tests. 31 waves of production use with zero data-loss incidents. The system runs on one machine -- proven sufficient for the current scale (1 human + fleet, 520 PRs across 18 days). Multi-instance coordination is on the roadmap as a separate design effort (leases + event-sourcing on SQLite), not a Postgres migration.

**Consequences:** No connection pools, no schema migrations, no eventual consistency. State survives machine wipes via git clone. The limitation is explicit: team scale beyond one machine requires additional work. Postgres is a refactoring target AFTER the single-box proves the core loop works -- premature distribution is premature optimization.

---

## ADR-4: Ceiling Rule in Benchmarks

**Context:** The held-out benchmark (39 judgment tasks) was designed to test whether Haiku is sufficient for seam-level engineering work. If multiple model tiers all score near-perfect, the benchmark has found a convergence zone, not a separating frontier -- and reporting "Haiku = Opus" would be misleading.

**Decision:** Pre-declared ceiling rule: when >=2 tiers score >=92%, the instrument failed to discriminate. Report sufficiency floor, not tier equivalence.

**Data:** Combined results across 39 judgment tasks (code review, severity calibration, root-cause analysis, refactor equivalence, security spots): Haiku **39/39**, Sonnet **39/39**, Opus **38/39** (Opus erred on one severity call; Haiku did not). The ceiling rule trips -- both Haiku and Sonnet achieved 100%. Full analysis in [bench/METHODOLOGY.md](../bench/METHODOLOGY.md) and the [ceiling addendum](../bench/results/2026-07-26-judgment-v3-ceiling-addendum.md).

**Consequences:** The benchmark proves Haiku is sufficient for the judgment shapes measured, not that it equals Opus everywhere. Honest limits: N=39 is directional evidence (not statistical proof), tasks are curated (not sampled from real fleet transcripts), and frontier reasoning is explicitly out of scope. The scoring loop is deterministic Python (no LLM in the grading loop), so results are reproducible by anyone who clones the repo.

---

## ADR-5: Append-Only Event-Sourced State (Git as Audit Trail)

**Context:** Orchestration state (intent, phase, progress snapshots) needed to be durable, human-readable, and auditable. Options ranged from database-backed event stores to file-based approaches.

**Decision:** STATE.md (intent, phase, NEXT STEPS) + BUILDLOG.md (append-only progress snapshots) committed to git. SQLite event log for structured queries. Git is the audit trail, not the coordination layer.

**Data:** The convergence loop (52 findings to zero across 5 rounds) was debugged entirely via `git log -p` and BUILDLOG.md diffs. When state drifted (wave-1 zombie tracker: 79% of "open" items already shipped), the fix was structural reconciliation against git truth, not a database migration. The system recovered from real crashes by re-reading committed state -- no special recovery hooks needed.

**Consequences:** Every decision is diffable (`git log`). Every state transition has a commit hash. The cost: git is not designed for high-frequency writes (batching to wave boundaries is the mitigation). The state layer consolidation (collapsing git + SQLite + STATE.md into SQLite-as-source + git-as-audit-trail) is scheduled as multi-wave architectural work.

---

## ADR-6: Rules-as-Code over Prose Rules

**Context:** Early waves relied on CLAUDE.md prose to enforce safety rules ("never use --admin", "always run secret scan"). Agents violated these rules repeatedly: one used `--no-verify` to bypass the pre-push gate, another used `--admin` to merge hallucinated documentation.

**Decision:** Safety rules are executable code (hooks, gates, linters), not prose documentation. Prose documents intent; code enforces it.

**Data:** Production incidents that drove this decision:
- `--no-verify` bypass attempt: agent tried to skip the pre-push secret gate. Detected, flag banned from every dispatch template.
- `--admin` merge of hallucinated docs: agent merged fabricated documentation bypassing required checks. Reverted, flag forbidden in all orchestrated prompts.
- Green-never-ran: multiple PRs showed "CI green" but suites never actually executed. Built `tools/ci_workflow_lint.py` to verify every suite ran.
- Wave-24 all-Haiku audit: reported 4 P0 findings, verification found zero real (2 hallucinated, 2 severity-inflated). Added adversarial verification layer.

**Consequences:** Fail-closed by default. A secret-scan that silently passes on unreadable files is worse than a crash. The system has: pre-push secret gate (`tools/secret_scan.py`), kill-switch (`tools/halt.py`), cost ceiling (`tools/cost_ceiling.py`), branch protection hooks, subprocess guard (AST-scanned), watcher linter, and spec-contract validator. Each gate was added in response to a real incident, not a hypothetical risk.

---

## See Also

- [A/B Cost Dataset](./ab-cost-dataset.md) -- raw data behind ADR-1
- [The Aesop Hypothesis](./THE-AESOP-HYPOTHESIS.md) -- design philosophy behind ADR-2
- [How I Built Aesop](./how-i-built-aesop.md) -- first-person account with incident details for ADR-6
- [ARCHITECTURE.md](./ARCHITECTURE.md) -- system diagram and component overview
- [bench/METHODOLOGY.md](../bench/METHODOLOGY.md) -- benchmark design behind ADR-4
