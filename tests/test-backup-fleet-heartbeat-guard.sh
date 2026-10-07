#!/usr/bin/env bash
set -uo pipefail

# Behavioral proof: backup-fleet.sh must NEVER silently derive a sibling
# CONDUCTOR_ROOT (and write the real fleet heartbeat) when AESOP_ROOT does
# not look like the canonical ~/aesop checkout.
#
# Incident (recurrence, 2026-10-07): the live heartbeat
# C:/Users/matt8/conductor3/state/.watchdog-heartbeat was overwritten while
# lanes ran local test/gate suites in sibling worktrees
# (C:/Users/matt8/aesop-wt-*). Every worktree/test fixture whose AESOP_ROOT
# is a DIRECT CHILD of the real $HOME is a sibling of the real ~/conductor3,
# so backup-fleet.sh's old unconditional fallback
# (CONDUCTOR_ROOT="${CONDUCTOR_ROOT:-$(dirname "$AESOP_ROOT")/conductor3}")
# silently resolved to the REAL fleet-state root for ANY such AESOP_ROOT --
# not just the canonical ~/aesop tree.
#
# Fix: only auto-derive the sibling CONDUCTOR_ROOT when AESOP_ROOT's
# basename is exactly "aesop" (the canonical tree's name). Any other
# AESOP_ROOT (a worktree like "aesop-wt-*", or any test fixture) must
# either pass CONDUCTOR_ROOT explicitly, or the heartbeat write is skipped
# entirely (same fail-closed philosophy as the existing "failure paths
# skip the write" contract).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_FLEET_SCRIPT="$SCRIPT_DIR/daemons/backup-fleet.sh"

TEST_DIR="${TMPDIR:-/tmp}/aesop-hb-guard-$$"
PASSED=0
FAILED=0

cleanup() {
  rm -rf "$TEST_DIR" 2>/dev/null || true
}
trap cleanup EXIT

log()  { echo "[TEST] $*"; }
fail() { echo "FAIL: $*" >&2; ((FAILED++)); }
pass() { echo "PASS: $*"; ((PASSED++)); }

setup() {
  rm -rf "$TEST_DIR"
  mkdir -p "$TEST_DIR"
}

# Scope backup-fleet.sh's repo discovery to a single, nonexistent repo path
# via aesop.config.json so a cycle never falls through to full-home
# autodiscovery (which scans every real repo under $HOME and is both slow
# and not what these heartbeat-guard tests are about).
write_scoped_config() {
  local aesop_root="$1"
  mkdir -p "$aesop_root"
  cat > "$aesop_root/aesop.config.json" <<EOF
{"repos": [{"path": "$aesop_root/state/.no-such-repo"}]}
EOF
}

# ---------------------------------------------------------------------------
# Test 1: AESOP_ROOT whose basename is NOT "aesop" (simulating a worktree
# like aesop-wt-hbpoll, or any test fixture) + CONDUCTOR_ROOT unset must
# NOT write a heartbeat into the sibling-derived path at all.
# ---------------------------------------------------------------------------
test_non_canonical_aesop_root_does_not_derive_sibling_heartbeat() {
  log "TEST 1: non-canonical AESOP_ROOT (worktree-shaped) must not write a sibling heartbeat"
  setup

  local fake_home="$TEST_DIR/home"
  local aesop_root="$fake_home/aesop-wt-fixture"   # worktree-shaped, basename != "aesop"
  local sibling_conductor_hb="$fake_home/conductor3/state/.watchdog-heartbeat"

  mkdir -p "$aesop_root/state" "$fake_home/conductor3/state"
  echo "PRE-EXISTING-REAL-VALUE" > "$sibling_conductor_hb"
  write_scoped_config "$aesop_root"

  ( unset CONDUCTOR_ROOT
    AESOP_ROOT="$aesop_root" bash "$BACKUP_FLEET_SCRIPT" > "$TEST_DIR/out1.log" 2>&1 )

  local after
  after=$(cat "$sibling_conductor_hb")
  if [ "$after" != "PRE-EXISTING-REAL-VALUE" ]; then
    fail "sibling conductor3 heartbeat was modified by a non-canonical AESOP_ROOT run (got: $after)"
    cat "$TEST_DIR/out1.log" >&2
    return 1
  fi
  pass "sibling conductor3 heartbeat left untouched for worktree-shaped AESOP_ROOT"
}

# ---------------------------------------------------------------------------
# Test 2: explicit CONDUCTOR_ROOT always wins, even for a worktree-shaped
# AESOP_ROOT -- the override escape hatch must keep working.
# ---------------------------------------------------------------------------
test_explicit_conductor_root_still_honored() {
  log "TEST 2: explicit CONDUCTOR_ROOT override is still honored"
  setup

  local aesop_root="$TEST_DIR/aesop-wt-fixture"
  local conductor_root="$TEST_DIR/explicit-conductor"
  mkdir -p "$aesop_root/state" "$conductor_root/state"
  write_scoped_config "$aesop_root"

  AESOP_ROOT="$aesop_root" CONDUCTOR_ROOT="$conductor_root" \
    bash "$BACKUP_FLEET_SCRIPT" > "$TEST_DIR/out2.log" 2>&1

  if [ ! -f "$conductor_root/state/.watchdog-heartbeat" ]; then
    fail "explicit CONDUCTOR_ROOT heartbeat was not written"
    cat "$TEST_DIR/out2.log" >&2
    return 1
  fi
  pass "explicit CONDUCTOR_ROOT heartbeat written as expected"
}

# ---------------------------------------------------------------------------
# Test 3: canonical AESOP_ROOT (basename "aesop") with CONDUCTOR_ROOT unset
# must keep deriving the sibling conductor3/state heartbeat -- production
# behavior on the real tree must not regress.
# ---------------------------------------------------------------------------
test_canonical_aesop_root_still_derives_sibling_heartbeat() {
  log "TEST 3: canonical AESOP_ROOT (basename 'aesop') still derives the sibling heartbeat"
  setup

  local fake_home="$TEST_DIR/home2"
  local aesop_root="$fake_home/aesop"   # canonical basename
  mkdir -p "$aesop_root/state" "$fake_home/conductor3/state"
  write_scoped_config "$aesop_root"

  ( unset CONDUCTOR_ROOT
    AESOP_ROOT="$aesop_root" bash "$BACKUP_FLEET_SCRIPT" > "$TEST_DIR/out3.log" 2>&1 )

  if [ ! -f "$fake_home/conductor3/state/.watchdog-heartbeat" ]; then
    fail "canonical AESOP_ROOT did not derive+write the sibling conductor3 heartbeat (regression)"
    cat "$TEST_DIR/out3.log" >&2
    return 1
  fi
  pass "canonical AESOP_ROOT derivation preserved"
}

test_non_canonical_aesop_root_does_not_derive_sibling_heartbeat
test_explicit_conductor_root_still_honored
test_canonical_aesop_root_still_derives_sibling_heartbeat

echo ""
echo "Test Results: $PASSED passed, $FAILED failed"
if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
