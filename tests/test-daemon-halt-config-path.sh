#!/bin/bash
# Behavioral proof: daemon halt respects config-overridden state_root
#
# VERIFIED AUDIT FINDING: Daemon scripts hardcoded the halt sentinel path and
# never consulted aesop.config.json's state_root override or AESOP_STATE_ROOT
# env var. When state_root was relocated (a tested deployment mode), halt.py
# would report HALTED but daemons kept running — the abort silently failed.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ID="$$-$(date +%s)"
TMP_BASE="/c/Users/matt8/AppData/Local/Temp"
TEST_STATE_1="${TMP_BASE}/halt-state-env-${TEST_ID}"
TEST_STATE_2="${TMP_BASE}/halt-state-cfg-${TEST_ID}"
CYCLE_COUNTER="${TMP_BASE}/halt-counter-${TEST_ID}"

mkdir -p "${TEST_STATE_1}" "${TEST_STATE_2}"
echo "0" > "${CYCLE_COUNTER}"

# Mock cycle script
MOCK_CYCLE="${TMP_BASE}/mock-cycle-${TEST_ID}.sh"
cat > "${MOCK_CYCLE}" << 'EOFMOCK'
#!/bin/bash
COUNTER_FILE="$1"
COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
echo $((COUNT + 1)) > "$COUNTER_FILE"
echo "[mock-cycle] Counter incremented to $((COUNT + 1))"
EOFMOCK
chmod +x "${MOCK_CYCLE}"

trap_cleanup() {
  rm -rf "${TEST_STATE_1}" "${TEST_STATE_2}" "${MOCK_CYCLE}" "${CYCLE_COUNTER}"
}
trap "trap_cleanup" EXIT

echo "Test Setup:"
echo "  AESOP_ROOT: ${REPO_ROOT}"
echo "  TEST_STATE_1 (env): ${TEST_STATE_1}"
echo "  TEST_STATE_2 (cfg): ${TEST_STATE_2}"
echo ""

# Test 1: AESOP_STATE_ROOT env var
echo "=== TEST 1: AESOP_STATE_ROOT env var override ==="

AESOP_STATE_ROOT="${TEST_STATE_1}" python3 "${REPO_ROOT}/tools/halt.py" set "halt via env" > /dev/null 2>&1

if [ ! -f "${TEST_STATE_1}/.HALT" ]; then
  echo "FAIL: .HALT not created in AESOP_STATE_ROOT"
  exit 1
fi
echo "PASS: .HALT created in AESOP_STATE_ROOT"

# Daemon should detect halt
echo "0" > "${CYCLE_COUNTER}"
OUT=$(mktemp)
AESOP_ROOT="${REPO_ROOT}" \
  AESOP_STATE_ROOT="${TEST_STATE_1}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT" 2>&1 || true

COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$COUNT" != "0" ]; then
  echo "FAIL: Cycle ran while halted"
  cat "$OUT"
  exit 1
fi
echo "PASS: Daemon skips cycle when halted"

if ! grep -q "HALTED" "$OUT"; then
  echo "FAIL: Expected HALTED message"
  cat "$OUT"
  exit 1
fi
rm -f "$OUT"

# Clear and verify resume
AESOP_STATE_ROOT="${TEST_STATE_1}" python3 "${REPO_ROOT}/tools/halt.py" --clear > /dev/null 2>&1
echo "0" > "${CYCLE_COUNTER}"

OUT=$(mktemp)
AESOP_ROOT="${REPO_ROOT}" \
  AESOP_STATE_ROOT="${TEST_STATE_1}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT" 2>&1

COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$COUNT" != "1" ]; then
  echo "FAIL: Cycle did not run after halt cleared"
  exit 1
fi
echo "PASS: Daemon resumes after halt cleared"
rm -f "$OUT"

echo ""

# Test 2: aesop.config.json state_root (using Windows paths so Python can read them)
echo "=== TEST 2: aesop.config.json state_root override ==="

# Convert bash path to Windows path for Python to read
WIN_STATE_2=$(cd "${TEST_STATE_2}" && pwd -W 2>/dev/null || echo "${TEST_STATE_2}")

cat > "${REPO_ROOT}/aesop.config.json" << EOFCONFIG
{"state_root": "${WIN_STATE_2}"}
EOFCONFIG

python3 "${REPO_ROOT}/tools/halt.py" set "halt via config" > /dev/null 2>&1

if [ ! -f "${TEST_STATE_2}/.HALT" ]; then
  echo "FAIL: .HALT not created in config state_root"
  echo "Config file content:"
  cat "${REPO_ROOT}/aesop.config.json"
  echo "Directory contents:"
  ls -la "${TEST_STATE_2}"
  rm -f "${REPO_ROOT}/aesop.config.json"
  exit 1
fi
echo "PASS: .HALT created via config state_root"

# Daemon should detect halt
echo "0" > "${CYCLE_COUNTER}"
OUT=$(mktemp)
AESOP_ROOT="${REPO_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT" 2>&1 || true

COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$COUNT" != "0" ]; then
  echo "FAIL: Cycle ran while halted (config)"
  cat "$OUT"
  rm -f "${REPO_ROOT}/aesop.config.json"
  exit 1
fi
echo "PASS: Daemon skips cycle when halted (config)"
rm -f "$OUT"

# Clear and resume
python3 "${REPO_ROOT}/tools/halt.py" --clear > /dev/null 2>&1
echo "0" > "${CYCLE_COUNTER}"

OUT=$(mktemp)
AESOP_ROOT="${REPO_ROOT}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT" 2>&1

COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$COUNT" != "1" ]; then
  echo "FAIL: Cycle did not run after halt cleared (config)"
  rm -f "${REPO_ROOT}/aesop.config.json"
  exit 1
fi
echo "PASS: Daemon resumes after halt cleared (config)"
rm -f "$OUT" "${REPO_ROOT}/aesop.config.json"

echo ""
echo "=== All behavioral tests PASSED ==="
