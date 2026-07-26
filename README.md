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

**Aesop** is a **crash-recoverable orchestration harness** for multi-agent workflows on any repository. One-line theme: *stateless agent execution over git-backed durable memory*.

Core idea: **agent behavior is source code.** Every decision lives in durable, human-diffable files—git history, plain-text STATE.md, append-only BUILDLOG.md, Python guardrails. When a machine fails, you re-read from disk. No vector DBs, no distributed consensus, no magic. This repo's own 387 merged PRs across 1181 commits were delivered by Aesop's own `/buildsystem` wave loop—a supervised loop under a human operator who sets goals and owns outward gates (npm publish, releases, history rewrites).

**Why it matters:** crash recovery is not a special path; it is how the system *always* starts. Stateless workers, persistent filesystem brain, Haiku-first dispatch (4.3× cheaper than hierarchical design, proven by real A/B), fail-closed guardrails (pre-push secret gate, kill-switch, cost ceiling), and observable heartbeats. The result: 387 PRs across 1181 commits; Haiku at 39/39 on a 39-task benchmark vs Opus 38/39, at ~1/3 the cost.

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
| Merged PRs | 387 <!-- metrics-verified: self_stats.py (git log) --> |
| Total Commits | 1181 <!-- metrics-verified: self_stats.py (git log) --> |
| Project Age | 14 days <!-- metrics-verified: self_stats.py (git log) --> |
| Waves | 30 <!-- metrics-verified: self_stats.py (git log) --> |
| Insertions + Deletions | 222,290 <!-- metrics-verified: self_stats.py (git log) --> |
| Files Tracked | 642 <!-- metrics-verified: self_stats.py (git log) --> |
| Distinct Co-authors | 11 <!-- metrics-verified: self_stats.py (git log) --> |

<!-- STATS:END -->





*Wave: one complete build cycle (intake → dispatch → verify → ship) run by the orchestration engine.*









## Why Haiku-First Works

The benchmark proves sufficiency: across 39 judgment tasks (code review, severity calibration, root-cause analysis, refactor equivalence, security spots), Haiku scored **39/39** vs Opus **38/39** at ~1/3 the per-token cost. See [`bench/results/2026-07-17-judgment-v3-haiku-sonnet-opus.md`](./bench/results/2026-07-17-judgment-v3-haiku-sonnet-opus.md). The pre-declared ceiling rule (when ≥2 tiers score ≥92%, the instrument failed to discriminate) trips on this result — both Haiku and Sonnet achieved 39/39, meaning the benchmark maps a *sufficiency floor*, not tier equivalence. **Curated set, N=39; scoped to extraction and judgment tasks with context at the seam** — does NOT reach frontier reasoning, long-horizon planning, or open-ended synthesis. Full analysis: [`bench/results/2026-07-26-judgment-v3-ceiling-addendum.md`](./bench/results/2026-07-26-judgment-v3-ceiling-addendum.md) and [`bench/EQUIVALENCE-MARGIN.md`](./bench/EQUIVALENCE-MARGIN.md).

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
