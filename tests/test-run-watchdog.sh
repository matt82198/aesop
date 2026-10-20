#!/bin/bash
# TDD tests for run-watchdog.sh lock mechanism
# Ensures single-instance guard (atomic lockfile) gates both loop and --once modes
# Tests concurrent starts → only one runs cycle, other exits with lock-held message
#
# HERMETIC: every run-watchdog.sh invocation below is pointed at a throwaway
# AESOP_ROOT (a mktemp -d fixture), never at the real project checkout. That
# guarantees LOCK_DIR / FLEET-BACKUP.log / heartbeat all live under
# $TMP_DIR/state, so this suite can never race a live daemon or touch real
# project state. REPO_ROOT is used ONLY to locate the run-watchdog.sh script
# under test — it is never passed as AESOP_ROOT to any invocation.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR=$(mktemp -d)
AESOP_ROOT="${TMP_DIR}"
# Fail-closed root rule: a non-canonical fixture AESOP_ROOT has no derivable conductor root,
# so every daemon invocation pins its OWN fixture conductor root (never the live fleet).
CONDUCTOR_ROOT="${TMP_DIR}/conductor3"
export CONDUCTOR_ROOT
TEST_STATE_DIR="${TMP_DIR}/state"
CYCLE_COUNTER="${TMP_DIR}/cycle-counter"

# Cleanup on exit
trap "rm -rf ${TMP_DIR}" EXIT

# Setup
mkdir -p "${TEST_STATE_DIR}"
echo "0" > "${CYCLE_COUNTER}"

# Mock cycle script that increments a counter
MOCK_CYCLE="${TMP_DIR}/mock-cycle.sh"
cat > "${MOCK_CYCLE}" << 'EOFMOCK'
#!/bin/bash
COUNTER_FILE="$1"
if [ -z "$COUNTER_FILE" ]; then
  echo "Usage: mock-cycle.sh <counter-file>" >&2
  exit 1
fi
COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
echo $((COUNT + 1)) > "$COUNTER_FILE"
echo "[mock-cycle] Counter incremented to $((COUNT + 1))"
sleep 0.1
EOFMOCK
chmod +x "${MOCK_CYCLE}"
echo "Mock cycle script created at: ${MOCK_CYCLE}"

echo "=== Test 1: Concurrent --once invocations (should lock second one out) ==="
echo "Starting two --once instances simultaneously..."

# Capture outputs and exit codes
OUT1=$(mktemp)
OUT2=$(mktemp)
EXIT1=""
EXIT2=""

# Start first instance in background
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT1" 2>&1 &
PID1=$!

# Start second instance immediately (should be blocked by lock)
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT2" 2>&1 &
PID2=$!

# Wait for both to complete
wait $PID1
EXIT1=$?
wait $PID2
EXIT2=$?

# Check results
CYCLE_RUN_COUNT=$(cat "${CYCLE_COUNTER}")

echo "Process 1 (PID=$PID1) exit code: $EXIT1"
echo "Process 2 (PID=$PID2) exit code: $EXIT2"
echo "Cycle run count: $CYCLE_RUN_COUNT"
echo ""
echo "Process 1 output:"
cat "$OUT1"
echo ""
echo "Process 2 output:"
cat "$OUT2"

# Assertions
if [ "$CYCLE_RUN_COUNT" != "1" ]; then
  echo "FAIL: Expected cycle to run exactly once, but it ran $CYCLE_RUN_COUNT times"
  exit 1
fi
echo "PASS: Cycle ran exactly once"

# One should succeed, one should be blocked (exit code 1 or success but with lock message)
if ! grep -q "already running\|lock held\|not starting" "$OUT2" 2>/dev/null; then
  echo "WARNING: Second process didn't show lock-held message, but this is OK if it also exited cleanly"
fi

# One process should indicate it acquired the lock, other should indicate it was held
if grep -q "already running\|lock held" "$OUT1" && grep -q "already running\|lock held" "$OUT2"; then
  echo "WARNING: Both processes show lock message — should only be one"
fi

echo ""
echo "=== Test 2: Stale lock recovery (lock older than threshold should be reclaimed) ==="
# Clean state
rm -rf "${TEST_STATE_DIR}"
mkdir -p "${TEST_STATE_DIR}"
echo "0" > "${CYCLE_COUNTER}"

# Create an old lock (manually for test) — this now lives at the SAME path the
# script under test reads (AESOP_ROOT is the throwaway fixture root), so aging
# it genuinely exercises acquire_lock's staleness/reclaim branch.
OLD_LOCK="${TEST_STATE_DIR}/.watchdog-lock"
mkdir -p "$OLD_LOCK"
# Write stale timestamp (500 seconds ago) into the file that acquire_lock checks
echo $(($(date +%s) - 500)) > "$OLD_LOCK/timestamp"
# Also write a fake PID for completeness
echo "9999" > "$OLD_LOCK/pid"

echo "Created stale lock at: $OLD_LOCK"
ls -la "$OLD_LOCK"
cat "$OLD_LOCK/timestamp"

# Run watchdog with stale lock present
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT1" 2>&1
EXIT1=$?

CYCLE_RUN_COUNT=$(cat "${CYCLE_COUNTER}")
echo "After stale lock recovery: exit code=$EXIT1, cycle_run_count=$CYCLE_RUN_COUNT"
echo "Output:"
cat "$OUT1"

if [ "$CYCLE_RUN_COUNT" != "1" ]; then
  echo "FAIL: Expected stale lock to be reclaimed and cycle to run, but count=$CYCLE_RUN_COUNT"
  exit 1
fi
echo "PASS: Stale lock was reclaimed and cycle ran"

if ! grep -q "reclaimed" "$OUT1" 2>/dev/null; then
  echo "FAIL: Expected acquire_lock to report reclaiming the stale lock, but 'reclaimed' not found in output"
  exit 1
fi
echo "PASS: acquire_lock reported reclaiming the stale lock"

rm -f "$OUT1" "$OUT2"

echo ""
echo "=== Test 3: Lock reclaim contract (dead holder reclaimed, live holder never) ==="
# Contract (f18337dc30c3): a lock is reclaimed ONLY when its recorded holder pid is
# dead. A live holder is never reclaimed, however old its timestamp is.
# Superseded assertion (do not restore): this test used to age the lock of a LIVE
# holder and expect a second daemon to reclaim it. That encoded the defect the fix
# removes (a live daemon's lock being stolen), so the test was rewritten.
rm -rf "${TEST_STATE_DIR}"
mkdir -p "${TEST_STATE_DIR}"
echo "0" > "${CYCLE_COUNTER}"

LOCK_PATH="${TEST_STATE_DIR}/.watchdog-lock"

echo "--- Test 3a: holder pid DEAD + stale timestamp -> second daemon reclaims ---"
# A reaped child's pid is dead by construction (same technique as Test 6).
( exit 0 ) &
DEAD_PID=$!
wait "$DEAD_PID" 2>/dev/null
if kill -0 "$DEAD_PID" 2>/dev/null; then
  echo "FAIL: fixture pid ${DEAD_PID} still alive after reap"
  exit 1
fi
mkdir -p "$LOCK_PATH"
echo "$DEAD_PID" > "$LOCK_PATH/pid"
echo $(($(date +%s) - 350)) > "$LOCK_PATH/timestamp"

OUT_3A=$(mktemp)
set +e
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT_3A" 2>&1
EXIT_3A=$?
set -e

echo "Output (dead holder, exit ${EXIT_3A}):"
cat "$OUT_3A"
CYCLE_RUN_COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$CYCLE_RUN_COUNT" != "1" ]; then
  echo "FAIL: Expected dead-holder lock to be reclaimed and cycle to run, but count=$CYCLE_RUN_COUNT"
  exit 1
fi
if ! grep -q "reclaimed" "$OUT_3A"; then
  echo "FAIL: Expected a 'reclaimed' message for a dead holder"
  exit 1
fi
if [ -d "$LOCK_PATH" ]; then
  echo "FAIL: Lock directory still exists after the reclaiming daemon's own release"
  exit 1
fi
echo "PASS: Dead-holder lock was reclaimed, cycle ran, and the reclaimer released it"

echo "--- Test 3b: holder pid LIVE + stale timestamp -> second daemon does NOT reclaim ---"
echo "0" > "${CYCLE_COUNTER}"
# A real process this test starts and kills itself. Its pid stays live for the run.
sleep 60 &
LIVE_PID=$!
if ! kill -0 "$LIVE_PID" 2>/dev/null; then
  echo "FAIL: fixture sleep process ${LIVE_PID} did not start"
  exit 1
fi
rm -rf "$LOCK_PATH"
mkdir -p "$LOCK_PATH"
echo "$LIVE_PID" > "$LOCK_PATH/pid"
echo $(($(date +%s) - 350)) > "$LOCK_PATH/timestamp"

OUT_3B=$(mktemp)
set +e
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT_3B" 2>&1
EXIT_3B=$?
set -e
LIVE_PID_AFTER=$(cat "$LOCK_PATH/pid" 2>/dev/null || echo "")
kill "$LIVE_PID" 2>/dev/null || true
wait "$LIVE_PID" 2>/dev/null || true

echo "Output (live holder, exit ${EXIT_3B}):"
cat "$OUT_3B"
CYCLE_RUN_COUNT=$(cat "${CYCLE_COUNTER}")
if grep -q "reclaimed" "$OUT_3B"; then
  echo "FAIL: A LIVE holder's lock was reclaimed (f18337dc30c3 regression)"
  exit 1
fi
if [ "$CYCLE_RUN_COUNT" != "0" ]; then
  echo "FAIL: Second daemon ran a cycle while a live holder held the lock, count=$CYCLE_RUN_COUNT"
  exit 1
fi
if ! grep -q "already running" "$OUT_3B"; then
  echo "FAIL: Second daemon did not report the lock as held by a live holder"
  exit 1
fi
if [ "$LIVE_PID_AFTER" != "$LIVE_PID" ]; then
  echo "FAIL: Live holder's lock pid was changed by the second daemon (got '$LIVE_PID_AFTER')"
  exit 1
fi
echo "PASS: Live holder's lock was not reclaimed; second daemon exited as held"

rm -rf "$LOCK_PATH"
rm -f "$OUT_3A" "$OUT_3B"
echo "PASS: Lock reclaim contract enforced (dead holder reclaimed, live holder never)"

echo ""
echo "=== Test 4: CYCLE_CMD with spaces in AESOP_ROOT ==="
# Clean state
rm -rf "${TEST_STATE_DIR}"
# Create a path with spaces
TEST_STATE_DIR_SPACES="${TMP_DIR}/state with spaces"
mkdir -p "${TEST_STATE_DIR_SPACES}"
echo "0" > "${CYCLE_COUNTER}"

# Create a path with spaces
AESOP_ROOT_WITH_SPACES="${TMP_DIR}/aesop with spaces"
# Real install layout: the daemon halt-checks via tools/halt.py (fail-closed when
# the tool is missing), so the fixture must ship daemons/ and tools/ together.
mkdir -p "$AESOP_ROOT_WITH_SPACES/daemons" "$AESOP_ROOT_WITH_SPACES/tools"
cp "${REPO_ROOT}/daemons/run-watchdog.sh" "${REPO_ROOT}/daemons/conductor-root.sh" "$AESOP_ROOT_WITH_SPACES/daemons/"
cp "${REPO_ROOT}/tools/halt.py" "${REPO_ROOT}/tools/common.py" "$AESOP_ROOT_WITH_SPACES/tools/"

OUT_SPACES=$(mktemp)
AESOP_ROOT="${AESOP_ROOT_WITH_SPACES}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${AESOP_ROOT_WITH_SPACES}/daemons/run-watchdog.sh" --once > "$OUT_SPACES" 2>&1
EXIT_SPACES=$?

echo "Output from AESOP_ROOT with spaces:"
cat "$OUT_SPACES"

CYCLE_RUN_COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$CYCLE_RUN_COUNT" = "1" ]; then
  echo "PASS: Cycle ran with AESOP_ROOT containing spaces"
else
  echo "FAIL: Cycle did not run with spaces in path. Count=$CYCLE_RUN_COUNT"
  exit 1
fi

rm -f "$OUT_SPACES"

echo ""
echo "=== Test 5: Exit status capture and error logging (P1 fix) ==="
# Clean state
rm -rf "${TEST_STATE_DIR}"
mkdir -p "${TEST_STATE_DIR}"
echo "0" > "${CYCLE_COUNTER}"

# Create a mock cycle that fails with exit code 42
FAIL_MOCK="${TMP_DIR}/fail-mock.sh"
cat > "${FAIL_MOCK}" << 'EOFFAIL'
#!/bin/bash
echo "[mock-cycle] This is output line 1"
echo "[mock-cycle] This is output line 2 (last line)"
exit 42
EOFFAIL
chmod +x "${FAIL_MOCK}"

OUT_FAIL=$(mktemp)
set +e
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${FAIL_MOCK}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT_FAIL" 2>&1
EXIT_FAIL=$?
set -e

echo "Mock cycle that fails with exit 42:"
cat "$OUT_FAIL"
echo ""

# Check that error was logged to FLEET-BACKUP.log with the exit code
LOG_FILE="${AESOP_ROOT}/state/FLEET-BACKUP.log"
if grep -q "ERROR: cycle #1 failed with exit code 42" "$LOG_FILE" 2>/dev/null; then
  echo "PASS: Exit status 42 was logged to FLEET-BACKUP.log"
else
  echo "FAIL: Exit status not logged correctly to FLEET-BACKUP.log"
  echo "Log contents:"
  cat "$LOG_FILE" || echo "(log file missing)"
  exit 1
fi

# Check that the output shows [ERROR: exit 42] indicator
if grep -q "ERROR: exit 42" "$OUT_FAIL" 2>/dev/null; then
  echo "PASS: Exit status 42 displayed in output"
else
  echo "FAIL: Exit status not displayed in output"
  exit 1
fi

# Check that the last 2 lines of the failed command output are still shown
if grep -q "This is output line 1" "$OUT_FAIL" 2>/dev/null; then
  echo "PASS: Failed command output still displayed"
else
  echo "FAIL: Failed command output not displayed"
  exit 1
fi

rm -f "$OUT_FAIL" "$FAIL_MOCK"
echo "PASS: Exit status capture and error logging (P1 fix)"

echo ""
echo "=== Test 6: AUDIT FIX 2 - Lock dir without timestamp file ==="
# Clean state
rm -rf "${TEST_STATE_DIR}"
mkdir -p "${TEST_STATE_DIR}"
echo "0" > "${CYCLE_COUNTER}"

# Create lock dir with ONLY pid file (no timestamp) - should be treated as stale-eligible
BROKEN_LOCK="${TEST_STATE_DIR}/.watchdog-lock"
mkdir -p "$BROKEN_LOCK"
# The reclaim path only fires when the recorded pid is DEAD (kill -0 fails).
# A hardcoded "9999" is live on some CI runners (ubuntu shard 0 failed on
# 3b0479d4 and 618951c2 with "watchdog already running"), so the lock was
# correctly honoured and the test went red. Use the pid of a child we have
# already reaped: a reaped pid is dead by construction.
( exit 0 ) &
DEAD_PID=$!
wait "$DEAD_PID" 2>/dev/null
if kill -0 "$DEAD_PID" 2>/dev/null; then
  echo "FAIL: fixture pid ${DEAD_PID} still alive after reap"
  exit 1
fi
echo "$DEAD_PID" > "$BROKEN_LOCK/pid"
# NOTE: no timestamp file!

echo "Created broken lock with pid but no timestamp:"
ls -la "$BROKEN_LOCK"

OUT_BROKEN=$(mktemp)
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT_BROKEN" 2>&1
EXIT_BROKEN=$?

CYCLE_RUN_COUNT=$(cat "${CYCLE_COUNTER}")
echo "After lock without timestamp: exit code=$EXIT_BROKEN, cycle_run_count=$CYCLE_RUN_COUNT"
echo "Output:"
cat "$OUT_BROKEN"

if [ "$CYCLE_RUN_COUNT" != "1" ]; then
  echo "FAIL: AUDIT FIX 2 - Lock without timestamp should be treated as stale, but count=$CYCLE_RUN_COUNT"
  exit 1
fi
echo "PASS: AUDIT FIX 2 - Lock dir without timestamp file was treated as stale"

rm -f "$OUT_BROKEN"

echo ""
echo "=== Test 7: AUDIT FIX 2 - Lock dir without pid file ==="
# Clean state
rm -rf "${TEST_STATE_DIR}"
mkdir -p "${TEST_STATE_DIR}"
echo "0" > "${CYCLE_COUNTER}"

# Create lock dir with ONLY timestamp file (no pid) - should be treated as stale-eligible
BROKEN_LOCK2="${TEST_STATE_DIR}/.watchdog-lock"
mkdir -p "$BROKEN_LOCK2"
echo $(($(date +%s) - 500)) > "$BROKEN_LOCK2/timestamp"
# NOTE: no pid file!

echo "Created broken lock with timestamp but no pid:"
ls -la "$BROKEN_LOCK2"

OUT_BROKEN2=$(mktemp)
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT_BROKEN2" 2>&1
EXIT_BROKEN2=$?

CYCLE_RUN_COUNT=$(cat "${CYCLE_COUNTER}")
echo "After lock without pid: exit code=$EXIT_BROKEN2, cycle_run_count=$CYCLE_RUN_COUNT"
echo "Output:"
cat "$OUT_BROKEN2"

if [ "$CYCLE_RUN_COUNT" != "1" ]; then
  echo "FAIL: AUDIT FIX 2 - Lock without pid should be treated as stale, but count=$CYCLE_RUN_COUNT"
  exit 1
fi
echo "PASS: AUDIT FIX 2 - Lock dir without pid file was treated as stale"

rm -f "$OUT_BROKEN2"

echo ""
echo "=== Test 8: FIX #5 contract - cycle exit code 3 (blocked cycle) is WARN, not ERROR ==="
# Clean state
rm -rf "${TEST_STATE_DIR}"
mkdir -p "${TEST_STATE_DIR}"
echo "0" > "${CYCLE_COUNTER}"

# Mock cycle that reports a "blocked cycle" (exit 3), mirroring
# daemons/backup-fleet.sh main()'s any_blocked contract.
BLOCKED_MOCK="${TMP_DIR}/blocked-mock.sh"
cat > "${BLOCKED_MOCK}" << 'EOFBLOCKED'
#!/bin/bash
echo "[mock-cycle] BLOCKED: some-repo"
exit 3
EOFBLOCKED
chmod +x "${BLOCKED_MOCK}"

OUT_BLOCKED=$(mktemp)
set +e
AESOP_ROOT="${AESOP_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${BLOCKED_MOCK}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT_BLOCKED" 2>&1
EXIT_BLOCKED=$?
set -e

echo "Mock cycle that reports a blocked cycle (exit 3):"
cat "$OUT_BLOCKED"
echo ""

# --once mode must NOT treat this as a hard failure: overall exit stays 0
# and SMOKE reports PASSED, never FAILED/exit-3-propagated.
if [ "$EXIT_BLOCKED" -eq 0 ]; then
  echo "PASS: run-watchdog.sh --once treats cycle exit 3 as non-fatal (exit 0)"
else
  echo "FAIL: run-watchdog.sh --once should exit 0 on a blocked (exit 3) cycle, got $EXIT_BLOCKED"
  exit 1
fi

if grep -q "WATCHDOG SMOKE: PASSED" "$OUT_BLOCKED" 2>/dev/null; then
  echo "PASS: SMOKE reported PASSED (not FAILED) for a blocked cycle"
else
  echo "FAIL: SMOKE should report PASSED for a blocked (exit 3) cycle"
  exit 1
fi

LOG_FILE_BLOCKED="${AESOP_ROOT}/state/FLEET-BACKUP.log"
if grep -q "WARN: cycle #1 reported blocked repo" "$LOG_FILE_BLOCKED" 2>/dev/null; then
  echo "PASS: exit 3 logged as WARN, not ERROR"
else
  echo "FAIL: expected a WARN log line for exit code 3"
  cat "$LOG_FILE_BLOCKED" || true
  exit 1
fi

if grep -q "ERROR: cycle #1 failed with exit code 3" "$LOG_FILE_BLOCKED" 2>/dev/null; then
  echo "FAIL: exit code 3 was logged as a hard ERROR (should be WARN)"
  exit 1
else
  echo "PASS: exit code 3 was NOT logged as a hard ERROR"
fi

rm -f "$OUT_BLOCKED" "$BLOCKED_MOCK"
echo "PASS: FIX #5 contract - --once mode handles cycle exit 3 as WARN"

echo ""
echo "=== Test 9: FIX #5 contract - daemon loop mode has a distinct exit-3 WARN branch (static check) ==="
# The persistent daemon loop already never halts on ANY non-zero cycle exit
# (it always continues to the next 150s sleep), so the behavior this proves
# is the log-level distinction: exit 3 must be logged/labeled WARN, not fall
# through to the generic ERROR branch used for other non-zero exit codes.
if grep -qF 'elif [ $cmd_exit -eq 3 ]' "${REPO_ROOT}/daemons/run-watchdog.sh"; then
  echo "PASS: daemon loop mode has a distinct exit-3 WARN branch (not the generic ERROR path)"
else
  echo "FAIL: daemon loop mode is missing a distinct exit-3 WARN branch"
  exit 1
fi

echo ""
echo "=== All tests passed ==="
