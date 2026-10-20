#!/usr/bin/env bash
# Merge-queue advancer runner: ONE bounded pass per invocation. Not a daemon.
#
# There is no loop and no sleep in this script or in the tool it calls. The
# Windows Scheduled Task (AesopMergeQueue, 5-minute repeat) IS the loop, which
# is the whole point: merges must not depend on a live interactive session.
# Measured green-to-merge dead time was ~31.75 HOURS with no session running
# versus 9-109 seconds with one.
#
# Usage: run-merge-queue.sh [--once]
#
# Configuration:
#   AESOP_ROOT        project root (default: parent of this script's directory)
#   AESOP_STATE_ROOT  state directory (default: $AESOP_ROOT/state)
#   AESOP_QUEUE_ROOT  queue checkout root (default: $AESOP_ROOT; must be set before invoking merge_queue.py)
#   AESOP_MERGE_QUEUE_CMD  override the advancer invocation (test hook)
#
# Kill switch: if a .HALT sentinel exists, the pass logs "HALTED: <reason>" and
# does no work at all until the sentinel is cleared. Detection delegates to
# `python tools/halt.py --status` as the single source of truth, respecting
# all resolution precedence (AESOP_STATE_ROOT env > aesop.config.json state_root
# > default). The box-wide sentinel is checked first by existence, with the same
# root rule as halt.py (explicit CONDUCTOR_ROOT, else the canonical-aesop sibling). An
# UNRESOLVED root never falls back to a home fleet root: halt.py --status then exits 3 and
# the pass is skipped (fail closed). This script never exports
# AESOP_STATE_ROOT, because an exported value counts as an explicit isolated override.
#
# Exit codes: 0 = pass completed / halted / lock contention, 1 = an action in
# the pass failed, 2 = precondition failure (see tools/merge_queue.py).

# Resolve a python interpreter; empty string means none is available.
resolve_python() {
  if command -v python3 >/dev/null 2>&1; then
    printf 'python3'
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf 'python'
    return 0
  fi
  printf ''
}

# Append one timestamped line to the merge-queue log (append-only).
log_line() {
  local log_file="$1"
  local message="$2"
  if [ -n "$log_file" ]; then
    printf '[%s] %s\n' "$(date '+%F %T')" "$message" >> "$log_file"
  fi
}

# Kill-switch check. Returns 0 (bash true) when the pass must be skipped, 1 only
# when halt.py positively reports "not halted". Mirrors run-watchdog.sh check_halt
# (PR #957): fails CLOSED on any unknown halt status.
#   1. Box-wide sentinel $CONDUCTOR_ROOT/state/.HALT: existence check, no interpreter.
#   2. halt.py --status: exit 0 = not halted; exit 1 with a HALTED: line = halted;
#      anything else (Store stub exit 49, traceback, no interpreter) = UNKNOWN.
#   UNKNOWN is logged as HALT-CHECK-FAILED and treated as halted.
check_halt() {
  local log_file="$1"
  local halt_py="${2:-${HALT_PY_PATH}}"
  # Box-wide root by the conductor-root rule (same as halt.py resolve_conductor_root()):
  # explicit CONDUCTOR_ROOT, else the sibling for the canonical "aesop" tree, else empty.
  # Empty never falls back to a home fleet root: the existence check is skipped and
  # halt.py --status below reports UNKNOWN (exit 3), which fails closed.
  local box_root="${CONDUCTOR_ROOT:-}"
  if [ -z "$box_root" ] && declare -F resolve_conductor_root >/dev/null; then
    box_root="$(resolve_conductor_root "${AESOP_ROOT:-}")"
  fi
  local box_sentinel=""
  if [ -n "$box_root" ]; then
    box_sentinel="$box_root/state/.HALT"
  fi

  # Trigger 1 (box-wide, operator kill switch): file existence only, needs no interpreter.
  if [ -n "$box_sentinel" ] && [ -e "$box_sentinel" ]; then
    local box_reason
    box_reason=$(sed -n 's/.*"reason"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$box_sentinel" 2>/dev/null | head -n 1)
    if [ -z "$box_reason" ]; then
      box_reason="(unreadable sentinel)"
    fi
    printf 'HALTED: %s [box-wide %s]\n' "$box_reason" "$box_sentinel"
    log_line "$log_file" "HALTED: $box_reason [box-wide $box_sentinel]"
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
        # halt.py outputs: "HALTED: <reason> (since <timestamp>)"
        local reason
        reason=$(echo "$halt_output" | head -n 1 | sed 's/^HALTED: //' | sed 's/ (since.*$//')
        if [ -z "$reason" ]; then
          reason="$halt_output"
        fi
        printf 'HALTED: %s\n' "$reason"
        log_line "$log_file" "HALTED: $reason"
        return 0
      fi
      ;;
  esac

  # Unexpected result (no interpreter, Store stub exit 49, traceback, unknown code):
  # the halt status is unknowable, so fail CLOSED and skip the cycle.
  local fail_msg="HALT-CHECK-FAILED (exit $halt_exit); treating as HALTED, cycle skipped"
  printf '%s\n' "$fail_msg"
  log_line "$log_file" "$fail_msg"
  return 0
}

main() {
  if [ "$MODE" != "--once" ] && [ "$MODE" != "once" ]; then
    printf 'usage: run-merge-queue.sh [--once]\n' >&2
    exit 2
  fi

  mkdir -p "$AESOP_STATE_ROOT" 2>/dev/null || true

  # Halt check runs FIRST and fails closed: a missing or unusable interpreter is
  # an UNKNOWN halt status (HALT-CHECK-FAILED), not a pass. The box-wide sentinel
  # is honored by existence even with no interpreter.
  if check_halt "$LOG_FILE" "$HALT_PY_PATH"; then
    printf 'MERGE-QUEUE: HALTED\n'
    exit 0
  fi

  log_line "$LOG_FILE" "=== merge-queue pass START ==="

  local pass_out
  local pass_exit
  if [ -n "$AESOP_MERGE_QUEUE_CMD" ]; then
    pass_out=$(bash -c "$AESOP_MERGE_QUEUE_CMD" 2>&1)
    pass_exit=$?
  else
    pass_out=$("$PYTHON_EXE" "$AESOP_ROOT/tools/merge_queue.py" --advance 2>&1)
    pass_exit=$?
  fi

  printf '%s\n' "$pass_out"
  printf '%s\n' "$pass_out" >> "$LOG_FILE"

  if [ $pass_exit -eq 0 ]; then
    log_line "$LOG_FILE" "=== merge-queue pass END (ok) ==="
    printf 'MERGE-QUEUE: PASSED\n'
  elif [ $pass_exit -eq 2 ]; then
    log_line "$LOG_FILE" "=== merge-queue pass END (precondition failure, exit 2) ==="
    printf 'MERGE-QUEUE: BLOCKED - precondition failure\n' >&2
  else
    log_line "$LOG_FILE" "=== merge-queue pass END (exit $pass_exit) ==="
    printf 'MERGE-QUEUE: FAILED - exit %d\n' "$pass_exit" >&2
  fi
  exit $pass_exit
}

# Execution guard: sourcing this file (e.g. from tests, to reuse check_halt or
# log_line) must never run a pass or export environment as a side effect, so
# every path/env variable is declared inside the guard.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  AESOP_ROOT="${AESOP_ROOT:-$(dirname "$SCRIPT_DIR")}"
  # Box-wide root by the daemons/conductor-root.sh rule (explicit CONDUCTOR_ROOT, else the
  # sibling for the canonical "aesop" tree, else empty). Empty fails closed in check_halt.
  # shellcheck source=conductor-root.sh
  . "$SCRIPT_DIR/conductor-root.sh"
  # AESOP_STATE_ROOT is deliberately NOT exported: an exported value is read by
  # halt.py as an explicit isolated override, which suppresses the box-wide HALT.
  # The default is only a shell variable (used for the log path and mkdir); a
  # caller that sets AESOP_STATE_ROOT in its environment keeps the isolation.
  AESOP_STATE_ROOT="${AESOP_STATE_ROOT:-$AESOP_ROOT/state}"
  AESOP_QUEUE_ROOT="${AESOP_QUEUE_ROOT:-$AESOP_ROOT}"
  export AESOP_QUEUE_ROOT
  # Resolve halt.py from the repo (daemons/ is in same AESOP_ROOT as tools/)
  HALT_PY_PATH="$(dirname "$SCRIPT_DIR")/tools/halt.py"
  MODE="${1:---once}"
  LOG_FILE="$AESOP_STATE_ROOT/MERGE-QUEUE.log"
  PYTHON_EXE="$(resolve_python)"

  # gh resolves the repository from the working directory, so the pass must run
  # inside the project root. AESOP_QUEUE_ROOT must be exported before cd so
  # merge_queue.py can verify it's in the right directory.
  cd "$AESOP_ROOT" || exit 2

  main "$@"
fi
