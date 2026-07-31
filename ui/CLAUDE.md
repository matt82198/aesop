# ui/ â€” Web dashboard (self-contained domain guide)

**Purpose**: Local observability dashboard. Python backend serves a React+Vite frontend on a configurable port via Server-Sent Events (realtime updates), with CSRF + session protection and event-sourced state. All file I/O uses explicit `encoding="utf-8"`.

## Try it in 30 seconds (no API key)

A fresh scaffold has empty state, so a stranger sees a dead shell. `--demo`
serves a seeded, self-identifying fleet snapshot instead â€” no API key, no gh
auth, no prior runs needed:

```bash
git clone https://github.com/matt82198/aesop.git && cd aesop
python ui/serve.py --demo        # then open http://127.0.0.1:8770
# or via the CLI: npx @matt82198/aesop dash --demo
```

The page carries a fixed **DEMO DATA** banner and `/api/state` returns
`"demo": true` â€” seeded data can never be mistaken for a live fleet (honesty
is the project's brand). Default mode (no flag) is byte-identical to before.

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Backend Python modules (stdlib-only)

**serve.py** (~65 lines): Composition layer. Calls `config.reload()`, `csrf.init()`, `sse.reset_state()`, re-exports their symbols for test suite.

**config.py** (call-time config, load-bearing rule):
- Path/env/`aesop.config.json` resolution; `reload()` recomputes all paths from current environment.
- **RULE: Every other module reads `config.X` at call time (`import config`), NEVER `from config import <path>`.** Frozen imports go stale after `reload()` (breaks test fixture isolation).
- Exports: `PORT`, `AESOP_ROOT`, `CONFIG_FILE`, `STATE_DIR`, `TRANSCRIPTS_ROOT`, `WEB_DIST`, `WATCHDOG_HEARTBEAT`, `MONITOR_HEARTBEAT`, `REPOS_JSON`, `ALERTS_LOG`, `INBOX_FILE`, `AUDIT_BACKLOG_FILE`, `UI_SESSION_TOKEN_FILE`, `TRACKER_FILE`, `ORCH_STATUS_FILE`, `LEDGER_FILE`, `COLLECTOR_INTERVAL`, `SSE_KEEPALIVE_SECONDS`, `SSE_MAX_CLIENTS`.

**csrf.py**: Session-token generation (atomic O_EXCL 0600 mode) + `validate_csrf_request()` (Origin/Referer check + X-Aesop-Token header). `init()` sets `SESSION_TOKEN` (43-char URL-safe base64). Token persisted to `state/.ui-session-token` (readable only by owner).

**collectors.py**: Read-only data collectors (heartbeats, repos, events, alerts, messages, backlog parse), tracker CRUD via WriteAPI, and SSE section snapshots. Functions: `_snapshot_data`, `_snapshot_tracker`, `_snapshot_orchestrator_status`, `drain_tracker_inbox`, `get_alerts`, `get_heartbeat_status`, `get_agent_lifecycle_events` (wave-29: agent state transitions from transcript analysis), etc. **Inc 1 (2026-07-30):** Tracker CRUD (`create_tracker_item`, `update_tracker_item`, `delete_tracker_item`) now routes through `state_store.write_api.WriteAPI`, which provides atomic append + render under OCC. Deleted independent `save_tracker()` render path. `load_tracker()` still reads `tracker.json` as a cache.

**agents.py**: Agent transcript reading (`get_fleet_agents`, `extract_agent_dispatch_prompt`, `get_agent_detail`), path-traversal-safe agent-id handling via `_AGENT_ID_FORBIDDEN`.

**sse.py** (Server-Sent Events): Client registry, bounded broadcast, hash-gated `_maybe_emit()`, background `collector_loop()` thread. `reset_state()` restores per-import collector isolation (cached module across test re-imports). Sections emitted (in order): "data", "backlog", "agents", "tracker", "status", "cost" (wave-14 addition). Keepalive comment-line (`: keepalive`) every ~15s.

**render.py**: Renders `ui/web/dist/index.html` with CSRF token substituted via unique sentinel `__AESOP_CSRF_SENTINEL__` (no `.format()` â€” Vite build passes it through verbatim). Requires `template_path` parameter; legacy fallback removed.

**handler.py** (HTTP routing + GET/POST endpoints):
- `DashboardHandler` class (extends `http.server.BaseHTTPRequestHandler`).
- `run_server(host, port, app_handler_fn)` â€” ThreadingHTTPServer required (SSE holds one connection per client).
- Reads `config.X` / `csrf.SESSION_TOKEN` at call time.

**cost.py**: Parser for `state/ledger/OUTCOMES-LEDGER.md` (markdown table); returns per-model + per-day aggregates, verdict scorecards, optional dollar estimates (if `aesop.config.json` supplies `pricing` map).

**state_query_panel.py** â€” Time-travel state query API: `GET /api/state/events` (temporal/stream/type filters) and `GET /api/state/streams` (aggregate view). Wraps `state_store.StateAPI`; gracefully degrades if DB missing.

**wave_prs.py** â€” Wave PR board: `get_wave_prs()` gathers open PRs + PR-less `feat/*` branches, rolls CI checks into passing/failing/pending/none, derives top blocker, caches ~5s. Degrades to `{available:false, error}` when gh missing/un-authed. Subprocess reads use `encoding='utf-8', errors='replace'`. Override gh binary: `AESOP_GH_BIN` env var.

**wave_telemetry.py** â€” Wave telemetry: `get_wave_telemetry()` extracts current phase (from `STATE.md`), top blocker (from `AUDIT-BACKLOG.md`), cost metrics (from ledger). Reads state at call time (no cache); degrades gracefully on missing files.

**wave_dispatch.py** â€” Wave dispatch (per-agent visibility): reads agent transcripts, infers phase (dispatch/thinking/tool-use/stall/done) from tail, estimates tokens from file size, computes activity age from mtime. Returns per-agent rows with phase badge, age, warnings (inactive >5min, stalled >10min). Degrades `{available:false}`. Polled 2-3s.

**wave_failure.py** â€” Wave PR failure drill-down: `get_wave_failure(pr_number)` shells `gh run view --json jobs` for jobs on PR branch, then `gh api .../jobs/{id}/logs` for failing jobs; extracts ~100-line log tails. Caches ~5s per PR; degrades to `{available:false, error}` when gh missing/un-authed. Override gh binary: `AESOP_GH_BIN` env var.

**demo.py** â€” Zero-key demo mode (`--demo` or `AESOP_DEMO=1`). Seeds throwaway state root with fabricated data, redirects all env vars to it; shell-out collectors use `get_demo_agents()`/`get_demo_wave_prs()`. Daemon refresher (~45s) keeps timestamps fresh. `AESOP_ROOT` stays real (dist must resolve). Honesty: BANNER_HTML + `"demo": true` in /api/state. No-op in default mode. Optional `AESOP_DEMO_ROOT` (tests).

**bench_panel.py**: Benchmark API routes (`/api/bench`, `/api/bench/compare`). Reads `bench_results_cache` at call time.

**tooling_panel.py** â€” Tooling dashboard panel: `GET /api/tooling/summary` aggregates results from repo analysis tools (todo_tracker, test_coverage_gaps, dead_code_check, import_cycle_check, encoding_lint). Runs tools via subprocess, caches 60s, gracefully degrades to null for missing tools. `?force=1` bypasses cache.

**quality_scorecard.py** â€” Quality scorecard API: spec-sharpness and per-wave quality metrics.

**wave_audit_tail.py** â€” Wave audit tail: streams recent audit findings for live dashboard display.

**wave_gantt.py** â€” Wave Gantt chart data: per-agent timeline for wave execution visualization.

**wave_reasoning_tail.py** â€” Wave reasoning tail: streams orchestrator reasoning for live display.

**wave_context.py** â€” Wave context file listing: files touched by wave agents for context display.

**api/__init__.py**, **api/tracker.py**, **api/submit.py**: Mutation handlers (tracker CRUD, inbox append).

## Frontend (React 18 + Vite + TypeScript)

**ui/web/src/**:
- **main.tsx**: Vite entry point; renders `<App />` to `#root`.
- **App.tsx**: App shell; hash-routed views (/#/, /#/work, /#/activity, /#/cost, /#/prs).
- **styles/tokens.css** + **global.css**: Design tokens (light/dark palettes, spacing, typography).
- **views/**: Overview, Work, Activity, Cost, WavePRBoard (with SSE bindings). 5 views total: `/#/` (Overview), `/#/work` (Work), `/#/activity` (Activity), `/#/cost` (Cost), `/#/prs` (PR Board). WavePRBoard polls `/api/wave/prs` every 5s; drills down to FailureDrilldown on click.
- **components/**: HealthHeader (always-visible mission-control status header), AgentsPanel, TrackerBoard, Timeline, CostChart, CostAnalyticsPanel, FailureDrilldown, BenchmarkPanel, etc.
  - HealthHeader: Mission-control status header (D4, always visible). Three-zone layout: (1) fleet orchestrator phase + agent counts/status breakdown from live agents array, (2) system health (watchdog/monitor/alerts/SSE/data freshness), (3) controls (cost snapshot, theme toggle, manual refresh). Color-coded freshness indicator dot. All metrics bound to real SSE state; nothing invented. Clickable cells jump to corresponding views. No local state beyond focus/hover. Props from App.tsx.
  - BenchmarkPanel: Results table (model/accuracy/tokens/latency/cost/timestamp) + model comparison cards; fetches `/api/bench` and `/api/bench/compare`; dark/light theme, responsive grid.
  - CostAnalyticsPanel (wave-29 UX): info-dense operator view with (a) spend per wave (bar chart), (b) model efficiency vs Opus counterfactual, (c) burn rate + end-of-wave projection with ceiling alert; graceful DATA-UNAVAILABLE states when ledger/ceiling missing.
  - FailureDrilldown: drawer showing CI job list + ~100-line log excerpts on expand; fetches `/api/wave/failure?pr=N`.
  - ToolingPanel: compact card grid showing repo tooling health (TODO count, coverage, dead code, import cycles, encoding issues); fetches `/api/tooling/summary`; green/yellow/red severity coding; refresh button; responsive grid.
- **lib/api.ts**: Typed fetch helpers + CSRF header injection + `/api/session` fallback for dev server.
- **lib/useSSE.ts**: EventSource hook with reconnect logic, per-section state, connection status.
- **lib/types.ts**: TypeScript types for all API payloads (backend contract).
- **lib/sanitizeUrl.ts**: XSS-safe URL parsing (inerts PR links on bad schemes).
- **vite.config.ts**: Vite config with API proxy to :8770.
- **dist/**: Built static files (committed to git; served by Python handler). Content-hashed by Vite.

**testids-in-fixtures pattern** (both Python + React): Test components with `data-testid` attributes. React tests use `getByTestId()` (via `@testing-library/react`). Python tests use fixtures to set testids for integration proofs. Fixtures use repository-agnostic paths (`<REPO>/` placeholders instead of hardcoded personal paths) for portability across repos/machines.

## API Routes

**Read-only**: `/` (HTML+CSRF), `/data`, `/assets/*`, `/api/state` (first-paint snapshot), `/api/session` (dev token), `/api/cost`, `/api/backlog`, `/api/agents`, `/api/agent?id=`, `/api/tracker` (?status/priority), `/api/state/events` (?stream/type/after/before/limit), `/api/state/streams`, `/api/wave/prs` (CI rollup, 5s cache), `/api/wave/telemetry`, `/api/wave/dispatch` (2-3s poll), `/api/wave/failure?pr=N`, `/api/wave/gantt`, `/api/wave/audit-tail`, `/api/wave/reasoning-tail`, `/api/wave/quality-scorecards`, `/api/context/files`, `/api/quality/spec-sharpness`, `/api/bench`, `/api/bench/compare`, `/api/tooling/summary` (60s cache, ?force=1), `/events` (SSE, 6 sections, 15s keepalive), `/favicon.ico` (204). All `/api/wave/*` honor `AESOP_GH_BIN`.

**Mutations (CSRF-gated)**: `POST /submit` (inbox), `POST /api/tracker` (create), `POST /api/tracker/<id>` (?action=update|delete).

## CSRF & Session Protection

43-char URL-safe base64 token, persisted to `state/.ui-session-token` (0600), regenerated if missing. Mutations require Origin/Referer local check + X-Aesop-Token header match (both fail-closed). CLI reads token from file; browser gets it injected into HTML template.

## SSE Contract

Realtime via `GET /events` (ThreadingHTTPServer required). 6 sections (data/backlog/agents/tracker/status/cost) emitted on content-hash change. Keepalive ~15s. Background collector thread polls every `COLLECTOR_INTERVAL` (1s default), mtime-gated, fail-open on crash.

## State Store Integration

**Inc 1 consolidation (2026-07-30):**
- Write: All tracker CRUD routes through `state_store.write_api.WriteAPI`, which appends events to SQLite and renders views atomically via `state_store.materialize`.
- Read: `load_tracker()` reads the materialized `tracker.json` (git-ignored, gitignored, rebuildable â€” NOT committed). `tracker.json` is a derived view of the event store, kept current by WriteAPI.
- Canonical render path: `materialize_tracker()` (one pure function) â€” all callers use this, not independent render logic.
- Recovery: `python tools/state_rebuild.py --all` rebuilds views from event store with zero data loss.

## Configuration

Precedence: env vars > `aesop.config.json` > built-in defaults. Key env vars: `PORT` (8770), `AESOP_ROOT`, `AESOP_STATE_ROOT`, `AESOP_TRANSCRIPTS_ROOT`, `AESOP_UI_COLLECT_INTERVAL` (1.0s), `AESOP_GH_BIN`, `AESOP_AUDIT_BACKLOG`, `AESOP_DEMO`/`AESOP_DEMO_ROOT`. Config keys: `state_root`, `transcripts_root`, `aesop_root`, `pricing`.

## Build & Test

`cd ui/web && npm run build` before serving (dist always required, no fallback). Dev: `npm run dev` (Vite proxies API to :8770). Python tests: `python -m unittest discover -s tests`. React: `cd ui/web && npm test`. Playwright: `npx playwright test`.

## Invariants

Stdlib-only backend (ThreadingHTTPServer for SSE). Collector fail-open. Config: `import config; config.X` (never `from config import X`). Dist always required (hard 500 if missing). Map of all domains: /CLAUDE.md

## Tracker event-log migration (collectors.py)

`_ensure_tracker_migrated()` backfills the event log from an existing `tracker.json`. Two markers:
`migration_started` claims the attempt, `migration_completed` is written only after a fully
successful backfill -- skip requires BOTH, so a failed or partial migration retries instead of
being permanently blocked by a stale claim.

It reconciles in BOTH directions. Items missing from the log are backfilled from disk; and where
disk holds a status the log never recorded (historical closes were written straight to the
projection without emitting events), an `item_updated` event is emitted so replay cannot resurrect
them. The reconcile skips any item that already carries an explicit `item_updated` event -- that
item is owned by the log, and overwriting it from a stale projection would revert a real update.

## Cost view: absent vs empty → three distinct states

**RESOLVED (wave-31 audit findings):**

`Cost.tsx` now receives both `cost` prop AND `connectionStatus` to distinguish three states:
1. **Loading** (`cost=null` and `connectionStatus.status='live'`): shows "Loading cost metrics..." with explanatory hint.
2. **Error** (`cost=null` and `connectionStatus.status != 'live'`): shows error callout with connection error message + retry button.
3. **Empty** (`cost` has `total_runs=0`): shows "No cost data yet" with explanation (fresh install case).

Old behavior: null ambiguously covered both loading and error, rendering both as a slow-connection alarm.
New behavior: loads three distinct callout styles with honest messaging, preserving retry affordance.

**Chart accessibility (FINDING 2):** All SVG charts already had `role="img"` + `aria-label` naming; added tests to verify screen readers can announce chart content:
- `CostChart.tsx`: "Daily token usage by model"
- `ModelMixTrendChart.tsx`: "Daily model usage distribution"  
- `CostAnalyticsPanel.tsx` WaveSpendChart: "Spend per wave"
Bar segments include `<title>` elements for tooltips. Tests verify presence and correctness.
