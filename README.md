<p align="center">
  <img src="https://raw.githubusercontent.com/matt82198/aesop/main/assets/logo.png" alt="Aesop" width="420">
</p>

<p align="center">
  <em>Crash-only multi-agent orchestration for any repository</em>
</p>

<p align="center">
  <a href="https://github.com/matt82198/aesop/actions/workflows/ci.yml"><img src="https://github.com/matt82198/aesop/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.npmjs.com/package/@matt82198/aesop"><img src="https://img.shields.io/npm/v/@matt82198/aesop" alt="npm"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-orange.svg" alt="License: PolyForm Strict 1.0.0 (source-available)"></a>
</p>

## What It Does

**Aesop** is an **orchestration harness that runs fleets of LLM coding agents**, verifies their output, and ships merge-ready code to CI. Each agent reads your repository state, fixes a ranked backlog item, runs tests locally, and auto-pushes. If a machine crashes mid-task, the next run re-reads from disk and continues — no external state server, no vector DB, no consensus machinery. The entire system and all decisions live in source-controlled, human-diffable files: git history, STATE.md, BUILDLOG.md, guardrail scripts. Aesop is battle-tested: this repository's own ~450 merged PRs across ~1230 commits were shipped by its own `/buildsystem` loop.

## How It Works

**Agent behavior is source code.** Every orchestration rule lives in durable files (STATE.md, BUILDLOG.md, Python guardrails, git history). When a machine fails, you re-read from disk—no special recovery path. The architecture is: crash-only workers (request-scoped Haiku agents over persistent filesystem state, ~1/3 Opus cost each), persistent filesystem brain (git-backed), fail-closed guardrails (pre-push secret-scan, cost ceiling, verification re-runs), and observable heartbeats (to detect and auto-restart stalls).

**Proof:** This repo is built entirely by Aesop. On a 39-task judgment benchmark, Haiku and Sonnet both scored 39/39 vs Opus 38/39 — the pre-declared ceiling rule trips, mapping a sufficiency floor for seam-level tasks (code review, severity calibration, local orchestration), not tier equivalence. Frontier reasoning and long-horizon planning are out of scope. Removing the hierarchical supervisor layer cut dispatch cost ~4× at identical graded quality (A/B; topology cancelled, data kept). The loop study shows that crash-only checkpointing + repair loops recover 20pp on hard tasks, lifting overall 67.8% → 77.2%.

## Why It Matters

Crash recovery is not a special path; it is how the system *always* starts. This design choice eliminates distributed consensus, external state servers, and recovery machinery. The trade-off: you own the git repo as your state layer, and you provide the human-in-the-loop to set goals and vet outbound gates (publishing, releases, history rewrites). The result: crash-only (request-scoped workers over persistent filesystem state) is simpler, faster to debug, and easier to audit than systems with hidden distributed state.

**Why it's built this way:** [The Aesop Hypothesis](./docs/THE-AESOP-HYPOTHESIS.md) — the design philosophy, the trade-offs, the cancelled architectures with published data.

**New in 0.4.0:** Swap the **worker** and **orchestrator** model (Claude, Codex, or any OpenAI-compatible endpoint) from one `seats` config block without code changes. See [docs/MICROKERNEL.md](./docs/MICROKERNEL.md) for the two-seat architecture and a 60-second quickstart. Single-instance proven; multi-instance coordination is scheduled.

## Feature Demo

**One-turn wave** — Run a complete build cycle (tests, build, docs, review, merge, audit) end-to-end:
```bash
python driver/wave_loop.py --manifest wave.json --one-turn
```
Ranks backlog, dispatches parallel Haiku workers, runs tests, audits the output. Produces a JSON report of all agent runs and their verdicts.

**Multi-model drivers** — Choose your backend (Claude Code, Ollama, OpenRouter) with one config line:
```json
{ "backend": "openai-compatible", "model": "mistral-small", "base_url": "http://localhost:1234/v1" }
```
Verification tiers auto-adapt to backend capability—weaker models get stronger safety checking without code changes.

**Wave templates** — Bootstrap a new fleet with a preset architecture:
```bash
python tools/wave_templates.py saas --project-name my-api --base-dir /workspace
```
Generates a manifest for typical 3-tier (API, frontend, ops) or data pipelines.

**Live dashboard** — Real-time view of fleet health, security alerts, work-item kanban, cost analytics:
```bash
npx @matt82198/aesop dash
```
Opens http://localhost:8770. Four views: Overview (agents, events), Work (kanban), Activity (reasoning tail), Cost (spend/tokens).

**Health score** — Readiness assessment: env, git, Python, Node, ports, config, hooks:
```bash
python tools/health_score.py
```
Outputs a scorecard of system readiness; --json for parsing.

**Self-monitoring daemon** — Runs every 150s: backs up work, scans secrets, detects drift:
```bash
npx @matt82198/aesop watch
```
Pre-push hook blocks leaks. Heartbeat signals liveness; monitor auto-detects stalls and restarts the fleet.

**Hardened gate stack** — Fail-closed secret-scan, adversarial review (default-on):
```bash
python tools/secret_scan.py --staged   # Blocks push if leak detected
```
Exits with failure on file-read errors (not silently passing). CI validates every merge.

**Parallel test battery** — Run all four test harnesses concurrently with isolated logs and enforced timeouts (`tools/test_battery.py` — ~5.4 min vs ~10 serial).

**Windows CI green** — Full parity support on Windows-latest GitHub Actions: promoted to a required check after 6 consecutive green main runs.

## Proof Numbers

Aesop builds itself. These numbers are live from git, verified by anyone who clones.

## Get Started

```bash
npx @matt82198/aesop my-fleet --name "api" --repos "/path/to/repo"
```
→ Copy `skills/` into `~/.claude/skills` to enable the `/power` and `/buildsystem` commands.
→ See [docs/INSTALL.md](./docs/INSTALL.md) for setup and first `/power` → `/buildsystem` cycle.
→ See [docs/DEMO.md](./docs/DEMO.md) for a complete walkthrough of one wave.


<!-- STATS:START -->

## Aesop builds itself

Aesop is built entirely by its own `/buildsystem` wave cycle—running parallel Haiku fleets across ranked backlog items, verifying merges, auditing orchestration health. These stats are the receipts: all numbers computed LIVE from git, verified by anyone who clones.

| Metric | Value |
| --- | --- |
| Merged PRs | 455 <!-- metrics-verified: self_stats.py (git log) --> |
| Total Commits | 1249 <!-- metrics-verified: self_stats.py (git log) --> |
| Project Age | 17 days <!-- metrics-verified: self_stats.py (git log) --> |
| Insertions + Deletions | 260,978 <!-- metrics-verified: self_stats.py (git log) --> |
| Files Tracked | 843 <!-- metrics-verified: self_stats.py (git log) --> |
| Authors | 1 human + 4 Claude model tiers <!-- metrics-verified: self_stats.py (git log) --> |

<!-- STATS:END -->

**Project Timeline:** Aesop is 17 days old, built by 1 human + the fleet. Every number above is regenerable from git history by anyone who clones the repo (`bash scripts/verify-stats.sh --check`); no hidden telemetry.







*Wave: one complete build cycle (intake → dispatch → verify → ship) run by the orchestration engine.*









## Why Haiku-First Works

The benchmark proves sufficiency for seam-level engineering tasks: across 39 judgment tasks (code review, severity calibration, root-cause analysis, refactor equivalence, security spots), Haiku scored **39/39** vs Opus **38/39** at ~1/3 the per-token cost. **Measured on seam-level engineering tasks (code review, severity calibration, local orchestration) — not frontier reasoning or long-horizon planning.** See [`bench/results/2026-07-17-judgment-v3-haiku-sonnet-opus.md`](./bench/results/2026-07-17-judgment-v3-haiku-sonnet-opus.md). The pre-declared ceiling rule (when ≥2 tiers score ≥92%, the instrument failed to discriminate) trips on this result — both Haiku and Sonnet achieved 39/39, meaning the benchmark maps a *sufficiency floor*, not tier equivalence. Full analysis: [`bench/results/2026-07-26-judgment-v3-ceiling-addendum.md`](./bench/results/2026-07-26-judgment-v3-ceiling-addendum.md) and [`bench/EQUIVALENCE-MARGIN.md`](./bench/EQUIVALENCE-MARGIN.md).

## Known Limitations

- **Benchmark is curated, not sampled:** The 39-task judgment set trips the pre-declared ceiling rule; it measures a sufficiency floor (Haiku is good enough for this domain), not equivalence or tier ranking. See [`bench/EQUIVALENCE-MARGIN.md`](./bench/EQUIVALENCE-MARGIN.md) for boundary conditions.
- **Adversarial review enforcement is deferred:** Verification runs via orchestrator-level exact-gate re-runs and adversarial verify lanes; in-loop enforcement deferred to a later increment. See [`driver/wave_loop.py` line ~36](./driver/wave_loop.py#L36).
- **MCP server is read-only by design:** The state-store projections are accurate only as of the last successful run state. Real-time multi-agent coordination is not yet implemented.
- **Seam-level only:** This repo's agents operate within the seam (local orchestration, code review, severity assessment, test bifurcation). Frontier reasoning tasks (architecture redesign, novel algorithms) are out of scope and will underperform.

## Evidence & Receipts

All evidence is committed to the repo and can be regenerated or verified by cloning.

**Gates That Fired:**  
The guardrails below are not theoretical. Real activations: the pre-push secret scan has blocked pushes—including a benchmark-vocabulary false-positive where the gate was kept strict and the content reworded; an agent's `--no-verify` bypass attempt was caught and the flag banned from every dispatch template; the watchdog has detected and auto-restarted stalled agents; and self-reported "green" results have been repeatedly refuted by re-running the exact CI gate—in one audited overnight session, nine such claims failed the re-run (BUILDLOG record). An early `--admin` merge of hallucinated docs led to those flags being forbidden in every orchestrated prompt.

- **Metrics gate:** [`bash scripts/verify-stats.sh --check`](./scripts/verify-stats.sh) — verifies stats.json matches git; README refreshed on every commit.
- **Test suite count:** [`python tools/verify_test_suite_count.py --check`](./tools/verify_test_suite_count.py) — confirms test count hasn't drifted.
- **Benchmark pre-registration:** [`bench/SEAM-STUDY-PREREG.md`](./bench/SEAM-STUDY-PREREG.md) — pre-declared design, success criteria, ceiling rule.
- **Equivalence margin amendments:** [`bench/EQUIVALENCE-MARGIN.md`](./bench/EQUIVALENCE-MARGIN.md) — pre-reg record and all amendments after each run.
- **Dated results:** [`bench/results/`](./bench/results/) — all judgment and frontier runs with timestamps.
  - **Loop study (2026-07-28):** [`bench/results/seam-loop-study-2026-07-28.md`](./bench/results/seam-loop-study-2026-07-28.md) — checkpoint recovery + repair loop data: 122/180 (checkpoint) → 139/180 (loop), +20pp on hard tasks.
- **Kill switch & ceilings:** [`tools/halt.py`](./tools/halt.py), [`tools/cost_ceiling.py`](./tools/cost_ceiling.py) — enforced at dispatch time.
- **Secret gate:** [`tools/secret_scan.py`](./tools/secret_scan.py) — pre-push enforcement; non-zero exit on leak.
- **Green-never-ran detection:** [`tools/ci_workflow_lint.py`](./tools/ci_workflow_lint.py) — ensures every CI suite actually runs before merge.

## Learn More

- **[AesopServer](https://github.com/matt82198/AesopServer)** — the JVM lens: a Spring Boot 3.5 microservice + server-rendered AesopDashboard observing this same brain read-only (typed record contracts, SQLite projections, SSE on virtual threads). Same hub, different process — the architecture is the point.
- **[docs/INSTALL.md](./docs/INSTALL.md)** — Setup and first wave  
- **[docs/MICROKERNEL.md](./docs/MICROKERNEL.md)** — The two swappable seats (worker + orchestrator), the invariant Report/state boundary, and a 60-second quickstart for swapping either seat's model  
- **[docs/PORTING.md](./docs/PORTING.md)** — Adopter's guide: port Aesop to your repo (prerequisites, scaffold, 10 failure modes)  
- **[docs/HOW-THE-LOOP-WORKS.md](./docs/HOW-THE-LOOP-WORKS.md)** — Concrete walkthrough of a wave cycle  
- **[docs/DISPATCH-MODEL.md](./docs/DISPATCH-MODEL.md)** — Cost analysis and scaling  
- **[docs/CARDINAL-RULES.md](./docs/CARDINAL-RULES.md)** — 10 foundational principles  
- **[docs/autonomous-swe.md](./docs/autonomous-swe.md)** — What "autonomous" means (and doesn't), evidence for all claims, honest limits  
- **[RELEASE-NOTES.md](./RELEASE-NOTES.md)** — Version 0.4.0 (the two-seat micro-kernel): swappable worker + orchestrator models from one config, live orchestrator seat-swap gate, IPv6/DNS hardening, scaffolding completeness

## Contributing

Aesop is **source-available** under the PolyForm Strict License 1.0.0, which does not permit modification or redistribution — so outside code patches can't be accepted as merged contributions. That said, **feedback is genuinely welcome**:

- **Issues and bug reports** — tell us what's broken or confusing.
- **Discussion and ideas** — feature requests, design critiques, use-case questions.

The repo develops itself via its own `/buildsystem` loop; code changes are made by the maintainer at their discretion, or by prior arrangement. See [CONTRIBUTING.md](./CONTRIBUTING.md) for details.

## License

**Source-available** under the [PolyForm Strict License 1.0.0](./LICENSE). You may read, run, and use the software for any permitted purpose, but **modification and redistribution are not permitted**. See [`LICENSE`](./LICENSE) for the full terms and the definition of permitted (noncommercial and personal) purposes.

**License history:** Aesop was released under the MIT License until 2026-07-17, when it was relicensed to PolyForm Strict 1.0.0. Snapshots cloned or forked before that date retain their original MIT license grant; new work lives under PolyForm Strict 1.0.0.

Copyright 2026 Matt Culliton.

## References

- [Anthropic Claude API docs](https://docs.anthropic.com)
- [Claude Code CLI](https://github.com/anthropics/claude-code)
- [Git docs](https://git-scm.com/doc)

---

**Aesop**: Autonomous developer for any repository, built by Aesop itself. May your orchestrator be wise and your subagents swift.
