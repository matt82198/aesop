#!/usr/bin/env bash
# Hermetic bash tests for tools/check-ps1-syntax.sh (runs on Linux CI via tools/run_shell_tests.sh).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK_SH="$REPO_ROOT/tools/check-ps1-syntax.sh"
CHECK_PS1="$REPO_ROOT/tools/check-ps1-syntax.ps1"
CI_YML="$REPO_ROOT/.github/workflows/ci.yml"

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/check-ps1-syntax-test.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

passed=0
failed=0

record_result() {
  if [ "$1" -eq 0 ]; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
  fi
}

init_fixture_repo() {
  local dir="$1"
  rm -rf "$dir"
  mkdir -p "$dir"
  git -C "$dir" init -q -b main
  git -C "$dir" config user.email "test@example.com"
  git -C "$dir" config user.name "Test"
  git -C "$dir" config core.autocrlf false
}

printf '\n=== Test 1: repo with no tracked *.ps1 files passes cleanly ===\n'
(
  [ -f "$CHECK_SH" ] || { printf 'FAIL: %s does not exist\n' "$CHECK_SH"; exit 1; }
  dir="$TMP_ROOT/empty_repo"
  init_fixture_repo "$dir"
  printf 'hello\n' > "$dir/README.md"
  git -C "$dir" add README.md
  git -C "$dir" commit -q -m "seed"

  out=$(bash "$CHECK_SH" "$dir" 2>&1)
  rc=$?
  [ "$rc" -eq 0 ] || { printf 'FAIL: expected rc=0 on empty repo, got %s: %s\n' "$rc" "$out"; exit 1; }
  printf '%s\n' "$out" | grep -q "No tracked \*.ps1 files found" || { printf 'FAIL: missing empty message: %s\n' "$out"; exit 1; }
  printf 'PASS: empty repo passes\n'
)
record_result $?

printf '\n=== Test 2: valid tracked *.ps1 passes (including quotes, backticks, and here-strings) ===\n'
(
  [ -f "$CHECK_SH" ] || { printf 'FAIL: %s does not exist\n' "$CHECK_SH"; exit 1; }
  dir="$TMP_ROOT/valid_repo"
  init_fixture_repo "$dir"
  cat > "$dir/valid.ps1" <<'PSEOF'
param([string]$Name = 'world')
function Say-Hello {
    param([string]$Target)
    $pattern = '*"*'
    $msg = "Hello `$Target (`$Name)"
    $here = @"
unpaired { inside here-string is literal
"@
    Write-Host "$msg $pattern $here"
}
Say-Hello -Target $Name
PSEOF
  git -C "$dir" add valid.ps1
  git -C "$dir" commit -q -m "valid ps1"

  out=$(CHECK_PS1_FORCE_POSIX=1 bash "$CHECK_SH" "$dir" 2>&1)
  rc=$?
  [ "$rc" -eq 0 ] || { printf 'FAIL: expected valid.ps1 rc=0 under POSIX parser, got %s:\n%s\n' "$rc" "$out"; exit 1; }
  printf '%s\n' "$out" | grep -q "OK: valid.ps1" || { printf 'FAIL: missing OK line:\n%s\n' "$out"; exit 1; }
  printf '%s\n' "$out" | grep -q "PowerShell syntax check PASSED" || { printf 'FAIL: missing PASSED summary:\n%s\n' "$out"; exit 1; }
  printf 'PASS: valid *.ps1 passes\n'
)
record_result $?

printf '\n=== Test 3: broken *.ps1 files fail closed under both default and POSIX Linux modes ===\n'
(
  [ -f "$CHECK_SH" ] || { printf 'FAIL: %s does not exist\n' "$CHECK_SH"; exit 1; }
  dir="$TMP_ROOT/broken_repo"
  init_fixture_repo "$dir"
  cat > "$dir/broken_brace.ps1" <<'PSEOF'
function Broken-Brace {
    Write-Host "missing closing brace"
PSEOF
  cat > "$dir/broken_quote.ps1" <<'PSEOF'
$x = "unterminated string literal
Write-Host $x
PSEOF
  git -C "$dir" add broken_brace.ps1 broken_quote.ps1
  git -C "$dir" commit -q -m "broken ps1"

  for mode in default posix; do
    if [ "$mode" = "posix" ]; then
      out=$(CHECK_PS1_FORCE_POSIX=1 bash "$CHECK_SH" "$dir" 2>&1)
      rc=$?
    else
      out=$(bash "$CHECK_SH" "$dir" 2>&1)
      rc=$?
    fi
    [ "$rc" -ne 0 ] || { printf 'FAIL [%s]: broken .ps1 should exit non-zero, got 0:\n%s\n' "$mode" "$out"; exit 1; }
    printf '%s\n' "$out" | grep -q "FAIL: broken_brace.ps1" || { printf 'FAIL [%s]: did not report broken_brace.ps1:\n%s\n' "$mode" "$out"; exit 1; }
    printf '%s\n' "$out" | grep -q "FAIL: broken_quote.ps1" || { printf 'FAIL [%s]: did not report broken_quote.ps1:\n%s\n' "$mode" "$out"; exit 1; }
    printf '%s\n' "$out" | grep -q "PowerShell syntax check FAILED" || { printf 'FAIL [%s]: missing FAILED summary:\n%s\n' "$mode" "$out"; exit 1; }
  done
  printf 'PASS: broken *.ps1 files fail closed in both default and POSIX Linux modes\n'
)
record_result $?

printf '\n=== Test 4: real repo *.ps1 passes POSIX check, and ci.yml ps1-syntax-check stays on pwsh + check-ps1-syntax.ps1 AST parser ===\n'
(
  [ -f "$CHECK_SH" ] || { printf 'FAIL: %s does not exist\n' "$CHECK_SH"; exit 1; }
  [ -f "$CHECK_PS1" ] || { printf 'FAIL: %s must stay retained for the CI AST gate\n' "$CHECK_PS1"; exit 1; }
  out=$(CHECK_PS1_FORCE_POSIX=1 bash "$CHECK_SH" "$REPO_ROOT" 2>&1)
  rc=$?
  [ "$rc" -eq 0 ] || { printf 'FAIL: real repo *.ps1 failed POSIX Linux check (rc=%s):\n%s\n' "$rc" "$out"; exit 1; }

  py_bin="python3"
  command -v python3 >/dev/null 2>&1 || py_bin="python"
  "$py_bin" - "$CI_YML" "$CHECK_PS1" <<'PYEOF'
import pathlib
import sys
import yaml

ci_doc = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
ps1_src = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")

job = (ci_doc.get("jobs") or {}).get("ps1-syntax-check")
if not isinstance(job, dict):
    sys.exit("FAIL: jobs.ps1-syntax-check missing from ci.yml")
if job.get("runs-on") != "windows-latest":
    sys.exit(f"FAIL: ps1-syntax-check runs-on must be 'windows-latest', got {job.get('runs-on')!r}")
if job.get("continue-on-error"):
    sys.exit("FAIL: ps1-syntax-check must not set continue-on-error")

steps = [s for s in (job.get("steps") or []) if s.get("name") == "Check PowerShell syntax"]
if len(steps) != 1:
    sys.exit(f"FAIL: expected 1 'Check PowerShell syntax' step, got {len(steps)}")
step = steps[0]
if step.get("shell") != "pwsh":
    sys.exit(f"FAIL: ps1-syntax-check step must use shell: pwsh, got {step.get('shell')!r}")
run_norm = (step.get("run") or "").replace("\\", "/")
if "tools/check-ps1-syntax.ps1" not in run_norm:
    sys.exit(f"FAIL: ps1-syntax-check step must invoke tools/check-ps1-syntax.ps1, got {step.get('run')!r}")
if "check-ps1-syntax.sh" in run_norm:
    sys.exit("FAIL: ps1-syntax-check CI gate must not substitute check-ps1-syntax.sh for PowerShell AST parser")
if "[System.Management.Automation.Language.Parser]::ParseInput" not in ps1_src:
    sys.exit("FAIL: tools/check-ps1-syntax.ps1 missing native PowerShell AST Parser::ParseInput")
PYEOF
  [ "$?" -eq 0 ] || exit 1
  printf 'PASS: real repo *.ps1 passes POSIX check and ci.yml ps1-syntax-check uses pwsh + check-ps1-syntax.ps1 AST parser\n'
)
record_result $?

printf '\n=== Summary: %d passed, %d failed ===\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
