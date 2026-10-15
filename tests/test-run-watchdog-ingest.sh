#!/bin/bash
# TDD tests for run-watchdog.sh run_task_ingest (task-event ingest step).
#
# Contract under test:
#   - The ingest runs against the SAME state dir the watchdog is rooted at:
#     "$AESOP_ROOT/state" (argv check on a recording python shim).
#   - Fail closed, never silent: no python interpreter -> ERROR logged to
#     FLEET-BACKUP.log and return 1; ingest exit N -> ERROR logged and return N.
#   - A tool-less checkout (no tools/ingest_task_events.py) is a clean skip, but
#     it is LOGGED as skipped, not swallowed.
#
# HERMETIC: every fixture lives in a mktemp AESOP_ROOT. Exit codes are captured
# explicitly into variables; piped output is never used as the verdict.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

FAILS=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }

# Source the daemon for its functions only (execution guard skips the cycle).
# shellcheck disable=SC1091
source "${REPO_ROOT}/daemons/run-watchdog.sh"

# Fixture AESOP_ROOT with a tool file present.
AESOP_FIX="${TMP_DIR}/aesop"
mkdir -p "${AESOP_FIX}/tools" "${AESOP_FIX}/state"
: > "${AESOP_FIX}/tools/ingest_task_events.py"
LOG_FILE="${AESOP_FIX}/state/FLEET-BACKUP.log"

# Recording python shim: writes its argv to ARGS_FILE and exits with FAKE_RC.
SHIM="${TMP_DIR}/fake-python"
ARGS_FILE="${TMP_DIR}/shim-args.txt"
cat > "${SHIM}" <<'EOFSHIM'
#!/bin/bash
printf '%s\n' "$@" > "${FAKE_ARGS_FILE}"
echo "task-events ingest: ingested=0 duplicates=0 malformed=0 quarantined=0 cursor=0"
exit "${FAKE_RC:-0}"
EOFSHIM
chmod +x "${SHIM}"
export FAKE_ARGS_FILE="${ARGS_FILE}"

echo "=== Test 1: ingest is rooted at \$AESOP_ROOT/state ==="
: > "${LOG_FILE}"
FAKE_RC=0 run_task_ingest "${AESOP_FIX}" "${SHIM}" "${LOG_FILE}"
RC1=$?
if [ "${RC1}" -eq 0 ] && grep -qx -- "--state-dir" "${ARGS_FILE}" && grep -qx -- "${AESOP_FIX}/state" "${ARGS_FILE}"; then
  pass "clean ingest exits 0 and was given --state-dir ${AESOP_FIX}/state (exit=${RC1})"
else
  fail "expected exit 0 and --state-dir ${AESOP_FIX}/state; exit=${RC1}; argv=$(tr '\n' ' ' < "${ARGS_FILE}" 2>/dev/null)"
fi

echo "=== Test 2: ingest exit code propagates and is logged as ERROR ==="
: > "${LOG_FILE}"
FAKE_RC=3 run_task_ingest "${AESOP_FIX}" "${SHIM}" "${LOG_FILE}"
RC2=$?
if [ "${RC2}" -eq 3 ] && grep -q "ERROR: task-event ingest failed (exit 3)" "${LOG_FILE}"; then
  pass "ingest exit 3 returned as 3 and logged (exit=${RC2})"
else
  fail "expected return 3 and an ERROR line; exit=${RC2}; log=$(cat "${LOG_FILE}")"
fi

echo "=== Test 3: missing python interpreter fails closed (not silent) ==="
: > "${LOG_FILE}"
run_task_ingest "${AESOP_FIX}" "" "${LOG_FILE}"
RC3=$?
if [ "${RC3}" -ne 0 ] && grep -q "ERROR: task-event ingest" "${LOG_FILE}"; then
  pass "no python -> return ${RC3} and an ERROR line logged"
else
  fail "no python must fail closed with a logged ERROR; exit=${RC3}; log=$(cat "${LOG_FILE}")"
fi

echo "=== Test 4: tool-less checkout is a logged skip, not a silent one ==="
AESOP_BARE="${TMP_DIR}/bare"
mkdir -p "${AESOP_BARE}/state"
BARE_LOG="${AESOP_BARE}/state/FLEET-BACKUP.log"
: > "${BARE_LOG}"
run_task_ingest "${AESOP_BARE}" "${SHIM}" "${BARE_LOG}"
RC4=$?
if [ "${RC4}" -eq 0 ] && grep -q "task-event ingest skipped" "${BARE_LOG}"; then
  pass "missing tool -> return 0 and a 'skipped' line logged (exit=${RC4})"
else
  fail "missing tool must log a skip; exit=${RC4}; log=$(cat "${BARE_LOG}")"
fi

echo ""
if [ "${FAILS}" -eq 0 ]; then
  echo "WATCHDOG INGEST SUITE: PASSED (4/4)"
  exit 0
fi
echo "WATCHDOG INGEST SUITE: FAILED (${FAILS} failing)"
exit 1
