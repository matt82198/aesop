# skills/ — Orchestration and priming skills

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Module layout

Five orchestration skills, each invoked via CLI (e.g., `/buildsystem`, `/power`, `/dashboard`):

- **power/** — prime the filesystem brain (load CLAUDE.md rules, dispatch model, memory, state); init-prime or fast-path for already-primed repos
- **buildsystem/** — execute one complete orchestrated development wave (backlog rank → parallel Haiku fleet → merge train → checkpoint → audit)
- **dashboard/** — launch the aesop web dashboard (idempotent startup, SSE realtime updates, CSRF protection)
- **healthcheck/** — check fleet health (one colored ball: green/yellow/red based on heartbeats, alerts, orchestrator status)
- **fleet/** — snapshot fleet state as JSON (agents, heartbeats, tracker lanes, orchestrator status) — never mutates

## Buildsystem manifest & work-order contract

**Backlog format** (`state/BACKLOG.md`):
```markdown
## P1: Critical
- [ ] Task title — agent-type — time-estimate
  Example: Fix secret-scan hang — backend-dev — 5min
## P2: Features
- [ ] Add feature X — frontend-dev — 8min
## P3: Tech Debt
- [ ] Refactor module Y — backend-dev — 15min
```

Time estimates: 3–5 min (typo/config), 5–10 min (function/test), 10–15 min (module refactor), 15+ min (split smaller).

**Work-order contract** (each agent receives):
- Task: one backlog item (feature/fix/tech debt)
- Agent type: backend-dev, frontend-dev, test-bot, docs-agent, etc.
- Time budget: 3–10 min (Haiku best-effort scope)
- Scope: reproduce, change location, test command, commit message template
- Constraints: never modify CLAUDE.md/STATE.md, must pass pre-commit hook, no external calls

**Preflight guard invariant** (wave-flat-dispatch.template.mjs):
- No two backlog items may own the same file or directory
- Each item assigned to exactly one agent
- Preflight detects overlap and aborts with remediation plan before dispatch

**Phases**:
1. Preflight (1–2 min) — validate setup, check heartbeat, parse backlog, detect file overlap
2. Backlog & Dispatch (5–10 min) — rank items, assign agents, create work orders
3. Fleet Execution (30–90 min) — Haiku agents work in parallel, in isolated worktrees
4. Code Review & Integration (10–30 min) — run CI, merge train (serial), flag failures
5. Checkpoint & Audit (2–5 min) — update STATE.md, append BUILDLOG.md, commit + push

**Worktree isolation** (critical):
- Each agent runs in `aesop-wt-<wave>-<agent-id>/` (independent branch, HEAD, working dir)
- Zero cross-agent interference
- Automatic cleanup after wave closes

**Merge serialization** (critical):
- All PRs from a wave merge through serial merge train
- First agent to green CI merges, next waits for main to stabilize, re-tests, then merges

## Power skill

Two entry paths:

1. **Init-prime** (unprimed repo, no CLAUDE.md): fan-out Haiku explorers → synthesize project CLAUDE.md + domain units + STATE.md seed → commit + push
2. **Already-primed** (fast path): load global rules (`~/.claude/CLAUDE.md`), load memory (MEMORY.md), load active project STATE.md, report compact brief, optionally spin up app and monitor background loops

Prerequisites: Aesop scaffolded into repo via CLI (`npx @matt82198/aesop . --name "my-project"`).

## Healthcheck skill

One-line status command: `python tools/healthcheck.py` (or `--json` for machine-readable).

Output ball:
- **🟢 Green** — watchdog <300s, monitor <3600s, no HIGH alerts
- **🟡 Yellow** — stale heartbeat OR unreviewed MED alert
- **🔴 Red** — HIGH alert OR watchdog dead (>600s) during active dispatch

## Fleet skill

One-shot snapshot: `aesop fleet` (JSON output, read-only, never mutates state).

Returns:
- heartbeats (watchdog, monitor: age, status, thresholds)
- agents (active: id, project, status, age, tokens, task label)
- tracker (backlog counts by lane)
- orchestrator (activity, phase, timestamp)

Graceful degradation: missing files produce `unavailable: "<reason>"` entries.

## Test commands

- **buildsystem**: Run a full wave (in a test repo with mock backlog)
  ```bash
  cd <test-repo>
  /buildsystem
  # Verify: STATE.md updated, BUILDLOG.md appended, PRs merged
  ```
- **power**: Prime a new repo or already-primed repo
  ```bash
  /power
  # Verify: compact brief output, CLAUDE.md files readable, STATE.md NEXT STEPS clear
  ```
- **healthcheck**: Check fleet health
  ```bash
  python tools/healthcheck.py
  python tools/healthcheck.py --json
  ```
- **fleet**: Snapshot fleet state
  ```bash
  aesop fleet
  # Verify: JSON valid, heartbeats present, agents or "unavailable" message
  ```

## Key invariants & gotchas

1. **Buildsystem is stateless across phases** — each phase stands alone (safe to restart mid-wave)
2. **Backlog must be parseable** (YAML or markdown, consistent format) — preflight will abort on parse errors
3. **File ownership overlap is fatal** — preflight guard prevents two agents from touching same file
4. **Worktrees auto-cleanup** — don't manually delete `aesop-wt-*` dirs; buildsystem handles it
5. **Power init-prime writes repo files** — controlled commit + push via secret-scan gate
6. **Healthcheck is read-only** — diagnostics only, never repairs
7. **Fleet snapshot is JSON canonical** — orchestrators use this, not ad-hoc state reads

## Domain-specific details

Each skill has its own **SKILL.md** file in its directory with complete implementation details, manifests, and troubleshooting:

- `skills/buildsystem/SKILL.md` — phases, manifest, work-order contract, troubleshooting
- `skills/power/SKILL.md` — init-prime and fast-path workflows
- `skills/healthcheck/SKILL.md` — diagnostics and repair procedures
- `skills/fleet/SKILL.md` — snapshot structure and queries

Map of all domains: /CLAUDE.md
