#!/bin/bash
# TDD tests for daemons/run-merge-queue.sh check_halt() fail-closed contract.
#
# Contract (mirrors daemons/run-watchdog.sh, PR #957):
#   - $CONDUCTOR_ROOT/state/.HALT (box-wide) exists  => halted, no interpreter needed.
#   - halt.py --status exit 0                        => not halted, pass runs.
#   - halt.py --status exit 1 with HALTED: line     => halted, pass skipped.
#   - anything else (Store stub exit 49, traceback,
#     missing interpreter)                           => HALT-CHECK-FAILED, treated
#                                                     as halted, pass skipped.
#
# HERMETIC: every invocation uses a throwaway AESOP_ROOT, AESOP_STATE_ROOT and
# CONDUCTOR_ROOT under mktemp -d. The live ~/conductor3 is never touched.
# The merge advancer is replaced by a counter mock (AESOP_MERGE_QUEUE_CMD), so
# "cycle ran" == counter incremented.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

FAKE_ROOT="${TMP_DIR}/project"
ALT_STATE="${TMP_DIR}/alt-state"
BOX_ROOT="${TMP_DIR}/box-conductor"
STUB_DIR="${TMP_DIR}/stub-bin"
COUNTER="${TMP_DIR}/cycle-counter"
MOCK="${TMP_DIR}/mock-advance.sh"

mkdir -p "${FAKE_ROOT}/state" "${ALT_STATE}" "${BOX_ROOT}/state" "${STUB_DIR}"

printf '#!/bin/bash\nCOUNT=$(cat "$1" 2>/dev/null || echo 0)\necho $((COUNT + 1)) > "$1"\necho "[mock-advance] pass ran"\n' > "${MOCK}"
chmod +x "${MOCK}"

# Fake interpreters that behave like the Windows Store stub: exit 49, no output.
for name in python3 python; do
  printf '#!/bin/bash\nexit 49\n' > "${STUB_DIR}/${name}"
  chmod +x "${STUB_DIR}/${name}"
done

run_pass() {
  # $1 = out file. Caller may prefix PATH.
  AESOP_ROOT="${FAKE_ROOT}" AESOP_STATE_ROOT="${ALT_STATE}" \
    CONDUCTOR_ROOT="${BOX_ROOT}" \
    AESOP_MERGE_QUEUE_CMD="${MOCK} ${COUNTER}" \
    bash "${REPO_ROOT}/daemons/run-merge-queue.sh" --once > "$1" 2>&1
  return $?
}

reset_counter() {
  echo "0" > "${COUNTER}"
}

cycle_count() {
  cat "${COUNTER}"
}

echo "=== Test 1: Store-stub interpreter (exit 49) must NOT fail open ==="
reset_counter
OUT1="${TMP_DIR}/out1"
PATH="${STUB_DIR}:${PATH}" run_pass "$OUT1"
EXIT1=$?
cat "$OUT1"
if [ "$(cycle_count)" != "0" ]; then
  echo "FAIL: merge-queue pass ran although halt status was unknowable (exit 49) -- fail-open"
  exit 1
fi
if ! grep -q "HALT-CHECK-FAILED (exit 49)" "$OUT1"; then
  echo "FAIL: expected 'HALT-CHECK-FAILED (exit 49)' in output"
  exit 1
fi
if [ "$EXIT1" != "0" ]; then
  echo "FAIL: fail-closed skip should exit 0 (same as halted), got $EXIT1"
  exit 1
fi
echo "PASS: exit-49 interpreter fails closed (cycle skipped, HALT-CHECK-FAILED logged)"

echo ""
echo "=== Test 2: box-wide sentinel honored by existence, even when halt.py says not halted ==="
reset_counter
printf '{"reason": "box-wide operator stop", "timestamp": "2026-10-14T00:00:00Z"}\n' \
  > "${BOX_ROOT}/state/.HALT"
OUT2="${TMP_DIR}/out2"
run_pass "$OUT2"
EXIT2=$?
cat "$OUT2"
if [ "$(cycle_count)" != "0" ]; then
  echo "FAIL: merge-queue pass ran while \$CONDUCTOR_ROOT/state/.HALT existed -- box-wide kill switch ignored"
  exit 1
fi
if ! grep -q "^HALTED: box-wide operator stop \[box-wide " "$OUT2"; then
  echo "FAIL: expected exact 'HALTED: box-wide operator stop [box-wide <path>]' (reason parsed from JSON, no trailing junk)"
  exit 1
fi
if [ "$EXIT2" != "0" ]; then
  echo "FAIL: halted pass should exit 0, got $EXIT2"
  exit 1
fi
echo "PASS: box-wide sentinel halts the pass"

echo ""
echo "=== Test 3: box-wide sentinel honored with an unusable interpreter (existence only) ==="
reset_counter
OUT3="${TMP_DIR}/out3"
PATH="${STUB_DIR}:${PATH}" run_pass "$OUT3"
EXIT3=$?
cat "$OUT3"
if [ "$(cycle_count)" != "0" ]; then
  echo "FAIL: box-wide sentinel not honored when interpreter is unusable"
  exit 1
fi
if ! grep -q "^HALTED: box-wide operator stop \[box-wide " "$OUT3"; then
  echo "FAIL: expected box-wide HALTED line when interpreter is unusable"
  exit 1
fi
if [ "$EXIT3" != "0" ]; then
  echo "FAIL: expected exit 0, got $EXIT3"
  exit 1
fi
echo "PASS: box-wide sentinel needs no interpreter"

echo ""
echo "=== Test 4: real halt.py halt still halts with its reason (regression) ==="
rm -f "${BOX_ROOT}/state/.HALT"
if ! command -v python3 >/dev/null 2>&1; then
  echo "SKIP: no python3 on PATH for real halt.py regression"
else
  reset_counter
  AESOP_STATE_ROOT="${ALT_STATE}" python3 "${REPO_ROOT}/tools/halt.py" set "per-checkout stop" > /dev/null 2>&1
  OUT4="${TMP_DIR}/out4"
  run_pass "$OUT4"
  EXIT4=$?
  cat "$OUT4"
  if [ "$(cycle_count)" != "0" ]; then
    echo "FAIL: pass ran while halt.py reported halted"
    exit 1
  fi
  if ! grep -q "HALTED: per-checkout stop" "$OUT4"; then
    echo "FAIL: expected 'HALTED: per-checkout stop'"
    exit 1
  fi
  if [ "$EXIT4" != "0" ]; then
    echo "FAIL: expected exit 0, got $EXIT4"
    exit 1
  fi
  echo "PASS: halt.py halted state still halts"

  echo ""
  echo "=== Test 5: clearing halt.py sentinel lets the pass run (regression) ==="
  AESOP_STATE_ROOT="${ALT_STATE}" python3 "${REPO_ROOT}/tools/halt.py" --clear > /dev/null 2>&1
  reset_counter
  OUT5="${TMP_DIR}/out5"
  run_pass "$OUT5"
  if [ "$(cycle_count)" != "1" ]; then
    echo "FAIL: expected pass to run once nothing halts it"
    cat "$OUT5"
    exit 1
  fi
  echo "PASS: unhalted pass runs"
fi

echo ""
echo "=== All run-merge-queue halt fail-closed tests passed ==="
exit 0
