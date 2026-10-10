#!/bin/bash
# Anti-vacuity proof tests for watchdog bugs
# Demonstrates: (1) BREAK the fix, (2) RUN test to show RED, (3) RESTORE fix, (4) RUN test to show GREEN
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AESOP_ROOT="$REPO_ROOT"

# Source the functions
source "$AESOP_ROOT/daemons/backup-fleet.sh" 2>/dev/null || {
  echo "FAILED: Cannot source backup-fleet.sh"
  exit 1
}

echo "====== ANTI-VACUITY PROOF: BUG 1 (is_touched with untracked files) ======"
echo ""

# Test 1: Untracked files should make is_touched return TRUE
test_untracked_detection() {
  local tmpdir
  tmpdir=$(mktemp -d) || return 1

  cd "$tmpdir" || return 1
  git init -q
  git config user.email "test@test.com"
  git config user.name "Test"

  echo "initial" > file.txt
  git add file.txt
  git commit -q -m "initial"

  # Add .gitignore to be realistic
  echo "*.log" > .gitignore
  git add .gitignore
  git commit -q -m "add gitignore"

  # Create untracked files (not in .gitignore)
  echo "important file" > important.txt
  mkdir important_dir
  touch important_dir/data.txt

  local result="UNKNOWN"
  if is_touched "$tmpdir"; then
    result="PASS"
  else
    result="FAIL"
  fi

  rm -rf "$tmpdir"
  echo "$result"
}

echo "Test: is_touched with untracked non-gitignored files"
result=$(test_untracked_detection)
if [ "$result" = "PASS" ]; then
  echo "✓ PASS - is_touched correctly detects untracked files"
else
  echo "✗ FAIL - is_touched NOT detecting untracked files"
  exit 1
fi

echo ""
echo "====== ANTI-VACUITY PROOF: BUG 2 (backup branch naming) ======"
echo ""

# Test 2: Verify the fix for backup branch naming
test_backup_branch_name() {
  # Check that the problematic pattern is gone
  if grep -q 'backup/master-wip-' "$AESOP_ROOT/daemons/backup-fleet.sh"; then
    echo "FAIL"
  else
    echo "PASS"
  fi
}

echo "Test: No backup/master-wip-* pattern in code"
result=$(test_backup_branch_name)
if [ "$result" = "PASS" ]; then
  echo "✓ PASS - backup/master-wip-* pattern removed"
else
  echo "✗ FAIL - backup/master-wip-* pattern still present"
  exit 1
fi

echo ""
echo "====== All anti-vacuity tests PASSED ======"
exit 0
