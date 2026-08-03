# Aesop — Project CLAUDE.md

**What**: MIT-licensed multi-agent orchestration harness for autonomous software development.

## RED CI Escape Detection

RED CI runs (any FAILURE check on main or open PRs) are automatically detected every 20 minutes
by `~/scripts/detect_red_ci_runs.py`, which runs as part of the orchestrator's idle_tick.
Detected escapes are queued to `~/.claude/gate-escapes-queue.json`; idle_tick surfaces them as HIGH
priority; orchestrator's main thread invokes gate-escape-resolver workflow to fix + postmortem + enforce.
No RED run is missed; deduplication prevents redundant queue entries (keys on run_id).

## Domain map

- **skills/** — Orchestration skills (/power: priming skill) — read skills/CLAUDE.md
- **daemons/** — Watchdog daemon (repo backup, secret-scan gate, heartbeat) — read daemons/CLAUDE.md
- **dash/** — TUI dashboard (watchdog-gui.sh, real-time fleet status) — read dash/CLAUDE.md
- **monitor/** — Orchestration monitor (signals, AUTO/PROPOSE logic) — read monitor/CLAUDE.md
- **mcp/** — Read-only MCP server (fleet status, agents, tracker, costs) — read mcp/CLAUDE.md
- **scan/** — Example IOC/secret scanner template — read scan/CLAUDE.md
- **tools/** — Build utilities (Python/shell, secret scanning, verification gates) — read tools/CLAUDE.md
- **hooks/** — Git pre-push policy enforcement (branch protection, secrets) — read hooks/CLAUDE.md
- **driver/** — AgentDriver multi-model seam (Claude Code, Codex, OpenAI-compatible backends; wave bridge for verification routing) — read driver/CLAUDE.md
- **driver/orchestrator-swap/** — Orchestrator-backend selection, configuration, and seat-swap (HS-1/HS-2 increments; wave scheduler; adjudication gate) — read driver/orchestrator-swap/CLAUDE.md
- **bin/** — CLI scaffolder (Node.js entry point) — read bin/CLAUDE.md
- **ui/** — Web dashboard (SSE, CSRF protection, collector thread) — read ui/CLAUDE.md
- **state_store/** — Event-sourced state layer (SQLite WAL, projections) — read state_store/CLAUDE.md
- **bench/** — Held-out model benchmark (quality scorer) — read bench/README.md
- **templates/** — Wave-manifest presets (saas/data/library JSON) consumed by tools/wave_templates.py — read tools/CLAUDE.md
- **tests/** — Test suites (shell, Node, Python) and fixtures — read tests/CLAUDE.md
- **docs/** — Architecture guides, tutorials, setup — read docs/
- **assets/** — Logo, branding, media
- **state/** — Runtime durable checkpoints (git-ignored)
- **examples/** — Reference implementations and adopter walkthrough kits — read examples/first-wave-baseline/README.md
- **scripts/** — Repo maintenance wrappers (stats verification) — see § scripts/ below

## examples/

Adopter reference implementations and verified baseline walkthroughs. Artifacts here demonstrate the aesop product end-to-end: canonical wave cycles, manifest schema compliance, and realistic onboarding flows. **Invariants:** (1) All manifests validate against `tools/wave_templates.py` schema; (2) No personal paths, secrets, or invented CLI flags; (3) All claimed commands must be verifiable from `bin/cli.js --help` or repo tools; (4) README files include honest timing, cost, and prerequisites.

## scripts/

Thin CRLF-safe shell wrappers for repo maintenance. Currently: `verify-stats.sh` — the single-source stats gate (delegates to `tools/self_stats.py --check` / `--regenerate`; stats.json is the only stats source consumed by README and the portfolio). Invariants: wrappers delegate to tools/, never own logic; ASCII, no line continuations; exit codes propagate (fail-closed).

## Dispatch rule

Workers read exactly ONE domain CLAUDE.md; this file is navigation only.

## Setup for development

See docs/ for full setup, architecture, and usage guides.

## Relocated

- **Key principles** (Subagents always Haiku, Orchestrator on main thread only, State committed to git, Secret-scan gates, Idempotent + append-only, Observable machinery) → inlined into each domain's CLAUDE.md
- **Branch + PR discipline** (feature/* only, secret-scan.py gate, not a vault repo) → tools/CLAUDE.md
- **Setup for development** (install, config, watchdog test, dashboard, monitor extension) → docs/INSTALL.md
