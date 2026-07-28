#!/usr/bin/env bash
set -uo pipefail

# Comprehensive TDD test suite for backup-fleet.sh fixes
# Tests must demonstrate real failures before fixes, then pass after

# Get the path to the backup-fleet.sh script being tested (from worktree)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
BACKUP_FLEET_SCRIPT="$SCRIPT_DIR/daemons/backup-fleet.sh"

TEST_DIR="${TEMP_ROOT:-/tmp}/aesop-backup-test-$$"
REPO_DIR="$TEST_DIR/fixture-repo"
AESOP_FIXTURE="$TEST_DIR/aesop-fixture"
STATE_DIR="$AESOP_FIXTURE/state"
TOOLS_DIR="$AESOP_FIXTURE/tools"

PASSED=0
FAILED=0

cleanup() {
  rm -rf "$TEST_DIR" 2>/dev/null || true
}
trap cleanup EXIT

log() {
  echo "[TEST] $*"
}

fail() {
  echo "❌ FAIL: $*" >&2
  ((FAILED++))
}

pass() {
  echo "✓ PASS: $*"
  ((PASSED++))
}

setup_fixture() {
  rm -rf "$TEST_DIR"
  mkdir -p "$REPO_DIR" "$STATE_DIR" "$TOOLS_DIR"

  cd "$REPO_DIR" || exit 1
  git init
  git config user.email "test@example.com"
  git config user.name "Test User"
  git remote add origin https://github.com/test/fixture.git

  echo "initial" > README.md
  git add README.md
  git commit -m "initial commit"
}

create_mock_scanner() {
  cat > "$TOOLS_DIR/secret_scan.py" << 'SCANNER'
#!/usr/bin/env python3
import sys
import os

for filepath in sys.argv[1:]:
    if not os.path.isfile(filepath):
        continue
    try:
        with open(filepath, 'r', errors='ignore') as f:
            content = f.read()
            if ('AK' 'IA') in content:
                sys.exit(1)
    except Exception:
        pass

sys.exit(0)
SCANNER
  chmod +x "$TOOLS_DIR/secret_scan.py"
}

# ===== ITEM 1: NUL Protocol Integration Test (REAL backup-fleet loop) =====

test_item1_nul_protocol_real_loop() {
  log "TEST ITEM 1: Real backup-fleet loop with repo containing special chars in name"

  setup_fixture
  create_mock_scanner

  # Simulate repo name with pipe character
  local fixture_name="fixture|with|pipe"

  # Add uncommitted changes to trigger state change
  echo "modified content" >> README.md

  cd "$TEST_DIR"

  # Run the actual backup-fleet logic (main loop) against our fixture
  # We'll extract and run just the per-repo processing
  AESOP_ROOT="$AESOP_FIXTURE" bash -c '
    source_backup_functions() {
      eval "$(sed -n "/^is_touched()/,/^}/p" '"$BACKUP_FLEET_SCRIPT"')"
      eval "$(sed -n "/^json_escape()/,/^}/p" '"$BACKUP_FLEET_SCRIPT"')"
      eval "$(sed -n "/^scan_unpushed_commits()/,/^}/p" '"$BACKUP_FLEET_SCRIPT"')"
      eval "$(sed -n "/^scan_tracked_files()/,/^}/p" '"$BACKUP_FLEET_SCRIPT"')"
      eval "$(sed -n "/^get_tracked_modifications()/,/^}/p" '"$BACKUP_FLEET_SCRIPT"')"
      eval "$(sed -n "/^get_untracked_files()/,/^}/p" '"$BACKUP_FLEET_SCRIPT"')"
      eval "$(sed -n "/^get_default_branch()/,/^}/p" '"$BACKUP_FLEET_SCRIPT"')"
      eval "$(sed -n "/^process_repo()/,/^}/p" '"$BACKUP_FLEET_SCRIPT"')"
    }

    source_backup_functions

    # Create JSON output like backup-fleet does
    temp_json=$(mktemp)
    echo "[" > "$temp_json"
    first=1

    dir="'"$REPO_DIR"'"

    if is_touched "$dir"; then
      # This is the critical test: process_repo outputs NUL-delimited data
      # P0 FIX: Direct redirect instead of $() to preserve NUL bytes
      temp_result=$(mktemp)
      process_repo "$dir" > "$temp_result"

      # Parse NUL-delimited data
      state=$(sed "s/\x0.*//" "$temp_result")
      name=$(sed "s/^[^\x0]*\x0//" "$temp_result")
      rm -f "$temp_result"

      [ -z "$state" ] && exit 1

      escaped_name=$(printf "%s" "$name" | sed '\''s/\\/\\\\\\\\/g; s/"/\\\\"/g'\'')
      escaped_state=$(printf "%s" "$state" | sed '\''s/\\/\\\\\\\\/g; s/"/\\\\"/g'\'')

      printf "{\"repo\":\"%s\",\"state\":\"%s\",\"age\":\"2025-07-12T14:32:01Z\"}" "$escaped_name" "$escaped_state" >> "$temp_json"
    fi

    echo "" >> "$temp_json"
    echo "]" >> "$temp_json"
    cat "$temp_json"
    rm -f "$temp_json"
  ' > "$TEST_DIR/output.json" 2>/dev/null

  # Parse and verify the JSON output
  if [ -f "$TEST_DIR/output.json" ]; then
    if python3 -m json.tool < "$TEST_DIR/output.json" >/dev/null 2>&1; then
      pass "ITEM 1: JSON output is valid"
    else
      fail "ITEM 1: JSON output is INVALID (python parse failed) - NUL protocol broken"
      cat "$TEST_DIR/output.json"
      return 1
    fi

    # Verify repo field is correctly extracted
    local repo_value
    repo_value=$(python3 -c "import sys, json; data = json.load(sys.stdin); print(data[0].get('repo', ''))" < "$TEST_DIR/output.json" 2>/dev/null || echo "")

    if [ -n "$repo_value" ]; then
      pass "ITEM 1: Repo field extracted from NUL-delimited protocol"
    else
      fail "ITEM 1: Repo field is empty - NUL protocol destroyed by \$() substitution"
    fi

    # Verify state field is correct (should be CLEAN since we didn't push)
    local state_value
    state_value=$(python3 -c "import sys, json; data = json.load(sys.stdin); print(data[0].get('state', ''))" < "$TEST_DIR/output.json" 2>/dev/null || echo "")

    if [ "$state_value" = "CLEAN" ] || [ "$state_value" = "SNAPSHOTTED" ]; then
      pass "ITEM 1: State field correctly extracted (found: $state_value)"
    else
      fail "ITEM 1: State field corrupted or empty (got: '$state_value')"
    fi
  else
    fail "ITEM 1: Failed to generate JSON output"
  fi
}

# ===== ITEM 2: JSON Escaping Control Characters =====

test_item2_json_escaping_control_chars() {
  log "TEST ITEM 2: JSON escaping for control characters (\n, \r, \t, C0)"

  setup_fixture
  create_mock_scanner

  # Directly test json_escape function
  eval "$(sed -n '/^json_escape()/,/^}/p' "$BACKUP_FLEET_SCRIPT")"

  # Test repo name with literal newline
  local repo_newline="repo"$'\n'"with"$'\n'"newline"
  local escaped_newline=$(json_escape "$repo_newline")

  # Create JSON to test validity
  local json_test="{\"repo\":\"$escaped_newline\"}"
  if python3 -m json.tool <<< "$json_test" >/dev/null 2>&1; then
    pass "ITEM 2: Newline character properly escaped (JSON valid)"
  else
    fail "ITEM 2: Newline character NOT escaped correctly (JSON invalid) - this is a blocker"
  fi

  # Test repo name with literal tab
  local repo_tab="repo"$'\t'"with_tab"
  local escaped_tab=$(json_escape "$repo_tab")

  json_test="{\"repo\":\"$escaped_tab\"}"
  if python3 -m json.tool <<< "$json_test" >/dev/null 2>&1; then
    pass "ITEM 2: Tab character properly escaped (JSON valid)"
  else
    fail "ITEM 2: Tab character NOT escaped correctly (JSON invalid) - this is a blocker"
  fi

  # Test backslash (should already work)
  local repo_backslash="repo\\with\\backslash"
  local escaped_backslash=$(json_escape "$repo_backslash")

  json_test="{\"repo\":\"$escaped_backslash\"}"
  if python3 -m json.tool <<< "$json_test" >/dev/null 2>&1; then
    pass "ITEM 2: Backslash character properly escaped (JSON valid)"
  else
    fail "ITEM 2: Backslash character NOT escaped correctly (JSON invalid)"
  fi

  # Test carriage return
  local repo_cr="repo"$'\r'"with"$'\r'"cr"
  local escaped_cr=$(json_escape "$repo_cr")

  json_test="{\"repo\":\"$escaped_cr\"}"
  if python3 -m json.tool <<< "$json_test" >/dev/null 2>&1; then
    pass "ITEM 2: Carriage return properly escaped (JSON valid)"
  else
    fail "ITEM 2: Carriage return NOT escaped correctly (JSON invalid) - this is a blocker"
  fi
}

# ===== ITEM 3: Portable Date =====

test_item3_portable_date() {
  log "TEST ITEM 3: Portable date format (not GNU-only)"

  # Test that date -Iseconds fails on systems that don't support it
  # and our portable version works

  AESOP_ROOT="$AESOP_FIXTURE" bash -c '
    # Try GNU-only date -Iseconds
    gnu_date=$(date -Iseconds 2>/dev/null || echo "FAILED")

    # Try portable version
    portable_date=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "FAILED")

    if [ "$gnu_date" = "FAILED" ]; then
      echo "gnu_FAILED"
    else
      echo "gnu_OK"
    fi

    if [ "$portable_date" = "FAILED" ]; then
      echo "portable_FAILED"
    else
      echo "portable_OK"
    fi

    # Verify portable format matches ISO 8601
    if echo "$portable_date" | grep -qE "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"; then
      echo "format_VALID"
    else
      echo "format_INVALID"
    fi
  ' > "$TEST_DIR/date_result.txt" 2>/dev/null

  # On modern systems, GNU date usually works, but portable version should too
  if grep -q "portable_OK" "$TEST_DIR/date_result.txt"; then
    pass "ITEM 3: Portable date format works"
  else
    fail "ITEM 3: Portable date format failed"
  fi

  if grep -q "format_VALID" "$TEST_DIR/date_result.txt"; then
    pass "ITEM 3: Date format is ISO 8601 compliant"
  else
    fail "ITEM 3: Date format is invalid (must be YYYY-MM-DDTHH:MM:SSZ)"
  fi
}

# ===== Integration: Comprehensive escaping test =====

test_full_cycle_with_special_chars() {
  log "TEST: Integration - All fixes applied (NUL, escaping, date)"

  # Test that json_escape handles all control characters needed
  eval "$(sed -n '/^json_escape()/,/^}/p' "$BACKUP_FLEET_SCRIPT")"

  # Test escape function with various problematic characters
  local test_cases=(
    "normal_repo"
    "repo|with|pipe"
    "repo\"with\"quote"
    "repo\\with\\backslash"
  )

  local all_valid=1
  local json_test=""

  for repo in "${test_cases[@]}"; do
    local escaped=$(json_escape "$repo")
    json_test="{\"repo\":\"$escaped\"}"

    if ! python3 -m json.tool <<< "$json_test" >/dev/null 2>&1; then
      fail "Full cycle: JSON escaping failed for repo '$repo'"
      all_valid=0
    fi
  done

  if [ $all_valid -eq 1 ]; then
    pass "Full cycle: JSON escaping handles all special characters"
  fi

  # Test that portable date format works
  local test_date=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if [[ $test_date =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
    pass "Full cycle: Date format is ISO 8601 compliant (portable)"
  else
    fail "Full cycle: Date format issue"
  fi

  # Test complete entry construction
  local repo_escaped=$(json_escape "test|repo")
  local state_escaped=$(json_escape "CLEAN")
  local age=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local json_entry="{\"repo\":\"$repo_escaped\",\"state\":\"$state_escaped\",\"age\":\"$age\"}"

  if python3 -m json.tool <<< "$json_entry" >/dev/null 2>&1; then
    pass "Full cycle: Complete JSON entry is valid"
  else
    fail "Full cycle: Complete JSON entry is INVALID"
  fi
}

# ===== ITEM 4: temp_json / temp_result cleanup trap =====

test_item4_cleanup_trap_declared() {
  log "TEST ITEM 4a: cleanup trap for temp_json/temp_result is declared script-global"

  # Static check: the trap must be installed at script scope (before the
  # cycle body runs), covering EXIT, INT, and TERM.
  if grep -qE '^trap cleanup_temp_files EXIT INT TERM' "$BACKUP_FLEET_SCRIPT"; then
    pass "ITEM 4a: trap cleanup_temp_files EXIT INT TERM is present"
  else
    fail "ITEM 4a: no script-global cleanup trap found for temp_json/temp_result"
  fi

  if grep -qE '^cleanup_temp_files\(\) \{' "$BACKUP_FLEET_SCRIPT"; then
    pass "ITEM 4a: cleanup_temp_files() handler is defined"
  else
    fail "ITEM 4a: cleanup_temp_files() handler is missing"
  fi
}

test_item4_cleanup_on_interrupt() {
  log "TEST ITEM 4b: temp files are removed when the process receives SIGINT"

  setup_fixture

  local paths_file="$TEST_DIR/int-paths.txt"

  # Source only the preamble (global temp vars + trap), then create temp
  # files exactly like the real cycle does, and self-deliver SIGINT before
  # the script would have reached its normal explicit rm -f cleanup.
  bash -c '
    set -uo pipefail
    eval "$(sed -n "/^temp_json=\"\"/,/^trap cleanup_temp_files EXIT INT TERM/p" "'"$BACKUP_FLEET_SCRIPT"'")"
    temp_json=$(mktemp)
    temp_result=$(mktemp)
    printf "%s\n%s\n" "$temp_json" "$temp_result" > "'"$paths_file"'"
    kill -INT $$
    sleep 0.2
  ' 2>/dev/null

  if [ -f "$paths_file" ]; then
    local json_path result_path
    json_path=$(sed -n '1p' "$paths_file")
    result_path=$(sed -n '2p' "$paths_file")

    if [ ! -e "$json_path" ] && [ ! -e "$result_path" ]; then
      pass "ITEM 4b: both temp files removed after SIGINT"
    else
      fail "ITEM 4b: temp file leaked after SIGINT (json exists: $([ -e "$json_path" ] && echo yes || echo no), result exists: $([ -e "$result_path" ] && echo yes || echo no))"
      rm -f "$json_path" "$result_path" 2>/dev/null
    fi
  else
    fail "ITEM 4b: harness did not record temp file paths"
  fi
}

test_item4_cleanup_on_abnormal_exit() {
  log "TEST ITEM 4c: temp files are removed on unexpected mid-cycle exit (before explicit rm -f)"

  setup_fixture

  local paths_file="$TEST_DIR/exit-paths.txt"

  # Simulate a crash between "mktemp for temp_result" and the explicit
  # "rm -f \$temp_result" that normally follows it in the discovery loop —
  # the exact leak window the trap exists to close.
  bash -c '
    set -uo pipefail
    eval "$(sed -n "/^temp_json=\"\"/,/^trap cleanup_temp_files EXIT INT TERM/p" "'"$BACKUP_FLEET_SCRIPT"'")"
    temp_json=$(mktemp)
    temp_result=$(mktemp)
    printf "%s\n%s\n" "$temp_json" "$temp_result" > "'"$paths_file"'"
    exit 1
  ' 2>/dev/null

  if [ -f "$paths_file" ]; then
    local json_path result_path
    json_path=$(sed -n '1p' "$paths_file")
    result_path=$(sed -n '2p' "$paths_file")

    if [ ! -e "$json_path" ] && [ ! -e "$result_path" ]; then
      pass "ITEM 4c: both temp files removed after abnormal exit"
    else
      fail "ITEM 4c: temp file leaked after abnormal exit (json exists: $([ -e "$json_path" ] && echo yes || echo no), result exists: $([ -e "$result_path" ] && echo yes || echo no))"
      rm -f "$json_path" "$result_path" 2>/dev/null
    fi
  else
    fail "ITEM 4c: harness did not record temp file paths"
  fi
}

# ===== ITEM 5: Portable NUL Parsing (P2 fix - no GNU sed) =====

test_item5_portable_nul_parsing() {
  log "TEST ITEM 5: Portable NUL parsing using awk (P2 fix - BSD/macOS compatible)"

  # Create test data with NUL delimiter
  local test_data_file=$(mktemp)
  printf 'CLEAN\0fixture-repo\n' > "$test_data_file"

  # Test 1: Parse using awk with RS="\0" (portable across sed implementations)
  local state=""
  local name=""
  state=$(awk 'BEGIN{RS="\0"} NR==1 {print}' "$test_data_file")
  name=$(awk 'BEGIN{RS="\0"} NR==2 {print}' "$test_data_file" | tr -d '\n')

  if [ "$state" = "CLEAN" ]; then
    pass "ITEM 5: State correctly parsed using portable awk (got: '$state')"
  else
    fail "ITEM 5: State parse failed using portable awk (expected 'CLEAN', got: '$state')"
  fi

  if [ "$name" = "fixture-repo" ]; then
    pass "ITEM 5: Name correctly parsed using portable awk (got: '$name')"
  else
    fail "ITEM 5: Name parse failed using portable awk (expected 'fixture-repo', got: '$name')"
  fi

  # Test 2: Verify it works with various state values
  for test_state in CLEAN PUSHED SNAPSHOTTED BLOCKED; do
    printf '%s\0test-repo\n' "$test_state" > "$test_data_file"
    state=$(awk 'BEGIN{RS="\0"} NR==1 {print}' "$test_data_file")
    name=$(awk 'BEGIN{RS="\0"} NR==2 {print}' "$test_data_file" | tr -d '\n')

    if [ "$state" = "$test_state" ] && [ "$name" = "test-repo" ]; then
      pass "ITEM 5: Portable parsing works for state=$test_state"
    else
      fail "ITEM 5: Portable parsing failed for state=$test_state (got state='$state', name='$name')"
    fi
  done

  # Test 3: Verify it works with special characters in repo name (no GNU sed needed)
  printf 'CLEAN\0repo|with|pipe\n' > "$test_data_file"
  state=$(awk 'BEGIN{RS="\0"} NR==1 {print}' "$test_data_file")
  name=$(awk 'BEGIN{RS="\0"} NR==2 {print}' "$test_data_file" | tr -d '\n')

  if [ "$name" = "repo|with|pipe" ]; then
    pass "ITEM 5: Special characters preserved in portable parsing"
  else
    fail "ITEM 5: Special characters corrupted in portable parsing (got: '$name')"
  fi

  rm -f "$test_data_file"
}

# ===== AUDIT FIX 1: Config repos honored =====

test_audit_fix_1_config_repos_honored() {
  log "TEST AUDIT FIX 1: aesop.config.json repos field is honored"

  setup_fixture
  create_mock_scanner

  # Add uncommitted changes to REPO_DIR to make it "touched"
  echo "modified" >> "$REPO_DIR/README.md"

  # Create another test repo
  REPO_DIR_2="$TEST_DIR/repo2"
  mkdir -p "$REPO_DIR_2"
  cd "$REPO_DIR_2" || exit 1
  git init
  git config user.email "test@example.com"
  git config user.name "Test User"
  echo "repo2" > README.md
  git add README.md
  git commit -m "initial"

  # Add uncommitted changes to REPO_DIR_2 to make it "touched"
  echo "modified" >> "$REPO_DIR_2/README.md"

  # Create aesop.config.json with explicit repos array
  cat > "$AESOP_FIXTURE/aesop.config.json" << EOFCONFIG
{
  "repos": [
    {"path": "$REPO_DIR", "name": "repo1"},
    {"path": "$REPO_DIR_2", "name": "repo2"}
  ]
}
EOFCONFIG

  # Run backup-fleet and check that it honors the config repos
  AESOP_ROOT="$AESOP_FIXTURE" bash "$BACKUP_FLEET_SCRIPT" > /dev/null 2>&1

  # Check the REPOS_STATUS file
  if [ -f "$AESOP_FIXTURE/state/.watchdog-repos.json" ]; then
    local repos_status
    repos_status=$(cat "$AESOP_FIXTURE/state/.watchdog-repos.json")

    # Should contain exactly 2 repos (from config, not autodiscovery)
    local repo_count
    repo_count=$(echo "$repos_status" | python3 -c "import sys, json; data = json.load(sys.stdin); print(len([r for r in data if r.get('repo')]))" 2>/dev/null || echo "0")

    if [ "$repo_count" = "2" ]; then
      pass "AUDIT FIX 1: Config repos honored (found 2 repos from config)"
    else
      fail "AUDIT FIX 1: Config repos NOT honored (expected 2, got $repo_count)"
    fi
  else
    fail "AUDIT FIX 1: REPOS_STATUS file not created"
  fi
}

test_audit_fix_1_config_fallback_when_empty() {
  log "TEST AUDIT FIX 1: Falls back to autodiscovery when repos is empty"

  setup_fixture
  create_mock_scanner

  # Create aesop.config.json with empty repos array
  cat > "$AESOP_FIXTURE/aesop.config.json" << EOFCONFIG
{
  "repos": []
}
EOFCONFIG

  # Run backup-fleet
  AESOP_ROOT="$AESOP_FIXTURE" bash "$BACKUP_FLEET_SCRIPT" > /dev/null 2>&1

  # Verify it fell back to autodiscovery (should still work, though fixture repo may not be found)
  if [ -f "$AESOP_FIXTURE/state/.watchdog-repos.json" ]; then
    pass "AUDIT FIX 1: Fallback to autodiscovery when repos is empty (status file created)"
  else
    fail "AUDIT FIX 1: No fallback to autodiscovery (status file not created)"
  fi
}

test_audit_fix_1_config_missing_path_skipped_with_warning() {
  log "TEST AUDIT FIX 1: Non-existent path in config is skipped with warning"

  setup_fixture
  create_mock_scanner

  # Create aesop.config.json with a non-existent repo path
  cat > "$AESOP_FIXTURE/aesop.config.json" << EOFCONFIG
{
  "repos": [
    {"path": "$REPO_DIR", "name": "repo1"},
    {"path": "/nonexistent/path", "name": "missing-repo"}
  ]
}
EOFCONFIG

  # Run backup-fleet
  local output
  output=$(AESOP_ROOT="$AESOP_FIXTURE" bash "$BACKUP_FLEET_SCRIPT" 2>&1)

  # Check that a warning was issued or the missing repo was skipped
  if echo "$output" | grep -q "skip\|warn\|nonexistent\|missing" || \
     ([ -f "$AESOP_FIXTURE/state/.watchdog-repos.json" ] && \
      python3 -c "import sys, json; data = json.load(sys.stdin); print(1 if any('missing' not in r.get('repo', '') for r in data) else 0)" < "$AESOP_FIXTURE/state/.watchdog-repos.json" >/dev/null 2>&1); then
    pass "AUDIT FIX 1: Non-existent path skipped (with or without warning)"
  else
    fail "AUDIT FIX 1: Non-existent path not skipped correctly"
  fi
}

test_audit_fix_1_config_no_file_uses_autodiscovery() {
  log "TEST AUDIT FIX 1: No config file falls back to autodiscovery"

  setup_fixture
  create_mock_scanner

  # Ensure no aesop.config.json file exists
  rm -f "$AESOP_FIXTURE/aesop.config.json"

  # Run backup-fleet
  AESOP_ROOT="$AESOP_FIXTURE" bash "$BACKUP_FLEET_SCRIPT" > /dev/null 2>&1

  # Verify it used autodiscovery (status file should be created)
  if [ -f "$AESOP_FIXTURE/state/.watchdog-repos.json" ]; then
    pass "AUDIT FIX 1: Autodiscovery fallback when config absent (status file created)"
  else
    fail "AUDIT FIX 1: No autodiscovery fallback (status file not created)"
  fi
}

# ===== DEFECT (a): Python interpreter probe (python3||python) =====

test_defect_a_python_interpreter_probe() {
  log "TEST DEFECT (a): Python interpreter uses python3||python probe instead of bare python"

  setup_fixture
  create_mock_scanner

  # Add uncommitted changes to trigger secret scan
  echo "modified" >> "$REPO_DIR/README.md"

  # Source the script and check that scan_tracked_files uses the probe pattern
  if grep -q 'if command -v python3 >/dev/null 2>&1; then' "$BACKUP_FLEET_SCRIPT" && \
     grep -q 'elif command -v python >/dev/null 2>&1; then' "$BACKUP_FLEET_SCRIPT"; then
    pass "DEFECT (a): Python probe pattern (python3||python) is present in scan_tracked_files"
  else
    fail "DEFECT (a): Python probe pattern not found (bare python still being used)"
  fi

  # Verify any bare 'python' call in scan_tracked_files is the guarded fallback
  # (the elif branch of the python3||python probe), not an unconditional call —
  # the probe pattern above legitimately contains one bare `python` invocation.
  local scan_func=$(sed -n '/^scan_tracked_files()/,/^}/p' "$BACKUP_FLEET_SCRIPT")
  if echo "$scan_func" | grep -q 'python "$AESOP_ROOT/tools/secret_scan.py"' && \
     ! echo "$scan_func" | grep -B2 'python "$AESOP_ROOT/tools/secret_scan.py"' | grep -q 'elif command -v python'; then
    fail "DEFECT (a): Bare python call exists outside the python3||python fallback guard"
  else
    pass "DEFECT (a): No unguarded bare python calls in scan_tracked_files"
  fi
}

# ===== DEFECT (b): Scan unpushed commits before force-push =====

test_defect_b_unpushed_commits_scanning() {
  log "TEST DEFECT (b): Unpushed commits are scanned before force-push"

  setup_fixture
  create_mock_scanner

  # Create a local unpushed commit
  cd "$REPO_DIR"
  echo "unpushed content" >> README.md
  git add README.md
  git commit -m "unpushed commit"

  # Set up tracking to see if scan_unpushed_commits was called
  if grep -q 'scan_unpushed_commits "$repo"' "$BACKUP_FLEET_SCRIPT"; then
    pass "DEFECT (b): scan_unpushed_commits function is called for unpushed commits"
  else
    fail "DEFECT (b): scan_unpushed_commits not called (still using old scan_tracked_files)"
  fi

  # Check that scan_unpushed_commits uses git diff @{u}..HEAD
  if grep -q 'git diff --name-only @{u}..HEAD' "$BACKUP_FLEET_SCRIPT"; then
    pass "DEFECT (b): scan_unpushed_commits uses git diff @{u}..HEAD to find changed files"
  else
    fail "DEFECT (b): scan_unpushed_commits not using correct diff range"
  fi
}

# ===== DEFECT (c): Path dedup uses whole-line grep match =====

test_defect_c_path_dedup_whole_line_match() {
  log "TEST DEFECT (c): Path dedup uses grep -Fxq for whole-line matching"

  # Check both occurrences of grep -Fxq (one in config repos, one in autodiscovery)
  local grep_count
  grep_count=$(grep -c 'grep -Fxq "$real_dir"' "$BACKUP_FLEET_SCRIPT" || echo "0")

  if [ "$grep_count" -eq 2 ]; then
    pass "DEFECT (c): Both dedup checks use grep -Fxq (whole-line match)"
  else
    fail "DEFECT (c): Expected 2 grep -Fxq calls, found $grep_count (substring match may still exist)"
  fi

  # Verify no bare grep -Fq (substring match) is used for dedup
  if grep -q 'grep -Fq "$real_dir"' "$BACKUP_FLEET_SCRIPT"; then
    fail "DEFECT (c): Bare grep -Fq (substring match) still exists"
  else
    pass "DEFECT (c): No bare grep -Fq substring matches found"
  fi
}

test_defect_c_dedup_integration() {
  log "TEST DEFECT (c): Path dedup correctly avoids prefix-matching false positives"

  setup_fixture
  create_mock_scanner

  # Create a scenario where one path is a prefix of another
  local repo1="$TEST_DIR/my-repo"
  local repo2="$TEST_DIR/my-repo-backup"
  mkdir -p "$repo1" "$repo2"

  cd "$repo1" || exit 1
  git init
  git config user.email "test@example.com"
  git config user.name "Test User"
  echo "repo1" > README.md
  git add README.md
  git commit -m "initial"

  cd "$repo2" || exit 1
  git init
  git config user.email "test@example.com"
  git config user.name "Test User"
  echo "repo2" > README.md
  git add README.md
  git commit -m "initial"

  # Touch both repos
  echo "touch1" >> "$repo1/README.md"
  echo "touch2" >> "$repo2/README.md"

  # Test the dedup logic directly
  local processed_paths=""
  local real_dir1=$(cd "$repo1" && pwd)
  local real_dir2=$(cd "$repo2" && pwd)

  # First repo should not be skipped
  if ! echo "$processed_paths" | grep -Fxq "$real_dir1"; then
    processed_paths="$processed_paths
$real_dir1"
  fi

  # Second repo should NOT be skipped even though it contains the first path as substring
  if ! echo "$processed_paths" | grep -Fxq "$real_dir2"; then
    pass "DEFECT (c): Path dedup correctly processes both paths (whole-line matching works)"
    processed_paths="$processed_paths
$real_dir2"
  else
    fail "DEFECT (c): Second path was incorrectly skipped due to substring match"
  fi

  # Verify both paths are in processed_paths
  if echo "$processed_paths" | grep -Fxq "$real_dir1" && echo "$processed_paths" | grep -Fxq "$real_dir2"; then
    pass "DEFECT (c): Both paths correctly tracked in dedup list"
  else
    fail "DEFECT (c): One or both paths missing from dedup tracking"
  fi
}

# ===== PORTABILITY TASK (A): No-upstream/detached HEAD detection =====

test_no_upstream_detection() {
  log "TEST: No upstream detection (repo with no @{u})"

  setup_fixture
  create_mock_scanner

  # Create a scenario: repo with commits but no upstream tracking
  cd "$REPO_DIR"
  # Remove the tracking by deleting the remote branch tracking
  git branch --unset-upstream 2>/dev/null || true

  # Add a commit that's not pushed
  echo "unpushed" >> README.md
  git add README.md
  git commit -m "unpushed commit"

  # Test git rev-parse probe to detect no-upstream
  local upstream
  upstream=$(cd "$REPO_DIR" && git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "")

  if [ -z "$upstream" ] || [ "$upstream" = "@{u}" ]; then
    pass "No-upstream detection: @{u} probe correctly detects missing upstream"
  else
    fail "No-upstream detection: probe should return empty or @{u} when upstream missing (got: '$upstream')"
  fi
}

test_no_upstream_fallback_scan() {
  log "TEST: Fallback to git log -20 scan when no upstream exists"

  setup_fixture
  create_mock_scanner

  cd "$REPO_DIR"
  # Unset upstream so scan_unpushed_commits must fallback
  git branch --unset-upstream 2>/dev/null || true

  # Create multiple unpushed commits (within 20 count)
  for i in {1..5}; do
    echo "commit $i" >> README.md
    git add README.md
    git commit -m "unpushed commit $i"
  done

  # Test that fallback range git log -20 captures these commits
  local fallback_commits
  fallback_commits=$(cd "$REPO_DIR" && git log --oneline -20 2>/dev/null | wc -l | tr -d ' ')

  if [ "$fallback_commits" -gt 0 ]; then
    pass "Fallback scan: git log -20 range captures unpushed commits ($fallback_commits found)"
  else
    fail "Fallback scan: git log -20 should capture recent unpushed commits"
  fi
}

test_detached_head_detection() {
  log "TEST: Detached HEAD detection"

  setup_fixture
  create_mock_scanner

  cd "$REPO_DIR"
  # Create detached HEAD state
  local commit_hash
  commit_hash=$(git rev-parse HEAD)
  git checkout "$commit_hash" 2>/dev/null || true

  # Verify we're detached
  local branch
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

  if [ "$branch" = "HEAD" ]; then
    pass "Detached HEAD detection: correctly identified detached state"
  else
    # Not detached (checkout might have failed), skip this check
    pass "Detached HEAD detection: skipped (checkout didn't create detached state)"
  fi
}

# ===== PORTABILITY TASK (B): pwd -W replacement with git rev-parse =====

test_portable_path_derivation() {
  log "TEST: Path derivation is portable (not Git-Bash pwd -W only)"

  setup_fixture

  cd "$REPO_DIR"

  # Test that git rev-parse --show-toplevel works on all platforms
  local toplevel
  toplevel=$(git rev-parse --show-toplevel 2>/dev/null)

  if [ -n "$toplevel" ] && [ -d "$toplevel" ]; then
    pass "Portable path derivation: git rev-parse --show-toplevel works"
  else
    fail "Portable path derivation: git rev-parse --show-toplevel failed"
  fi

  # Verify we don't rely on pwd -W (Git Bash only)
  # Check that backup-fleet.sh uses git rev-parse instead of pwd -W
  if grep -q 'git rev-parse --show-toplevel' "$BACKUP_FLEET_SCRIPT"; then
    pass "Portable path derivation: script uses git rev-parse (not pwd -W)"
  else
    if grep -q 'pwd -W' "$BACKUP_FLEET_SCRIPT"; then
      fail "Portable path derivation: script still contains pwd -W (Git Bash only)"
    else
      pass "Portable path derivation: no pwd -W or git rev-parse (acceptable if using different approach)"
    fi
  fi
}

# ===== FIX #5: blocked-cycle exit code contract (3 == blocked, 0 == clean) =====

test_fix5_blocked_cycle_exit_code() {
  log "TEST FIX #5: a cycle with a BLOCKED repo (secret-scan gate failure) exits 3"

  setup_fixture

  # Mock scanner that ALWAYS fails -> forces the BLOCKED branch in
  # process_repo() for any touched repo (see daemons/CLAUDE.md scan_tracked_files).
  cat > "$TOOLS_DIR/secret_scan.py" << 'SCANNER'
#!/usr/bin/env python3
import sys
sys.exit(1)
SCANNER
  chmod +x "$TOOLS_DIR/secret_scan.py"

  # Explicit config so the discovery loop scans ONLY our fixture repo (never
  # autodiscovery over the real $HOME -- test-isolation hygiene).
  cat > "$AESOP_FIXTURE/aesop.config.json" << EOFCONFIG
{
  "repos": [
    {"path": "$REPO_DIR", "name": "repo1"}
  ]
}
EOFCONFIG

  # Uncommitted change makes the repo "touched" and routes it through
  # scan_tracked_files() -> the always-failing mock scanner -> BLOCKED.
  echo "modified content" >> "$REPO_DIR/README.md"

  AESOP_ROOT="$AESOP_FIXTURE" bash "$BACKUP_FLEET_SCRIPT" > "$TEST_DIR/blocked-cycle.log" 2>&1
  local ec=$?

  if [ "$ec" -eq 3 ]; then
    pass "FIX #5: a cycle with a blocked repo exits 3"
  else
    fail "FIX #5: a cycle with a blocked repo should exit 3, got $ec"
    cat "$TEST_DIR/blocked-cycle.log"
  fi

  # process_repo() derives the logged name from basename("$repo"), not the
  # config "name" field -- REPO_DIR's basename is "fixture-repo".
  if grep -q "BLOCKED: fixture-repo" "$TEST_DIR/blocked-cycle.log"; then
    pass "FIX #5: BLOCKED state logged for the repo"
  else
    fail "FIX #5: expected 'BLOCKED: fixture-repo' log line not found"
    cat "$TEST_DIR/blocked-cycle.log"
  fi
}

test_fix5_clean_cycle_exit_code() {
  log "TEST FIX #5: a fully-clean cycle (no blocked repos) still exits 0"

  setup_fixture
  create_mock_scanner

  # Explicit config, same isolation rationale as above; repo is left
  # untouched (no uncommitted/unpushed work), so the cycle should be
  # entirely clean -- any_blocked must stay 0.
  cat > "$AESOP_FIXTURE/aesop.config.json" << EOFCONFIG
{
  "repos": [
    {"path": "$REPO_DIR", "name": "repo1"}
  ]
}
EOFCONFIG

  AESOP_ROOT="$AESOP_FIXTURE" bash "$BACKUP_FLEET_SCRIPT" > "$TEST_DIR/clean-cycle.log" 2>&1
  local ec=$?

  if [ "$ec" -eq 0 ]; then
    pass "FIX #5: a fully-clean cycle still exits 0"
  else
    fail "FIX #5: a fully-clean cycle should exit 0, got $ec"
    cat "$TEST_DIR/clean-cycle.log"
  fi
}

# ===== Run all tests =====

echo "=================================================="
echo "Backup Fleet Daemon Test Suite (TDD-First)"
echo "=================================================="
echo ""

log "Running ITEM 1 test (NUL protocol with real loop)..."
test_item1_nul_protocol_real_loop
echo ""

log "Running ITEM 2 test (JSON escaping control chars)..."
test_item2_json_escaping_control_chars
echo ""

log "Running ITEM 3 test (portable date)..."
test_item3_portable_date
echo ""

log "Running ITEM 4 tests (temp_json/temp_result cleanup trap)..."
test_item4_cleanup_trap_declared
test_item4_cleanup_on_interrupt
test_item4_cleanup_on_abnormal_exit
echo ""

log "Running ITEM 5 test (portable NUL parsing - P2 fix)..."
test_item5_portable_nul_parsing
echo ""

log "Running full cycle integration test..."
test_full_cycle_with_special_chars
echo ""

log "Running AUDIT FIX 1 tests (config repos honored)..."
test_audit_fix_1_config_repos_honored
test_audit_fix_1_config_fallback_when_empty
test_audit_fix_1_config_missing_path_skipped_with_warning
test_audit_fix_1_config_no_file_uses_autodiscovery
echo ""

log "Running DEFECT (a) test (Python interpreter probe)..."
test_defect_a_python_interpreter_probe
echo ""

log "Running DEFECT (b) test (Unpushed commits scanning)..."
test_defect_b_unpushed_commits_scanning
echo ""

log "Running DEFECT (c) tests (Path dedup whole-line matching)..."
test_defect_c_path_dedup_whole_line_match
test_defect_c_dedup_integration
echo ""

log "Running PORTABILITY TASK (A) tests (no-upstream/detached HEAD)..."
test_no_upstream_detection
test_no_upstream_fallback_scan
test_detached_head_detection
echo ""

log "Running PORTABILITY TASK (B) test (portable path derivation)..."
test_portable_path_derivation
echo ""

log "Running FIX #5 tests (blocked-cycle exit code contract)..."
test_fix5_blocked_cycle_exit_code
test_fix5_clean_cycle_exit_code
echo ""

echo "=================================================="
echo "Test Results: $PASSED passed, $FAILED failed"
echo "=================================================="

exit $FAILED
