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

- `agent_prompt_hygiene.py` — Gate detecting forbidden patterns in agent/dispatch prompt templates
- `alert_bridge.py` — Slack/Discord webhook bridge for SECURITY-ALERTS
- `bash_guard_check.py` — BASH_SOURCE exec guard validator for shell scripts; detects missing guards in scripts with functions + top-level commands
- `bench_api_runner.py` — Bench v2+v3 via Anthropic API (BENCH_API_KEY, API-only per bench-no-cli-fallback rule); reuses bench_runner machinery; CLI: `bench_api_runner.py <v2|v3|all> <model...>`
- `bench_results_cache.py` — Append-only benchmark results journal (state/bench-runs.jsonl); idempotent dedup by model+timestamp; stdlib-only
- `bench_runner.py` — Held-out benchmark runner + scorer (Haiku/Sonnet/Opus pluggable)
- `fixture_intent_check.py` — Deliberately-broken fixture manifest validator; verifies bench/fixtures-intent.json tracks all intentionally-broken/incomplete fixtures to distinguish benchmarks from regressions; CLI: `[--manifest PATH] [--root DIR] [--json]`; exit 0=valid/1=findings/2=error; stdlib-only
- `build_static_dash.py` — Build a static, self-contained snapshot of the dashboard with demo data for GitHub Pages; starts demo server, captures API state, produces _site/ with fetch/EventSource shim; CLI: `--output DIR`
- `buildlog.py` — Uniform BUILDLOG.md appender (writes via state_store WriteAPI: entry also lands as buildlog_entry event)
- `chaos_harness.py` — Chaos-wave resilience harness: offline deterministic fault injection (worker kill, checkpoint corruption, planted secret, heartbeat stall, forced red test) with detection/recovery measurement; CLI: `--offline [--state-root DIR] [--output REPORT.md] [--json REPORT.json]`
- `claudemd_contract.py` — Domain CLAUDE.md contract validator (purpose statement, key sections, non-empty); fail-closed exit 1 on violation, 2 on usage error
- `ci_merge_wait.py` — CI-gated merge helper (polls gh pr view until SUCCESS; fail-closed: empty rollup=PENDING, --expect-checks requires ALL named checks present AND concluded, --allow-no-checks escape hatch)
- `ci_shard_runner.py` — Shard-aware Python test runner (distributes tracked test files across N shards round-robin; spawn-safe with __main__ guard; used by ci and windows-shard jobs)
- `ci_gate_runability.py` — CI gate-runability validator (Guardrail G2.5): prevents "green can mean never ran" incidents by verifying known suite families (Python unit suites via ci_shard_runner.py, npm test:node, run_shell_tests.sh, playwright, verify_*.py, lint/guard gates) are not silently skipped due to branch protection misconfiguration; checks job/step-level if conditions that exclude PRs, continue-on-error on gates, missing file references; CLI: `[--check] [--json] [--root DIR]`; exit 0=clean/1=findings/2=error; stdlib-only; staged (wire into ci.yml after #596)
- `ci_workflow_lint.py` — CI workflow linter (YAML parsing, npm ci lockfile checks, test coverage)
- `crossos_drift.py` — Cross-OS CI drift measurement (Windows vs Linux outcome drift from GitHub Actions history; CLI: `--runs N=10 [--json]`; reports pass rates, divergence set, failing test aggregation; exit 3 on auth failure)
- `commit_lint.py` — Conventional commit message linter (type/scope/length/trailer checks); CLI: `[--message MSG] [--range RANGE] [--json] [--check]`; exit 0=clean/1=violations/2=error; stdlib-only
- `dispatch_lint.py` — Dispatch policy linter (merge automation + security rules); detects forbidden patterns (gh pr merge, --admin/--auto/--no-verify/--force, git stash, credential hunting); `# dispatch-ok` suppression; CLI: `[--check] [--fix] [--json] [PATH]`; exit 0=clean/1=violations/2=error
- `common.py` — Shared utilities (state directory resolution, heartbeat staleness)
- `cost_ceiling.py` — Cost-ceiling checker; trips HALT kill-switch on token limits exceeded
- `cost_forecast.py` — Cost forecasting tool: weighted-moving-average daily burn rate, predicted monthly spend, days-to-ceiling; reads fleet ledger; CLI: `--ceiling DOLLARS [--ledger PATH] [--json] [--check] [--help]`; stdlib-only, fail-closed on unknown flags
- `cost_projection.py` — Live burn-rate observability; projects end-of-wave spend and fires threshold alerts at 70% and 90% of ceiling; CLI: `--projection [--window N] [--json]` or `--check-alerts --wave N [--json]`; idempotent per wave via flag files under state/
- `defect_escape.py` — Haiku code quality telemetry (fix-forward rate, first-try estimate); CLI: `--repo <path> --since <ISO date> [--json]`
- `doctor.js` — Preflight checklist for adopter onboarding (diagnostic checks: config, hooks, CLAUDE.md, state, heartbeats, git identity, secret-scan; exit 0=all pass, 1=failed)
- `ensure_state.py` — Scaffold STATE.md and BUILDLOG.md templates (writes via state_store WriteAPI: scaffold emits state_md_written + buildlog events)
- `eod_sweep.py` — End-of-day safety check (dirty trees, unpushed commits); verdict appended to BUILDLOG.md via state_store WriteAPI (--buildlog filename must be BUILDLOG.md, fail-closed)
- `file_size_lint.py` — Python file size linter (flags oversized modules)
- `fleet.js` — One-shot fleet snapshot (JSON: agents, heartbeats, tracker, orchestrator status; Node STDLIB only)
- `fleet_ledger.py` — Append-only cost ledger with harvest/rotate
- `fleet_prompt_extractor.py` — Extract and deduplicate Agent/Task spawn prompts
- `gen_state_md.py` — STATE.md checkpoint generator from event-sourced state store; reads tracker projection via StateAPI read facade; renders markdown with current status header (ISO timestamp), open tracker items by lane, and next steps; CLI: `[--state-root DIR] [--out PATH]`; exit 0=success / 1=malformed store; deterministic + ASCII-safe
- `git_identity_check.py` — Validate repo git user.name/user.email via --expect-name/--expect-email CLI args OR aesop.config.json identity block; verifies .git/config physically (not config cache)
- `halt.py` — Kill-switch: writes/reads/clears `.HALT` sentinel (daemons/dispatch check it)
- `handoff_proof.py` — Team-handoff proof: crash-only resume demo on the real driver/wave_loop.py engine offline (control vs interrupted+resumed runs must reach identical terminal state); outputs docs/HANDOFF-CERTIFICATE.md + state/handoff-proof-*.json
- `hook_preflight.py` — Interpreter health checker (Guardrail G12): verifies all interpreters required by hooks and daemons are present and executable; detects broken wrappers (e.g. bash stub with missing target); fail-closed (exit 1 if any broken, exit 2 if no checks performed, never exit 0 without checking at least one); CLI: `[--check-file PATH]` (default: check all hooks/ and daemons/); portable (ASCII-only, explicit encoding='utf-8', timeout on subprocess); integrated into pre-push-policy.sh early guard
- `health-score.js` — Readiness score for primed projects (0-100 weighted score: config, git hooks, CLAUDE.md, state writable, daemon heartbeats, git identity, secret-scan runnable)
- `health_score.py` — Readiness score (0-100) for primed projects; CLI: `--cwd <path> [--json]`; checks: config, hooks, CLAUDE.md, writable, heartbeats, git-identity, secret-scan (weighted scoring)
- `health.js`, `healthcheck.py` — Fleet health aggregator (heartbeat/alert/orchestrator status); health.js wraps Python
- `heartbeat.py` — Single-instance loop liveness registry
- `import_cycle_check.py` — AST-based import cycle detector for Python modules
- `import_resolution_check.py` — Guardrail G5: Python import resolution validator (parses staged .py files via AST, resolves imports against repo structure + stdlib, fail-closed on unresolvable modules); catches isolation escapes where agent writes to primary tree with unresolvable imports; CLI: no args (exit 0=all resolvable/1=unresolvable); logs audit trail to state/IMPORT-AUDIT.log; integrated into pre-push-policy.sh after secret_scan
- `inbox_drain.py` — Drain UI inbox submissions
- `init_project.py` — Project scaffolder (`aesop init`): creates CLAUDE.md, config, state dir, CI template, pre-push hook
- `instance_manager.py` — Multi-instance coordination CLI (register/heartbeat/list/claim/release/status); respects AESOP_STATE_ROOT env var for db path; --json flag for JSON output on all subcommands; validates status response is dict (exit 2 on contract violation)
- `incident_report.py` — Incident log generator: mines git history for operational failures (fake-green, ci-drift, test-pollution, flake, conflict, stall, gate-activation, doc-invented); generates docs/INCIDENTS.md table; CLI: `[--repo PATH]` (print) | `--regenerate [--output FILE]` | `--check` (drift exit 1); all output deterministic, idempotent
- `latency_report.py` — Wave latency report generator: parses wave journals/bench results/BUILDLOG timestamps into per-wave, per-phase, and percentile timing breakdowns with explicit estimated-vs-measured caveats; CLI: `[--out docs/LATENCY.md]`
- `launch_tui.py` — Spawn bash TUI script in detached terminal
- `list_test_suites.py` — Generate live test suite inventory: scans filesystem for test files (tests/*.test.mjs, tests/test_*.py, tests/*.test.sh, tests/test_*.sh, tests/test-*.sh, hooks/pre-push-policy.sh --test) and outputs grouped listing with first-line doc summaries; ASCII-safe, deterministic; CLI: `list_test_suites.py [--repo ROOT]`; used in tests/CLAUDE.md docs and CI coverage gates; replaces hand-maintained suite listings (kills conflict magnet in merge conflicts)
- `lock.mjs` — Fail-closed atomic lock (exponential backoff + stale-lock detection)
- `merge_train.py` — Serial or integration-branch merge train: serial mode processes PRs one-at-a-time (update-branch, wait for CI, merge, verify MERGED); integration mode (`-i [BATCH_NAME]`) batches PRs into a local `integrate/<name>` branch, runs CI once, squash-merges, closes superseded PRs

- `metrics_gate.py` — PR gate for hard numeric claims in markdown
- `multi_dispatch.py` — Multi-instance-aware dispatch wrapper (checks file claims before dispatch, releases on completion)
- `mutation_test.py` — Test quality harness via mutation testing (apply code mutations, run tests, report survived mutations as test gaps); CLI: `--target <module.py> --test <test_module.py> [--json]`; exit 0 on valid results (advisory), exit 1 when the sandbox baseline fails (results invalid, fail-closed)
- `orchestrator_status.py` — Atomic orchestrator status updates
- `otel_sink.py` — OpenTelemetry tracing integration (spans/metrics emitter for fleet observability)
- `playwright_common.py` — Shared Playwright harness boilerplate: `free_port()`, `copy_dist()`, `start_server()`, `stop_server()` extracted from verify_*.py to reduce duplication (module for import, not CLI)
- `port_fidelity_check.py` — Validates port/copy/vendor/migrate dispatch prompts require source path, source-unique marker, and independent verification
- `portability_check.py` — Shipped-surface gate: scan for hardcoded personal/environment paths (Windows user paths, POSIX home paths, private-machine tokens 'conductor3'/'matt8'); exit 0 clean / 1 with findings; --json output, --root flag for base directory; stdlib only
- `power_selftest.py` — Health check harness for /power bootstrap
- `prepublish_scan.py` — Pre-publish full history + staged-changes scan gate
- `proposals.mjs` — Proposal lifecycle manager (list/accept/reject via lock.mjs)
- `reproduce.js` — Offline verification suite (mirrors reproduce.yml; REPO/INSTALLED modes; exit 0=pass; exact-match doctor pre-init classification)
- `reconcile.py` — Detect/resolve drift (git STATE.md vs. state_store projection; git-authoritative; --resolve appends to SQLite only, never rewrites git-side state)
- `reconstitute.sh` — Clone/fetch repos from config with security validation
- `rotate_logs.py` — Log rotation utility (size/line thresholds)
- `run_shell_tests.sh` — Glob-based shell test runner: discovers and runs all shell tests sequentially via glob patterns (tests/*.test.sh, tests/test_*.sh, tests/test-*.sh, hooks/pre-push-policy.sh --test); fails fast with clear output; CRLF-safe, no line continuations; CLI: `bash tools/run_shell_tests.sh [REPO_ROOT]`; invoked as npm run test:sh in package.json; replaces hand-maintained explicit test chain (kills conflict magnet)
- `scanner_selftest.py` — Regression harness for secret_scan.py
- `stateapi_lint.py` — StateAPI migration ratchet: AST-scans for direct state-file reads outside state_store/read_api.py facade; violations keyed file@pattern-id against committed baseline (new violation = exit 1; fixed violation = must shrink baseline); `--update-baseline` regenerates (forbidden in CI)
- `state_query.py` — Time-travel state query CLI: temporal/stream/version-range/event-type filters over event-sourced SQLite state store; ASCII table (default), --json, --aggregate modes; reuses StateAPI facade + common.py; stdlib-only, fail-closed on missing DB
- `secret_scan.py` — Pre-push secret/credential detection gate (staged/history/paths); token patterns are word-boundary anchored (`\bsk-`) so a key-shaped substring inside a longer hyphenated word is not reported as a live key
- `self_stats.py` — Git-derived metrics counter + README block generator; author email dynamically fetched from git config (not hardcoded) for repo portability. `extract_model_tier(model_name)` normalizes a model display name: strips a leading `Claude ` prefix, removes parenthetical context hints (e.g. `(1M context)`), and rewrites two known variants (`Opus 5.0` -> `Opus 5`, `Haiku 4` -> `Haiku 4.5`); any other name is returned unchanged. Called by `classify_author()` to label model-authored commits with a tier.
- `session_usage_summary.py` — Aggregate token usage across session transcripts
- `spec_contract_validator.py` — Guardrail G4: AST-scans agent-dispatch call sites (`agent(`/`Agent(`/`Task(`/`subagent_type=`/`agentType=`) in driver/*.py, monitor/*.py, tools/*.py for forbidden flags (--admin/--auto/--force/--no-verify), credential-hunting + env-var allowlist violations, missing isolation marker on file-writing prompts, and advisory role-routing (unknown specialist types); `# contract-ok` inline comment suppresses a call site; CLI: `--check` (default) | `--json` | `--paths DIR_OR_FILE...` | `--root PATH`; stdlib only, ASCII output; exit 0=clean/1=findings/2=error
- `state_rebuild.py` — Rebuild and verify materialized state views (tracker.json, STATE.md) from event store; CI gate via --check mode detects drift
- `shadow_adjudication.py` — Orchestrator-swap shadow wave: replays the ground-truth adjudication corpus (driver/decisions/shadow/) through OrchestratorDriver.decide() on a challenger backend; blind (labels never reach prompts), 40-call cap, scorecard + success-bar to bench/results/; --offline FakeTransport for tests; --live builds the seat from aesop.config.json seats.orchestrator (--model/--config override; hosted seats need their api_key_env, is_local none)
- `seated_shadow_adjudication.py` — Seated variant of the shadow wave (increment 4a): builds context packs from the REAL file brain (STATE.md/tracker) + real cited repo code, routes through the completed OrchestratorDriver.decide() seam; frontier-first + early-abort; measures whether real seated context changes adjudication vs the decontextualized ladder; --offline/--live (seat from seats.orchestrator; --model/--config override), --repeat N, per-model results to bench/results/
- `stall_check.py` — Automated agent transcript stall detector; optional --active-from flag refines STALLED verdict to require both stale mtime AND active task file; --emit-recovery emits JSON advisories; --recovery-dir writes recovery-<agent>.json files (idempotent)
- `status.js` — One-shot fleet status snapshot (watchdog/monitor heartbeat age, dashboard port reachability, git branch and working tree state)
- `subprocess_guard.py` — G6 AST guard for subprocess anti-patterns in tests/ (bare `subprocess.run(['bash', ...])`/`Popen` without explicit `cwd=`, `shell=True`, explicit `cwd=None`, `os.system()`); suppress via `# subprocess-ok` inline comment; CLI: `[--check] [--json] [--paths PATH ...]` (default scan dir: `tests/`); exit 0=clean, 1=findings; stdlib only, ASCII-only output
- `svg_to_png.mjs` — Rasterize SVG to PNG via @resvg/resvg-js (lazy import error handling)
- `test_battery.py` — Local union test battery: runs the 4 harnesses (py/node/sh/ui) as parallel subprocesses with per-harness rc capture, stdin closed, logs to temp; parallel mode sets AESOP_TEST_CHILD_TIMEOUT_MS=90000 for node scaffold children; `--serial` fallback, `--skip <h>`, `--json`; exit 0 only when all harnesses green
- `test_coverage_gaps.py` — Test coverage gap finder (identifies untested modules)
- `todo_tracker.py` — TODO/FIXME/HACK comment tracker for codebase hygiene
- `tracker_autoclose.py` — Tracker zombie-prevention auto-close gate: classifies active items as SHIPPED (merged PR or ownsFiles on origin/main), OPEN (no evidence), or AMBIGUOUS (partial evidence); CLI: `[--check | --apply] [--json] [--skip-gh] [--skip-git]`; --check (DRY RUN, exit 0 if no closable items / 1 if closable found); --apply (auto-close + journal); exit 2 on error; reproduces/fixes 79% zombie-rate escapes (items shipped but in-progress) <!-- metrics-verified: wave-1 /afk tracker reconcile — 15 of 19 active items already shipped = 78.9% -->

- `tracker_reconcile.py` — Tracker zombie reconciliation tool (detects shipped-but-open items)
- `wave_history.py` — Wave history CLI for per-wave event store analysis (guardrail G1): closes items whose linked PRs merged or whose ownsFiles shipped on main; CLI: `[--check | --dry-run]`; exit 0=all resolved, 1=items still open; timezone-aware UTC timestamps
- `tracker_guard.py` — Append-only lane journal + zombie-resurrection fail-closed gate; prevents items in terminal lanes (done/rejected) from re-entering active lanes (ranked/proposed/in-progress/accepted); modes: --seed (bootstrap journal), --check (detect violations, exit 1 if found, default), --enforce (revert zombies to terminal lane); CLI: `tracker_guard.py [--seed | --check | --enforce]`; journaled in state/tracker-journal.jsonl with rotation at 5000 lines
- `transcript_digest.py` — Digest agent-*.jsonl transcripts into compact redacted per-agent briefs (state/ledger/transcripts-brief.jsonl; deterministic, idempotent, strips paths/emails/tokens)
- `claudemd_lint.py` — Lint the domain CLAUDE.md layer: doc-pointers resolve, cited npm scripts exist, runtime/state artifacts not flagged, domain cross-refs prohibited; 4 checks: DOC-POINTER, TEST-CMD, DOMAIN-CROSS-REF (domain CLAUDE.md must not reference other domain CLAUDE.md with directives; parent-child refs allowed), line-count; --json; root CLAUDE.md exempt from cross-ref check
- `claudemd_sync_gate.py` — CLAUDE.md synchronization gate (Guardrail G5): for each domain directory with code changes, verifies the corresponding domain/CLAUDE.md was also modified in the same PR; exempts: test-only changes, docs-only, meta files (stats.json, README.md, CHANGELOG.md, package.json, .nvmrc), .github/ (CI), CLAUDE.md-only changes; CLI: `--check` (default, fail-closed) | `--json` | `--base-ref` [BRANCH] (default main); exit 0=synced, 1=drift, 2=error
- `auto_merge.py` — Batch PR merge tool (list-form subprocess/no shell=True per P2 injection fix; fix-by-default: merge main into broken branches + merge green PRs; `--no-fix`/`--loop`/`--dry-run`/`--json`/`--wait`); continuous polling merge tool; run with `--loop` to continuously merge all green PRs; use merge_train.py for one-shot serial CI-gated queues
- `audit_report.py` — Deterministic markdown audit report aggregator (defect_escape, mutation results, lint/drift findings, ledger verdict rates); --out/--strict/--json inputs from machine outputs only
- `claudemd_drift.py` — Semantic drift detector: CLAUDE.md claims vs disk reality (missing refs, unmapped dirs, dead map entries, absent CLI flags); exit 1 on drift; --json
- `cost_econ.py` — Cost economics metrics (cost-per-LOC, per-merged-PR, per-wave/backlog-item) from stats.json + fleet ledger; shares ui/cost.py pricing; honesty caveats documented in output
- `dead_code_check.py` — AST-based dead code detector (unused functions/classes/imports)
- `dep_graph.py` — Dependency graph analyzer for import relationships
- `docstring_check.py` — AST-based docstring coverage checker for Python modules
- `encoding_lint.py` — Encoding lint: flags `open()` without `encoding=`, and `subprocess.run/check_output/Popen` with `text=True`/`universal_newlines=True` and no `encoding=` (the Windows cp1252 trap that crashed metrics_gate on a binary diff). Ratchets against `.encoding-baseline.json` (`--baseline`, `--update-baseline`) so the existing backlog stays visible without blocking pushes while NEW violations fail closed
- `dash.js` — Launch the web dashboard (spawns python ui/serve.py with configured port from PORT env var, aesop.config.json, or default 8770)
- `wave_backlog_analyzer.py` — Pre-wave backlog risk analyzer (per-item risk_level/estimated_retries from git fix-forward history + tracker lanes); warn-level only, --json
- `wave_templates.py` — Wave-manifest preset generator: instantiate/validate templates/wave-presets/*.json into ready manifests; CLI: `validate [--template saas|data|library|all]` (exits 0=clean / 1=defects per item), `instantiate <preset> --project-name --base-dir [--output FILE]`
- `wave_scorecard.py` — Wave quality scorecard generator (deterministic metrics from on-disk telemetry); computes items dispatched/succeeded, repair rounds, first-try-green rate, tokens + cost by phase/model, agent success by type, retry frequency; CLI: `[--json|--md] [--waves N] [--state-root PATH]` (default ASCII); emits n/a for missing sources; hermetic, stdlib-only
- `verify_*.py` — Browser proofs (Playwright/Chromium; exit 0=proven/1=failed; `--allow-skip` only in truly browserless envs): `verify_dash` realtime SSE dashboard; `verify_activity_filter` Activity-view agent status filter; `verify_agent_inspector` Agent Inspector drawer (/api/agent?id=); `verify_prboard` Wave PR Board (/api/wave/prs); `verify_submit_encoding` /submit UTF-8 inbox bootstrap; `verify_wave_telemetry` wave telemetry components; `verify_failure_drilldown` wave failure drill-down; `verify_dispatch_panel` DispatchPanel (ui/web/dist/ + /api/wave/dispatch); `verify_cost_panel.py` Cost Analytics Panel (spend per wave, model efficiency, burn rate; fixture ledger + pricing); `verify_scorecards` wave quality scorecards panel; `verify_ui_trio` UI trio panels (Gantt Timeline, Audit Tail Stream, Live Reasoning Transparency). Fixture-gated proofs honor AESOP_PROOF_FIXTURES.
- `transcript_replay.py` — Replay post-commit edits from transcripts to recover work
- `transcript_timeline.py` — Extract Write/Edit/Read timeline from transcripts
- `verify_ui_trio_redaction_proof.py` — Offline falsifiability proof: verify that transcript redaction detects leaks (POSIX paths, Windows paths, sk- tokens) when unredacted; exit 0=proof passes / 1=proof failed
- `verify_test_coverage.py` — Guardrail G2: CI gate that verifies all on-disk test files are run by some CI job (prevents fake-green: test files existing but never executed). Discovers: Python (git ls-files tests/test_*.py), Node (tests/*.test.mjs via npm test:node glob), Shell (explicit bash commands in package.json test:sh), Playwright (testMatch pattern in playwright.config.ts). CLI: `--check` (exit 1 if orphans found; CI gate) | `--fix` (suggest how to add orphans) | `--help`; hermetic, stdlib-only; exit 0=all covered, 1=orphans found, 2=error
- `verify_test_suite_count.py` — Test suite count drift gate (auto-verifiable + auto-fixable); CLI: `--check` (fail if counts drift; pre-push gate only -- NOT run by any CI workflow) | `--fix [--dry-run]` (auto-rewrite tests/CLAUDE.md counts to match disk; lanes use this); --repo/--claudemd overrides; idempotent; exit 0=clean/fixed, 1=drift/error; wired into hooks/pre-push-policy.sh via check_test_suite_count() to catch drift locally before push
- `wave_ledger_hook.py` — Orchestrator-tail CLI wrapper to append per-wave telemetry to OUTCOMES-LEDGER.md (idempotent phase appends; validates timestamp for markdown table safety)
- `wave_preflight.py` — Wave-open readiness validator: (1) repo-readiness checks (branch/clean-tree/HALT/heartbeats/tracker JSON parse); (2) backlog validation via --tracker (flags: missing ownsFiles, stale file refs, ownership overlaps, ledger aggregate retry rate); (3) --from-stdin mode reads repo roots from stdin to check multiple repos in one run; --json mode + --state-root/AESOP_STATE_ROOT split from --root; warn-level checks never flip exit 1; advisory tool exit 0 for --tracker mode
- `wave_manifest_lint.py` — Wave manifest preflight validator: (1) file-ownership disjointness (no overlaps via fnmatch glob matching); (2) ownsFiles path existence (new files flagged as INFO); (3) prompt sanity (non-empty + [ISOLATION: sibling worktree] required + [[ALLOW-NON-HAIKU]] warns unless [[ALLOW-SONNET]]/[[ALLOW-OPUS]]); (4) git history churn (14-day commits >3 = WARN); (5) testCmd validation (on PATH or repo-relative script). CLI: `wave_manifest_lint.py <manifest.json> [--json] [--strict] [--root DIR]`. Exit 0=PASS (warnings OK) / 1=FAIL or (--strict) WARN. ASCII+JSON output
- `wave_resume.py` — Mid-wave recovery: parse workflow journal.jsonl + worktree to classify items as completed (files written + tests green) vs remaining, enabling resume from last good phase instead of re-run
- `watch.js` — Launch the watchdog daemon (spawns bash daemons/run-watchdog.sh with inherited stdio in foreground mode)
- `workflow_model_linter.py` — Guardrail G7: workflow model pin linter; AST-scans .js/.mjs files for agent() calls missing explicit model:'haiku' parameter (bypasses PreToolUse hook); suppress via `// model-ok`; CLI: `--check` (default) | `--json` | `--help`; exit 0=clean/1=violations/2=error; stdlib-only
- `watcher_linter.py` — Guardrail G3: watcher/polling anti-pattern linter (mechanizes "no watcher pattern in long runs"); AST-scans tools/monitor/driver/daemons for while-True+sleep loops, watch_/monitor_/poll_-named functions with infinite loops, and subprocess calls inside infinite loops (exempts loops with a break/return/raise/exit -- legitimate bounded poll-until-timeout code is not flagged); string-scans prompt-ish assignments/kwargs/dict-keys for "wait for a monitor/watcher/signal/notification", "poll"/"poll for", "watch for changes" phrasing; suppress via `# watcher-ok` inline comment; CLI: `--check` (default) | `--json` | `--paths DIR...` | `--root DIR`; exit 0=clean/1=findings/2=error; stdlib-only, ASCII output
- `state_md_verifier.py` — Guardrail #1: STATE.md checkpoint-accuracy verifier; parses STATE.md for falsifiable progress claims ("resolved", "pushed", "MERGED") and verifies against on-disk git truth (git status --porcelain for unmerged files, git ls-remote --heads for pushed branches, gh pr view for PR states); exit 0=no contradictions/1=contradictions found/2=error; reports UNVERIFIABLE/SKIP for unparseable/unavailable-tool claims; stdlib-only
- `agent-forensics.sh` — Incident forensics; behavior reconstruction (read-only git plumbing)

## Gates & tests

- `secret_scan.py --staged` — pre-push gate (exit 0=clean/1=findings/2=error; `# secretscan: allow-pattern-docs` pragma)
- `agent-forensics.sh <commit>` — behavior forensics; `--diff <A> <B>` for rules/docs diff
- **Python**: `npm run test:py`; **Shell**: `bash -n tools/*.sh && shellcheck tools/*.sh`; **Node**: `node --check tools/*.mjs`

## Subprocess encoding convention (Guardrail G10)

- `encoding_lint.py` -- flags `subprocess.run`/`Popen` calls that pass `text=True`/`universal_newlines=True` without an explicit `encoding=`. Note it scans the WHOLE repo, not only changed files, so a single violation anywhere blocks every push that touches Python.
- Convention: every subprocess call that decodes output passes `encoding='utf-8'`. Without it Python decodes using the platform default, which on Windows is the ANSI codepage (cp1252); that corrupts non-ASCII tool output and is a known source of Windows-passes/Linux-fails drift.
