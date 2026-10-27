# Design decisions and the cost model

Aesop records its architecture decisions as ADRs in [docs/DECISIONS.md](../DECISIONS.md) and its cost
reasoning in [docs/DISPATCH-MODEL.md](../DISPATCH-MODEL.md). This page lists them, says what each one
rests on, and says which numbers you can reproduce from this repository and which you cannot.

## The six decisions

<!-- CMD: grep -n '^## ADR' docs/DECISIONS.md
OUTPUT:
7:## ADR-1: Flat Haiku Fleet over Hierarchical Sonnet Tiers
22:## ADR-2: Crash-Only Design (Checkpoint/Resume as the Only Execution Model)
34:## ADR-3: SQLite WAL Single-Box over Postgres
46:## ADR-4: Ceiling Rule in Benchmarks
58:## ADR-5: Append-Only Event-Sourced State (Git as Audit Trail)
70:## ADR-6: Rules-as-Code over Prose Rules
-->

| ADR | Decision in one line | Where | What you can check here |
|---|---|---|---|
| 1 | One orchestrator plus a flat fleet of cheap-tier workers, no supervisor tier | `docs/DECISIONS.md:7` | The decision text. The A/B numbers are author-reported (see below) |
| 2 | Crash-only: a lane that fails is killed, never corrected; restart is recovery | `docs/DECISIONS.md:22` | The lane contract text, `LANE-CONTRACT.md:5-8` |
| 3 | SQLite in WAL mode, single machine, by choice | `docs/DECISIONS.md:34` | `state_store/store.py:5` (WAL) |
| 4 | A benchmark that cannot separate tiers must say so (ceiling rule) | `docs/DECISIONS.md:46` | `bench/METHODOLOGY.md` |
| 5 | State is `STATE.md` plus an append-only `BUILDLOG.md` in git, with a SQLite log for queries | `docs/DECISIONS.md:58` | `docs/CHECKPOINTING.md` |
| 6 | Safety rules are executable code, not prose | `docs/DECISIONS.md:70` | The pre-push chain (23 checks, below) and the three example hooks in `hooks/claude/` |

<!-- CMD: grep -cE '^check_[a-z_]+\(\)' hooks/pre-push-policy.sh
OUTPUT: 23 -->

## What is author-reported and not reproducible from this repository

ADR-1 rests on two A/B runs dated 2026-07-14. The dataset page
([docs/ab-cost-dataset.md](../ab-cost-dataset.md)) reports, for the dispatch-topology run, a hierarchical
arm at 1.75x raw output tokens and 4.33x weighted cost against the flat arm, with the same 20 of 20 tests
passing in both arms. Read these limits before you rely on them:

- The raw measurement artifacts are in a private archive. The dataset page says so itself
  (`docs/ab-cost-dataset.md:6,144-148`). The harness and the journal are not in this repository.
- Each run used one fixture. The dispatch-topology run used a single fixture of six modules
  (`docs/ab-cost-dataset.md`, "A/B Test 1"). Treat it as one data point, not a rate.
- The weights (1 for the cheap tier, 3 for the middle tier, 5 for the top tier) are a modeling choice stated in
  the dataset page, not a measured price.
- Figures are output tokens only, as the dataset page labels them.

So the honest label for "hierarchical dispatch costs more at equal quality" is: author-reported, one fixture
per run, raw data private, not reproducible from the public tree. The decision may still be right. This
repository cannot show it.

ADR-3 and ADR-2 also cite throughput and usage figures from the author's own operation. Those are not
checkable from the public tree either, and this page does not repeat them.

## What the cost model page is

[docs/DISPATCH-MODEL.md](../DISPATCH-MODEL.md) is a rule-of-thumb document: spawn a handful of cheap workers
in parallel, keep the orchestrator context small, log spend. It states a main rule that subagents use the cheap
tier (`docs/DISPATCH-MODEL.md:40`). The shipped example of enforcing that rule as code is
`hooks/claude/force-model-policy.mjs`, which rewrites a non-compliant model to the policy model
(`hooks/claude/force-model-policy.mjs:1-12`). Wiring it into Claude Code settings is the adopter's job
(`docs/HOOK-INSTALL.md`).

The example hook is one enforcement point, not the whole dispatch system. The hooks that track lanes are not
in this repository (see [Public and private](public-and-private.md)).

## Limits

- The ADR text is the author's reasoning at the time. It is a record, not a proof.
- No page here claims the flat fleet is cheaper than every alternative, only what the two runs above reported.
