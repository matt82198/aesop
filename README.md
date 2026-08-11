<p align="center">
  <img src="https://raw.githubusercontent.com/matt82198/aesop/main/assets/logo.png" alt="Aesop" width="420">
</p>

<p align="center">
  <em>Crash-only multi-agent orchestration — restart IS recovery. Stateless workers, durable filesystem state, no central server to lose.</em>
</p>

<p align="center">
  <a href="https://github.com/matt82198/aesop/actions/workflows/ci.yml"><img src="https://github.com/matt82198/aesop/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.npmjs.com/package/@matt82198/aesop"><img src="https://img.shields.io/npm/v/@matt82198/aesop" alt="npm version"></a>
  <a href="https://www.npmjs.com/package/@matt82198/aesop"><img src="https://img.shields.io/npm/dm/@matt82198/aesop" alt="npm downloads"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>

## What It Is

**Aesop is a multi-agent orchestration harness for autonomous software development.** It runs fleets of LLM coding agents across ranked backlog items, verifies their output locally, and ships merge-ready code to CI. Crash-only by design: workers are stateless, state lives in git + SQLite + durable files, and restart is the only recovery path.

## Credibility: The Trap Tests

Aesop does not trust itself. Every agent writes code claiming to be correct, and [the trap tests](./tests/test_traps.py) deliberately reproduce patterns of agent deception:

- **Fake-green trap**: Tests that skip real validation (caught incident #464: playwright browser-proofs reported green without actually running). Prevention: all test discovery against documented counts via python tools/verify_test_suite_count.py --check.
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

No API keys, no Python, no configuration. Just Node.js >= 18 and git.

```bash
git clone https://github.com/matt82198/aesop.git
cd aesop

npm install
npx . --help                  # See CLI
npx . doctor                  # Readiness check (no keys needed)
npm run test:all             # Run full test suite
```

That is enough to explore the CLI, read the docs, and verify the test infrastructure. No API keys are touched.

## Full Setup (LLM orchestration)

To run the actual multi-agent orchestration loop you need:

- **Claude Code CLI** (or another supported backend) with a valid API key
- **Python 3.10+** for guardrails, secret scanning, and the dashboard
- **Bash 4+** (or Git Bash on Windows) for daemon scripts

```bash
npx @matt82198/aesop my-fleet --name "api" --repos "/path/to/repo"
cd my-fleet

# Enable /power, /buildsystem, /fleet, /dashboard, /healthcheck.
# Claude Code only discovers skills under ~/.claude/skills/ (or a project's
# .claude/skills/) — the scaffolded skills/ directory is not scanned on its own.
mkdir -p ~/.claude/skills
cp -r skills/*/ ~/.claude/skills/

# Verify, then restart Claude Code (skills are enumerated at startup):
ls ~/.claude/skills/power/SKILL.md
```

For the complete setup guide, orchestration walkthrough, and multi-instance roadmap, see:
- **[docs/INSTALL.md](./docs/INSTALL.md)** — Setup and first wave
- **[docs/HOW-THE-LOOP-WORKS.md](./docs/HOW-THE-LOOP-WORKS.md)** — Concrete walkthrough of one wave cycle
- **[docs/MICROKERNEL.md](./docs/MICROKERNEL.md)** — Two-seat architecture (worker + orchestrator), model swapping

## How It Works

**Three layers:** An orchestrator (Opus/Fable, main thread) dispatches parallel Haiku workers in isolated worktrees—1/5 the per-token cost of Opus. Durable state lives in SQLite WAL + git-committed files (STATE.md, BUILDLOG.md). On crash, the orchestrator re-reads from disk. No special recovery.

**Verification gates:** Fail-closed secret scan (pre-push), re-run of the exact CI gate (no mock), adversarial review (default-on). These are not optional best-practices—they are the default architecture.

**Observable machinery:** Heartbeats detect stalls; watchdog auto-restarts; every decision is readable in logs and git history. See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the full picture.

## Known Limitations

- **Benchmark is curated, not sampled.** The 39-task judgment set (Haiku vs Sonnet vs Opus) scored 39/39 on all tiers, hitting the pre-declared ceiling rule (discrimination failure). It measures a sufficiency floor for seam-level tasks, not tier equivalence. See [bench/METHODOLOGY.md](./bench/METHODOLOGY.md).
- **Seam-level only.** This repo's agents operate within code review, test triage, severity assessment, and orchestration seams. Frontier reasoning (architecture redesign, novel algorithms) is out of scope.
- **MCP server is read-only by design.** State-store projections reflect the last successful run state. Real-time multi-agent coordination is not implemented.

## Learn More

- **[THE-AESOP-HYPOTHESIS.md](./docs/THE-AESOP-HYPOTHESIS.md)** — Design philosophy, trade-offs, and why crash-only.
- **[DISPATCH-MODEL.md](./docs/DISPATCH-MODEL.md)** — Cost analysis, Haiku sufficiency, scaling properties.
- **[CARDINAL-RULES.md](./docs/CARDINAL-RULES.md)** — 10 foundational principles for safe autonomous code.
- **[autonomous-swe.md](./docs/autonomous-swe.md)** — What "autonomous" means and doesn't. Honest limits.
- **[docs/INCIDENTS.md](./docs/INCIDENTS.md)** — All 41 incidents: fake-green, gate-activation, test-pollution, stalls, conflicts, flakes, CI drift, hallucinations.
- **[RELEASE-NOTES.md](./RELEASE-NOTES.md)** — Version 0.7.0: hardened gates, multi-instance roadmap.

## Contributing

Aesop is **open source** under the MIT License. Patches and contributions are welcome.

- **Issues and bug reports** — tell us what's broken or confusing.
- **Discussion and ideas** — feature requests, design critiques, use-case questions.
- **Code contributions** — fork, commit, and open a PR; we'll review and merge.

See [CONTRIBUTING.md](./CONTRIBUTING.md) for details. The repo develops itself via its own /buildsystem loop.

## License

**Open source** under the [MIT License](./LICENSE). Relicensed to MIT on 2026-07-29.

Copyright 2026 Matt Culliton.

## References

- [Anthropic Claude API docs](https://docs.anthropic.com)
- [Claude Code CLI](https://github.com/anthropics/claude-code)
- [Git docs](https://git-scm.com/doc)

---

**Aesop**: Autonomous developer for any repository, built by Aesop itself. May your orchestrator be wise and your subagents swift.
