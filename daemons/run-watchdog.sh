#!/usr/bin/env bash
# Durable fleet watchdog daemon (runs in a shell window). Ctrl-C to stop.
# Backs up committed + uncommitted fleet work and scans for security issues every 150s.
# Usage: run-watchdog.sh [--once]
#
# Configuration: export AESOP_ROOT=/path/to/aesop before running (defaults to script directory's parent).
# Testing: export AESOP_WATCHDOG_CYCLE_CMD to override backup-fleet.sh invocation
#
# Kill switch (wave-26 safety brake): if $CONDUCTOR_ROOT/state/.HALT (box-wide, written by
# tools/halt.py set) or the per-checkout state/.HALT exists, every cycle logs "HALTED: <reason>"
# and skips all work — no backup, no push, no scan — until cleared via `python tools/halt.py --clear`.
# Fails closed: if the halt status cannot be determined, the cycle is skipped too.

# Lock dir layout (f18337dc30c3): $lock_dir/pid is the holder's pid, written FIRST;
# $lock_dir/timestamp is written second and refreshed by the holder every cycle
# (refresh_lock). A live writer therefore always has a live pid on disk.
write_lock_owner() {
  local lock_dir="$1"
  echo $$ > "$lock_dir/pid" 2>/dev/null || return 1
  date +%s > "$lock_dir/timestamp" 2>/dev/null || return 1
  return 0
}

# Holder heartbeat: called once per daemon loop iteration (including halted ones) so a
# live daemon's lock never looks abandoned. Only the owning pid may refresh it.
refresh_lock() {
  local lock_dir="$1"
  if [ -f "$lock_dir/pid" ] && [ "$(cat "$lock_dir/pid" 2>/dev/null)" = "$$" ]; then
    date +%s > "$lock_dir/timestamp" 2>/dev/null || true
  fi
}

# Atomic lock acquire. Returns 0 acquired, 1 held (live holder, or too fresh to judge),
# 2 stale lock reclaimed. Reclaim rules:
#   - recorded pid ALIVE (kill -0)                       -> held, never reclaimed, however old
#   - recorded pid DEAD                                  -> reclaimed, however fresh
#   - no pid, no timestamp file                          -> held (writer mid-acquire), unless the
#                                                           dir itself is older than stale_threshold
#   - no pid, timestamp empty or non-numeric             -> reclaimed (corrupt)
#   - no pid, timestamp older than stale_threshold       -> reclaimed
acquire_lock() {
  local lock_dir="$1"
  local stale_threshold="$2"

  # Ensure parent directory exists
  mkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true

  # Try to create lock directory atomically (this is atomic on all POSIX systems)
  if mkdir "$lock_dir" 2>/dev/null; then
    write_lock_owner "$lock_dir"
    return 0
  fi

  [ -d "$lock_dir" ] || return 1

  local lock_pid="" lock_ts="" expect_pid="" reason="" reclaim=0 ts_corrupt=0
  if [ -f "$lock_dir/pid" ]; then
    lock_pid=$(cat "$lock_dir/pid" 2>/dev/null)
  fi
  if [ -f "$lock_dir/timestamp" ]; then
    lock_ts=$(cat "$lock_dir/timestamp" 2>/dev/null)
    if ! [[ "$lock_ts" =~ ^[0-9]+$ ]]; then
      ts_corrupt=1
    fi
  fi

  if [[ "$lock_pid" =~ ^[0-9]+$ ]]; then
    if kill -0 "$lock_pid" 2>/dev/null; then
      # Live holder: never reclaim, regardless of timestamp age.
      return 1
    fi
    reclaim=1
    expect_pid="$lock_pid"
    reason="holder pid $lock_pid is dead"
  else
    expect_pid=""
    if [ ! -f "$lock_dir/timestamp" ]; then
      # No pid and no timestamp: a writer may be mid-acquire. Judge only by dir age.
      if [ -n "$(find "$lock_dir" -maxdepth 0 -mmin +$((stale_threshold / 60)) 2>/dev/null)" ]; then
        reclaim=1
        reason="incomplete lock older than ${stale_threshold}s"
      fi
    elif [ "$ts_corrupt" -eq 1 ]; then
      reclaim=1
      reason="corrupt timestamp, no pid"
    elif [ $(( $(date +%s) - lock_ts )) -gt "$stale_threshold" ]; then
      reclaim=1
      reason="no pid, timestamp $(( $(date +%s) - lock_ts ))s old"
    fi
  fi

  if [ "$reclaim" -ne 1 ]; then
    return 1
  fi
  if [ "$ts_corrupt" -eq 1 ]; then
    echo "empty/corrupt lock timestamp — treating lock as stale" >&2
  fi
  if reclaim_lock "$lock_dir" "$expect_pid"; then
    echo "watchdog lock was stale ($reason) — reclaimed." >&2
    return 2
  fi
  return 1
}

# Atomic reclaim: rename the stale dir aside with mv. Only one rename of a given dir can
# succeed, so only one reclaimer proceeds. The moved dir's pid is re-checked against the
# pid we judged stale; if a fresh holder slipped in, its lock is put back untouched.
reclaim_lock() {
  local lock_dir="$1"
  local expect_pid="$2"
  local aside="$lock_dir.reclaim.$$"

  rm -rf "$aside" 2>/dev/null || true
  mv "$lock_dir" "$aside" 2>/dev/null || return 1
  local moved_pid=""
  if [ -f "$aside/pid" ]; then
    moved_pid=$(cat "$aside/pid" 2>/dev/null)
  fi
  if [ "$moved_pid" != "$expect_pid" ]; then
    [ -e "$lock_dir" ] || mv "$aside" "$lock_dir" 2>/dev/null || true
    return 1
  fi
  rm -rf "$aside" 2>/dev/null || true
  if mkdir "$lock_dir" 2>/dev/null; then
    write_lock_owner "$lock_dir"
    return 0
  fi
  return 1
}

# Release lock: verify ownership before removing (P0 fix)
release_lock() {
  local lock_dir="$1"
  if [ -f "$lock_dir/pid" ]; then
    local lock_pid=$(cat "$lock_dir/pid" 2>/dev/null || echo "")
    if [ "$lock_pid" = "$$" ]; then
      rm -rf "$lock_dir" 2>/dev/null
    fi
  fi
}

# Check monitor heartbeat staleness; log to fleet-backup.log if missing or >600s old
# Gracefully skips if CONDUCTOR_ROOT doesn't exist (portability for non-conductor deployments)
check_monitor_staleness() {
  local hb_file="$1"
  local stale_threshold="$2"
  local log_file="$3"
  if [ -z "$hb_file" ] || [ -z "$log_file" ]; then
    return
  fi
  if [ ! -d "$(dirname "$hb_file")" ]; then
    return
  fi
  if [ ! -f "$hb_file" ]; then
    echo "[$(date '+%F %T')] SIGNAL: monitor heartbeat missing ($hb_file)" >> "$log_file"
    return
  fi
  local hb_epoch=$(cat "$hb_file" 2>/dev/null || echo 0)
  if [ -z "$hb_epoch" ] || [ "$hb_epoch" = "0" ]; then
    echo "[$(date '+%F %T')] SIGNAL: monitor heartbeat empty/unreadable" >> "$log_file"
    return
  fi
  local now=$(date +%s)
  local hb_age=$((now - hb_epoch))
  if [ "$hb_age" -gt "$stale_threshold" ]; then
    echo "[$(date '+%F %T')] SIGNAL: monitor heartbeat stale (${hb_age}s > ${stale_threshold}s threshold)" >> "$log_file"
  fi
}

# Receipt flush (tools/receipt_flush.py): posts any spooled receipt whose sha now
# exists on GitHub. Runs on EVERY cycle (no throttle: an empty spool costs nothing,
# and a spool entry is a pending hosted verify-receipt result). The pre-push hook
# spawns a detached poller per spooled push (tools/receipt_autoflush.py); this is
# the backstop for anything that poller missed (process killed, sha slow to appear,
# gh outage). Fail-open: a flush failure is logged, never fails the cycle.
run_receipt_flush() {
  local aesop_root="$1"
  local python_exe="$2"
  local log_file="$3"
  if [ -z "$python_exe" ] || [ ! -f "$aesop_root/tools/receipt_flush.py" ]; then
    return 0
  fi
  local flush_out
  flush_out=$("$python_exe" "$aesop_root/tools/receipt_flush.py" --repo "$aesop_root" < /dev/null 2>&1) || true
  if [ -n "$flush_out" ]; then
    echo "$flush_out" >> "$log_file"
  fi
  return 0
}

# Session-independent PR sweep (tools/pr_sweep.py, STATE.md item 9 residual):
# arms missing GitHub native auto-merge, nudges BEHIND branches (capped at 2),
# and signals stuck-red/DIRTY PRs to the signal-hub queue. Throttled to at
# most once every 15 minutes via a stamp file so it layers cheaply onto the
# existing 5-minute --once cadence without spamming `gh`. Never blocks or
# fails the watchdog cycle (|| true) -- a sweep failure is advisory, not a
# backup-fleet outage.
run_pr_sweep_throttled() {
  local aesop_root="$1"
  local python_exe="$2"
  local log_file="$3"
  local stamp_file="$aesop_root/state/.pr-sweep-last"
  if [ -z "$python_exe" ] || [ ! -f "$aesop_root/tools/pr_sweep.py" ]; then
    return
  fi
  local now_epoch
  now_epoch=$(date +%s)
  local last_epoch=0
  if [ -f "$stamp_file" ]; then
    last_epoch=$(cat "$stamp_file" 2>/dev/null || echo 0)
  fi
  if [ -z "$last_epoch" ]; then
    last_epoch=0
  fi
  if [ $((now_epoch - last_epoch)) -gt 900 ]; then
    "$python_exe" "$aesop_root/tools/pr_sweep.py" >> "$log_file" 2>&1 || true
    echo "$now_epoch" > "$stamp_file"
  fi
}

# Session-independent task-event ingest (tools/ingest_task_events.py): tails
# ~/.claude/state/task-events.jsonl into the state store `task` stream. Fail
# closed: a non-zero exit is logged as ERROR to FLEET-BACKUP.log and returned
# to the caller, which reports it (--once exits 1; daemon keeps its cadence).
# The tool itself writes the heartbeat only on a clean run.
run_task_ingest() {
  local aesop_root="$1"
  local python_exe="$2"
  local log_file="$3"
  # No interpreter means the ingest did not run: fail closed and say so in the log,
  # never a silent return 0. A checkout without the tool is a logged skip.
  if [ -z "$python_exe" ]; then
    echo "[$(date '+%F %T')] ERROR: task-event ingest not run: no python interpreter" >> "$log_file"
    return 1
  fi
  if [ ! -f "$aesop_root/tools/ingest_task_events.py" ]; then
    echo "[$(date '+%F %T')] task-event ingest skipped: $aesop_root/tools/ingest_task_events.py missing" >> "$log_file"
    return 0
  fi
  local ingest_out ingest_rc
  ingest_out=$("$python_exe" "$aesop_root/tools/ingest_task_events.py" --state-dir "${AESOP_STATE_ROOT:-$aesop_root/state}" 2>&1)
  ingest_rc=$?
  echo "$ingest_out" >> "$log_file"
  if [ $ingest_rc -ne 0 ]; then
    echo "[$(date '+%F %T')] ERROR: task-event ingest failed (exit $ingest_rc)" >> "$log_file"
    return $ingest_rc
  fi
  return 0
}

# Kill switch check (wave-26 safety brake). Returns 0 (bash true) and logs
# "HALTED: <reason>" if halt sentinel exists; returns 1 otherwise.
# Never runs backup/push/scan work when halted — caller must skip the cycle.
#
# CRITICAL: Always query python tools/halt.py --status as the single source
# of truth. This respects all halt.py resolution precedence (AESOP_STATE_ROOT env >
# aesop.config.json state_root > default). Bash never re-derives the sentinel path
# — doing so creates drift when state_root is overridden.
# Global HALT_CHECK_ERROR: set to the halt.py exit label ONLY when check_halt fails
# closed (status unknowable). A genuine sentinel leaves it empty. --once reads it so
# the smoke verdict can tell "halted on purpose" apart from "halt check broke".
check_halt() {
  local log_file="$1"
  local halt_py="${2:-${HALT_PY_PATH}}"
  HALT_CHECK_ERROR=""
  local box_sentinel=""
  if [ -n "${CONDUCTOR_ROOT:-}" ]; then
    box_sentinel="$CONDUCTOR_ROOT/state/.HALT"
  fi

  # Trigger 1 (box-wide, operator kill switch): file existence only, needs no interpreter.
  if [ -n "$box_sentinel" ] && [ -e "$box_sentinel" ]; then
    local box_reason
    box_reason=$(sed -n 's/.*"reason"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p' "$box_sentinel" 2>/dev/null | head -n 1)
    if [ -z "$box_reason" ]; then
      box_reason="(unreadable sentinel)"
    fi
    echo "HALTED: $box_reason [box-wide $box_sentinel]"
    if [ -n "$log_file" ]; then
      echo "[$(date '+%F %T')] HALTED: $box_reason [box-wide $box_sentinel]" >> "$log_file"
    fi
    return 0
  fi

  # Trigger 2 (per-checkout): halt.py --status. Exit 0 = not halted; exit 1 with a
  # HALTED: line = halted. Everything else is UNKNOWN and fails closed below.
  local halt_output=""
  local halt_exit=""
  if [ -n "$PYTHON_EXE" ] && [ -n "$halt_py" ]; then
    halt_output=$("$PYTHON_EXE" "$halt_py" --status 2>&1)
    halt_exit=$?
  else
    halt_exit="no-interpreter"
  fi

  if [ "$halt_exit" = "0" ]; then
    return 1
  fi

  case "$halt_output" in
    HALTED:*)
      if [ "$halt_exit" = "1" ]; then
        # halt.py outputs: "HALTED: <reason> (since <timestamp>) [<label>]"
        local reason
        reason=$(echo "$halt_output" | head -n 1 | sed 's/^HALTED: //' | sed 's/ (since.*$//')
        if [ -z "$reason" ]; then
          reason="$halt_output"
        fi
        echo "HALTED: $reason"
        if [ -n "$log_file" ]; then
          echo "[$(date '+%F %T')] HALTED: $reason" >> "$log_file"
        fi
        return 0
      fi
      ;;
  esac

  # Unexpected result (no interpreter, Store stub exit 49, traceback, unknown code):
  # the halt status is unknowable, so fail CLOSED and skip the cycle.
  local fail_msg="HALT-CHECK-FAILED (exit $halt_exit); treating as HALTED, cycle skipped"
  HALT_CHECK_ERROR="$halt_exit"
  echo "$fail_msg"
  if [ -n "$log_file" ]; then
    echo "[$(date '+%F %T')] $fail_msg" >> "$log_file"
  fi
  return 0
}

# Box fence gate: see tools/box_fence.py. Returns 1 (and logs ERROR) only when the fence FAILs.
run_box_fence() {
  local aesop_root="$1" python_exe="$2" log_file="$3" fence_out fence_rc
  if [ ! -f "$aesop_root/tools/box_fence.py" ]; then
    return 0
  fi
  mkdir -p "$(dirname "$log_file")" 2>/dev/null || true
  if [ -z "$python_exe" ]; then
    echo "[$(date '+%F %T')] WARN: box fence not run: no python interpreter" >> "$log_file"
    return 0
  fi
  fence_out=$("$python_exe" "$aesop_root/tools/box_fence.py" --repo "$aesop_root" < /dev/null 2>&1)
  fence_rc=$?
  if [ $fence_rc -ne 0 ]; then
    echo "[$(date '+%F %T')] ERROR: box fence FAILED, watchdog not started (exit $fence_rc): $fence_out" >> "$log_file"
    printf 'WATCHDOG: NOT STARTED (box fence)\n%s\n' "$fence_out" >&2
    return 1
  fi
  case "$fence_out" in *"FENCE WARN"*) echo "[$(date '+%F %T')] WARN: $fence_out" >> "$log_file" ;; esac
  return 0
}

# Main execution — guarded below so sourcing this file (e.g. from tests, to
# reuse acquire_lock/release_lock/check_halt) never runs a cycle as a
# side effect of the source itself.
main() {
  # Try to acquire lock (applies to both --once and daemon modes)
  acquire_lock "$LOCK_DIR" "$LOCK_STALE_THRESHOLD"
  lock_result=$?

  if [ $lock_result -eq 1 ]; then
    echo "watchdog already running — not starting a duplicate."
    exit 0
  fi

  echo "==================================================================="
  echo "  FLEET WATCHDOG DAEMON  ·  backup + ensure-push + scan / 150s"
  echo "  logs: $AESOP_ROOT/state/FLEET-BACKUP.log   ·   Ctrl-C to stop"
  echo "==================================================================="
  echo "[$(date '+%F %T')] === watchdog daemon (shell) STARTED ===" >> "$AESOP_ROOT/state/FLEET-BACKUP.log"
  trap "release_lock \"$LOCK_DIR\"; echo \"[$(date '+%F %T')] === watchdog daemon (shell) STOPPED ===\" >> \"$AESOP_ROOT/state/FLEET-BACKUP.log\"; echo \"stopped.\"; exit 0" INT TERM

  # Allow override of backup cycle command (for testing)
  # Use array to safely handle paths with spaces (P1 fix)
  if [ -n "$AESOP_WATCHDOG_CYCLE_CMD" ]; then
    # Override: run as-is through bash -c
    CYCLE_CMD_ARRAY=("bash" "-c" "$AESOP_WATCHDOG_CYCLE_CMD")
  else
    # Default: array form for proper quoting
    CYCLE_CMD_ARRAY=("bash" "$AESOP_ROOT/daemons/backup-fleet.sh")
  fi

  if [ "$MODE" = "--once" ]; then
    if check_halt "$AESOP_ROOT/state/FLEET-BACKUP.log"; then
      release_lock "$LOCK_DIR"
      # Halt check ERROR (fail-closed, status unknowable): the cycle was skipped
      # because we could not verify the halt state. That is a FAILED smoke, exit 1
      # (same exit convention as the task-event ingest failure below).
      if [ -n "${HALT_CHECK_ERROR:-}" ]; then
        printf 'WATCHDOG SMOKE: FAILED (halt check error, exit %s)\n' "$HALT_CHECK_ERROR" >&2
        exit 1
      fi
      # Genuine HALT sentinel: a halted cycle is a clean no-op by design (the daemon
      # obeyed the kill switch), so PASSED with exit 0. The wording says so explicitly.
      printf 'WATCHDOG SMOKE: PASSED (halted: HALT sentinel honored, no cycle run)\n'
      exit 0
    fi
    full_out=$("${CYCLE_CMD_ARRAY[@]}" 2>&1)
    cmd_exit=$?
    echo "$full_out"
    if [ $cmd_exit -eq 3 ]; then
      # FIX #5 contract (mirrored in daemons/backup-fleet.sh main()): exit 3
      # from the cycle command means "blocked cycle" -- at least one repo was
      # BLOCKED by the secret-scan gate. This is a WARN, not a hard error:
      # log it and fall through to the normal PASSED/exit-0 path below
      # instead of the FAILED/exit-$cmd_exit path used for other non-zero codes.
      warn_msg="[$(date '+%F %T')] WARN: cycle #1 reported blocked repo(s) (secret-scan gate; exit code 3)"
      echo "$warn_msg" >> "$AESOP_ROOT/state/FLEET-BACKUP.log"
    elif [ $cmd_exit -ne 0 ]; then
      err_msg="[$(date '+%F %T')] ERROR: cycle #1 failed with exit code $cmd_exit"
      echo "$err_msg" >> "$AESOP_ROOT/state/FLEET-BACKUP.log"
      printf 'WATCHDOG SMOKE: FAILED — [ERROR: exit %d]\n' "$cmd_exit" >&2
      release_lock "$LOCK_DIR"
      exit $cmd_exit
    fi
    if [ -n "$PYTHON_EXE" ]; then
      "$PYTHON_EXE" "$AESOP_ROOT/tools/alert_bridge.py" --scan || true
    fi
    run_receipt_flush "$AESOP_ROOT" "$PYTHON_EXE" "$AESOP_ROOT/state/FLEET-BACKUP.log"
    run_pr_sweep_throttled "$AESOP_ROOT" "$PYTHON_EXE" "$AESOP_ROOT/state/FLEET-BACKUP.log"
    ingest_rc=0
    run_task_ingest "$AESOP_ROOT" "$PYTHON_EXE" "$AESOP_ROOT/state/FLEET-BACKUP.log" || ingest_rc=$?
    if [ $ingest_rc -ne 0 ]; then
      release_lock "$LOCK_DIR"
      printf 'WATCHDOG SMOKE: FAILED — [task-event ingest exit %d]\n' "$ingest_rc" >&2
      exit 1
    fi
    release_lock "$LOCK_DIR"
    printf 'WATCHDOG SMOKE: PASSED\n'
    exit 0
  fi

  n=0
  while true; do
    n=$((n+1))
    # Refresh the lock every iteration, halted ones included, so a live daemon never
    # looks abandoned to a second instance or to selfheal.
    refresh_lock "$LOCK_DIR"
    if check_halt "$AESOP_ROOT/state/FLEET-BACKUP.log"; then
      sleep 150
      continue
    fi
    full_out=$("${CYCLE_CMD_ARRAY[@]}" 2>&1)
    cmd_exit=$?
    if [ $cmd_exit -eq 0 ]; then
      out=$(echo "$full_out" | tail -2)
      printf '%s  cycle #%d\n%s\n' "$(date '+%H:%M:%S')" "$n" "$out"
    elif [ $cmd_exit -eq 3 ]; then
      # FIX #5 contract (mirrored in daemons/backup-fleet.sh main()): exit 3
      # means "blocked cycle" (>=1 repo BLOCKED by the secret-scan gate) --
      # WARN, not ERROR. The daemon stays up and keeps its normal 150s
      # cadence; only the log level and console label differ from ERROR.
      echo "[$(date '+%F %T')] WARN: cycle #$n reported blocked repo(s) (secret-scan gate; exit code 3)" >> "$AESOP_ROOT/state/FLEET-BACKUP.log"
      out=$(echo "$full_out" | tail -2)
      printf '%s  cycle #%d [WARN: blocked repo(s), exit 3]\n%s\n' "$(date '+%H:%M:%S')" "$n" "$out"
    else
      echo "[$(date '+%F %T')] ERROR: cycle #$n failed with exit code $cmd_exit" >> "$AESOP_ROOT/state/FLEET-BACKUP.log"
      out=$(echo "$full_out" | tail -2)
      printf '%s  cycle #%d [ERROR: exit %d]\n%s\n' "$(date '+%H:%M:%S')" "$n" "$cmd_exit" "$out"
    fi
    check_monitor_staleness "$MONITOR_HB_FILE" "$MONITOR_HB_STALE_THRESHOLD" "$AESOP_ROOT/state/FLEET-BACKUP.log"
    if [ -n "$PYTHON_EXE" ] && [ -f "$AESOP_ROOT/tools/git_integrity_check.py" ] && [ -f "$AESOP_ROOT/state/.watchdog-repos.json" ]; then "$PYTHON_EXE" "$AESOP_ROOT/tools/git_integrity_check.py" --repos-json "$AESOP_ROOT/state/.watchdog-repos.json" >> "$AESOP_ROOT/state/FLEET-BACKUP.log" 2>&1 || echo "[$(date '+%F %T')] SECURITY ALERT: git repository integrity check found DAMAGED repos (exit code $?)" >> "$AESOP_ROOT/state/SECURITY-ALERTS.log"; fi
    if [ -n "$PYTHON_EXE" ]; then
      "$PYTHON_EXE" "$AESOP_ROOT/tools/alert_bridge.py" --scan || true
    fi
    run_receipt_flush "$AESOP_ROOT" "$PYTHON_EXE" "$AESOP_ROOT/state/FLEET-BACKUP.log"
    run_pr_sweep_throttled "$AESOP_ROOT" "$PYTHON_EXE" "$AESOP_ROOT/state/FLEET-BACKUP.log"
    run_task_ingest "$AESOP_ROOT" "$PYTHON_EXE" "$AESOP_ROOT/state/FLEET-BACKUP.log" || true
    sleep 150
  done
}

# Execution guard: only run a cycle when this script is executed directly,
# not when it is sourced (e.g. `source run-watchdog.sh` in a test harness to
# reuse acquire_lock/release_lock/check_halt without triggering a cycle).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # Tracker cebab35aabeb: when unset, the root is derived from this script's own checkout
  # (the scheduled task runs it with no AESOP_ROOT, see daemons/install-tasks.ps1), and the
  # derivation is announced on stderr instead of happening silently. The export below
  # hands the same root to backup-fleet.sh, so the two cannot disagree.
  if [ -z "${AESOP_ROOT:-}" ]; then
    AESOP_ROOT="$(dirname "$SCRIPT_DIR")"
    printf '[%s] NOTICE: AESOP_ROOT unset; derived checkout root %s from script location\n' \
      "$(date '+%F %T')" "$AESOP_ROOT" >&2
  fi
  # Exported (tracker 00fa6e4cb68b): backup-fleet.sh runs as a child and refuses to run
  # without AESOP_ROOT, so the scheduled task's cwd and environment cannot decide
  # which tree's secret scanner gates the push.
  export AESOP_ROOT
  MODE="${1:-daemon}"
  LOCK_DIR="$AESOP_ROOT/state/.watchdog-lock"
  LOCK_STALE_THRESHOLD=300
  # Resolve halt.py from the repo (daemons/ is in same AESOP_ROOT as tools/)
  HALT_PY_PATH="$(dirname "$SCRIPT_DIR")/tools/halt.py"

  # Resolve Python interpreter (portable: prefer python3, fallback to python)
  PYTHON_EXE=""
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_EXE="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_EXE="python"
  fi

  # Live store root: AESOP_STATE_ROOT env wins; otherwise export the aesop.config.json
  # state_root key (tools/state_root.py --config-only) so the watchdog ingests into the
  # same store the boot brief reads. Key unset = no export (legacy $AESOP_ROOT/state).
  # An unreadable config is logged as ERROR, never silently ignored.
  if [ -z "${AESOP_STATE_ROOT:-}" ] && [ -n "$PYTHON_EXE" ] && [ -f "$AESOP_ROOT/tools/state_root.py" ]; then
    cfg_state_root=$(AESOP_ROOT="$AESOP_ROOT" "$PYTHON_EXE" "$AESOP_ROOT/tools/state_root.py" --config-only 2>&1)
    cfg_state_rc=$?
    if [ $cfg_state_rc -ne 0 ]; then
      echo "[$(date '+%F %T')] ERROR: state_root config unreadable (exit $cfg_state_rc): $cfg_state_root" >> "$AESOP_ROOT/state/FLEET-BACKUP.log"
    elif [ -n "$cfg_state_root" ]; then
      export AESOP_STATE_ROOT="$cfg_state_root"
    fi
  fi

  # Resolve conductor3 directory (sibling of AESOP_ROOT); env var override for portability
  CONDUCTOR_ROOT="${CONDUCTOR_ROOT:-$(dirname "$AESOP_ROOT")/conductor3}"
  MONITOR_HB_FILE="$CONDUCTOR_ROOT/monitor/.monitor-heartbeat"
  MONITOR_HB_STALE_THRESHOLD=600

  # Box fence (tools/box_fence.py): a box behind the shared BOX_PROTOCOL must not run. FAIL logs an
  # ERROR line (the monitor surfaces it) and exits 1 before any lock, backup or push. WARN (offline)
  # and PASS continue; a checkout without the tool is a skip.
  run_box_fence "$AESOP_ROOT" "$PYTHON_EXE" "$AESOP_ROOT/state/FLEET-BACKUP.log" || exit 1

  main "$@"
fi
