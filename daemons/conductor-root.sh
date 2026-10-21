#!/usr/bin/env bash
# CONDUCTOR_ROOT resolver shared by read-only consumers (dash/watchdog-gui.sh).
# Same rule as daemons/backup-fleet.sh (heartbeat-pollution guard, 2026-10-07):
#   1. an explicit CONDUCTOR_ROOT always wins;
#   2. otherwise the sibling conductor root is derived ONLY when the basename of the
#      given AESOP_ROOT is exactly "aesop" (the canonical tree), and only when that
#      candidate holds a live heartbeat (state/.watchdog-heartbeat);
#   3. otherwise the default canonical $HOME/conductor3 is used, under the same heartbeat test;
#   4. if nothing resolves, the result is the empty string (fail closed: no heartbeat
#      path, and the GUI reports UNRESOLVED rather than STALE).
# The heartbeat file, not a bare state/ directory, is the marker: a stray sibling dir
# can hold state/ (e.g. only state/pr-sweep.log) and must not shadow the real root.
# Sourcing this file defines functions only; it has no side effects.
#
# Usage: source daemons/conductor-root.sh; resolve_conductor_root "$AESOP_ROOT"
# Prints the resolved root (possibly empty). Exit status is always 0.

# _conductor_root_is_live <root>: true when <root> carries the daemon heartbeat file.
_conductor_root_is_live() {
  [ -n "${1:-}" ] && [ -f "$1/state/.watchdog-heartbeat" ]
}

resolve_conductor_root() {
  local aesop_root="${1:-}" candidate
  if [ -n "${CONDUCTOR_ROOT:-}" ]; then
    printf '%s' "$CONDUCTOR_ROOT"
    return 0
  fi
  if [ "$(basename "$aesop_root")" = "aesop" ]; then
    candidate="$(dirname "$aesop_root")/conductor3"  # default sibling (canonical tree only)
    if _conductor_root_is_live "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  fi
  candidate="${HOME:-}/conductor3"  # default canonical conductor3 on this box
  if _conductor_root_is_live "$candidate"; then
    printf '%s' "$candidate"
  fi
  return 0
}
