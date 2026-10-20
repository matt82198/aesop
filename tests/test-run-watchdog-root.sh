#!/bin/bash
# TDD test for run-watchdog.sh root resolution when AESOP_ROOT is UNSET (tracker cebab35aabeb).
#
# Contract under test:
#   1. With AESOP_ROOT unset, the watchdog resolves the checkout root it physically lives in
#      (the same root the scheduled task runs from, install-tasks.ps1) and exports it to the
#      child cycle (backup-fleet.sh), so both agree on one root.
#   2. That derivation is never silent: stderr names the resolved root and says it was derived.
#   3. No state is ever written to the parent of the resolved root (a guessed parent dir).
#   4. Control: with AESOP_ROOT set explicitly, no derivation notice is printed.
#
# HERMETIC: the script under test is copied into a mktemp fixture laid out like a real install
# (daemons/ + tools/). The derived root is therefore the fixture, never the real checkout.
# HOME, TMPDIR, CONDUCTOR_ROOT and AESOP_STATE_ROOT are pinned under the fixture.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PASSED=0
FAILED=0
pass() { echo "PASS: $*"; PASSED=$((PASSED + 1)); }
fail() { echo "FAIL: $*"; FAILED=$((FAILED + 1)); }

FIXTURE_ROOT="$TMP_DIR/aesop"
mkdir -p "$FIXTURE_ROOT/daemons" "$FIXTURE_ROOT/tools" "$TMP_DIR/home"
cp "$REPO_ROOT/daemons/run-watchdog.sh" "$REPO_ROOT/daemons/conductor-root.sh" "$FIXTURE_ROOT/daemons/"
cp "$REPO_ROOT/tools/halt.py" "$REPO_ROOT/tools/common.py" "$FIXTURE_ROOT/tools/"
# Canonical spelling of the fixture root, as the script's own pwd will print it.
EXPECTED_ROOT="$(cd "$FIXTURE_ROOT" && pwd)"

MARK="$TMP_DIR/child-saw-root"
CYCLE_CMD='printf "%s" "$AESOP_ROOT" > '"'$MARK'"

# Run the watchdog once. $1 = "unset" or "set"; stderr goes to $2.
run_once() {
  local mode="$1" errfile="$2"
  rm -f "$MARK"
  if [ "$mode" = "unset" ]; then
    env -u AESOP_ROOT -u AESOP_STATE_ROOT \
      HOME="$TMP_DIR/home" TMPDIR="$TMP_DIR" CONDUCTOR_ROOT="$TMP_DIR/conductor3" \
      AESOP_WATCHDOG_CYCLE_CMD="$CYCLE_CMD" \
      bash "$FIXTURE_ROOT/daemons/run-watchdog.sh" --once >/dev/null 2>"$errfile"
  else
    env -u AESOP_STATE_ROOT \
      HOME="$TMP_DIR/home" TMPDIR="$TMP_DIR" CONDUCTOR_ROOT="$TMP_DIR/conductor3" \
      AESOP_ROOT="$FIXTURE_ROOT" AESOP_WATCHDOG_CYCLE_CMD="$CYCLE_CMD" \
      bash "$FIXTURE_ROOT/daemons/run-watchdog.sh" --once >/dev/null 2>"$errfile"
  fi
}

echo "=== Test 1: AESOP_ROOT unset resolves the checkout root and exports it to the cycle ==="
ERR_UNSET="$TMP_DIR/stderr-unset.txt"
run_once unset "$ERR_UNSET"
if [ -f "$MARK" ] && [ "$(cat "$MARK")" = "$EXPECTED_ROOT" ]; then
  pass "cycle ran and child saw AESOP_ROOT=$EXPECTED_ROOT"
else
  fail "child did not see the checkout root (got: '$(cat "$MARK" 2>/dev/null)', want: '$EXPECTED_ROOT')"
fi

echo "=== Test 2: the derivation is announced on stderr, naming the resolved root ==="
if grep -qF "AESOP_ROOT unset" "$ERR_UNSET" && grep -qF "$EXPECTED_ROOT" "$ERR_UNSET"; then
  pass "stderr announces the derived root"
else
  fail "no loud derivation notice naming $EXPECTED_ROOT on stderr. stderr was:"
  cat "$ERR_UNSET"
fi

echo "=== Test 3: no state is written to the parent of the resolved root ==="
if [ ! -e "$TMP_DIR/state" ] && [ -f "$FIXTURE_ROOT/state/FLEET-BACKUP.log" ]; then
  pass "state written under the resolved root only"
else
  fail "state leaked to the parent dir or missing under the resolved root"
fi

echo "=== Test 4: control - explicit AESOP_ROOT prints no derivation notice ==="
ERR_SET="$TMP_DIR/stderr-set.txt"
rm -f "$FIXTURE_ROOT/state/.watchdog-lock" 2>/dev/null
run_once set "$ERR_SET"
if ! grep -qF "AESOP_ROOT unset" "$ERR_SET"; then
  pass "explicit AESOP_ROOT is silent about derivation"
else
  fail "explicit AESOP_ROOT still printed the derivation notice"
fi

echo "=== Test 5: CONDUCTOR_ROOT unset + non-canonical AESOP_ROOT fails closed before any cycle ==="
# Same rule as daemons/conductor-root.sh: the sibling conductor3 is derived only for the
# canonical "aesop" basename. This checkout is named aesop-wt-x, so the root is unresolved
# and the watchdog must refuse to start (no cycle, no guessed sibling, no home fallback).
WT_ROOT="$TMP_DIR/wt/aesop-wt-x"
mkdir -p "$WT_ROOT/daemons" "$WT_ROOT/tools" "$TMP_DIR/wt/conductor3/state"
cp "$REPO_ROOT/daemons/run-watchdog.sh" "$REPO_ROOT/daemons/conductor-root.sh" "$WT_ROOT/daemons/"
cp "$REPO_ROOT/tools/halt.py" "$REPO_ROOT/tools/common.py" "$WT_ROOT/tools/"
MARK5="$TMP_DIR/child-saw-root-5"
rm -f "$MARK5"
ERR_5="$TMP_DIR/stderr-5.txt"
env -u AESOP_ROOT -u CONDUCTOR_ROOT -u AESOP_STATE_ROOT \
  HOME="$TMP_DIR/home" USERPROFILE="$TMP_DIR/home" TMPDIR="$TMP_DIR" \
  AESOP_ROOT="$WT_ROOT" AESOP_WATCHDOG_CYCLE_CMD='printf x > '"'$MARK5'" \
  bash "$WT_ROOT/daemons/run-watchdog.sh" --once >/dev/null 2>"$ERR_5"
RC5=$?
if [ "$RC5" -ne 0 ] && [ ! -e "$MARK5" ] && grep -qF "CONDUCTOR_ROOT" "$ERR_5"; then
  pass "unresolved CONDUCTOR_ROOT: watchdog exits $RC5 with a CONDUCTOR_ROOT message, no cycle ran"
else
  fail "unresolved CONDUCTOR_ROOT did not fail closed (rc=$RC5, cycle ran=$([ -e "$MARK5" ] && echo yes || echo no)). stderr was:"
  cat "$ERR_5"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
