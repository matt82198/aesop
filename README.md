> **Contact policy.** I am not accepting acquisition offers, recruiter inquiries, or cold calls. Inquiries regarding licensing or commercial usage must go to matt82198@gmail.com. All other outreach will be ignored.

﻿<p align="center">
  <img src="https://raw.githubusercontent.com/matt82198/aesop/main/assets/logo.png" alt="Aesop" width="420">
</p>

<p align="center">
  <em>Crash-only multi-agent orchestration — restart IS recovery. Stateless workers, durable filesystem state, no central server to lose.</em>
</p>

<p align="center">
  <video width="400" controls><source src="https://raw.githubusercontent.com/matt82198/aesop/main/assets/clips/lens-fanout-haiku-pin.mp4" type="video/mp4"><img src="https://raw.githubusercontent.com/matt82198/aesop/main/assets/clips/lens-fanout-haiku-pin.gif" alt="Aesop wave opening and audit fleet fanout" width="400"></video>
</p>

<p align="center">
  <a href="https://github.com/matt82198/aesop/actions/workflows/ci.yml"><img src="https://github.com/matt82198/aesop/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.npmjs.com/package/@matt82198/aesop"><img src="https://img.shields.io/npm/v/@matt82198/aesop" alt="npm version"></a>
  <a href="https://www.npmjs.com/package/@matt82198/aesop"><img src="https://img.shields.io/npm/dm/@matt82198/aesop" alt="npm downloads"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm--Noncommercial--1.0.0-blue.svg" alt="License: PolyForm Noncommercial 1.0.0"></a>
</p>

> **Status notice:** Aesop Kernel is mid-way through a major event-based overhaul whose goal is restoring fully fail-closed hooks. The 0.9.x and 0.10.x minor releases may show some degradation versus earlier versions while that lands.

## What It Is

**Aesop Kernel is a multi-agent orchestration kernel for autonomous software development.** A kernel, not a harness: it owns the boundary between the model and your codebase, enforces one invariant (fail-closed verification), and refuses what it cannot verify rather than steering the model. It runs fleets of LLM coding agents across ranked backlog items, verifies their output locally, and ships merge-ready code to CI. Crash-only by design: workers are stateless, state lives in git + SQLite + durable files, and restart is the only recovery path.

The design lineage spans 50 years: Unix kernel/user-space boundaries and fail-closed composable tools (1975), through crash-only software (Candea & Fox, 2003), to the agentic loop — the Ralph technique of running a coding agent in a tight reconciliation loop (2025). Aesop Kernel implements this: the wave cycle is a reconciliation controller; STATE.md + BUILDLOG.md are the durable event log; workers are supervised processes; crashes trigger re-reads from disk.

**Self-learning.** The kernel dogfoods itself (it builds itself via its own /buildsystem loop) and categorizes failure modes into enforcement: the gate-escape-resolver skill turns CI failures into postmortems and audit cycles extract repeated issues and wire them into verification gates.

**Due to anthropics recent changes preventing clean implementations of fail closed hooks, the project is currently not fully firing all hooks, this is actively being solved, and v0.9.1 shipped fixes for some of the largest issues.**

## Credibility: The Trap Tests

Aesop Kernel does not trust itself. Every agent writes code claiming to be correct, and [the trap tests](./tests/test_traps.py) deliberately reproduce patterns of agent deception:

- **Fake-green trap**: Tests that skip real validation (caught incident #464: playwright browser-proofs reported green without actually running). Prevention: counts are computed live from git ls-files (no stored artifact to drift) and `python tools/verify_test_suite_count.py --check` verifies the CI shard matrix actually covers every tracked test file.
- **Gate-activation trap**: Forbidden flags (--admin, --no-verify, --auto) in dispatch templates (caught 7+ incidents). Pre-push secret scan blocks leaks with fail-closed exit on read errors.
- **Doc-invented trap**: Documentation claims not backed by facts (caught hallucinated 0.3.0 CHANGELOG entries). Prevention: statistics gate verifies README matches git.
- **Test-pollution trap**: Test state leaking across isolation boundaries (caught 6 incidents including sys.modules mock pollution). Prevention: all tests use isolated temp directories and subprocess cwd= isolation.
- **CI-drift trap**: Workflow state out of sync (caught #450: pytest missing from main-full.yml). Prevention: YAML validation and required-tool assertion.

**See docs/INCIDENTS.md for the 41-incident log backing each trap class.**

## Credibility: The Cancelled Architecture

A measured design decision, not a feature.

In wave-11, a hierarchical dispatch architecture was built: orchestrator → Sonnet specialists → Haiku fleets. A/B testing measured it as **4.3x weighted cost** (`docs/ab-cost-dataset.md`) producing **identical quality** (100% test pass <!-- metrics-verified: docs/ab-cost-dataset.md -->, zero repair rounds) on the same fixture. The architecture was cancelled on 2026-07-14. `docs/archive/spikes/tiered-cognition/` keeps it for reference; nothing is wired into live settings.

**The lesson:** flat Haiku-first dispatch is not a limitation—it is the measured optimum for this problem. Architectural changes require A/B proof or they stay off.

## Quick Try (2 min, no API keys)

Node.js >= 18, git, and Python 3.11+ (`npm run test:all` invokes Python test runners; `test:sh` also needs bash). No API keys needed.

### Dependencies (what is literally true)

- **npm runtime dependencies: none.** The root `package.json` has no `dependencies` key. Its only entry is the devDependency `@playwright/test`. `mcp/package.json` declares `"dependencies": {}`. The Node runtime code in `bin/`, `daemons/`, `monitor/`, `dash/`, `mcp/`, `hooks/claude/` and `scan/` imports only Node built-in modules.
- **Python**: the tooling under `tools/` and `daemons/` is mostly standard library. The non-stdlib imports are PyYAML (CI workflow linters), `cryptography` (receipt signing, with an HMAC fallback when absent), `playwright` (browser verification scripts), `opentelemetry` (optional, import-guarded telemetry sink), `anthropic` (bench API runner), and `pyflakes` (imported by `tools/pyflakes_gate.py`). CI also installs `pytest` to run `tests/`; no file under `tools/` or `daemons/` imports it. The declared list is in [`pyproject.toml`](./pyproject.toml).
- **CI floor**: the lowest Python version any workflow in `.github/workflows/` runs is 3.11.

```bash
git clone https://github.com/matt82198/aesop.git && cd aesop
npm install && npm run test:all             # Run full test suite
npx . --help                                # See CLI
```

Explore the CLI, verify the test infrastructure, and study [examples/first-wave-baseline](./examples/first-wave-baseline) — a replay kit of a complete autonomous wave cycle.

## Full Setup (LLM orchestration)

To run the actual multi-agent orchestration loop you need:

- **Claude Code CLI** (or another supported backend) with a valid API key
- **Python 3.11+** for guardrails, secret scanning, and the dashboard
- **Bash 4+** (or Git Bash on Windows) for daemon scripts

```bash
# Scaffolds the fleet AND installs /power, /buildsystem, /fleet, /dashboard,
# /healthcheck into ~/.claude/skills/ — the only path Claude Code scans.
# Add --install-deps to also run npm install and pip install -r requirements.txt.
npx @matt82198/aesop my-fleet --name "api" --repos "/path/to/repo" --install-deps
cd my-fleet

# Restart Claude Code — skills are enumerated at startup.
npx @matt82198/aesop doctor   # Confirms skills, hooks, config, and interpreters
```

Existing skills of yours are never clobbered: an installed skill that differs
from the shipped one is preserved and reported, and `--force` overwrites it.
Use `--no-skills` to scaffold without touching `~/.claude/`.

See the Learn More section for setup and architecture guides.

## How It Works

**Three layers:** An orchestrator (Opus/Fable, main thread) dispatches parallel Haiku workers in isolated worktrees—1/5 the per-token cost of Opus. Durable state lives in SQLite WAL + git-committed files (STATE.md, BUILDLOG.md). On crash, the orchestrator re-reads from disk. No special recovery.

**Verification gates:** Fail-closed secret scan (pre-push), re-run of the exact CI gate (no mock), adversarial review (default-on). These are not optional best-practices—they are the default architecture.

**Observable machinery:** Heartbeats detect stalls; watchdog auto-restarts; every decision is readable in logs and git history. See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the full picture.

## Known Limitations

- **Benchmark is curated, not sampled.** The 39-task judgment set (Haiku vs Sonnet vs Opus) scored 39/39 on all tiers, hitting the pre-declared ceiling rule (discrimination failure). It measures a sufficiency floor for seam-level tasks, not tier equivalence. See [bench/METHODOLOGY.md](./bench/METHODOLOGY.md).
- **Seam-level only.** This repo's agents operate within code review, test triage, severity assessment, and orchestration seams. Frontier reasoning (architecture redesign, novel algorithms) is out of scope.
- **MCP server is read-only by design.** State-store projections reflect the last successful run state. Real-time multi-agent coordination is not implemented.

## Learn More

- **[docs/INSTALL.md](./docs/INSTALL.md)** — Setup and first wave
- **[docs/FIRST-WAVE.md](./docs/FIRST-WAVE.md)** — Walkthrough of a complete autonomous wave cycle
- **[docs/MICROKERNEL.md](./docs/MICROKERNEL.md)** — Two-seat architecture (worker + orchestrator), model swapping
- **[docs/HOW-THE-LOOP-WORKS.md](./docs/HOW-THE-LOOP-WORKS.md)** — Concrete walkthrough of one wave cycle
- **[THE-AESOP-HYPOTHESIS.md](./docs/THE-AESOP-HYPOTHESIS.md)** — Design philosophy, trade-offs, and why crash-only
- **[DISPATCH-MODEL.md](./docs/DISPATCH-MODEL.md)** — Cost analysis, Haiku sufficiency, scaling properties
- **[docs/how-i-built-aesop.md](./docs/how-i-built-aesop.md)** — Technical narrative of the orchestration design
- **[CARDINAL-RULES.md](./docs/CARDINAL-RULES.md)** — 10 foundational principles for safe autonomous code
- **[autonomous-swe.md](./docs/autonomous-swe.md)** — What "autonomous" means and doesn't. Honest limits.
- **[docs/INCIDENTS.md](./docs/INCIDENTS.md)** — All 98 incidents: fake-green, gate-activation, test-pollution, stalls, conflicts, flakes, CI drift, hallucinations.
- **[RELEASE-NOTES.md](./RELEASE-NOTES.md)** — Release notes for the latest version (full per-PR list in [CHANGELOG.md](./CHANGELOG.md)).

## Contributing

Aesop Kernel is **source-available** under the PolyForm Noncommercial License 1.0.0. Patches and contributions are welcome.

- **Issues and bug reports** — tell us what's broken or confusing.
- **Discussion and ideas** — feature requests, design critiques, use-case questions.
- **Code contributions** — fork, commit, and open a PR; we'll review and merge.

See [CONTRIBUTING.md](./CONTRIBUTING.md) for details. The repo develops itself via its own /buildsystem loop.

## License

Aesop Kernel is licensed under the **[PolyForm Noncommercial License 1.0.0](./LICENSE)** from 0.8.0 forward: free for personal, research, and noncommercial use; commercial use is prohibited without a separate agreement.

Versions **0.7.2 and earlier** (released as Aesop) were under the MIT License and remain available under those terms — the relicense is not retroactive.

For commercial licensing, contact the author (Matt Culliton).

Copyright 2026 Matt Culliton.

## Citing

If you use Aesop Kernel, please cite it using the metadata in [CITATION.cff](./CITATION.cff) (GitHub
renders a "Cite this repository" button from this file). It also lists the preferred research
citation, *The Receipts Loop* (manuscript draft, arXiv submission pending).

## References

- [Anthropic Claude API docs](https://docs.anthropic.com)
- [Claude Code CLI](https://github.com/anthropics/claude-code)
- [Git docs](https://git-scm.com/doc)

---

**Aesop Kernel**: Autonomous developer for any repository, built by itself. May your kernel be honest and your workers swift.
