#!/usr/bin/env bash
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
