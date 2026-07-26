#!/bin/bash
# TDD tests for run-watchdog.sh smoke signal (--once verdict)
# Ensures --once mode prints clear PASSED/FAILED verdicts with honest exit codes
#
# HERMETIC: every invocation is pointed at a throwaway AESOP_ROOT (mktemp fixture),
# never at the real project. This suite never touches real project state.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR=$(mktemp -d)
AESOP_ROOT="${TMP_DIR}"
TEST_STATE_DIR="${TMP_DIR}/state"

# Cleanup on exit
trap "rm -rf ${TMP_DIR}" EXIT

# Setup
mkdir -p "${TEST_STATE_DIR}"

# Mock cycle script that succeeds
MOCK_CYCLE_SUCCESS="${TMP_DIR}/mock-cycle-success.sh"
cat > "${MOCK_CYCLE_SUCCESS}" << 'EOFMOCK'
#!/bin/bash
echo "[mock-cycle] Cycle completed successfully"
echo "[mock-cycle] All repos processed"
exit 0
EOFMOCK
chmod +x "${MOCK_CYCLE_SUCCESS}"

# Mock cycle script that fails
MOCK_CYCLE_FAIL="${TMP_DIR}/mock-cycle-fail.sh"
cat > "${MOCK_CYCLE_FAIL}" << 'EOFFAIL'
#!/bin/bash
echo "[mock-cycle] Cycle started"
echo "[mock-cycle] Error: backup failed"
exit 42
EOFFAIL
chmod +x "${MOCK_CYCLE_FAIL}"

echo "=== Test 1: --once success path prints WATCHDOG SMOKE: PASSED ==="
OUT_SUCCESS=$(mktemp)
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE_SUCCESS}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT_SUCCESS" 2>&1
EXIT_SUCCESS=$?

echo "Exit code: $EXIT_SUCCESS"
echo "Output:"
cat "$OUT_SUCCESS"
echo ""

# Check for the verdict line
if grep -q "WATCHDOG SMOKE: PASSED" "$OUT_SUCCESS"; then
  echo "PASS: SUCCESS path printed 'WATCHDOG SMOKE: PASSED'"
else
  echo "FAIL: SUCCESS path did not print 'WATCHDOG SMOKE: PASSED'"
  exit 1
fi

# Check exit code is 0
if [ "$EXIT_SUCCESS" -eq 0 ]; then
  echo "PASS: SUCCESS path exited with 0"
else
  echo "FAIL: SUCCESS path exited with $EXIT_SUCCESS (expected 0)"
  exit 1
fi

rm -f "$OUT_SUCCESS"

echo ""
echo "=== Test 2: --once failure path prints WATCHDOG SMOKE: FAILED ==="
OUT_FAIL=$(mktemp)
set +e
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE_FAIL}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT_FAIL" 2>&1
EXIT_FAIL=$?
set -e

echo "Exit code: $EXIT_FAIL"
echo "Output:"
cat "$OUT_FAIL"
echo ""

# Check for the verdict line
if grep -q "WATCHDOG SMOKE: FAILED" "$OUT_FAIL"; then
  echo "PASS: FAILURE path printed 'WATCHDOG SMOKE: FAILED'"
else
  echo "FAIL: FAILURE path did not print 'WATCHDOG SMOKE: FAILED'"
  exit 1
fi

# Check that the exit code matches the cycle's exit code (42)
if [ "$EXIT_FAIL" -eq 42 ]; then
  echo "PASS: FAILURE path exited with 42 (cycle's exit code)"
else
  echo "FAIL: FAILURE path exited with $EXIT_FAIL (expected 42)"
  exit 1
fi

# Check that the error code is included in the verdict
if grep -q "ERROR: exit 42" "$OUT_FAIL"; then
  echo "PASS: FAILURE verdict includes the exit code"
else
  echo "FAIL: FAILURE verdict does not include exit code"
  exit 1
fi

rm -f "$OUT_FAIL"

echo ""
echo "=== Test 3: --once halted path prints WATCHDOG SMOKE: PASSED and exits 0 ==="
# Clean state
rm -rf "${TEST_STATE_DIR}"
mkdir -p "${TEST_STATE_DIR}"

# Create halt sentinel
cat > "${TEST_STATE_DIR}/.HALT" << 'EOFHALT'
{"reason": "test halt", "timestamp": "2026-07-26T00:00:00Z"}
EOFHALT

OUT_HALT=$(mktemp)
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE_FAIL}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT_HALT" 2>&1
EXIT_HALT=$?

echo "Exit code: $EXIT_HALT"
echo "Output:"
cat "$OUT_HALT"
echo ""

# Check for HALTED message
if grep -q "HALTED:" "$OUT_HALT"; then
  echo "PASS: HALTED path printed HALTED message"
else
  echo "FAIL: HALTED path did not print HALTED message"
  exit 1
fi

# Check for the verdict line (halted is a clean no-op, so PASSED)
if grep -q "WATCHDOG SMOKE: PASSED" "$OUT_HALT"; then
  echo "PASS: HALTED path printed 'WATCHDOG SMOKE: PASSED' (clean no-op)"
else
  echo "FAIL: HALTED path did not print 'WATCHDOG SMOKE: PASSED'"
  exit 1
fi

# Check exit code is 0 (halted is not an error)
if [ "$EXIT_HALT" -eq 0 ]; then
  echo "PASS: HALTED path exited with 0"
else
  echo "FAIL: HALTED path exited with $EXIT_HALT (expected 0)"
  exit 1
fi

rm -f "$OUT_HALT"

echo ""
echo "=== All smoke signal tests passed ==="
