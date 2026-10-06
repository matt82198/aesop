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

# Run a single test file
run_test() {
  local test_path="$1"
  local test_name="$2"
  echo "[TEST] Running: $test_name"
  if bash "$test_path"; then
    passed_tests+=("$test_name")
    echo "[PASS] $test_name"
  else
    failed_tests+=("$test_name")
    echo "[FAIL] $test_name (exit code: $?)"
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
  # in the LIVE ~/conductor3/state/.watchdog-heartbeat). Pin HOME/USERPROFILE and
  # TMPDIR/TMP/TEMP to a fresh mktemp root before any test runs, so:
  #   - any `mktemp -d`/`${TMPDIR:-/tmp}` fallback a test uses for its OWN
  #     fixtures lands under the isolated root structurally, and
  #   - any future tool that derives a default from Path.home()/$HOME (e.g.
  #     ui/config.py's AESOP_CONDUCTOR3_ROOT default) gets the fake home, not
  #     the real one.
  # Deliberately NOT exported here: AESOP_ROOT / AESOP_STATE_ROOT /
  # CONDUCTOR_ROOT. Several already-hermetic tests (test-selfheal.sh, the
  # test-run-watchdog*.sh family, test-daemon-halt-sentinel.sh) rely on the
  # daemon scripts' own `${CONDUCTOR_ROOT:-$(dirname "$AESOP_ROOT")/conductor3}`
  # fallback staying UNSET so it derives from THEIR OWN AESOP_ROOT fixture, and
  # test-daemon-halt-config-path.sh's Test 2 relies on AESOP_STATE_ROOT staying
  # UNSET so halt.py falls through to its aesop.config.json state_root check.
  # Exporting a runner-level default for these doesn't add safety (every
  # invocation that matters already pins them explicitly per-command) and
  # actively breaks that fallback-must-be-unset contract -- confirmed by
  # running the full suite with them exported: it broke 5 previously-green
  # tests. The real, confirmed vulnerability (test-daemon-halt-config-path.sh
  # passing REPO_ROOT as AESOP_ROOT with CONDUCTOR_ROOT unpinned, so it derived
  # the REAL ~/conductor3) is fixed directly in that file instead.
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
