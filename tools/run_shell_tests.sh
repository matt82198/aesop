#!/usr/bin/env bash
set -uo pipefail

# Glob-based shell test runner — discovers and runs all shell tests sequentially.
# Discovers from: tests/*.test.sh tests/test_*.sh tests/test-*.sh hooks/pre-push-policy.sh --test
# Fails fast with clear output.

REPO_ROOT="${1:-.}"
TESTS_DIR="${REPO_ROOT}/tests"
HOOKS_DIR="${REPO_ROOT}/hooks"

failed_tests=()
passed_tests=()

# Run a single test file/command
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

echo "Shell test runner — discovering and running tests from $TESTS_DIR"
echo ""

# Discover and run tests/*.test.sh
for test in "$TESTS_DIR"/*.test.sh; do
  if [ -f "$test" ]; then
    run_test "$test" "$(basename "$test")"
  fi
done

# Discover and run tests/test_*.sh
for test in "$TESTS_DIR"/test_*.sh; do
  if [ -f "$test" ]; then
    run_test "$test" "$(basename "$test")"
  fi
done

# Discover and run tests/test-*.sh
for test in "$TESTS_DIR"/test-*.sh; do
  if [ -f "$test" ]; then
    run_test "$test" "$(basename "$test")"
  fi
done

# Run hooks/pre-push-policy.sh --test if it exists
if [ -f "$HOOKS_DIR/pre-push-policy.sh" ]; then
  run_test_command "bash '$HOOKS_DIR/pre-push-policy.sh' --test" "hooks/pre-push-policy.sh --test"
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
