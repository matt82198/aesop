# daemons/ — Watchdog and self-healing daemons

**Purpose**: Long-running backup, push, secret-scan daemon (run-watchdog.sh) ensuring fleet-wide repo safety, plus self-healing supervisor (selfheal.sh) detecting stale sibling heartbeats and triggering safe daemon restarts.

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Files

- **run-watchdog.sh**: Daemon supervisor (1.8K); spawns backup-fleet.sh every 150s with atomic lockfile guard, maintains heartbeat, logs to FLEET-BACKUP.log, calls `tools/git_integrity_check.py --repos-json` on .watchdog-repos.json after each cycle (appends FLEET-BACKUP.log; on DAMAGED repos writes SECURITY-ALERTS.log alert), posts security alerts via alert_bridge.py (opt-in); `run_pr_sweep_throttled()` runs `tools/pr_sweep.py` (session-independent PR actor: arms auto-merge, nudges BEHIND, signals stuck-red/DIRTY) at most once per 15 minutes via the `.pr-sweep-last` stamp, from both the `--once` path and the daemon loop. Traps INT/TERM cleanly. `run_receipt_flush()` runs `tools/receipt_flush.py --repo $AESOP_ROOT` every cycle (both paths; the backstop for the detached pre-push poller; fail-open). `run_task_ingest()` runs `tools/ingest_task_events.py --state-dir "$AESOP_ROOT/state"` each cycle (both paths; fail closed: a non-zero exit is logged as ERROR to FLEET-BACKUP.log, and `--once` exits 1; no python interpreter is an ERROR plus return 1, never a silent return 0; a checkout without the tool is logged as skipped). The daemon's state dir is the one at `$AESOP_ROOT`, so a box whose scheduled task points at `aesop-daemon-runtime` writes its cursor and heartbeat under that tree, not the primary `aesop/state`. Tests: `tests/test-run-watchdog-ingest.sh`. **Halt detection (wave-27 safety fix)**: check_halt() calls `python tools/halt.py --status` as the single source of truth, respecting AESOP_STATE_ROOT env var > aesop.config.json state_root > default; bash never re-derives the sentinel path, eliminating drift from configuration overrides. On the `--once` path, when check_halt fails closed, the smoke prints "WATCHDOG SMOKE: FAILED (halt check error, exit N)" and exits 1, while a genuine `.HALT` prints "WATCHDOG SMOKE: PASSED (halted: HALT sentinel honored, no cycle run)" and exits 0. **BASH_SOURCE exec-guard**: all path/env variables declared INSIDE the guard (sourcing exposes only functions like acquire_lock/release_lock/check_halt, not env side effects).
- **backup-fleet.sh**: Core backup worker (5K); discovers repos (~/.*, ~/*, ~/dev/*), stashes uncommitted work to backup/* branches, pushes unpushed commits, scans tracked/untracked files for secrets. Blocks push if secret-scan fails. **Set -u pipefail** at top; heartbeat is written at the END of a completed cycle, never before main() runs -- a heartbeat must attest to work DONE. Written up front, a crash inside main() still left a fresh timestamp, so selfheal.sh saw a healthy age and never restarted the daemon while backups silently halted. Heartbeat contract (one rule): every COMPLETED cycle writes it -- exit 0 (clean) and exit 3 (BLOCKED, WARN) -- and hard failures (exit 1, e.g. the cycle cannot stage its scratch file or persist `.watchdog-repos.json`) never do, so a stale heartbeat means the cycle did not complete. On exit 3 the blocked repo names are written to `$CONDUCTOR_ROOT/state/.watchdog-blocked`; a clean cycle removes it. Scanner HIGH/FOUND/FATAL lines are appended to FLEET-BACKUP.log via `run_secret_gate` (matched values stay masked by secret_scan.py's `mask_secret`). **Heartbeat location** (2026-10-06 fix): writes to `$CONDUCTOR_ROOT/state/.watchdog-heartbeat` (not AESOP_ROOT) so the shared monitoring infrastructure (GUI, idle_tick, monitor, power_selftest) can read it regardless of worktree or AESOP_ROOT path. **Heartbeat-pollution guard** (2026-10-07 fix, recurrence of the 2026-10-06 incident): CONDUCTOR_ROOT is NO LONGER derived unconditionally as a sibling of AESOP_ROOT. Every worktree (`aesop-wt-*`) and every test fixture is a direct child of the same `$HOME` as the real `~/conductor3`, so the old unconditional `${CONDUCTOR_ROOT:-$(dirname "$AESOP_ROOT")/conductor3}` silently resolved to the REAL fleet-state root for ANY AESOP_ROOT shape, letting test/lane runs overwrite the live heartbeat. Now: explicit `CONDUCTOR_ROOT` always wins; otherwise the sibling is auto-derived ONLY when `basename "$AESOP_ROOT"` is exactly `aesop` (the canonical tree); any other shape skips the heartbeat write entirely (same fail-closed contract as other failure paths). See tests/test-backup-fleet-heartbeat-guard.sh.
- **selfheal.sh**: Self-healing supervisor; monitors heartbeats of run-watchdog.sh and the sibling monitor daemon (CONDUCTOR_ROOT-resolved). On each cycle (~60s), detects stale heartbeats (>600s age) and restarts dead daemons. Single-instance guarded via atomic mkdir. Never kills anything with fresh heartbeat (idempotent). Logs to state/SELFHEAL.log. **BASH_SOURCE exec-guard**: all path/env variables inside guard. CRLF-safe. Supports `--once` mode for testing.
- **run-merge-queue.sh**: Merge-queue advancer runner (~140 lines). NOT a daemon and NOT a loop: one invocation runs exactly ONE bounded pass of `tools/merge_queue.py --advance`, and the 5-minute `AesopMergeQueue` Scheduled Task IS the loop. Deliberate -- merging must not depend on a live interactive session (measured green->merge dead time ~31.75 HOURS sessionless vs 9-109 seconds seated). Zero `sleep` calls, zero polling. **Halt detection (wave-27 safety fix)**: check_halt() calls `python tools/halt.py --status` as the single source of truth, respecting AESOP_STATE_ROOT env var > aesop.config.json state_root > default; bash never re-derives the sentinel path, eliminating drift from configuration overrides. **Fail-closed (mirrors run-watchdog.sh, PR #957)**: box-wide `$CONDUCTOR_ROOT/state/.HALT` is honored by existence with no interpreter; only halt.py exit 0 means "not halted"; any other result (Store-stub exit 49, missing interpreter, traceback) logs `HALT-CHECK-FAILED` and skips the pass. `cd`s to `$AESOP_ROOT` because `gh` resolves the repo from cwd, exports `AESOP_STATE_ROOT` and `AESOP_QUEUE_ROOT` before `cd` to allow merge_queue.py's cwd isolation guard to verify it's in the dedicated queue root, appends to state/MERGE-QUEUE.log, and propagates the tool's exit code. **BASH_SOURCE exec-guard**: every path/env variable declared INSIDE the guard, so sourcing exposes only resolve_python/log_line/check_halt.
- **run-hidden.cs → bin/run-hidden.exe** (exe gitignored): C# launcher, GUI subsystem, starts bash with `CreateNoWindow`, waits, and returns the child exit code. Compiled by install-tasks.ps1 with the .NET Framework `csc.exe` when missing or older than the source; a failed build exits 1 before any task is touched. It is the task action itself, so a missing exe fails the task (fail closed) instead of running visibly. `run-hidden.vbs` is retired: `WScript.Shell.Run(cmd, 0, True)` hides only the first window, and bash.exe still got a console on Windows 11.
- **install-tasks.ps1**: Windows task installer (PowerShell 5.1). Every scheduler call passes `-TaskPath` root (`\`), so a disabled `\Aesop\<name>` copy is never read or written. Action: `bin\run-hidden.exe "<bash-exe>" -lc "<cmd>"`; trigger: interval repeating 10y; settings: Hidden, IgnoreNew, 1h timeout. Params: BashExe, WatchdogCommand, MonitorCommand, MergeQueueCommand, intervals, TaskPrefix, -DryRun, -Plan, -ExistingTasksJson, -Uninstall, -EnableAuditLog, -EnableMergeQueue, -All, -Force. **-Plan** (`-Plan -ExistingTasksJson <file>`) prints JSON per task (name, execute, arguments, interval, logon type, decision) and calls no scheduler cmdlet; the decision is `register`, `unchanged`, or `re-register-because-<field>-diverged`. Ignored same-name tasks outside the root are listed. **AESOP_INSTALL_TASKS_DRY_RUN=1** refuses the real path (registration and -Uninstall); -Plan and -DryRun stay allowed. **Divergence (D45)**: any difference in action.Execute, action.Arguments, principal.LogonType, or interval re-registers, which converges old wscript tasks; there is no warn-and-skip. **Scope**: default watchdog only; -EnableMergeQueue merge-queue only (**OPT-IN**: it merges to main with no interactive session); -All all three; -MonitorCommand monitor only. -Uninstall removes all three. -Force re-registers regardless. Tests: `tests/test_install_tasks.py` (Windows only) drives ONLY -Plan with fixtures and greps itself for the scheduler cmdlet names, so it is the one file that may never name them. `tests/test_install_tasks_stubbed.py` (Windows only) is the ONLY file allowed to stub `Get-ScheduledTask`, `Register-ScheduledTask` and `Unregister-ScheduledTask`: it drives the real registration branch against recording stubs (hermeticity probe, interval-change re-registration, identical-config idempotence) and never sets `AESOP_INSTALL_TASKS_DRY_RUN`. **Live-record reads use the CIM property `Actions[0].Arguments`** (plural; `.Argument` is the `New-ScheduledTaskAction` parameter name and yields `$null`, which would make every existing task look divergent). **Trigger comparison** (e881846942fd): an existing task whose action matches but whose repetition `Interval` or `Duration` differs is re-registered, never reported idempotent; both are parsed from CIM's ISO-8601 strings with `XmlConvert` (`ConvertTo-WholeMinutes`). Regression-covered in plan mode by test_interval_divergence_reregisters and test_duration_divergence_reregisters, and on the real branch by test_interval_change_reports_update_not_idempotent and test_same_interval_and_command_is_idempotent (stubbed file).

## State files & contracts (git-ignored)

- `$CONDUCTOR_ROOT/state/.watchdog-heartbeat`: Unix epoch seconds; updated by backup-fleet.sh at the end of every completed cycle (exit 0 or exit 3); used by selfheal.sh to detect staleness (>600s). selfheal.sh resolves the same path with the same CONDUCTOR_ROOT rule as backup-fleet.sh and SKIPs the watchdog check when it cannot be resolved. Path is shared across all monitoring infrastructure (GUI, idle_tick, monitor) regardless of AESOP_ROOT worktree location.
- `$CONDUCTOR_ROOT/state/.watchdog-blocked`: Present only after a completed cycle with >=1 BLOCKED repo; one blocked repo name per line. Written with the heartbeat on exit 3, removed by a clean cycle. Lets consumers distinguish healthy from healthy-with-blocked.
- `$AESOP_ROOT/state/.watchdog-lock/`: Atomic lockfile dir (mkdir-based, POSIX atomic). Contains `pid` (written first) and `timestamp` (epoch, refreshed every loop iteration by the holder). Reclaimed ONLY when the pid is dead (kill -0 fails), or when there is no pid and the timestamp is >300s old; a live holder is never reclaimed. Reclaim is an atomic `mv` aside, so only one reclaimer wins (f18337dc30c3).
- `$AESOP_ROOT/state/.selfheal-lock/`: Atomic lockfile dir for selfheal.sh (same pattern as watchdog); prevents concurrent healing attempts.
- `$AESOP_ROOT/state/.watchdog-repos.json`: Per-cycle snapshot [{repo, state: CLEAN|PUSHED|SNAPSHOTTED|BLOCKED, age}] with JSON-escaped repo names (backslash/quote/newline/control chars).
- `$AESOP_ROOT/state/FLEET-BACKUP.log`: Append-only; cycle start/end, repo statuses, secret-scan blocks, monitor staleness signals.
- `$AESOP_ROOT/state/SELFHEAL.log`: Append-only; self-healing cycle start/end, stale heartbeat detection, daemon restart actions (dry-run or real).
- `$AESOP_ROOT/state/SECURITY-ALERTS.log`: Append-only security alerts (read by alert_bridge.py for Slack/Discord webhooks).
- `$AESOP_ROOT/state/.alert-bridge-cursor`: Line number of last sent alert (idempotent dispatch).
- `$AESOP_ROOT/state/.merge-queue-lock/`: Atomic lockfile dir for run-merge-queue.sh's advancer pass (owned by tools/merge_queue.py, not the shell). Contains `timestamp` + `pid`; fail-closed on contention (a held lock means the scheduler just retries in 5 minutes), reclaimed only past 600s.
- `$AESOP_ROOT/state/.merge-queue-heartbeat`: Unix epoch seconds; beaten once per advancer pass.
- `$AESOP_ROOT/state/merge-queue/exceptions.jsonl`: Append-only advancer exception ledger; one JSON object per line, `{ts, pr, kind, detail, run_url}`, deduped on (pr, kind, detail) so a re-entrant pass appends nothing.
- `$AESOP_ROOT/state/MERGE-QUEUE.log`: Append-only; per-pass START/END markers and the advancer's stdout.
- `$AESOP_ROOT/state/.pr-sweep-last`: Unix epoch seconds; written after each `tools/pr_sweep.py` invocation; throttles the sweep to once per 15 minutes regardless of the watchdog's own 5-minute cadence.
- `.HALT`: Kill-switch sentinel (JSON {reason}); checked at cycle start by run-watchdog.sh and run-merge-queue.sh via `python tools/halt.py --status` as the single source of truth. Daemon reads halt.py's resolution chain (AESOP_STATE_ROOT env > aesop.config.json state_root > `$AESOP_ROOT/state/.HALT`), so halt.py --status and daemon are always synchronized. A halted cycle logs "HALTED: <reason>" and skips all work until cleared. Box-wide: `$CONDUCTOR_ROOT/state/.HALT` (written by `halt.py set` unless AESOP_STATE_ROOT is set) is honored by run-watchdog.sh from any checkout (2026-10-14 N-01); run-watchdog check_halt fails CLOSED (HALT-CHECK-FAILED = skipped cycle) on any unexpected halt.py exit (F-02). Behavioral proof tests: test-daemon-halt-config-path.sh (AESOP_STATE_ROOT and config overrides), test-run-watchdog-halt-boxwide.sh (operator halt from another checkout, Store-stub exit 49, no interpreter). Prior drift (daemon re-deriving sentinel path) fixed in wave-27.

## Environment variables

- `AESOP_ROOT` (no default for backup-fleet.sh: unset refuses to run): Project root; prepends to all state/ and tools/ paths. backup-fleet.sh never falls back to `.`. The run-watchdog.sh entry point exports it, deriving it from its own script location when unset, never from the current directory.
- `CONDUCTOR_ROOT` (default: sibling of AESOP_ROOT): Conductor3 root; if unset or missing, monitor-related operations skip gracefully (portability).
- `AESOP_WATCHDOG_CYCLE_CMD`: Override backup-fleet.sh invocation (test override); if set, runs as `bash -c "$AESOP_WATCHDOG_CYCLE_CMD"`.
- `AESOP_SELFHEAL_SKIP_RESTART`: If set, selfheal.sh detects stale heartbeats and logs dry-run actions instead of actually restarting daemons (test-only flag).
- `AESOP_STATE_ROOT` (default: `$AESOP_ROOT/state`): State directory; exported by run-merge-queue.sh so the advancer's lock/heartbeat/ledger land with the rest of state.
- `AESOP_MERGE_QUEUE_CMD`: Override the `tools/merge_queue.py --advance` invocation (test override); if set, runs as `bash -c "$AESOP_MERGE_QUEUE_CMD"`.

## Invariants & Style

1. **Single-instance guard**: run-watchdog.sh and selfheal.sh each use atomic mkdir ($LOCK_DIR) to prevent concurrent daemons (both daemon and --once modes). Stale-lock recovery is pid-based (dead holder = reclaimable); a live holder is never reclaimed. selfheal.sh does not respawn run-watchdog.sh while a HALT sentinel exists.
2. **CRLF-safe, no line continuations**: Use POSIX-safe heredocs (no backslash wrapping); scripts must work on Windows/CRLF systems.
3. **Append-only logs**: FLEET-BACKUP.log, SELFHEAL.log only grow; rotate via tools/rotate_logs.py if >20KB.
4. **Secret-scan gate**: `scan_tracked_files()` and `scan_unpushed_commits()` call tools/secret_scan.py; non-0 exit blocks push, marks repo BLOCKED.
5. **Exit codes**: run-watchdog exits 0 on healthy cycle (--once) or clean startup (daemon); exits non-zero on cycle failure (backup step failed). selfheal exits 0 always (trap cleans up); backup-fleet exits 0 on a clean cycle, 3 when >=1 repo is BLOCKED by the secret-scan gate (heartbeat and `.watchdog-blocked` written; run-watchdog treats 3 as WARN and the daemon stays up), and 1 on a hard failure (no heartbeat).
6. **Path dedup via realpath**: Avoids processing symlinked repos twice.
7. **Alert Bridge integration**: After backup-fleet.sh cycles, run-watchdog calls `python tools/alert_bridge.py --scan || true` to post HIGH/CRITICAL alerts and heartbeat staleness. No-op if webhook_url missing in aesop.config.json (opt-in feature). Cursor file ensures idempotent dispatch.
8. **Cycle cadence**: 150s for watchdog backup cycles; 60s for selfheal healing cycles.
9. **Selfheal safety**: Never kills/restarts a daemon with a fresh heartbeat (idempotent). Monitors both local (watchdog) and conductor3 (monitor) heartbeats from aesop state/. Restarts via documented launch command (bash run-watchdog.sh / bash run-monitor.sh) in background.
10. **Windows: tasks must be registered via install-tasks.ps1** (hidden launcher `bin\run-hidden.exe`) — never raw bash.exe actions (visible console window flashes every interval).
11. **The scheduler is the loop**: run-merge-queue.sh has no loop, no sleep and no watcher; it runs one bounded pass and exits. Never add polling here or in tools/merge_queue.py — a scheduled actor that waits is just a session with extra steps.
12. **The advancer never bypasses a gate**: no `--admin`, no `--auto`, no force-push, no review-thread resolution, no secret-scan tampering, no model calls. It merges only when `enforce_admins` is asserted AND every required check is green under its own fail-closed bucketing; anything else becomes an exception row.

## Cadence verification (Windows)

Heartbeats prove a daemon *ran*; they cannot prove it ran at the right *rate*. The refinement
monitor once fired hourly against its 20-minute SLA with every cycle exiting 0 and every
heartbeat fresh at read time -- nothing compared the registered cadence to the defined one.

`tools/task_cadence_check.py` closes that gap: it parses this directory's `install-tasks.ps1`
as the source of truth (binding each `Register-DaemonTask` call site's `-TaskName` /
`-IntervalMinutes` through the param defaults and `${TaskPrefix}` interpolation, so expected
cadences are never hand-copied), then reads live state from `schtasks /query /tn <name> /xml`
and compares. Exit 1 on interval mismatch, unregistered task, or disabled task; exit 2
(fail-closed) on any query/parse failure, which outranks exit 1 so an unevaluable task is
never masked by clean siblings. `<Enabled>` is optional in Task Scheduler XML and its absence
means enabled; `/xml` output is UTF-16 and is decoded by BOM.

Windows-only by construction: on any other platform it prints a `SKIPPED-non-windows` line and
exits 0, so ubuntu CI is unaffected. The intended consumer is the local `/power` self-test
(`power_selftest.py`), which should invoke it alongside the existing heartbeat-staleness
checks. Run it manually after any `install-tasks.ps1` change or after a reboot:
`python tools/task_cadence_check.py [--json]`.

Its tests (`tests/test_task_cadence_check.py`) are fixture-driven and never shell out to
`schtasks` -- the query function is injectable -- so the suite runs identically on both OSes.

## Testing

**Run all daemons tests** (hermetic, uses mktemp fixtures):
```bash
bash tests/test-run-watchdog.sh
bash tests/test-run-watchdog-lockguard.sh
bash tests/test-run-watchdog-halt.sh
bash tests/test-run-watchdog-ingest.sh
bash tests/test-daemon-halt-config-path.sh
bash tests/backup-fleet.test.sh
bash tests/test-selfheal.sh
bash tests/test-backup-fleet-blocked-heartbeat.sh
bash tests/test-selfheal-conductor-heartbeat.sh
```

Or via npm:
```bash
npm run test:sh
```

**Selfheal test coverage**:
- Stale heartbeat detection (missing/corrupt/old timestamps → treated as stale).
- Fresh heartbeat respect (never restart daemons with live heartbeats).
- Append-only logging (each cycle appends cycle start/end + actions).
- Single-instance guard (concurrent invocations lock second one out).
- Dry-run mode (AESOP_SELFHEAL_SKIP_RESTART=1 logs actions without restarting).

Tests never touch real AESOP_ROOT/state; all invocations point at throwaway mktemp dirs. REPO_ROOT locates the script under test only.

## Dropped (reason)
- Alert Bridge details (wave-14 feature; see tools/alert_bridge.py for implementation)
- Config parsing details (see aesop.config.json structure in root CLAUDE.md)
- Per-function implementation details (inlined only contracts/interfaces above)

Map of all domains: /CLAUDE.md
