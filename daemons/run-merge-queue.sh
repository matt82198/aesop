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
#   AESOP_MERGE_QUEUE_CMD  override the advancer invocation (test hook)
#
# Kill switch: if $AESOP_ROOT/state/.HALT exists (tools/halt.py), the pass logs
# "HALTED: <reason>" and does no work at all until the sentinel is cleared.
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

# Kill-switch check. Returns 0 (bash true) and logs when halted, 1 otherwise.
check_halt() {
  local sentinel="$1"
  local log_file="$2"
  local python_exe="$3"
  if [ ! -f "$sentinel" ]; then
    return 1
  fi
  local reason="halted (reason unavailable)"
  if [ -n "$python_exe" ]; then
    local parsed
    parsed=$("$python_exe" -c '
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
  printf 'HALTED: %s\n' "$reason"
  log_line "$log_file" "HALTED: $reason"
  return 0
}

main() {
  if [ "$MODE" != "--once" ] && [ "$MODE" != "once" ]; then
    printf 'usage: run-merge-queue.sh [--once]\n' >&2
    exit 2
  fi

  if [ -z "$PYTHON_EXE" ]; then
    printf 'MERGE-QUEUE: FAILED - no python interpreter on PATH\n' >&2
    log_line "$LOG_FILE" "ERROR: no python interpreter on PATH"
    exit 2
  fi

  mkdir -p "$AESOP_STATE_ROOT" 2>/dev/null || true

  if check_halt "$HALT_SENTINEL" "$LOG_FILE" "$PYTHON_EXE"; then
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
  AESOP_STATE_ROOT="${AESOP_STATE_ROOT:-$AESOP_ROOT/state}"
  export AESOP_STATE_ROOT
  MODE="${1:---once}"
  LOG_FILE="$AESOP_STATE_ROOT/MERGE-QUEUE.log"
  HALT_SENTINEL="$AESOP_ROOT/state/.HALT"
  PYTHON_EXE="$(resolve_python)"

  # gh resolves the repository from the working directory, so the pass must run
  # inside the project root.
  cd "$AESOP_ROOT" || exit 2

  main "$@"
fi
