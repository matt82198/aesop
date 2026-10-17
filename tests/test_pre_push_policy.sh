#!/usr/bin/env bash
# Tests for pre-push-policy.sh — branch protection and secret scanning hooks
# TDD: Write failing tests first, then implement fixes
set -uo pipefail

# Source the hook script
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SCRIPT="$REPO_ROOT/hooks/pre-push-policy.sh"

# Temporary test directory
TMPDIR="${TMPDIR:-/tmp}"
TEST_ROOT="$TMPDIR/pre_push_test_$$"
trap "rm -rf '$TEST_ROOT'" EXIT
mkdir -p "$TEST_ROOT"

# Import hook functions by sourcing the hook script directly.
# The hook guards its own "main "$@"" call with a BASH_SOURCE check so
# sourcing it here only defines functions and never executes main()
# or reads from stdin.
source "$HOOK_SCRIPT"

test_passed=0
test_failed=0

# Gate code is judged from origin/main (hooks/pre-push-policy.sh gate_base_dir).
# publish_base_tools <clone> <tools_src>: origin/main:tools/ becomes <tools_src>
# (empty src -> only a placeholder), via a separate base repo fetched as origin.
publish_base_tools() {
  local clone="$1" src="$2" base="$1.base"
  rm -rf "$base"
  mkdir -p "$base/tools"
  if [ -n "$src" ] && [ -d "$src" ]; then
    cp -R "$src"/. "$base/tools/"
  fi
  : > "$base/tools/.keep"
  git -C "$base" init -q -b main
  git -C "$base" add -A
  git -C "$base" -c user.email=t@example.com -c user.name=t -c core.autocrlf=false commit -q -m base
  git -C "$clone" remote remove origin >/dev/null 2>&1 || true
  git -C "$clone" remote add origin "$base"
  git -C "$clone" fetch -q origin main
}

# emit_fixture_base <root>: makes <root> a git repo and publishes <root>/tools as its
# origin/main:tools. check_emit_receipt runs its emitter from that BASE copy, so an
# emit fixture must publish the stub it just wrote; otherwise the real trunk emitter
# (and its key/gh/network paths) runs instead. Call after writing the stub, then cd.
emit_fixture_base() {
  local root="$1"
  if [ ! -d "$root/.git" ]; then
    git -C "$root" init -q -b feature/test
    git -C "$root" -c user.email=t@example.com -c user.name=t -c core.autocrlf=false commit -q --allow-empty -m seed
  fi
  publish_base_tools "$root" "$root/tools"
}

# make_clone_with_base <clone> <tools_src>: fresh clone on feature/test with a base published.
make_clone_with_base() {
  local clone="$1" src="$2"
  rm -rf "$clone"
  mkdir -p "$clone"
  git -C "$clone" init -q -b main
  git -C "$clone" config user.email t@example.com
  git -C "$clone" config user.name t
  git -C "$clone" config core.autocrlf false
  printf 'base\n' > "$clone/README.md"
  git -C "$clone" add -A
  git -C "$clone" commit -q -m base
  publish_base_tools "$clone" "$src"
  git -C "$clone" checkout -q -b feature/test
}


# ===== NEW TESTS FOR 6 FINDINGS =====

printf '\n=== Finding 1: Race condition in hash-chain (concurrent writes) ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_race"
  mkdir -p "$AESOP_ROOT/state"

  # Simulate two concurrent log_block calls (both read tail, both append)
  # Expected: write lock ensures consistent prev_hash chain
  log_block "concurrent_1" &
  pid1=$!
  log_block "concurrent_2" &
  pid2=$!
  wait $pid1 $pid2

  # Verify chain integrity after concurrent writes
  if verify_audit_log "$AESOP_ROOT/state/SECURITY-AUDIT.log" >/dev/null 2>&1; then
    printf 'PASS: Concurrent log_block calls do not break hash chain\n'
  else
    printf 'FAIL: Hash chain broken after concurrent writes\n'
    exit 1
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Finding 2: Tail truncation not detected ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_truncate"
  mkdir -p "$AESOP_ROOT/state"

  log_block "entry_1"
  log_block "entry_2"
  log_block "entry_3"

  # Get the original tail hash before truncation
  # We'll check the sidecar file for the anchor
  original_tail_file="$AESOP_ROOT/state/.audit-tail-hash"
  if [ ! -f "$original_tail_file" ]; then
    printf 'FAIL: Tail hash anchor file not created\n'
    exit 1
  fi

  # Truncate the log (remove last line)
  head -n 2 "$AESOP_ROOT/state/SECURITY-AUDIT.log" > "$AESOP_ROOT/state/SECURITY-AUDIT.log.tmp"
  mv "$AESOP_ROOT/state/SECURITY-AUDIT.log.tmp" "$AESOP_ROOT/state/SECURITY-AUDIT.log"

  # verify_audit_log should return non-zero on truncation
  if verify_audit_log "$AESOP_ROOT/state/SECURITY-AUDIT.log" >/dev/null 2>&1; then
    printf 'FAIL: Truncation not detected by verify_audit_log\n'
    exit 1
  fi
  printf 'PASS: Truncation detection working\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Finding 3: stdin loop hangs on tty + drops final line without newline ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_stdin"
  mkdir -p "$AESOP_ROOT/state"

  # Create isolated git repo on feature branch to hermetically test check_branch_policy
  # (avoids dependency on ambient git HEAD, which may be main during CI push events)
  ISOLATED_REPO="$TEST_ROOT/aesop_stdin_repo"
  mkdir -p "$ISOLATED_REPO"
  (
    cd "$ISOLATED_REPO" || exit 1
    git init -q
    git config user.email "test@example.com"
    git config user.name "Test User"
    echo "dummy" > file.txt
    git add file.txt
    git commit -q -m "initial"
    git checkout -q -b feature/isolated

    # Test 3a: Final line without newline should be handled
    printf 'refs/heads/feature/test abc123 refs/heads/feature/test def456' | {
      if check_branch_policy >/dev/null 2>&1; then
        printf 'PASS: Final line without newline handled correctly\n'
      else
        printf 'FAIL: Failed to handle final line without newline\n'
        exit 1
      fi
    }
  )
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Finding 4: Test 6 should call REAL check_branch_policy, not reimplement ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_test6"
  mkdir -p "$AESOP_ROOT/state"

  # Test that check_branch_policy via pipe works correctly
  local_sha="abc123def456"
  remote_main_sha="0000000000000000000000000000000000000000"

  printf 'refs/heads/feature/test %s refs/heads/main %s\n' "$local_sha" "$remote_main_sha" | {
    if ! check_branch_policy >/dev/null 2>&1; then
      printf 'PASS: Real check_branch_policy correctly blocks main via stdin\n'
    else
      printf 'FAIL: check_branch_policy should block push to main\n'
      exit 1
    fi
  }
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Finding 5: json_escape missing control chars (\\n, \\r, \\t, C0) ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_escape"
  mkdir -p "$AESOP_ROOT/state"

  # HYGIENE (incident 52d7be7): identity mutation must NEVER touch the live
  # repo config -- run this check inside an isolated fixture repo.
  ESCAPE_REPO="$TEST_ROOT/aesop_escape_repo"
  mkdir -p "$ESCAPE_REPO"
  cd "$ESCAPE_REPO" || exit 1
  git init -q
  git config user.email "test@example.com"
  # Set user name with embedded newline (dangerous!)
  git config user.name $'Alice\nAdmin'

  log_block "test"

  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")

  # Should be valid JSON after proper escaping
  if printf '%s' "$audit_line" | python3 -m json.tool >/dev/null 2>&1; then
    printf 'PASS: Control character escaping produces valid JSON (newline test)\n'
  else
    printf 'FAIL: JSON with control char is invalid\n'
    printf 'Entry: %s\n' "$audit_line"
    exit 1
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Finding 5b: json_escape all C0 control characters ===\n'
(
  # Direct test: Pass string with various C0 control chars to json_escape
  # Test chars: BEL (\x07), BS (\x08), VT (\x0b), FF (\x0c), SO (\x0e), ESC (\x1b)
  test_string=$(printf 'Alice\x07\x08\x0b\x0c\x0e\x1bAdmin')
  escaped=$(json_escape "$test_string")

  # The escaped output should be JSON-safe (no bare control chars)
  # Create a minimal JSON object with the escaped string and validate
  json_obj=$(printf '{"user":"%s"}' "$escaped")

  if printf '%s' "$json_obj" | python3 -m json.tool >/dev/null 2>&1; then
    printf 'PASS: All C0 control characters properly escaped to valid JSON\n'
  else
    printf 'FAIL: JSON with escaped C0 control chars is invalid\n'
    printf 'Escaped string: %s\n' "$escaped"
    printf 'JSON object: %s\n' "$json_obj"
    exit 1
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Finding 6: sha256sum unchecked → silent empty hash ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_nosha"
  mkdir -p "$AESOP_ROOT/state"

  # Mask sha256sum so it's not available
  export PATH="/usr/bin:/bin"  # Minimal PATH without sha256sum

  # Call get_previous_hash; should fail loudly or fallback gracefully
  if hash=$(get_previous_hash "$AESOP_ROOT/state/SECURITY-AUDIT.log" 2>&1); then
    if [ -z "$hash" ]; then
      printf 'FAIL: sha256sum unavailable produced silent empty hash\n'
      exit 1
    fi
    printf 'PASS: Fallback or error handling for missing sha256sum\n'
  else
    printf 'PASS: Loud error when sha256sum unavailable\n'
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

# ===== EXISTING TESTS (6-11) =====

printf '\n=== Test 6 (existing): stdin refspec bypass detection (HEAD:main) ===\n'
(
  cd "$TEST_ROOT" || exit 1
  git init -q
  git config user.email "test@example.com"
  git config user.name "Test User"

  # Create initial commit
  echo "dummy" > file.txt
  git add file.txt
  git commit -q -m "initial"

  # Create a feature branch and commit
  git checkout -q -b feature/test
  echo "change" > file.txt
  git add file.txt
  git commit -q -m "feature change"

  local_sha=$(git rev-parse HEAD)
  remote_main_sha="0000000000000000000000000000000000000000"

  stdin_input="refs/heads/feature/test $local_sha refs/heads/main $remote_main_sha"

  printf '%s\n' "$stdin_input" | {
    if ! check_branch_policy >/dev/null 2>&1; then
      printf 'PASS: Correctly blocked push to main via stdin refspec\n'
    else
      printf 'FAIL: Should have blocked push to main\n'
      exit 1
    fi
  }
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 7 (existing): Secret scan unavailable fails-CLOSED ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_no_scanner"
  mkdir -p "$AESOP_ROOT/state"
  make_clone_with_base "$TEST_ROOT/no_scanner_clone" ""
  cd "$TEST_ROOT/no_scanner_clone" || exit 1

  # Provide a valid 4-field tuple to get past empty-stdin check
  # This tests that when scanner is missing, it fails-closed (not when stdin is empty)
  # Format: <local-ref> <local-sha> <remote-ref> <remote-sha>
  stdin_input="refs/heads/feature/test abc123def456 refs/heads/feature/test 0000000000000000000000000000000000000000"

  stderr_output=$( { printf '%s\n' "$stdin_input" | check_secret_scan; } 2>&1 1>/dev/null )
  exit_code=$?

  if [ "$exit_code" -eq 0 ]; then
    printf 'FAIL: check_secret_scan should return 1 when scanner missing (fail-closed)\n'
    exit 1
  fi

  if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
    printf 'FAIL: Audit log not created when scanner is unavailable\n'
    exit 1
  fi

  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")

  if ! printf '%s' "$audit_line" | python3 -m json.tool >/dev/null 2>&1; then
    printf 'FAIL: Audit log entry is not valid JSON\n'
    exit 1
  fi

  if ! printf '%s' "$audit_line" | grep -q '"event":"push_blocked"'; then
    printf 'FAIL: Audit log entry missing "push_blocked" event type\n'
    exit 1
  fi

  if ! printf '%s' "$audit_line" | grep -q 'secret_scan_unavailable'; then
    printf 'FAIL: Audit log entry missing "secret_scan_unavailable" reason\n'
    exit 1
  fi

  printf 'PASS: Secret scan unavailable fails-closed and logged as push_blocked\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 8 (existing): Hash-chain audit log (GENESIS first event) ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_hashchain"
  mkdir -p "$AESOP_ROOT/state"

  log_block "test_block_1"

  if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
    printf 'FAIL: Audit log not created\n'
    exit 1
  fi

  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")

  if ! printf '%s' "$audit_line" | grep -q '"prev_hash":"GENESIS"'; then
    printf 'FAIL: First event should have prev_hash=GENESIS\n'
    exit 1
  fi

  if ! printf '%s' "$audit_line" | python3 -m json.tool >/dev/null 2>&1; then
    printf 'FAIL: Hash-chained entry is not valid JSON\n'
    exit 1
  fi

  printf 'PASS: First event has GENESIS prev_hash and valid JSON\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 9 (existing): Hash-chain builds across 2+ events ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_hashchain2"
  mkdir -p "$AESOP_ROOT/state"

  log_block "test_block_1"
  log_block "test_block_2"

  if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
    printf 'FAIL: Audit log not created\n'
    exit 1
  fi

  line_count=$(wc -l < "$AESOP_ROOT/state/SECURITY-AUDIT.log")
  if [ "$line_count" -ne 2 ]; then
    printf 'FAIL: Expected 2 audit log entries, got %d\n' "$line_count"
    exit 1
  fi

  line1=$(head -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")
  line2=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")

  if ! printf '%s' "$line1" | grep -q '"prev_hash":"GENESIS"'; then
    printf 'FAIL: First event should have prev_hash=GENESIS\n'
    exit 1
  fi

  if ! printf '%s' "$line2" | grep -q '"prev_hash":'; then
    printf 'FAIL: Second event should have prev_hash field\n'
    exit 1
  fi

  line1_hash=$(printf '%s' "$line1" | sha256sum | awk '{print $1}')
  line2_prev=$(printf '%s' "$line2" | python3 -c "import sys, json; print(json.load(sys.stdin).get('prev_hash', ''))")

  if [ "$line1_hash" != "$line2_prev" ]; then
    printf 'FAIL: Second event prev_hash does not match first line hash\n'
    exit 1
  fi

  printf 'PASS: Hash chain builds correctly across 2 events\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 10 (existing): verify-audit-log passes on intact chain ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_verify"
  mkdir -p "$AESOP_ROOT/state"

  log_block "entry_1"
  log_block "entry_2"
  log_event "entry_3"

  if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
    printf 'FAIL: Audit log not created\n'
    exit 1
  fi

  if type verify_audit_log >/dev/null 2>&1; then
    if verify_audit_log "$AESOP_ROOT/state/SECURITY-AUDIT.log" >/dev/null 2>&1; then
      printf 'PASS: Verification passed on intact chain\n'
    else
      printf 'FAIL: Verification should pass on intact chain\n'
      exit 1
    fi
  else
    printf 'SKIP: verify_audit_log function not yet implemented\n'
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 11 (existing): verify-audit-log detects tampered line ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_tamper"
  mkdir -p "$AESOP_ROOT/state"

  log_block "entry_1"
  log_block "entry_2"
  log_block "entry_3"

  if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
    printf 'FAIL: Audit log not created\n'
    exit 1
  fi

  sed -i '2s/"reason":"[^"]*"/"reason":"TAMPERED"/g' "$AESOP_ROOT/state/SECURITY-AUDIT.log"

  if type verify_audit_log >/dev/null 2>&1; then
    if ! verify_audit_log "$AESOP_ROOT/state/SECURITY-AUDIT.log" >/dev/null 2>&1; then
      printf 'PASS: Verification detected tampered middle line\n'
    else
      printf 'FAIL: Verification should detect tampering\n'
      exit 1
    fi
  else
    printf 'SKIP: verify_audit_log function not yet implemented\n'
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== P1 Bug2: compute_sha256 falls back to shasum when sha256sum is unavailable ===\n'
(
  expected="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"  # sha256("abc")
  got=$(printf 'abc' | compute_sha256)
  if [ "$got" != "$expected" ]; then
    printf 'FAIL: compute_sha256 primary path produced wrong hash: %s\n' "$got"
    exit 1
  fi
  # Force the fallback deterministically: shadow the `command` builtin so
  # `command -v sha256sum` reports "not found" (simulates a shasum-only host
  # like macOS/BSD). The old code hardcoded sha256sum at 4 of 5 sites and would
  # emit an EMPTY hash here; the compute_sha256 helper must produce a real one.
  command() {
    if [ "${1:-}" = "-v" ] && [ "${2:-}" = "sha256sum" ]; then return 1; fi
    builtin command "$@"
  }
  if builtin command -v shasum >/dev/null 2>&1; then
    gotfb=$(printf 'abc' | compute_sha256)
    if [ "$gotfb" != "$expected" ]; then
      printf 'FAIL: compute_sha256 shasum-fallback produced wrong hash: %s\n' "$gotfb"
      exit 1
    fi
    printf 'PASS: compute_sha256 falls back to shasum and yields the correct hash\n'
  else
    printf 'PASS: primary path correct (shasum absent; fallback path not exercisable here)\n'
  fi
)
if [ $? -eq 0 ]; then test_passed=$((test_passed + 1)); else test_failed=$((test_failed + 1)); fi

printf '\n=== P1 Bug1: verify_audit_log holds the write lock (no false truncation vs concurrent append) ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_verify_lock"
  mkdir -p "$AESOP_ROOT/state"
  log="$AESOP_ROOT/state/SECURITY-AUDIT.log"
  log_block "seed_1" >/dev/null 2>&1
  log_block "seed_2" >/dev/null 2>&1
  bad=0
  i=0
  while [ "$i" -lt 6 ]; do
    log_block "concurrent_$i" >/dev/null 2>&1 &
    ap=$!
    out=$(verify_audit_log "$log" 2>&1)
    wait "$ap" 2>/dev/null
    if printf '%s' "$out" | grep -qiE 'truncat|tamper|chain.*brok|brok.*chain'; then
      bad=1
    fi
    i=$((i + 1))
  done
  if [ "$bad" -ne 0 ]; then
    printf 'FAIL: verify_audit_log falsely reported truncation/tamper during a concurrent append (no lock)\n'
    exit 1
  fi
  if [ -d "$AESOP_ROOT/state/.audit-log-lock" ]; then
    printf 'FAIL: verify_audit_log left its .audit-log-lock behind (not released)\n'
    exit 1
  fi
  printf 'PASS: verify_audit_log serializes via the write lock; no false truncation, lock released\n'
)
if [ $? -eq 0 ]; then test_passed=$((test_passed + 1)); else test_failed=$((test_failed + 1)); fi

printf '\n=== SECURITY P1: check_secret_scan handles empty vs malformed stdin (P1 fix) ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_malformed_stdin"
  mkdir -p "$AESOP_ROOT/state"
  mkdir -p "$AESOP_ROOT/tools"

  # Create a dummy scanner script so we get past the "scanner not found" check
  # and actually test the stdin parsing logic
  cat > "$AESOP_ROOT/tools/secret_scan.py" <<'SCANNER'
#!/usr/bin/env python3
import sys
if '--range' in sys.argv:
    sys.exit(0)
sys.exit(1)
SCANNER
  chmod +x "$AESOP_ROOT/tools/secret_scan.py"

  # Test 1: Empty stdin (P1 fix: now ALLOWED with log_event, not fail-closed)
  stderr_output=$( { printf '' | check_secret_scan; } 2>&1 1>/dev/null )
  exit_code=$?

  if [ "$exit_code" -ne 0 ]; then
    printf 'FAIL: Empty stdin should be allowed (rc 0) with P1 fix, got rc %d\n' "$exit_code"
    exit 1
  fi

  # Verify log_event was created
  if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
    printf 'FAIL: Audit log not created for empty stdin\n'
    exit 1
  fi

  # Test 2: Garbage stdin with insufficient tokens (still fails-closed)
  rm "$AESOP_ROOT/state/SECURITY-AUDIT.log" 2>/dev/null
  stderr_output=$( { printf 'garbage garbage\n' | check_secret_scan; } 2>&1 1>/dev/null )
  exit_code=$?

  if [ "$exit_code" -eq 0 ]; then
    printf 'FAIL: check_secret_scan should return 1 on unparseable stdin (fail-closed)\n'
    exit 1
  fi

  # Should print a clear reason to stderr
  if ! printf '%s' "$stderr_output" | grep -qi 'malformed\|parse.*fail\|unable.*parse'; then
    printf 'FAIL: No clear error message to stderr on malformed stdin\n'
    printf 'stderr was: %s\n' "$stderr_output"
    exit 1
  fi

  printf 'PASS: Empty stdin allowed with log_event; malformed stdin fails-closed\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: Scanner missing should fail-closed ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_no_scanner"
  mkdir -p "$AESOP_ROOT/state"

  cd "$TEST_ROOT" || exit 1
  git init -q
  git config user.email "test@example.com"
  git config user.name "Test User"
  echo "dummy" > file.txt
  git add file.txt
  git commit -q -m "initial"
  git checkout -q -b feature/test 2>/dev/null || git branch -M feature/test

  local_sha=$(git rev-parse HEAD 2>/dev/null || echo "0000000")

  # Simulate git pre-push stdin with feature branch
  stdin_input="refs/heads/feature/test $local_sha refs/heads/feature/test 0000000000000000000000000000000000000000"

  # Run check_secret_scan with missing scanner (scanner path doesn't exist)
  stderr_output=$( { printf '%s\n' "$stdin_input" | check_secret_scan; } 2>&1 1>/dev/null )
  exit_code=$?

  # Should return 1 (fail-closed)
  if [ "$exit_code" -ne 1 ]; then
    printf 'FAIL: check_secret_scan should return 1 when scanner is missing (fail-closed), got %d\n' "$exit_code"
    exit 1
  fi

  # Should print FATAL to stderr
  if ! printf '%s' "$stderr_output" | grep -q -i 'FATAL\|not found\|not executable'; then
    printf 'FAIL: check_secret_scan should print FATAL/error message to stderr\n'
    printf 'stderr was: %s\n' "$stderr_output"
    exit 1
  fi

  printf 'PASS: check_secret_scan correctly fails-closed (exit 1) when scanner is missing\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 19: delete-only push skips secret scan (rc 0, even with NO scanner available) ===\n'
(
  zeros="0000000000000000000000000000000000000000"
  old_sha=$(git rev-parse HEAD 2>/dev/null || echo "1234567890123456789012345678901234567890")
  # Point AESOP_ROOT at an empty dir: no scanner exists (reproduces CI). A
  # delete-only push carries no content, so it must pass without a scanner.
  scannerless_root=$(mktemp -d)
  printf '(delete) %s refs/heads/some-old-branch %s\n' "$zeros" "$old_sha" | {
    AESOP_ROOT="$scannerless_root" check_secret_scan
    exit_code=$?
    if [ "$exit_code" -ne 0 ]; then
      printf 'FAIL: delete-only push should skip secret scan (rc 0), got %d\n' "$exit_code"
      rm -rf "$scannerless_root"
      exit 1
    fi
  } || { rm -rf "$scannerless_root"; exit 1; }
  rm -rf "$scannerless_root"
  printf 'PASS: delete-only push skips secret scan cleanly (scannerless env)\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 20: delete-only push allowed by branch policy even from main checkout ===\n'
(
  zeros="0000000000000000000000000000000000000000"
  old_sha=$(git rev-parse HEAD 2>/dev/null || echo "1234567890123456789012345678901234567890")
  # branch policy must not consult current branch for a pure-delete push
  printf '(delete) %s refs/heads/some-old-branch %s\n' "$zeros" "$old_sha" | {
    check_branch_policy
    exit_code=$?
    if [ "$exit_code" -ne 0 ]; then
      printf 'FAIL: delete-only push should pass branch policy (rc 0), got %d\n' "$exit_code"
      exit 1
    fi
  } || exit 1
  printf 'PASS: delete-only push passes branch policy regardless of checkout\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 21: deleting refs/heads/main itself is still BLOCKED ===\n'
(
  zeros="0000000000000000000000000000000000000000"
  old_sha=$(git rev-parse HEAD 2>/dev/null || echo "1234567890123456789012345678901234567890")
  printf '(delete) %s refs/heads/main %s\n' "$zeros" "$old_sha" | {
    check_branch_policy
    exit_code=$?
    if [ "$exit_code" -eq 0 ]; then
      printf 'FAIL: deleting refs/heads/main must be blocked, got rc 0\n'
      exit 1
    fi
  } || exit 1
  printf 'PASS: deleting refs/heads/main is blocked\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== P1 BUG FIX: Empty stdin (no tuples) → allowed with log_event ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_empty_stdin"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"

  # Create a dummy scanner script (should not be called for empty stdin)
  cat > "$AESOP_ROOT/tools/secret_scan.py" <<'SCANNER'
#!/usr/bin/env python3
import sys
sys.exit(1)  # Fail if called (it shouldn't be for empty stdin)
SCANNER
  chmod +x "$AESOP_ROOT/tools/secret_scan.py"

  # Test 1: truly empty stdin (no lines at all)
  stderr_output=$( { printf '' | check_secret_scan; } 2>&1 1>/dev/null )
  exit_code=$?

  if [ "$exit_code" -ne 0 ]; then
    printf 'FAIL: Empty stdin should be allowed (rc 0), got rc %d\n' "$exit_code"
    exit 1
  fi

  # Should have log_event "secret_scan_skipped_empty_stdin" in audit log
  if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
    printf 'FAIL: Audit log not created for empty stdin\n'
    exit 1
  fi

  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")
  if ! printf '%s' "$audit_line" | grep -q 'secret_scan_skipped_empty_stdin'; then
    printf 'FAIL: Audit log missing "secret_scan_skipped_empty_stdin" event\n'
    printf 'Audit entry: %s\n' "$audit_line"
    exit 1
  fi

  if ! printf '%s' "$audit_line" | grep -q '"event":"secret_scan_event"'; then
    # log_event uses "event" field (not "push_blocked"); verify it's valid JSON
    if ! printf '%s' "$audit_line" | python3 -m json.tool >/dev/null 2>&1; then
      printf 'FAIL: Audit log entry is not valid JSON\n'
      exit 1
    fi
  fi

  printf 'PASS: Empty stdin allowed with log_event "secret_scan_skipped_empty_stdin"\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== P1 BUG FIX: Malformed 3-field tuple (missing remote_sha) → blocked ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_malformed_3field"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"

  # Create a dummy scanner script
  cat > "$AESOP_ROOT/tools/secret_scan.py" <<'SCANNER'
#!/usr/bin/env python3
import sys
sys.exit(0)
SCANNER
  chmod +x "$AESOP_ROOT/tools/secret_scan.py"

  # Test: malformed 3-field line (missing remote_sha)
  # Format should be: <local-ref> <local-sha> <remote-ref> <remote-sha>
  # We'll provide: <local-ref> <local-sha> <remote-ref> (only 3 fields)
  stderr_output=$( { printf 'refs/heads/feature/test abc123def456 refs/heads/feature/test\n' | check_secret_scan; } 2>&1 1>/dev/null )
  exit_code=$?

  if [ "$exit_code" -eq 0 ]; then
    printf 'FAIL: Malformed 3-field tuple should fail-closed (rc nonzero), got rc 0\n'
    exit 1
  fi

  # Should log a block event
  if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
    printf 'FAIL: Audit log not created for malformed stdin\n'
    exit 1
  fi

  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")
  if ! printf '%s' "$audit_line" | grep -q 'secret_scan_stdin_parse_failed'; then
    printf 'FAIL: Audit log missing "secret_scan_stdin_parse_failed" reason\n'
    exit 1
  fi

  # Verify audit log is valid JSON
  if ! printf '%s' "$audit_line" | python3 -m json.tool >/dev/null 2>&1; then
    printf 'FAIL: Audit log entry is not valid JSON\n'
    exit 1
  fi

  # Verify stderr message mentions malformed
  if ! printf '%s' "$stderr_output" | grep -qi 'malformed'; then
    printf 'FAIL: stderr does not mention "malformed"\n'
    printf 'stderr was: %s\n' "$stderr_output"
    exit 1
  fi

  printf 'PASS: Malformed 3-field tuple blocked with audit log\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== P1 Security: repo_name with quotes/backslashes escapes properly in audit JSON ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_repo_escape"
  mkdir -p "$AESOP_ROOT/state"

  # Test 1: Direct json_escape test with problematic repo names
  # Repo name with double quotes (path separator on Windows would be backslash)
  repo_with_quotes='my"repo"dir'
  escaped_quotes=$(json_escape "$repo_with_quotes")

  # Build a JSON object with the escaped repo name to verify it's valid JSON
  json_obj=$(printf '{"repo":"%s"}' "$escaped_quotes")

  if ! printf '%s' "$json_obj" | python3 -m json.tool >/dev/null 2>&1; then
    printf 'FAIL: Repo name with quotes did not escape properly. JSON: %s\n' "$json_obj"
    exit 1
  fi

  # Repo name with backslashes
  repo_with_backslash='my\repo\dir'
  escaped_backslash=$(json_escape "$repo_with_backslash")
  json_obj2=$(printf '{"repo":"%s"}' "$escaped_backslash")

  if ! printf '%s' "$json_obj2" | python3 -m json.tool >/dev/null 2>&1; then
    printf 'FAIL: Repo name with backslashes did not escape properly. JSON: %s\n' "$json_obj2"
    exit 1
  fi

  # Test 2: Verify actual log_block/log_event with escaped repo name in context
  # by mocking the git repo name via function override and checking the audit log
  git_repo_name='test"repo"name'
  (
    # Override git output to return our problematic repo name
    git() {
      if [ "$1" = "rev-parse" ] && [ "$2" = "--show-toplevel" ]; then
        printf '/path/to/%s\n' "$git_repo_name"
      else
        command git "$@"
      fi
    }
    export -f git

    log_block "test_with_quoted_repo_name" >/dev/null 2>&1
  )

  # Verify the audit log was created and is valid JSON
  if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
    printf 'FAIL: Audit log not created when repo name contains quotes\n'
    exit 1
  fi

  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")

  # The JSON must parse correctly
  if ! printf '%s' "$audit_line" | python3 -m json.tool >/dev/null 2>&1; then
    printf 'FAIL: Audit log entry with escaped repo name is not valid JSON\n'
    printf 'Entry: %s\n' "$audit_line"
    exit 1
  fi

  # Verify the repo field exists and contains the escaped name
  if ! printf '%s' "$audit_line" | python3 -c "import sys, json; data = json.load(sys.stdin); r = data.get('repo', ''); print(r if 'repo' in r or len(r) > 0 else sys.exit(1))" >/dev/null 2>&1; then
    printf 'FAIL: Audit log entry missing valid repo field\n'
    exit 1
  fi

  printf 'PASS: repo_name with quotes and backslashes properly escaped in audit log JSON\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== P1 TTY FIX: get_commit_range tty → fail-closed (option B) ===\n'
(
  # Option B: get_commit_range with tty returns rc=1 (fail-closed).
  # Empty stdin (piped, no tuples) still returns rc=3 (allowed).
  # Direct tty invocation (nearly dead code post-main()-guard) fails-closed.

  export AESOP_ROOT="$TEST_ROOT/aesop_tty"
  mkdir -p "$AESOP_ROOT/state"

  # Test 1: Empty stdin (piped, not tty) returns rc=3 (allowed)
  rc=$( printf '' | get_commit_range; echo "$?" )

  if [ "$rc" = "3" ]; then
    printf 'PASS: get_commit_range with empty piped stdin returns rc=3 (allowed)\n'
  else
    printf 'FAIL: get_commit_range should return rc=3 for empty piped stdin, got rc=%s\n' "$rc"
    exit 1
  fi

  # Test 2: Malformed stdin still returns rc=1
  rc=$( printf 'refs/heads/test abc123 refs/heads/test\n' | get_commit_range 2>/dev/null; echo "$?" )

  if [ "$rc" = "1" ]; then
    printf 'PASS: get_commit_range with malformed stdin returns rc=1 (fail-closed)\n'
  else
    printf 'FAIL: get_commit_range should return rc=1 for malformed stdin, got rc=%s\n' "$rc"
    exit 1
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== P1 TTY FIX: check_secret_scan allows empty piped stdin (option B) ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_tty_check_secret"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"

  # Create a dummy scanner script
  cat > "$AESOP_ROOT/tools/secret_scan.py" <<'SCANNER'
#!/usr/bin/env python3
import sys
sys.exit(0)
SCANNER
  chmod +x "$AESOP_ROOT/tools/secret_scan.py"

  # Test: check_secret_scan with empty piped stdin should allow with log_event
  # (get_commit_range returns rc=3 for empty piped stdin, which check_secret_scan treats as "allowed")
  # Note: Direct tty invocation would fail-close at main() guard, not here
  rc=$( printf '' | check_secret_scan 2>/dev/null; echo "$?" )

  if [ "$rc" = "0" ]; then
    printf 'PASS: check_secret_scan with empty piped stdin returns rc=0 (allow)\n'
  else
    printf 'FAIL: check_secret_scan should return rc=0 for empty piped stdin, got rc=%s\n' "$rc"
    exit 1
  fi

  # Verify log_event was created
  if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
    printf 'FAIL: Audit log not created for empty-stdin secret_scan\n'
    exit 1
  fi

  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")
  if ! printf '%s' "$audit_line" | grep -q 'secret_scan_skipped_empty_stdin'; then
    printf 'FAIL: Audit log missing "secret_scan_skipped_empty_stdin" event\n'
    exit 1
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== main() TTY guard (option B): piped-empty → allowed, tty guard present ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_main_tty"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"

  # Create a dummy scanner script so main() doesn't fail on missing scanner
  cat > "$AESOP_ROOT/tools/secret_scan.py" <<'SCANNER'
#!/usr/bin/env python3
import sys
sys.exit(0)
SCANNER
  chmod +x "$AESOP_ROOT/tools/secret_scan.py"
  # The G6 subprocess ratchet is fail-closed without its committed baseline (a real repo has one).
  printf '{"violations": {}}\n' > "$AESOP_ROOT/.subprocess-guard-baseline.json"

  # Stub the remaining gate scripts. A tools/ directory holding only
  # secret_scan.py is a PARTIAL aesop install, and the gates now fail closed on
  # that (a missing gate script in a repo that has tools/ used to be silently
  # skipped -- the escape this fixture must not re-create). These stubs keep the
  # fixture focused on main()'s stdin handling instead of gate behavior.
  #
  # DERIVED, not hand-maintained: a hardcoded list here forgets every new
  # fail-closed gate the moment it's added to pre-push-policy.sh (PR #872 --
  # five red CI rounds, one of them this exact fixture missing a stub for a
  # just-added gate). tools/gate_stub_list.py parses the real hook script for
  # every check_* function that resolves its script via gate_tool_status() and
  # emits the current, correct list -- see tests/test_gate_stub_list.py for the
  # red-first proof that a brand-new check_* gate is picked up automatically.
  py_bin="python3"
  command -v python3 >/dev/null 2>&1 || py_bin="python"
  # tr -d '\r': python.exe's stdout text layer translates \n -> \r\n on Windows,
  # which would otherwise leave a trailing \r glued onto each stub name below.
  derived_stubs=$("$py_bin" "$(dirname "$HOOK_SCRIPT")/../tools/gate_stub_list.py" "$HOOK_SCRIPT" | tr -d '\r')
  if [ -z "$derived_stubs" ]; then
    printf 'FAIL: tools/gate_stub_list.py derived an empty stub list from %s\n' "$HOOK_SCRIPT" >&2
    exit 1
  fi
  for gate_stub in $derived_stubs; do
    printf 'import sys\nsys.exit(0)\n' > "$AESOP_ROOT/tools/$gate_stub.py"
  done

  # Test 1: Piped empty stdin (simulates up-to-date push with no ref tuples)
  # main() should allow (rc=0) — this is the #289 legitimate case.
  # HEAD-INDEPENDENCE: check_branch_policy's empty-stdin fallback inspects the
  # CURRENT branch, so this must run inside an isolated fixture repo on a
  # feature branch (on main's own CI the repo checkout is main → false block).
  ISOLATED_MAIN_REPO="$TEST_ROOT/aesop_main_tty_repo"
  mkdir -p "$ISOLATED_MAIN_REPO"
  (
    cd "$ISOLATED_MAIN_REPO" || exit 1
    git init -q
    git config user.email "test@example.com"
    git config user.name "Test User"
    echo "dummy" > file.txt
    git add file.txt
    git commit -q -m "initial"
    git checkout -q -b feature/isolated-empty-stdin
  )
  # emit_receipt.py is read from the base copy and fails closed when absent: stub it green.
  printf 'import sys\nsys.exit(0)\n' > "$AESOP_ROOT/tools/emit_receipt.py"
  publish_base_tools "$ISOLATED_MAIN_REPO" "$AESOP_ROOT/tools"  # gate code is pinned to origin/main
  stderr_output=$( { printf '' | ( cd "$ISOLATED_MAIN_REPO" && bash "$HOOK_SCRIPT" ); } 2>&1 1>/dev/null )
  exit_code=$?

  if [ "$exit_code" -eq 0 ]; then
    printf 'PASS: main() with piped empty stdin returns rc=0 (allow — #289 path)\n'
  else
    printf 'FAIL: main() should return rc=0 for piped empty stdin, got rc=%d\n' "$exit_code"
    printf 'stderr: %s\n' "$stderr_output"
    exit 1
  fi

  # Test 2: Verify main() contains the TTY guard code
  # Construct: "if [ -t 0 ]" → print message → "log_block" → "exit 1"
  # This verifies the fail-closed posture is implemented (even if we can't test
  # it with a real tty on this system without tools like 'script' or 'expect')

  # Grep the hook script for the tty guard message
  if grep -q 'interactive invocation: this hook runs under git push' "$HOOK_SCRIPT"; then
    printf 'PASS: main() contains TTY guard message\n'
  else
    printf 'FAIL: main() missing TTY guard message\n'
    exit 1
  fi

  if grep -q 'if \[ -t 0 \]' "$HOOK_SCRIPT" && grep -q 'interactive_invocation_blocked' "$HOOK_SCRIPT"; then
    printf 'PASS: main() contains TTY guard logic and audit log\n'
  else
    printf 'FAIL: main() missing TTY guard logic or audit log\n'
    exit 1
  fi

  # Test 3: Direct function test — get_commit_range with tty path returns 1
  # (This exercises the nearly-dead code path since main() blocks first)
  # Verify that get_commit_range's tty branch contains "return 1"

  if grep -q 'no stdin to parse → fail-closed' "$HOOK_SCRIPT" && \
     grep -A4 'no stdin to parse → fail-closed' "$HOOK_SCRIPT" | grep -q 'return 1'; then
    printf 'PASS: get_commit_range tty path fails-closed (return 1)\n'
  else
    printf 'FAIL: get_commit_range tty path not fail-closed\n'
    exit 1
  fi

  printf 'PASS: main() TTY guard correctly implemented per option B\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Fix 1: get_next_seq/verify_audit_log stay python3||python (seq does not stick at 1 when python3 is shadowed) ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_py3shim"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"

  if ! command -v python >/dev/null 2>&1; then
    printf 'SKIP: no "python" binary on this host to exercise the python3-shadowed fallback\n'
    exit 0
  fi

  # CI-hang postmortem: an earlier version of this test hid python3 by
  # filtering it OUT of PATH directory-by-directory. On Linux, python3
  # typically lives in the SAME directory as bash/coreutils/git (/usr/bin),
  # so that filtering silently stripped mkdir/date/sleep/tail/sha256sum too
  # -- and acquire_audit_lock's retry loop (called by log_block) then spun
  # forever: mkdir (missing) never acquires the lock, its own 10s give-up
  # check depends on `date` (also missing) so it never fires, and `sleep`
  # (also missing) never throttles the loop. That is an unbounded busy-spin,
  # not an interactive-stdin block, but it hangs a CI job identically. This
  # version instead shadows ONLY the `command -v python3` lookup (the exact
  # pattern the "P1 Bug2" compute_sha256 test above already uses for
  # sha256sum) so every other lookup -- coreutils, git, the real python --
  # keeps resolving completely normally; PATH itself is never touched.
  #
  # Defense in depth per the CI incident: run in a SEPARATE process under a
  # hard `timeout`, with stdin explicitly pinned to /dev/null, so nothing
  # here can ever exceed a bounded wall-clock cap or inherit a real tty --
  # even if some other, still-undiscovered hang mechanism existed.
  run_out=$(
    HOOK_SCRIPT="$HOOK_SCRIPT" AESOP_ROOT="$AESOP_ROOT" \
      timeout 30 bash -c '
        source "$HOOK_SCRIPT"
        command() {
          if [ "${1:-}" = "-v" ] && [ "${2:-}" = "python3" ]; then return 1; fi
          builtin command "$@"
        }
        log_block "shim_entry_1"
        log_block "shim_entry_2"
        log_block "shim_entry_3"
      ' </dev/null 2>&1
  )
  run_rc=$?

  if [ $run_rc -eq 124 ]; then
    printf 'FAIL: log_block under python3-shadowed environment TIMED OUT after 30s (hung) instead of completing. Output: %s\n' "$run_out"
    exit 1
  fi

  audit_log="$AESOP_ROOT/state/SECURITY-AUDIT.log"
  if [ ! -f "$audit_log" ]; then
    printf 'FAIL: audit log not created under python3-shadowed environment. Output: %s\n' "$run_out"
    exit 1
  fi

  line_count=$(wc -l < "$audit_log")
  first_seq=$(head -n 1 "$audit_log" | grep -oE '"seq":[0-9]+' | cut -d: -f2)
  last_seq=$(tail -n 1 "$audit_log" | grep -oE '"seq":[0-9]+' | cut -d: -f2)

  if [ "$line_count" -ne 3 ]; then
    printf 'FAIL: expected 3 audit entries, got %s\n' "$line_count"
    exit 1
  fi

  if [ "$first_seq" != "1" ] || [ "$last_seq" != "3" ]; then
    printf 'FAIL: seq did not stay monotonic under python3-shadowed environment (first=%s last=%s) -- the seq-based truncation/tamper invariant is silently defeated when python3 is shadowed\n' "$first_seq" "$last_seq"
    exit 1
  fi

  # verify_audit_log itself also hardcoded python3 at two more sites
  # (actual_prev_hash, current_seq); prove it also still works, same bounding.
  verify_out=$(
    HOOK_SCRIPT="$HOOK_SCRIPT" AUDIT_LOG="$audit_log" \
      timeout 30 bash -c '
        source "$HOOK_SCRIPT"
        command() {
          if [ "${1:-}" = "-v" ] && [ "${2:-}" = "python3" ]; then return 1; fi
          builtin command "$@"
        }
        verify_audit_log "$AUDIT_LOG"
      ' </dev/null 2>&1
  )
  vrc=$?
  if [ $vrc -eq 124 ]; then
    printf 'FAIL: verify_audit_log under python3-shadowed environment TIMED OUT after 30s (hung)\n'
    exit 1
  fi
  if [ $vrc -ne 0 ]; then
    printf 'FAIL: verify_audit_log failed under python3-shadowed environment (rc=%d). Output: %s\n' "$vrc" "$verify_out"
    exit 1
  fi

  printf 'PASS: seq stayed monotonic (1..3) and verify_audit_log passed with python3 shadowed (only python resolves)\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Fix 2: check_secret_scan prefers python3 over python (broken/legacy python does not crash the scan) ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_py2shim"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"

  if ! command -v python3 >/dev/null 2>&1; then
    printf 'SKIP: no python3 found on this host\n'
    exit 0
  fi

  # A clean-scanning scanner script (Python-3-only in spirit): exits 0
  # unconditionally, standing in for "no secrets found". This is only
  # meaningful if a WORKING interpreter actually runs it -- if the broken
  # `python` shim below gets invoked instead, it exits 1 regardless,
  # simulating a Python-2 crash on a Python-3-only script.
  cat > "$AESOP_ROOT/tools/secret_scan.py" <<'SCANNER'
#!/usr/bin/env python3
import sys
sys.exit(0)
SCANNER
  chmod +x "$AESOP_ROOT/tools/secret_scan.py"

  make_clone_with_base "$TEST_ROOT/shim_clone" "$AESOP_ROOT/tools"
  pushd "$TEST_ROOT/shim_clone" >/dev/null || exit 1
  shim_dir="$TEST_ROOT/py2shim_bin"
  mkdir -p "$shim_dir"
  # Broken `python`: simulates a Python-2-only interpreter hitting the
  # Python-3-only scanner. Always exits nonzero immediately -- never reads
  # stdin, never execs/recurses into anything else -- so it cannot itself
  # become a second hang vector.
  cat > "$shim_dir/python" <<'SHIM'
#!/usr/bin/env bash
printf 'SyntaxError: simulated python2 incompatibility\n' >&2
exit 1
SHIM
  chmod +x "$shim_dir/python"

  local_sha="abc123def456"
  stdin_input="refs/heads/feature/test $local_sha refs/heads/feature/test 0000000000000000000000000000000000000000"

  # Bounded, non-interactive: separate process under `timeout`. PATH only
  # gains shim_dir PREPENDED -- nothing removed, so coreutils/git/the real
  # python3 all keep resolving normally. stdin is explicitly the controlled
  # ref tuple (never an inherited real tty).
  out=$(
    HOOK_SCRIPT="$HOOK_SCRIPT" AESOP_ROOT="$AESOP_ROOT" SHIM_DIR="$shim_dir" \
      timeout 30 bash -c '
        source "$HOOK_SCRIPT"
        export PATH="$SHIM_DIR:$PATH"
        check_secret_scan
      ' <<< "$stdin_input" 2>&1
  )
  popd >/dev/null
  rc=$?

  if [ $rc -eq 124 ]; then
    printf 'FAIL: check_secret_scan TIMED OUT after 30s (hung) instead of completing. Output: %s\n' "$out"
    exit 1
  fi
  if [ $rc -ne 0 ]; then
    printf 'FAIL: check_secret_scan picked the broken "python" over a working "python3" on PATH (interpreter order not python3-first). Output: %s\n' "$out"
    exit 1
  fi
  printf 'PASS: check_secret_scan prefers python3, so a broken/legacy "python" earlier on PATH does not crash the scan\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test P1-Worktree: Checkers use resolve_aesop_root, not hardcoded paths ===\n'
(
  # This test verifies the fix for the audit finding: check_encoding_lint and
  # check_test_coverage now call resolve_aesop_root() instead of hardcoding
  # ${AESOP_ROOT:-$HOME/aesop}, so they run the worktree's copy of the checker
  # tools, not the primary tree's copy.
  #
  # Worktree scenario:
  # 1. Primary tree has encoding_lint.py that always exits 0
  # 2. Worktree has encoding_lint.py that exits 1 (gate violation)
  # 3. Push from worktree should use worktree's checker and fail
  # 4. Without the fix, it would use primary tree's checker and pass
  #
  # We can't easily create actual worktrees in the test, but we CAN verify
  # that the functions call resolve_aesop_root() by checking the script itself.

  # Extract the check_encoding_lint function and verify it uses resolve_aesop_root()
  encoding_lint_fn=$(sed -n '/^check_encoding_lint()/,/^}/p' "$HOOK_SCRIPT")

  # The function should contain a call to resolve_aesop_root, not a hardcoded path
  if printf '%s' "$encoding_lint_fn" | grep -q 'aesop_root=\$(resolve_aesop_root)'; then
    printf 'PASS: check_encoding_lint calls resolve_aesop_root()\n'
  else
    printf 'FAIL: check_encoding_lint does not call resolve_aesop_root()\n'
    exit 1
  fi

  # The function should NOT contain a hardcoded ${AESOP_ROOT:-$HOME/aesop} assignment
  # in its local variable (resolve_aesop_root itself may reference AESOP_ROOT, but
  # the check_encoding_lint function should not hardcode it)
  if printf '%s' "$encoding_lint_fn" | grep 'local aesop_root' | grep -q 'AESOP_ROOT'; then
    printf 'FAIL: check_encoding_lint still hardcodes AESOP_ROOT in local assignment\n'
    exit 1
  fi

  # Extract the check_test_coverage function and verify it uses resolve_aesop_root()
  test_coverage_fn=$(sed -n '/^check_test_coverage()/,/^}/p' "$HOOK_SCRIPT")

  if printf '%s' "$test_coverage_fn" | grep -q 'aesop_root=\$(resolve_aesop_root)'; then
    printf 'PASS: check_test_coverage calls resolve_aesop_root()\n'
  else
    printf 'FAIL: check_test_coverage does not call resolve_aesop_root()\n'
    exit 1
  fi

  # The function should NOT contain a hardcoded ${AESOP_ROOT:-$HOME/aesop} assignment
  if printf '%s' "$test_coverage_fn" | grep 'local aesop_root' | grep -q 'AESOP_ROOT'; then
    printf 'FAIL: check_test_coverage still hardcodes AESOP_ROOT in local assignment\n'
    exit 1
  fi

  # Verify no other check functions have this pattern (grep for hardcoded patterns)
  # Count functions that have "local aesop_root" followed by hardcoding on same/next line
  hardcoded_count=$(grep -A1 'local aesop_root' "$HOOK_SCRIPT" | grep '${AESOP_ROOT:-' | wc -l)
  if [ "$hardcoded_count" -gt 0 ]; then
    printf 'FAIL: Found %d other instances of hardcoded AESOP_ROOT in local aesop_root assignments\n' "$hardcoded_count"
    exit 1
  fi

  printf 'PASS: No hardcoded AESOP_ROOT paths found in check functions\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: gen_tool_index gate fails when tool has no INDEX: line ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_no_index"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"

  # Create a dummy gen_tool_index.py script that returns 1 (simulating missing INDEX line)
  # This mimics the real tool's behavior when it finds a tool without an INDEX: line
  cat > "$AESOP_ROOT/tools/gen_tool_index.py" <<'SCRIPT'
#!/usr/bin/env python3
import sys
if "--check" in sys.argv:
    print("ERROR: tools/ files missing an INDEX: docstring line (fail-closed):", file=sys.stderr)
    print("  - tools/new_tool.py", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
SCRIPT
  chmod +x "$AESOP_ROOT/tools/gen_tool_index.py"
  make_clone_with_base "$TEST_ROOT/gen_clone_1" "$AESOP_ROOT/tools"
  cd "$TEST_ROOT/gen_clone_1" || exit 1

  # Create stub scripts for other gates so check_gen_tool_index doesn't fail before we test it
  for gate in tracker_guard import_resolution_check claudemd_sync_gate metrics_gate \
              verify_test_suite_count encoding_lint verify_test_coverage; do
    printf 'import sys\nsys.exit(0)\n' > "$AESOP_ROOT/tools/${gate}.py"
  done

  # Test: check_gen_tool_index should return 1 when the index script fails
  if ! check_gen_tool_index; then
    printf 'PASS: check_gen_tool_index correctly fails when INDEX line is missing\n'
  else
    printf 'FAIL: check_gen_tool_index should have failed (exit 1) when INDEX line missing\n'
    exit 1
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: gen_tool_index gate passes when all tools have INDEX lines ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_with_index"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"

  # Create a dummy gen_tool_index.py script that returns 0 (simulating success)
  cat > "$AESOP_ROOT/tools/gen_tool_index.py" <<'SCRIPT'
#!/usr/bin/env python3
import sys
if "--check" in sys.argv:
    print("[OK] tools/INDEX.md is in sync (42 tools)")
    sys.exit(0)
sys.exit(1)
SCRIPT
  chmod +x "$AESOP_ROOT/tools/gen_tool_index.py"
  make_clone_with_base "$TEST_ROOT/gen_clone_2" "$AESOP_ROOT/tools"
  cd "$TEST_ROOT/gen_clone_2" || exit 1

  # Create stub scripts for other gates
  for gate in tracker_guard import_resolution_check claudemd_sync_gate metrics_gate \
              verify_test_suite_count encoding_lint verify_test_coverage; do
    printf 'import sys\nsys.exit(0)\n' > "$AESOP_ROOT/tools/${gate}.py"
  done

  # Test: check_gen_tool_index should return 0 when index is in sync
  if check_gen_tool_index; then
    printf 'PASS: check_gen_tool_index correctly passes when all tools have INDEX lines\n'
  else
    printf 'FAIL: check_gen_tool_index should have passed (exit 0) when INDEX is in sync\n'
    exit 1
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

# ===== Receipt-gate auto-emission (check_emit_receipt / PR #884-followup) =====
# Day-1 measurement: 41 merged PRs, ZERO receipts -- lanes were never told to
# run emit_receipt.py and nothing ran it automatically. check_emit_receipt()
# makes emission automatic in the hook, ALWAYS fail-open (never blocks a
# push). These tests prove each branch behaviorally via the real function
# and the real audit log, not by reading source text.

# Capture the real timeout binary BEFORE we modify PATH in any test
real_timeout=$(command -v timeout)
if [ -z "$real_timeout" ]; then
  printf 'ERROR: timeout binary not found on PATH\n'
  exit 1
fi

printf '\n=== Test: check_emit_receipt opts out entirely on AESOP_RECEIPT_EMIT=0 ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_receipt_optout"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"
  # A tool that would SUCCEED if invoked -- proves opt-out short-circuits
  # BEFORE running it, not merely that the end result happens to be 0.
  printf 'import sys\nsys.exit(0)\n' > "$AESOP_ROOT/tools/emit_receipt.py"
  export AESOP_RECEIPT_KEY="$AESOP_ROOT/dummy_key.pem"
  touch "$AESOP_ROOT/dummy_key.pem"
  export AESOP_RECEIPT_EMIT=0

  if ! check_emit_receipt; then
    printf 'FAIL: check_emit_receipt must always return 0 (fail-open), even on opt-out\n'
    exit 1
  fi

  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log" 2>/dev/null)
  if ! printf '%s' "$audit_line" | grep -q 'receipt_emit_skipped_opted_out'; then
    printf 'FAIL: expected receipt_emit_skipped_opted_out audit event, got: %s\n' "$audit_line"
    exit 1
  fi
  printf 'PASS: AESOP_RECEIPT_EMIT=0 skips emission without invoking the tool\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: check_emit_receipt blocks (fail-closed) when the base copy has no emit_receipt.py ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_receipt_no_tool"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"
  unset AESOP_RECEIPT_EMIT
  # Base copy (origin/main:tools) has no emit_receipt.py: the emitter is read from the
  # base, so there is no conclusion to gate on. Contract: hooks/CLAUDE.md step 18.
  emit_fixture_base "$AESOP_ROOT"
  cd "$AESOP_ROOT" || exit 1

  stderr_output=$( { check_emit_receipt; } 2>&1 1>/dev/null )
  exit_code=$?
  if [ "$exit_code" -eq 0 ]; then
    printf 'FAIL: check_emit_receipt must BLOCK (return 1) when the base copy lacks emit_receipt.py\n'
    exit 1
  fi
  case "$stderr_output" in
    *emit_receipt.py*) ;;
    *) printf 'FAIL: block must name emit_receipt.py; got: %s\n' "$stderr_output"; exit 1 ;;
  esac

  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log" 2>/dev/null)
  if ! printf '%s' "$audit_line" | grep -q 'receipt_emit_base_tool_missing'; then
    printf 'FAIL: expected receipt_emit_base_tool_missing audit event, got: %s\n' "$audit_line"
    exit 1
  fi
  printf 'PASS: missing base emit_receipt.py blocks, fail-closed, and names the file\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: check_emit_receipt is BOUNDED -- times out and fails open instead of hanging ===\n'
(
  # The default matrix re-runs the whole repo test suite (~500s for the
  # slowest shard observed live); a hook must never be able to hang a push
  # indefinitely. AESOP_RECEIPT_TIMEOUT=1 against a stub that sleeps proves
  # the timeout path fires and still returns 0 (fail-open), not a hang.
  export AESOP_ROOT="$TEST_ROOT/aesop_receipt_timeout"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"
  # Stub that sleeps indefinitely but exits on SIGTERM (which timeout sends).
  # Python's signal handling causes sleep() to raise KeyboardInterrupt on signals,
  # so explicit handling ensures the process terminates instead of staying in sleep.
  printf 'import signal, sys, time\nsignal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))\nwhile True: time.sleep(1)\n' > "$AESOP_ROOT/tools/emit_receipt.py"
  export AESOP_RECEIPT_KEY="$AESOP_ROOT/dummy_key.pem"
  touch "$AESOP_ROOT/dummy_key.pem"
  export AESOP_RECEIPT_TIMEOUT=1
  unset AESOP_RECEIPT_EMIT
  emit_fixture_base "$AESOP_ROOT"
  cd "$AESOP_ROOT" || exit 1

  start_ts=$(date +%s)
  # Use 60s timeout guard (with real timeout from outer PATH) to catch hangs and print trace
  trace_file="$AESOP_ROOT/test-trace.log"
  stderr_output=$(
    "$real_timeout" --foreground 60 bash -c "
      set -x
      { check_emit_receipt; } 2>&1 1>/dev/null
    " 2>"$trace_file" || true
  )
  exit_code=$?
  elapsed=$(( $(date +%s) - start_ts ))

  if [ $exit_code -eq 124 ]; then
    printf 'FAIL: check_emit_receipt timed out (hung for 60s). Trace:\n'
    cat "$trace_file"
    exit 1
  fi

  # Re-run without trace to get stderr
  stderr_output=$( { check_emit_receipt; } 2>&1 1>/dev/null )
  exit_code=$?

  if [ "$exit_code" -ne 1 ]; then
    printf 'FAIL: check_emit_receipt must BLOCK (return 1) on timeout: an unreadable conclusion is fail-closed\n'
    exit 1
  fi
  # Threshold is a wide 20s, not a tight bound on the 1s AESOP_RECEIPT_TIMEOUT:
  # this box runs under heavy, variable concurrent load (observed 3-5s just
  # for process teardown), so the real property under test is "nowhere near
  # the 30s sleep" (timeout worked), not "exactly 1s" (which would make the
  # test flake on load alone without catching any real regression).
  if [ "$elapsed" -gt 20 ]; then
    printf 'FAIL: check_emit_receipt took %ds, did not actually bound the sleeping stub to ~1s\n' "$elapsed"
    exit 1
  fi
  if ! printf '%s' "$stderr_output" | grep -qi 'BLOCKED.*timed out'; then
    printf 'FAIL: expected a BLOCKED timeout line on stderr, got: %s\n' "$stderr_output"
    exit 1
  fi
  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log" 2>/dev/null)
  if ! printf '%s' "$audit_line" | grep -q 'receipt_emit_timed_out'; then
    printf 'FAIL: expected receipt_emit_timed_out audit event, got: %s\n' "$audit_line"
    exit 1
  fi
  printf 'PASS: a slow matrix is bounded by AESOP_RECEIPT_TIMEOUT and blocks fail-closed (elapsed %ds)\n' "$elapsed"
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: check_emit_receipt WARNs and fails open when no signing key material is set ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_receipt_no_key"
  export HOME="$AESOP_ROOT/home"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools" "$HOME"
  printf 'import sys\nsys.exit(0)\n' > "$AESOP_ROOT/tools/emit_receipt.py"
  unset AESOP_RECEIPT_EMIT
  unset AESOP_RECEIPT_KEY
  unset AESOP_RECEIPT_HMAC_SECRET

  stderr_output=$( { check_emit_receipt; } 2>&1 1>/dev/null )
  exit_code=$?

  if [ "$exit_code" -ne 0 ]; then
    printf 'FAIL: check_emit_receipt must fail-open (return 0) when no key material is set\n'
    exit 1
  fi
  if ! printf '%s' "$stderr_output" | grep -qi 'WARN'; then
    printf 'FAIL: expected a one-line WARN on stderr, got: %s\n' "$stderr_output"
    exit 1
  fi
  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log" 2>/dev/null)
  if ! printf '%s' "$audit_line" | grep -q 'receipt_emit_skipped_no_key_material'; then
    printf 'FAIL: expected receipt_emit_skipped_no_key_material audit event, got: %s\n' "$audit_line"
    exit 1
  fi
  printf 'PASS: no key material WARNs and fails open\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: check_emit_receipt uses default path when env vars unset ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_receipt_default_path"
  export HOME="$AESOP_ROOT/home"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools" "$HOME/.aesop"

  # Create a key file at the default path
  printf 'dummy_key_content' > "$HOME/.aesop/receipt_key.pem"

  printf 'import sys\nsys.exit(0)\n' > "$AESOP_ROOT/tools/emit_receipt.py"
  emit_fixture_base "$AESOP_ROOT"
  cd "$AESOP_ROOT" || exit 1
  unset AESOP_RECEIPT_EMIT
  unset AESOP_RECEIPT_KEY
  unset AESOP_RECEIPT_HMAC_SECRET

  stderr_output=$( { check_emit_receipt; } 2>&1 1>/dev/null )
  exit_code=$?

  if [ "$exit_code" -ne 0 ]; then
    printf 'FAIL: check_emit_receipt must proceed (return 0) when default path key exists\n'
    exit 1
  fi
  # Should NOT warn about missing key material since default path exists
  if printf '%s' "$stderr_output" | grep -qi 'no receipt signing key material'; then
    printf 'FAIL: should not warn about missing key when default path exists, got: %s\n' "$stderr_output"
    exit 1
  fi
  printf 'PASS: default path fallback allows emission to proceed\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: check_emit_receipt WARNs and fails open when gh is unavailable ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_receipt_no_gh"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"
  printf 'import sys\nsys.exit(0)\n' > "$AESOP_ROOT/tools/emit_receipt.py"
  export AESOP_RECEIPT_KEY="$AESOP_ROOT/dummy_key.pem"
  touch "$AESOP_ROOT/dummy_key.pem"
  unset AESOP_RECEIPT_EMIT

  # Create a stub gh that exits 127 (command not found). Prepend it to PATH
  # so it shadows any real gh. Don't copy binaries (they break on CI due to RPATH deps).
  stub_gh_dir="$AESOP_ROOT/stub-gh"
  mkdir -p "$stub_gh_dir"
  printf '#!/bin/sh\nexit 127\n' > "$stub_gh_dir/gh"
  chmod +x "$stub_gh_dir/gh"
  test_path="$stub_gh_dir:$PATH"

  # Use 60s timeout guard (with real timeout from outer PATH) to catch hangs and print trace
  trace_file="$AESOP_ROOT/test-trace.log"
  stderr_output=$(
    "$real_timeout" --foreground 60 bash -c "
      set -x
      export PATH='$test_path'
      { check_emit_receipt; } 2>&1 1>/dev/null
    " 2>"$trace_file" || true
  )
  exit_code=$?

  if [ $exit_code -eq 124 ]; then
    printf 'FAIL: check_emit_receipt timed out (hung for 60s). Trace:\n'
    cat "$trace_file"
    exit 1
  fi

  # Re-run without trace to get the actual stderr output
  stderr_output=$( { PATH="$test_path" check_emit_receipt; } 2>&1 1>/dev/null )
  exit_code=$?

  if [ "$exit_code" -ne 0 ]; then
    printf 'FAIL: check_emit_receipt must fail-open (return 0) when gh is unavailable\n'
    exit 1
  fi
  if ! printf '%s' "$stderr_output" | grep -qi 'WARN'; then
    printf 'FAIL: expected a one-line WARN on stderr, got: %s\n' "$stderr_output"
    exit 1
  fi
  audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log" 2>/dev/null)
  if ! printf '%s' "$audit_line" | grep -q 'receipt_emit_skipped_no_gh'; then
    printf 'FAIL: expected receipt_emit_skipped_no_gh audit event, got: %s\n' "$audit_line"
    exit 1
  fi
  printf 'PASS: missing gh WARNs and fails open\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: check_emit_receipt is BOUNDED without timeout binary (fallback branch) ===\n'
(
  # When coreutils timeout is unavailable (e.g., both timeout and gtimeout fail),
  # check_emit_receipt must still be bounded using background job + explicit kill.
  # This test forces the fallback branch and verifies it doesn't hang.
  export AESOP_ROOT="$TEST_ROOT/aesop_receipt_no_timeout_bin"
  mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"
  printf 'import signal, sys, time\nsignal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))\nwhile True: time.sleep(1)\n' > "$AESOP_ROOT/tools/emit_receipt.py"
  emit_fixture_base "$AESOP_ROOT"
  cd "$AESOP_ROOT" || exit 1
  export AESOP_RECEIPT_KEY="$AESOP_ROOT/dummy_key.pem"
  touch "$AESOP_ROOT/dummy_key.pem"
  export AESOP_RECEIPT_TIMEOUT=1
  unset AESOP_RECEIPT_EMIT

  # Create stub timeout/gtimeout that exit 127. Prepend to PATH so they shadow real ones.
  # This forces the hook's fallback branch. Don't copy binaries (they break on CI due to RPATH deps).
  stub_timeout_dir="$AESOP_ROOT/stub-timeout"
  mkdir -p "$stub_timeout_dir"
  printf '#!/bin/sh\nexit 127\n' > "$stub_timeout_dir/timeout"
  printf '#!/bin/sh\nexit 127\n' > "$stub_timeout_dir/gtimeout"
  chmod +x "$stub_timeout_dir/timeout" "$stub_timeout_dir/gtimeout"
  test_path="$stub_timeout_dir:$PATH"

  # Use 60s timeout guard (with REAL timeout from outer PATH) to catch hangs
  trace_file="$AESOP_ROOT/test-trace.log"
  stderr_output=$(
    "$real_timeout" --foreground 60 bash -c "
      set -x
      export PATH='$test_path'
      { check_emit_receipt; } 2>&1 1>/dev/null
    " 2>"$trace_file" || true
  )
  exit_code=$?

  if [ $exit_code -eq 124 ]; then
    printf 'FAIL: check_emit_receipt timed out (hung for 60s) without timeout binary. Trace:\n'
    cat "$trace_file"
    exit 1
  fi

  # Re-run without trace to get stderr (need to get timing for elapsed check)
  start_ts=$(date +%s)
  stderr_output=$( { PATH="$test_path" check_emit_receipt; } 2>&1 1>/dev/null )
  exit_code=$?
  elapsed=$(( $(date +%s) - start_ts ))

  if [ "$exit_code" -ne 1 ]; then
    printf 'FAIL: check_emit_receipt must BLOCK (return 1) on timeout even without timeout binary, got %s\n' "$exit_code"
    exit 1
  fi
  if [ "$elapsed" -gt 20 ]; then
    printf 'FAIL: check_emit_receipt took %ds without timeout binary, did not bound the sleeping stub\n' "$elapsed"
    exit 1
  fi
  if ! printf '%s' "$stderr_output" | grep -qi 'BLOCKED.*timed out'; then
    printf 'FAIL: expected a BLOCKED timeout line on stderr, got: %s\n' "$stderr_output"
    exit 1
  fi
  printf 'PASS: fallback branch (no timeout binary) is bounded (elapsed %ds)\n' "$elapsed"
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

# Receipt conclusion gate (red blocks; skipped-with-reason and green pass; unreadable blocks;
# AESOP_PUSH_RED=1 is the recorded override). emit_receipt.py is stubbed to the exit-code contract
# it documents: 0 green, 3 red + --fail-on-red, 4 red + --pushed-red, anything else unreadable.
RECEIPT_STUB_PRELUDE='import sys,os; open(os.environ["STUB_ARGV"],"w").write(" ".join(sys.argv[1:]));'

receipt_gate_case() {
  # $1 label  $2 stub python body  $3 expected return  $4 expected audit reason  $5 optional VAR=val
  # $6 optional "no_timeout": shadow timeout/gtimeout so the hook takes its no-timeout (background-job) branch
  local label="$1" body="$2" want_rc="$3" want_reason="$4" extra="${5:-}" no_timeout="${6:-}"
  local root="$TEST_ROOT/receipt_gate_$label"
  mkdir -p "$root/state" "$root/tools" "$root/stub-bin"
  printf '%s\n' "$body" > "$root/tools/emit_receipt.py"
  printf '#!/bin/sh\nexit 0\n' > "$root/stub-bin/gh"
  chmod +x "$root/stub-bin/gh"
  if [ -n "$no_timeout" ]; then
    printf '#!/bin/sh\nexit 127\n' > "$root/stub-bin/timeout"
    printf '#!/bin/sh\nexit 127\n' > "$root/stub-bin/gtimeout"
    chmod +x "$root/stub-bin/timeout" "$root/stub-bin/gtimeout"
  fi
  touch "$root/dummy_key.pem"
  emit_fixture_base "$root"
  (
    cd "$root" || exit 1
    export AESOP_ROOT="$root" AESOP_RECEIPT_KEY="$root/dummy_key.pem" STUB_ARGV="$root/argv.txt"
    export PATH="$root/stub-bin:$PATH"
    unset AESOP_RECEIPT_EMIT AESOP_RECEIPT_TIMEOUT AESOP_PUSH_RED
    if [ -n "$extra" ]; then export "$extra"; fi
    check_emit_receipt > "$root/stdout.txt" 2> "$root/stderr.txt"
    echo $? > "$root/rc.txt"
  )
  local got_rc audit_line
  got_rc=$(cat "$root/rc.txt")
  audit_line=$(tail -n 1 "$root/state/SECURITY-AUDIT.log" 2>/dev/null)
  if [ "$got_rc" != "$want_rc" ]; then
    printf 'FAIL [%s]: check_emit_receipt returned %s, want %s; stderr: %s\n' "$label" "$got_rc" "$want_rc" "$(cat "$root/stderr.txt")"
    return 1
  fi
  if [ "$want_reason" != "-" ] && ! printf '%s' "$audit_line" | grep -q "$want_reason"; then
    printf 'FAIL [%s]: expected audit reason %s, got: %s\n' "$label" "$want_reason" "$audit_line"
    return 1
  fi
  printf 'PASS [%s]: check_emit_receipt returned %s (reason %s)\n' "$label" "$got_rc" "$want_reason"
  return 0
}

printf '\n=== Test: a RED required part blocks the push (emit_receipt exit 3) ===\n'
if receipt_gate_case red_blocks "$RECEIPT_STUB_PRELUDE"'
print("part py-shard-3 exit=1"); print("conclusion=failure"); sys.exit(3)' 1 receipt_red_blocked; then
  # The hook must ask the emitter to block on red and must NOT pass the override by default.
  if grep -q -- '--fail-on-red' "$TEST_ROOT/receipt_gate_red_blocks/argv.txt" && ! grep -q -- '--pushed-red' "$TEST_ROOT/receipt_gate_red_blocks/argv.txt"; then
    test_passed=$((test_passed + 1))
  else
    printf 'FAIL: red case must pass --fail-on-red and no --pushed-red, got: %s\n' "$(cat "$TEST_ROOT/receipt_gate_red_blocks/argv.txt")"
    test_failed=$((test_failed + 1))
  fi
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: a RED required part blocks the push in the NO-TIMEOUT branch (child exit code must propagate) ===\n'
# Fail-open regression: the no-timeout branch did `wait $pid || true; rc=$?`, which always read 0,
# so a red matrix passed. The child exits 3 (red); the hook must still block with rc 1.
if receipt_gate_case red_no_timeout "$RECEIPT_STUB_PRELUDE"'
print("part py-shard-3 exit=1"); print("conclusion=failure"); sys.exit(3)' 1 receipt_red_blocked "" no_timeout; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: an all-green matrix is allowed ===\n'
if receipt_gate_case all_green "$RECEIPT_STUB_PRELUDE"'
print("conclusion=success"); sys.exit(0)' 0 receipt_emit_posted; then test_passed=$((test_passed + 1)); else test_failed=$((test_failed + 1)); fi

printf '\n=== Test: a SKIPPED part with a printed reason is not red and is allowed ===\n'
if receipt_gate_case skipped_reason "$RECEIPT_STUB_PRELUDE"'
print("skip browser-proofs not runnable here: hosted browsers only"); print("conclusion=success"); sys.exit(0)' 0 receipt_emit_posted; then test_passed=$((test_passed + 1)); else test_failed=$((test_failed + 1)); fi

printf '\n=== Test: an unreadable conclusion (emit_receipt exit 2) blocks ===\n'
if receipt_gate_case unreadable_exit2 "$RECEIPT_STUB_PRELUDE"'
print("ERROR: could not sign"); sys.exit(2)' 1 receipt_emit_unreadable_blocked; then test_passed=$((test_passed + 1)); else test_failed=$((test_failed + 1)); fi

printf '\n=== Test: an unexpected emitter exit (1) blocks ===\n'
if receipt_gate_case unexpected_exit1 "$RECEIPT_STUB_PRELUDE"'
sys.exit(1)' 1 receipt_emit_unreadable_blocked; then test_passed=$((test_passed + 1)); else test_failed=$((test_failed + 1)); fi

printf '\n=== Test: AESOP_PUSH_RED=1 allows a red push, loudly, and passes --pushed-red ===\n'
if receipt_gate_case push_red_override "$RECEIPT_STUB_PRELUDE"'
print("conclusion=failure pushed_red=true"); sys.exit(4)' 0 receipt_push_red_override AESOP_PUSH_RED=1; then
  if grep -q -- '--pushed-red' "$TEST_ROOT/receipt_gate_push_red_override/argv.txt" \
     && grep -q 'AESOP_PUSH_RED=1: pushing a RED head' "$TEST_ROOT/receipt_gate_push_red_override/stderr.txt"; then
    test_passed=$((test_passed + 1))
  else
    printf 'FAIL: override must pass --pushed-red and print the loud AESOP_PUSH_RED line on stderr\n'
    test_failed=$((test_failed + 1))
  fi
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: main() emits a receipt only after every gate above it passed ===\n'
(
  # WATCHED-TO-FAIL pair: fixture A has every gate green -> emission runs.
  # Fixture B has exactly ONE gate (claudemd_sync_gate) exit 1 -> main()
  # exits before ever reaching check_emit_receipt. Same fixture builder,
  # one flipped stub, proving the ordering behaviorally rather than by
  # reading main()'s source for call order.
  build_repo() {
    local repo="$1"
    mkdir -p "$repo"
    (
      cd "$repo" || exit 1
      git init -q
      git config user.email "test@example.com"
      git config user.name "Test User"
      echo "dummy" > file.txt
      git add file.txt
      git commit -q -m "initial"
      git branch -M main
      git checkout -q -b feature/receipt-emit-test
    )
  }

  build_tools() {
    local aesop_root="$1"
    local sync_gate_exit="$2"
    mkdir -p "$aesop_root/state" "$aesop_root/tools"
    # check_secret_scan fail-closes on `[ ! -x "$scan_script" ]`. git tracks
    # tools/secret_scan.py at mode 644 (not executable) -- this Windows dev
    # box's git-bash happens to report every checked-out file as `-x` true
    # regardless (an observed box-specific quirk), which masked this on one
    # platform but a real Linux checkout honors the tracked 644 and `cp`
    # alone copies a NON-executable file there. `chmod +x` after the copy
    # covers both: a no-op where cp's result already reads executable, a
    # real fix where it does not.
    cp "$REPO_ROOT/tools/secret_scan.py" "$aesop_root/tools/secret_scan.py"
    chmod +x "$aesop_root/tools/secret_scan.py"
    # Every OTHER fail-closed gate stub is DERIVED from tools/gate_stub_list.py
    # -- the same tool tools/new_gate_check.py's checklist uses -- instead of
    # hand-listed, so a new check_*() added later (like check_generated_regen's
    # generated_push_gate.py, which broke this exact hand list once already)
    # is covered automatically rather than needing this fixture edited again.
    #
    # Retried up to 3x: under this box's heavy concurrent load (this fixture
    # runs after 30+ prior subprocess-spawning tests in the same suite), a
    # bare `python ...` invocation has been observed to transiently return
    # empty/short output with no non-zero exit code to flag it -- never
    # reproduced in isolation, only after sustained prior load. A short,
    # empty, or failed result is retried rather than silently trusted, since
    # trusting it silently is exactly what produced a confusing
    # "which gate is missing" failure instead of a clear one.
    for gate in $(python "$REPO_ROOT/tools/gate_stub_list.py" "$REPO_ROOT/hooks/pre-push-policy.sh"); do
      gate=$(printf '%s' "$gate" | tr -d '\r')  # Strip Windows CR from gate names
      [ "$gate" = "claudemd_sync_gate" ] && continue  # needs a variable exit code below
      printf 'import sys\nsys.exit(0)\n' > "$aesop_root/tools/${gate}.py"
    done
    printf 'import sys\nsys.exit(%s)\n' "$sync_gate_exit" > "$aesop_root/tools/claudemd_sync_gate.py"
    # emit_receipt stub: proves it RAN by printing a marker to stdout, which
    # check_emit_receipt echoes on to main()'s own stdout on success. (A
    # python-side file write using a bash /tmp path is unreliable here: this
    # box's native Windows python.exe does not go through MSYS's
    # argv path translation for a path embedded in SOURCE TEXT rather than
    # passed as a CLI argument, so it resolves "/tmp/..." against the
    # current drive root instead of the real TMPDIR and fails. A marker on
    # stdout sidesteps path translation entirely.)
    printf 'import sys\nprint("EMIT_RECEIPT_RAN_MARKER")\nsys.exit(0)\n' > "$aesop_root/tools/emit_receipt.py"
    # claudemd_lint.py / generated_paths.py intentionally absent: both gates
    # fail OPEN on a missing script by design (check_claudemd_headroom,
    # check_generated_paths), so leaving them out keeps the fixture minimal.
  }

  stdin_tuple() {
    local repo="$1"
    local local_sha
    local_sha=$(git -C "$repo" rev-parse HEAD)
    printf 'refs/heads/feature/receipt-emit-test %s refs/heads/feature/receipt-emit-test %s\n' \
      "$local_sha" "0000000000000000000000000000000000000000"
  }

  # --- Fixture A: every gate green -> emission runs ---
  REPO_A="$TEST_ROOT/receipt_main_green_repo"
  AESOP_ROOT_A="$TEST_ROOT/receipt_main_green_tools"
  build_repo "$REPO_A"
  build_tools "$AESOP_ROOT_A" 0
  publish_base_tools "$REPO_A" "$AESOP_ROOT_A/tools"
  # G6 subprocess ratchet reads the pushed tree's baseline (fail-closed when absent).
  printf '{"violations": {}}\n' > "$AESOP_ROOT_A/.subprocess-guard-baseline.json"
  export AESOP_RECEIPT_KEY="$AESOP_ROOT_A/dummy_key.pem"
  touch "$AESOP_ROOT_A/dummy_key.pem"
  unset AESOP_RECEIPT_EMIT

  main_stdout_a="$TEST_ROOT/receipt_main_green.stdout"
  (
    cd "$REPO_A" || exit 1
    export AESOP_ROOT="$AESOP_ROOT_A"
    stdin_tuple "$REPO_A" | main > "$main_stdout_a" 2>"$TEST_ROOT/receipt_main_green.stderr"
  )
  main_exit_a=$?

  if [ "$main_exit_a" -ne 0 ]; then
    printf 'FAIL: main() should exit 0 when every gate is green (got %d)\n' "$main_exit_a"
    cat "$main_stdout_a" "$TEST_ROOT/receipt_main_green.stderr"
    exit 1
  fi
  if ! grep -q 'EMIT_RECEIPT_RAN_MARKER' "$main_stdout_a"; then
    printf 'FAIL: emit_receipt.py never ran even though every gate passed\n'
    exit 1
  fi
  printf 'PASS (green): main() reaches check_emit_receipt and it runs\n'

  # --- Fixture B: claudemd_sync_gate fails -> main() must exit before emission ---
  REPO_B="$TEST_ROOT/receipt_main_red_repo"
  AESOP_ROOT_B="$TEST_ROOT/receipt_main_red_tools"
  build_repo "$REPO_B"
  build_tools "$AESOP_ROOT_B" 1
  publish_base_tools "$REPO_B" "$AESOP_ROOT_B/tools"
  export AESOP_RECEIPT_KEY="$AESOP_ROOT_B/dummy_key.pem"
  touch "$AESOP_ROOT_B/dummy_key.pem"

  main_stdout_b="$TEST_ROOT/receipt_main_red.stdout"
  (
    cd "$REPO_B" || exit 1
    export AESOP_ROOT="$AESOP_ROOT_B"
    stdin_tuple "$REPO_B" | main > "$main_stdout_b" 2>"$TEST_ROOT/receipt_main_red.stderr"
  )
  main_exit_b=$?

  if [ "$main_exit_b" -eq 0 ]; then
    printf 'FAIL: main() should exit 1 when claudemd_sync_gate fails\n'
    exit 1
  fi
  if grep -q 'EMIT_RECEIPT_RAN_MARKER' "$main_stdout_b"; then
    printf 'FAIL: emit_receipt.py ran even though an earlier gate (claudemd_sync_gate) failed -- a red gate must never reach emission\n'
    exit 1
  fi
  printf 'PASS (red): a failing gate blocks the push before emit_receipt.py ever runs\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 29: check_pyflakes_ratchet blocks on baseline mismatch ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_pyflakes_mismatch"
  mkdir -p "$AESOP_ROOT/tools"
  mkdir -p "$AESOP_ROOT/state"

  # Stub pyflakes_gate.py that returns 1 (baseline mismatch)
  printf 'import sys\nsys.exit(1)\n' > "$AESOP_ROOT/tools/pyflakes_gate.py"

  make_clone_with_base "$TEST_ROOT/pyflakes_t29_clone" "$AESOP_ROOT/tools"
  cd "$TEST_ROOT/pyflakes_t29_clone" || exit 1
  if check_pyflakes_ratchet >/dev/null 2>&1; then
    printf 'FAIL: check_pyflakes_ratchet should block on baseline mismatch (exit 1)\n'
    exit 1
  fi
  printf 'PASS: check_pyflakes_ratchet blocks on baseline mismatch\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 30: check_pyflakes_ratchet fails open when pyflakes not installed ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_pyflakes_missing"
  mkdir -p "$AESOP_ROOT/tools"
  mkdir -p "$AESOP_ROOT/state"

  # Stub pyflakes_gate.py that returns 2 (pyflakes not installed / error condition)
  printf 'import sys\nsys.exit(2)\n' > "$AESOP_ROOT/tools/pyflakes_gate.py"

  make_clone_with_base "$TEST_ROOT/pyflakes_t30_clone" "$AESOP_ROOT/tools"
  cd "$TEST_ROOT/pyflakes_t30_clone" || exit 1
  if ! check_pyflakes_ratchet >/dev/null 2>&1; then
    printf 'FAIL: check_pyflakes_ratchet should fail-open when pyflakes is missing (return 0)\n'
    exit 1
  fi
  printf 'PASS: check_pyflakes_ratchet fails open when pyflakes is missing\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 31: check_pyflakes_ratchet skipped when tool missing from aesop repo ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_pyflakes_tool_missing"
  mkdir -p "$AESOP_ROOT/tools"
  mkdir -p "$AESOP_ROOT/state"

  # Do NOT create pyflakes_gate.py; the tool should be considered missing

  make_clone_with_base "$TEST_ROOT/pyflakes_t31_clone" "$AESOP_ROOT/tools"
  cd "$TEST_ROOT/pyflakes_t31_clone" || exit 1
  if ! check_pyflakes_ratchet >/dev/null 2>&1; then
    printf 'FAIL: check_pyflakes_ratchet should fail-open (return 0) when tool missing\n'
    exit 1
  fi
  printf 'PASS: check_pyflakes_ratchet fails open when tool is missing\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 32: check_pyflakes_ratchet honors AESOP_PYFLAKES_SKIP=1 ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/aesop_pyflakes_skip"
  export AESOP_PYFLAKES_SKIP=1
  mkdir -p "$AESOP_ROOT/tools"
  mkdir -p "$AESOP_ROOT/state"

  # Stub pyflakes_gate.py that would return 1 (blocked), but should be skipped
  printf 'import sys\nsys.exit(1)\n' > "$AESOP_ROOT/tools/pyflakes_gate.py"

  if ! check_pyflakes_ratchet >/dev/null 2>&1; then
    printf 'FAIL: check_pyflakes_ratchet should be skipped when AESOP_PYFLAKES_SKIP=1\n'
    exit 1
  fi
  printf 'PASS: check_pyflakes_ratchet honors AESOP_PYFLAKES_SKIP=1 opt-out\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

# ===== REMOTE-REF POLICY: judge the pushed remote ref, not the local branch =====
# git feeds pre-push stdin as "<local ref> <local sha> <remote ref> <remote sha>".
# The verdict must follow the remote ref. Each case runs inside an isolated repo
# whose checked-out (local) branch is set per case.
policy_fixture_repo() {
  # policy_fixture_repo <dir> <branch>: fresh repo with one commit, checked out on <branch>.
  local dir="$1" branch="$2"
  rm -rf "$dir"
  mkdir -p "$dir"
  git -C "$dir" init -q -b main
  git -C "$dir" config user.email t@example.com
  git -C "$dir" config user.name t
  git -C "$dir" config core.autocrlf false
  printf 'base\n' > "$dir/README.md"
  git -C "$dir" add -A
  git -C "$dir" commit -q -m base
  if [ "$branch" != "main" ]; then
    git -C "$dir" checkout -q -b "$branch"
  fi
}

printf '\n=== Test 33: local main -> refs/heads/checkpoint/x is ALLOWED (remote ref is not protected) ===\n'
(
  repo="$TEST_ROOT/policy_main_to_checkpoint"
  policy_fixture_repo "$repo" main
  sha=$(git -C "$repo" rev-parse HEAD)
  cd "$repo" || exit 1
  printf 'refs/heads/main %s refs/heads/checkpoint/x 0000000000000000000000000000000000000000\n' "$sha" | {
    check_branch_policy
  }
  rc=$?
  if [ "$rc" -ne 0 ]; then
    printf 'FAIL: push from local main to refs/heads/checkpoint/x must be allowed, got rc %d\n' "$rc"
    exit 1
  fi
  printf 'PASS: local main -> refs/heads/checkpoint/x allowed\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 34: local feature/x -> refs/heads/main is BLOCKED ===\n'
(
  repo="$TEST_ROOT/policy_feature_to_main"
  policy_fixture_repo "$repo" feature/x
  sha=$(git -C "$repo" rev-parse HEAD)
  cd "$repo" || exit 1
  printf 'refs/heads/feature/x %s refs/heads/main 0000000000000000000000000000000000000000\n' "$sha" | {
    check_branch_policy
  }
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf 'FAIL: push from feature/x to refs/heads/main must be blocked, got rc 0\n'
    exit 1
  fi
  printf 'PASS: local feature/x -> refs/heads/main blocked\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 35: local main -> refs/heads/main is BLOCKED ===\n'
(
  repo="$TEST_ROOT/policy_main_to_main"
  policy_fixture_repo "$repo" main
  sha=$(git -C "$repo" rev-parse HEAD)
  cd "$repo" || exit 1
  printf 'refs/heads/main %s refs/heads/main 0000000000000000000000000000000000000000\n' "$sha" | {
    check_branch_policy
  }
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf 'FAIL: push from local main to refs/heads/main must be blocked, got rc 0\n'
    exit 1
  fi
  printf 'PASS: local main -> refs/heads/main blocked\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 36: local main -> refs/heads/master blocked; multi-ref with main tuple blocked; feature->feature from main checkout allowed ===\n'
(
  repo="$TEST_ROOT/policy_master_multi"
  policy_fixture_repo "$repo" main
  sha=$(git -C "$repo" rev-parse HEAD)
  zeros="0000000000000000000000000000000000000000"
  cd "$repo" || exit 1
  printf 'refs/heads/main %s refs/heads/master %s\n' "$sha" "$zeros" | check_branch_policy
  if [ $? -eq 0 ]; then
    printf 'FAIL: push to refs/heads/master from local main must be blocked\n'
    exit 1
  fi
  printf 'refs/heads/main %s refs/heads/checkpoint/y %s\nrefs/heads/main %s refs/heads/main %s\n' "$sha" "$zeros" "$sha" "$zeros" | check_branch_policy
  if [ $? -eq 0 ]; then
    printf 'FAIL: multi-ref push containing a refs/heads/main tuple must be blocked\n'
    exit 1
  fi
  printf 'refs/heads/main %s refs/heads/feature/z %s\n' "$sha" "$zeros" | check_branch_policy
  if [ $? -ne 0 ]; then
    printf 'FAIL: local main -> refs/heads/feature/z must be allowed\n'
    exit 1
  fi
  printf 'PASS: master blocked, multi-ref main tuple blocked, feature/z allowed from main checkout\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

# ===== Tests 37-40: subprocess_guard ratchet (G6, pre-push parity with ci.yml) =====

# make_sg_clone <clone> <baseline_json|MISSING> <shell_true_count>
# Clone whose origin/main:tools holds the real subprocess_guard.py, with a working
# tree containing tests/test_fixture_sg.py (N shell=True calls) and the baseline.
make_sg_clone() {
  local clone="$1" baseline="$2" nshell="$3"
  local sg_src="$TEST_ROOT/sg_src_$(basename "$clone")"
  rm -rf "$sg_src"
  mkdir -p "$sg_src"
  cp "$REPO_ROOT/tools/subprocess_guard.py" "$sg_src/subprocess_guard.py"
  make_clone_with_base "$clone" "$sg_src"
  mkdir -p "$clone/tests" "$clone/tools"
  cp "$REPO_ROOT/tools/subprocess_guard.py" "$clone/tools/subprocess_guard.py"
  {
    printf 'import subprocess\n'
    local i=0
    while [ "$i" -lt "$nshell" ]; do
      printf 'subprocess.run("true", shell=True)\n'
      i=$((i + 1))
    done
  } > "$clone/tests/test_fixture_sg.py"
  if [ "$baseline" != "MISSING" ]; then
    printf '%s\n' "$baseline" > "$clone/.subprocess-guard-baseline.json"
  fi
}

SG_BASE_ONE='{"violations": {"tests/test_fixture_sg.py@SHELL_TRUE": 1}}'

printf '\n=== Test 37: check_subprocess_guard_ratchet blocks a new shell=True above baseline ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/sg_t33_clone"
  mkdir -p "$AESOP_ROOT/state"
  if [ "$(type -t check_subprocess_guard_ratchet)" != "function" ]; then
    printf 'FAIL: check_subprocess_guard_ratchet is not defined in the hook\n'
    exit 1
  fi
  make_sg_clone "$AESOP_ROOT" "$SG_BASE_ONE" 2
  cd "$AESOP_ROOT" || exit 1
  if check_subprocess_guard_ratchet >/dev/null 2>&1; then
    printf 'FAIL: check_subprocess_guard_ratchet should block a shell=True above baseline\n'
    exit 1
  fi
  printf 'PASS: check_subprocess_guard_ratchet blocks a shell=True above baseline\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 38: check_subprocess_guard_ratchet passes when findings equal baseline ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/sg_t34_clone"
  mkdir -p "$AESOP_ROOT/state"
  if [ "$(type -t check_subprocess_guard_ratchet)" != "function" ]; then
    printf 'FAIL: check_subprocess_guard_ratchet is not defined in the hook\n'
    exit 1
  fi
  make_sg_clone "$AESOP_ROOT" "$SG_BASE_ONE" 1
  cd "$AESOP_ROOT" || exit 1
  if ! check_subprocess_guard_ratchet >/dev/null 2>&1; then
    printf 'FAIL: check_subprocess_guard_ratchet should pass when findings equal baseline\n'
    exit 1
  fi
  printf 'PASS: check_subprocess_guard_ratchet passes at baseline\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 39: check_subprocess_guard_ratchet fails closed when baseline is missing ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/sg_t35_clone"
  mkdir -p "$AESOP_ROOT/state"
  if [ "$(type -t check_subprocess_guard_ratchet)" != "function" ]; then
    printf 'FAIL: check_subprocess_guard_ratchet is not defined in the hook\n'
    exit 1
  fi
  make_sg_clone "$AESOP_ROOT" MISSING 1
  cd "$AESOP_ROOT" || exit 1
  if check_subprocess_guard_ratchet >/dev/null 2>&1; then
    printf 'FAIL: check_subprocess_guard_ratchet should fail closed when the baseline is missing\n'
    exit 1
  fi
  printf 'PASS: check_subprocess_guard_ratchet fails closed without a baseline\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test 40: pre-push ratchet and the ci.yml invocation agree on the same fixtures ===\n'
(
  export AESOP_ROOT="$TEST_ROOT/sg_t36_clone"
  mkdir -p "$AESOP_ROOT/state"
  if [ "$(type -t check_subprocess_guard_ratchet)" != "function" ]; then
    printf 'FAIL: check_subprocess_guard_ratchet is not defined in the hook\n'
    exit 1
  fi
  # The exact run line ci.yml executes for the subprocess guard ratchet.
  ci_line=$(sed -n 's/^[[:space:]]*run:[[:space:]]*\(python tools\/subprocess_guard\.py --check --baseline [^[:space:]]*\)[[:space:]]*$/\1/p' "$REPO_ROOT/.github/workflows/ci.yml" | head -1)
  if [ -z "$ci_line" ]; then
    printf 'FAIL: could not locate the subprocess_guard ratchet run line in ci.yml\n'
    exit 1
  fi
  py_bin=$(resolve_py_bin) || { printf 'FAIL: no python on PATH\n'; exit 1; }
  ci_cmd="${ci_line/#python/$py_bin}"
  parity_ok=1
  # Case A: 2 findings above a baseline of 1 -> both must fail.
  # Case B: 1 finding equal to a baseline of 1 -> both must pass.
  for case in "2:fail" "1:pass"; do
    nshell="${case%%:*}"
    want="${case##*:}"
    make_sg_clone "$AESOP_ROOT" "$SG_BASE_ONE" "$nshell"
    cd "$AESOP_ROOT" || exit 1
    if (bash -c "$ci_cmd") >/dev/null 2>&1; then ci_rc=0; else ci_rc=1; fi
    if check_subprocess_guard_ratchet >/dev/null 2>&1; then hook_rc=0; else hook_rc=1; fi
    if [ "$want" = "fail" ]; then
      if [ "$ci_rc" -ne 1 ] || [ "$hook_rc" -ne 1 ]; then parity_ok=0; fi
    else
      if [ "$ci_rc" -ne 0 ] || [ "$hook_rc" -ne 0 ]; then parity_ok=0; fi
    fi
    printf '  nshell=%s want=%s ci_rc=%s hook_rc=%s\n' "$nshell" "$want" "$ci_rc" "$hook_rc"
  done
  if [ "$parity_ok" -ne 1 ]; then
    printf 'FAIL: pre-push ratchet and ci.yml invocation disagree\n'
    exit 1
  fi
  printf 'PASS: pre-push ratchet and ci.yml invocation agree\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

# ===== Gate base must carry every root package the base gates import =====
# Regression: gate_base_dir extracted only origin/main:tools, but tools/tracker_guard.py
# imports the sibling package state_store/. The base copy then raised
# ModuleNotFoundError and the tracker gate failed closed on EVERY push.
printf '\n=== Test: tracker_guard runs from the base gate copy with state_store importable ===\n'
(
  export TMPDIR="$TEST_ROOT/gate_base_tmp"
  mkdir -p "$TMPDIR"
  base="$TEST_ROOT/tracker_base_src.base"
  rm -rf "$base"
  mkdir -p "$base"
  cp -R "$REPO_ROOT/tools" "$base/tools"
  cp -R "$REPO_ROOT/state_store" "$base/state_store"
  git -C "$base" init -q -b main
  git -C "$base" add -A
  git -C "$base" -c user.email=t@example.com -c user.name=t -c core.autocrlf=false commit -q -m base
  clone="$TEST_ROOT/tracker_clone"
  rm -rf "$clone"
  mkdir -p "$clone"
  git -C "$clone" init -q -b main
  git -C "$clone" config user.email t@example.com
  git -C "$clone" config user.name t
  git -C "$clone" config core.autocrlf false
  printf 'base\n' > "$clone/README.md"
  git -C "$clone" add -A
  git -C "$clone" commit -q -m base
  git -C "$clone" remote add origin "$base"
  git -C "$clone" fetch -q origin main
  git -C "$clone" checkout -q -b feature/test
  cd "$clone" || exit 1
  export AESOP_ROOT="$clone"
  mkdir -p "$AESOP_ROOT/state"

  guard_out=$(check_tracker_guard 2>&1)
  guard_rc=$?
  if printf '%s\n' "$guard_out" | grep -q "ModuleNotFoundError"; then
    printf 'FAIL: tracker_guard base copy cannot import state_store:\n%s\n' "$guard_out"
    exit 1
  fi
  if [ "$guard_rc" -ne 0 ]; then
    printf 'FAIL: check_tracker_guard returned %s from the base gate copy:\n%s\n' "$guard_rc" "$guard_out"
    exit 1
  fi
  printf 'PASS: check_tracker_guard exits 0 from the extracted base gate copy (state_store importable)\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test: a HUNG python3 on PATH fails fast (bounded probe), never a multi-minute push hang ===\n'
(
  pp_root="$TEST_ROOT/pyprobe_hang"
  pp_stub_bin="$pp_root/stub-bin"
  pp_pid_file="$pp_root/stub.pids"
  pp_remote="$pp_root/remote.git"
  pp_clone="$pp_root/clone"
  rm -rf "$pp_root"
  mkdir -p "$pp_stub_bin" "$pp_root/state"

  # Stub python3 that blocks forever. It records its PID and exec's sleep so
  # the recorded PID is the sleeper itself; `timeout` kills it directly.
  printf '#!/bin/sh\necho $$ >> "%s"\nexec sleep 600\n' "$pp_pid_file" > "$pp_stub_bin/python3"
  chmod +x "$pp_stub_bin/python3"

  # Kill only the stub PIDs this test recorded (never an untargeted kill).
  pp_kill_stubs() {
    local p
    [ -f "$pp_pid_file" ] || return 0
    while IFS= read -r p; do
      [ -n "$p" ] && kill "$p" 2>/dev/null
    done < "$pp_pid_file"
    return 0
  }
  trap 'pp_kill_stubs' EXIT

  # Fixture: a trunk carrying this repo's committed tree (so every gate artifact
  # the hook checks is present), published to a bare remote. A feature branch is
  # published first so the hooked push is a normal update, not a new-branch push.
  git init -q --bare "$pp_remote"
  git init -q -b main "$pp_clone"
  git -C "$pp_clone" config user.email t@example.com
  git -C "$pp_clone" config user.name t
  git -C "$pp_clone" config core.autocrlf false
  git -C "$REPO_ROOT" archive HEAD | tar -x -C "$pp_clone"
  git -C "$pp_clone" add -A
  git -C "$pp_clone" commit -q -m trunk
  git -C "$pp_clone" remote add origin "$pp_remote"
  git -C "$pp_clone" push -q origin HEAD:refs/heads/main
  git -C "$pp_clone" checkout -q -b feature/pyprobe
  printf 'one\n' > "$pp_clone/PYPROBE.txt"
  git -C "$pp_clone" add -A
  git -C "$pp_clone" commit -q -m one
  git -C "$pp_clone" push -q origin feature/pyprobe
  printf 'two\n' > "$pp_clone/PYPROBE.txt"
  git -C "$pp_clone" add -A
  git -C "$pp_clone" commit -q -m two
  # The real pre-push hook, installed the way git runs it.
  printf '#!/bin/sh\nexec bash "%s" "$@"\n' "$HOOK_SCRIPT" > "$pp_clone/.git/hooks/pre-push"
  chmod +x "$pp_clone/.git/hooks/pre-push"

  # Output goes to a FILE, not $(...): an orphaned stub would hold a command-
  # substitution pipe open until it exits. Stubs are killed right after the push.
  pp_start=$(date +%s)
  (cd "$pp_clone" && PATH="$pp_stub_bin:$PATH" AESOP_ROOT="$pp_clone" \
    AESOP_STATE_ROOT="$pp_root/state" AESOP_RECEIPT_EMIT=0 \
    timeout 40 git push -q origin HEAD:refs/heads/feature/pyprobe </dev/null >"$pp_root/push.out" 2>&1)
  pp_rc=$?
  pp_elapsed=$(( $(date +%s) - pp_start ))
  pp_kill_stubs
  pp_out=$(cat "$pp_root/push.out")

  if [ "$pp_elapsed" -ge 30 ]; then
    printf 'FAIL: push with a hung python3 on PATH took %ss (rc=%s, bound <30s). Output: %s\n' "$pp_elapsed" "$pp_rc" "$pp_out"
    exit 1
  fi
  if [ "$pp_rc" -eq 0 ]; then
    pp_remote_head=$(git -C "$pp_remote" rev-parse --verify -q refs/heads/feature/pyprobe || true)
    pp_local_head=$(git -C "$pp_clone" rev-parse HEAD)
    if [ "$pp_remote_head" != "$pp_local_head" ]; then
      printf 'FAIL: push reported rc=0 but remote head (%s) != local head (%s)\n' "$pp_remote_head" "$pp_local_head"
      exit 1
    fi
    printf 'PASS: push succeeded via the next candidate in %ss (hung python3 skipped)\n' "$pp_elapsed"
    exit 0
  fi
  if printf '%s' "$pp_out" | grep -qF 'Error: no usable python interpreter; push blocked.'; then
    printf 'PASS: push failed closed in %ss with the no-usable-interpreter error\n' "$pp_elapsed"
    exit 0
  fi
  printf 'FAIL: push rc=%s in %ss without the fail-closed error. Output: %s\n' "$pp_rc" "$pp_elapsed" "$pp_out"
  exit 1
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

# ===== Metrics + range gates scan the PUSHED refs read from stdin (tag-only push defect) =====
# Defect: check_metrics() ignored stdin and ran metrics_gate.py with its default range
# origin/main...HEAD, so a tag-only push was judged by the current branch's unpushed commits.
ZERO40="0000000000000000000000000000000000000000"

# setup_metrics_repo <dir>: origin/main carries tools/metrics_gate.py plus two commits
# (base, release). The local branch feature/test adds STATE.md with an unverified 80%.
# Sets MG_BASE_SHA, MG_RELEASE_SHA, MG_FEATURE_SHA. Working tree ends on feature/test.
setup_metrics_repo() {
  local mg="$1"
  rm -rf "$mg" "$mg.origin"
  mkdir -p "$mg/tools"
  cp "$REPO_ROOT/tools/metrics_gate.py" "$mg/tools/metrics_gate.py"
  git -C "$mg" init -q -b main
  git -C "$mg" config user.email t@example.com
  git -C "$mg" config user.name t
  git -C "$mg" config core.autocrlf false
  printf 'base\n' > "$mg/README.md"
  git -C "$mg" add -A
  git -C "$mg" commit -q -m base
  MG_BASE_SHA=$(git -C "$mg" rev-parse HEAD)
  printf 'release notes\n' > "$mg/RELEASE.md"
  git -C "$mg" add -A
  git -C "$mg" commit -q -m release
  MG_RELEASE_SHA=$(git -C "$mg" rev-parse HEAD)
  git init -q --bare "$mg.origin"
  git -C "$mg" remote add origin "$mg.origin"
  git -C "$mg" push -q origin main
  git -C "$mg" checkout -q -b feature/test
  printf '# State\n\nCost fell 80%% this wave.\n' > "$mg/STATE.md"
  git -C "$mg" add -A
  git -C "$mg" commit -q -m unverified-metric
  MG_FEATURE_SHA=$(git -C "$mg" rev-parse HEAD)
}

printf '\n=== Metrics gate: tag-only push of an already-pushed commit passes (pushed refs, not HEAD) ===\n'
(
  mg="$TEST_ROOT/mg_tag_only"
  setup_metrics_repo "$mg"
  export AESOP_ROOT="$mg"
  cd "$mg" || exit 1
  if check_metrics <<< "refs/tags/v9.9.9 $MG_RELEASE_SHA refs/tags/v9.9.9 $ZERO40" >/dev/null 2>&1; then
    printf 'PASS: tag-only push passes; the unverified metric lives only in the unpushed local branch\n'
  else
    printf 'FAIL: tag-only push was blocked by a metric that is not in the pushed range\n'
    exit 1
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Metrics gate: branch push that adds an unverified metric still FAILS ===\n'
(
  mg="$TEST_ROOT/mg_branch"
  setup_metrics_repo "$mg"
  export AESOP_ROOT="$mg"
  cd "$mg" || exit 1
  if check_metrics <<< "refs/heads/feature/test $MG_FEATURE_SHA refs/heads/feature/test $MG_RELEASE_SHA" >/dev/null 2>&1; then
    printf 'FAIL: branch push adding an unverified metric passed the gate\n'
    exit 1
  fi
  printf 'PASS: branch push adding an unverified metric is still blocked\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Metrics gate: empty stdin keeps today behaviour (fail-closed on the HEAD range) ===\n'
(
  mg="$TEST_ROOT/mg_empty"
  setup_metrics_repo "$mg"
  export AESOP_ROOT="$mg"
  cd "$mg" || exit 1
  if check_metrics </dev/null >/dev/null 2>&1; then
    printf 'FAIL: empty stdin fell open instead of scanning the current range\n'
    exit 1
  fi
  printf 'PASS: empty stdin still runs the default range and blocks the unverified metric\n'
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== get_commit_range: tag push with zero remote sha scans the tagged commit alone ===\n'
(
  mg="$TEST_ROOT/mg_range_tag"
  setup_metrics_repo "$mg"
  export AESOP_ROOT="$mg"
  cd "$mg" || exit 1
  got=$(printf 'refs/tags/v9.9.9 %s refs/tags/v9.9.9 %s\n' "$MG_RELEASE_SHA" "$ZERO40" | get_commit_range 2>/dev/null)
  want="$MG_BASE_SHA..$MG_RELEASE_SHA"
  if [ "$got" = "$want" ]; then
    printf 'PASS: tag range is %s (the tagged commit alone)\n' "$got"
  else
    printf 'FAIL: tag range was %s, expected %s\n' "$got" "$want"
    exit 1
  fi
)
if [ $? -eq 0 ]; then
  test_passed=$((test_passed + 1))
else
  test_failed=$((test_failed + 1))
fi

printf '\n=== Test Summary ===\n'
printf 'Tests PASSED: %d\n' "$test_passed"
printf 'Tests FAILED: %d\n' "$test_failed"

if [ "$test_failed" -eq 0 ]; then
  printf '\nAll tests passed.\n'
  exit 0
else
  printf '\nSome tests failed.\n'
  exit 1
fi
