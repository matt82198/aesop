# First-Wave Replay Kit: Canonical Walkthrough

This guide walks you through a **complete, verified wave cycle** using the First-Wave Replay Kit—a minimal example you can fork to see how Aesop works end-to-end.

**What you'll see:**
- A realistic 5-item wave manifest  
- Parallel dispatch of independent tasks  
- Testing and verification at each phase  
- Merge workflow with no conflicts  
- Honest timing and cost expectations

---

## The Replay Kit

The **First-Wave Replay Kit** lives in `examples/first-wave-baseline/` and contains:

- **wave-manifest.json** — The 5-item wave definition (validates against the real schema)
- **sample-backlog.md** — Evidence-based writeup of each item  
- **README.md** — Step-by-step walkthrough with real commands

### Why Use It?

This kit answers: **"How do I structure my first wave?"**

It demonstrates:
1. Realistic task scope (5 small, independent items)
2. Correct manifest schema — proven by `wave_templates.validate_manifest`
3. Disjoint file ownership — proven by `tools/wave_manifest_lint.py`
4. Fail-closed test commands — proven by `examples/first-wave-baseline/verify-testcmds.sh`
5. Duration figures labelled as estimates, because no real run was timed to produce them

---

## Quick Start: Validate & Inspect

### Step 1: Validate the Manifest

The tool that validates a manifest **file** is `tools/wave_manifest_lint.py`. From the aesop repo
root:

```bash
python tools/wave_manifest_lint.py examples/first-wave-baseline/wave-manifest.json
```

Real output (captured 2026-08-02, exit code `0`):

```
PASS: ownership_disjointness: No file ownership overlaps
INFO: path_existence: 5 new file(s)
PASS: prompt_sanity: All prompts valid
PASS: git_history_churn: No high-churn files detected
WARN: testcmd_validity: No testCmd specified
```

That last `WARN` is expected and is a linter quirk, not a manifest defect: the check reads a
*top-level* `testCmd`, while the wave engine reads a per-item `testCmd`. Every item in this kit
has one. (Consequently `--strict`, which promotes warnings to failures, exits `1` here.)

> **Do not** run `python tools/wave_templates.py validate <path>`. `validate` takes
> `--template {saas,data,library,all}` and accepts no file argument; handing it a path exits `2`
> with `wave_templates.py: error: unrecognized arguments: <path>`. To validate the built-in
> presets, the correct form is `python tools/wave_templates.py validate --template all`.

To run the same schema check the wave engine performs:

```bash
python -c "
import json, sys
sys.path.insert(0, 'tools')
from wave_templates import validate_manifest
with open('examples/first-wave-baseline/wave-manifest.json') as f:
    validate_manifest(json.load(f), allow_placeholders=False, require_testcmd=True)
print('Manifest is valid')
"
```

Real output — one line, exit code `0`:

```
Manifest is valid
```

`validate_manifest` raises on failure and returns `None` on success. It does not print item
counts or per-slug summaries; if you want those, see Step 2.

### Step 2: Inspect the 5 Items

```bash
python -c "
import json
m = json.load(open('examples/first-wave-baseline/wave-manifest.json'))
for i in m['items']:
    print(f\"{i['slug']}: {', '.join(i['ownsFiles'])}\")
"
```

Real output:

```
readme-typo-fix: README.md
enable-skipped-test: tests/test_example.js
add-eslint-config: .eslintrc.json, package.json
fix-doc-links: docs/ARCHITECTURE.md, docs/SETUP.md
simplify-util-functions: src/utils/helpers.js, src/utils/helpers.test.js
```

**Key observation:** All 5 items own **non-overlapping file sets**, which is what enables parallel
dispatch and conflict-free sequential merge. You do not have to take that on trust — it is the
`PASS: ownership_disjointness` line from Step 1.

### Step 2b: Confirm the Gates Are Real

A wave is only as honest as its `testCmd`s. A command like
`test -f docs/ARCHITECTURE.md && echo 'Doc files present'` passes whether or not the work was
done — it would bless an empty PR. The kit ships a script that proves its own gates fail when the
work is missing:

```bash
bash examples/first-wave-baseline/verify-testcmds.sh
```

Real output (exit code `0`):

```
########## PRE-WORK: nothing implemented -- every gate must FAIL ##########
  readme-typo-fix            exit=1   OK   (fails as required)
  enable-skipped-test        exit=1   OK   (fails as required)
  add-eslint-config          exit=1   OK   (fails as required)
  fix-doc-links              exit=1   OK   (fails as required)
  simplify-util-functions    exit=1   OK   (fails as required)

########## POST-WORK: all 5 items implemented -- every gate must PASS ##########
  readme-typo-fix            exit=0   OK   (passes as required)
  enable-skipped-test        exit=0   OK   (passes as required)
  add-eslint-config          exit=0   OK   (passes as required)
  fix-doc-links              exit=0   OK   (passes as required)
  simplify-util-functions    exit=0   OK   (passes as required)

RESULT: all 5 testCmds are fail-closed.
```

Requires `node` 18+, `npm`, `python`, and network access (it installs `eslint@8` so the
`add-eslint-config` gate runs the real linter). Apply the same test to your own manifests.

### Step 3: Read the Backlog

```bash
cat examples/first-wave-baseline/sample-backlog.md
```

Shows evidence, complexity, and expected effort for each item.

---

## Wave Cycle Overview

The replay kit demonstrates end-to-end (durations below are **planning estimates, not timings from
a measured run**):

```
┌──────────────────────┐
│  DISPATCH            │  Orchestrator loads manifest, assigns workers
│  5 items dispatched  │
└──────────────────────┘
         ↓
┌──────────────────────────────────────────┐
│  IMPLEMENT (parallel; dominant term)     │
│  Worker 1: Fix typo                      │
│  Worker 2: Enable test (same time)       │
│  Worker 3: Add linter config             │
│  Worker 4: Fix docs                      │
│  Worker 5: Refactor utils                │
└──────────────────────────────────────────┘
         ↓
┌──────────────────────┐
│  TEST                │  Each item's testCmd must exit 0
│  Verify readiness    │  (all 5 verified fail-closed — see Step 2b)
└──────────────────────┘
         ↓
┌──────────────────────┐
│  MERGE               │  5 branches, disjoint files, no conflicts
│  Wave closed         │
└──────────────────────┘
```

---

## Real-World Adaptation

### Copy the Kit

```bash
cp examples/first-wave-baseline/wave-manifest.json /path/to/your-repo/wave-manifest.json
cd /path/to/your-repo
```

### Customize the 5 Items

Edit the manifest to replace items with your backlog:

```json
{
  "items": [
    {
      "slug": "your-item",
      "ownsFiles": ["file1.js"],
      "prompt": "Your task description",
      "testCmd": "npm test -- file1.test.js",
      "workDir": "."
    },
    ...
  ]
}
```

### Validate Before Running

```bash
python tools/wave_manifest_lint.py your-wave-manifest.json
```

(Again: `wave_templates.py validate` is for the built-in presets and takes `--template`, not a
file path. Passing it a path exits `2`.)

Then confirm your own `testCmd`s are fail-closed — each one must exit non-zero *before* the work
is done, or it is measuring nothing. `examples/first-wave-baseline/verify-testcmds.sh` shows the
pattern.

### Run Your Wave

Use the orchestrator:

```bash
# Via the skill:
/buildsystem

# Or directly:
python driver/wave_loop.py --manifest wave-manifest.json
```

---

## What Each Item Demonstrates

| Item | Files | Complexity | What its gate actually asserts |
|------|-------|-----------|---------------|
| readme-typo-fix | 1 | Trivial | Correct spelling present AND typo absent |
| enable-skipped-test | 1 | Simple | No `.skip` marker remains AND the suite passes |
| add-eslint-config | 2 | Simple | Config exists, `lint` script declared, `npm run lint` exits 0 |
| fix-doc-links | 2 | Simple | Every relative markdown link resolves on disk |
| simplify-util-functions | 2 | Moderate | `Refactor goal:` marker present AND tests green |

The last row is the one honest caveat in this kit: a marker plus a green suite proves the worker
touched the file and broke nothing. It cannot prove the refactor was an improvement — that
judgement is not mechanizable and is left to review rather than faked by a passing command.

---

## Before You Start (Original Guide)

Make sure you've completed:

1. **[INSTALL.md](INSTALL.md)** — Installed Aesop and verified with `bash daemons/run-watchdog.sh --once`
2. **[CONFIGURE.md](CONFIGURE.md)** — Created and validated `aesop.config.json`
3. **Skills installed** — Copied `skills/power/` and `skills/buildsystem/` to `~/.claude/skills/`

---

## Step 1: Prime Your Orchestrator Brain (/power)

Open Claude Code and type:

```
/power
```

The `/power` skill reads your orchestrator brain files from disk:

- `~/.claude/CLAUDE.md` — Your global rules (cardinal rules, dispatch model, reliability principles)
- `~/.claude/MEMORY.md` — Team facts, learnings, project context
- `aesop.config.json` — Your configuration
- `state/STATE.md` — Current phase and NEXT STEPS
- `state/BUILDLOG.md` — Recent progress

**Output**: A **health brief** telling you:

```
✓ Orchestrator brain loaded
✓ Repos configured: my-api (clean), my-frontend (clean)
✓ Fleet daemons healthy (watchdog heartbeat ~10s old)
✓ STATE.md: Phase 1 (setup), NEXT STEPS: (1) create initial backlog
```

This tells you the orchestrator is ready.

---

## Step 2: Prepare a Backlog

Before running a wave, you need ranked work. The orchestrator needs to know what to build.

### Where your backlog lives

Create a `state/BACKLOG.md` file (or update the backlog section in `state/STATE.md`). Use this markdown format:

```markdown
# Wave 15 Backlog

## P1: Critical

- [ ] Fix secret-scan gate hang (blocking CI) — backend-dev — 5min
- [ ] Update MEMORY.md with wave-15 learnings — docs-agent — 3min

## P2: Features

- [ ] Add cost-ceiling dashboard widget — frontend-dev — 8min
- [ ] Implement fleet-ops monitoring — backend-dev — 10min

## P3: Tech Debt

- [ ] Refactor monitor/collect-signals.mjs (duplicate checks) — backend-dev — 15min
- [ ] Add type hints to Python tools — test-bot — 10min
```

**Backlog format**:
- **Sections**: Group by priority (P1/P2/P3)
- **Items**: Checkbox (`[ ]`) + title + agent type + time estimate
- **Sizing guide**:
  - **3–5 min**: Typo fix, simple config change, one-line refactor
  - **5–10 min**: New function, small feature, unit test
  - **10–15 min**: Module refactor, feature with 2–3 functions
  - **15+ min**: Split into smaller items (waves work best in 30–90 min total)

### Backlog principles

- **Sized for Haiku**: Each task should take 1 Haiku agent 3–10 minutes (not 30 minutes)
- **Scoped**: "Fix auth timeout" is better than "Refactor auth system"
- **Ranked**: P1 (blockers/critical), P2 (quality/features), P3 (tech debt/docs)
- **Typed**: Assign each to an agent type (backend-dev, frontend-dev, test-bot, docs-agent)

If your backlog items are too big, split them. If too many, rank and pick the top 3–4 for this wave.

---

## Step 3: Run the Wave (/buildsystem)

Type:

```
/buildsystem
```

The `/buildsystem` skill runs the complete wave cycle. Here's what happens:

### Phase 1: Rank & Assign (~5 min)

The orchestrator reads your backlog and assigns agents to each item. You'll see:

```
Wave 23 backlog:
  ✓ Task 1: Add README docs (docs-agent)
  ✓ Task 2: Fix typo (frontend-dev)
  ✓ Task 3: Unit tests (test-bot)
→ Dispatching 3 Haiku agents in parallel...
```

### Phase 2: Agent Fleet (60–90 min)

The orchestrator spawns 3–8 Haiku agents in parallel. Each works in its own worktree (no conflicts).

**What you see**:

```
[Haiku-1] Docs agent: writing README... 
[Haiku-2] Frontend: fixing typo... 
[Haiku-3] Test: adding coverage... 
```

**Meanwhile**: The orchestrator doesn't idle. It reads the monitor for health signals, gathers fleet status for next phase, or extends the backlog with ideas.

**Watchdog**: Runs every 10s in the background, checking heartbeats and respawning any hung agents (max 3 retries).

### Phase 3: Verify & Merge (~30 min)

Once agents finish (or hit retry cap), the orchestrator:

1. Reviews each PR
2. Runs tests
3. Approves + merges
4. Updates main

**Output**:
```
✓ PR #101: Add README docs (MERGED)
✓ PR #102: Fix typo (MERGED)
✓ PR #103: Unit tests (MERGED)
→ All 3 PRs merged to main
→ Integration tests: PASSED ✓
```

### Phase 4: Close & Audit (~15 min)

The orchestrator wraps up:

1. **Audit**: Did agents follow rules? (branch discipline, secret-scan, test coverage)
2. **Findings**: What went well? What bottlenecked?
3. **Backlog for next wave**: Any learnings to feed the next cycle?

**Output**:
```
Wave 23 complete:
✓ 3/3 agents finished (avg 45 min each)
✓ All PRs merged and tests green
✓ Findings: (1) docs task took 12 min, consider breaking smaller
          (2) test coverage suggestion: add integration tests
→ Next wave backlog suggestions: (1) expand dashboard tests (2) cache refactor
```

---

## What Happens If an Agent Hangs

**Watchdog protocol** (no human intervention needed):

1. **First hang** (>200s idle): Watchdog auto-restarts the agent
2. **Second hang**: Watchdog auto-restarts again
3. **Third hang**: Watchdog auto-restarts (last automatic attempt)
4. **Fourth hang**: Mark BLOCKED in BUILDLOG.md and surface to you

You then decide: break the task smaller, escalate to Sonnet, or park it and move on.

---

## Monitoring the Wave

### Watch the dashboard

While agents work, open the dashboard:

```bash
python ui/serve.py
```

Then open http://localhost:8770 in your browser. You'll see:

- **Overview**: Fleet agents (running/done), recent events
- **Work** (#/work): Task kanban (proposed → ranked → in-progress → done)
- **Activity** (#/activity): Agent timeline, main-thread reasoning
- **Cost** (#/cost): Token spend, cost breakdown by model

### Watch the TUI (Optional)

If you have `jq` installed, open the watchdog dashboard in another terminal:

```bash
bash dash/watchdog-gui.sh
```

This shows real-time fleet health: agents, worktrees, heartbeats, cost (refreshes every 3s, Ctrl-C to exit).

---

## After the Wave: Review & Learn

Once all phases complete:

1. **Check the BUILDLOG** — Read `state/BUILDLOG.md` for timestamped progress
2. **Review findings** — What went well? What slowed us down?
3. **Plan the next wave** — Use audit findings to rank next backlog

**Example learnings**:

```
✓ Fast: docs agent (12 min) — tasks are right-sized
⚠ Slow: test coverage took 35 min — consider pre-writing test scaffold
→ Next wave: prioritize cache refactor (high-value, good for Haiku scoping)
```

---

## Common Questions

### "How long does a wave take?"

**Typical**: 2–3 hours wall-clock (agents run in parallel).

- Phase 1 (rank): 5–10 min
- Phase 2 (agents): 60–90 min
- Phase 3 (verify): 20–30 min
- Phase 4 (close): 10–20 min

If you have 8 agents, you might see 2–4 hours. If 3 agents, closer to 1.5–2 hours.

### "What if I'm not ready for a wave?"

No problem! Run `/power` to check your health, then come back when you have a ranked backlog. Waves are optional — only run `/buildsystem` when you have work to ship.

### "Can I cancel a wave in progress?"

Yes, type `/stop` in Claude Code to halt the orchestrator. Any agents still running will be TaskStop'd. Work in progress (open PRs) will remain on the branch for you to handle manually.

### "How much does this cost?"

**Typical wave cost**: ~$0.03–$0.05 USD.

- 1 Opus orchestrator + 5 Haiku agents
- Haiku is ~1/5 the per-token cost of Opus
- Result: much cheaper than an all-Opus fleet

See [CONCEPTS.md](CONCEPTS.md) for the full cost model.

### "What if an agent fails?"

The watchdog handles it:

1. Agent task fails → watchdog detects (heartbeat stale)
2. Watchdog TaskStop's agent and relaunches (up to 3 times)
3. After 3 retries → mark BLOCKED and surface to you

You can then:
- Break the task smaller
- Escalate to Sonnet (rare)
- Park it and move on

---

## Next Steps

1. **Refine your backlog** — Add 3–4 tasks sized for Haiku
2. **Run your first wave** — Type `/buildsystem`
3. **Review the findings** — Check `state/BUILDLOG.md` and audit output
4. **Plan wave 2** — Use learnings to rank next backlog

For deeper understanding of the concepts, see [CONCEPTS.md](CONCEPTS.md). For governance and operational principles, see [GOVERNANCE.md](GOVERNANCE.md).

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `/power` returns "brain not found" | Check `~/.claude/CLAUDE.md` exists; copy the skills with `cp -r skills/power/ ~/.claude/skills/power/` |
| `/buildsystem` says "no backlog" | Create a ranked backlog before running; orchestrator needs work items |
| Watchdog doesn't start | Check `AESOP_ROOT` env var is set; verify `daemons/run-watchdog.sh` is executable |
| Agents stuck at "initializing" | Check agent model exists (Haiku); monitor `/events` endpoint in dashboard for errors |
| Dashboard shows "unavailable" | Install Node.js v18+; check `dash-extra.mjs` exists in root |

For more help, see [../README.md#troubleshooting](../README.md#troubleshooting).
