# Init-Prime-Demo: Your First Complete Aesop Flow

## Try it in 30 seconds (no API key)

Want to see the dashboard *before* wiring up a fleet? The zero-key demo mode
serves a realistic, seeded fleet snapshot — no API key, no `gh` auth, no prior
runs required:

```bash
git clone https://github.com/matt82198/aesop.git && cd aesop
python ui/serve.py --demo        # then open http://127.0.0.1:8770
```

Or, from a scaffolded harness, `npx @matt82198/aesop dash --demo`.

You'll see a mid-execution wave ("wave-2: guardrail wiring + stats hardening"):
8–10 agents in mixed running/idle states, healthy watchdog + monitor
heartbeats, a populated PR board, a non-zero cost panel, and live wave
progress. Every timestamp is generated fresh so ages never go stale while the
demo runs. The page carries a **DEMO DATA** banner and `/api/state` returns
`"demo": true` — seeded data is always clearly labelled, never mistaken for a
live fleet. Running without `--demo` is unchanged.

The rest of this page is the full end-to-end walkthrough.

---

**TL;DR**: This 10-minute walkthrough takes you from nothing to a working orchestration harness, primed and ready for your first wave.

Covered in this demo:
1. Create a toy project (just a git repo)
2. Scaffold Aesop into a fleet harness
3. Run `/power` to prime the orchestrator
4. Understand what happens next

---

## What You'll Need

Before starting, ensure you have:

- **Node.js** v18+ (for scaffolder and CLI)
- **Git** v2.40+ (for repo setup)
- **Bash** v4+ (for daemons; Windows: Git Bash)
- **Claude Code CLI** v0.1+ (for `/power` and `/buildsystem` skills)

Check versions:
```bash
node --version
git --version
bash --version
claude --version
```

---

## Step 1: Create a Toy Project (2 min)

This simulates a real project that Aesop will manage.

```bash
# Create a new directory and initialize git
mkdir ~/my-demo-project
cd ~/my-demo-project
git init

# Configure git (required for commits)
git config user.email "demo@aesop.test"
git config user.name "Demo User"

# Create a sample file and make an initial commit
echo "# My Demo Project" > README.md
git add README.md
git commit -m "Initial commit"
```

Your toy project is now ready. It's a clean git repo with one commit.

---

## Step 2: Scaffold Aesop (2 min)

Create a fleet harness in a separate directory. The harness orchestrates your project(s).

```bash
# Go to a working directory (e.g., your home or ~/projects)
cd ~

# Scaffold Aesop (creates ~/demo-fleet/)
npx @matt82198/aesop demo-fleet \
  --name "demo-fleet" \
  --repos "~/my-demo-project" \
  --yes

# Move into the harness
cd ~/demo-fleet
```

What this does:
- Creates `daemons/`, `skills/`, `monitor/`, `tools/`, `ui/`, `docs/` subdirectories
- Generates `aesop.config.json` with your project path
- Creates `CLAUDE.md` (orchestrator brain template)
- Installs a git pre-push hook for secret scanning
- Initializes a git repo in the harness

The scaffold will report success and print next steps. Your output will vary depending on your environment, but will confirm:
- Fleet directory created
- Configuration files generated
- Git initialized
- Pre-push hook installed

---

## Step 3: Verify Installation (1 min)

Run preflight checks to ensure everything is ready.

```bash
npx @matt82198/aesop doctor
```

The tool will report which prerequisites are found and their versions. All required tools (Node.js, Git, Bash) will show as available if installation succeeded. Optional tools like Python may be reported as missing, which is fine for the demo.

If you see warnings about missing optional tools (Python, jq), that's OK for this demo. You can install them later.

---

## Step 4: Prime the Orchestrator (/power) (3 min)

Now open Claude Code and invoke the `/power` skill. This reads your configuration and verifies the orchestrator is healthy.

```
/power
```

What `/power` does:
1. Reads `~/.claude/CLAUDE.md` (your global orchestrator rules)
2. Reads `aesop.config.json` (your fleet configuration)
3. Checks `state/STATE.md` (current phase and next steps)
4. Validates that daemons are healthy (checks heartbeat files)
5. Reports a health brief

The skill will report:
- Configuration files loaded (global rules, fleet config, state)
- Daemon health status (may show warnings on first run if heartbeat files don't exist yet)
- Current phase and next steps

If you see warnings about missing heartbeat files on first run, this is normal—they'll be created after the watchdog starts.

If you see errors about missing CLAUDE.md or heartbeat files, don't worry—they'll be created on first startup. Re-run `/power` after a few seconds.

---

## Step 5: Understand the Backlog (Optional, 2 min)

Before running your first wave, you need a backlog of work. The orchestrator dispatches agents to complete these tasks.

Example backlog:
```markdown
## Wave 1 Backlog

- **P1: Add setup documentation** (backend-dev) — Document the initial setup process
- **P2: Fix README formatting** (docs-agent) — Improve readability of README.md
- **P3: Add a test scaffold** (test-bot) — Create tests/ directory with example test
```

Each task should:
- Fit in 5–15 minutes for a Haiku agent
- Be scoped (not "refactor everything")
- Have a priority (P1=blocker, P2=quality, P3=tech debt)
- Be assigned to an agent type

For this demo, you can skip this step and just observe what `/buildsystem` would do. Or create 2–3 simple tasks.

---

## Step 6: Run Your First Wave (Optional, 10+ min)

Once you have a backlog, invoke `/buildsystem` in Claude Code:

```
/buildsystem
```

What `/buildsystem` does:
1. **Rank & Assign** — Reads your backlog, assigns agents to tasks
2. **Dispatch Fleet** — Spawns 3–8 Haiku agents in parallel worktrees
3. **Execute** — Each agent works on its task independently
4. **Verify** — Runs tests, checks compilation, validates changes
5. **Merge Train** — Commits and pushes completed tasks
6. **Checkpoint** — Saves state (STATE.md, BUILDLOG.md)

You'll see live updates as agents work. Each task runs in isolation (no conflicts).

The orchestrator will:
1. Rank and assign your backlog tasks to agents
2. Dispatch parallel Haiku workers (one per task, up to your configured concurrency)
3. Show live progress as agents work
4. Report completed PRs, test results, and merge status
5. Print a summary with duration, cost, and files changed

Actual output varies based on your backlog size, task complexity, and system performance. Review `state/BUILDLOG.md` for detailed progress and `state/STATE.md` for current phase.

---

## What Happened?

You just ran a complete Aesop wave cycle:

1. **Orchestrator** (you) → defined the backlog
2. **Dispatch** (Aesop) → analyzed tasks, assigned agents
3. **Execution** → 3+ Haiku agents worked in parallel (each in their own worktree)
4. **Verification** → tests passed, code reviewed, merged
5. **Checkpoint** → progress saved to `state/STATE.md` and `state/BUILDLOG.md`

Each wave is independent. You can run wave 2 with a new backlog, or pause and review results.

---

## Exploring the Harness

Now that you've seen it work, explore:

### 1. Check the state files
```bash
cat state/STATE.md          # Current phase and next steps
cat state/BUILDLOG.md       # Append-only progress log
```

### 2. View the dashboard (optional)
```bash
npx @matt82198/aesop dash  # Launches http://localhost:8770
```

Monitor waves in real-time: see active agents, heartbeats, logs, and costs.

### 3. Check the daemons
```bash
ls -la daemons/
bash daemons/run-watchdog.sh --once  # Run watchdog once (no daemon)
```

The watchdog handles backup, secret scanning, and fleet health checks.

### 4. Review the configuration
```bash
cat aesop.config.json       # Your fleet configuration
cat CLAUDE.md               # Orchestrator brain (domain map, rules)
```

---

## Scaling to Real Projects

Once you're comfortable with the demo flow, you can:

1. **Add more repos** — Edit `aesop.config.json` and list your real projects
2. **Customize domains** — Edit `CLAUDE.md` to define your team structure
3. **Tune the backlog** — Work with your team to prioritize tasks
4. **Run continuous waves** — Schedule `/buildsystem` to run on a cadence
5. **Set cost ceilings** — Add spending limits to prevent runaway bills

See [CONFIGURE.md](CONFIGURE.md) and [INSTALL.md](INSTALL.md) for full details.

---

## Troubleshooting

**Q: `/power` says "CLAUDE.md not found"**
- A: Copy the skills to your Claude Code home:
  ```bash
  cp -r skills/power ~/.claude/skills/
  cp -r skills/buildsystem ~/.claude/skills/
  ```

**Q: `aesop doctor` reports missing Python**
- A: Python is optional for the demo. If you want secret scanning or log rotation, install it:
  ```bash
  python3 --version  # Check if installed
  pip install pyyaml  # If needed
  ```

**Q: Watchdog fails with "not inside a git repository"**
- A: Make sure you've run the scaffold from a location with git installed and initialized. Re-run scaffold:
  ```bash
  npx @matt82198/aesop demo-fleet --yes
  ```

**Q: Dashboard won't open (port 8770 in use)**
- A: The scaffold will use the next available port (8771, 8772, etc). Check `aesop.config.json` for the actual port.

See [INSTALL.md](INSTALL.md) for more troubleshooting and [CONCEPTS.md](CONCEPTS.md) for deeper understanding.

---

## Next Steps

1. **Run the full test suite** — Verify everything works:
   ```bash
   npm run test:all
   ```

2. **Read [FIRST-WAVE.md](FIRST-WAVE.md)** — Deep dive into the wave cycle

3. **Explore [CONCEPTS.md](CONCEPTS.md)** — Understand the dispatch model and state architecture

4. **Add your real repos** — Scale from demo to production

5. **Set up continuous integration** — Schedule waves to run automatically

Enjoy! 🚀
