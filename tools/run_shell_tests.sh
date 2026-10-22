#!/usr/bin/env bash
# INDEX: Glob-based shell test runner: discovers and runs all shell tests sequentially via glob patterns (tests/*.test.sh, tests/test_*.sh, tests/test-*.sh, hooks/pre-push-policy.sh --test); fails fast with clear output; CRLF-safe, no line continuations; CLI: `bash tools/run_shell_tests.sh [REPO_ROOT]`; invoked as npm run test:sh in package.json; replaces hand-maintained explicit test chain (kills conflict magnet)
set -uo pipefail

# Glob-based shell test runner — discovers and runs all shell tests sequentially.
# Discovers from: tests/*.test.sh tests/test_*.sh tests/test-*.sh hooks/pre-push-policy.sh --test
# Fails fast with clear output.
#
# Usage:
#   bash run_shell_tests.sh [REPO_ROOT]          # Run all discovered tests
#   bash run_shell_tests.sh --list [REPO_ROOT]   # List discovered test files (one per line)

# Discover test files (returns array in global discovered_tests)
discover_tests() {
  discovered_tests=()
  discovered_hooks=()

  # Discover tests/*.test.sh
  for test in "$TESTS_DIR"/*.test.sh; do
    if [ -f "$test" ]; then
      discovered_tests+=("$test")
    fi
  done

  # Discover tests/test_*.sh
  for test in "$TESTS_DIR"/test_*.sh; do
    if [ -f "$test" ]; then
      discovered_tests+=("$test")
    fi
  done

  # Discover tests/test-*.sh
  for test in "$TESTS_DIR"/test-*.sh; do
    if [ -f "$test" ]; then
      discovered_tests+=("$test")
    fi
  done

  # Check for hooks/pre-push-policy.sh --test
  if [ -f "$HOOKS_DIR/pre-push-policy.sh" ]; then
    discovered_hooks+=("$HOOKS_DIR/pre-push-policy.sh")
  fi
}

# Live fleet-state heartbeat tripwire (incident 2026-10-14: a test wrote the
# placeholder "1234567890" into the LIVE fleet watchdog and monitor
# heartbeats). Snapshot = "<content>|<mtime>". A change during the run is
# clean ONLY if the new content is a daemon-style epoch inside the run window
# [start-60, now+60]; anything else (placeholder, stale epoch, deletion) FAILS.
live_hb_snapshot() {
  local f="$1"
  if [ -f "$f" ]; then
    printf '%s|%s' "$(tr -d '[:space:]' < "$f" 2>/dev/null)" "$(stat -c %Y "$f" 2>/dev/null || echo 0)"
  else
    printf 'ABSENT|0'
  fi
}

# $1 = before snapshot, $2 = live heartbeat path, $3 = run start epoch.
# Returns 0 when clean, 1 (and prints FAIL to stderr) when a test polluted it.
live_hb_verdict() {
  local before="$1" f="$2" start="$3" after content now
  after="$(live_hb_snapshot "$f")"
  if [ "$after" = "$before" ]; then
    echo "[LIVE-HB] unchanged: $f"
    return 0
  fi
  content="${after%|*}"
  now="$(date +%s)"
  if [[ "$content" =~ ^[0-9]+$ ]] && [ "$content" -ge $((start - 60)) ] && [ "$content" -le $((now + 60)) ]; then
    echo "[LIVE-HB] advanced to in-window epoch $content (daemon cycle, not a test write): $f"
    return 0
  fi
  echo "[LIVE-HB] FAIL: live heartbeat $f changed during this run to '${content}' (before: ${before%|*})." >&2
  echo "[LIVE-HB] A test reached the LIVE fleet-state heartbeat. Find the writer and pin its heartbeat path to its own TEST_DIR." >&2
  return 1
}

# Run a single test file
run_test() {
  local test_path="$1"
  local test_name="$2"
  echo "[TEST] Running: $test_name"
  local rc=0
  bash "$test_path" || rc=$?
  if [ "$rc" -eq 0 ]; then
    passed_tests+=("$test_name")
    echo "[PASS] $test_name"
  else
    failed_tests+=("$test_name")
    echo "[FAIL] $test_name (exit code: $rc)"
  fi
}

# Run a test command (e.g., hooks/pre-push-policy.sh --test)
run_test_command() {
  local cmd="$1"
  local test_name="$2"
  echo "[TEST] Running: $test_name"
  if eval "$cmd"; then
    passed_tests+=("$test_name")
    echo "[PASS] $test_name"
  else
    failed_tests+=("$test_name")
    echo "[FAIL] $test_name (exit code: $?)"
  fi
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  mode="run"
  REPO_ROOT="."

  if [ "$#" -gt 0 ]; then
    if [ "$1" = "--list" ]; then
      mode="list"
      REPO_ROOT="${2:-.}"
    else
      REPO_ROOT="$1"
    fi
  fi

  TESTS_DIR="${REPO_ROOT}/tests"
  HOOKS_DIR="${REPO_ROOT}/hooks"

  failed_tests=()
  passed_tests=()

  # List mode: print discovered test files (one per line, relative paths for coverage gate)
  if [ "$mode" = "list" ]; then
    discover_tests
    for test in "${discovered_tests[@]}"; do
      echo "$test"
    done
    exit 0
  fi

  # Isolation (incident: a shell-test lane left the test placeholder "1234567890"
  # in the LIVE fleet-state root heartbeat). Pin HOME/USERPROFILE and
  # TMPDIR/TMP/TEMP to a fresh mktemp root before any test runs, so:
  #   - any `mktemp -d`/`${TMPDIR:-/tmp}` fallback a test uses for its OWN
  #     fixtures lands under the isolated root structurally, and
  #   - any future tool that derives a default from Path.home()/$HOME (e.g.
  #     tools/common.py's get_conductor_root() default) gets the fake home, not
  #     the real one.
  # Deliberately NOT exported here: AESOP_ROOT / AESOP_STATE_ROOT /
  # CONDUCTOR_ROOT. Several already-hermetic tests (test-selfheal.sh, the
  # test-run-watchdog*.sh family, test-daemon-halt-sentinel.sh) rely on the
  # daemon scripts' own CONDUCTOR_ROOT default (sibling of AESOP_ROOT)
  # fallback staying UNSET so it derives from THEIR OWN AESOP_ROOT fixture, and
  # test-daemon-halt-config-path.sh's Test 2 relies on AESOP_STATE_ROOT staying
  # UNSET so halt.py falls through to its aesop.config.json state_root check.
  # Exporting a runner-level default for these doesn't add safety (every
  # invocation that matters already pins them explicitly per-command) and
  # actively breaks that fallback-must-be-unset contract -- confirmed by
  # running the full suite with them exported: it broke 5 previously-green
  # tests. The real, confirmed vulnerability (test-daemon-halt-config-path.sh
  # passing REPO_ROOT as AESOP_ROOT with CONDUCTOR_ROOT unpinned, so it derived
  # the REAL fleet-state root) is fixed directly in that file instead.
  # Live-heartbeat tripwire: capture the REAL heartbeat before HOME is pinned.
  # AESOP_LIVE_HEARTBEAT overrides the watched path (used only by the
  # tripwire's own self-tests / red-first proofs).
  LIVE_HB_PATH="${AESOP_LIVE_HEARTBEAT:-${HOME}/conductor3/state/.watchdog-heartbeat}"
  LIVE_HB_START="$(date +%s)"
  LIVE_HB_BEFORE="$(live_hb_snapshot "$LIVE_HB_PATH")"
  export SHELL_TEST_LIVE_HEARTBEAT="$LIVE_HB_PATH"
  if [ "${LIVE_HB_BEFORE%|*}" != "ABSENT" ] && [ "${LIVE_HB_BEFORE%|*}" -lt $((LIVE_HB_START - 86400)) ] 2>/dev/null; then
    echo "[LIVE-HB] WARNING: live heartbeat already holds a stale/placeholder value (${LIVE_HB_BEFORE%|*}) before this run: $LIVE_HB_PATH"
  fi

  SHELL_TEST_ISO_ROOT="$(mktemp -d)"
  export SHELL_TEST_ISO_ROOT
  export HOME="${SHELL_TEST_ISO_ROOT}/home"
  export USERPROFILE="${SHELL_TEST_ISO_ROOT}/home"
  export TMPDIR="${SHELL_TEST_ISO_ROOT}/tmp"
  export TMP="${TMPDIR}"
  export TEMP="${TMPDIR}"
  mkdir -p "$HOME" "$TMPDIR"
  trap 'rm -rf "$SHELL_TEST_ISO_ROOT"' EXIT

  echo "Isolation: SHELL_TEST_ISO_ROOT=$SHELL_TEST_ISO_ROOT (HOME/USERPROFILE/TMPDIR/TMP/TEMP pinned under it for this run)"
  echo ""

  # Run mode: discover and execute tests
  echo "Shell test runner — discovering and running tests from $TESTS_DIR"
  echo ""

  discover_tests

  # Run all discovered test files
  for test in "${discovered_tests[@]}"; do
    run_test "$test" "$(basename "$test")"
  done

  # Run hooks tests
  for hook in "${discovered_hooks[@]}"; do
    run_test_command "bash '$hook' --test" "$(basename "$hook") --test"
  done

  # Live-heartbeat tripwire verdict: a test that polluted the live file FAILS the run.
  live_hb_failed=0
  live_hb_verdict "$LIVE_HB_BEFORE" "$LIVE_HB_PATH" "$LIVE_HB_START" || live_hb_failed=1
  if [ "$live_hb_failed" -eq 1 ]; then
    failed_tests+=("live-heartbeat tripwire ($LIVE_HB_PATH)")
  fi

  echo ""
  echo "---"
  echo "Test Results:"
  echo "Passed: ${#passed_tests[@]}"
  echo "Failed: ${#failed_tests[@]}"

  if [ ${#failed_tests[@]} -gt 0 ]; then
    echo ""
    echo "Failed tests:"
    for test in "${failed_tests[@]}"; do
      echo "  - $test"
    done
    exit 1
  fi

  exit 0
fi
