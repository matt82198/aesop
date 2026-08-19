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
- **verify_*.py are mandatory CI gates**: `verify_dash.py`, `verify_submit_encoding.py`, `verify_activity_filter.py`, `verify_agent_inspector.py`, `verify_prboard.py`, `verify_failure_drilldown.py`, `verify_wave_telemetry.py`, `verify_dispatch_panel.py`, `verify_scorecards.py`, `verify_ui_trio.py`, `verify_cost_panel.py`, etc. are required pre-push gates; use `--allow-skip` only in truly browserless environments (CI must run all).
- **lock.mjs is the ONLY lock implementation**: never reimplement locking in `proposals.mjs` or elsewhere; all proposals/state updates must use fail-closed `lock.mjs` with exponential backoff + stale-lock breaking.
- **Merge-queue daemon (merge_queue.py) self-heals on crash**: when a pass crashes mid-batch and leaves the shared worktree checked out on an `integrate/q-*` branch, the next pass detects this in `worktree_is_safe()` and automatically reparks to main before proceeding. No manual intervention needed; stalled passes recover transparently.

## power_selftest.py — Trigger-layer guardrail (Guardrail: half-restored-box detection)

- **check_trigger_layer()**: Detects half-restored boxes (missing scheduled tasks/watchdog/heartbeats).
- **Three-phase validation**: (1) Orchestrator heartbeat staleness (600s threshold, FAIL-CLOSED); (2) Watchdog heartbeat staleness (300s per config); (3) Windows scheduled tasks (Aesop\AesopHeartbeat, Aesop\AesopIdleTick) health check.
- **Graceful degradation**: Fresh clones with no heartbeat directory report `trigger:unconfigured` (WARN, not FAIL). Prevents silent drift rot where STATE.md goes unnoticed for 100+ PRs (proven incident class).
- **Heartbeat location**: `state_root/.heartbeats/orchestrator-heartbeat` (same directory as watchdog-heartbeat, monitor-heartbeat).
- **Exit codes**: FAIL (exit 1) for missing/stale heartbeats when configured; WARN (exit 0) for unconfigured; OK (exit 0) when fresh.
- **Test coverage**: 4 test cases in `tests/test_tools_power_selftest.py`: unconfigured scenario, fresh heartbeat, stale heartbeat, missing heartbeat.

## init_project.py — Worktree support

- **Worktree .git handling**: `resolve_real_git_dir()` detects when `.git` is a FILE (worktree case) and uses `git rev-parse --git-common-dir` to locate the actual git directory.
- **Fallback git dir resolution**: Manual parsing of `.git` file's `gitdir:` pointer if git command fails.
- **Hook installation**: `install_pre_push_hook()` uses resolved git dir to place hooks in the common directory (not worktree), preventing ENOTDIR errors in worktree scenarios.
- **Security**: symlink checks preserved throughout resolution.

## Tool index

The per-tool one-liner index lives in `tools/INDEX.md`, generated from each tool's
`INDEX:` docstring/header line — NOT hand-maintained here (that inline list was the
top merge-queue conflict surface, since every tool-adding PR edited it). To document
a new or changed tool, edit that tool's own `INDEX:` line and run
`python tools/gen_tool_index.py --regenerate`. A tool with no `INDEX:` line fails
closed. `claudemd_lint.py` enforces that `tools/INDEX.md` is byte-identical to the
generator output (hand-edits are rejected).

## Gates & tests
- `secret_scan.py --staged` — pre-push gate (exit 0=clean/1=findings/2=error; `# secretscan: allow-pattern-docs` pragma)
- `agent-forensics.sh <commit>` — incident/behavior forensics, read-only git plumbing; `--diff <A> <B>` for rules/docs diff
- **Python**: `npm run test:py`; **Shell**: `bash -n tools/*.sh && shellcheck tools/*.sh`; **Node**: `node --check tools/*.mjs`
- **Subprocess encoding (G10)**: every `subprocess.run`/`check_output`/`Popen` decoding output passes BOTH an explicit `encoding='utf-8'` AND an explicit error handler — `errors='replace'` unless there is a documented reason for `backslashreplace`/`surrogateescape`. `errors='ignore'` and `errors='strict'` are rejected: a corrupted byte must stay visible as U+FFFD, never silently vanish from a branch name you are about to act on. The encoding alone is only half the rule. The platform default is cp1252 on Windows and corrupts non-ASCII output, but strict UTF-8 decoding of one bad byte (0x97, the cp1252 em-dash, is the usual one) raises inside subprocess's reader THREAD, which never reaches the caller — `result.stdout` silently becomes `None` and the next `.strip()` dies with a meaningless `AttributeError`. That is exactly how the merge queue crashed on 24+ consecutive scheduled passes while this gate reported clean; the handler was prose the linter did not enforce. `encoding_lint.py` now enforces both halves and scans the WHOLE repo, so one violation anywhere blocks every Python-touching push.
