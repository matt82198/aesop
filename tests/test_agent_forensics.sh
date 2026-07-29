#!/usr/bin/env bash
# Tests for agent-forensics.sh — incident forensics tool
# Reconstructs agent behavior at a commit and diffs behavior-controlling files.
#
# STRICTLY HERMETIC: every git init / file write / commit happens inside a
# subshell that has cd'd into a mktemp -d sandbox FIRST. The parent shell
# NEVER changes directory and NEVER derives a cd target from git output, so
# the real repository can never be written to or committed against.

set -uo pipefail

# Resolve the script-under-test to an ABSOLUTE path before any cd happens.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORENSICS_SCRIPT="$SCRIPT_DIR/tools/agent-forensics.sh"

# Single hermetic sandbox; all fixtures live under here and are removed on exit.
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/forensics-test.XXXXXX")"
RESULTS="$SANDBOX/.results"
: > "$RESULTS"
trap 'rm -rf "$SANDBOX"' EXIT

# pass/fail record to an absolute path so tallies survive subshells.
pass() {
  echo "  ✓ PASS: $*"
  printf 'P' >> "$RESULTS"
}

fail() {
  echo "  ❌ FAIL: $*" >&2
  printf 'F' >> "$RESULTS"
}

# Build a base repo with behavior-controlling files. MUST be called from
# inside a subshell that has already cd'd into a sandbox fixture directory.
build_base_repo() {
  # SELF-GUARD (incident 52d7be7): refuse to run inside an EXISTING repo —
  # a fresh sandbox dir is not one; the live primary/worktree is. This makes
  # caller-cd discipline unnecessary for safety.
  if git rev-parse --show-toplevel >/dev/null 2>&1; then
    printf 'FATAL: build_base_repo called inside an existing git repo (%s)
' "$PWD" >&2
    exit 1
  fi
  git init -q
  git config user.email "test@example.com"
  git config user.name "Test User"

  mkdir -p docs
  cat > docs/CARDINAL-RULES.md << 'RULES'
# Cardinal Rules

## Rule 1: Subagents are Haiku
All subagents must use Haiku to control cost.

## Rule 2: State is durable
STATE.md and BUILDLOG.md survive process wipes.
RULES

  cat > CLAUDE.md << 'CLAUDEMD'
# Project CLAUDE.md

- **daemons/** — Watchdog daemon
- **dash/** — TUI dashboard
- **monitor/** — Orchestration monitor
CLAUDEMD

  cat > STATE.md << 'STATEMD'
# STATE.md

## Current Phase
Exploration

## Next Steps
1. Create test suite
2. Add documentation
STATEMD

  git add -A
  git commit -q -m "init: project setup with base rules"
}

# --- Test 1: Forensics snapshot at a commit (basic output structure) ---
echo "[TEST] TEST 1: Forensics snapshot at a commit includes expected sections"
(
  cd "$SANDBOX" && mkdir -p t1 && cd t1 || exit 1
  build_base_repo
  commit=$(git rev-parse HEAD)

  output=$(bash "$FORENSICS_SCRIPT" "$commit" 2>&1)
  ec=$?

  [ "$ec" -eq 0 ] && pass "Exit code 0 on success" || fail "Exit code should be 0, got $ec"
  echo "$output" | grep -q "^# Forensics @" && pass "Forensics header present" || fail "Missing forensics header"
  echo "$output" | grep -q "^Commit:" && pass "Commit line present" || fail "Missing commit line"
  echo "$output" | grep -q "^Date:" && pass "Date line present" || fail "Missing date line"
  echo "$output" | grep -q "^Subject:" && pass "Subject line present" || fail "Missing subject line"
  echo "$output" | grep -q "## Rules (docs/CARDINAL-RULES.md)" && pass "Rules section present" || fail "Missing rules section"
  echo "$output" | grep -q "## CLAUDE.md" && pass "CLAUDE.md section present" || fail "Missing CLAUDE.md section"
  echo "$output" | grep -q "## STATE.md" && pass "STATE.md section present" || fail "Missing STATE.md section"
)

# --- Test 2: Forensics snapshot includes file content ---
echo "[TEST] TEST 2: Forensics snapshot includes file content"
(
  cd "$SANDBOX" && mkdir -p t2 && cd t2 || exit 1
  build_base_repo
  commit=$(git rev-parse HEAD)

  output=$(bash "$FORENSICS_SCRIPT" "$commit" 2>&1)

  echo "$output" | grep -q "daemons/" && pass "CLAUDE.md content included" || fail "CLAUDE.md content not found"
  echo "$output" | grep -q "Exploration" && pass "STATE.md content included" || fail "STATE.md content not found"
  echo "$output" | grep -q "Haiku" && pass "CARDINAL-RULES.md content included" || fail "Rules content not found"
)

# --- Test 3: Forensics --diff shows changed behavior files ---
echo "[TEST] TEST 3: Forensics --diff shows changed behavior-controlling files"
(
  cd "$SANDBOX" && mkdir -p t3 && cd t3 || exit 1
  build_base_repo
  commit_a=$(git rev-parse HEAD)

  cat > CLAUDE.md << 'CLAUDEMD2'
# Project CLAUDE.md (updated)

- **daemons/** — Watchdog daemon (v2)
- **dash/** — TUI dashboard (enhanced)
- **monitor/** — Orchestration monitor (new)
- **tools/** — Build utilities
CLAUDEMD2
  git add CLAUDE.md
  git commit -q -m "update: enhance CLAUDE.md"
  commit_b=$(git rev-parse HEAD)

  output=$(bash "$FORENSICS_SCRIPT" --diff "$commit_a" "$commit_b" 2>&1)
  ec=$?

  [ "$ec" -eq 0 ] && pass "Exit code 0 for valid --diff" || fail "Exit code should be 0, got $ec"
  echo "$output" | grep -q "# Behavior Diff" && pass "Behavior Diff header present" || fail "Missing behavior diff header"
  echo "$output" | grep -q "CLAUDE.md" && pass "CLAUDE.md shown in diff" || fail "CLAUDE.md not shown in diff"
  echo "$output" | grep -q "enhanced" && pass "Diff includes modified text" || fail "Diff content missing/truncated"
)

# --- Test 4: Forensics --diff with no behavior changes ---
echo "[TEST] TEST 4: Forensics --diff shows '(no changes)' for non-behavior edits"
(
  cd "$SANDBOX" && mkdir -p t4 && cd t4 || exit 1
  build_base_repo
  commit_a=$(git rev-parse HEAD)

  mkdir -p src
  echo "code" > src/main.py
  git add -A
  git commit -q -m "chore: add source code"
  commit_b=$(git rev-parse HEAD)

  output=$(bash "$FORENSICS_SCRIPT" --diff "$commit_a" "$commit_b" 2>&1)

  echo "$output" | grep -q "(no changes in behavior-controlling files)" \
    && pass "Correctly identified no behavior changes" \
    || fail "Should report no changes for non-behavior edits"
)

# --- Test 5: Forensics with unknown commit returns clean error ---
echo "[TEST] TEST 5: Forensics with unknown commit returns clean error (no raw git trace)"
(
  cd "$SANDBOX" && mkdir -p t5 && cd t5 || exit 1
  build_base_repo

  err=$(bash "$FORENSICS_SCRIPT" "invalid-commit-xyz" 2>&1)
  ec=$?

  [ "$ec" -ne 0 ] && pass "Non-zero exit for unknown commit" || fail "Should be non-zero for unknown commit"
  echo "$err" | grep -q "^Error:" && pass "Clean error message" || fail "Error not in expected format"
  echo "$err" | grep -qi "fatal:" && fail "Raw git trace leaked" || pass "No raw git trace in error"
)

# --- Test 6: Forensics --diff with missing second commit ---
echo "[TEST] TEST 6: Forensics --diff with missing second commit returns clean error"
(
  cd "$SANDBOX" && mkdir -p t6 && cd t6 || exit 1
  build_base_repo
  commit_a=$(git rev-parse HEAD)

  err=$(bash "$FORENSICS_SCRIPT" --diff "$commit_a" "invalid-xyz" 2>&1)
  ec=$?

  [ "$ec" -ne 0 ] && pass "Non-zero exit for invalid commit in --diff" || fail "Should be non-zero"
  echo "$err" | grep -q "^Error:" && pass "Clean error for missing commit in --diff" || fail "Error not in expected format"
)

# --- Test 7: Forensics with missing behavior files (not present at commit) ---
echo "[TEST] TEST 7: Forensics handles missing behavior files gracefully"
(
  cd "$SANDBOX" && mkdir -p t7 && cd t7 || exit 1
  git init -q
  git config user.email "test@example.com"
  git config user.name "Test User"
  echo "minimal" > README.md
  git add -A
  git commit -q -m "minimal repo"
  commit=$(git rev-parse HEAD)

  output=$(bash "$FORENSICS_SCRIPT" "$commit" 2>&1)
  ec=$?

  [ "$ec" -eq 0 ] && pass "Exit 0 even with missing behavior files" || fail "Exit should be 0, got $ec"
  echo "$output" | grep -q "(not present at this commit)" && pass "Reports missing CLAUDE.md" || fail "Should report missing files"
)

# --- Test 8: Forensics BUILDLOG.md truncation message ---
echo "[TEST] TEST 8: Forensics shows truncation message for long BUILDLOG.md"
(
  cd "$SANDBOX" && mkdir -p t8 && cd t8 || exit 1
  git init -q
  git config user.email "test@example.com"
  git config user.name "Test User"
  i=1
  while [ "$i" -le 50 ]; do
    echo "Line $i of BUILDLOG.md" >> BUILDLOG.md
    i=$((i + 1))
  done
  git add -A
  git commit -q -m "add long buildlog"
  commit=$(git rev-parse HEAD)

  output=$(bash "$FORENSICS_SCRIPT" "$commit" 2>&1)

  echo "$output" | grep -q "last 30 lines" && pass "BUILDLOG header indicates truncation" || fail "BUILDLOG header missing"
  # Only the last 30 lines of the 50-line file should appear (Line 1 must be absent).
  echo "$output" | grep -q "^Line 1 of BUILDLOG.md$" && fail "BUILDLOG not truncated (Line 1 present)" || pass "BUILDLOG truncated to last 30 lines"
  echo "$output" | grep -q "^Line 50 of BUILDLOG.md$" && pass "BUILDLOG tail present (Line 50)" || fail "BUILDLOG tail missing"
)

# --- Test 9: Forensics exit codes are clean (0 on success, 1 on error) ---
echo "[TEST] TEST 9: Forensics exit codes are clean (0 on success, 1 on error)"
(
  cd "$SANDBOX" && mkdir -p t9 && cd t9 || exit 1
  build_base_repo
  commit=$(git rev-parse HEAD)

  bash "$FORENSICS_SCRIPT" "$commit" >/dev/null 2>&1
  [ "$?" -eq 0 ] && pass "Success returns exit code 0" || fail "Success should return 0"

  bash "$FORENSICS_SCRIPT" "invalid" >/dev/null 2>&1
  [ "$?" -eq 1 ] && pass "Error returns exit code 1" || fail "Error should return 1"
)

# --- Test 10: Forensics --diff with same commit (no changes) ---
echo "[TEST] TEST 10: Forensics --diff with same commit shows no changes"
(
  cd "$SANDBOX" && mkdir -p t10 && cd t10 || exit 1
  build_base_repo
  commit=$(git rev-parse HEAD)

  output=$(bash "$FORENSICS_SCRIPT" --diff "$commit" "$commit" 2>&1)
  ec=$?

  [ "$ec" -eq 0 ] && pass "Exit 0 for identical commits" || fail "Exit should be 0, got $ec"
  echo "$output" | grep -q "(no changes in behavior-controlling files)" \
    && pass "No changes reported for identical commits" \
    || fail "Should report no changes for identical commits"
)

# --- Test 11: --help exits 0 with usage on stdout; genuine usage errors still exit non-zero ---
echo "[TEST] TEST 11: --help exits 0 with usage on stdout; bad usage still exits non-zero (FIX #4)"
(
  cd "$SANDBOX" && mkdir -p t11 && cd t11 || exit 1

  help_out=$(bash "$FORENSICS_SCRIPT" --help 2>/dev/null)
  help_ec=$?
  [ "$help_ec" -eq 0 ] && pass "--help exits 0" || fail "--help should exit 0, got $help_ec"
  echo "$help_out" | grep -q "^Usage:" && pass "--help prints usage to stdout" || fail "--help usage text missing from stdout"

  help_err=$(bash "$FORENSICS_SCRIPT" --help 2>&1 1>/dev/null)
  [ -z "$help_err" ] && pass "--help writes nothing to stderr" || fail "--help unexpectedly wrote to stderr: $help_err"

  short_out=$(bash "$FORENSICS_SCRIPT" -h 2>/dev/null)
  short_ec=$?
  [ "$short_ec" -eq 0 ] && pass "-h exits 0" || fail "-h should exit 0, got $short_ec"

  bad_out=$(bash "$FORENSICS_SCRIPT" 2>/dev/null)
  bad_ec=$?
  [ "$bad_ec" -ne 0 ] && pass "no-args usage error still exits non-zero" || fail "no-args usage error should exit non-zero, got $bad_ec"
  [ -z "$bad_out" ] && pass "no-args usage error prints nothing to stdout" || fail "no-args usage error unexpectedly wrote to stdout: $bad_out"

  bad_err=$(bash "$FORENSICS_SCRIPT" 2>&1 1>/dev/null)
  echo "$bad_err" | grep -q "^Usage:" && pass "no-args usage error prints usage to stderr" || fail "no-args usage error missing usage text on stderr"
)

# --- Tally results ---
PASSED=$(tr -cd 'P' < "$RESULTS" | wc -c)
FAILED=$(tr -cd 'F' < "$RESULTS" | wc -c)

printf '\n=== Test Summary ===\n'
printf 'Tests PASSED: %d\n' "$PASSED"
printf 'Tests FAILED: %d\n' "$FAILED"

if [ "$FAILED" -eq 0 ]; then
  printf '\nAll tests passed.\n'
  exit 0
else
  printf '\nSome tests failed.\n'
  exit 1
fi
