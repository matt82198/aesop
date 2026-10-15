#!/usr/bin/env bash
# Behavioral test for the live-heartbeat tripwire in tools/run_shell_tests.sh.
# Drives live_hb_snapshot / live_hb_verdict on fixture files only (never the
# real ~/conductor3 heartbeat). Incident 2026-10-14: a test wrote the
# placeholder "1234567890" into the LIVE watchdog and monitor heartbeats.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$SCRIPT_DIR/tools/run_shell_tests.sh"

TEST_DIR="${TMPDIR:-/tmp}/aesop-live-hb-$$"
PASSED=0
FAILED=0
cleanup() { rm -rf "$TEST_DIR" 2>/dev/null || true; }
trap cleanup EXIT

# Sourcing the runner defines its functions only (its main block is guarded).
# shellcheck source=/dev/null
source "$RUNNER"

pass() { echo "PASS: $*"; PASSED=$((PASSED + 1)); }
fail() { echo "FAIL: $*" >&2; FAILED=$((FAILED + 1)); }

# Every fixture heartbeat lives under TEST_DIR; anything else aborts the test.
fixture_hb() {
  local p="$TEST_DIR/$1/state/.watchdog-heartbeat"
  case "$p" in
    "$TEST_DIR"/*) ;;
    *) echo "GUARD: fixture path escaped TEST_DIR: $p" >&2; exit 2 ;;
  esac
  mkdir -p "$(dirname "$p")"
  printf '%s' "$p"
}

expect_rc() {  # $1 = label, $2 = expected rc, $3 = actual rc
  if [ "$2" -eq "$3" ]; then pass "$1 (rc=$3)"; else fail "$1 (expected rc=$2, got rc=$3)"; fi
}

# 1. Unchanged live heartbeat is clean.
f="$(fixture_hb unchanged)"; echo "1792024000" > "$f"
before="$(live_hb_snapshot "$f")"; start="$(date +%s)"
live_hb_verdict "$before" "$f" "$start" >/dev/null 2>&1; expect_rc "unchanged heartbeat passes" 0 $?

# 2. A test writing the placeholder over a live heartbeat must FAIL.
f="$(fixture_hb placeholder)"; echo "1792024000" > "$f"
before="$(live_hb_snapshot "$f")"; start="$(date +%s)"
echo "1234567890" > "$f"
live_hb_verdict "$before" "$f" "$start" >/dev/null 2>&1; expect_rc "placeholder 1234567890 write FAILS the tripwire" 1 $?

# 3. A legitimate daemon cycle (current epoch, inside the run window) passes.
f="$(fixture_hb daemon)"; echo "1792024000" > "$f"
before="$(live_hb_snapshot "$f")"; start="$(date +%s)"
date +%s > "$f"
live_hb_verdict "$before" "$f" "$start" >/dev/null 2>&1; expect_rc "in-window daemon heartbeat advance passes" 0 $?

# 4. Heartbeat created from nothing with the placeholder must FAIL.
f="$(fixture_hb created)"; rm -f "$f"
before="$(live_hb_snapshot "$f")"; start="$(date +%s)"
echo "1234567890" > "$f"
live_hb_verdict "$before" "$f" "$start" >/dev/null 2>&1; expect_rc "placeholder created from absent FAILS" 1 $?

# 5. Heartbeat deleted during the run must FAIL.
f="$(fixture_hb deleted)"; echo "1792024000" > "$f"
before="$(live_hb_snapshot "$f")"; start="$(date +%s)"
rm -f "$f"
live_hb_verdict "$before" "$f" "$start" >/dev/null 2>&1; expect_rc "deleted heartbeat FAILS" 1 $?

echo ""
echo "Test Results: $PASSED passed, $FAILED failed"
if [ "$FAILED" -gt 0 ]; then exit 1; fi
