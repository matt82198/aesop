#!/usr/bin/env bash
set -uo pipefail

# Behavioral proof: daemons/selfheal.sh must read the watchdog heartbeat where
# daemons/backup-fleet.sh WRITES it -- $CONDUCTOR_ROOT/state/.watchdog-heartbeat --
# and must not treat an unresolvable path as a stale daemon (which would
# restart the watchdog every cycle). HERMETIC: mktemp fixtures only; daemon
# restarts are suppressed with AESOP_SELFHEAL_SKIP_RESTART=1.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SELFHEAL_SCRIPT="$SCRIPT_DIR/daemons/selfheal.sh"

TEST_DIR="$(mktemp -d)"
if command -v cygpath >/dev/null 2>&1; then
  TEST_DIR="$(cygpath -m "$TEST_DIR")"
fi
PASSED=0
FAILED=0

cleanup() {
  rm -rf "$TEST_DIR" 2>/dev/null || true
}
trap cleanup EXIT

log()  { echo "[TEST] $*"; }
fail() { echo "FAIL: $*" >&2; FAILED=$((FAILED + 1)); }
pass() { echo "PASS: $*"; PASSED=$((PASSED + 1)); }

# Run one selfheal cycle with CONDUCTOR_ROOT unset (the derivation under test).
run_selfheal_once() {
  local aesop_root="$1"
  env -u CONDUCTOR_ROOT AESOP_ROOT="$aesop_root" AESOP_SELFHEAL_SKIP_RESTART=1 \
    bash "$SELFHEAL_SCRIPT" --once >/dev/null 2>&1
}

selfheal_log_has() {
  local aesop_root="$1" needle="$2"
  grep -qF "$needle" "$aesop_root/state/SELFHEAL.log" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Test 1: fresh heartbeat at the CONDUCTOR_ROOT path -> watchdog NOT stale.
# ---------------------------------------------------------------------------
test_fresh_conductor_heartbeat_is_not_stale() {
  log "TEST 1: fresh \$CONDUCTOR_ROOT/state heartbeat is read as live (no stale restart)"
  local root="$TEST_DIR/t1"
  local aesop="$root/aesop"
  mkdir -p "$aesop/state" "$root/conductor3/state" "$root/conductor3/monitor"
  date +%s > "$root/conductor3/state/.watchdog-heartbeat"

  run_selfheal_once "$aesop"

  if selfheal_log_has "$aesop" "watchdog heartbeat stale"; then
    fail "selfheal read a fresh CONDUCTOR_ROOT heartbeat as stale (wrong path)"
    cat "$aesop/state/SELFHEAL.log" >&2 2>/dev/null; return 1
  fi
  pass "fresh CONDUCTOR_ROOT heartbeat respected"
}

# ---------------------------------------------------------------------------
# Test 2: control -- stale heartbeat at the CONDUCTOR_ROOT path IS reported.
# Proves the staleness check is live, not vacuously silent.
# ---------------------------------------------------------------------------
test_stale_conductor_heartbeat_is_reported() {
  log "TEST 2: stale \$CONDUCTOR_ROOT/state heartbeat is still detected (control)"
  local root="$TEST_DIR/t2"
  local aesop="$root/aesop"
  mkdir -p "$aesop/state" "$root/conductor3/state" "$root/conductor3/monitor"
  echo "1000" > "$root/conductor3/state/.watchdog-heartbeat"

  run_selfheal_once "$aesop"

  if ! selfheal_log_has "$aesop" "watchdog heartbeat stale"; then
    fail "stale CONDUCTOR_ROOT heartbeat was not reported"
    cat "$aesop/state/SELFHEAL.log" >&2 2>/dev/null; return 1
  fi
  pass "stale CONDUCTOR_ROOT heartbeat detected"
}

# ---------------------------------------------------------------------------
# Test 3: a fresh heartbeat under AESOP_ROOT/state is NOT the watchdog heartbeat.
# ---------------------------------------------------------------------------
test_aesop_state_heartbeat_is_ignored() {
  log "TEST 3: a fresh heartbeat under AESOP_ROOT/state does not satisfy the watchdog check"
  local root="$TEST_DIR/t3"
  local aesop="$root/aesop"
  mkdir -p "$aesop/state" "$root/conductor3/state" "$root/conductor3/monitor"
  date +%s > "$aesop/state/.watchdog-heartbeat"

  run_selfheal_once "$aesop"

  if ! selfheal_log_has "$aesop" "watchdog heartbeat stale"; then
    fail "selfheal accepted AESOP_ROOT/state heartbeat, which backup-fleet never writes"
    cat "$aesop/state/SELFHEAL.log" >&2 2>/dev/null; return 1
  fi
  pass "AESOP_ROOT/state heartbeat ignored"
}

# ---------------------------------------------------------------------------
# Test 4: non-canonical AESOP_ROOT (worktree-shaped, no explicit CONDUCTOR_ROOT)
# has no resolvable heartbeat path: selfheal must SKIP the watchdog check and
# must not restart on a missing file.
# ---------------------------------------------------------------------------
test_unresolved_conductor_root_skips_watchdog_check() {
  log "TEST 4: unresolvable CONDUCTOR_ROOT skips the watchdog check (no restart-on-missing)"
  local root="$TEST_DIR/t4"
  local aesop="$root/aesop-wt-fixture"
  mkdir -p "$aesop/state"

  run_selfheal_once "$aesop"

  if selfheal_log_has "$aesop" "watchdog heartbeat stale"; then
    fail "non-canonical AESOP_ROOT with no CONDUCTOR_ROOT was treated as a stale watchdog"
    cat "$aesop/state/SELFHEAL.log" >&2 2>/dev/null; return 1
  fi
  if ! selfheal_log_has "$aesop" "SKIP: watchdog heartbeat path unresolved"; then
    fail "expected an explicit SKIP line for the unresolved watchdog heartbeat path"
    cat "$aesop/state/SELFHEAL.log" >&2 2>/dev/null; return 1
  fi
  pass "unresolved CONDUCTOR_ROOT: watchdog check skipped with explicit SKIP line"
}

test_fresh_conductor_heartbeat_is_not_stale
test_stale_conductor_heartbeat_is_reported
test_aesop_state_heartbeat_is_ignored
test_unresolved_conductor_root_skips_watchdog_check

echo ""
echo "Test Results: $PASSED passed, $FAILED failed"
if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
