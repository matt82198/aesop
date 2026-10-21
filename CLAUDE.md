# Aesop Kernel — Project CLAUDE.md

**What**: PolyForm Noncommercial 1.0.0-licensed orchestration kernel for autonomous software development (versions ≤0.7.0 remain MIT).

## RED CI Escape Detection

RED CI runs (any FAILURE check on main or open PRs) are detected whenever the orchestrator runs idle_tick (no scheduled cadence)
by `~/scripts/detect_red_ci_runs.py`, which idle_tick invokes.
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

## If you need X, read Y

Search by question, not by directory. For a term not in this table run `aesop map <term...>` (searches INDEX.md and CLAUDE.md entry lines only). Grep only after a miss, and record that miss as a Discovery line: every repeated discovery becomes a new row here.

| If you need | Read |
|---|---|
| how CI runs game-e2e (keyrun) | LANE-CONTRACT.md § CI contract (keyrun) |
| a fresh keyrun worktree has no Godot import cache | LANE-CONTRACT.md § CI contract (keyrun) (setup-worktree.ps1 note) |
| a lane's row file format (keyrun game/lanes) | LANE-CONTRACT.md § CI contract (keyrun) item 7 (canonical format: keyrun's lanes README) |
| git -C with a $VAR path, or pushing from the wrong cwd | LANE-CONTRACT.md § 4a item 6 (push from the worktree, literal paths, separate commit and push commands) |
| the checks to run before push, in order | LANE-CONTRACT.md § 4a Before you push |
| what a finished lane must prove (LANDED / BLOCKED-LANDING) | LANE-CONTRACT.md § 8b Landing |
| the hand-back required fields and the consequence-free discovery line | LANE-CONTRACT.md § 8 Report format, "Required discovery-cost fields" |
| where hand-backs land | C:/Users/Mattt/.claude/state/handbacks/<session>/ |
| the test isolation rules (HOME, AESOP_STATE_ROOT) | LANE-CONTRACT.md § 2 Tests; tests/CLAUDE.md |
| which gates CI runs and how to run them | LANE-CONTRACT.md § 5 Verification |
| how to find a term in the index files | `aesop map <term...>` (bin/cli.js `map`, tools/map_lookup.js) |

## examples/

Adopter reference implementations and verified baseline walkthroughs. Artifacts here demonstrate the aesop product end-to-end: canonical wave cycles, manifest schema compliance, and realistic onboarding flows. **Invariants:** (1) All manifests validate against `tools/wave_templates.py` schema; (2) No personal paths, secrets, or invented CLI flags; (3) All claimed commands must be verifiable from `bin/cli.js --help` or repo tools; (4) README files include honest timing, cost, and prerequisites.

## scripts/

Thin CRLF-safe shell wrappers for repo maintenance. Currently: `verify-stats.sh` — the single-source stats gate (delegates to `tools/self_stats.py --check` / `--regenerate`; stats.json is the only stats source consumed by README and the portfolio). Invariants: wrappers delegate to tools/, never own logic; ASCII, no line continuations; exit codes propagate (fail-closed).

## Dispatch rule

Workers read exactly ONE domain CLAUDE.md; this file is navigation only.

- **LANE-CONTRACT.md** — the lane-contract hook refuses any dispatch whose prompt does not reference it (it injects nothing); read it before writing code.

## Setup for development

See docs/ for full setup, architecture, and usage guides.

## Relocated

- **Key principles** (Subagents always Haiku, Orchestrator on main thread only, State committed to git, Secret-scan gates, Idempotent + append-only, Observable machinery) → inlined into each domain's CLAUDE.md
- **Branch + PR discipline** (feature/* only, secret-scan.py gate, not a vault repo) → tools/CLAUDE.md
- **Setup for development** (install, config, watchdog test, dashboard, monitor extension) → docs/INSTALL.md
