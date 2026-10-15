#!/usr/bin/env bash
# Pre-commit hook — dispatch_lint.py gate
# Runs tools/dispatch_lint.py over the tree and blocks the commit when it reports
# violations. Keys on the linter's EXIT CODE (0 = clean, 1 = violations, 2 = error).
# Fails closed: a missing python or a missing linter also blocks the commit.
#
# Escape hatch: none. Fix the violation or add a `# dispatch-ok` suppression on a
# genuine code site (see tools/dispatch_lint.py). Never bypass with --no-verify.

set -uo pipefail

block() {
  printf '\nBLOCKED by pre-commit-dispatch-lint: %s\n' "$1" >&2
  exit 1
}

main() {
  local toplevel
  toplevel=$(git rev-parse --show-toplevel 2>/dev/null) || block "not inside a git work tree"

  # Nothing staged means nothing to gate.
  local staged_files
  staged_files=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null) \
    || block "could not list staged files"
  if [ -z "$staged_files" ]; then
    exit 0
  fi

  # Resolve the interpreter: AESOP_PYTHON override, then python3, then python.
  local py="${AESOP_PYTHON:-}"
  if [ -z "$py" ]; then
    if command -v python3 >/dev/null 2>&1; then
      py=python3
    elif command -v python >/dev/null 2>&1; then
      py=python
    fi
  fi
  if [ -z "$py" ] || ! command -v "$py" >/dev/null 2>&1; then
    block "python not found (tried ${AESOP_PYTHON:-python3/python}); the dispatch linter cannot run"
  fi
  if ! "$py" -c 'import sys' >/dev/null 2>&1; then
    block "interpreter '$py' cannot run; the dispatch linter cannot run"
  fi

  local linter="$toplevel/tools/dispatch_lint.py"
  if [ ! -f "$linter" ]; then
    block "tools/dispatch_lint.py is missing from $toplevel; the gate cannot run"
  fi

  local findings rc
  findings=$("$py" "$linter" "$toplevel" --check 2>&1)
  rc=$?
  if [ "$rc" -eq 0 ]; then
    exit 0
  fi

  printf '%s\n' "$findings" >&2
  if [ "$rc" -eq 1 ]; then
    block "dispatch policy violations found (see findings above)"
  fi
  block "dispatch_lint exited $rc (error); treating as a failed gate"
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
