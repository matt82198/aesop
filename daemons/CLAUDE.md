# daemons/ — Watchdog and self-healing daemons

**Purpose**: Long-running backup, push, secret-scan daemon (run-watchdog.sh) ensuring fleet-wide repo safety, plus self-healing supervisor (selfheal.sh) detecting stale sibling heartbeats and triggering safe daemon restarts.

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Files

- **run-watchdog.sh**: Daemon supervisor (1.7K); spawns backup-fleet.sh every 150s with atomic lockfile guard, maintains heartbeat, logs to FLEET-BACKUP.log, posts security alerts via alert_bridge.py (opt-in). Traps INT/TERM cleanly. **BASH_SOURCE exec-guard**: all path/env variables declared INSIDE the guard (sourcing exposes only functions like acquire_lock/release_lock, not env side effects).
- **backup-fleet.sh**: Core backup worker (5K); discovers repos (~/.*, ~/*, ~/dev/*), stashes uncommitted work to backup/* branches, pushes unpushed commits, scans tracked/untracked files for secrets. Blocks push if secret-scan fails. **Set -u pipefail** at top; heartbeat is written at the END of a completed cycle, never before main() runs -- a heartbeat must attest to work DONE. Written up front, a crash inside main() still left a fresh timestamp, so selfheal.sh saw a healthy age and never restarted the daemon while backups silently halted. Failure paths deliberately skip the write so staleness is detectable.
- **selfheal.sh**: Self-healing supervisor; monitors heartbeats of run-watchdog.sh and the sibling monitor daemon (CONDUCTOR_ROOT-resolved). On each cycle (~60s), detects stale heartbeats (>600s age) and restarts dead daemons. Single-instance guarded via atomic mkdir. Never kills anything with fresh heartbeat (idempotent). Logs to state/SELFHEAL.log. **BASH_SOURCE exec-guard**: all path/env variables inside guard. CRLF-safe. Supports `--once` mode for testing.
- **run-merge-queue.sh**: Merge-queue advancer runner (~140 lines). NOT a daemon and NOT a loop: one invocation runs exactly ONE bounded pass of `tools/merge_queue.py --advance`, and the 5-minute `AesopMergeQueue` Scheduled Task IS the loop. Deliberate -- merging must not depend on a live interactive session (measured green->merge dead time ~31.75 HOURS sessionless vs 9-109 seconds seated). Zero `sleep` calls, zero polling. Checks `.HALT` first via `check_halt_any` over EVERY location halt.py may have written it — `$AESOP_STATE_ROOT/.HALT` then `$AESOP_ROOT/state/.HALT`, deduped so one halt logs once (halted = log + exit 0, no work) — `cd`s to `$AESOP_ROOT` because `gh` resolves the repo from cwd, exports `AESOP_STATE_ROOT`, appends to state/MERGE-QUEUE.log, and propagates the tool's exit code. **BASH_SOURCE exec-guard**: every path/env variable declared INSIDE the guard, so sourcing exposes only resolve_python/log_line/check_halt.
- **run-hidden.vbs**: Windows VBScript launcher (~30 lines); rebuilds quoted command line from WScript.Arguments and runs via WScript.Shell.Run with window style 0 (hidden). Used by install-tasks.ps1 to launch bash commands from Scheduled Tasks without console flash.
- **install-tasks.ps1**: Windows task installer (PowerShell 5.1, ~280 lines); scoped, idempotent registration of watchdog/monitor/merge-queue Scheduled Tasks with hidden wscript launcher. **Scoping & idempotency contract** (wave guard/installer-task-scoping): tasks registered ONLY if explicitly requested via flag OR absent from system; tasks with existing divergent action paths are WARNED and skipped (never silently re-pointed). Params: BashExe, WatchdogCommand, MonitorCommand, MergeQueueCommand, intervals, TaskPrefix, -DryRun, -Uninstall, -EnableAuditLog, -EnableMergeQueue, -All, -Force. Actions: wscript.exe //B //Nologo run-hidden.vbs <bash-exe> -lc <cmd>; Trigger: once per interval (default 5m watchdog / 20m monitor / 5m merge-queue) repeating 10y; Settings: Hidden, IgnoreNew, 1h timeout, StartWhenAvailable. **Scope logic**: default (no flags) → watchdog only; -EnableMergeQueue → merge-queue only; -All → all three; -MonitorCommand → monitor only (combinations additive). **AesopMergeQueue is OPT-IN** (`-EnableMergeQueue` or explicit `-MergeQueueCommand`): merges to main with no interactive session, never a side effect. `-Uninstall` always removes all three. `-Force` overrides divergent-path skip (repair tool only, use with caution). **Divergence comparison reads `(Get-ScheduledTask ...).Actions[0].Arguments`** (plural, the CIM property) — reading `.Argument` (the `New-ScheduledTaskAction` *parameter* name) silently yields `$null`, making every existing task look divergent and the idempotent branch unreachable; `Execute` is always `wscript.exe` so it can never distinguish worktrees. Regression-covered by test_divergent_path_warning_and_skip / test_idempotent_same_path_is_noop / test_force_overrides_divergent_skip, which register throwaway `AesopDiverge*`/`AesopIdem*`/`AesopForce*` tasks (never the three real ones) and clean up in `finally`.

## State files & contracts (git-ignored)

- `$AESOP_ROOT/state/.watchdog-heartbeat`: Unix epoch seconds; updated by backup-fleet.sh each cycle; used by selfheal.sh to detect staleness (>600s).
- `$AESOP_ROOT/state/.watchdog-lock/`: Atomic lockfile dir (mkdir-based, POSIX atomic). Contains `timestamp` (epoch) and `pid` files; stale if >300s old or process dead (atomic reclaim logic in acquire_lock()).
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
- `.HALT`: Kill-switch sentinel (JSON {reason}); checked at cycle start by run-watchdog.sh and run-merge-queue.sh; a halted cycle logs "HALTED: <reason>" and skips all work until cleared. **Read from BOTH `$AESOP_STATE_ROOT/.HALT` and `$AESOP_ROOT/state/.HALT`, first match wins** — that is tools/halt.py's own write precedence (AESOP_STATE_ROOT > config state_root > ./state) plus the historical location. Reading only the latter, as both scripts once did, meant any AESOP_STATE_ROOT other than `$AESOP_ROOT/state` made `halt.py set` write a sentinel no daemon read: `halt.py --status` said HALTED while the merge queue kept merging to main unattended. The kill switch is safety-critical, so it reads every location the writer may have used instead of assuming one. Regression-covered by tests/test-daemon-halt-sentinel.sh.

## Environment variables

- `AESOP_ROOT` (default: `.`): Project root; prepends to all state/ and tools/ paths.
- `CONDUCTOR_ROOT` (default: sibling of AESOP_ROOT): Conductor3 root; if unset or missing, monitor-related operations skip gracefully (portability).
- `AESOP_WATCHDOG_CYCLE_CMD`: Override backup-fleet.sh invocation (test override); if set, runs as `bash -c "$AESOP_WATCHDOG_CYCLE_CMD"`.
- `AESOP_SELFHEAL_SKIP_RESTART`: If set, selfheal.sh detects stale heartbeats and logs dry-run actions instead of actually restarting daemons (test-only flag).
- `AESOP_STATE_ROOT` (default: `$AESOP_ROOT/state`): State directory; exported by run-merge-queue.sh so the advancer's lock/heartbeat/ledger land with the rest of state.
- `AESOP_MERGE_QUEUE_CMD`: Override the `tools/merge_queue.py --advance` invocation (test override); if set, runs as `bash -c "$AESOP_MERGE_QUEUE_CMD"`.

## Invariants & Style

1. **Single-instance guard**: run-watchdog.sh and selfheal.sh each use atomic mkdir ($LOCK_DIR) to prevent concurrent daemons (both daemon and --once modes). Stale-lock recovery at 300s threshold; crashed holder won't wedge forever.
2. **CRLF-safe, no line continuations**: Use POSIX-safe heredocs (no backslash wrapping); scripts must work on Windows/CRLF systems.
3. **Append-only logs**: FLEET-BACKUP.log, SELFHEAL.log only grow; rotate via tools/rotate_logs.py if >20KB.
4. **Secret-scan gate**: `scan_tracked_files()` and `scan_unpushed_commits()` call tools/secret_scan.py; non-0 exit blocks push, marks repo BLOCKED.
5. **Exit codes**: run-watchdog exits 0 on healthy cycle (--once) or clean startup (daemon); exits non-zero on cycle failure (backup step failed). selfheal exits 0 always (trap cleans up); backup-fleet exits 0 even on secret-scan block.
6. **Path dedup via realpath**: Avoids processing symlinked repos twice.
7. **Alert Bridge integration**: After backup-fleet.sh cycles, run-watchdog calls `python tools/alert_bridge.py --scan || true` to post HIGH/CRITICAL alerts and heartbeat staleness. No-op if webhook_url missing in aesop.config.json (opt-in feature). Cursor file ensures idempotent dispatch.
8. **Cycle cadence**: 150s for watchdog backup cycles; 60s for selfheal healing cycles.
9. **Selfheal safety**: Never kills/restarts a daemon with a fresh heartbeat (idempotent). Monitors both local (watchdog) and conductor3 (monitor) heartbeats from aesop state/. Restarts via documented launch command (bash run-watchdog.sh / bash run-monitor.sh) in background.
10. **Windows: tasks must be registered via install-tasks.ps1** (hidden wscript launcher) — never raw bash.exe actions (visible console window flashes every interval).
11. **The scheduler is the loop**: run-merge-queue.sh has no loop, no sleep and no watcher; it runs one bounded pass and exits. Never add polling here or in tools/merge_queue.py — a scheduled actor that waits is just a session with extra steps.
12. **The advancer never bypasses a gate**: no `--admin`, no `--auto`, no force-push, no review-thread resolution, no secret-scan tampering, no model calls. It merges only when `enforce_admins` is asserted AND every required check is green under its own fail-closed bucketing; anything else becomes an exception row.

## Testing

**Run all daemons tests** (hermetic, uses mktemp fixtures):
```bash
bash tests/test-run-watchdog.sh
bash tests/test-run-watchdog-lockguard.sh
bash tests/test-run-watchdog-halt.sh
bash tests/backup-fleet.test.sh
bash tests/test-selfheal.sh
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
