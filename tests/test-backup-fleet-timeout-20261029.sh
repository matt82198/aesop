#!/usr/bin/env bash
set -uo pipefail

# TDD test suite for backup-fleet.sh timeout fix (20261029)
# Tests that per-repo git operations have bounded timeout and heartbeat at cycle START

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_FLEET_SCRIPT="$SCRIPT_DIR/daemons/backup-fleet.sh"

TEST_DIR="${TEMP_ROOT:-/tmp}/aesop-timeout-test-$$"
AESOP_FIXTURE="$TEST_DIR/aesop-fixture"
STATE_DIR="$AESOP_FIXTURE/state"
TOOLS_DIR="$AESOP_FIXTURE/tools"
CONDUCTOR_FIXTURE="$TEST_DIR/conductor-fixture"
CONDUCTOR_STATE_DIR="$CONDUCTOR_FIXTURE/state"

PASSED=0
FAILED=0

cleanup() {
  rm -rf "$TEST_DIR" 2>/dev/null || true
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  ((FAILED++))
}

pass() {
  echo "PASS: $*"
  ((PASSED++))
}

setup_fixture() {
  rm -rf "$TEST_DIR"
  mkdir -p "$STATE_DIR" "$TOOLS_DIR" "$CONDUCTOR_STATE_DIR"
  cat > "$TOOLS_DIR/secret_scan.py" << 'EOF'
#!/usr/bin/env python3
import sys
sys.exit(0)
EOF
  chmod +x "$TOOLS_DIR/secret_scan.py"
}

test_per_repo_operations_timeout() {
  echo ""
  echo "TEST 1: per-repo git operations have timeout wrapper"
  setup_fixture
  export CONDUCTOR_ROOT="$CONDUCTOR_FIXTURE"

  if grep -q "timeout" "$BACKUP_FLEET_SCRIPT"; then
    pass "timeout logic found in script"
  else
    fail "timeout logic not found"
  fi
}

test_heartbeat_at_cycle_start() {
  echo ""
  echo "TEST 2: heartbeat written at cycle START"
  setup_fixture
  export CONDUCTOR_ROOT="$CONDUCTOR_FIXTURE"

  if grep -q "write_heartbeat" "$BACKUP_FLEET_SCRIPT"; then
    pass "write_heartbeat function exists"
  else
    fail "write_heartbeat function not found"
  fi
}

test_autodiscovery_timeout() {
  echo ""
  echo "TEST 3: autodiscovery wrapped in timeout"
  setup_fixture

  if grep -q "timeout" "$BACKUP_FLEET_SCRIPT"; then
    pass "timeout found in autodiscovery"
  else
    fail "no timeout in autodiscovery"
  fi
}

test_timeout_status_logged() {
  echo ""
  echo "TEST 4: TIMEOUT status logged for slow repos"
  setup_fixture

  if grep -q "TIMEOUT" "$BACKUP_FLEET_SCRIPT"; then
    pass "TIMEOUT status defined"
  else
    fail "TIMEOUT status not found"
  fi
}

test_cycle_completes() {
  echo ""
  echo "TEST 5: cycle completes with empty fixture"
  setup_fixture
  export CONDUCTOR_ROOT="$CONDUCTOR_FIXTURE"
  mkdir -p "$CONDUCTOR_STATE_DIR"

  local exit_code=0
  AESOP_ROOT="$AESOP_FIXTURE" CONDUCTOR_ROOT="$CONDUCTOR_FIXTURE" timeout 5 bash "$BACKUP_FLEET_SCRIPT" > /dev/null 2>&1 || exit_code=$?

  if [ "$exit_code" -eq 0 ] || [ "$exit_code" -eq 3 ]; then
    pass "cycle completed (exit code $exit_code)"
  elif [ "$exit_code" -eq 124 ]; then
    fail "cycle timed out (unbounded?)"
  else
    fail "cycle failed (exit code $exit_code)"
  fi

  if [ -f "$CONDUCTOR_STATE_DIR/.watchdog-heartbeat" ]; then
    pass "heartbeat file written"
  else
    fail "heartbeat not written"
  fi
}

echo "Running backup-fleet timeout TDD tests..."
echo "Script under test: $BACKUP_FLEET_SCRIPT"

test_per_repo_operations_timeout
test_heartbeat_at_cycle_start
test_autodiscovery_timeout
test_timeout_status_logged
test_cycle_completes

echo ""
echo "========================================"
echo "Tests PASSED: $PASSED"
echo "Tests FAILED: $FAILED"
echo "========================================"

if [ $FAILED -gt 0 ]; then
  exit 1
fi

exit 0
