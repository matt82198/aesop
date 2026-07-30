# daemons/ — Watchdog and self-healing daemons

**Purpose**: Long-running backup, push, secret-scan daemon (run-watchdog.sh) ensuring fleet-wide repo safety, plus self-healing supervisor (selfheal.sh) detecting stale sibling heartbeats and triggering safe daemon restarts.

## Universal rules (every domain)
- Feature branch only, never main; every push gated by `python tools/secret_scan.py --staged` exit 0.
- Tests never pollute cwd or global git config; temp dirs only; dummy secrets are runtime-concatenated, never literal.
- In worktrees use ABSOLUTE paths under the worktree for every write.
- Domain docs stay minimal-but-complete; update this file in the same PR as code it describes.

## Files

- **run-watchdog.sh**: Daemon supervisor (1.7K); spawns backup-fleet.sh every 150s with atomic lockfile guard, maintains heartbeat, logs to FLEET-BACKUP.log, posts security alerts via alert_bridge.py (opt-in). Traps INT/TERM cleanly. **BASH_SOURCE exec-guard**: all path/env variables declared INSIDE the guard (sourcing exposes only functions like acquire_lock/release_lock, not env side effects).
- **backup-fleet.sh**: Core backup worker (5K); discovers repos (~/.*, ~/*, ~/dev/*), stashes uncommitted work to backup/* branches, pushes unpushed commits, scans tracked/untracked files for secrets. Blocks push if secret-scan fails. **Set -u pipefail** at top; heartbeat write inside BASH_SOURCE guard (no side effects on source).
- **selfheal.sh**: Self-healing supervisor; monitors heartbeats of run-watchdog.sh and the sibling monitor daemon (CONDUCTOR_ROOT-resolved). On each cycle (~60s), detects stale heartbeats (>600s age) and restarts dead daemons. Single-instance guarded via atomic mkdir. Never kills anything with fresh heartbeat (idempotent). Logs to state/SELFHEAL.log. **BASH_SOURCE exec-guard**: all path/env variables inside guard. CRLF-safe. Supports `--once` mode for testing.
- **run-hidden.vbs**: Windows VBScript launcher (~30 lines); rebuilds quoted command line from WScript.Arguments and runs via WScript.Shell.Run with window style 0 (hidden). Used by install-tasks.ps1 to launch bash commands from Scheduled Tasks without console flash.
- **install-tasks.ps1**: Windows task installer (PowerShell 5.1, ~170 lines); idempotent registration of watchdog/monitor Scheduled Tasks with hidden wscript launcher. Params: BashExe, WatchdogCommand, MonitorCommand, intervals, TaskPrefix, -DryRun, -Uninstall. Actions: wscript.exe //B //Nologo run-hidden.vbs <bash-exe> -lc <cmd>; Trigger: once per interval (default 5m/20m) repeating 10y; Settings: Hidden, IgnoreNew, 1h timeout, StartWhenAvailable.

## State files & contracts (git-ignored)

- `$AESOP_ROOT/state/.watchdog-heartbeat`: Unix epoch seconds; updated by backup-fleet.sh each cycle; used by selfheal.sh to detect staleness (>600s).
- `$AESOP_ROOT/state/.watchdog-lock/`: Atomic lockfile dir (mkdir-based, POSIX atomic). Contains `timestamp` (epoch) and `pid` files; stale if >300s old or process dead (atomic reclaim logic in acquire_lock()).
- `$AESOP_ROOT/state/.selfheal-lock/`: Atomic lockfile dir for selfheal.sh (same pattern as watchdog); prevents concurrent healing attempts.
- `$AESOP_ROOT/state/.watchdog-repos.json`: Per-cycle snapshot [{repo, state: CLEAN|PUSHED|SNAPSHOTTED|BLOCKED, age}] with JSON-escaped repo names (backslash/quote/newline/control chars).
- `$AESOP_ROOT/state/FLEET-BACKUP.log`: Append-only; cycle start/end, repo statuses, secret-scan blocks, monitor staleness signals.
- `$AESOP_ROOT/state/SELFHEAL.log`: Append-only; self-healing cycle start/end, stale heartbeat detection, daemon restart actions (dry-run or real).
- `$AESOP_ROOT/state/SECURITY-ALERTS.log`: Append-only security alerts (read by alert_bridge.py for Slack/Discord webhooks).
- `$AESOP_ROOT/state/.alert-bridge-cursor`: Line number of last sent alert (idempotent dispatch).
- `$AESOP_ROOT/state/.HALT`: Kill-switch sentinel (JSON {reason}); checked at cycle start; any cycle halted logs "HALTED: <reason>" and skips all work until cleared.

## Environment variables

- `AESOP_ROOT` (default: `.`): Project root; prepends to all state/ and tools/ paths.
- `CONDUCTOR_ROOT` (default: sibling of AESOP_ROOT): Conductor3 root; if unset or missing, monitor-related operations skip gracefully (portability).
- `AESOP_WATCHDOG_CYCLE_CMD`: Override backup-fleet.sh invocation (test override); if set, runs as `bash -c "$AESOP_WATCHDOG_CYCLE_CMD"`.
- `AESOP_SELFHEAL_SKIP_RESTART`: If set, selfheal.sh detects stale heartbeats and logs dry-run actions instead of actually restarting daemons (test-only flag).

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
