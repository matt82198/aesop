# How the Wave Loop Works (The Fast, Cheap Way)

**TL;DR**: Aesop's `/buildsystem` runs one cycle per wave: rank backlog → fan out parallel Haiku agents → watchdog catches hangs → verify + merge → close with audits. The result: **lower cost (Haiku is the lowest-priced tier) and faster wall-clock from running agents in parallel** through parallelism and cheap subagents, while the orchestrator stays busy planning next work.

---

## The Wave Cycle at a Glance

A **wave** is one complete delivery loop:

```
1. Rank Backlog (~10 min)
   ↓
2. Fan Out (spawn 5–8 parallel Haiku agents, ~80 min)
   ↓
3. Watchdog (catch stalls, respawn as needed)
   ↓
4. Verify + Merge (orchestrator reviews outputs, merges PRs, ~30 min)
   ↓
5. Close: Audit + Ideation + Fleet Ops (~20 min)
   ↓
6. NEXT WAVE: feed the backlog with findings
```

**Wallclock time per wave**: 2–3 hours. **Token spend**: lower than an all-Opus wave. **What ships**: 5–8 small, tested features/fixes.

---

## 1. Rank Backlog (10 min)

Before spawning agents, you decide what to build. Use these lenses:

- **Priority**: P1 blockers first, P2 quality/UX, P3 tech debt
- **Sizing**: fit each item to a Haiku agent (scoped <5 min reasoning)
- **Ownership**: assign one agent type per item (backend-dev for APIs, frontend-dev for UI, test-bot for test fixes)

**Example backlog after ranking**:
```
P1: Fix auth timeout (backend-dev)
P1: Add verify-dash UTF-8 check (frontend-dev)
P2: Refactor config loader (backend-dev)
P3: Simplify hook test suite (test-bot)
P3: Document wave cycle (docs-agent)
```

**Productivity win**: Ranked backlog prevents "what should I build next?" tax. Agents don't guess — they execute known work.

---

## 2. Fan Out (80 min, all in parallel)

The orchestrator spawns 5–8 Haiku agents **in one command**, one per backlog item. Each agent:

- Gets a **scoped brief** (what to build, acceptance criteria)
- **Works in its own worktree** (no conflicts, safe parallelism)
- **Commits + pushes** when done
- **Opens a PR** with a summary

Example spawn:
```
→ Haiku-1 (auth): "fix timeout, add tests, commit & PR"
→ Haiku-2 (UI): "add UTF-8 check to /submit, update test"
→ Haiku-3 (refactor): "simplify config, preserve API"
→ Haiku-4 (tests): "prune old hooks, keep coverage >90%"
→ Haiku-5 (docs): "write HOW-THE-LOOP-WORKS.md"
```

**Productivity win**: the agents run concurrently instead of one at a time, and each Haiku costs less than Sonnet or Opus — so the fleet is both faster in wall-clock and far cheaper than one expensive agent working serially.

---

## 3. Watchdog (Continuous, 80 min)

While agents work, a background watchdog:

- **Polls every 10s**: checks agent heartbeats and PR status
- **Respawns hung agents**: if >200s idle, TaskStop + relaunch (max 3 auto-retries)
- **Escalates hangs**: after 3 retries, mark BLOCKED and surface to you

**No human waiting**. The watchdog runs autonomously.

---

## 4. Verify + Merge (30 min)

Once all 5 agents finish (or hit 3-retry cap), the orchestrator:

1. **Reviews each PR** (code review, test coverage, no known-broken state)
2. **Approves + merges** (or routes back to agent for fixes)
3. **Runs integration tests** (do the merged features work together?)
4. **Updates main** (all work lands together, not one-at-a-time)

**Example merge sequence**:
```
✓ Haiku-1 PR #95: auth timeout fix (APPROVED)
✓ Haiku-2 PR #96: UTF-8 check (APPROVED)
✓ Haiku-3 PR #97: config refactor (APPROVED)
✓ Haiku-4 PR #98: test prune (APPROVED)
✓ Haiku-5 PR #99: docs (APPROVED)
→ Merge all 5 → run integration tests → green ✓
```

**Productivity win**: Batch PR reviews (all at once) + single merge transaction = faster feedback loops, fewer context-switches.

---

## 5. Close (20 min): Audit + Ideation + Fleet Ops

Before the next wave, close this one:

- **Audit**: Did all 5 agents follow rules? (branch discipline, secret-scan gate, test coverage)
- **Ideation**: What went well? What bottlenecked? Any tech-debt findings to backlog?
- **Fleet ops**: Rotate logs (BUILDLOG.md archives), update MEMORY.md with learnings, clean up worktrees

**Example findings**:
```
✓ 5/5 agents followed rules
✓ All PRs had tests
→ Config refactor revealed opportunity: auto-gen schemas (P3, backlog for wave 12)
→ UTF-8 check is cheap; suggest expanding to other endpoints (P2)
→ One agent took 95 min (slightly over 80); consider smaller tasks next wave
```

**Productivity win**: Intentional retrospective prevents repeating mistakes. Backlog feeds into the next wave's ranking, so you build on learnings, not starting fresh each time.

---

## Why It's Fast & Cheap

| Metric | Aesop Wave | All-Opus Equivalent |
|--------|-----------|-------------------|
| **Agents** | 5 Haiku + 1 Opus orchestrator | 6 Opus |
| **Time** | ~2–3 hours (parallel) | ~12+ hours (serial) |
| **Cost** | Lower | Higher |
| **Test coverage** | Required per agent | Often skipped in time-crunch |
| **Outputs** | All PRs merged, all tests green | Might be 1–2 half-finished features |

**Key levers**:
1. **Parallelism**: agents work concurrently instead of one-at-a-time (measured example: wave-10's 5-agent fleet finished in ~293s of wall-clock vs ~897s if run serially)
2. **Cheap subagents**: Haiku is cheaper than Sonnet and Opus on both input and output; running many in parallel keeps spend low
3. **Orchestrator stays lean**: reads only STATE.md + BUILDLOG.md + git one-liners (no re-reading entire codebase)
4. **No idle time**: while agents work, orchestrator plans next phase (never blocking on agents)

---

## When the Loop Stalls (Retry Cap & Escalation)

If an agent hangs 3 times on the same task:

1. **Auto-retry 1–3**: Watchdog relaunches agent (same prompt, no changes)
2. **On 4th hang**: Mark BLOCKED in BUILDLOG.md, surface to you
3. **You decide**: Break the task smaller, escalate to Sonnet, or park it and move on

**Why a cap?** Infinite retries waste tokens and hide real problems (task too big, external dependency broken, etc.). 3 retries = 6 min of agent time; if still stuck, it's a human decision.

---

## About /buildsystem and /power Skills

**Important**: The `/buildsystem` and `/power` skills are **orchestrator-brain skills** installed to `~/.claude/skills/`, not files inside the aesop template. They are invoked via Claude Code as slash commands.

- **Location**: `~/.claude/skills/buildsystem/` and `~/.claude/skills/power/` on your workstation.
- **Template copy**: The aesop repo contains skill definitions in `skills/` for reference and setup (copy these to `~/.claude/skills/` on first use).
- **Invocation**: In Claude Code, type `/power` to prime your orchestrator brain, or `/buildsystem` to start a wave cycle.

See [skills/power/SKILL.md](../skills/power/SKILL.md) for detailed setup instructions.

## Next Steps

1. Run `/buildsystem` to start a wave (orchestrator handles the flow)
2. Monitor via the TUI dashboard (`bash dash/watchdog-gui.sh`)
3. After merge, review findings to feed the next backlog
4. Rinse, repeat

For deep dives on cost/dispatch patterns, see [DISPATCH-MODEL.md](DISPATCH-MODEL.md). For reliability guarantees, see [RELIABILITY.md](RELIABILITY.md).
