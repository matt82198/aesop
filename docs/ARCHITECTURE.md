# Architecture

Aesop's architecture is designed for **low cost, high parallelism, and durable state**. This page shows the system architecture with one wave cycle diagram.

---

## System Architecture Diagram

```mermaid
graph TD
    A[Ranked Backlog]
    B["Orchestrator<br/>(Fable/Opus main thread)"]
    C["Parallel Haiku Fleet<br/>(worktree-isolated agents)"]
    D1["Haiku-1<br/>(feat branch)"]
    D2["Haiku-2<br/>(feat branch)"]
    D3["Haiku-3<br/>(feat branch)"]
    D4["..."]
    E["Watchdog<br/>(heartbeat + respawn)"]
    F["Integration Branch<br/>(merge train)"]
    G["Checkpoint<br/>(STATE.md +<br/>BUILDLOG.md)"]
    H["Closing Audit<br/>(findings)"]
    I["Next Backlog"]
    J["Monitor Daemon<br/>(signals)"]
    K["Dashboard<br/>(live status)"]

    A -->|spawns| B
    B -->|dispatches| C
    C --> D1
    C --> D2
    C --> D3
    C --> D4
    D1 -->|commit + push| F
    D2 -->|commit + push| F
    D3 -->|commit + push| F
    D4 -->|commit + push| F
    E -->|polls heartbeats| C
    E -->|respawns on stall| C
    F -->|orchestrator reviews| B
    B -->|approves + merges| G
    G -->|git-committed| H
    H -->|findings → backlog| I
    J -->|health checks| K
    J -->|feeds| H
    K -->|displays| B
```

---

## Component Overview

### 1. Ranked Backlog

The input to a wave. Backlog items are:

- **Ranked** by priority (P1: blockers, P2: quality, P3: debt)
- **Sized** for Haiku (3–10 min per agent)
- **Scoped** (one agent per item, no dependencies)

**Owned by**: You (at wave start)

---

### 2. Orchestrator (Main Thread)

**Fable or Opus** orchestrator on the main thread.

**Responsibilities**:
- Reads STATE.md, BUILDLOG.md, MEMORY.md (durable state)
- Ranks backlog into actionable items
- Spawns Haiku agents in parallel
- Monitors watchdog signals
- Reviews PRs and approves merges
- Conducts closing audit

**Why main thread?** Preserves context, warms prompt cache, avoids orchestrator-as-subagent (too expensive).

---

### 3. Parallel Haiku Fleet

**5–8 Haiku agents** running in parallel, each in its own worktree.

**Each agent**:
- Gets a **scoped brief** (what to build, acceptance criteria)
- Works in a **sibling worktree** (no conflicts with primary tree or other agents)
- Commits + pushes to a **feature branch**
- Opens a **PR** when done

**Cost benefit**: Haiku is ~1/3 Sonnet, 1/5 Opus. Running 5 in parallel = faster + much cheaper than serial Opus.

**Worktree isolation**: Each agent uses `git worktree add ../wt-<agent-name>` to ensure:
- No conflicts (agents don't interfere)
- Safe parallelism (each has its own git checkout)
- Easy cleanup (delete worktree on completion)

---

### 4. Watchdog (Background Daemon)

**Purpose**: Detect hung agents and respawn them automatically.

**Polling**:
- Every 10s: check agent heartbeats
- Every 150s: run full backup (secret-scan, drift check, state backup)

**On stall** (>200s idle):
1. Ping agent (is it responsive?)
2. If no response: TaskStop + relaunch (auto-retry 1–3)
3. After 3 retries: mark BLOCKED and surface to user

**Why**: Prevents silent hangs, keeps fleet healthy, gives humans visibility into failures.

---

### 5. Integration Branch (Merge Train)

All feature branches are merged into a **integration branch** (e.g., `integration/wave-23`) in sequence.

**Orchestrator**:
1. Reviews each PR (code review, test coverage)
2. Approves if green
3. Merges to integration branch
4. Runs integration tests on the combined result

**Why ordered merge?** If one PR causes an integration failure, you know exactly which one. Easier to debug than merging all-at-once.

---

### 6. Checkpoint (STATE.md + BUILDLOG.md)

After all merges succeed, **commit STATE.md and BUILDLOG.md to git**.

**STATE.md** (hand-maintained):
- Current phase (e.g., "Phase 4: Closing audit")
- Locked decisions
- Explicit NEXT STEPS

**BUILDLOG.md** (append-only):
- Timestamped work snapshots
- Status (green / blocked / pending)
- Recent blocker or next action

**Why git-committed?** Survives machine wipes. On resume, read them and sync from disk (no data loss).

---

### 7. Closing Audit

Before the next wave, **audit** this wave:

**Checks**:
- Did all agents follow rules? (branch discipline, secret-scan gate, test coverage)
- How many retries did watchdog need?
- What bottlenecked? (long tasks, slow external APIs, etc.)

**Findings** → feed into next wave's backlog:
- "Config loader took 35 min; consider pre-writing scaffold" → P2 for wave 24
- "Dashboard tests are thin; expand coverage" → P2 for wave 24

---

### 8. Monitor Daemon (Side Observer)

**Background process** that collects health signals:

- Agent heartbeats and task status
- Fleet cost tracking
- Security events (secret-scan blocks, branch violations)
- Custom health checks (extensible via `monitor/collect-signals.mjs`)

**Outputs**:
- `BRIEF.md` — Snapshot of fleet health
- `SIGNALS.json` — Structured data for dashboard
- `.monitor-heartbeat` — Liveness marker

**Why separate?** Decouples observability from orchestration. Signals feed into audit, not directly into agent dispatch.

---

### 9. Dashboard (Live Status)

**Web UI** at http://localhost:8770 (runs `python ui/serve.py`).

**Feeds from**:
- Monitor daemon signals
- Orchestrator state (STATE.md, BUILDLOG.md)
- Cost ledger (token spend, per-model breakdown)

**Views**:
- **Overview** — Fleet agents, recent events, security alerts
- **Work** (#/work) — Task kanban (proposed → ranked → in-progress → done)
- **Activity** (#/activity) — Agent timeline, main-thread reasoning
- **Cost** (#/cost) — Per-model spend, per-day chart, verdict scorecard

**Why observability matters?** You can see exactly what's happening without reading logs. Faster decisions, faster recovery.

---

### 10. Next Backlog (Feedback Loop)

Findings from closing audit feed into the **next wave's backlog**:

- Learnings from this wave
- Tech-debt opportunities discovered during implementation
- Performance/UX improvements suggested by agents
- Scaling improvements (e.g., "cache this lookup")

**Result**: Each wave improves on the last. The system learns continuously.

---

## Durable State Model

```
STATE.md ──┐
BUILDLOG.md├──→ git commit ──→ git push ──→ Survives machine wipe
MEMORY.md ─┘
```

On resume:
1. Read STATE.md → understand intent, current phase, NEXT STEPS
2. Skim BUILDLOG.md → review recent progress
3. Run git log → verify actual file state
4. Sync → if STATE.md is stale, update before proceeding

**Zero data loss**: Durable state on disk (git) is source of truth.

---

## Cost Model

| Component | Cost | Notes |
|-----------|------|-------|
| Orchestrator (Opus) | ~$0.02 per wave | Main thread, lean context, 5–10 min reasoning |
| Haiku fleet (5 agents) | ~$0.01–0.02 per wave | 1/5 cost per token vs Opus |
| **Total** | **~$0.03–0.05 per wave** | 4–5x cheaper than all-Opus fleet |

**Key lever**: Haiku-first dispatch. Every subagent defaults to cheap tier (Haiku). Orchestrator stays lean. Result: maximum savings at scale.

See [DISPATCH-MODEL.md](DISPATCH-MODEL.md) for detailed cost analysis and patterns.

---

## Security Model

### Pre-push hook (local enforcement)

Every push is gated:

1. **Branch check**: Reject pushes to `main` / `master` (must use `feature/*`)
2. **Secret-scan**: Scan staged files for detected credentials (API keys, tokens, passwords)

**Script**: `hooks/pre-push-policy.sh`

### GitHub branch protection (real enforcement)

For production, pair with GitHub:

```
Settings > Branches > main
  ✓ Require pull request reviews
  ✓ Require status checks to pass
  ✓ Dismiss stale PR approvals
  ✓ Restrict pushes to (Admins only)
```

See [HOOK-INSTALL.md](archive/HOOK-INSTALL.md) for setup.

---

## Threat model: prompt injection

**Threat.** Agents read text they do not control: issue and PR bodies, `gh` output, web pages fetched by WebFetch or WebSearch, subagent reports, tool output, and repository files. Any of that text can contain instructions. The orchestrator also composes subagent dispatch prompts from upstream text, and `hooks/claude/force-model-policy.mjs` states that "prompts are untrusted text" (line 14). The agents run as the operator's local user with the same `gh` and git credentials that the scripts in `tools/` use (for example `tools/auto_merge.py`, the merge path named in `LANE-CONTRACT.md` section 6).

The controls below are the only ones this repo implements. Each is cited to the file that implements it. Anything not listed here is not a control.

### Controls that exist

1. **Lane rules (text, not enforcement).** `LANE-CONTRACT.md` tells dispatched agents not to merge, not to use `--admin`, `--no-verify` or `--force`, not to self-grant `--@gate-allow`, and not to hand-edit generated files (sections 3, 4 and 6). It is instruction text to the model. No file under `hooks/` injects it into a dispatch.
2. **Dispatch linter.** `tools/dispatch_lint.py` scans committed Python, JavaScript and Markdown for forbidden dispatch patterns: manual `gh pr merge` without `--auto`, `--admin`, `--no-verify`, `--force`, `git stash`, and credential hunting. It is a regex scan of files in the tree. It runs as a pre-commit hook (`hooks/pre-commit-dispatch-lint.sh`) and is listed as a gate in `LANE-CONTRACT.md` section 5. It does not see prompts an orchestrator composes at runtime.
3. **Claude Code PreToolUse hooks** (`hooks/claude/`, wired per `docs/archive/HOOK-INSTALL.md`):
   - `force-model-policy.mjs` (matcher `Agent|Task`) rewrites a subagent dispatch whose model is not the policy model. It constrains model choice only. It does not inspect the prompt for instructions.
   - `no-heredoc-file-authoring.mjs` denies Bash heredocs that author file content. Its stated reason is reliability, not security.
   - `no-scrollback-as-record.mjs` denies Bash, Read, Grep and Glob access to session transcripts. Its stated reason is that transcripts are not the system of record.
   - The two deny hooks match command or path text with regular expressions in their `verdictFor` function. `force-model-policy.mjs` checks the prompt text for its escape marker. All three honour a bracketed escape token named by an `ESCAPE_HATCH` constant, and each logs its use.
4. **Dispatch suppression marker.** A `# dispatch-ok` comment suppresses a `tools/dispatch_lint.py` finding on that line (`tools/dispatch_lint.py`, suppression rule in its docstring).
5. **Secret scan.** `tools/secret_scan.py` checks the pushed range in `hooks/pre-push-policy.sh` (`check_secret_scan`) and runs on the checked-out tree in CI (`.github/workflows/ci.yml`, lines 90 and 414). It matches known credential patterns only.
6. **Push policy.** `hooks/pre-push-policy.sh` does the following:
   - `check_branch_policy` blocks any push that targets `main` or `master` on the remote.
   - Gate pinning: gate scripts run from the trunk's `tools/` copy (`gate_base_dir`), not from the pushed branch, so a branch cannot weaken the gate that judges it.
   - `check_gate_tamper` prints a non-blocking banner when a push edits gate files.
   - `check_conflict_markers`, `check_import_resolution` and `check_symbol_survival` (`tools/pr_symbol_survival_check.py`) block pushes that carry conflict markers, unresolvable imports or a symbol dropped by a merge.

### What is not covered

- **No untrusted-text filtering.** No file in `tools/`, `hooks/`, `daemons/` or `bin/` filters, labels or sanitises text from issues, PRs, web pages or subagent reports before it enters an agent prompt.
- **Escape tokens and suppression markers are readable by injected text.** Any text an agent reads that contains the `ESCAPE_HATCH` token of a hook above, or a `# dispatch-ok` marker, bypasses that check. The bypass is logged, not blocked.
- **Regex matching.** The two deny hooks match command shapes. A paraphrased command, or a different interpreter that avoids the regex, is not matched.
- **Model policy is not tool policy.** `force-model-policy.mjs` controls the model of a subagent. It does not limit which tools the subagent may call.
- **Runtime prompts are not linted.** `tools/dispatch_lint.py` only sees committed files.
- **Local hook bypass.** The pre-push hook runs on the developer's machine and `git push --no-verify` skips it. Server-side enforcement depends on GitHub branch protection, which is set in GitHub settings and is not in this repo.
- **Secret scan scope.** `tools/secret_scan.py` does not inspect text sent through `gh` comments or web requests.

## Scaling Characteristics

### Parallelism

- 5 agents in parallel = ~3x wall-clock speedup vs serial
- 8 agents in parallel = ~4x speedup (diminishing returns beyond 10 agents due to orchestrator overhead)

### Cost

- Haiku-first dispatch = ~1/5 cost per token vs Opus
- 5 Haiku + 1 Opus orchestrator = ~25% cost of all-Opus fleet

### Reliability

- Durable state (git-committed) = zero data loss on wipe
- Watchdog (heartbeat + respawn) = auto-recovery from hung agents
- Append-only logs = auditable, no corruption

---

## Next Steps

- **[HOW-THE-LOOP-WORKS.md](HOW-THE-LOOP-WORKS.md)** — Detailed wave cycle walkthrough
- **[DISPATCH-MODEL.md](DISPATCH-MODEL.md)** — Cost analysis and dispatch patterns
- **[CHECKPOINTING.md](CHECKPOINTING.md)** — STATE.md / BUILDLOG.md lifecycle
- **[FIRST-WAVE.md](FIRST-WAVE.md)** — Run your first wave and see the architecture in action
