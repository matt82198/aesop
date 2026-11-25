#!/usr/bin/env bash
# Bash test verifying tests/test_tools_eod_sweep.py contains no PowerShell subprocess calls.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$REPO_ROOT/tests/test_tools_eod_sweep.py"

if grep -Eqi 'powershell|Scripting\.FileSystemObject' "$TARGET"; then
  printf 'FAIL: %s still invokes PowerShell or Scripting.FileSystemObject\n' "$TARGET"
  exit 1
fi
printf 'PASS: %s uses stdlib without PowerShell\n' "$TARGET"
