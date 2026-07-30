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

acquire_lock() {
  local lock_dir="$1"
  local stale_threshold="$2"

  mkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true

  if mkdir "$lock_dir" 2>/dev/null; then
    if ! date +%s > "$lock_dir/timestamp" 2>/dev/null || ! echo $$ > "$lock_dir/pid" 2>/dev/null; then
      rm -rf "$lock_dir" 2>/dev/null || true
      return 1
    fi
    return 0
  fi

  if [ -d "$lock_dir" ]; then
    local lock_pid=""
    if [ -f "$lock_dir/pid" ]; then
      lock_pid=$(cat "$lock_dir/pid" 2>/dev/null)
    fi

    if [ -f "$lock_dir/timestamp" ] && [ -f "$lock_dir/pid" ] && [ -n "$lock_pid" ]; then
      local lock_ts=$(cat "$lock_dir/timestamp" 2>/dev/null)
      local lock_mtime=${lock_ts:-0}
      local now=$(date +%s)
      local lock_age=$((now - lock_mtime))

      if [ "$lock_age" -gt "$stale_threshold" ]; then
        rm -rf "$lock_dir" 2>/dev/null || true
        if mkdir "$lock_dir" 2>/dev/null; then
          date +%s > "$lock_dir/timestamp" 2>/dev/null || true
          echo $$ > "$lock_dir/pid" 2>/dev/null || true
          return 2
        fi
      fi
    else
      local should_reclaim=0
      if [ -f "$lock_dir/pid" ] && [ -n "$lock_pid" ]; then
        if ! kill -0 "$lock_pid" 2>/dev/null; then
          should_reclaim=1
        fi
      fi

      if [ $should_reclaim -eq 0 ] && [ -f "$lock_dir/timestamp" ]; then
        local lock_ts=$(cat "$lock_dir/timestamp" 2>/dev/null)
        local lock_mtime=${lock_ts:-0}
        local now=$(date +%s)
        local lock_age=$((now - lock_mtime))

        if [ "$lock_age" -gt "$stale_threshold" ]; then
          should_reclaim=1
        fi
      fi

      if [ $should_reclaim -eq 1 ]; then
        rm -rf "$lock_dir" 2>/dev/null || true
        if mkdir "$lock_dir" 2>/dev/null; then
          date +%s > "$lock_dir/timestamp" 2>/dev/null || true
          echo $$ > "$lock_dir/pid" 2>/dev/null || true
          return 2
        fi
      fi
    fi
  fi

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

  if is_heartbeat_stale "$WATCHDOG_HB" "$WATCHDOG_STALE_THRESHOLD"; then
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
    cycle_once
    sleep 60
  done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  AESOP_ROOT="${AESOP_ROOT:-$(dirname "$SCRIPT_DIR")}"
  CONDUCTOR_ROOT="${CONDUCTOR_ROOT:-$(dirname "$AESOP_ROOT")/conductor3}"
  MODE="${1:-daemon}"

  SELFHEAL_LOCK_DIR="$AESOP_ROOT/state/.selfheal-lock"
  SELFHEAL_LOG="$AESOP_ROOT/state/SELFHEAL.log"
  SELFHEAL_STALE_THRESHOLD=600

  WATCHDOG_HB="$AESOP_ROOT/state/.watchdog-heartbeat"
  WATCHDOG_STALE_THRESHOLD=600

  MONITOR_HB="$CONDUCTOR_ROOT/monitor/.monitor-heartbeat"
  MONITOR_STALE_THRESHOLD=600

  main "$@"
fi
