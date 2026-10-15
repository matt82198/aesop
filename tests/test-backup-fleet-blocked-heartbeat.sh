#!/usr/bin/env bash
set -uo pipefail

# Behavioral proof for the blocked-cycle contract of daemons/backup-fleet.sh:
#   1. a cycle with >=1 BLOCKED repo writes the heartbeat AND
#      $CONDUCTOR_ROOT/state/.watchdog-blocked (blocked repo names, one per
#      line), and exits 3 (a WARN; the daemon stays up).
#   2. a later clean cycle removes .watchdog-blocked and writes the heartbeat.
#   3. a hard failure (cycle cannot stage its scratch file -> exit 1) writes NO
#      heartbeat, so staleness stays detectable.
#   4. the secret scanner's HIGH lines (file:line, rule, MASKED value) reach
#      FLEET-BACKUP.log; the raw secret value never does.
# HERMETIC: every path lives under a mktemp dir; CONDUCTOR_ROOT is explicit;
# git runs with an empty global config so no real user config is read.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_FLEET_SCRIPT="$SCRIPT_DIR/daemons/backup-fleet.sh"
SECRET_SCAN_SRC="$SCRIPT_DIR/tools/secret_scan.py"

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

export GIT_CONFIG_GLOBAL="$TEST_DIR/empty-gitconfig"
export GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME="Aesop Test" GIT_AUTHOR_EMAIL="test@example.invalid"
export GIT_COMMITTER_NAME="Aesop Test" GIT_COMMITTER_EMAIL="test@example.invalid"
: > "$GIT_CONFIG_GLOBAL"

log()  { echo "[TEST] $*"; }
fail() { echo "FAIL: $*" >&2; FAILED=$((FAILED + 1)); }
pass() { echo "PASS: $*"; PASSED=$((PASSED + 1)); }

# Build a fresh fixture: $fx/aesop (AESOP_ROOT, with the real scanner copied in),
# $fx/conductor (explicit CONDUCTOR_ROOT), $fx/repo (one git repo with a bare origin).
new_fixture() {
  local fx="$1"
  rm -rf "$fx"
  mkdir -p "$fx/aesop/state" "$fx/aesop/tools" "$fx/conductor/state"
  cp "$SECRET_SCAN_SRC" "$fx/aesop/tools/secret_scan.py"
  printf '{"repos": [{"path": "%s/repo"}]}\n' "$fx" > "$fx/aesop/aesop.config.json"
  git init -q --bare "$fx/repo-origin.git"
  git init -q "$fx/repo"
  git -C "$fx/repo" symbolic-ref HEAD refs/heads/main
  echo "clean seed" > "$fx/repo/README.txt"
  git -C "$fx/repo" add README.txt
  git -C "$fx/repo" commit -q -m "seed"
  git -C "$fx/repo" remote add origin "$fx/repo-origin.git"
  git -C "$fx/repo" push -q -u origin main
}

# Run one backup cycle with CONDUCTOR_ROOT explicit. Extra args are env
# assignments (e.g. TMPDIR=...). Output is kept in $fx/cycle.out.
run_cycle() {
  local fx="$1"; shift
  env -u CONDUCTOR_ROOT AESOP_ROOT="$fx/aesop" CONDUCTOR_ROOT="$fx/conductor" "$@" \
    bash "$BACKUP_FLEET_SCRIPT" > "$fx/cycle.out" 2>&1
}

# ---------------------------------------------------------------------------
# Test 1: blocked cycle -> heartbeat written, marker names the repo, exit 3,
# scanner HIGH line reaches FLEET-BACKUP.log without the raw secret value.
# ---------------------------------------------------------------------------
test_blocked_cycle_writes_heartbeat_and_marker() {
  log "TEST 1: blocked cycle writes heartbeat + .watchdog-blocked, exits 3, logs masked HIGH line"
  local fx="$TEST_DIR/t1"
  new_fixture "$fx"
  # Runtime-assembled dummy AWS-shaped key (never a literal in source).
  printf 'aws_key = %s\n' "AKIA""IOSFODNN7EXAMPLE" > "$fx/repo/notes-leak.txt"

  run_cycle "$fx"
  local rc=$?

  if [ "$rc" -ne 3 ]; then
    fail "blocked cycle exit code expected 3, got $rc"; cat "$fx/cycle.out" >&2; return 1
  fi
  local hb="$fx/conductor/state/.watchdog-heartbeat"
  if [ ! -f "$hb" ] || ! [[ "$(cat "$hb")" =~ ^[0-9]+$ ]]; then
    fail "blocked cycle did not write the heartbeat at $hb"; return 1
  fi
  local marker="$fx/conductor/state/.watchdog-blocked"
  if [ "$(cat "$marker" 2>/dev/null)" != "repo" ]; then
    fail "blocked marker expected to name 'repo', got: $(cat "$marker" 2>/dev/null || echo '<missing>')"; return 1
  fi
  local flog="$fx/aesop/state/FLEET-BACKUP.log"
  if ! grep -q "SECRET-SCAN .*HIGH .*notes-leak.txt" "$flog" 2>/dev/null; then
    fail "FLEET-BACKUP.log lacks a HIGH line naming notes-leak.txt"; cat "$flog" >&2 2>/dev/null; return 1
  fi
  if grep -q "IOSFODNN7EXAMPLE" "$flog" 2>/dev/null; then
    fail "raw secret value leaked into FLEET-BACKUP.log"; return 1
  fi
  pass "blocked cycle: heartbeat + marker('repo') + exit 3 + masked HIGH line in log"
}

# ---------------------------------------------------------------------------
# Test 2: a clean cycle removes a stale .watchdog-blocked and writes heartbeat.
# ---------------------------------------------------------------------------
test_clean_cycle_removes_blocked_marker() {
  log "TEST 2: clean cycle removes .watchdog-blocked and exits 0"
  local fx="$TEST_DIR/t2"
  new_fixture "$fx"
  local marker="$fx/conductor/state/.watchdog-blocked"
  echo "repo" > "$marker"

  run_cycle "$fx"
  local rc=$?

  if [ "$rc" -ne 0 ]; then
    fail "clean cycle exit code expected 0, got $rc"; cat "$fx/cycle.out" >&2; return 1
  fi
  if [ -e "$marker" ]; then
    fail "clean cycle left .watchdog-blocked behind"; return 1
  fi
  if [ ! -f "$fx/conductor/state/.watchdog-heartbeat" ]; then
    fail "clean cycle did not write the heartbeat"; return 1
  fi
  pass "clean cycle: marker removed, heartbeat written, exit 0"
}

# ---------------------------------------------------------------------------
# Test 3: hard failure (scratch file cannot be created -> exit 1) writes NO
# heartbeat. TMPDIR points at a directory that does not exist.
# ---------------------------------------------------------------------------
test_hard_failure_writes_no_heartbeat() {
  log "TEST 3: hard failure (exit 1) must not write the heartbeat"
  local fx="$TEST_DIR/t3"
  new_fixture "$fx"

  run_cycle "$fx" TMPDIR="$fx/no-such-tmpdir"
  local rc=$?

  if [ "$rc" -ne 1 ]; then
    fail "hard failure exit code expected 1, got $rc"; cat "$fx/cycle.out" >&2; return 1
  fi
  if [ -e "$fx/conductor/state/.watchdog-heartbeat" ]; then
    fail "hard failure (exit 1) wrote the heartbeat -- a failed cycle must read as stale"; return 1
  fi
  pass "hard failure: exit 1 and no heartbeat"
}

test_blocked_cycle_writes_heartbeat_and_marker
test_clean_cycle_removes_blocked_marker
test_hard_failure_writes_no_heartbeat

echo ""
echo "Test Results: $PASSED passed, $FAILED failed"
if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
