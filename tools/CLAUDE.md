# tools/ — Build utilities

Local-only Python (stdlib only, no external deps), bash (POSIX, CRLF-safe).

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Core invariants

- **Never print secrets**: mask as pattern name + masked value only; NEVER output raw credentials/tokens.
- **AESOP_STATE_ROOT**: all heartbeat/ledger/logs use `AESOP_STATE_ROOT` env var (default `./state`) or CLI args; no hardcoded personal paths.
- **Fragment-assembled secrets in tests**: `scanner_selftest.py` concatenates dummy secrets at runtime so pattern text never appears contiguously (self-scan invariant).
- **verify_*.py are mandatory CI gates**: `verify_dash.py`, `verify_submit_encoding.py`, `verify_activity_filter.py`, `verify_agent_inspector.py`, `verify_prboard.py`, `verify_failure_drilldown.py`, `verify_wave_telemetry.py`, `verify_dispatch_panel.py`, `verify_scorecards.py`, `verify_ui_trio.py` are required pre-push gates; use `--allow-skip` only in truly browserless environments (CI must run all).
- **lock.mjs is the ONLY lock implementation**: never reimplement locking in `proposals.mjs` or elsewhere; all proposals/state updates must use fail-closed `lock.mjs` with exponential backoff + stale-lock breaking.

## Tool index (one-liners)

- `alert_bridge.py` — Slack/Discord webhook bridge for SECURITY-ALERTS
- `bench_runner.py` — Held-out benchmark runner + scorer (Haiku/Sonnet/Opus pluggable)
- `buildlog.py` — Uniform BUILDLOG.md appender
- `chaos_harness.py` — Chaos-wave resilience harness: offline deterministic fault injection (worker kill, checkpoint corruption, planted secret, heartbeat stall, forced red test) with detection/recovery measurement; CLI: `--offline [--state-root DIR] [--output REPORT.md] [--json REPORT.json]`
- `ci_merge_wait.py` — CI-gated merge helper (polls gh pr view until SUCCESS; fail-closed: empty rollup=PENDING, --expect-checks requires ALL named checks present AND concluded, --allow-no-checks escape hatch)
- `ci_shard_runner.py` — Shard-aware Python test runner (distributes tracked test files across N shards round-robin; spawn-safe with __main__ guard; used by ci and windows-shard jobs)
- `ci_workflow_lint.py` — CI workflow linter (YAML parsing, npm ci lockfile checks, test coverage)
- `crossos_drift.py` — Cross-OS CI drift measurement (Windows vs Linux outcome drift from GitHub Actions history; CLI: `--runs N=10 [--json]`; reports pass rates, divergence set, failing test aggregation; exit 3 on auth failure)
- `common.py` — Shared utilities (state directory resolution, heartbeat staleness)
- `cost_ceiling.py` — Cost-ceiling checker; trips HALT kill-switch on token limits exceeded
- `cost_projection.py` — Live burn-rate observability; projects end-of-wave spend and fires threshold alerts at 70% and 90% of ceiling; CLI: `--projection [--window N] [--json]` or `--check-alerts --wave N [--json]`; idempotent per wave via flag files under state/
- `defect_escape.py` — Haiku code quality telemetry (fix-forward rate, first-try estimate); CLI: `--repo <path> --since <ISO date> [--json]`
- `doctor.js` — Preflight checklist for adopter onboarding (diagnostic checks: config, hooks, CLAUDE.md, state, heartbeats, git identity, secret-scan; exit 0=all pass, 1=failed)
- `ensure_state.py` — Scaffold STATE.md and BUILDLOG.md templates
- `eod_sweep.py` — End-of-day safety check (dirty trees, unpushed commits)
- `fleet.js` — One-shot fleet snapshot (JSON: agents, heartbeats, tracker, orchestrator status; Node STDLIB only)
- `fleet_ledger.py` — Append-only cost ledger with harvest/rotate
- `fleet_prompt_extractor.py` — Extract and deduplicate Agent/Task spawn prompts
- `git_identity_check.py` — Validate repo git user.name/user.email via --expect-name/--expect-email CLI args OR aesop.config.json identity block; verifies .git/config physically (not config cache)
- `halt.py` — Kill-switch: writes/reads/clears `.HALT` sentinel (daemons/dispatch check it)
- `handoff_proof.py` — Team-handoff proof: crash-only resume demo on the real driver/wave_loop.py engine offline (control vs interrupted+resumed runs must reach identical terminal state); outputs docs/HANDOFF-CERTIFICATE.md + state/handoff-proof-*.json
- `health-score.js` — Readiness score for primed projects (0-100 weighted score: config, git hooks, CLAUDE.md, state writable, daemon heartbeats, git identity, secret-scan runnable)
- `health_score.py` — Readiness score (0-100) for primed projects; CLI: `--cwd <path> [--json]`; checks: config, hooks, CLAUDE.md, writable, heartbeats, git-identity, secret-scan (weighted scoring)
- `healthcheck.py` — Fleet health aggregator (heartbeat/alert/orchestrator status)
- `heartbeat.py` — Single-instance loop liveness registry
- `inbox_drain.py` — Drain UI inbox submissions
- `incident_report.py` — Incident log generator: mines git history for operational failures (fake-green, ci-drift, test-pollution, flake, conflict, stall, gate-activation, doc-invented); generates docs/INCIDENTS.md table; CLI: `[--repo PATH]` (print) | `--regenerate [--output FILE]` | `--check` (drift exit 1); all output deterministic, idempotent
- `latency_report.py` — Wave latency report generator: parses wave journals/bench results/BUILDLOG timestamps into per-wave, per-phase, and percentile timing breakdowns with explicit estimated-vs-measured caveats; CLI: `[--out docs/LATENCY.md]`
- `launch_tui.py` — Spawn bash TUI script in detached terminal
- `lock.mjs` — Fail-closed atomic lock (exponential backoff + stale-lock detection)
- `metrics_gate.py` — PR gate for hard numeric claims in markdown
- `mutation_test.py` — Test quality harness via mutation testing (apply code mutations, run tests, report survived mutations as test gaps); CLI: `--target <module.py> --test <test_module.py> [--json]`; exit 0 on valid results (advisory), exit 1 when the sandbox baseline fails (results invalid, fail-closed)
- `orchestrator_status.py` — Atomic orchestrator status updates
- `playwright_common.py` — Shared Playwright harness boilerplate: `free_port()`, `copy_dist()`, `start_server()`, `stop_server()` extracted from verify_*.py to reduce duplication (module for import, not CLI)
- `portability_check.py` — Shipped-surface gate: scan for hardcoded personal/environment paths (Windows user paths, POSIX home paths, private-machine tokens 'conductor3'/'matt8'); exit 0 clean / 1 with findings; --json output, --root flag for base directory; stdlib only
- `power_selftest.py` — Health check harness for /power bootstrap
- `prepublish_scan.py` — Pre-publish full history + staged-changes scan gate
- `proposals.mjs` — Proposal lifecycle manager (list/accept/reject via lock.mjs)
- `reproduce.js` — Offline verification suite (mirrors reproduce.yml; REPO/INSTALLED modes; exit 0=pass; exact-match doctor pre-init classification)
- `reconcile.py` — Detect/resolve drift (git STATE.md vs. state_store projection; git-authoritative; --resolve appends to SQLite only, never rewrites git-side state)
- `reconstitute.sh` — Clone/fetch repos from config with security validation
- `rotate_logs.py` — Log rotation utility (size/line thresholds)
- `scanner_selftest.py` — Regression harness for secret_scan.py
- `stateapi_lint.py` — StateAPI migration ratchet: AST-scans for direct state-file reads outside state_store/read_api.py facade; violations keyed file@pattern-id against committed baseline (new violation = exit 1; fixed violation = must shrink baseline); `--update-baseline` regenerates (forbidden in CI)
- `secret_scan.py` — Pre-push secret/credential detection gate (staged/history/paths)
- `self_stats.py` — Git-derived metrics counter + README block generator
- `session_usage_summary.py` — Aggregate token usage across session transcripts
- `spec_contract_validator.py` — Guardrail G4: AST-scans agent-dispatch call sites (`agent(`/`Agent(`/`Task(`/`subagent_type=`/`agentType=`) in driver/*.py, monitor/*.py, tools/*.py for forbidden flags (--admin/--auto/--force/--no-verify), credential-hunting + env-var allowlist violations, missing isolation marker on file-writing prompts, and advisory role-routing (unknown specialist types); `# contract-ok` inline comment suppresses a call site; CLI: `--check` (default) | `--json` | `--paths DIR_OR_FILE...` | `--root PATH`; stdlib only, ASCII output; exit 0=clean/1=findings/2=error
- `shadow_adjudication.py` — Orchestrator-swap shadow wave: replays the ground-truth adjudication corpus (driver/decisions/shadow/) through OrchestratorDriver.decide() on a challenger backend; blind (labels never reach prompts), 40-call cap, scorecard + success-bar to bench/results/; --offline FakeTransport for tests; --live builds the seat from aesop.config.json seats.orchestrator (--model/--config override; hosted seats need their api_key_env, is_local none)
- `seated_shadow_adjudication.py` — Seated variant of the shadow wave (increment 4a): builds context packs from the REAL file brain (STATE.md/tracker) + real cited repo code, routes through the completed OrchestratorDriver.decide() seam; frontier-first + early-abort; measures whether real seated context changes adjudication vs the decontextualized ladder; --offline/--live (seat from seats.orchestrator; --model/--config override), --repeat N, per-model results to bench/results/
- `stall_check.py` — Automated agent transcript stall detector; optional --active-from flag refines STALLED verdict to require both stale mtime AND active task file; --emit-recovery emits JSON advisories; --recovery-dir writes recovery-<agent>.json files (idempotent)
- `status.js` — One-shot fleet status snapshot (watchdog/monitor heartbeat age, dashboard port reachability, git branch and working tree state)
- `svg_to_png.mjs` — Rasterize SVG to PNG via @resvg/resvg-js (lazy import error handling)
- `test_battery.py` — Local union test battery: runs the 4 harnesses (py/node/sh/ui) as parallel subprocesses with per-harness rc capture, stdin closed, logs to temp; parallel mode sets AESOP_TEST_CHILD_TIMEOUT_MS=90000 for node scaffold children; `--serial` fallback, `--skip <h>`, `--json`; exit 0 only when all harnesses green
- `tracker_guard.py` — Append-only lane journal + zombie-resurrection fail-closed gate; prevents items in terminal lanes (done/rejected) from re-entering active lanes (ranked/proposed/in-progress/accepted); modes: --seed (bootstrap journal), --check (detect violations, exit 1 if found, default), --enforce (revert zombies to terminal lane); CLI: `tracker_guard.py [--seed | --check | --enforce]`; journaled in state/tracker-journal.jsonl with rotation at 5000 lines
- `transcript_digest.py` — Digest agent-*.jsonl transcripts into compact redacted per-agent briefs (state/ledger/transcripts-brief.jsonl; deterministic, idempotent, strips paths/emails/tokens)
- `claudemd_lint.py` — Lint the domain CLAUDE.md layer: doc-pointers resolve, cited npm scripts exist, runtime/state artifacts not flagged; --json (guards one-file-per-domain)
- `audit_report.py` — Deterministic markdown audit report aggregator (defect_escape, mutation results, lint/drift findings, ledger verdict rates); --out/--strict/--json inputs from machine outputs only
- `claudemd_drift.py` — Semantic drift detector: CLAUDE.md claims vs disk reality (missing refs, unmapped dirs, dead map entries, absent CLI flags); exit 1 on drift; --json
- `cost_econ.py` — Cost economics metrics (cost-per-LOC, per-merged-PR, per-wave/backlog-item) from stats.json + fleet ledger; shares ui/cost.py pricing; honesty caveats documented in output
- `dash.js` — Launch the web dashboard (spawns python ui/serve.py with configured port from PORT env var, aesop.config.json, or default 8770)
- `wave_backlog_analyzer.py` — Pre-wave backlog risk analyzer (per-item risk_level/estimated_retries from git fix-forward history + tracker lanes); warn-level only, --json
- `wave_templates.py` — Wave-manifest preset generator: instantiate/validate templates/wave-presets/*.json into ready manifests; CLI: `validate [--template saas|data|library|all]` (exits 0=clean / 1=defects per item), `instantiate <preset> --project-name --base-dir [--output FILE]`
- `wave_scorecard.py` — Wave quality scorecard generator (deterministic metrics from on-disk telemetry); computes items dispatched/succeeded, repair rounds, first-try-green rate, tokens + cost by phase/model, agent success by type, retry frequency; CLI: `[--json|--md] [--waves N] [--state-root PATH]` (default ASCII); emits n/a for missing sources; hermetic, stdlib-only
- `verify_scorecards.py` — Browser proof for the wave quality scorecards panel (self-hosted test port + fixtures; AESOP_PROOF_FIXTURES gated)
- `transcript_replay.py` — Replay post-commit edits from transcripts to recover work
- `transcript_timeline.py` — Extract Write/Edit/Read timeline from transcripts
- `verify_activity_filter.py` — Browser proof for Activity view agent status filter
- `verify_agent_inspector.py` — Browser proof for Agent Inspector drawer (/api/agent?id=)
- `verify_dash.py` — Browser proof for realtime SSE dashboard
- `verify_cost_panel.py` — Browser proof for Cost Analytics Panel (spend per wave, model efficiency, burn rate; self-hosted test port + fixture ledger + pricing; exit 0=proven, 1=failed, --allow-skip for CI)
- `verify_failure_drilldown.py` — Browser proof for wave failure drill-down feature
- `verify_prboard.py` — Browser proof for Wave PR Board (/api/wave/prs)
- `verify_submit_encoding.py` — Browser proof for /submit UTF-8 inbox bootstrap
- `verify_wave_telemetry.py` — Browser proof for wave telemetry components
- `verify_dispatch_panel.py` — Browser proof for DispatchPanel component (ui/web/dist/ + /api/wave/dispatch; Playwright/Chromium; exit 0=proven, 1=failed, --allow-skip for CI)
- `verify_ui_trio.py` — Browser proof for UI trio panels (Gantt Timeline, Audit Tail Stream, Live Reasoning Transparency; AESOP_PROOF_FIXTURES)
- `verify_ui_trio_redaction_proof.py` — Offline falsifiability proof: verify that transcript redaction detects leaks (POSIX paths, Windows paths, sk- tokens) when unredacted; exit 0=proof passes / 1=proof failed
- `verify_test_suite_count.py` — Test suite count drift gate (auto-verifiable + auto-fixable); CLI: `--check` (fail if counts drift; CI gate) | `--fix [--dry-run]` (auto-rewrite tests/CLAUDE.md counts to match disk; lanes use this); --repo/--claudemd overrides; idempotent; exit 0=clean/fixed, 1=drift/error
- `wave_ledger_hook.py` — Orchestrator-tail CLI wrapper to append per-wave telemetry to OUTCOMES-LEDGER.md (idempotent phase appends; validates timestamp for markdown table safety)
- `wave_preflight.py` — Wave-open readiness validator: (1) repo-readiness checks (branch/clean-tree/HALT/heartbeats/tracker JSON parse); (2) backlog validation via --tracker (flags: missing ownsFiles, stale file refs, ownership overlaps, ledger aggregate retry rate); (3) --from-stdin mode reads repo roots from stdin to check multiple repos in one run; --json mode + --state-root/AESOP_STATE_ROOT split from --root; warn-level checks never flip exit 1; advisory tool exit 0 for --tracker mode
- `wave_manifest_lint.py` — Wave manifest preflight validator: (1) file-ownership disjointness (no overlaps via fnmatch glob matching); (2) ownsFiles path existence (new files flagged as INFO); (3) prompt sanity (non-empty + [ISOLATION: sibling worktree] required + [[ALLOW-NON-HAIKU]] warns unless [[ALLOW-SONNET]]/[[ALLOW-OPUS]]); (4) git history churn (14-day commits >3 = WARN); (5) testCmd validation (on PATH or repo-relative script). CLI: `wave_manifest_lint.py <manifest.json> [--json] [--strict] [--root DIR]`. Exit 0=PASS (warnings OK) / 1=FAIL or (--strict) WARN. ASCII+JSON output
- `wave_resume.py` — Mid-wave recovery: parse workflow journal.jsonl + worktree to classify items as completed (files written + tests green) vs remaining, enabling resume from last good phase instead of re-run
- `watch.js` — Launch the watchdog daemon (spawns bash daemons/run-watchdog.sh with inherited stdio in foreground mode)
- `agent-forensics.sh` — Incident forensics; behavior reconstruction (read-only git plumbing)

## secret_scan.py — Pre-push gate

CLI: `secret_scan.py --staged [--repo PATH]` | `--history [--repo PATH]` | `--from-stdin [--repo PATH]` | `PATH [PATH...]`

Exit: 0=clean, 1=findings, 2=error. Pragma escape (pattern findings only; filenames always fatal):
```
# secretscan: allow-pattern-docs
```

`--from-stdin`: Read newline-delimited file paths from stdin for direct scanning (e.g., `git diff --name-only | secret_scan.py --from-stdin`).

## agent-forensics.sh — Behavior forensics

CLI: `bash tools/agent-forensics.sh <commit>` (print snapshot) | `--diff <commitA> <commitB>` (diff rules/docs)

## Test commands

- **Python**: `npm run test:py` (= `python -m unittest discover -s tests`); a single module: `python -m unittest tests.test_<name>` (tests live in tests/, not tools/; the repo uses unittest, not pytest)
- **Shell**: `bash -n tools/*.sh && shellcheck tools/*.sh` (syntax + linting)
- **Node**: `node --check tools/*.mjs` (syntax validation)
- **Full suite**: `python tools/scanner_selftest.py && python tools/power_selftest.py` (mandatory CI gates)

---

Map of all domains: /CLAUDE.md
