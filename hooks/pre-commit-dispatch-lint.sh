#!/usr/bin/env bash
# Pre-commit hook — dispatch_lint.py gate
# Runs dispatch linter on staged files containing dispatch patterns
# Blocks commit if violations found

set -uo pipefail

main() {
  local toplevel
  toplevel=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

  # Get staged files
  local staged_files
  staged_files=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null) || exit 0

  if [ -z "$staged_files" ]; then
    exit 0
  fi

  # Run dispatch linter on staged files
  # dispatch_lint scans only files with dispatch indicators, so this is safe for all files
  if ! python3 "$toplevel/tools/dispatch_lint.py" "$toplevel" --check 2>&1 | grep -q "Pattern"; then
    exit 0
  fi

  # If we got here, violations were found
  printf '\nError: Dispatch policy violations found. See above for details.\n' >&2
  printf 'Dispatch violations:\n' >&2
  python3 "$toplevel/tools/dispatch_lint.py" "$toplevel" --fix >&2

  exit 1
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
