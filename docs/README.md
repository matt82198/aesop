# Aesop Documentation

Aesop is an autonomous developer system that crawls into any repository and orchestrates intelligent work. It ranks tasks, dispatches parallel Haiku agents, verifies merges, audits the work, and feeds the next iteration. **State persists across multiple instances via a durable SQLite event log**, so your whole team uses one coordinated system.

---

## Adopter Journey (Start Here)

If you're new to Aesop, follow this 4-stage path:

### Stage 1: Install
**[INSTALL.md](INSTALL.md)** — Prerequisites, `npx` scaffold, what gets created

- System requirements (Claude Code CLI, Git, Bash, Node.js, Python)
- Quick-start: `npx @matt82198/aesop my-fleet`
- Manual setup for development (git clone)
- Pre-push hook installation

### Stage 2: Configure
**[CONFIGURE.md](CONFIGURE.md)** — aesop.config.json, repos, ports, brain root

- Field reference (`aesopRoot`, `braindRoot`, `repos`, `dashboardPort`, `dashboardOrigin`)
- Example configurations (single repo, microservices, Windows paths)
- Security notes (git-ignore config, no secrets in config)
- Environment variables

### Stage 3: First Wave
**[FIRST-WAVE.md](FIRST-WAVE.md)** — Run `/power` then `/buildsystem` end-to-end

- What to expect from each phase (rank → dispatch → verify → close)
- Monitoring (dashboard, TUI, logs)
- When agents hang (watchdog protocol, retry cap)
- Common Q&A (wave duration, cost, cancellation, failure handling)

### Stage 4: Concepts
**[CONCEPTS.md](CONCEPTS.md)** — Dispatch model, cost, state, security, governance

- The wave cycle at a glance
- Dispatch model (Haiku-first subagents, cost model, patterns)
- State & checkpointing (STATE.md, BUILDLOG.md, durable recovery)
- Security gates (secret-scan, branch discipline)
- Governance (single-writer files, heartbeat protocol)
- Reliability core (inputs always produce outputs, pride bar)
- Links to all deep-dive reference docs

---

## System Architecture

**[ARCHITECTURE.md](ARCHITECTURE.md)** — Visual flow diagram + component overview

- Mermaid diagram: orchestrator → parallel fleet → merge train → checkpoint → audit → next backlog
- Component breakdown (watchdog, monitor, dashboard, durable state)
- Cost model and scaling characteristics
- Security model (pre-push hook + GitHub branch protection)

---

## Core Concepts: /power and /buildsystem

### /power — System Initialization
The `/power` skill initializes Aesop into your repository. It:
- Loads your orchestrator brain (cardinal rules, domain map, team memory, system state)
- Verifies the system is healthy (filesystem, git, API keys, watchdog)
- Outputs a health brief and next-step recommendations

Run `/power` at the start of each Claude Code session. It's idempotent—safe to run multiple times. See [skills/power/SKILL.md](../skills/power/SKILL.md) for details.

### /buildsystem — One Complete Wave Cycle
The `/buildsystem` skill runs **one complete iteration of the autonomous delivery loop**. It:
1. **Ranks** the backlog (priority, dependencies, team affinity)
2. **Dispatches** parallel Haiku agents on file-disjoint domains (tests, build, docs, UI, review, etc.)
3. **Verifies** each merge (CI, security scans, audit spot-checks)
4. **Checkpoints** (STATE.md + BUILDLOG.md committed to git, survive wipes)
5. **Audits** fleet health and feeds next backlog (monitor signals, signal collectors, findings)

This is the repeatable loop that runs your delivery cycle indefinitely, with each wave learning from the prior audit. You can run `/buildsystem` once per wave (typically 30 min–2 hours depending on backlog size). See [HOW-THE-LOOP-WORKS.md](HOW-THE-LOOP-WORKS.md) for a concrete walkthrough.

### Team State & Multi-Instance Design
**Current Status (0.4.0)**: Single-instance proven, with swappable worker + orchestrator model backends (Claude, Codex, OpenAI-compatible). See [MICROKERNEL.md](MICROKERNEL.md) for the two-seat architecture + a 60-second model-swap quickstart. State is durably checkpointed in git (STATE.md, BUILDLOG.md, tracker.json exports).

**In Design**: Multi-instance coordination via the state_store substrate. The event-sourced SQLite layer is production-ready but currently opt-in. A future release will enable multiple Aesop instances (e.g., per-team subgroups or geographic regions) to coordinate around a single source of truth—a Postgres-backed event log, with git as a diffable export. See [TEAM-STATE.md](TEAM-STATE.md) (design in progress) for the vision and current architecture decisions.

---

## Deep-Dive Reference Docs

Once you've completed the adopter journey, use these for operational reference:

### Wave Cycle & Orchestration
- **[HOW-THE-LOOP-WORKS.md](HOW-THE-LOOP-WORKS.md)** — Concrete walkthrough of one complete `/buildsystem` wave cycle (rank → fan-out → verify → merge → close)
- **[MICROKERNEL.md](MICROKERNEL.md)** — The two swappable seats (worker + orchestrator): what's invariant across a model swap, what's proven vs. bounded, and a 60-second quickstart

### Design Philosophy
- **[WHY-CRASH-ONLY.md](WHY-CRASH-ONLY.md)** — Crash-only vs. distributed consensus for agent fleets: why crash-only fits LLM workers, measured recovery lift (67.8% → 77.2%), MTTR 0.0s–0.5s across fault classes, when it works and when it breaks

### Dispatch & Cost
- **[DISPATCH-MODEL.md](DISPATCH-MODEL.md)** — Haiku-first subagent dispatch, cost analysis, patterns (fan-out, sequential, hierarchical)

### State Management & Recovery
- **[CHECKPOINTING.md](CHECKPOINTING.md)** — STATE.md and BUILDLOG.md lifecycle, recovery on resume, log rotation patterns

### Foundational Principles
- **[CARDINAL-RULES.md](CARDINAL-RULES.md)** — 10 foundational principles (dispatch model, cost, subagent discipline, TDD, reliability, orchestrator isolation, orchestrator focus, durable handoff, control files, security & version control)

### Operational Governance
- **[GOVERNANCE.md](GOVERNANCE.md)** — Single-instance loops, single-writer files, heartbeat protocol, inbox pattern, AUTO/PROPOSE action tiers

### Reliability Guarantees
- **[RELIABILITY.md](RELIABILITY.md)** — Reliability core (inputs always produce outputs, no silent waits, pride bar for completion), failure scenarios, recovery patterns

### Security & Hooks
- **[HOOK-INSTALL.md](HOOK-INSTALL.md)** — Install and customize `hooks/pre-push-policy.sh` (branch discipline, secret-scan enforcement at push time)

### Team Onboarding
- **[MEMORY-TEMPLATE.md](MEMORY-TEMPLATE.md)** — Canonical format for team memory and facts (to be stored in `~/.claude/MEMORY.md`)

### Behavioral Review Checklist
- **[BEHAVIORAL-PR-REVIEW.md](BEHAVIORAL-PR-REVIEW.md)** — Checklist for reviewing PRs that modify rules, policies, or orchestration behavior

### Operational Guides
- **[FORENSICS.md](FORENSICS.md)** — Reconstruct agent failures using `tools/agent-forensics.sh`; make agent behavior git-debuggable
- **[RESTORE.md](RESTORE.md)** — Reconstitute Aesop & fleet on a new machine from git + watchdog backups
- **[PUBLISHING.md](PUBLISHING.md)** — Release Aesop to npm using GitHub Actions with OIDC trusted publishing
- **[av-resilience.md](av-resilience.md)** — Antivirus and behavioral-engine resilience patterns for reliable agent execution

### Lessons & Case Studies
- **[autonomous-swe.md](autonomous-swe.md)** — What "autonomous SWE" means here (a fleet running the wave loop under a human who owns the outward gates): the committed evidence behind each claim (held-out benchmark, verified audit, proven kill-switch, reproducible package), the 0.1.0-rc.1 baseline, 0.4.0 evolution (multi-model support), and the limits the project owns
- **[case-study-portfolio.md](case-study-portfolio.md)** — How Aesop built its own portfolio site; full audit trail and cost breakdown
- **[SCRIPTS-POLICY.md](SCRIPTS-POLICY.md)** — Local-only execution, shared script library (`~/scripts`), task-local vs. reusable heuristics

---

## Quick Index by Use Case

**I'm setting up Aesop for the first time**
→ [INSTALL.md](INSTALL.md) → [CONFIGURE.md](CONFIGURE.md) → [FIRST-WAVE.md](FIRST-WAVE.md)

**I want to understand the cost model**
→ [DISPATCH-MODEL.md](DISPATCH-MODEL.md) or [HOW-THE-LOOP-WORKS.md](HOW-THE-LOOP-WORKS.md#why-its-fast--cheap)

**I want to know what's actually proven vs. claimed**
→ [autonomous-swe.md](autonomous-swe.md)

**I need to understand how state survives a crash**
→ [CHECKPOINTING.md](CHECKPOINTING.md)

**I want to understand multi-instance coordination**
→ [TEAM-STATE.md](TEAM-STATE.md)

**I want to swap the worker or orchestrator model (Ollama, OpenRouter, OpenAI...)**
→ [MICROKERNEL.md](MICROKERNEL.md)

**I'm reviewing a PR that changes orchestration behavior**
→ [BEHAVIORAL-PR-REVIEW.md](BEHAVIORAL-PR-REVIEW.md)

**An agent failed and I need to debug it**
→ [FORENSICS.md](FORENSICS.md)

**I need to set up Aesop on a new machine after a wipe**
→ [RESTORE.md](RESTORE.md)

**I want to release a new version of Aesop**
→ [PUBLISHING.md](PUBLISHING.md)

**I'm experiencing reliability issues**
→ [RELIABILITY.md](RELIABILITY.md) or [GOVERNANCE.md](GOVERNANCE.md)

---

## Architecture Diagram (Quick Reference)

```
Ranked Backlog
     ↓
Orchestrator (main thread)
     ↓
Parallel Haiku Fleet (worktrees)
  [test] [build] [docs] [ui] ...
     ↓
Watchdog (heartbeat + respawn)
     ↓
Integration Merge Train
     ↓
Checkpoint (STATE.md + BUILDLOG.md)
     ↓
Closing Audit (findings)
     ↓
Monitor + Dashboard (signals)
     ↓
Next Backlog (feedback loop)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full diagram and component breakdown.

---

## Quick Answers

**Q: How do I start a wave?**
A: Type `/power` to prime your orchestrator brain, then `/buildsystem` to start a wave cycle. See [FIRST-WAVE.md](FIRST-WAVE.md).

**Q: How much does a wave cost?**
A: ~$0.03–$0.05 USD (1 Opus orchestrator + 5–8 Haiku agents). See [DISPATCH-MODEL.md](DISPATCH-MODEL.md).

**Q: What if an agent hangs?**
A: The watchdog detects it (>200s idle), respawns automatically (up to 3 retries), then surfaces to you if still stuck. See [CARDINAL-RULES.md](CARDINAL-RULES.md).

**Q: How do I add my repos?**
A: Edit `aesop.config.json` and add paths to `repos` array. See [CONFIGURE.md](CONFIGURE.md).

**Q: Do agents work in the primary tree?**
A: No. Each agent works in its own sibling worktree (via `git worktree add`). See [ARCHITECTURE.md](ARCHITECTURE.md).

**Q: How do I set up secret-scan?**
A: The pre-push hook is auto-installed. Customize `tools/secret_scan.py` with your rules. See [HOOK-INSTALL.md](HOOK-INSTALL.md).

---

## Contributing

Aesop is **source-available** under the PolyForm Strict License 1.0.0, which doesn't permit modification or redistribution — so outside code patches can't be merged as contributions. **Issues, bug reports, and discussion are warmly welcome**, and are the best way to help. The repo develops itself via its own `/buildsystem` loop; code changes are made by the maintainer at their discretion, or by prior arrangement.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for details and [CARDINAL-RULES.md](CARDINAL-RULES.md) for core principles.

---

## License

**Source-available** under the [PolyForm Strict License 1.0.0](../LICENSE). Use is permitted for a permitted purpose; **modification and redistribution are not**. See [`LICENSE`](../LICENSE) for full terms.
