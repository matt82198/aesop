# Aesop in ANY Repository — Portability Walkthrough

**Aesop works in any Git repository**, no matter the stack, size, or language. 
This guide walks you through initializing Aesop into a codebase you've just cloned, 
then running a full orchestration cycle to see how the system works.

---

## Prerequisites

Before starting, make sure you have:

- **Claude Code CLI** (v0.1+) with `/power` and `/buildsystem` skills installed
- **Git** (v2.40+)
- **Bash** (v4+) or Git Bash on Windows
- **Node.js** (v18+), **Python** (v3.10+) — for orchestration daemons
- A Git repository you want to orchestrate (can be your own repo, a fork, or a fresh clone of an existing project)

Check versions:
```bash
claude --version
git --version
bash --version
node --version
python3 --version
```

---

## Step 1: Clone or Navigate to Your Target Repo

Let's say you've just cloned a project you want to orchestrate:

```bash
cd ~/my-target-repo
git status  # confirm you're in a git repo
```

If the repo **already has `CLAUDE.md` + `STATE.md`**, you can jump to [Step 3](#step-3-run-power-fast-path-already-primed).

If the repo has **no CLAUDE.md layer**, continue to [Step 2](#step-2-install-aesop-harness).

---

## Step 2: Install Aesop Harness

Scaffold the Aesop daemons, skills, and configuration templates into your target repo root:

```bash
# Option 1: Scaffold into current directory (target repo already exists)
npx @matt82198/aesop .

# Option 2: Scaffold into a new aesop-fleet subdirectory
# (with a TTY on stdin and stdout, bare aesop scaffolds; without a TTY, bare aesop is the stdio pipe
#  and prints one stderr notice, so run `npx @matt82198/aesop wizard` to scaffold from a script)
npx @matt82198/aesop

# Option 3: Scaffold with project name and repos (headless mode)
npx @matt82198/aesop orchestrator --name "my-project" --repos "/path/to/repo1"
```

This creates (or adds to) the following structure in your repo:

```
.
├── .git/
├── .github/
│   └── workflows/           (optional CI examples)
├── daemons/
│   ├── run-watchdog.sh      (background monitor: backup, secret-scan, heartbeat)
│   ├── backup-fleet.sh
│   └── ...
├── skills/
│   ├── power/               (orchestrator brain-priming skill)
│   └── buildsystem/         (wave automation skill)
├── monitor/
│   ├── collect-signals.mjs  (extensible health signal collectors)
│   └── CHARTER.md
├── ui/
│   ├── serve.py             (dashboard backend)
│   └── web/                 (React frontend; optional)
├── hooks/
│   ├── pre-push-policy.sh   (secret-scan + branch discipline enforcement)
│   ├── pre-commit-waveguard.sh (wave checkpoint guard)
│   └── install-waveguard.sh (hook installer script)
├── tools/
│   ├── secret_scan.py       (block credentials before push)
│   ├── power_selftest.py    (verify orchestration health)
│   └── ...
├── state/                   (git-ignored; runtime checkpoints live here)
│   └── .gitkeep
├── aesop.config.example.json
└── aesop.config.json        (your personalized config, git-ignored)
```

**Automatic Hook Installation**: The scaffold process automatically installs git hooks in `.git/hooks/`:
- **pre-push** (auto-installed copy of `hooks/pre-push-policy.sh`): runs secret scanning and branch discipline checks before allowing pushes
- **pre-commit** (auto-installed waveguard hook): implements wave checkpointing guard (ensures consistent state)

These hooks are copied (not symlinked) to `.git/hooks/` to work reliably across all platforms (Windows, macOS, Linux). On re-scaffold with `--force`, hooks are replaced; without it, existing hooks are preserved (unless they differ from source, in which case a warning is shown).

---

## Step 3: Run `/power` (Init-Prime)

Now run the `/power` skill in the repository root:

```bash
cd ~/my-target-repo
/power
```

**What happens:**

Aesop detects that your repo has **no CLAUDE.md layer** and enters **init-prime mode**:

1. **Fan-out exploration** (4 parallel Haiku subagents, ~30s each):
   - Haiku-A scans repository structure, build system, entry points
   - Haiku-B catalogs key libraries, frameworks, dependencies
   - Haiku-C explores testing, CI/CD, deployment
   - Haiku-D identifies gotchas, non-obvious invariants

2. **Write CLAUDE.md layer**:
   - **Project-root `CLAUDE.md`**: purpose, build/run commands, gotchas, domain map
   - **Domain `CLAUDE.md` units**: e.g., `src/CLAUDE.md`, `api/CLAUDE.md`, `tests/CLAUDE.md`
   - **`STATE.md` checkpoint seed**: intent, phase, next steps (durable across wipes)

3. **Commit + Push** (gated by secret-scan):
   ```
   git add CLAUDE.md STATE.md src/CLAUDE.md ...
   git commit -m "init: initialize aesop orchestration semantics"
   python tools/secret_scan.py --staged
   git push origin feat/aesop-init-prime
   ```

4. **Report completion**:
   ```
   ✓ Aesop init-prime complete
     - Project-root CLAUDE.md written (purpose, commands, gotchas, 4 domains)
     - Domain CLAUDE.md units: src/, api/, core/, tests/
     - STATE.md checkpoint seed created
     - Pushed to origin feat/aesop-init-prime (secret-scan: all clear)
   
   NEXT: Review the `.md` layer, iterate on domains, then run /buildsystem for your first orchestration wave.
   ```

---

## Step 4: Review the Generated `.md` Layer

The init-prime step wrote several new files. Let's review them:

```bash
# View project-root CLAUDE.md (purpose, commands, gotchas)
cat CLAUDE.md
```

Example output:
```markdown
# my-target-repo — Orchestration Brain

**What**: my-target-repo is a Node.js + TypeScript REST API with PostgreSQL, 
orchestrated through cost-optimized multi-agent dispatch.

## Cardinal Rules
1. Subagents are always Haiku (cost optimization at scale)
2. Orchestrator (Opus) on main thread only
3. State gates everything (git commit + push)
4. Secret-scan gates every push (no credentials leak)
5. Idempotent + append-only (safe to restart mid-cycle)
6. Observable machinery (every action logged)

## Domain Map
- **API layer** (`src/api/`): HTTP routes, middleware, controllers
- **Core logic** (`src/core/`): business logic, state machine, data model
- **Infrastructure** (`src/infra/`): database, caching, external services
- **Tests** (`tests/`): fixtures, test harness, mocking

## Key Commands
- `npm run build` — TypeScript compilation
- `npm run test` — Run test suite
- `npm start` — Start the server (localhost:3000)
- `npm run migrate` — Database migrations

## Gotchas
- Tests must run in isolation (no shared DB state)
- Docker build takes 5 minutes; cache aggressively
- Postgres connection pool size = 10 (hardcoded, needed for high concurrency)
```

```bash
# View a domain CLAUDE.md (contracts, invariants)
cat src/api/CLAUDE.md
```

Example output:
```markdown
# API Layer

## Module Layout
- `routes.ts` — Express route handlers
- `middleware.ts` — CORS, auth, logging
- `controllers/` — business logic per endpoint
- `types.ts` — TypeScript interfaces for requests/responses

## Key Contracts
- All endpoints return `{ data: T, error?: string }`
- Authentication: Bearer token in Authorization header
- Content-Type: application/json only

## Anti-Patterns
- Don't call database directly from routes (use Core layer)
- Don't mutate global state in middleware
```

```bash
# View the state checkpoint
cat STATE.md
```

Example output:
```markdown
# my-target-repo — State & Next Steps

**Phase**: init-prime (first CLAUDE.md layer written)

**Intent**: my-target-repo is now initialized with aesop orchestration semantics. 
All domain rules live in scoped CLAUDE.md files. Ready to dispatch first work.

**NEXT STEPS**:
- [ ] Review + approve the written CLAUDE.md layer
- [ ] Adjust domains if needed (re-run init-prime or edit manually)
- [ ] Run `/buildsystem` to execute one example orchestration wave
```

---

## Step 5: Run `/buildsystem` (Orchestration Wave)

Now let's run a full orchestration cycle:

```bash
cd ~/my-target-repo
/buildsystem
```

**What happens:**

The `/buildsystem` skill reads your newly-written CLAUDE.md layer and orchestrates a full development wave:

1. **Phase 1: Backlog Review** (Opus orchestrator)
   - Read your project's CLAUDE.md domains and current STATE.md
   - Surface any open decisions, blockers, or proposed tasks
   - Rank the backlog by priority and risk

2. **Phase 2: Parallel Haiku Fleets** (6-8 Haiku subagents in parallel)
   - **One Haiku per domain** (API layer, core logic, tests, etc.)
   - Each Haiku:
     - Reads its domain CLAUDE.md (contracts, invariants, entry points)
     - Writes minimal TDD-first test improvements
     - Implements fixes or features
     - Runs local compile + test checks
     - **Pushes to a feature branch** (not main)
   - **Continues until all are green or blocked**

3. **Phase 3: Merge Train** (Opus orchestrator)
   - Collects all feature branches from Haiku fleets
   - Runs full test suite (CI) on each
   - Merges to main in order (if all green)
   - Reports which features shipped + their costs

4. **Phase 4: Checkpoint** (git commit + push)
   - Update `STATE.md` with new phase, completed work, next steps
   - Append to `BUILDLOG.md` (append-only log of all dispatches, timestamps, costs)
   - Commit + push (gated by secret-scan)

5. **Phase 5: Audit** (Haiku auditors, parallel)
   - Security review of merged code
   - Performance review of test times
   - Documentation audit (ensure CLAUDE.md is still accurate)
   - Report findings into the **next wave's backlog**

6. **Report Wave Summary**:
   ```
   ✓ Wave 1 complete
     - Domains: API layer, Core logic, Tests, Infra (4 parallel Haikus)
     - Work shipped: 3 feature branches merged to main
       - fix/db-connection-pool (API layer) — 2 tests added, 1 bug fixed
       - feat/cache-ttl (Core logic) — new cache invalidation strategy
       - refactor/test-fixtures (Tests) — improved isolation, 5m faster suite
     - Security audit: all clear (no new vulnerabilities)
     - Cost: 2,847 tokens (Opus: 1,200 + 4x Haiku: 412 each)
     - Duration: 4m 23s
   
   STATE.md updated. Ready for Wave 2 when you are.
   ```

---

## Step 6: Explore the New State Files

After the orchestration wave completes, several new files have appeared:

```bash
# State checkpoint — updated with new phase and next steps
cat STATE.md

# Append-only build log — every dispatch, cost, timestamp
tail -50 BUILDLOG.md

# Feature branches created by Haiku fleets (now merged to main)
git log --oneline main | head -5

# Watch the next wave begin (if autonomous)
tail -f state/POWER-SELFTEST.log
```

**Key files created/updated**:

| File | Purpose | Git-tracked? | Team-shareable? |
|------|---------|-------------|-----------------|
| `CLAUDE.md` | Project-root orchestration rules | ✓ Yes | ✓ Yes (team reference) |
| `src/CLAUDE.md`, etc. | Domain-scoped contracts + invariants | ✓ Yes | ✓ Yes (team reference) |
| `STATE.md` | Durable phase, locked decisions, next steps | ✓ Yes | ✓ Yes (team coordination) |
| `BUILDLOG.md` | Append-only log of every dispatch | ✓ Yes | ✓ Yes (audit trail, costs) |
| `aesop.config.json` | Personal paths, secrets (webhook URLs) | ✗ No (git-ignored) | ✗ No (per-developer) |
| `state/.watchdog-heartbeat` | Daemon liveness timestamp | ✗ No (git-ignored) | ✗ No (ephemeral) |
| `state/.power-selftest.log` | Orchestration health checks | ✗ No (git-ignored) | ✗ No (ephemeral) |

**Team-shareable files** (`CLAUDE.md`, `STATE.md`, `BUILDLOG.md`) are committed to git and shared across all developers. 
They're the team's **persistent orchestration brain**.

**Personal files** (`aesop.config.json`, heartbeats, logs) are git-ignored and only live on each developer's machine.

---

## Step 7: Running `/power` on Already-Primed Repo (Fast Path)

Now that your repo is primed (has CLAUDE.md + STATE.md), running `/power` again takes the **fast path**:

```bash
/power
```

**What happens (fast path)**:

1. **Detect priming**: Read project-root `CLAUDE.md` ✓
2. **Load rules**: Internalize dispatch model, cardinal rules
3. **Load memory**: Read team facts from `~/.claude/MEMORY.md`
4. **Load state**: Read `STATE.md` (current phase, locked decisions, next steps)
5. **Health check**: Run `tools/power_selftest.py` (hooks, secrets, heartbeats)
6. **Report compact brief**:
   ```
   ✓ Primed
     - Project: my-target-repo (Node.js API, 4 domains)
     - Dispatch model: Haiku subagents (one per domain), Opus orchestrator
     - Phase: wave-1 complete; ready for wave-2
     - Health: all clear (secret-scan: passing, hooks: installed, heartbeats: alive)
     - Next steps: run /buildsystem for wave-2
   ```
7. **Continue**: Proceed autonomously under the loaded rules

**Fast path is 5-10x faster** than init-prime (no code scanning, no .md generation, just loading persisted state).

---

## Customizing Your Domains

If init-prime identified domains incorrectly, or you want to refactor the domain map:

1. **Edit `CLAUDE.md`**: Update the domain map section
2. **Edit or add domain `CLAUDE.md` files**: Adjust contracts, invariants, entry points
3. **Update `STATE.md`** with a note: "Domains refactored in wave N"
4. **Commit + push**:
   ```bash
   git add CLAUDE.md src/CLAUDE.md core/CLAUDE.md STATE.md
   git commit -m "refactor: adjust domain map for clarity"
   git push origin main
   ```

Next `/buildsystem` cycle will use the updated domain map to dispatch Haikus.

---

## Troubleshooting

### "Init-prime didn't write files"
- Check that you're in the repo root: `pwd`
- Confirm the repo has no existing `CLAUDE.md`: `ls CLAUDE.md`
- Run `/power` with debug output: `DEBUG=1 /power`

### "Secret-scan blocked my commit"
- Review the leak: `git show --cached | grep -i "password\|secret\|key"`
- Fix the leaky file, re-stage, re-commit
- Never bypass the scan (`--no-verify` is not allowed)

### "Haikus didn't run; saw blank report"
- Check that `/buildsystem` skill is installed: `ls ~/.claude/skills/buildsystem/SKILL.md`
- Verify your domain `CLAUDE.md` files are readable and not corrupted
- Check `state/BUILDLOG.md` for error messages

### "I want to add a custom signal to the monitor"
- Edit `monitor/collect-signals.mjs` (it's a template)
- Add a new signal function (see the examples in the file)
- Restart the monitor (if running)
- Signal will appear in the next health check

---

## Next Steps

1. **Commit the CLAUDE.md layer** to your main branch (or review in a PR first)
2. **Run `/buildsystem` regularly** — it automates your team's development waves
3. **Iterate domain CLAUDE.md** as your team discovers invariants
4. **Monitor the dashboard** — `python ui/serve.py` and open http://localhost:8770
5. **Read [DISPATCH-MODEL.md](DISPATCH-MODEL.md)** to understand cost analysis + parallelism

---

## Key Takeaways

- **Aesop works in ANY repo** — no special stack, language, or project structure required
- **Init-prime is automatic** — one `/power` run generates the CLAUDE.md layer for you
- **Fast path is the default** — every subsequent `/power` loads state in seconds
- **State files are team-shareable** — CLAUDE.md, STATE.md, BUILDLOG.md are git-tracked and shared
- **Personal config is local** — aesop.config.json + heartbeats are git-ignored
- **Fully portable** — no hardcoded personal paths, all paths are configurable or relative

Your repo is now orchestrated. Happy deploying! 🚀
