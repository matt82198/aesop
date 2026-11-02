# Aesop Dispatch Model — Cost & Orchestration Patterns

**TL;DR**: Spawn 5–8 cheap Haiku subagents in parallel (not serial Opus). Result: **lower cost (Haiku is the lowest-priced tier per call) plus a parallel speedup** via parallelism. Rule: subagents ALWAYS Haiku unless scoped work genuinely exceeds its capability (rare).

---

## The fable conceit

In Aesop's Fables, Aesop (the narrator) directs the characters (tortoise, hare, fox, etc.) through moral tales. Here, **Aesop (the orchestrator)** directs a fleet of **Fables (the subagents)** toward reliable task completion.

The tortoise represents deliberate, resourceful thinking; the hare, quick speed. Together (slow orchestrator + fast subagents), they're unbeatable.

## Cost model

### Baseline: all-Opus fleet

```
5 tasks × 10 min each × Opus cost (~60 tokens/min for reasoning)
= 5 × 10 × 60 = 3,000 tokens/session
```

### Aesop dispatch: Opus + 5 Haiku

```
1 orchestrator (Opus, 5 min thinking)  = 5 × 60 = 300 tokens
5 subagents (Haiku, 2 min each)        = 5 × 2 × 20 = 200 tokens
Total                                   = 500 tokens/session
= a large saving vs all-Opus (Haiku is the lowest-priced tier per call)
```

### Scaling to 10 parallel domains

```
1 orchestrator (Opus, 8 min)       = 480 tokens
10 subagents (Haiku, 3 min each)   = 600 tokens
Total                              = 1,080 tokens
= a large saving vs all-Opus (most domains run on Haiku, the lowest-priced tier per call)
```

## MAIN RULE: Subagents are ALWAYS the cheap tier

This is **the single most important cost lever** in the entire system. Every subagent spawned must default to Haiku, never Sonnet or Opus. This rule, more than any other, multiplies your savings at scale.

**Why**: Haiku is the lowest-priced tier per call, with sufficient capability for scoped domain work. Scaling from 1 subagent to 6–8 in parallel multiplies the per-domain savings — most of the work runs on Haiku, the lowest-priced tier per call. Violating this rule (spawning Sonnet/Opus subagents) erases the economic advantage of the entire dispatch model.

**The exception**: Only use Sonnet/Opus for a subagent if:
1. The task genuinely exceeds Haiku capability (rare for scoped domains), **AND**
2. You've decomposed it as far as possible and still can't fit it into Haiku, **AND**
3. You have explicit approval to do so.

Even then, use Sonnet as a supervisor only (splits work into Haiku subdomains), never Opus as a subagent.

## Dispatch patterns

### Pattern 1: Fan-out (wide parallelism)

**Use case**: 10 independent tasks with no dependencies.

```
Orchestrator
├─ Haiku-1: Test domain (write test, red → green)
├─ Haiku-2: Build domain (compile, package)
├─ Haiku-3: Docs domain (README, examples)
├─ Haiku-4: Security domain (audit, review)
└─ Haiku-5: Deployment domain (stage, verify)

Total cost: 1 Opus + 5 Haiku, lower than an all-Opus fleet of the same size (no per-session total is measured in the repo)
```

### Pattern 2: Sequential with handoff

**Use case**: Feature across 3 layers (API → DB → UI) with dependencies.

```
Orchestrator → Phase 1
├─ Haiku-1: API spec + tests
└─ (wait for Haiku-1 to complete)
    → Phase 2
    ├─ Haiku-2: DB schema (reads Haiku-1 output)
    └─ (wait)
        → Phase 3
        ├─ Haiku-3: UI (reads both outputs)
        └─ Haiku-4: Integration test
        
Orchestrator reassembles + QA
Total cost: 1 Opus orchestrator + 4 Haiku implementers — the four implementers are on the lowest-priced tier per call, not Opus
```

### Pattern 3: Hierarchical (supervisor + workers)

**Use case**: Complex codebase review (split into modules, each reviewed in parallel).

```
Orchestrator
├─ Review-Supervisor (Sonnet, splits codebase into 5 modules)
│  ├─ Haiku-1: auth module
│  ├─ Haiku-2: api module
│  ├─ Haiku-3: db module
│  ├─ Haiku-4: ui module
│  └─ Haiku-5: config module
└─ (Sonnet synthesizes reviews into final report)

Cost: 1 Opus + 1 Sonnet + 5 Haiku, which costs less than running every domain on Opus (no per-fleet total is measured in the repo)
```

## Decision flowchart

When you have a new task, ask:

```
Is this task scoped to <5 min reasoning?
├─ YES → Use Haiku subagent (cost: lowest-priced tier per call)
├─ NO → Can I decompose it into smaller tasks?
│   ├─ YES → Fan-out to multiple Haiku (cost: several Haiku calls, no measured total)
│   └─ NO → Use Opus (cost: higher-priced tier per call)
└─ Does this need final review/synthesis?
    ├─ YES → Opus orchestrator does it (cost: one higher-priced tier call)
    └─ NO → Done (subagent output is final)
```
## Anti-patterns

### ❌ All-Opus fleet

**Cost**: higher — Sonnet/Opus per-token rates. **Why**: throws CPU power at problems that are scoped.

**Fix**: Decompose into Haiku-sized tasks.

### ❌ Giant orchestrator context

**Cost**: token waste, slower decisions. **Why**: reads full logs, commits, code diffs.

**Fix**: Orchestrator reads STATE.md + BUILDLOG.md + git one-liners only. Delegate research to Haiku.

### ❌ Silent hangs

**Cost**: invisible waste (subagent stalls, orchestrator waits). **Why**: no heartbeat, no timeout.

**Fix**: Heartbeat every 60s min. Watchdog respawns >200s stale. Orchestrator never waits>60s (spawn next task).

### ❌ Cloud Python execution

**Cost**: latency + complexity. **Why**: cloud runners add overhead, partial failure risk.

**Fix**: All Python runs locally. Cloud agents spawn only for distributed compute (rare).

## Token spend tracking

### Log format (FLEET-LEDGER.md)

```
| timestamp           | domain   | subagent    | tokens | result      |
|--|--|--|--|--|
| 2024-01-15T10:30:00 | auth     | haiku       | 1200   | SUCCESS     |
| 2024-01-15T10:35:00 | ui       | haiku       | 950    | SUCCESS     |
| 2024-01-15T10:40:00 | test     | haiku       | 1100   | FAILED (1r) |
| 2024-01-15T10:45:00 | test     | haiku       | 1050   | SUCCESS     |
```

### Cost signals to watch

1. **Spend spike** (+20% baseline): investigate domain; may need larger model.
2. **Respawn loop** (same domain 3+ times): subagent repeatedly failing; escalate to Sonnet or orchestrator review.
3. **Serial bottleneck** (orchestrator >500 tokens): parallelism breaking down; split smaller or delegate.

## Retry cap: Automatic recovery + escalation

The orchestrator's watchdog automatically detects hung agents and relaunches them with the same scoped prompt:

- **1st–3rd hang**: TaskStop + relaunch automatically. Workflows resume from cache via `resumeFromRunId`; standalone agents respawn fresh.
- **4th hang**: Mark BLOCKED in BUILDLOG.md and surface to the user instead of respawning.

**Why the cap?** Infinite retry loops waste tokens and hide systemic problems. After 3 attempts, a persistent hang indicates either:
- Task is fundamentally unscopable (too large, too complex)
- Subagent needs a larger model (escalate to Sonnet or Opus)
- External dependency is broken (requires human intervention)

Surfacing to the user after 3 retries ensures visibility and prevents silent cost bleed.

### Optimization levers

- **Fan-out more tasks**: go from 2 Haiku to 5.
- **Shift to Sonnet**: if 3 Haiku respawns in a row, use Sonnet once for harder task.
- **Reduce orchestrator context**: read fewer files, shorter CLAUDE.md.
- **Cache cardinal rules**: use prompt caching on base CLAUDE.md (reused every session).

## Orchestrator role (the slow tortoise)

- **Reading**: STATE.md (phase + next steps), BUILDLOG.md (latest 10 entries), git one-liners.
- **Deciding**: which 3–5 Haiku to spawn, what task each gets, how to reassemble results.
- **Waiting**: while subagents run in parallel, orchestrator uses time to:
  - Brief user on progress.
  - Spot-check one Haiku output.
  - Plan next phase.
- **Synthesizing**: reads Haiku results, updates STATE.md, decides next phase or declares done.

**Golden rule**: Orchestrator thinks 5–10 min per hour of wallclock time (while agents run). Never blocks; never waits idle.

## Subagent role (the swift fables)

- **Scoped task**: <5 min pure reasoning per task.
- **Clear acceptance criteria**: from orchestrator brief.
- **Output**: append to BUILDLOG.md, return result to orchestrator.
- **Failure**: log reason, don't retry (orchestrator decides retry).
- **Speed**: finish in <2 min if possible; alert orchestrator if >5 min (break task smaller).

**Golden rule**: Subagents stay cheap and fast. If a Haiku takes >10 min, you've scoped too large.

---

**Summary**: Aesop's dispatch model trades a small amount of orchestration complexity for **lower cost** (Haiku is the lowest-priced tier per call) and **parallel speedup** (via parallelism). It works by keeping subagents small, orchestrator lean, and feedback constant.
