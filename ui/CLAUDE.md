# ui/ — Web dashboard (self-contained domain guide)

**Purpose**: Local observability dashboard. Python backend serves a React+Vite frontend on a configurable port via Server-Sent Events (realtime updates), with CSRF + session protection and event-sourced state. All file I/O uses explicit `encoding="utf-8"`.

## Try it in 30 seconds (no API key)

A fresh scaffold has empty state, so a stranger sees a dead shell. `--demo`
serves a seeded, self-identifying fleet snapshot instead — no API key, no gh
auth, no prior runs needed:

```bash
git clone https://github.com/matt82198/aesop.git && cd aesop
python ui/serve.py --demo        # then open http://127.0.0.1:8770
# or via the CLI: npx @matt82198/aesop dash --demo
```

The page carries a fixed **DEMO DATA** banner and `/api/state` returns
`"demo": true` — seeded data can never be mistaken for a live fleet (honesty
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

**collectors.py**: Read-only data collectors (heartbeats, repos, events, alerts, messages, backlog parse), tracker CRUD, and SSE section snapshots. Functions: `_snapshot_data`, `_snapshot_tracker`, `_snapshot_orchestrator_status`, `drain_tracker_inbox`, `get_alerts`, `get_heartbeat_status`, `get_agent_lifecycle_events` (wave-29: agent state transitions from transcript analysis), etc.

**agents.py**: Agent transcript reading (`get_fleet_agents`, `extract_agent_dispatch_prompt`, `get_agent_detail`), path-traversal-safe agent-id handling via `_AGENT_ID_FORBIDDEN`.

**sse.py** (Server-Sent Events): Client registry, bounded broadcast, hash-gated `_maybe_emit()`, background `collector_loop()` thread. `reset_state()` restores per-import collector isolation (cached module across test re-imports). Sections emitted (in order): "data", "backlog", "agents", "tracker", "status", "cost" (wave-14 addition). Keepalive comment-line (`: keepalive`) every ~15s.

**render.py**: Renders `ui/web/dist/index.html` with CSRF token substituted via unique sentinel `__AESOP_CSRF_SENTINEL__` (no `.format()` — Vite build passes it through verbatim). Requires `template_path` parameter; legacy fallback removed.

**handler.py** (HTTP routing + GET/POST endpoints):
- `DashboardHandler` class (extends `http.server.BaseHTTPRequestHandler`).
- `run_server(host, port, app_handler_fn)` — ThreadingHTTPServer required (SSE holds one connection per client).
- Reads `config.X` / `csrf.SESSION_TOKEN` at call time.

**cost.py**: Parser for `state/ledger/OUTCOMES-LEDGER.md` (markdown table); returns per-model + per-day aggregates, verdict scorecards, optional dollar estimates (if `aesop.config.json` supplies `pricing` map).

**wave_prs.py** — Wave PR board: `get_wave_prs()` gathers open PRs + PR-less `feat/*` branches, rolls CI checks into passing/failing/pending/none, derives top blocker, caches ~5s. Degrades to `{available:false, error}` when gh missing/un-authed. Subprocess reads use `encoding='utf-8', errors='replace'`. Override gh binary: `AESOP_GH_BIN` env var.

**wave_telemetry.py** — Wave telemetry: `get_wave_telemetry()` extracts current phase (from `STATE.md`), top blocker (from `AUDIT-BACKLOG.md`), cost metrics (from ledger). Reads state at call time (no cache); degrades gracefully on missing files.

**wave_dispatch.py** — Wave dispatch (per-agent visibility): reads agent transcripts, infers phase (dispatch/thinking/tool-use/stall/done) from tail, estimates tokens from file size, computes activity age from mtime. Returns per-agent rows with phase badge, age, warnings (inactive >5min, stalled >10min). Degrades `{available:false}`. Polled 2-3s.

**wave_failure.py** — Wave PR failure drill-down: `get_wave_failure(pr_number)` shells `gh run view --json jobs` for jobs on PR branch, then `gh api .../jobs/{id}/logs` for failing jobs; extracts ~100-line log tails. Caches ~5s per PR; degrades to `{available:false, error}` when gh missing/un-authed. Override gh binary: `AESOP_GH_BIN` env var.

**demo.py** — Zero-key demo mode (`--demo` or `AESOP_DEMO=1`). Seeds throwaway state root with fabricated data, redirects all env vars to it; shell-out collectors use `get_demo_agents()`/`get_demo_wave_prs()`. Daemon refresher (~45s) keeps timestamps fresh. `AESOP_ROOT` stays real (dist must resolve). Honesty: BANNER_HTML + `"demo": true` in /api/state. No-op in default mode. Optional `AESOP_DEMO_ROOT` (tests).

**bench_panel.py**: Benchmark API routes (`/api/bench`, `/api/bench/compare`). Reads `bench_results_cache` at call time.

**api/__init__.py**, **api/tracker.py**, **api/submit.py**: Mutation handlers (tracker CRUD, inbox append).

## Frontend (React 18 + Vite + TypeScript)

**ui/web/src/**:
- **main.tsx**: Vite entry point; renders `<App />` to `#root`.
- **App.tsx**: App shell; hash-routed views (/#/, /#/work, /#/activity, /#/cost).
- **styles/tokens.css** + **global.css**: Design tokens (light/dark palettes, spacing, typography).
- **views/**: Overview, Work, Activity, Cost, WavePRBoard (with SSE bindings). WavePRBoard polls `/api/wave/prs` every 5s; drills down to FailureDrilldown on click.
- **components/**: HealthHeader, AgentsPanel, TrackerBoard, Timeline, CostChart, CostAnalyticsPanel, FailureDrilldown, BenchmarkPanel, etc.
  - BenchmarkPanel: Results table (model/accuracy/tokens/latency/cost/timestamp) + model comparison cards; fetches `/api/bench` and `/api/bench/compare`; dark/light theme, responsive grid.
  - CostAnalyticsPanel (wave-29 UX): info-dense operator view with (a) spend per wave (bar chart), (b) model efficiency vs Opus counterfactual, (c) burn rate + end-of-wave projection with ceiling alert; graceful DATA-UNAVAILABLE states when ledger/ceiling missing.
  - FailureDrilldown: drawer showing CI job list + ~100-line log excerpts on expand; fetches `/api/wave/failure?pr=N`.
- **lib/api.ts**: Typed fetch helpers + CSRF header injection + `/api/session` fallback for dev server.
- **lib/useSSE.ts**: EventSource hook with reconnect logic, per-section state, connection status.
- **lib/types.ts**: TypeScript types for all API payloads (backend contract).
- **lib/sanitizeUrl.ts**: XSS-safe URL parsing (inerts PR links on bad schemes).
- **vite.config.ts**: Vite config with API proxy to :8770.
- **dist/**: Built static files (committed to git; served by Python handler). Content-hashed by Vite.

**testids-in-fixtures pattern** (both Python + React): Test components with `data-testid` attributes. React tests use `getByTestId()` (via `@testing-library/react`). Python tests use fixtures to set testids for integration proofs.

## API Routes (complete list)

**Read-only (no CSRF required)**:
- `GET /` — Renders `ui/web/dist/index.html` with CSRF token (hard 500 if dist missing).
- `GET /data` — Dashboard data snapshot. `GET /assets/*` — Static files (content-hashed, immutable cache, path-traversal-safe).
- `GET /api/state` — First-paint snapshot: `{data, backlog, agents, tracker, status, cost}` in one round trip.
- `GET /api/session` — Returns `{token}` for Vite dev server (Origin-checked fail-closed, local only).
- `GET /api/cost` — Cost/scorecard from ledger. `GET /api/backlog` — Audit backlog by tier.
- `GET /api/agents` — Fleet agent list. `GET /api/agent?id=<id>` — Agent detail (path-traversal-safe).
- `GET /api/tracker` — Tracker items (optional `status`/`priority` filters).
- `GET /api/wave/prs` — PR board with CI rollup (cached ~5s; degrades `{available:false}`).
- `GET /api/wave/telemetry` — Phase + blocker + cost metrics. `GET /api/wave/dispatch` — Per-agent phase visibility (polled 2-3s).
- `GET /api/wave/failure?pr=N` — CI failure drill-down with log excerpts (cached ~5s).
- `GET /api/bench` — Latest benchmark results from journal. `GET /api/bench/compare` — Model comparison data.
- All `/api/wave/*` routes: read-only, call-time reads, polled not SSE; gh-backed honor `AESOP_GH_BIN`.
- `GET /events` — SSE stream (6 sections, keepalive ~15s). `GET /favicon.ico` — 204.

**Mutations (CSRF-gated)**:
- `POST /submit` — Append to inbox (X-Aesop-Token + Origin/Referer validation, fail-closed).
- `POST /api/tracker` — Create tracker item.
- `POST /api/tracker/<id>` — Update/delete tracker item (query param: `action=update` or `action=delete`).

## CSRF & Session Protection (Invariants)

- Token model: Per-session 43-char URL-safe base64 (256 bits), generated at startup, persisted to `state/.ui-session-token` (mode 0600). Persistent across server restarts; regenerated if missing.
- Validation on `/submit POST`:
  1. **Origin/Referer check**: if present, must be local (http://127.0.0.1:<port>, http://localhost:<port>, or http://[::1]:<port>).
  2. **X-Aesop-Token header**: must match `SESSION_TOKEN` exactly.
  - Both checks fail-closed (missing header = rejection).
- Local CLI access: tools read token from `state/.ui-session-token` (0600) for `/submit`.
- Browser clients: token injected into HTML template, sent by JavaScript via `api.ts` helpers.

## SSE (Server-Sent Events) Contract

- Realtime streaming via `GET /events` (ThreadingHTTPServer required).
- Streams JSON frames: `event: <section>` / `data: <json>` (one section per frame).
- Keepalive comment-line (`: keepalive`) every ~15s (tunable: `SSE_KEEPALIVE_SECONDS`).
- 6 Sections emitted (only on content change, in this order):
  1. **data** — heartbeat, daemon state, log tail.
  2. **backlog** — `AUDIT-BACKLOG.md` parsed into tiers (P0/P1/P2/Needs decision).
  3. **agents** — fleet agent activity (from Claude transcripts directory).
  4. **tracker** — tracker items (CRUD mutations reflected realtime).
  5. **status** — orchestrator phase + activity.
  6. **cost** — cost/scorecard summary.
- Background collector thread: daemon polling heartbeats/logs (mtime/fingerprint gates), re-derives SSE sections only on input change, broadcasts only when content-hash differs. Started on first HTTP request via `start_collector_thread()`, single-instance guard `_collector_lock`, wakes every `COLLECTOR_INTERVAL` (default 1.0s, env override: `AESOP_UI_COLLECT_INTERVAL`).
- If collector thread crashes, server continues serving; realtime updates stop but dashboard remains accessible (fail-open).

## State Store Integration (Wave-15)

**Dual-path mutation model** backed by event-sourced SQLite WAL:
- **Write path**: Mutations append events to `tracker_events.db` (WAL mode) via StateAPI layer (`state_store/api.py`). Each mutation (create/update/delete) produces immutable event.
- **Read path**: `tracker.json` re-rendered as export (projection) from event log on every read. Export committed to git for checkpoint durability.
- **Location**: `state/tracker_events.db` (SQLite WAL, never committed). `tracker.json` (rendered export, committed).
- **Render-failure recovery**: If projection rendering fails, UI falls back to last-known good `tracker.json`; no data loss. Stale renders repaired on next successful mutation (idempotent re-render).

## Configuration & Path Precedence

Environment variables override config file, which overrides built-in defaults:
1. **Environment variables** (highest priority):
   - `PORT` — HTTP server port (default: 8770).
   - `AESOP_ROOT` — aesop installation root (default: `$HOME/aesop`).
   - `AESOP_STATE_ROOT` — state directory (overrides config `state_root`).
   - `AESOP_TRANSCRIPTS_ROOT` — Claude transcript directory (overrides config `transcripts_root`).
   - `AESOP_UI_COLLECT_INTERVAL` — collector thread poll cadence in seconds (default: 1.0).
   - `AESOP_GH_BIN` — gh CLI binary path (default: `gh` on PATH).
   - `AESOP_AUDIT_BACKLOG` — override `AUDIT-BACKLOG.md` path (default: `AESOP_ROOT/AUDIT-BACKLOG.md`); used by demo mode.
   - `AESOP_DEMO` / `AESOP_DEMO_ROOT` — demo mode toggle + optional fixed demo root (see demo.py; normally set by the `--demo` flag).

2. **Config file** (`aesop.config.json`):
   - `state_root` — path to state/ directory.
   - `transcripts_root` — path to Claude transcript directory.
   - `aesop_root` — override derived AESOP_ROOT (tier 3).
   - `pricing` — optional {model: cost_per_mtok} map for dollar estimates.

3. **Built-in defaults** (lowest priority):
   - `AESOP_ROOT/state` for state directory.
   - `~/.claude/projects` for transcripts.

## Build & Deployment (Wave-14 U9 rule)

**dist/ rebuild**: React app must be built (`cd ui/web && npm run build`) BEFORE dist is served. Wave-14 cutover: `ui/web/dist/index.html` is **always required** (no fallback to legacy template). **Orchestrator tail (last agent in fleet) rebuilds dist on every wave close** (before merge to main) so main is always deployable. Dev workflow: run `npm run dev` from `ui/web/` to use Vite dev server (vite.config.ts proxies `/api`, `/events`, `/submit` to :8770).

## Test Commands

**Python backend** (pytest via unittest):
```bash
# All UI tests
python -m unittest discover -s tests

# Specific suite
python -m unittest tests.test_ui_config -v
python -m unittest tests.test_ui_handlers -v
python -m unittest tests.test_wave13_ui_correctness -v
python -m unittest tests.test_ui_collectors -v
python -m unittest tests.test_ui_cost -v
python -m unittest tests.test_ui_hardening -v
```

**React frontend** (vitest + jsdom):
```bash
cd ui/web
npm test
# Or filtered: npx vitest run src/components/TrackerBoard.test.tsx
```

**Integration/browser tests** (playwright, if available):
```bash
# From repo root (requires playwright install: npm install @playwright/test)
npx playwright test
# Run one test: npx playwright test tests/ui-integration.spec.ts
# Headed mode (debug): npx playwright test --headed
```

**Full suite**: `npm run test:py && npm run test:node && npm run test:all`

## Invariants & Gotchas

- **Stdlib-only backend**: `http.server`, `json`, `subprocess`, `threading` only. ThreadingHTTPServer required (SSE = 1 thread/client).
- **Collector fail-open**: crash → server stays up, realtime updates stop.
- **Token**: 0600 (Unix) / ACL (Windows); `state/.ui-session-token` gitignored, regenerated if missing.
- **Config at call time**: `import config` + `config.X`; never `from config import X` (breaks test isolation).
- **Dist always required**: missing = hard 500. Vite content-hashed assets. Dev: `npm run dev` from `ui/web/`.

## Dropped
- Wave-14 rewrite plan (separate docs). Legacy template (wave-9). MCP/state_store internals (own domains).

Map of all domains: /CLAUDE.md
