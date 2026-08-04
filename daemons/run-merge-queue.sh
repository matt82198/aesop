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
# Kill switch: if a .HALT sentinel exists, the pass logs "HALTED: <reason>" and
# does no work at all until the sentinel is cleared. Detection delegates to
# `python tools/halt.py --status` as the single source of truth, respecting
# all resolution precedence (AESOP_STATE_ROOT env > aesop.config.json state_root
# > default). Bash never re-derives the sentinel path, eliminating drift from
# configuration overrides.
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

# Kill-switch check using halt.py as single source of truth.
# Returns 0 (bash true) and logs when halted, 1 otherwise.
# halt.py --status exit code: 1=halted, 0=not halted.
check_halt() {
  local log_file="$1"
  local halt_py="${2:-${HALT_PY_PATH}}"

  if [ -z "$PYTHON_EXE" ] || [ -z "$halt_py" ]; then
    return 1
  fi

  # Query halt.py for halt status; exit code tells us: 1=halted, 0=not halted
  local halt_output
  halt_output=$("$PYTHON_EXE" "$halt_py" --status 2>&1)
  local halt_exit=$?

  if [ $halt_exit -eq 1 ]; then
    # Halted: extract reason from output
    # halt.py outputs: "HALTED: <reason> (since <timestamp>)"
    local reason
    reason=$(echo "$halt_output" | sed 's/^HALTED: //' | sed 's/ (since.*$//')
    if [ -z "$reason" ]; then
      reason="$halt_output"
    fi

    printf 'HALTED: %s\n' "$reason"
    log_line "$log_file" "HALTED: $reason"
    return 0
  fi

  # Not halted (exit 0 from halt.py)
  return 1
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

  HALT_PY_PATH="$AESOP_ROOT/tools/halt.py"

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
  AESOP_STATE_ROOT="${AESOP_STATE_ROOT:-$AESOP_ROOT/state}"
  export AESOP_STATE_ROOT
  MODE="${1:---once}"
  LOG_FILE="$AESOP_STATE_ROOT/MERGE-QUEUE.log"
  PYTHON_EXE="$(resolve_python)"

  # gh resolves the repository from the working directory, so the pass must run
  # inside the project root.
  cd "$AESOP_ROOT" || exit 2

  main "$@"
fi
