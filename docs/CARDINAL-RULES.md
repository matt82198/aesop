# Cardinal Rules for Aesop Orchestration

These are the foundational principles that guide all work in an Aesop-driven fleet. Violating these risks cost explosion, data loss, or orchestration breakdown.

## 1. Dispatch model & cost — Main Rule: subagents are ALWAYS cheap tier

**Rule**: Subagents are ALWAYS Haiku (the lowest-priced tier) — **the single most important cost lever**. Orchestrator (Fable/Opus) runs on main thread only, NEVER spawns as a subagent, performs final-catch review itself, and NEVER hand-writes files in normal flow.

**Why**: Haiku at scale (6–8 agents in parallel) costs less than an all-Opus fleet while maintaining quality on tiny scoped tasks. Main thread orchestrator preserves context stability and keeps prompt cache warm across turns.

**Watchdog & stalls**: The orchestrator's core job is detecting hung Haikus. Signs: agent transcript hasn't advanced for >200s, workflow phase isn't progressing, background task should have exited but hasn't. On detection, TaskStop + relaunch the hung agent. Workflows resume from cache via `resumeFromRunId`; standalone agents respawn fresh.

**Retry cap**: 1st–3rd hang = TaskStop + relaunch automatically. On 4th hang, mark BLOCKED in BUILDLOG.md and surface to the user instead of respawning. Prevents infinite loops and ensures visibility.

**Implementation**:
- When spawning a new agent, default to Haiku.
- If you need Opus-tier reasoning, consider decomposing into smaller Haiku tasks first.
- Track token spend per subagent; alert if spend deviates >20% from baseline.
- Watchdog monitors for stalls; never let a stuck Haiku silently block a pipeline.

## 2. TDD-first & parallel domains

**Rule**: Test-driven development: failing tests before implementation. Decompose work into tiny scoped domains; one Haiku subagent per domain in parallel.

**Why**: Small domains enable parallelism (cheaper, faster). TDD catches bugs early. Tests are living documentation.

**Implementation**:
- Write acceptance criteria first (in your story/ticket).
- Red: verify tests fail.
- Green: implement the minimum to pass tests.
- Refactor: simplify, improve, extend reusable libraries.
- When a task grows beyond one domain, split it and fan out to parallel Haiku agents.

## 3. Reliability core: inputs always produce outputs

**Rule**: Every input (request, event, cycle) must produce an output (brief/log/heartbeat/FAILED). **NEVER WAIT**: the orchestrator never goes idle while background work is in flight — dispatch the next unblocked work unit, extend the roadmap, stage ideas, queue facts, and always offer the user an idea for what to do next. "Waiting on X" must come with "meanwhile, doing/proposing Y."

**Why**: Hangs hide cost waste, data loss, and orchestration confusion. Observable failure is better than silent lag. Idle time is token waste.

**The pride bar**: Never ship known-broken. "An agent returned a brief" and "tests pass" are checkpoints, not completion. Done means: verified end-to-end, briefs cross-checked against reality, test artifacts cleaned, loose ends closed or explicitly logged, nothing known-broken shipped silently. If you'd hesitate to hand it over under your own name, keep going.

**Implementation**:
- Daemons emit heartbeats every cycle (even on error).
- Logs are append-only; every action logged with timestamp.
- If a subagent stalls >200s, watchdog respawns it.
- Orchestrator briefs the user with findings while delegating to subagents (never idle).
- Before marking work done, verify end-to-end and cross-check briefs against reality.

## 4. Orchestrator isolation: lean context

**Rule**: Orchestrator reads ONLY: cardinal rules, STATE.md, BUILDLOG.md, MEMORY.md, and short git one-liners. Dispatch Haiku for research.

**Why**: Large orchestrator context = token waste, slower decisions. Brief facts + git log tell the story.

**Implementation**:
- Orchestrator prompt stays <2000 tokens.
- Long-form analysis → delegated to Haiku researcher agents.
- Durable checkpoints (STATE.md, BUILDLOG.md) replace ephemeral context.
- Prompt caching on cardinal rules + memory improves throughput.

## 5. Durable handoff: STATE.md + BUILDLOG.md

**Rule**: STATE.md tracks intent/decisions/phase/NEXT STEPS. BUILDLOG.md is append-only snapshots of agent work. On resume, re-sync from disk before acting.

**Why**: Sessions end abruptly (wipes, crashes, restarts). Git-committed state survives. Re-syncing prevents duplicate work and data loss.

**Implementation**:
- Before acting, orchestrator reads STATE.md (5 min old max) and BUILDLOG.md (latest 10 entries).
- After each major step, orchestrator updates STATE.md with new phase + next steps.
- Subagents append work summaries to BUILDLOG.md (one line per completion).
- Heartbeats mark liveness; stale heartbeats trigger re-sync.

## 6. Branch discipline & continuous push

**Rule**: Feature branches only (never main/master). Continuously push green work to origin. **Never amend; create new commits.** **Never force-push** (unless explicitly approved for a specific commit).

**Why**: Main branch stays deployable. Continuous push distributes backup risk. Amending hides history; force-pushing erases commits. New commits preserve a clear audit trail.

**Implementation**:
- Create feature/your-task at start.
- Commit often (every 15–30 min of solid work).
- Push after every commit (github mirrors your work).
- Open PR when feature is ready for review.
- If you need to fix a commit, create a new commit with the fix (never amend).
- Never force-push; if you must rebase, do it on a draft branch before opening a PR.

## 7. Control files & single-writer discipline

**Rule**: Single-writer control files: MEMORY.md (keeper writes only), STATE.md (orchestrator writes only), BUILDLOG.md (append-only for everyone, never overwrite). Single-instance loops check heartbeat before starting; skip if a live one exists.

**Why**: Contention on shared state causes data loss and confusion. Single-writer enforcement prevents races. Append-only-for-everyone prevents accidental overwrites. Single-instance heartbeats prevent duplicate work (e.g., two memory keepers or monitors running simultaneously).

**Implementation**:
- Designate one writer per control file (keeper for MEMORY.md, orchestrator for STATE.md).
- BUILDLOG.md is append-only; anyone can append, no one edits earlier entries.
- Before starting a loop, check `~/.claude/loops/<loop-name>.heartbeat`; if exists and recent (<5 min old), skip and exit.
- On resume, read from disk; never trust in-memory state.
- Append-only logs never overwrite; oldest entries rotate to archives when >200 lines/20KB.

## 8. Secret-scan & version control

**Rule**: `secret_scan.py` blocks every push (exit 1 = blocked). No credentials in repos. Sensitive data → private remote (e.g., claude-vault).

**Why**: Credentials leak → account compromise. Scanning + gating prevents accidents.

**Implementation**:
- Install secret_scan.py in your aesop/tools/.
- Run before every push (watchdog daemon does this).
- If blocked, fix the issue and re-push (never --no-verify).
- Log blocked attempts to SECURITY-ALERTS.log; triage later.

## 9. Local execution only

**Rule**: Python runs locally only (no cloud runners). Reusable scripts live in ~/scripts (indexed in CLAUDE.md); extend existing or add genuinely reusable.

**Why**: Cloud execution = latency, cost, complexity, partial failure modes. Local = deterministic, fast, auditable.

**Implementation**:
- When you write a script, ask: "Will this be reused?" If yes, add to ~/scripts and index in CLAUDE.md. If no, keep in scratchpad.
- Never schedule Python on a cloud agent or workflow.
- Daemon scripts (watchdog, monitor, tooling) are canonical and live in aesop/tools/.

## 10. Observability & audit trails

**Rule**: Every agent run is logged. Every cost tracked. Every security event triaged.

**Why**: Observability reveals cost leaks, drift, and breaches. Unreviewed alerts rot into ignored noise.

**Implementation**:
- FLEET-LEDGER.md: one row per agent outcome (timestamp, domain, token spend, result).
- SECURITY-ALERTS.log: one row per security event (classified REAL/FP, triaged, archived).
- COST-LOG.md: periodic summaries (spend rate, drivers, regressions, optimization levers).
- Dashboard shows live status; triaged alerts move to RESOLVED-FP archives.

---

## Enforcement

These rules are **guardrails-in-code**:
- Watchdog daemon enforces secret-scan gate and branch discipline.
- Monitor auto-detects violations and stages PROPOSALS.md or escalates.
- Orchestrator reads STATE.md to verify cardinal rule compliance.
- Violations caught late are logged and triaged; never silently ignored.

## What to do if a rule seems wrong

Rules are durable, but not dogmatic. If a rule creates friction:

1. Propose a change (write it to PROPOSALS.md).
2. Explain why it's necessary.
3. Ask the user for approval.
4. Never work around a rule; instead, propose improving it.

---

**Why these rules matter**: They ensure that your orchestration fleet operates **reliably** (inputs → outputs), **cheaply** (Haiku scale), **safely** (secrets gated), and **durably** (state survives wipes). Together, they enable you to scale from 1 Opus orchestrator to dozens of parallel Haiku subagents without losing control, visibility, or cost discipline.
