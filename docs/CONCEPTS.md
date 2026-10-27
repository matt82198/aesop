# Key Concepts

**TL;DR**: Aesop runs cheap, fast delivery waves via a dispatch model (Haiku-first subagents + lean orchestrator), durable state (git-committed checkpoints), and security gates. This page introduces the concepts; links below go to deep-dive reference docs.

---

## The Wave Cycle

A **wave** is one complete delivery loop: rank backlog → dispatch parallel agents → verify & merge → audit & close. Each wave ships 3–8 tested features/fixes and feeds findings into the next wave's backlog.

**Wallclock**: 2–3 hours  
**Cost**: ~$0.03–$0.05 per wave  
**Outputs**: All PRs merged, all tests green

See [HOW-THE-LOOP-WORKS.md](HOW-THE-LOOP-WORKS.md) for a concrete walkthrough.

---

## Dispatch Model: Cheap Subagents + Lean Orchestrator

Aesop's superpower is **Haiku-first dispatch**: spawn 5–8 cheap Haiku subagents in parallel, not serial Opus.

### The rule: Subagents are ALWAYS cheap tier

**Rule**: Every subagent defaults to Haiku (1/3 Sonnet cost, 1/5 Opus cost). The orchestrator runs on the main thread only, stays lean, and never spawns as a subagent.

**Why**: Haiku at scale (6–8 in parallel) costs ~25% of an all-Opus fleet while maintaining quality on scoped tasks. Running many in parallel is both faster (3x wall-clock) and cheaper (1/5 the cost per token).

**Exception**: Only upgrade to Sonnet/Opus if the task genuinely exceeds Haiku capability (rare for scoped domains) AND you've decomposed as far as possible AND you have explicit approval.

### Cost model: Math

```
All-Opus fleet:         5 agents × Opus cost = $0.15–0.20/wave
Aesop (1 Opus + 5 Haiku): 1 Opus + 5 Haiku = $0.03–0.05/wave

= 4–5x cheaper + 3x faster (parallelism)
```

See [DISPATCH-MODEL.md](DISPATCH-MODEL.md) for patterns (fan-out, sequential, hierarchical) and detailed cost analysis.

---

## State & Checkpointing: Durable Across Wipes

Aesop commits **STATE.md** and **BUILDLOG.md** to git. They survive machine wipes, session restarts, and context loss.

### STATE.md: Authoritative intent

- Current phase (e.g., "Phase 2: API integration")
- Locked decisions (tech choices, data models)
- Explicit NEXT STEPS (enumerated, owner assigned)
- Key file paths and data contracts

**Purpose**: On resume, read STATE.md to answer "where were we?" and "what's next?" without re-reading logs.

### BUILDLOG.md: Append-only progress

- Timestamped work snapshots
- Status (green / yellow / blocked / pending decision)
- Next step or blocker
- **Append-only discipline**: never edit earlier entries (prevents corruption)

**Example**:
```
[2026-07-12 14:00] Phase 1 complete: scaffolding + unit tests passing
[2026-07-12 15:30] API integration: 2 endpoints done, 1 blocked on schema review
[2026-07-12 16:00] Design approved; resume API endpoint 3 implementation
```

**Log rotation**: When BUILDLOG.md exceeds ~200 lines or 20 KB, archive to `BUILDLOG-YYYY-MM.md` and keep only recent entries in the live file.

See [CHECKPOINTING.md](CHECKPOINTING.md) for recovery workflows, handoff patterns, and rotation details.

---

## Security Gates: Secret-Scan & Branch Discipline

Every push is gated by the pre-push hook, which enforces:

### Branch discipline

- **Feature branches only**: Never push directly to `main` or `master`
- **Naming**: All branches must be `feature/*` or `fix/*`
- **Consequence**: Attempt to push to main is rejected locally

### Secret scanning

- **Pre-push check**: `tools/secret_scan.py` scans staged files for detected credentials (API keys, tokens, passwords)
- **Blocks on detection**: Push fails if credentials are found
- **No bypass**: `--no-verify` skips the hook but is not recommended (use GitHub branch protection for real enforcement)

**Pair with GitHub branch protection**:
```
Settings > Branches > main
  ✓ Require pull request reviews
  ✓ Require status checks to pass
  ✓ Dismiss stale PR approvals
  ✓ Restrict pushes to (Admins only)
```

See [HOOK-INSTALL.md](archive/HOOK-INSTALL.md) for setup and customization.

---

## Governance: Single-Writer Files & Heartbeat Protocol

Keep the system coherent: one writer per control file, one instance per standing loop, heartbeat protocol to detect stalls.

### Single-writer enforcement

Some files must have exactly one writer:

- **MEMORY.md** — memory keeper only
- **STATE.md** — active orchestrator only
- **BUILDLOG.md** — append-only (anyone appends, no one edits earlier entries)
- **Other loops** → append requests to an inbox file, never edit control files directly

**Why**: Prevents race conditions, data corruption, and conflicting writes.

### Heartbeat protocol

Standing loops (watchdog, monitor, memory keeper) must not run in duplicate. Before starting, each loop:

1. Checks `~/.claude/loops/<loop-name>.heartbeat`
2. If exists and recent (<5 min old), skips (another instance running)
3. If missing or stale, creates heartbeat, runs loop, deletes heartbeat on exit

**Result**: Idempotent, collision-free loop execution.

See [GOVERNANCE.md](GOVERNANCE.md) for single-instance loops, inbox patterns, and AUTO/PROPOSE action tiers.

---

## Reliability Core: Inputs Always Produce Outputs

**Rule**: Every input (event, request, cycle) must produce output (brief/log/heartbeat/FAILED). The orchestrator never goes idle — it dispatches next work, extends roadmap, gathers signals, and always offers the user an idea for what to do next.

### No silent hangs

- **Daemons emit heartbeats** every cycle (even on error)
- **Logs are append-only** with timestamps
- **Hung agents**: Watchdog detects stalls >200s and respawns (max 3 retries)
- **Orchestrator briefs user** while delegating to agents (never idle)

### The pride bar

Never ship known-broken. **Done** means:

- Verified end-to-end (not just tests pass)
- Briefs cross-checked against reality
- Test artifacts cleaned
- Loose ends closed or explicitly logged
- Nothing known-broken shipped silently

If you'd hesitate to hand it over under your own name, keep going.

See [CARDINAL-RULES.md](CARDINAL-RULES.md) for the full 10 foundational principles.

---

## Observability & Cost Tracking

Every agent run is logged, every cost is tracked, every security event is triaged.

### What's logged

- **Agent transcripts**: Each agent's reasoning and output
- **Fleet heartbeats**: Watchdog checks every 10–150s
- **Cost ledger**: Token spend per model, per agent, per wave
- **Security events**: Secret-scan detections, branch violations

### Dashboard access

Open http://localhost:8770 (after `python ui/serve.py`):

- **Overview**: Fleet agents, recent events, security alerts
- **Work**: Task kanban (proposed → ranked → in-progress → done)
- **Activity**: Agent timeline, main-thread reasoning
- **Cost**: Token spend breakdown, per-day bar chart

### Audit trail

Everything lives in git or durable checkpoints:

- **Commits**: Agent work lives in feature branches, merged PRs
- **STATE.md / BUILDLOG.md**: Phase progress and decisions
- **Logs**: `state/FLEET-BACKUP.log`, monitor outputs, cost ledger

---

## Reliability Guarantees

### Orchestrator isolation

The orchestrator reads only:

- **CLAUDE.md** — Cardinal rules (prompt-cached, warm across turns)
- **STATE.md** — Current phase and NEXT STEPS
- **BUILDLOG.md** — Recent progress
- **MEMORY.md** — Team facts and learnings
- **Short git one-liners** — Recent commits, branch status

**Why**: Lean context (no re-reading full codebase) = cheaper decisions, faster wall-clock, warm prompt cache.

See [CARDINAL-RULES.md](CARDINAL-RULES.md#4-orchestrator-isolation-lean-context) for details.

### Durable state

STATE.md + BUILDLOG.md survive:

- Machine wipes
- Session timeouts
- Context loss
- Network interruptions

On resume, read them and sync from git. Zero data loss.

See [RELIABILITY.md](RELIABILITY.md) for the full reliability core and failure scenarios.

---

## Reference Docs

**For adopters getting started**:
- [INSTALL.md](INSTALL.md) — Installation & setup
- [CONFIGURE.md](CONFIGURE.md) — Configuration (aesop.config.json)
- [FIRST-WAVE.md](FIRST-WAVE.md) — Running your first wave cycle

**For operational reference** (deep dives):
- [HOW-THE-LOOP-WORKS.md](HOW-THE-LOOP-WORKS.md) — Concrete walkthrough of one wave
- [DISPATCH-MODEL.md](DISPATCH-MODEL.md) — Cost analysis, dispatch patterns
- [CHECKPOINTING.md](CHECKPOINTING.md) — STATE.md/BUILDLOG.md lifecycle, recovery
- [CARDINAL-RULES.md](CARDINAL-RULES.md) — 10 foundational principles (subagent discipline, TDD, reliability, orchestrator isolation, etc.)
- [GOVERNANCE.md](GOVERNANCE.md) — Single-writer files, heartbeat protocol, AUTO/PROPOSE tiers
- [RELIABILITY.md](RELIABILITY.md) — Reliability core, inputs-always-outputs, pride bar
- [HOOK-INSTALL.md](archive/HOOK-INSTALL.md) — Pre-push hook setup and customization

**For specific tasks**:
- [BEHAVIORAL-PR-REVIEW.md](BEHAVIORAL-PR-REVIEW.md) — Checklist for PRs that modify rules
- [FORENSICS.md](FORENSICS.md) — Reconstruct agent failures (git-bisectable debugging)
- [RESTORE.md](RESTORE.md) — Reconstitute Aesop on a new machine
- [PUBLISHING.md](PUBLISHING.md) — Release to npm with OIDC trusted publishing

---

## Next Steps

1. **Set up your environment**: [INSTALL.md](INSTALL.md)
2. **Configure Aesop**: [CONFIGURE.md](CONFIGURE.md)
3. **Run your first wave**: [FIRST-WAVE.md](FIRST-WAVE.md)
4. **Deep dive into concepts**: Use the reference docs above as needed

Happy orchestrating!
