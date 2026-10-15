#!/usr/bin/env bash
# Tests: pre-push gates are judged by the BASE (origin/main) gate copy, never by
# the pushed branch's own copy. A branch that weakens tools/secret_scan.py must
# not pass its own push gate (F-04 / adv-enforcement F-03). Real hook functions
# are driven against a real temp origin + clone; nothing is mocked.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SCRIPT="$REPO_ROOT/hooks/pre-push-policy.sh"
PRISTINE_SCANNER="$REPO_ROOT/tools/secret_scan.py"

TMPDIR="${TMPDIR:-/tmp}"
TEST_ROOT="$TMPDIR/pre_push_gate_base_$$"
trap "rm -rf '$TEST_ROOT'" EXIT
mkdir -p "$TEST_ROOT"

# Sourcing defines the check_* functions; the BASH_SOURCE guard keeps main() from running.
source "$HOOK_SCRIPT"

test_passed=0
test_failed=0
ZERO=0000000000000000000000000000000000000000

# Build origin (bare) + clone with a pristine scanner on main, then a feature branch.
make_fixture() {
  local name="$1"
  local root="$TEST_ROOT/$name"
  mkdir -p "$root"
  git init -q --bare -b main "$root/origin.git"
  git init -q -b main "$root/work"
  (
    cd "$root/work" || exit 1
    git config user.email "gate-test@example.invalid"
    git config user.name "gate-test"
    git config core.autocrlf false
    mkdir -p tools state
    cp "$PRISTINE_SCANNER" tools/secret_scan.py
    chmod +x tools/secret_scan.py
    printf 'base\n' > README.md
    git add -A
    git commit -q -m "base"
    git remote add origin "$root/origin.git"
    git push -q origin main
    git checkout -q -b feature/x
  )
}

# Run the real check_secret_scan for the clone's HEAD as a new-branch push.
scan_push() {
  local work="$1"
  local sha
  sha=$(git -C "$work" rev-parse HEAD)
  (
    cd "$work" || exit 1
    export AESOP_ROOT="$work"
    printf 'refs/heads/feature/x %s refs/heads/feature/x %s\n' "$sha" "$ZERO" | check_secret_scan
  ) >/dev/null 2>"$TEST_ROOT/scan_err.txt"
  return $?
}

record() {
  if [ "$1" -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi
}

# ---------------------------------------------------------------------------
printf '\n=== F-04: branch that weakens secret_scan.py must NOT pass its own push ===\n'
(
  make_fixture weaken
  work="$TEST_ROOT/weaken/work"
  # Runtime-assembled dummy credential: never a literal token in this source file.
  v="Zq8Lm2Kp9"
  v="${v}Vx4Rt7Hw3Yb"
  printf 'DB_PASSWORD=%s\n' "$v" > "$work/leak.env"
  git -C "$work" add -A
  git -C "$work" commit -q -m "add leak"
  # Control: the pristine scanner detects the committed leak in the pushed range.
  (cd "$work" && python -I "$PRISTINE_SCANNER" --range "$(git rev-parse origin/main)..HEAD" >/dev/null 2>&1)
  pristine_rc=$?
  # Branch weakens the scanner to a no-op in a second commit.
  printf '#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n' > "$work/tools/secret_scan.py"
  chmod +x "$work/tools/secret_scan.py"
  git -C "$work" add -A
  git -C "$work" commit -q -m "weaken scanner"
  scan_push "$work"
  rc=$?
  if [ "$pristine_rc" -eq 0 ]; then
    printf 'FAIL: control broken - pristine scanner did not detect the dummy leak\n'
    exit 1
  fi
  if [ "$rc" -eq 0 ]; then
    printf 'FAIL: weakened scanner passed its own push gate (rc=0 on a leak)\n'
    exit 1
  fi
  if ! grep -q 'FOUND' "$TEST_ROOT/scan_err.txt"; then
    printf 'FAIL: blocked for the wrong reason (no scanner FOUND line):\n'
    cat "$TEST_ROOT/scan_err.txt"
    exit 1
  fi
  printf 'PASS: weakened scanner blocked by base gate (rc=%s, pristine rc=%s)\n' "$rc" "$pristine_rc"
)
record $?

# ---------------------------------------------------------------------------
printf '\n=== Control: branch that only IMPROVES the scanner still pushes ===\n'
(
  make_fixture improve
  work="$TEST_ROOT/improve/work"
  printf '\n# improvement: documentation-only change, scanner logic untouched\n' >> "$work/tools/secret_scan.py"
  git -C "$work" add -A
  git -C "$work" commit -q -m "improve scanner"
  scan_push "$work"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    printf 'FAIL: scanner-improving push blocked (rc=%s); gate improvements must still push\n' "$rc"
    cat "$TEST_ROOT/scan_err.txt"
    exit 1
  fi
  printf 'PASS: scanner-improving push allowed (rc=0)\n'
)
record $?

# ---------------------------------------------------------------------------
printf '\n=== GATE-TAMPER: a push that edits a gate file prints a GATE-CHANGED banner ===\n'
(
  make_fixture banner
  work="$TEST_ROOT/banner/work"
  printf '\n# touch\n' >> "$work/tools/secret_scan.py"
  git -C "$work" add -A
  git -C "$work" commit -q -m "touch gate"
  tuple=$(printf 'refs/heads/feature/x %s refs/heads/feature/x %s' "$(git -C "$work" rev-parse HEAD)" "$ZERO")
  banner=$( (cd "$work" && export AESOP_ROOT="$work" && printf '%s\n' "$tuple" | check_gate_tamper) 2>&1 )
  if ! printf '%s' "$banner" | grep -q 'GATE-CHANGED'; then
    printf 'FAIL: no GATE-CHANGED banner for a push that edits tools/secret_scan.py\n'
    exit 1
  fi
  printf 'PASS: GATE-CHANGED banner printed\n'
)
record $?

# ---------------------------------------------------------------------------
printf '\n=== Fail-closed: origin/main unreadable -> gate refuses (no fallback to pushed copy) ===\n'
(
  root="$TEST_ROOT/noorigin"
  mkdir -p "$root/work/tools"
  git init -q -b feature/x "$root/work"
  cp "$PRISTINE_SCANNER" "$root/work/tools/secret_scan.py"
  chmod +x "$root/work/tools/secret_scan.py"
  git -C "$root/work" -c core.autocrlf=false -c user.email=t@example.invalid -c user.name=t add -A
  git -C "$root/work" -c user.email=t@example.invalid -c user.name=t commit -q -m "no origin"
  scan_push "$root/work"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf 'FAIL: scan passed although origin/main is unreadable (fail-open)\n'
    exit 1
  fi
  if ! grep -q 'origin/main' "$TEST_ROOT/scan_err.txt"; then
    printf 'FAIL: no origin/main diagnostic on fail-closed block\n'
    exit 1
  fi
  printf 'PASS: unreadable origin/main blocks the push (rc=%s)\n' "$rc"
)
record $?

printf '\n=== Summary: %s passed, %s failed ===\n' "$test_passed" "$test_failed"
[ "$test_failed" -eq 0 ]
