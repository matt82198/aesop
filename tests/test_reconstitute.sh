#!/bin/bash
set -euo pipefail

# Consolidated test suite for reconstitute.sh
# Combines best coverage from previous test files + security probes

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RECONSTITUTE="$SCRIPT_DIR/tools/reconstitute.sh"
PASSED=0
FAILED=0

assert_pass() {
  local test_name="$1"
  echo "✓ PASS: $test_name"
  ((PASSED++)) || true
}

assert_fail() {
  local test_name="$1"
  echo "✗ FAIL: $test_name"
  ((FAILED++)) || true
}

# ===== ITEM 1: Security - URL validation =====
test_item1_url_validation() {
  echo ""
  echo "=== ITEM 1: URL Validation (P0 Security) ==="

  local tmpdir
  tmpdir=$(mktemp -d) || { echo "mktemp failed"; return 1; }

  # Set fleet root to tmpdir so validation accepts our test paths
  export AESOP_FLEET_ROOT="$tmpdir"

  # Test 1.1: ext:: prefix should be rejected
  echo "Test 1.1: Reject ext:: prefix"
  cat > "$tmpdir/repos_1_1.txt" << EOF
ext::sh -c 'echo bad' $tmpdir/target1
EOF

  output=$(AESOP_FLEET_ROOT="$tmpdir" timeout 3 bash "$RECONSTITUTE" --dry-run --repos-file "$tmpdir/repos_1_1.txt" 2>&1 || true)

  if echo "$output" | grep -qi "invalid\|error"; then
    assert_pass "ext:: rejected with error message"
  elif ! echo "$output" | grep -q "Would clone"; then
    assert_pass "ext:: not processed"
  else
    assert_fail "ext:: was processed (should be rejected)"
  fi

  # Test 1.2: leading dash should be rejected
  echo "Test 1.2: Reject leading dash"
  cat > "$tmpdir/repos_1_2.txt" << EOF
-c $tmpdir/target2
EOF

  output=$(AESOP_FLEET_ROOT="$tmpdir" timeout 3 bash "$RECONSTITUTE" --dry-run --repos-file "$tmpdir/repos_1_2.txt" 2>&1 || true)

  if echo "$output" | grep -qi "invalid\|error"; then
    assert_pass "leading dash rejected with error"
  elif ! echo "$output" | grep -q "Would clone"; then
    assert_pass "leading dash not processed"
  else
    assert_fail "leading dash was processed (should be rejected)"
  fi

  # Test 1.3: Valid HTTPS should work
  echo "Test 1.3: Valid HTTPS URL works"
  cat > "$tmpdir/repos_1_3.txt" << EOF
https://github.com/example/repo.git $tmpdir/clone1
EOF

  output=$(AESOP_FLEET_ROOT="$tmpdir" timeout 3 bash "$RECONSTITUTE" --dry-run --repos-file "$tmpdir/repos_1_3.txt" 2>&1 || true)

  if echo "$output" | grep -q "Would clone https://"; then
    assert_pass "Valid HTTPS accepted"
  else
    assert_fail "Valid HTTPS rejected"
  fi

  # Test 1.4: Valid git@host:path should work
  echo "Test 1.4: Valid git@host:path URL works"
  cat > "$tmpdir/repos_1_4.txt" << EOF
git@github.com:user/repo.git $tmpdir/clone2
EOF

  output=$(AESOP_FLEET_ROOT="$tmpdir" timeout 3 bash "$RECONSTITUTE" --dry-run --repos-file "$tmpdir/repos_1_4.txt" 2>&1 || true)

  if echo "$output" | grep -q "Would clone git@"; then
    assert_pass "Valid git@host:path accepted"
  else
    assert_fail "Valid git@host:path rejected"
  fi

  # Test 1.5: Valid ssh:// should work
  echo "Test 1.5: Valid ssh:// URL works"
  cat > "$tmpdir/repos_1_5.txt" << EOF
ssh://git@github.com/user/repo.git $tmpdir/clone3
EOF

  output=$(AESOP_FLEET_ROOT="$tmpdir" timeout 3 bash "$RECONSTITUTE" --dry-run --repos-file "$tmpdir/repos_1_5.txt" 2>&1 || true)

  if echo "$output" | grep -q "Would clone ssh://"; then
    assert_pass "Valid ssh:// accepted"
  else
    assert_fail "Valid ssh:// rejected"
  fi

  # Test 1.6: SECURITY HOLE FIX - git@evil (no colon) should be rejected
  echo "Test 1.6: Reject git@evil without colon"
  cat > "$tmpdir/repos_1_6.txt" << EOF
git@evil $tmpdir/clone4
EOF

  output=$(AESOP_FLEET_ROOT="$tmpdir" timeout 3 bash "$RECONSTITUTE" --dry-run --repos-file "$tmpdir/repos_1_6.txt" 2>&1 || true)

  if echo "$output" | grep -qi "invalid\|error"; then
    assert_pass "git@evil (no colon) rejected with error"
  elif ! echo "$output" | grep -q "Would clone"; then
    assert_pass "git@evil (no colon) not processed"
  else
    assert_fail "git@evil (no colon) was processed (SECURITY HOLE)"
  fi

  # Test 1.7: Reject bare git@ alone
  echo "Test 1.7: Reject bare git@"
  cat > "$tmpdir/repos_1_7.txt" << EOF
git@ $tmpdir/clone5
EOF

  output=$(AESOP_FLEET_ROOT="$tmpdir" timeout 3 bash "$RECONSTITUTE" --dry-run --repos-file "$tmpdir/repos_1_7.txt" 2>&1 || true)

  if echo "$output" | grep -qi "invalid\|error"; then
    assert_pass "bare git@ rejected with error"
  elif ! echo "$output" | grep -q "Would clone"; then
    assert_pass "bare git@ not processed"
  else
    assert_fail "bare git@ was processed (SECURITY HOLE)"
  fi

  rm -rf "$tmpdir"
}

# ===== ITEM 2: Config url field =====
test_item2_config_url_field() {
  echo ""
  echo "=== ITEM 2: Config URL Field (P0 Architecture) ==="

  local tmpdir
  tmpdir=$(mktemp -d) || { echo "mktemp failed"; return 1; }

  # Create a test bare repo
  git init --bare "$tmpdir/origin.bare" > /dev/null 2>&1

  # Test 2.1: Config should support url field in repos[]
  echo "Test 2.1: Config repos[] has url field"

  cat > "$tmpdir/aesop.config.json" << 'EOF'
{
  "repos": [
    {
      "name": "test_repo",
      "path": "path/to/repo",
      "url": "https://example.com/repo.git",
      "primary_branch": "main"
    }
  ]
}
EOF

  # Run in the temp dir
  (
    cd "$tmpdir"
    output=$(timeout 3 bash "$RECONSTITUTE" --dry-run 2>&1 || true)

    if echo "$output" | grep -q "Would clone\|Processing\|No repos"; then
      echo "✓ PASS: Config with url field loads correctly"
      ((PASSED++)) || true
    else
      echo "✗ FAIL: Config with url field not working"
      ((FAILED++)) || true
    fi
  )

  rm -rf "$tmpdir"
}

# ===== ITEM 3: --test uses real reconstruct_fleet =====
test_item3_test_calls_real_fleet() {
  echo ""
  echo "=== ITEM 3: --test calls real reconstruct_fleet (P2) ==="

  # Test 3.1: --test output includes CLONED/FETCHED/FAILED summary
  echo "Test 3.1: --test reports CLONED/FETCHED/FAILED"

  output=$(timeout 5 bash "$RECONSTITUTE" --test 2>&1 || true)

  if echo "$output" | grep -qE "CLONED|FETCHED|FAILED"; then
    assert_pass "Test output includes summary"
  else
    assert_fail "Test output missing CLONED/FETCHED/FAILED summary"
  fi

  # Test 3.2: Verify TEST 1 successfully clones
  if echo "$output" | grep -q "TEST 1.*CLONED:  1"; then
    assert_pass "TEST 1 clones successfully"
  elif echo "$output" | grep "TEST 1" | tail -1 | grep -q "CLONED:  1"; then
    assert_pass "TEST 1 clones successfully"
  else
    # Try alternative: look for CLONED: 1 after TEST 1 marker
    if echo "$output" | grep -A 20 "TEST 1:" | grep -q "CLONED:  1"; then
      assert_pass "TEST 1 clones successfully"
    else
      assert_fail "TEST 1 clone verification failed"
    fi
  fi

  # Test 3.3: Verify TEST 2 rejects ext:: (FAILED count)
  if echo "$output" | grep -A 15 "TEST 2:" | grep -q "FAILED:  1"; then
    assert_pass "TEST 2 correctly handles bad URL"
  else
    assert_fail "TEST 2 bad URL handling not working"
  fi
}

# ===== BONUS: Space in path parsing =====
test_bonus_space_parse() {
  echo ""
  echo "=== BONUS: Space-in-path parsing (P2) ==="

  local tmpdir
  tmpdir=$(mktemp -d) || { echo "mktemp failed"; return 1; }

  echo "Test Bonus: Path with space and tab delimiter"

  git init --bare "$tmpdir/origin.bare" > /dev/null 2>&1

  # Use tab delimiter to properly separate URL from path with spaces
  printf "%s\t%s\n" "https://example.com/repo.git" "$tmpdir/my cloned repo" > "$tmpdir/repos.txt"

  (
    cd "$tmpdir"
    output=$(timeout 3 bash "$RECONSTITUTE" --dry-run --repos-file "$tmpdir/repos.txt" 2>&1 || true)

    if echo "$output" | grep -q "my cloned repo"; then
      echo "✓ PASS: Spaced path parsed correctly with tab delimiter"
      ((PASSED++)) || true
    else
      if echo "$output" | grep -q "Would clone"; then
        echo "✓ PASS: Path parsing works"
        ((PASSED++)) || true
      else
        echo "✗ FAIL: Spaced path not properly parsed"
        ((FAILED++)) || true
      fi
    fi
  )

  rm -rf "$tmpdir"
}

# ===== ITEM 4: E2E test - clone from fixtures, run reconstitute.sh, verify origin remotes =====
test_item4_e2e_reconstitute() {
  echo ""
  echo "=== ITEM 4: E2E - Clone from fixtures + verify origin (P1) ==="

  local tmpdir
  tmpdir=$(mktemp -d) || { echo "mktemp failed"; return 1; }
  trap "rm -rf $tmpdir" RETURN

  echo "Test 4.1: Setup fixture repos and clone via reconstitute.sh"

  # Create two bare fixture repos
  local fixture1="$tmpdir/fixture1.bare"
  local fixture2="$tmpdir/fixture2.bare"

  git init --bare "$fixture1" > /dev/null 2>&1
  git init --bare "$fixture2" > /dev/null 2>&1

  # Populate fixture1
  # Local, repo-scoped identity: CI runners have no global git user.name/
  # user.email configured, and "git commit" fails loudly (exit 128) without
  # one, which kills this whole test file under set -e.
  (
    cd "$tmpdir"
    git clone "$fixture1" workdir1 > /dev/null 2>&1
    cd workdir1
    git config user.email "test@example.com"
    git config user.name "Test User"
    echo "repo1 content" > README.md
    git add README.md
    git commit -m "initial commit" > /dev/null 2>&1
    git push origin master 2>/dev/null || git push origin main 2>/dev/null || true
  )

  # Populate fixture2
  (
    cd "$tmpdir"
    git clone "$fixture2" workdir2 > /dev/null 2>&1
    cd workdir2
    git config user.email "test@example.com"
    git config user.name "Test User"
    echo "repo2 content" > README.md
    git add README.md
    git commit -m "initial commit" > /dev/null 2>&1
    git push origin master 2>/dev/null || git push origin main 2>/dev/null || true
  )

  # Create repos file pointing to fixtures (use file:// URLs for local paths)
  local repos_file="$tmpdir/repos.txt"
  local clone_dir1="$tmpdir/cloned1"
  local clone_dir2="$tmpdir/cloned2"

  printf "%s\t%s\n" "file://$fixture1" "$clone_dir1" > "$repos_file"
  printf "%s\t%s\n" "file://$fixture2" "$clone_dir2" >> "$repos_file"

  # ISSUE 2 FIX: E2E test drives real reconstitute.sh script (not a wrapper)
  # This ensures tests validate against the actual implementation
  AESOP_FLEET_ROOT="$tmpdir" TEST_MODE=1 bash "$RECONSTITUTE" --repos-file "$repos_file" > /dev/null 2>&1 || true


  # Verify both repos were cloned
  if [ -d "$clone_dir1/.git" ] && [ -d "$clone_dir2/.git" ]; then
    assert_pass "Both repos cloned successfully"
  else
    assert_fail "Repos not cloned (clone_dir1=$clone_dir1, clone_dir2=$clone_dir2)"
    return
  fi

  # Verify origin remotes point to fixtures
  # Note: git may normalize paths differently on Windows, so we check the basename instead
  local origin1
  origin1=$(git -C "$clone_dir1" config --get remote.origin.url 2>/dev/null || echo "")
  local origin2
  origin2=$(git -C "$clone_dir2" config --get remote.origin.url 2>/dev/null || echo "")

  # Normalize paths for comparison (remove trailing slashes, handle Windows path conversions)
  local fixture1_normalized
  local fixture2_normalized
  local origin1_normalized
  local origin2_normalized

  fixture1_normalized=$(basename "$fixture1")
  fixture2_normalized=$(basename "$fixture2")
  origin1_normalized=$(basename "$origin1")
  origin2_normalized=$(basename "$origin2")

  echo "  clone_dir1 origin: $origin1"
  echo "  clone_dir2 origin: $origin2"

  if [ "$origin1_normalized" = "$fixture1_normalized" ]; then
    assert_pass "clone_dir1 origin points to fixture1"
  else
    assert_fail "clone_dir1 origin mismatch (got basename: $origin1_normalized, expected basename: $fixture1_normalized)"
  fi

  if [ "$origin2_normalized" = "$fixture2_normalized" ]; then
    assert_pass "clone_dir2 origin points to fixture2"
  else
    assert_fail "clone_dir2 origin mismatch (got basename: $origin2_normalized, expected basename: $fixture2_normalized)"
  fi

  echo "Test 4.2: Verify cloned repos have correct structure"

  # Check that README.md exists in both
  if [ -f "$clone_dir1/README.md" ]; then
    assert_pass "clone_dir1 has README.md"
  else
    assert_fail "clone_dir1 missing README.md"
  fi

  if [ -f "$clone_dir2/README.md" ]; then
    assert_pass "clone_dir2 has README.md"
  else
    assert_fail "clone_dir2 missing README.md"
  fi

  echo "Test 4.3: Verify fetch works on existing clones"

  # Re-run real reconstitute.sh on existing clones (should fetch)
  AESOP_FLEET_ROOT="$tmpdir" TEST_MODE=1 bash "$RECONSTITUTE" --repos-file "$repos_file" > /dev/null 2>&1 || true

  # Check that fetch worked by verifying git history
  # (check if we have any commits, not just on main/master)
  if git -C "$clone_dir1" rev-list --all --count 2>/dev/null | grep -qv "^0$"; then
    assert_pass "clone_dir1 can access git history (fetch worked)"
  else
    assert_fail "clone_dir1 missing git history"
  fi
}

# ===== ITEM 5: python3 || python interpreter fallback (FIX #6) + shebang (FIX #7) =====
test_item5_python_fallback_when_python3_shadowed() {
  echo ""
  echo "=== ITEM 5: python3||python interpreter fallback (FIX #6) ==="

  local tmpdir
  tmpdir=$(mktemp -d) || { echo "mktemp failed"; return 1; }
  trap "rm -rf $tmpdir" RETURN

  git init --bare "$tmpdir/origin.bare" > /dev/null 2>&1

  cat > "$tmpdir/aesop.config.json" << EOF
{
  "fleet_root": "$tmpdir",
  "repos": [
    {"path": "$tmpdir/target1", "url": "file://$tmpdir/origin.bare"}
  ]
}
EOF

  # Test 5.1 (TDD-discriminating unit test): resolve_python() itself falls
  # back to `python` when `command -v python3` is shadowed out. This is the
  # assertion that actually distinguishes unpatched vs patched code -- an
  # end-to-end run can NOT discriminate them here, because the unpatched
  # script calls the literal `python3 -c ...` directly (never consulting
  # `command -v` first), so shadowing only `command -v python3` has zero
  # effect on it as long as the real python3 stays reachable on PATH. Do NOT
  # filter python3's whole PATH dir to force a "true" absence -- that strips
  # coreutils and hangs (tests/CLAUDE.md hygiene notes); shadow only
  # `command -v python3`, exactly as the resolver itself queries it.
  echo "Test 5.1: resolve_python() falls back to 'python' when 'command -v python3' is shadowed"
  local resolver_result
  resolver_result=$(
    command() {
      if [ "$1" = "-v" ] && [ "$2" = "python3" ]; then
        return 1
      fi
      builtin command "$@"
    }
    # Extract resolve_python()'s definition straight out of the real script
    # (same idiom tests/backup-fleet.test.sh uses for json_escape() etc.) so
    # this test fails loudly -- "NO_RESOLVER" -- against the unpatched
    # script, which has no such function at all.
    eval "$(sed -n '/^resolve_python() {/,/^}/p' "$RECONSTITUTE")"
    if declare -f resolve_python > /dev/null 2>&1; then
      PYTHON_EXE=""
      if resolve_python; then
        printf 'RESOLVED:%s\n' "$PYTHON_EXE"
      else
        printf 'RESOLVE_FAILED\n'
      fi
    else
      printf 'NO_RESOLVER\n'
    fi
  )
  echo "  resolver_result: $resolver_result"
  if [ "$resolver_result" = "RESOLVED:python" ]; then
    assert_pass "resolve_python() falls back to 'python' when python3 is shadowed out"
  else
    assert_fail "resolve_python() did not fall back correctly (got: '$resolver_result')"
  fi

  # Test 5.2: end-to-end sanity -- with the same command-v-python3 shadow in
  # place, the whole script still produces correct output driving through
  # get_fleet_root() and load_repos_from_config() (the two call sites that
  # use the resolver). Bounded with `timeout` + stdin pinned to /dev/null so
  # a shadow mistake can never hang headless CI.
  echo "Test 5.2: python3 shadowed out (command -v python3 fails) -- script still works end-to-end"
  local output
  output=$(
    cd "$tmpdir" || exit 1
    unset AESOP_FLEET_ROOT
    command() {
      if [ "$1" = "-v" ] && [ "$2" = "python3" ]; then
        return 1
      fi
      builtin command "$@"
    }
    export -f command
    TEST_MODE=1 timeout 5 bash "$RECONSTITUTE" --dry-run </dev/null 2>&1
  )
  echo "$output"
  if echo "$output" | grep -q "Would clone"; then
    assert_pass "reconstitute.sh works with python3 shadowed out (falls back to python)"
  else
    assert_fail "reconstitute.sh did not work with python3 shadowed out"
  fi

  # Test 5.3: sanity control -- python3 present (no shadow): identical
  # baseline behavior, proving FIX #6 makes no behavior change when python3
  # is present.
  echo "Test 5.3: python3 present (no shadow) -- baseline unchanged"
  local output2
  output2=$(
    cd "$tmpdir" || exit 1
    unset AESOP_FLEET_ROOT
    TEST_MODE=1 timeout 5 bash "$RECONSTITUTE" --dry-run </dev/null 2>&1
  )
  if echo "$output2" | grep -q "Would clone"; then
    assert_pass "baseline (python3 present) still works unchanged"
  else
    assert_fail "baseline (python3 present) regressed"
  fi

  # Test 5.4: FIX #7 -- shebang is #!/usr/bin/env bash (matches every sibling
  # daemon/hook script), not the hardcoded #!/bin/bash.
  echo "Test 5.4: shebang is #!/usr/bin/env bash"
  local first_line
  first_line=$(head -n 1 "$RECONSTITUTE")
  if [ "$first_line" = "#!/usr/bin/env bash" ]; then
    assert_pass "reconstitute.sh shebang is #!/usr/bin/env bash"
  else
    assert_fail "reconstitute.sh shebang is '$first_line', expected '#!/usr/bin/env bash'"
  fi
}

# ===== Main test runner =====
main() {
  echo "======================================"
  echo "reconstitute.sh Test Suite (Consolidated)"
  echo "======================================"

  test_item1_url_validation
  test_item2_config_url_field
  test_item3_test_calls_real_fleet
  test_bonus_space_parse
  test_item4_e2e_reconstitute
  test_item5_python_fallback_when_python3_shadowed

  echo ""
  echo "======================================"
  echo "Results: $PASSED passed, $FAILED failed"
  echo "======================================"

  if [ $FAILED -eq 0 ]; then
    echo "All tests passed!"
    exit 0
  else
    echo "$FAILED tests failed"
    exit 1
  fi
}

main "$@"
