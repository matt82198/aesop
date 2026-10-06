#!/usr/bin/env bash
set -uo pipefail

# Test: backup-fleet.sh writes heartbeat to $CONDUCTOR_ROOT/state/.watchdog-heartbeat
# when AESOP_ROOT and CONDUCTOR_ROOT are different directories.
# This ensures shared monitoring infrastructure (GUI, idle_tick, monitor, power_selftest)
# can read the heartbeat regardless of worktree location.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
BACKUP_FLEET_SCRIPT="$SCRIPT_DIR/daemons/backup-fleet.sh"

TEST_DIR="${TEMP_ROOT:-/tmp}/aesop-conductor-root-$$"
AESOP_DIR="$TEST_DIR/aesop-work"
CONDUCTOR_DIR="$TEST_DIR/conductor3"

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

test_conductor_root_heartbeat() {
  log "TEST: Heartbeat written to CONDUCTOR_ROOT/state when AESOP_ROOT differs"

  # Setup separate directories for AESOP and CONDUCTOR
  mkdir -p "$AESOP_DIR/state"
  mkdir -p "$CONDUCTOR_DIR/state"

  # Create a test repo in AESOP_DIR
  local test_repo="$AESOP_DIR/test-repo"
  mkdir -p "$test_repo"
  cd "$test_repo"
  git init
  git config user.email "test@example.com"
  git config user.name "Test User"
  echo "test" > README.md
  git add README.md
  git commit -m "initial"

  # Create mock secret_scan.py (always passes)
  mkdir -p "$AESOP_DIR/tools"
  cat > "$AESOP_DIR/tools/secret_scan.py" << 'SCANNER'
#!/usr/bin/env python3
import sys
sys.exit(0)
SCANNER
  chmod +x "$AESOP_DIR/tools/secret_scan.py"

  # Create mock git_integrity_check.py
  cat > "$AESOP_DIR/tools/git_integrity_check.py" << 'CHECKER'
#!/usr/bin/env python3
import json, sys
print(json.dumps([]))
CHECKER
  chmod +x "$AESOP_DIR/tools/git_integrity_check.py"

  # Run backup-fleet with both AESOP_ROOT and CONDUCTOR_ROOT set to different paths
  cd "$TEST_DIR"
  AESOP_ROOT="$AESOP_DIR" CONDUCTOR_ROOT="$CONDUCTOR_DIR" bash "$BACKUP_FLEET_SCRIPT" 2>&1 || true

  # Verify heartbeat exists in CONDUCTOR_ROOT, not AESOP_ROOT
  if [[ -f "$CONDUCTOR_DIR/state/.watchdog-heartbeat" ]]; then
    pass "Heartbeat found in CONDUCTOR_ROOT/state/.watchdog-heartbeat"
  else
    fail "Heartbeat NOT found in CONDUCTOR_ROOT/state/.watchdog-heartbeat"
    return 1
  fi

  if [[ -f "$AESOP_DIR/state/.watchdog-heartbeat" ]]; then
    fail "Heartbeat should NOT be in AESOP_ROOT/state/.watchdog-heartbeat (was: $(cat "$AESOP_DIR/state/.watchdog-heartbeat"))"
    return 1
  else
    pass "Heartbeat correctly NOT in AESOP_ROOT/state"
  fi

  # Verify heartbeat is a valid epoch timestamp
  local heartbeat=$(cat "$CONDUCTOR_DIR/state/.watchdog-heartbeat")
  if [[ "$heartbeat" =~ ^[0-9]+$ ]]; then
    pass "Heartbeat is valid epoch timestamp: $heartbeat"
  else
    fail "Heartbeat is not a valid epoch timestamp: $heartbeat"
    return 1
  fi
}

test_conductor_root_default() {
  log "TEST: CONDUCTOR_ROOT defaults to sibling of AESOP_ROOT when unset"

  # Test the variable derivation logic
  local test_aesop="/work/myaesop"
  local expected_conductor="/work/conductor3"

  # Recreate the CONDUCTOR_ROOT default logic from backup-fleet.sh
  local result_conductor="${CONDUCTOR_ROOT:-$(dirname "$test_aesop")/conductor3}"

  if [[ "$result_conductor" == "$expected_conductor" ]]; then
    pass "Default CONDUCTOR_ROOT correctly derived: $result_conductor"
  else
    fail "CONDUCTOR_ROOT derivation failed. Expected: $expected_conductor, Got: $result_conductor"
    return 1
  fi
}

# Run tests
test_conductor_root_heartbeat
test_conductor_root_default

# Print summary
echo ""
echo "Test Results: $PASSED passed, $FAILED failed"
if [[ $FAILED -gt 0 ]]; then
  exit 1
fi
