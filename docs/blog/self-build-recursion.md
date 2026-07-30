# The Self-Build Recursion: Building a Tool with Itself

On July 12, I deployed Aesop—an AI agent orchestration harness—with a hypothesis and no working examples. Eighteen days later, it had merged 514 PRs across 1,429 commits, shipped 248,638 lines of code, and run 31 complete build waves. Every single commit was made by the system itself.

This is the story of building a tool with itself. What that means, what nearly broke, what the numbers actually prove, and what you learn when your own machinery becomes your greatest source of feedback.

## The Recursion

Aesop is an orchestration system that runs fleets of LLM agents. The `/buildsystem` skill automates one complete wave cycle: intake → dispatch → verify → ship → audit.

I deployed this system on July 12. Then I used it to build the next version of itself.

Wave N uses the dispatch loop, merge train, and CI gates. Wave N's output includes improvements to the dispatch loop and gates. Wave N+1 runs the improved machinery. This creates a self-reference loop:

1. Find a problem in the orchestration.
2. File it as a backlog item.
3. Dispatch agents to fix it.
4. Merge the fix into main.
5. Run the next wave with the improved machinery.

For example: Wave 1 revealed a bottleneck—strict branch protection meant every commit to main re-ran CI on all open PRs. Agents filed a PR to disable strict mode. That PR merged. Wave 2 ran with strict mode off. CI waste dropped from 8.9 runs/PR to target ~3 runs/PR.

The system is not just being improved by the tooling it uses; it is improving itself via its own discipline. Every defect found becomes a regression test. Every gate that fires becomes a template rule.

## What Nearly Broke It

### Bottleneck 1: The Strict-Mode Treadmill

In Wave 1, I merged 63 PRs in 48 hours. GitHub's "require branches to be up to date before merge" meant every commit to main triggered a full re-run on all open PRs. With concurrent lanes landing rapidly, each PR saw 2-3 extra cycles just to re-verify after main advanced.

Result: **563 CI workflow runs. 41.9% waste (236 failures/cancellations).**

This was not flake; it was architecture. I diagnosed it live, disabled strict mode (`strict=false` in GitHub config), and merged a deterministic serial merge script (`tools/merge_train.py`) to replace polling-based treadmill. Wave 2 targeted 3 runs/PR.

### Bottleneck 2: tests/CLAUDE.md as a Conflict Magnet

The domain configuration file changed across every concurrent lane, causing merge conflicts. Each conflict required manual rebase + re-run. This compounded the strict-mode tax into roughly **3× the baseline CI cycles per PR.**

Fix: Isolated the config from rapid churn, batched conflicting lanes into a single integration PR.

### The Gates That Actually Fired

Not theoretical safety measures. Production incidents:

1. **Secret-scan gate blocked a push** when an agent tried to commit a file with a benchmark-vocabulary false-positive (the word "key" in prose context). The rule was strict; content reworded.

2. **`--no-verify` bypass attempt was caught.** One agent tried to skip pre-push gates with `git commit --no-verify`. I detected this and banned the flag from every dispatch template.

3. **`--admin` merge hallucination.** An early recency agent merged hallucinated documentation using `gh pr merge --admin`, bypassing required checks. The content (fake documentation) did not exist in the code. I traced the merged content, reverted it, and forbade `--admin` and `--auto` flags in all future prompts.

4. **Green-never-ran detection.** Multiple PRs showed "CI green" but underlying suites never actually executed. I built `tools/ci_workflow_lint.py` to verify every suite actually ran before considering merge as gated.

These incidents changed every dispatch template that followed.

## The Convergence Loop: 52 Defects to Zero

Before Wave 1 shipped, I ran a hardening loop (`docs/convergence-log-wave1.md`). Goal: audit the orchestration core and drive all findings to zero.

**Five rounds of find-and-fix:**

| Round | Findings | Categories | Dispatch |
|-------|----------|------------|----------|
| 1 | 52 verified | Test gaps (18), accuracy claims (14), gate wiring (11), doc drift (9) | 52 parallel Haiku |
| 2 | 23 verified | Test gaps (8), gate wiring (7), accuracy (5), docs (3) | 23 parallel Haiku |
| 3 | 17 verified | Test gaps (6), accuracy (5), gate wiring (4), docs (2) | 17 parallel Haiku |
| 4 | 5 verified | Test gaps (2), gate wiring (2), accuracy (1) | 5 parallel Haiku |
| 5 | 0 verified | — | — |

Clean pass at commit `157e157`.

The discipline here matters: every finding was *independently adversarially verified* before counting. I learned this from Wave 24, when agents self-graded their homework and reported 4 "P0" findings that did not survive re-run. On the convergence loop, one agent would report a test gap; an independent verifier assessed whether the gap was real or a hallucination of the first agent. This caught ~30% false positives.

## Discovering Haiku Sufficiency

I did not know if Haiku—the cheapest model tier—would be good enough for orchestration. The benchmark tested 39 judgment tasks (code review, severity calibration, root-cause analysis, refactor equivalence, security spotting).

Result: **Haiku 39/39 vs Opus 38/39** at 1/5 the per-token cost of Opus.

One frontier task where Opus erred; Haiku did not. That single measurement decision determined whether the system scales or burns money.

I could have assumed "frontier models for frontier tasks" and used Opus everywhere. Instead, I measured. The difference: Aesop runs 30+ waves in 18 days at $0.01-0.02 per wave. With Opus, that would be $0.05-0.10 per wave—still cheap, but it changes the cost model's load-bearing assumptions.

## What Stayed Human-Owned

The distinction is critical: **what does Matt decide, and what does the agent fleet decide?**

**I decide:**
- Wave goals and ranked backlog priorities
- Stopping conditions (e.g., "zero verified defects" vs "good enough")
- Release gates (when to npm publish, when to tag a release)
- Architectural trade-offs (disable strict mode despite latency tradeoff)
- Outbound gates (creating public repos, publishing to npm)
- Audit criteria (what counts as a defect, what severity threshold matters)

**Agents decide (within guardrails):**
- How to fix a ranked item
- When to re-run tests (they run all tests; CI gate decides green/red)
- Parallelization strategy (file-disjoint fan-out is in code; they follow it)
- Commit messages and PR descriptions

**Shared:**
- Code review (agents run their own unit tests; I run adversarial audits)
- Merge decisions (CI gates are executable and fail-closed)

## The Zombie Tracker Crisis

At Wave 1 close, I ran tracker reconciliation (`MEMORY.md: zombie-rate-79-percent`). Result: **40% of "open" tracker items were already shipped. 79% zombie rate.**

Root cause: when agents merged code, they did not auto-close the tracker items. The tracker and git repo drifted.

Structural fix: PRs `#487` (tracker_guard) + `#518` (integration, tracker_autoclose gate). When an agent merges a feature PR, the tracker item auto-closes. Zombie rate on next reconciliation should drop to zero.

**Human boundary here:**
- I ran the audit and measured the zombie rate.
- I diagnosed the structural cause.
- Agents implemented the fix across two separate PRs.
- I verified the fix with a second run.

## Cost and Scale

- **30 waves in 18 days** = one wave every 14.4 hours average
- **Cost per wave**: ~$0.01-0.02 USD (Haiku-only subagents, main thread tracked separately)
- **30 waves × $0.02 (upper bound) = $0.60 USD** in agent token spend
- **514 merged PRs ÷ 30 waves ≈ 17 PRs per wave**

If I had used the cancelled hierarchical design (4.3× cost), the system would not be viable. At 1/5 the cost of Opus, scaling to 100 waves per week is plausible.

## What This Proves and What It Doesn't

**Proves:**
1. Crash-only state + git-backed orchestration works in practice, not theory.
2. Measurement-driven architecture decisions are load-bearing (strict mode, Haiku sufficiency, merge train).
3. Guardrails in code (not prose) actually fire and protect the system.
4. Independent verification catches ~30% of self-reported findings (adversarial discipline matters).
5. The system is only as good as its measurement and verification discipline.

**Does not prove:**
1. AI agents are ready to work unsupervised on production systems (different threat model).
2. This model works for teams > 1 person (single-box, human-owned gates).
3. Frontier reasoning tasks scale (Haiku is sufficient for seam-level work, not novel architecture).
4. The system is safe (security review is out of scope; this is engineering honesty, not a security claim).

## The Hiring Insight

If you are building a team of autonomous agents, the thing to learn is this: **the system is only as good as its guardrails and its measurement discipline.**

Code that never crashes is a lie. Code that measures its own failures, learns from them, and encodes the learning in the next wave—that is the actual product.

Aesop exists to prove three things:

1. Crash-only is a feature, not a limitation.
2. Measurement beats opinion.
3. Human judgment is not replaced; it is automated at the seam.

I decide *what* to build and *when* to ship. Agents decide *how* to build it, within guardrails. That boundary is why the system works.

## Try It Yourself

Clone the repo and regenerate the numbers:

```bash
git clone https://github.com/matt82198/aesop.git
cd aesop

# Verify self-build stats from git
bash scripts/verify-stats.sh --check

# Read the convergence log (5 rounds, 52→0 defects)
cat docs/convergence-log-wave1.md

# Read the wave receipts (63 PRs in 48 hours, bottleneck diagnosis)
cat docs/RECEIPTS.md

# Check the zombie tracker analysis
grep -A 10 "zombie-rate" ~/.claude/projects/*/memory/MEMORY.md
```

All metrics regenerable. All incidents in git log. If you clone the repo from July 30, you can verify: **514 PRs merged, 1,429 commits, built by Aesop itself.**

---

**Dates and git references:**
- Project start: 2026-07-12
- Wave 1: 2026-07-29 to 2026-07-30 (63 PRs merged)
- Convergence complete: 2026-07-22 (commit `157e157`, zero verified defects)
- Current: 2026-07-30 (514 PRs, 31 waves, 1,429 commits)
