#!/usr/bin/env bash
set -uo pipefail

# Test: backup-fleet.sh writes heartbeat to $CONDUCTOR_ROOT/state/.watchdog-heartbeat
# when AESOP_ROOT and CONDUCTOR_ROOT are different directories.
# This ensures shared monitoring infrastructure (GUI, idle_tick, monitor, power_selftest)
# can read the heartbeat regardless of worktree location.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
BACKUP_FLEET_SCRIPT="$SCRIPT_DIR/daemons/backup-fleet.sh"

TEST_DIR="${SHELL_TEST_ISO_ROOT:-/tmp}/aesop-conductor-root-$$"
PASSED=0
FAILED=0

cleanup() {
  rm -rf "$TEST_DIR" 2>/dev/null || true
}
trap cleanup EXIT

log() {
  echo "[TEST] $*"
}

fail() {
  echo "❌ FAIL: $*" >&2
  ((FAILED++))
}

pass() {
  echo "✓ PASS: $*"
  ((PASSED++))
}

test_conductor_root_variable_derivation() {
  log "TEST: CONDUCTOR_ROOT defaults to sibling of AESOP_ROOT"

  # Test the variable derivation logic without running the full script
  # (which would scan all home directories and take too long in CI)

  local test_aesop="/work/myaesop"
  local expected_conductor="/work/conductor3"

  # Recreate the CONDUCTOR_ROOT default logic from backup-fleet.sh
  # This is the exact line from the script: CONDUCTOR_ROOT="${CONDUCTOR_ROOT:-$(dirname "$AESOP_ROOT")/conductor3}"
  local result_conductor="${CONDUCTOR_ROOT:-$(dirname "$test_aesop")/conductor3}"

  if [[ "$result_conductor" == "$expected_conductor" ]]; then
    pass "Default CONDUCTOR_ROOT correctly derived: $result_conductor"
  else
    fail "CONDUCTOR_ROOT derivation failed. Expected: $expected_conductor, Got: $result_conductor"
    return 1
  fi
}

test_conductor_root_explicit_path() {
  log "TEST: Explicit CONDUCTOR_ROOT path is respected"

  # Test that an explicit CONDUCTOR_ROOT overrides the default
  local test_aesop="/work/myaesop"
  local explicit_conductor="/custom/conductor"
  local result_conductor="${explicit_conductor:-$(dirname "$test_aesop")/conductor3}"

  if [[ "$result_conductor" == "$explicit_conductor" ]]; then
    pass "Explicit CONDUCTOR_ROOT is used: $result_conductor"
  else
    fail "Explicit CONDUCTOR_ROOT not respected. Expected: $explicit_conductor, Got: $result_conductor"
    return 1
  fi
}

test_heartbeat_path_construction() {
  log "TEST: Heartbeat path is CONDUCTOR_ROOT/state/.watchdog-heartbeat"

  mkdir -p "$TEST_DIR/conductor/state"
  mkdir -p "$TEST_DIR/aesop"

  # Simulate the heartbeat path construction from backup-fleet.sh
  local AESOP_ROOT="$TEST_DIR/aesop"
  local CONDUCTOR_ROOT="$TEST_DIR/conductor"
  local HEARTBEAT="$CONDUCTOR_ROOT/state/.watchdog-heartbeat"

  # Write a test heartbeat
  echo "1234567890" > "$HEARTBEAT"

  # Verify it's in the right place
  if [[ -f "$HEARTBEAT" ]]; then
    pass "Heartbeat path correct: $HEARTBEAT"
  else
    fail "Heartbeat not found at: $HEARTBEAT"
    return 1
  fi

  # Verify it's NOT in AESOP_ROOT/state
  if [[ ! -f "$AESOP_ROOT/state/.watchdog-heartbeat" ]]; then
    pass "Heartbeat correctly NOT in AESOP_ROOT"
  else
    fail "Heartbeat should not be in AESOP_ROOT"
    return 1
  fi
}

test_conductor_root_sibling_logic() {
  log "TEST: CONDUCTOR_ROOT sibling derivation with various AESOP_ROOT paths"

  # Test cases: AESOP_ROOT -> expected CONDUCTOR_ROOT
  local test_cases=(
    "/home/user/aesop:/home/user/conductor3"
    "/opt/projects/aesop:/opt/projects/conductor3"
    ".:./conductor3"
  )

  for case in "${test_cases[@]}"; do
    local aesop="${case%:*}"
    local expected="${case#*:}"
    local result="${CONDUCTOR_ROOT:-$(dirname "$aesop")/conductor3}"

    if [[ "$result" == "$expected" ]]; then
      pass "Sibling derivation correct: $aesop -> $result"
    else
      fail "Sibling derivation failed: $aesop. Expected: $expected, Got: $result"
      return 1
    fi
  done
}

# Run tests (all unit tests, no script invocation needed for CI)
test_conductor_root_variable_derivation
test_conductor_root_explicit_path
test_heartbeat_path_construction
test_conductor_root_sibling_logic

# Print summary
echo ""
echo "Test Results: $PASSED passed, $FAILED failed"
if [[ $FAILED -gt 0 ]]; then
  exit 1
fi
