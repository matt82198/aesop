#!/bin/bash
# Box-wide kill switch + fail-closed halt check for daemons/run-watchdog.sh.
#
# N-01 (2026-10-14 reliability refutation): the operator writes the halt from the
# primary checkout, but the scheduled task runs the watchdog from the runtime
# worktree, whose per-checkout state/.HALT is a different file. The fix is one
# box-wide sentinel at $CONDUCTOR_ROOT/state/.HALT that every checkout honors.
#
# F-02 (same refutation): check_halt() treated any python exit other than 1 as
# "not halted", so a Windows Store python stub (exit 49) ignored a present .HALT.
# check_halt() must now fail CLOSED on any unexpected result.
#
# HERMETIC: every path is a mktemp fixture (HOME, CONDUCTOR_ROOT, primary checkout,
# runtime checkout). Nothing here reads or writes the real ~/conductor3 or the
# real project state/.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

BASH_BIN="$(command -v bash)"
REAL_PY="$(command -v python3)"
if [ -z "$REAL_PY" ]; then
  echo "FAIL: precondition: a real python3 must be on PATH for the control cases"
  exit 1
fi

HOME_FIX="$TMP_DIR/home"
CR="$TMP_DIR/conductor3"
PRIMARY="$TMP_DIR/aesop"
RUNTIME="$TMP_DIR/aesop-daemon-runtime"
STUB_DIR="$TMP_DIR/py-stub"
OUT="$TMP_DIR/out.txt"
FAILS=0

mkdir -p "$HOME_FIX" "$CR/state" "$PRIMARY/state" "$RUNTIME/daemons" "$RUNTIME/tools" "$RUNTIME/state" "$STUB_DIR"
cp "$REPO_ROOT/daemons/run-watchdog.sh" "$REPO_ROOT/daemons/conductor-root.sh" "$RUNTIME/daemons/"
cp "$REPO_ROOT/tools/halt.py" "$REPO_ROOT/tools/common.py" "$RUNTIME/tools/"

# A python3 that behaves like the Windows Store stub: prints a message, exits 49.
printf '#!/bin/sh\necho "Python was not found; run without arguments to install" >&2\nexit 49\n' > "$STUB_DIR/python3"
chmod +x "$STUB_DIR/python3"

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }

clear_sentinels() {
  rm -f "$CR/state/.HALT" "$PRIMARY/state/.HALT" "$RUNTIME/state/.HALT"
  rm -rf "$RUNTIME/state/.watchdog-lock"
}

# Run the runtime watchdog once. $1 = PATH to use. Output goes to $OUT.
run_runtime_once() {
  local run_path="$1"
  (
    unset AESOP_STATE_ROOT
    export HOME="$HOME_FIX" CONDUCTOR_ROOT="$CR" AESOP_ROOT="$RUNTIME" PATH="$run_path"
    export AESOP_WATCHDOG_CYCLE_CMD='echo CYCLE-RAN'
    "$BASH_BIN" "$RUNTIME/daemons/run-watchdog.sh" --once
  ) > "$OUT" 2>&1
  return $?
}

# Operator halt: run halt.py from the PRIMARY checkout, the way a human trips it.
operator_halt() {
  ( cd "$PRIMARY" && unset AESOP_STATE_ROOT && export HOME="$HOME_FIX" CONDUCTOR_ROOT="$CR" \
      && "$REAL_PY" "$REPO_ROOT/tools/halt.py" set "$1" ) > "$TMP_DIR/op.txt" 2>&1
}

operator_clear() {
  ( cd "$PRIMARY" && unset AESOP_STATE_ROOT && export HOME="$HOME_FIX" CONDUCTOR_ROOT="$CR" \
      && "$REAL_PY" "$REPO_ROOT/tools/halt.py" --clear ) > "$TMP_DIR/clr.txt" 2>&1
}

PATH_REAL="$(dirname "$REAL_PY"):/usr/bin:/bin"
PATH_STUB="$STUB_DIR:/usr/bin:/bin"
# Python-free PATH. A bare "/usr/bin:/bin" is NOT python-free on Ubuntu runners
# (python3 lives in /usr/bin), so the precondition below failed in CI (ci (0),
# 2026-10-15). Build a private bin dir: symlink every executable from /usr/bin and
# /bin EXCEPT python interpreters. The precondition still proves python is absent.
NOPY_BIN="$TMP_DIR/nopy-bin"
mkdir -p "$NOPY_BIN"
for src_dir in /usr/bin /bin; do
  for f in "$src_dir"/*; do
    [ -f "$f" ] && [ -x "$f" ] || continue
    name="$(basename "$f")"
    case "$name" in python|python[0-9]*|python-*|pydoc*|idle*) continue ;; esac
    [ -e "$NOPY_BIN/$name" ] || ln -s "$f" "$NOPY_BIN/$name" 2>/dev/null || cp "$f" "$NOPY_BIN/$name" 2>/dev/null || true
  done
done
PATH_NOPY="$NOPY_BIN"

echo "=== Test 0: control - no halt anywhere, real python: the cycle RUNS ==="
clear_sentinels
run_runtime_once "$PATH_REAL"; rc=$?
if grep -q "CYCLE-RAN" "$OUT" && [ "$rc" = "0" ]; then pass "unhalted cycle runs (exit 0)"; else fail "unhalted cycle did not run (rc=$rc)"; cat "$OUT"; fi

echo "=== Test 1 (N-01): operator halt from PRIMARY checkout halts the RUNTIME watchdog ==="
clear_sentinels
operator_halt "operator halt from primary"
if [ -f "$CR/state/.HALT" ]; then pass "halt tool wrote box-wide sentinel at CONDUCTOR_ROOT/state/.HALT"; else fail "halt tool did not write box-wide sentinel"; cat "$TMP_DIR/op.txt"; fi
run_runtime_once "$PATH_REAL"; rc=$?
if grep -q "CYCLE-RAN" "$OUT"; then fail "CYCLE-RAN while operator-halted (kill switch dead)"; cat "$OUT"; else pass "no cycle while operator-halted"; fi
if grep -q "HALTED: operator halt from primary" "$OUT"; then pass "HALTED reason printed"; else fail "HALTED reason missing"; cat "$OUT"; fi
if [ "$rc" = "0" ]; then pass "halted --once exits 0"; else fail "halted --once exit $rc (want 0)"; fi

echo "=== Test 2: clearing the operator halt lets the runtime cycle run again ==="
operator_clear
run_runtime_once "$PATH_REAL"; rc=$?
if grep -q "CYCLE-RAN" "$OUT" && [ "$rc" = "0" ]; then pass "cycle runs after box-wide clear"; else fail "cycle still halted after clear (rc=$rc)"; cat "$OUT"; fi

echo "=== Test 3: per-checkout state/.HALT remains a second trigger ==="
clear_sentinels
printf '{"reason": "runtime local halt", "timestamp": "2026-10-14T00:00:00Z"}\n' > "$RUNTIME/state/.HALT"
run_runtime_once "$PATH_REAL"; rc=$?
if grep -q "CYCLE-RAN" "$OUT"; then fail "CYCLE-RAN despite per-checkout .HALT"; cat "$OUT"; else pass "per-checkout .HALT honored"; fi

echo "=== Test 4 (F-02): python stub exit 49 + present .HALT => treated as HALTED (fail closed) ==="
clear_sentinels
printf '{"reason": "stub-case halt", "timestamp": "2026-10-14T00:00:00Z"}\n' > "$RUNTIME/state/.HALT"
run_runtime_once "$PATH_STUB"; rc=$?
if grep -q "CYCLE-RAN" "$OUT"; then fail "CYCLE-RAN with present .HALT and exit-49 python (fail-open)"; cat "$OUT"; else pass "no cycle under exit-49 python stub"; fi
if grep -q "HALT-CHECK-FAILED (exit 49)" "$OUT"; then pass "unexpected exit logged with its code"; else fail "HALT-CHECK-FAILED (exit 49) not logged"; cat "$OUT"; fi
if [ "$rc" != "0" ]; then pass "fail-closed --once exits non-zero (smoke verdict is FAILED, not a lie)"; else fail "fail-closed --once exit 0 (want non-zero: a halt-check error is not a PASSED smoke)"; fi
if grep -q "WATCHDOG SMOKE: FAILED (halt check error, exit 49)" "$OUT"; then pass "fail-closed --once prints SMOKE: FAILED (halt check error, exit 49)"; else fail "fail-closed --once smoke verdict missing"; cat "$OUT"; fi
if grep -q "WATCHDOG SMOKE: PASSED" "$OUT"; then fail "fail-closed --once printed SMOKE: PASSED"; cat "$OUT"; else pass "fail-closed --once did not print SMOKE: PASSED"; fi

echo "=== Test 5 (F-02): no python on PATH + present .HALT => treated as HALTED ==="
clear_sentinels
printf '{"reason": "no-python halt", "timestamp": "2026-10-14T00:00:00Z"}\n' > "$RUNTIME/state/.HALT"
if PATH="$PATH_NOPY" "$BASH_BIN" -c 'command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1'; then
  fail "precondition: python must be absent from $PATH_NOPY"
else
  run_runtime_once "$PATH_NOPY"; rc=$?
  if grep -q "CYCLE-RAN" "$OUT"; then fail "CYCLE-RAN with no interpreter and present .HALT"; cat "$OUT"; else pass "no cycle with no interpreter and present .HALT"; fi
fi

echo "=== Test 6 (F-02): no python on PATH and no sentinel => cycle skipped (fail closed) ==="
clear_sentinels
run_runtime_once "$PATH_NOPY"; rc=$?
if grep -q "CYCLE-RAN" "$OUT"; then fail "CYCLE-RAN with no interpreter (halt status unknowable, must fail closed)"; cat "$OUT"; else pass "no cycle when halt status cannot be determined"; fi

clear_sentinels
echo ""
if [ "$FAILS" -gt 0 ]; then
  echo "=== $FAILS FAILURE(S) ==="
  exit 1
fi
echo "=== All box-wide halt / fail-closed tests passed ==="
exit 0
