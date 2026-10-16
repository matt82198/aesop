#!/usr/bin/env bash
# Self-healing fleet supervisor: detects stale sibling heartbeats and restarts daemons.
# Monitors run-watchdog.sh and run-monitor.sh with safe, idempotent recovery actions.
# Usage: selfheal.sh [--once]
#
# Configuration: export AESOP_ROOT=/path/to/aesop before running (defaults to script directory's parent).
# Testing: export AESOP_SELFHEAL_SKIP_RESTART=1 to skip actual daemon restarts (test-only flag).

set -u

ts() {
  date '+%Y-%m-%d %H:%M:%S'
}

log_heal() {
  local msg="$1"
  echo "[$(ts)] $msg" | tee -a "$SELFHEAL_LOG"
}

ensure_log_dir() {
  mkdir -p "$(dirname "$SELFHEAL_LOG")" 2>/dev/null || true
}

# Same lock contract as run-watchdog.sh acquire_lock (tracker f18337dc30c3): pid written
# first, timestamp second, refreshed each cycle. A live recorded pid is never reclaimed;
# a dead one is reclaimed by an atomic mv-aside, so only one reclaimer wins.
write_lock_owner() {
  local lock_dir="$1"
  echo $$ > "$lock_dir/pid" 2>/dev/null || return 1
  date +%s > "$lock_dir/timestamp" 2>/dev/null || return 1
  return 0
}

refresh_lock() {
  local lock_dir="$1"
  if [ -f "$lock_dir/pid" ] && [ "$(cat "$lock_dir/pid" 2>/dev/null)" = "$$" ]; then
    date +%s > "$lock_dir/timestamp" 2>/dev/null || true
  fi
}

acquire_lock() {
  local lock_dir="$1"
  local stale_threshold="$2"

  mkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true

  if mkdir "$lock_dir" 2>/dev/null; then
    if ! write_lock_owner "$lock_dir"; then
      rm -rf "$lock_dir" 2>/dev/null || true
      return 1
    fi
    return 0
  fi

  [ -d "$lock_dir" ] || return 1

  local lock_pid="" lock_ts="" expect_pid="" reclaim=0 ts_corrupt=0
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
      return 1
    fi
    reclaim=1
    expect_pid="$lock_pid"
  else
    expect_pid=""
    if [ ! -f "$lock_dir/timestamp" ]; then
      if [ -n "$(find "$lock_dir" -maxdepth 0 -mmin +$((stale_threshold / 60)) 2>/dev/null)" ]; then
        reclaim=1
      fi
    elif [ "$ts_corrupt" -eq 1 ] || [ $(( $(date +%s) - lock_ts )) -gt "$stale_threshold" ]; then
      reclaim=1
    fi
  fi

  if [ "$reclaim" -ne 1 ]; then
    return 1
  fi
  if reclaim_lock "$lock_dir" "$expect_pid"; then
    return 2
  fi
  return 1
}

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

# HALT sentinel check (tracker f18337dc30c3): selfheal must not respawn a daemon the
# operator halted. Existence is enough (no interpreter needed). Checks the box-wide
# $CONDUCTOR_ROOT sentinel, the per-checkout state sentinel and AESOP_STATE_ROOT's.
# Prints the sentinel path and returns 0 when one exists.
halt_sentinel_present() {
  local candidate
  for candidate in \
    "${AESOP_STATE_ROOT:+$AESOP_STATE_ROOT/.HALT}" \
    "$AESOP_ROOT/state/.HALT" \
    "${CONDUCTOR_ROOT:+$CONDUCTOR_ROOT/state/.HALT}"; do
    if [ -n "$candidate" ] && [ -e "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

release_lock() {
  local lock_dir="$1"
  rm -rf "$lock_dir" 2>/dev/null || true
  if [ -d "$lock_dir" ]; then
    ensure_log_dir
    log_heal "WARN: release_lock: failed to remove lock dir: $lock_dir (may be in-use on Windows)"
  fi
}

is_heartbeat_stale() {
  local hb_file="$1"
  local stale_threshold="$2"

  if [ ! -f "$hb_file" ]; then
    return 0
  fi

  local hb_ts=$(cat "$hb_file" 2>/dev/null)
  if [ -z "$hb_ts" ] || ! [[ "$hb_ts" =~ ^[0-9]+$ ]]; then
    return 0
  fi

  local now=$(date +%s)
  local age=$((now - hb_ts))

  if [ "$age" -gt "$stale_threshold" ]; then
    return 0
  fi

  return 1
}

restart_watchdog() {
  ensure_log_dir

  # An unresolved heartbeat path (non-canonical AESOP_ROOT, no CONDUCTOR_ROOT)
  # must not read as a stale watchdog: a missing file is "stale" to
  # is_heartbeat_stale, which would restart the daemon on every cycle.
  if [ -z "$WATCHDOG_HB" ]; then
    log_heal "SKIP: watchdog heartbeat path unresolved (CONDUCTOR_ROOT not set for non-canonical AESOP_ROOT)"
    return 1
  fi

  if is_heartbeat_stale "$WATCHDOG_HB" "$WATCHDOG_STALE_THRESHOLD"; then
    # A halted daemon writes no heartbeat, so it always looks stale. Respawning it every
    # 60s while HALT is set only leaks processes; the operator's kill switch wins.
    local halt_path
    if halt_path=$(halt_sentinel_present); then
      log_heal "SKIP: HALT sentinel present ($halt_path); not spawning watchdog"
      return 1
    fi
    if [ -z "${AESOP_SELFHEAL_SKIP_RESTART:-}" ]; then
      log_heal "ACTION: run-watchdog heartbeat stale, restarting..."
      if command -v bash >/dev/null 2>&1; then
        bash "$AESOP_ROOT/daemons/run-watchdog.sh" >/dev/null 2>&1 &
        log_heal "RESTART watchdog: spawned (PID: $!)"
      else
        log_heal "WARN: Cannot restart watchdog, bash not found"
      fi
    else
      log_heal "DRY-RUN: watchdog heartbeat stale (would restart)"
    fi
    return 0
  fi

  return 1
}

restart_monitor() {
  ensure_log_dir

  if [ ! -d "$CONDUCTOR_ROOT" ]; then
    return 1
  fi

  if is_heartbeat_stale "$MONITOR_HB" "$MONITOR_STALE_THRESHOLD"; then
    if [ -z "${AESOP_SELFHEAL_SKIP_RESTART:-}" ]; then
      log_heal "ACTION: run-monitor heartbeat stale, restarting..."
      if command -v bash >/dev/null 2>&1; then
        bash "$CONDUCTOR_ROOT/monitor/run-monitor.sh" >/dev/null 2>&1 &
        log_heal "RESTART monitor: spawned (PID: $!)"
      else
        log_heal "WARN: Cannot restart monitor, bash not found"
      fi
    else
      log_heal "DRY-RUN: monitor heartbeat stale (would restart)"
    fi
    return 0
  fi

  return 1
}

cycle_once() {
  ensure_log_dir
  log_heal "=== Selfheal cycle START ==="

  local any_action=0

  restart_watchdog
  if [ $? -eq 0 ]; then
    any_action=1
  fi

  restart_monitor
  if [ $? -eq 0 ]; then
    any_action=1
  fi

  if [ $any_action -eq 0 ]; then
    log_heal "No stale heartbeats detected"
  fi

  log_heal "=== Selfheal cycle END ==="
}

main() {
  lock_result=0
  acquire_lock "$SELFHEAL_LOCK_DIR" "$SELFHEAL_STALE_THRESHOLD"
  lock_result=$?

  if [ $lock_result -eq 1 ]; then
    exit 0
  fi

  trap "release_lock \"$SELFHEAL_LOCK_DIR\"; exit 0" INT TERM EXIT

  if [ "$MODE" = "--once" ]; then
    cycle_once
    exit 0
  fi

  while true; do
    refresh_lock "$SELFHEAL_LOCK_DIR"
    cycle_once
    sleep 60
  done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  AESOP_ROOT="${AESOP_ROOT:-$(dirname "$SCRIPT_DIR")}"
  # Same CONDUCTOR_ROOT resolution as daemons/backup-fleet.sh (heartbeat-pollution
  # guard, 2026-10-07): explicit override wins; the sibling conductor root is derived
  # ONLY for the canonical "aesop" tree; any other AESOP_ROOT gets "" (unresolved).
  if [ -n "${CONDUCTOR_ROOT:-}" ]; then
    : # explicit override always wins
  elif [ "$(basename "$AESOP_ROOT")" = "aesop" ]; then
    CONDUCTOR_ROOT="$(dirname "$AESOP_ROOT")/conductor3"
  else
    CONDUCTOR_ROOT=""
  fi
  MODE="${1:-daemon}"

  SELFHEAL_LOCK_DIR="$AESOP_ROOT/state/.selfheal-lock"
  SELFHEAL_LOG="$AESOP_ROOT/state/SELFHEAL.log"
  SELFHEAL_STALE_THRESHOLD=600

  # backup-fleet.sh writes the watchdog heartbeat under CONDUCTOR_ROOT, never AESOP_ROOT.
  WATCHDOG_HB="${CONDUCTOR_ROOT:+$CONDUCTOR_ROOT/state/.watchdog-heartbeat}"
  WATCHDOG_STALE_THRESHOLD=600

  MONITOR_HB="$CONDUCTOR_ROOT/monitor/.monitor-heartbeat"
  MONITOR_STALE_THRESHOLD=600

  main "$@"
fi
