#!/usr/bin/env bash
# Durable fleet watchdog daemon (runs in a shell window). Ctrl-C to stop.
# Backs up committed + uncommitted fleet work and scans for security issues every 150s.
# Usage: run-watchdog.sh [--once]
#
# Configuration: export AESOP_ROOT=/path/to/aesop before running (defaults to script directory's parent).
# Testing: export AESOP_WATCHDOG_CYCLE_CMD to override backup-fleet.sh invocation
#
# Kill switch (wave-26 safety brake): if AESOP_ROOT/state/.HALT exists (see
# tools/halt.py), every cycle logs "HALTED: <reason>" and skips all work — no
# backup, no push, no scan — until cleared via `python tools/halt.py --clear`.

# Atomic lock acquire: mkdir is atomic (POSIX guarantees)
# Returns 0 if lock acquired, 1 if held by another process, 2 if stale lock reclaimed
acquire_lock() {
  local lock_dir="$1"
  local stale_threshold="$2"

  # Ensure parent directory exists
  mkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true

  # Try to create lock directory atomically (this is atomic on all POSIX systems)
  if mkdir "$lock_dir" 2>/dev/null; then
    # We created it; write timestamp and PID
    date +%s > "$lock_dir/timestamp" 2>/dev/null
    echo $$ > "$lock_dir/pid" 2>/dev/null
    return 0
  fi

  # Lock directory already exists; check if it's stale
  # AUDIT FIX 2: Verify both timestamp AND pid files exist before the stale comparison
  if [ -d "$lock_dir" ]; then
    # Try to read pid file to check if process is still running
    local lock_pid=""
    if [ -f "$lock_dir/pid" ]; then
      lock_pid=$(cat "$lock_dir/pid" 2>/dev/null)
    fi

    # Check if both required files exist
    if [ -f "$lock_dir/timestamp" ] && [ -f "$lock_dir/pid" ] && [ -n "$lock_pid" ]; then
      # Both files present and pid is readable - use timestamp for stale check
      local lock_ts=$(cat "$lock_dir/timestamp" 2>/dev/null)
      local lock_mtime=${lock_ts:-0}
      if [ -z "$lock_ts" ]; then
        echo "empty/corrupt lock timestamp — treating lock as stale" >&2
      fi
      local now=$(date +%s)
      local lock_age=$((now - lock_mtime))

      if [ "$lock_age" -gt "$stale_threshold" ]; then
        # Lock is stale; try to reclaim it
        rm -rf "$lock_dir" 2>/dev/null || true
        if mkdir "$lock_dir" 2>/dev/null; then
          date +%s > "$lock_dir/timestamp" 2>/dev/null
          echo $$ > "$lock_dir/pid" 2>/dev/null
          echo "watchdog lock was stale (${lock_age}s) — reclaimed." >&2
          return 2
        fi
      fi
    else
      # AUDIT FIX 2: Timestamp or pid file missing, or pid is empty - lock is incomplete
      # Reclaim only if we can verify it's abandoned: dead pid OR stale timestamp
      local should_reclaim=0

      # Case 1: pid file exists and is readable - check if process is running
      if [ -f "$lock_dir/pid" ] && [ -n "$lock_pid" ]; then
        if ! kill -0 "$lock_pid" 2>/dev/null; then
          should_reclaim=1
        fi
      fi

      # Case 2: no pid file, but timestamp exists and is old (definitely abandoned)
      if [ $should_reclaim -eq 0 ] && [ -f "$lock_dir/timestamp" ]; then
        local lock_ts=$(cat "$lock_dir/timestamp" 2>/dev/null)
        local lock_mtime=${lock_ts:-0}
        if [ -z "$lock_ts" ]; then
          echo "empty/corrupt lock timestamp — treating lock as stale" >&2
        fi
        local now=$(date +%s)
        local lock_age=$((now - lock_mtime))
        if [ "$lock_age" -gt "$stale_threshold" ]; then
          should_reclaim=1
        fi
      fi

      if [ $should_reclaim -eq 1 ]; then
        # Lock is abandoned - reclaim it
        rm -rf "$lock_dir" 2>/dev/null || true
        if mkdir "$lock_dir" 2>/dev/null; then
          date +%s > "$lock_dir/timestamp" 2>/dev/null
          echo $$ > "$lock_dir/pid" 2>/dev/null
          echo "watchdog lock was stale (incomplete) — reclaimed." >&2
          return 2
        fi
      fi
      # If lock is incomplete but fresh, hold it (process might be in progress)
    fi
  fi

  # Lock is held by another process
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

# Kill switch check (wave-26 safety brake). Returns 0 (bash true) and logs
# "HALTED: <reason>" if a .HALT sentinel exists; returns 1 otherwise.
# Never runs backup/push/scan work when halted — caller must skip the cycle.
#
# The sentinel is looked for in EVERY location tools/halt.py may have written
# it: $AESOP_STATE_ROOT/.HALT first (halt.py resolves AESOP_STATE_ROOT ahead of
# everything else) then $AESOP_ROOT/state/.HALT. Reading only the latter meant
# a human running `halt.py set` under a non-default AESOP_STATE_ROOT wrote a
# sentinel this daemon never read — the abort silently did nothing.
check_halt() {
  local log_file="$1"
  local sentinel=""
  local candidate
  for candidate in "$HALT_SENTINEL" "$HALT_SENTINEL_LEGACY"; do
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then
      sentinel="$candidate"
      break
    fi
  done
  if [ -z "$sentinel" ]; then
    return 1
  fi

  local reason="halted (reason unavailable)"
  if [ -n "$PYTHON_EXE" ]; then
    local parsed
    parsed=$("$PYTHON_EXE" -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    r = data.get("reason")
    if r:
        print(r)
except Exception:
    pass
' "$sentinel" 2>/dev/null)
    if [ -n "$parsed" ]; then
      reason="$parsed"
    fi
  fi

  echo "HALTED: $reason"
  if [ -n "$log_file" ]; then
    echo "[$(date '+%F %T')] HALTED: $reason" >> "$log_file"
  fi
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
      printf 'WATCHDOG SMOKE: PASSED\n'
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
    release_lock "$LOCK_DIR"
    printf 'WATCHDOG SMOKE: PASSED\n'
    exit 0
  fi

  n=0
  while true; do
    n=$((n+1))
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
    if [ -n "$PYTHON_EXE" ]; then
      "$PYTHON_EXE" "$AESOP_ROOT/tools/alert_bridge.py" --scan || true
    fi
    sleep 150
  done
}

# Execution guard: only run a cycle when this script is executed directly,
# not when it is sourced (e.g. `source run-watchdog.sh` in a test harness to
# reuse acquire_lock/release_lock/check_halt without triggering a cycle).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  AESOP_ROOT="${AESOP_ROOT:-$(dirname "$SCRIPT_DIR")}"
  MODE="${1:-daemon}"
  LOCK_DIR="$AESOP_ROOT/state/.watchdog-lock"
  LOCK_STALE_THRESHOLD=300
  # Kill-switch read locations, in tools/halt.py's own write precedence order.
  HALT_SENTINEL="${AESOP_STATE_ROOT:-$AESOP_ROOT/state}/.HALT"
  HALT_SENTINEL_LEGACY="$AESOP_ROOT/state/.HALT"

  # Resolve Python interpreter (portable: prefer python3, fallback to python)
  PYTHON_EXE=""
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_EXE="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_EXE="python"
  fi

  # Resolve conductor3 directory (sibling of AESOP_ROOT); env var override for portability
  CONDUCTOR_ROOT="${CONDUCTOR_ROOT:-$(dirname "$AESOP_ROOT")/conductor3}"
  MONITOR_HB_FILE="$CONDUCTOR_ROOT/monitor/.monitor-heartbeat"
  MONITOR_HB_STALE_THRESHOLD=600

  main "$@"
fi
