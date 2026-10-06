#!/bin/bash
# TDD tests for run-watchdog.sh HALT kill-switch integration (wave-26 safety brake)
#
# HERMETIC: every run-watchdog.sh invocation below is pointed at a throwaway
# AESOP_ROOT (a mktemp -d fixture), never at the real project checkout. The
# .HALT sentinel is written under $TMP_DIR/state/.HALT — this suite never
# touches the real project's state/.HALT.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR=$(mktemp -d)
AESOP_ROOT="${TMP_DIR}"
TEST_STATE_DIR="${TMP_DIR}/state"
CYCLE_COUNTER="${TMP_DIR}/cycle-counter"

trap "rm -rf ${TMP_DIR}" EXIT

mkdir -p "${TEST_STATE_DIR}"
echo "0" > "${CYCLE_COUNTER}"

MOCK_CYCLE="${TMP_DIR}/mock-cycle.sh"
cat > "${MOCK_CYCLE}" << 'EOFMOCK'
#!/bin/bash
COUNTER_FILE="$1"
COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
echo $((COUNT + 1)) > "$COUNTER_FILE"
echo "[mock-cycle] Counter incremented to $((COUNT + 1))"
EOFMOCK
chmod +x "${MOCK_CYCLE}"

echo "=== Test 1: --once mode no-ops when .HALT sentinel present ==="
# Use halt.py to set the halt (single source of truth)
AESOP_STATE_ROOT="${TEST_STATE_DIR}" python3 "${REPO_ROOT}/tools/halt.py" set "manual stop for wave audit" > /dev/null 2>&1

OUT1=$(mktemp)
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_STATE_ROOT="${TEST_STATE_DIR}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT1" 2>&1
EXIT1=$?

echo "Output:"
cat "$OUT1"
echo "Exit code: $EXIT1"

CYCLE_RUN_COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$CYCLE_RUN_COUNT" != "0" ]; then
  echo "FAIL: Expected cycle to be skipped while halted, but it ran $CYCLE_RUN_COUNT times"
  exit 1
fi
echo "PASS: Cycle skipped while .HALT sentinel present"

if ! grep -q "HALTED: manual stop for wave audit" "$OUT1"; then
  echo "FAIL: Expected 'HALTED: manual stop for wave audit' in output"
  cat "$OUT1"
  exit 1
fi
echo "PASS: HALTED message with reason printed"

if [ "$EXIT1" != "0" ]; then
  echo "FAIL: Expected exit code 0 (halted is a clean no-op, not an error), got $EXIT1"
  exit 1
fi
echo "PASS: Halted --once exits 0"

if ! grep -q "HALTED: manual stop for wave audit" "${TEST_STATE_DIR}/FLEET-BACKUP.log" 2>/dev/null; then
  echo "FAIL: Expected HALTED line logged to FLEET-BACKUP.log"
  cat "${TEST_STATE_DIR}/FLEET-BACKUP.log" 2>/dev/null || echo "(log missing)"
  exit 1
fi
echo "PASS: HALTED reason logged to FLEET-BACKUP.log"

rm -f "$OUT1"

echo ""
echo "=== Test 2: lock is released after a halted --once run (not left dangling) ==="
if [ -d "${TEST_STATE_DIR}/.watchdog-lock" ]; then
  echo "FAIL: lock directory still present after halted run — release_lock did not run"
  exit 1
fi
echo "PASS: lock released after halted run"

echo ""
echo "=== Test 3: clearing the sentinel lets the cycle run again ==="
# Use halt.py to clear (single source of truth)
AESOP_STATE_ROOT="${TEST_STATE_DIR}" python3 "${REPO_ROOT}/tools/halt.py" --clear > /dev/null 2>&1
echo "0" > "${CYCLE_COUNTER}"

OUT2=$(mktemp)
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_STATE_ROOT="${TEST_STATE_DIR}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT2" 2>&1
EXIT2=$?

CYCLE_RUN_COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$CYCLE_RUN_COUNT" != "1" ]; then
  echo "FAIL: Expected cycle to run once sentinel is cleared, but count=$CYCLE_RUN_COUNT"
  cat "$OUT2"
  exit 1
fi
echo "PASS: Cycle runs normally once .HALT is cleared"
rm -f "$OUT2"

echo ""
echo "=== Test 4: sourcing the script (execution guard) runs no cycle ==="
rm -rf "${TEST_STATE_DIR}"
mkdir -p "${TEST_STATE_DIR}"
echo "0" > "${CYCLE_COUNTER}"

# Source it in a subshell so function/variable pollution doesn't affect this
# test script, and so a runaway daemon loop (if the guard failed) can't hang
# the parent. $0 inside a sourced-in-subshell context is still the sourcing
# script's path (this file), never run-watchdog.sh's own path, which is
# exactly the condition the BASH_SOURCE guard checks.
(
  AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  source "${REPO_ROOT}/daemons/run-watchdog.sh"
  echo "sourced-ok"
  # A function defined by the sourced script should now be callable —
  # proving the source actually loaded it rather than silently failing.
  type check_halt > /dev/null 2>&1 && echo "check_halt-defined"
)
SOURCE_STATUS=$?

CYCLE_RUN_COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$CYCLE_RUN_COUNT" != "0" ]; then
  echo "FAIL: Expected sourcing to run no cycle, but count=$CYCLE_RUN_COUNT"
  exit 1
fi
echo "PASS: Sourcing the script did not run a cycle"

if [ "$SOURCE_STATUS" != "0" ]; then
  echo "FAIL: Sourcing the script exited non-zero ($SOURCE_STATUS) — should source cleanly"
  exit 1
fi
echo "PASS: Sourcing exits cleanly (no stray 'exit'/lock-acquire side effects)"

echo ""
echo "=== All HALT integration tests passed ==="
