#!/bin/bash
# TDD tests: the kill switch a human trips MUST be the one the daemons read.
#
# Deep-scan finding A1. tools/halt.py writes its .HALT sentinel to the state
# dir resolved by AESOP_STATE_ROOT (env) > config state_root > ./state, while
# daemons/run-merge-queue.sh and daemons/run-watchdog.sh both hard-coded
# "$AESOP_ROOT/state/.HALT". Set AESOP_STATE_ROOT to anything other than
# $AESOP_ROOT/state and the two resolutions diverge: halt.py reports HALTED,
# the daemons keep merging to main unattended. The abort silently does nothing.
#
# These tests write the sentinel exactly the way a human does -- by running
# tools/halt.py -- and then assert each daemon FINDS it.
#
# HERMETIC: every daemon invocation is pointed at a throwaway AESOP_ROOT and a
# throwaway AESOP_STATE_ROOT under mktemp -d. The real project state/.HALT is
# never read or written.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR=$(mktemp -d)
FAKE_ROOT="${TMP_DIR}/project"
ALT_STATE="${TMP_DIR}/elsewhere-state"
COUNTER="${TMP_DIR}/cycle-counter"

trap "rm -rf ${TMP_DIR}" EXIT

mkdir -p "${FAKE_ROOT}/state" "${ALT_STATE}"

PYTHON_EXE=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_EXE="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_EXE="python"
fi
if [ -z "$PYTHON_EXE" ]; then
  echo "SKIP: no python interpreter on PATH"
  exit 0
fi

MOCK="${TMP_DIR}/mock-cycle.sh"
cat > "${MOCK}" << 'EOFMOCK'
#!/bin/bash
COUNTER_FILE="$1"
COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
echo $((COUNT + 1)) > "$COUNTER_FILE"
echo "[mock-cycle] ran"
EOFMOCK
chmod +x "${MOCK}"

# Write the sentinel the way a human trips the kill switch: tools/halt.py with
# AESOP_STATE_ROOT pointing somewhere other than $AESOP_ROOT/state.
write_halt_via_tool() {
  local state_root="$1"
  local reason="$2"
  ( cd "${TMP_DIR}" && AESOP_STATE_ROOT="${state_root}" \
      "${PYTHON_EXE}" "${REPO_ROOT}/tools/halt.py" set "${reason}" >/dev/null )
}

echo "=== Test 1: halt.py writes where AESOP_STATE_ROOT points ==="
write_halt_via_tool "${ALT_STATE}" "deep-scan A1 abort"
if [ ! -f "${ALT_STATE}/.HALT" ]; then
  echo "FAIL: halt.py did not write ${ALT_STATE}/.HALT"
  exit 1
fi
if [ -f "${FAKE_ROOT}/state/.HALT" ]; then
  echo "FAIL: halt.py also wrote \$AESOP_ROOT/state/.HALT (test premise broken)"
  exit 1
fi
echo "PASS: sentinel lives at \$AESOP_STATE_ROOT/.HALT only"

echo ""
echo "=== Test 2: merge-queue pass halts on the sentinel halt.py actually wrote ==="
echo "0" > "${COUNTER}"
OUT1=$(mktemp)
AESOP_ROOT="${FAKE_ROOT}" AESOP_STATE_ROOT="${ALT_STATE}" \
  AESOP_MERGE_QUEUE_CMD="${MOCK} ${COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-merge-queue.sh" --once > "$OUT1" 2>&1
EXIT1=$?
cat "$OUT1"
if [ "$(cat "${COUNTER}")" != "0" ]; then
  echo "FAIL: merge-queue advanced while halted -- the kill switch is dead"
  exit 1
fi
if ! grep -q "HALTED: deep-scan A1 abort" "$OUT1"; then
  echo "FAIL: expected 'HALTED: deep-scan A1 abort' in merge-queue output"
  exit 1
fi
if [ "$EXIT1" != "0" ]; then
  echo "FAIL: halted merge-queue pass should exit 0, got $EXIT1"
  exit 1
fi
rm -f "$OUT1"
echo "PASS: merge-queue halts on the AESOP_STATE_ROOT sentinel"

echo ""
echo "=== Test 3: watchdog halts on the sentinel halt.py actually wrote ==="
echo "0" > "${COUNTER}"
OUT2=$(mktemp)
AESOP_ROOT="${FAKE_ROOT}" AESOP_STATE_ROOT="${ALT_STATE}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK} ${COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT2" 2>&1
cat "$OUT2"
if [ "$(cat "${COUNTER}")" != "0" ]; then
  echo "FAIL: watchdog cycle ran while halted -- the kill switch is dead"
  exit 1
fi
if ! grep -q "HALTED: deep-scan A1 abort" "$OUT2"; then
  echo "FAIL: expected 'HALTED: deep-scan A1 abort' in watchdog output"
  exit 1
fi
rm -f "$OUT2"
echo "PASS: watchdog halts on the AESOP_STATE_ROOT sentinel"

echo ""
echo "=== Test 4: daemon respects halt.py resolution (AESOP_STATE_ROOT wins) ==="
rm -f "${ALT_STATE}/.HALT" "${FAKE_ROOT}/state/.HALT"
write_halt_via_tool "${FAKE_ROOT}/state" "legacy location abort"
echo "0" > "${COUNTER}"
OUT3=$(mktemp)
# Run with AESOP_STATE_ROOT pointing elsewhere — halt.py won't find the sentinel
AESOP_ROOT="${FAKE_ROOT}" AESOP_STATE_ROOT="${ALT_STATE}" \
  AESOP_MERGE_QUEUE_CMD="${MOCK} ${COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-merge-queue.sh" --once > "$OUT3" 2>&1
cat "$OUT3"
if [ "$(cat "${COUNTER}")" == "0" ]; then
  echo "FAIL: merge-queue halted when it shouldn't (halt.py should see no sentinel at AESOP_STATE_ROOT)"
  exit 1
fi
if grep -q "HALTED:" "$OUT3"; then
  echo "FAIL: daemon incorrectly reported HALTED (halt.py resolution is single source of truth)"
  exit 1
fi
echo "0" > "${COUNTER}"
OUT4=$(mktemp)
# Now run with AESOP_STATE_ROOT pointing to where the sentinel actually is
AESOP_ROOT="${FAKE_ROOT}" AESOP_STATE_ROOT="${FAKE_ROOT}/state" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK} ${COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT4" 2>&1
if [ "$(cat "${COUNTER}")" != "0" ]; then
  echo "FAIL: watchdog cycle ran when halt.py found the sentinel"
  exit 1
fi
if ! grep -q "HALTED: legacy location abort" "$OUT4"; then
  echo "FAIL: expected halt.py output with correct reason"
  exit 1
fi
rm -f "$OUT3" "$OUT4"
echo "PASS: daemon trusts halt.py resolution completely (no re-derivation)"

echo ""
echo "=== Test 5: clearing the sentinel lets a pass run again ==="
rm -f "${FAKE_ROOT}/state/.HALT" "${ALT_STATE}/.HALT"
echo "0" > "${COUNTER}"
OUT5=$(mktemp)
AESOP_ROOT="${FAKE_ROOT}" AESOP_STATE_ROOT="${ALT_STATE}" \
  AESOP_MERGE_QUEUE_CMD="${MOCK} ${COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-merge-queue.sh" --once > "$OUT5" 2>&1
if [ "$(cat "${COUNTER}")" != "1" ]; then
  echo "FAIL: expected the pass to run once the sentinel is cleared"
  cat "$OUT5"
  exit 1
fi
rm -f "$OUT5"
echo "PASS: cleared sentinel restores normal operation"

echo ""
echo "=== All halt-sentinel resolution tests passed ==="
