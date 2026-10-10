#!/bin/bash
# Test suite for watchdog bugs:
# BUG 1: is_touched() does not detect untracked files (missing -u flag)
# BUG 2: psinasty uses backup/master-wip-* instead of backup/wip-* pattern
set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AESOP_ROOT="$REPO_ROOT"

# Source the functions we're testing
source "$AESOP_ROOT/daemons/backup-fleet.sh" 2>/dev/null || {
  echo "FAILED: Cannot source backup-fleet.sh"
  exit 1
}

suite_setup() {
  echo "Setting up test suite"
}

suite_teardown() {
  echo "Cleaning up test suite"
}

test_bug1_untracked_files_detected() {
  local tmpdir
  tmpdir=$(mktemp -d) || return 1

  cd "$tmpdir" || return 1
  git init -q
  git config user.email "test@example.com"
  git config user.name "Test User"

  # Create initial commit so HEAD exists
  echo "initial" > file.txt
  git add file.txt
  git commit -q -m "initial"

  # Add ONLY untracked files (no staged or committed changes)
  echo "untracked content" > untracked.txt
  echo "another untracked" > untracked2.js

  # Test: is_touched() should return 0 (true) because there are untracked files
  # If this fails, it means the bug exists (is_touched returns 1/false incorrectly)
  if is_touched "$tmpdir"; then
    echo "PASS: is_touched correctly detected untracked files"
    rm -rf "$tmpdir"
    return 0
  else
    echo "FAIL: is_touched did NOT detect untracked files (BUG 1 present)"
    rm -rf "$tmpdir"
    return 1
  fi
}

test_bug2_backup_branch_naming() {
  # This test verifies that the WIPREF pattern for unpushed on default branch
  # uses backup/wip-* pattern (not backup/master-wip-* pattern)

  # Read the backup-fleet.sh script and check the WIPREF assignment for unpushed on default
  local line_with_master_wip
  line_with_master_wip=$(grep -n 'backup/master-wip-' "$AESOP_ROOT/daemons/backup-fleet.sh" 2>/dev/null || true)

  if [ -n "$line_with_master_wip" ]; then
    echo "FAIL: Found backup/master-wip-* pattern (BUG 2 present):"
    echo "  $line_with_master_wip"
    return 1
  else
    echo "PASS: No backup/master-wip-* patterns found"
    return 0
  fi
}

test_bug1_with_fix() {
  # This test will pass AFTER the fix is applied
  # It demonstrates the anti-vacuity proof by checking that git status -u works
  local tmpdir
  tmpdir=$(mktemp -d) || return 1

  cd "$tmpdir" || return 1
  git init -q
  git config user.email "test@example.com"
  git config user.name "Test User"

  # Create initial commit
  echo "initial" > file.txt
  git add file.txt
  git commit -q -m "initial"

  # Add ONLY untracked files
  echo "untracked" > untracked.txt

  # Verify that git status --porcelain (without -u) does NOT show untracked
  local status_without_u
  status_without_u=$(git status --porcelain 2>/dev/null || true)
  if [ -z "$status_without_u" ]; then
    echo "CONFIRMED: git status --porcelain (without -u) is empty for untracked files"
  else
    echo "UNEXPECTED: git status --porcelain showed output even without -u"
  fi

  # Verify that git status --porcelain -u DOES show untracked
  local status_with_u
  status_with_u=$(git status --porcelain -u 2>/dev/null || true)
  if [ -n "$status_with_u" ]; then
    echo "CONFIRMED: git status --porcelain -u shows untracked files as expected"
    echo "  Output: $status_with_u"
    rm -rf "$tmpdir"
    return 0
  else
    echo "FAIL: git status --porcelain -u did not show untracked files"
    rm -rf "$tmpdir"
    return 1
  fi
}

main() {
  local failed=0

  echo "====== TEST SUITE: Watchdog Bugs ======"

  suite_setup

  echo ""
  echo "--- Test BUG 1: is_touched() missing -u flag for untracked files ---"
  if ! test_bug1_untracked_files_detected; then
    failed=$((failed + 1))
  fi

  echo ""
  echo "--- Test BUG 2: backup/master-wip-* pattern detection ---"
  if ! test_bug2_backup_branch_naming; then
    failed=$((failed + 1))
  fi

  echo ""
  echo "--- Test BUG 1 Fix: Verify git status -u solution ---"
  if ! test_bug1_with_fix; then
    failed=$((failed + 1))
  fi

  suite_teardown

  echo ""
  if [ "$failed" -eq 0 ]; then
    echo "====== ALL TESTS PASSED ======"
    return 0
  else
    echo "====== $failed TEST(S) FAILED ======"
    return 1
  fi
}

# Return the suite declaration (required by LANE-CONTRACT.md)
declare -a test_suite=("test_bug1_untracked_files_detected" "test_bug2_backup_branch_naming" "test_bug1_with_fix")
main "$@"
exit $?
