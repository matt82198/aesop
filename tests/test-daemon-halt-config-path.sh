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
# Use platform-portable temp directory (TMPDIR env var, or system default)
TMP_BASE="${TMPDIR:-/tmp}"
TEST_STATE_1="${TMP_BASE}/halt-state-env-${TEST_ID}"
TEST_STATE_2="${TMP_BASE}/halt-state-cfg-${TEST_ID}"
CYCLE_COUNTER="${TMP_BASE}/halt-counter-${TEST_ID}"
# ISOLATION FIX: run-watchdog.sh derives CONDUCTOR_ROOT from
# dirname("$AESOP_ROOT")/conductor3 whenever CONDUCTOR_ROOT is unset. This test
# passes REPO_ROOT as AESOP_ROOT (it needs the real daemon script + REAL
# tools/halt.py on disk), but every repo checkout/worktree on this box is a
# direct child of $HOME, so that derivation silently resolves to the REAL
# ~/conductor3 -- exactly the incident this suite now guards against. Pin
# CONDUCTOR_ROOT to a throwaway fixture on every run-watchdog.sh invocation
# below so it can never fall back to the real one.
CONDUCTOR_ROOT_FIXTURE="${TMP_BASE}/halt-conductor-${TEST_ID}"
CONFIG_FILE="${REPO_ROOT}/aesop.config.json"

mkdir -p "${TEST_STATE_1}" "${TEST_STATE_2}" "${CONDUCTOR_ROOT_FIXTURE}/monitor"
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
  rm -rf "${TEST_STATE_1}" "${TEST_STATE_2}" "${MOCK_CYCLE}" "${CYCLE_COUNTER}" "${CONDUCTOR_ROOT_FIXTURE}"
  # Unconditional: aesop.config.json is written into REPO_ROOT by TEST 2 and
  # must never survive an early exit (a failed assertion between writing it
  # and the success-path rm would otherwise leave a stray state_root override
  # sitting in a real checkout/worktree).
  rm -f "${CONFIG_FILE}"
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
  CONDUCTOR_ROOT="${CONDUCTOR_ROOT_FIXTURE}" \
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
  CONDUCTOR_ROOT="${CONDUCTOR_ROOT_FIXTURE}" \
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

cat > "${CONFIG_FILE}" << EOFCONFIG
{"state_root": "${WIN_STATE_2}"}
EOFCONFIG

python3 "${REPO_ROOT}/tools/halt.py" set "halt via config" > /dev/null 2>&1

if [ ! -f "${TEST_STATE_2}/.HALT" ]; then
  echo "FAIL: .HALT not created in config state_root"
  echo "Config file content:"
  cat "${CONFIG_FILE}"
  echo "Directory contents:"
  ls -la "${TEST_STATE_2}"
  exit 1
fi
echo "PASS: .HALT created via config state_root"

# Daemon should detect halt
echo "0" > "${CYCLE_COUNTER}"
OUT=$(mktemp)
AESOP_ROOT="${REPO_ROOT}" \
  CONDUCTOR_ROOT="${CONDUCTOR_ROOT_FIXTURE}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT" 2>&1 || true

COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$COUNT" != "0" ]; then
  echo "FAIL: Cycle ran while halted (config)"
  cat "$OUT"
  exit 1
fi
echo "PASS: Daemon skips cycle when halted (config)"
rm -f "$OUT"

# Clear and resume
python3 "${REPO_ROOT}/tools/halt.py" --clear > /dev/null 2>&1
echo "0" > "${CYCLE_COUNTER}"

OUT=$(mktemp)
AESOP_ROOT="${REPO_ROOT}" \
  CONDUCTOR_ROOT="${CONDUCTOR_ROOT_FIXTURE}" \
  AESOP_WATCHDOG_CYCLE_CMD="${MOCK_CYCLE} ${CYCLE_COUNTER}" \
  bash "${REPO_ROOT}/daemons/run-watchdog.sh" --once > "$OUT" 2>&1

COUNT=$(cat "${CYCLE_COUNTER}")
if [ "$COUNT" != "1" ]; then
  echo "FAIL: Cycle did not run after halt cleared (config)"
  exit 1
fi
echo "PASS: Daemon resumes after halt cleared (config)"
rm -f "$OUT"

echo ""
echo "=== All behavioral tests PASSED ==="
