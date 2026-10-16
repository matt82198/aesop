#!/usr/bin/env bash
# CONDUCTOR_ROOT resolver shared by read-only consumers (dash/watchdog-gui.sh).
# Same rule as daemons/backup-fleet.sh (heartbeat-pollution guard, 2026-10-07):
#   1. an explicit CONDUCTOR_ROOT always wins;
#   2. otherwise the sibling conductor root is derived ONLY when the basename of the
#      given AESOP_ROOT is exactly "aesop" (the canonical tree);
#   3. any other shape resolves to the empty string (fail closed: no heartbeat path).
# Sourcing this file defines functions only; it has no side effects.
#
# Usage: source daemons/conductor-root.sh; resolve_conductor_root "$AESOP_ROOT"
# Prints the resolved root (possibly empty). Exit status is always 0.

resolve_conductor_root() {
  local aesop_root="${1:-}"
  if [ -n "${CONDUCTOR_ROOT:-}" ]; then
    printf '%s' "$CONDUCTOR_ROOT"
  elif [ "$(basename "$aesop_root")" = "aesop" ]; then
    printf '%s' "$(dirname "$aesop_root")/conductor3"  # default sibling (canonical tree only)
  fi
  return 0
}
