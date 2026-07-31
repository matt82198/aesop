#!/usr/bin/env bash
set -uo pipefail

resolve_aesop_root() {
  # Resolve the repo whose push is being gated -- NOT a hardcoded path.
  #
  # This previously defaulted to "$HOME/aesop" (the primary tree). Pushing from
  # any of the ~40 sibling worktrees therefore ran the PRIMARY tree's copy of
  # every gate script instead of the branch's own, so a gate fix on a branch
  # could never take effect for that branch's own push, and gates evaluated
  # code that was not being pushed.
  #
  # A pre-push hook runs with cwd inside the pushing worktree, so the git
  # toplevel is the correct root. Explicit AESOP_ROOT still wins; the old
  # hardcoded path remains only as a last-resort fallback.
  if [ -n "${AESOP_ROOT:-}" ]; then
    printf '%s\n' "$AESOP_ROOT"
    return 0
  fi
  local top=""
  if top=$(git rev-parse --show-toplevel 2>/dev/null) && [ -n "$top" ]; then
    printf '%s\n' "$top"
    return 0
  fi
  printf '%s\n' "$HOME/aesop"
}

json_escape() {
  # Escape backslashes first, then quotes, then control chars for valid JSON
  # Finding 5: Handle ALL C0 control characters (\x00-\x08, \x0b-\x0c, \x0e-\x1f)
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  # Escape remaining C0 control characters as \u00XX
  # Using sed with explicit byte mappings for each C0 char not yet escaped
  printf '%s' "$s" | sed \
    -e 's/[\x00]/\\u0000/g' \
    -e 's/[\x01]/\\u0001/g' \
    -e 's/[\x02]/\\u0002/g' \
    -e 's/[\x03]/\\u0003/g' \
    -e 's/[\x04]/\\u0004/g' \
    -e 's/[\x05]/\\u0005/g' \
    -e 's/[\x06]/\\u0006/g' \
    -e 's/[\x07]/\\u0007/g' \
    -e 's/[\x08]/\\u0008/g' \
    -e 's/[\x0b]/\\u000b/g' \
    -e 's/[\x0c]/\\u000c/g' \
    -e 's/[\x0e]/\\u000e/g' \
    -e 's/[\x0f]/\\u000f/g' \
    -e 's/[\x10]/\\u0010/g' \
    -e 's/[\x11]/\\u0011/g' \
    -e 's/[\x12]/\\u0012/g' \
    -e 's/[\x13]/\\u0013/g' \
    -e 's/[\x14]/\\u0014/g' \
    -e 's/[\x15]/\\u0015/g' \
    -e 's/[\x16]/\\u0016/g' \
    -e 's/[\x17]/\\u0017/g' \
    -e 's/[\x18]/\\u0018/g' \
    -e 's/[\x19]/\\u0019/g' \
    -e 's/[\x1a]/\\u001a/g' \
    -e 's/[\x1b]/\\u001b/g' \
    -e 's/[\x1c]/\\u001c/g' \
    -e 's/[\x1d]/\\u001d/g' \
    -e 's/[\x1e]/\\u001e/g' \
    -e 's/[\x1f]/\\u001f/g'
}
compute_sha256() {
  # P1-Bug2 fix: Single helper for sha256sum with fallback to shasum
  # Reads from stdin, outputs hex hash
  local hash_bin
  if command -v sha256sum >/dev/null 2>&1; then
    hash_bin="sha256sum"
  elif command -v shasum >/dev/null 2>&1; then
    hash_bin="shasum -a 256"
  else
    printf 'ERROR: sha256sum or shasum not found in PATH
' >&2
    return 1
  fi
  $hash_bin | awk '{print $1}'
}

resolve_py_bin() {
  # Single python-interpreter resolution point (mirrors compute_sha256's
  # hash_bin fallback style: prefer the modern/explicit name, fall back to
  # the generic one). Used by get_next_seq/verify_audit_log so a host that
  # only has `python` on PATH -- but it IS Python 3 -- does not silently
  # defeat the audit hash-chain's monotonic-seq tamper detection. Prints
  # nothing and returns 1 if neither resolves; callers must not mask that
  # into a silent default (Finding: get_next_seq's old `|| echo 0` did).
  if command -v python3 >/dev/null 2>&1; then
    printf 'python3'
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf 'python'
    return 0
  fi
  return 1
}

acquire_audit_lock() {
  # Finding 1: Mkdir-based atomic lock for audit log write safety
  local lock_dir="$1"
  local timeout=300
  local start_time
  start_time=$(date +%s)

  while true; do
    if mkdir "$lock_dir" 2>/dev/null; then
      # Acquired lock successfully
      echo "$$" > "$lock_dir/pid"
      return 0
    fi

    # Check if lock is stale (>timeout seconds old)
    if [ -f "$lock_dir/pid" ]; then
      local lock_time
      lock_time=$(stat -c %Y "$lock_dir" 2>/dev/null || stat -f %m "$lock_dir" 2>/dev/null || echo 0)
      local current_time
      current_time=$(date +%s)
      if [ $((current_time - lock_time)) -gt $timeout ]; then
        # Stale lock; force reclaim atomically
        rm -rf "$lock_dir" 2>/dev/null
        mkdir "$lock_dir" 2>/dev/null && echo "$$" > "$lock_dir/pid" && return 0
      fi
    fi

    # Check timeout
    if [ $(($(date +%s) - start_time)) -gt 10 ]; then
      # Lock holder is stuck; give up after 10s
      return 1
    fi

    sleep 0.1
  done
}

release_audit_lock() {
  # P0 fix: Release the lock directory only if we own it (pid matches)
  local lock_dir="$1"
  if [ -f "$lock_dir/pid" ]; then
    local lock_pid
    lock_pid=$(cat "$lock_dir/pid" 2>/dev/null || echo "")
    if [ "$lock_pid" = "$$" ]; then
      rm -rf "$lock_dir" 2>/dev/null
    fi
  fi
}

get_previous_hash() {
  # Finding 6: Fallback for missing sha256sum, fail loudly if unavailable
  local audit_log="$1"
  if [ ! -f "$audit_log" ] || [ ! -s "$audit_log" ]; then
    printf 'GENESIS'
    return 0
  fi

  local hash_bin
  if command -v sha256sum >/dev/null 2>&1; then
    hash_bin="sha256sum"
  elif command -v shasum >/dev/null 2>&1; then
    hash_bin="shasum -a 256"
  else
    printf 'ERROR: sha256sum or shasum not found in PATH\n' >&2
    return 1
  fi

  tail -n 1 "$audit_log" | tr -d '\n' | $hash_bin | awk '{print $1}'
}

get_next_seq() {
  # Finding 2: Get monotonically increasing sequence number
  local audit_log="$1"
  if [ ! -f "$audit_log" ] || [ ! -s "$audit_log" ]; then
    echo 1
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    # Genuinely missing interpreter: loud failure on stderr, not a silent
    # slide into the same `|| echo 0` masking used for JSON-parse errors
    # below. (This still degrades to seq=1 so log_block/log_event can keep
    # writing an audit trail even in this near-impossible edge case, but
    # the degradation is now visible instead of invisible.)
    printf 'ERROR: no python3 or python interpreter found in PATH; cannot compute next audit seq (monotonic-seq tamper detection DEGRADED)\n' >&2
    echo 1
    return 0
  fi

  local last_seq
  last_seq=$(tail -n 1 "$audit_log" | "$py_bin" -c "import sys, json; data = json.load(sys.stdin); print(data.get('seq', 0))" 2>/dev/null || echo 0)
  echo $((last_seq + 1))
}

verify_audit_log() {
  # P1-Bug1 fix: Acquire write lock while reading/verifying sidecar
  # Prevents false truncation positive during concurrent appends
  # Finding 2: Include truncation detection via tail hash sidecar and seq field
  local audit_log="$1"
  if [ ! -f "$audit_log" ]; then
    printf 'Error: Audit log not found at %s
' "$audit_log" >&2
    return 1
  fi

  if [ ! -s "$audit_log" ]; then
    printf 'Audit log is empty or does not exist
'
    return 0
  fi

  local state_dir
  state_dir=$(dirname "$audit_log")
  local lock_dir="$state_dir/.audit-log-lock"
  local tail_hash_file="$state_dir/.audit-tail-hash"

  # P1-Bug1: Acquire lock before reading sidecar to prevent race
  if ! acquire_audit_lock "$lock_dir"; then
    printf 'Warning: Could not acquire lock for verification; skipping sidecar check
' >&2
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    release_audit_lock "$lock_dir"
    printf 'ERROR: no python3 or python interpreter found in PATH; cannot verify audit log (hash-chain/seq verification unavailable)\n' >&2
    return 1
  fi

  local line_num=0
  local prev_line=""
  local expected_hash=""
  local actual_prev_hash=""
  local prev_seq=0

  while IFS= read -r line; do
    line_num=$((line_num + 1))

    if [ $line_num -eq 1 ]; then
      expected_hash="GENESIS"
    else
      expected_hash=$(printf '%s' "$prev_line" | compute_sha256)
    fi

    actual_prev_hash=$(printf '%s' "$line" | "$py_bin" -c "import sys, json; data = json.load(sys.stdin); print(data.get('prev_hash', 'MISSING'))" 2>/dev/null)

    if [ "$actual_prev_hash" = "MISSING" ]; then
      release_audit_lock "$lock_dir"
      printf 'Error: Line %d missing prev_hash field
' "$line_num" >&2
      return 1
    fi

    if [ "$actual_prev_hash" != "$expected_hash" ]; then
      release_audit_lock "$lock_dir"
      printf 'Error: Hash chain broken at line %d
' "$line_num" >&2
      printf '  Expected prev_hash: %s
' "$expected_hash" >&2
      printf '  Actual prev_hash: %s
' "$actual_prev_hash" >&2
      return 1
    fi

    # Check seq monotonicity
    local current_seq
    current_seq=$(printf '%s' "$line" | "$py_bin" -c "import sys, json; data = json.load(sys.stdin); print(data.get('seq', 0))" 2>/dev/null || echo 0)
    if [ "$current_seq" -le "$prev_seq" ] && [ $line_num -gt 1 ]; then
      release_audit_lock "$lock_dir"
      printf 'Error: Sequence number not monotonic at line %d (prev: %d, current: %d)
' "$line_num" "$prev_seq" "$current_seq" >&2
      return 1
    fi
    prev_seq=$current_seq

    prev_line="$line"
  done < "$audit_log"

  # Check truncation via tail hash anchor (within lock)
  if [ -f "$tail_hash_file" ]; then
    local stored_tail_hash
    stored_tail_hash=$(head -n 1 "$tail_hash_file" 2>/dev/null)
    local actual_tail_hash
    actual_tail_hash=$(tail -n 1 "$audit_log" | tr -d '
' | compute_sha256)

    if [ "$stored_tail_hash" != "$actual_tail_hash" ]; then
      release_audit_lock "$lock_dir"
      printf 'TRUNCATION SUSPECTED: Tail hash mismatch (stored: %s, actual: %s)
' "$stored_tail_hash" "$actual_tail_hash" >&2
      return 1
    fi
  fi

  release_audit_lock "$lock_dir"

  if [ $line_num -gt 0 ]; then
    printf 'Audit log verification OK (%d entries)
' "$line_num"
  fi
  return 0
}

check_branch_policy() {
  # Parse git pre-push stdin to check if any remote-ref targets main or master
  # Format: <local-ref> <local-sha> <remote-ref> <remote-sha>
  # This catches attempts like: git push origin HEAD:main (even from feature branch)
  # Finding 3: Handle tty mode and final line without trailing newline
  if [ -t 0 ]; then
    # Running interactively on a tty; skip stdin processing with note
    # but still check current branch as fallback. Note: main() blocks tty before this can matter.
    :
  else
    # Not a tty; read stdin normally
    local saw_tuple=0
    local saw_nondelete=0
    local all_tags_or_delete_only=1
    while IFS=' ' read -r local_ref local_sha remote_ref remote_sha || [ -n "$local_ref" ]; do
      # Skip empty lines
      if [ -z "$remote_ref" ]; then
        continue
      fi
      saw_tuple=1

      # Delete refspec (local sha all zeros): removes a remote ref, pushes no
      # content. Deleting refs/heads/main|master is still blocked below; other
      # deletions never constitute a push TO main.
      if [ "$local_sha" = "0000000000000000000000000000000000000000" ]; then
        if [ "$remote_ref" = "refs/heads/main" ] || [ "$remote_ref" = "refs/heads/master" ]; then
          return 1
        fi
        continue
      fi
      saw_nondelete=1

      # Block if attempting to push to main or master
      if [ "$remote_ref" = "refs/heads/main" ] || [ "$remote_ref" = "refs/heads/master" ]; then
        return 1
      fi

      # Track whether all non-delete tuples are tag refs (fully-qualified literal prefix match)
      if [ "$all_tags_or_delete_only" = "1" ]; then
        if [[ "$remote_ref" != refs/tags/* ]]; then
          all_tags_or_delete_only=0
        fi
      fi
    done

    # Tag-only push (tuples seen, all non-delete tuples are refs/tags/*): allowed
    # regardless of the currently checked-out branch -- tag pushes are administrative
    # and do not constitute a push to main.
    if [ "$saw_tuple" = "1" ] && [ "$all_tags_or_delete_only" = "1" ]; then
      return 0
    fi

    # Delete-only push (tuples seen, none pushing content): allowed regardless
    # of the currently checked-out branch -- branch deletion from a main
    # checkout is administrative, not a push to main.
    if [ "$saw_tuple" = "1" ] && [ "$saw_nondelete" = "0" ]; then
      return 0
    fi
  fi

  # If no protected branch in stdin, also check current branch as fallback
  # (for safety, in case stdin is empty or hook runs without git pre-push).
  # Note: this fallback is narrowed to apply only when stdin did not contain
  # a pure tag push or delete-only push, since those are administrative operations
  # independent of the currently checked-out branch.
  local current_branch
  current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")

  if [ "$current_branch" = "main" ] || [ "$current_branch" = "master" ]; then
    return 1
  fi

  return 0
}

get_commit_range() {
  # Parse pre-push stdin to build commit range(s) for scanning.
  # Format: <local-ref> <local-sha> <remote-ref> <remote-sha>
  # A single push (git push --all / multiple branches / multiple tags in one
  # invocation) can feed MULTIPLE ref tuples on stdin, one per line. Emits
  # ONE "remote-sha..local-sha" range PER valid tuple found, one per output
  # line (like check_branch_policy, which already iterates every tuple to
  # check branch policy) -- P3 wave-25 fix: this used to `return 0` after the
  # FIRST tuple, so a multi-ref push only ever scanned the first branch's
  # range and every other ref in the same push silently bypassed the secret
  # scan. Returns 0 if at least one range was emitted, 1 if none could be
  # parsed (malformed/invalid tuples), 2 if delete-only (tuples present but
  # all deletions), 3 if truly empty stdin (no tuples at all, e.g. up-to-date
  # push) -- single-ref callers see exactly the same one-line output as before.
  # P1 bug fix: distinguish empty stdin (rc=3, allow) from malformed stdin
  # (rc=1, fail-closed).
  local local_ref local_sha remote_ref remote_sha
  local found=0
  local saw_any_tuple=0

  if [ -t 0 ]; then
    # Running interactively on a tty; no stdin to parse → fail-closed (direct hook invocation)
    # main() blocks tty before this is reached via the normal flow; nearly dead code but tests exercise it
    printf 'Error: No stdin on tty; cannot parse pre-push ref tuples (interactive hook invocation)\n' >&2
    return 1
  fi

  local saw_delete=0
  while IFS=' ' read -r local_ref local_sha remote_ref remote_sha || [ -n "$local_ref" ]; do
    # Skip truly empty lines (no fields at all)
    # A line with any content (even if malformed) will have at least one non-empty field
    if [ -z "$local_ref" ] && [ -z "$local_sha" ] && [ -z "$remote_ref" ] && [ -z "$remote_sha" ]; then
      continue
    fi

    # Harden tuple parsing: require all 4 fields present (P1 fix)
    # If we have some content but not all 4 fields, it's malformed
    if [ -z "$remote_ref" ] || [ -z "$remote_sha" ]; then
      # Malformed line: has fewer than 4 fields
      printf 'Error: Malformed pre-push stdin: insufficient fields (expected 4, got fewer) in line: %s %s %s %s\n' "$local_ref" "$local_sha" "$remote_ref" "$remote_sha" >&2
      return 1
    fi

    saw_any_tuple=1

    # Delete refspec (local sha all zeros): no content is pushed, so there is
    # no commit range to scan. Skipped here; if the WHOLE push is deletes we
    # return 2 so the caller can pass without weakening fail-closed behavior
    # for unparseable stdin (which stays return 1).
    if [ "$local_sha" = "0000000000000000000000000000000000000000" ]; then
      saw_delete=1
      continue
    fi

    # Found a valid ref tuple; build its range
    # If remote_sha is all zeros (new branch), use merge-base with default branch
    if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
      # New branch: find merge-base with main/master
      local default_branch="main"
      if ! git rev-parse "$default_branch" >/dev/null 2>&1; then
        default_branch="master"
      fi
      printf '%s..%s\n' "$default_branch" "$local_sha"
    else
      # Existing branch: use remote sha as base
      printf '%s..%s\n' "$remote_sha" "$local_sha"
    fi
    found=1
  done

  if [ "$found" -eq 1 ]; then
    return 0
  fi
  if [ "$saw_delete" -eq 1 ]; then
    # Delete-only push: nothing to scan (distinct from unparseable stdin)
    return 2
  fi
  if [ "$saw_any_tuple" -eq 0 ]; then
    # Truly empty stdin: no tuples at all (e.g., up-to-date push) (P1 fix: rc=3)
    return 3
  fi
  # Malformed stdin: tuples present but none were valid
  return 1
}

check_secret_scan() {
  # tools/secret_scan.py is Python-3-only: resolve python3 BEFORE python
  # (mirrors daemons/*.sh and resolve_py_bin's own order) so a host where
  # `python` == Python 2 doesn't crash the scanner instead of running it.
  local scan_bin
  if ! scan_bin=$(resolve_py_bin); then
    scan_bin="python"
  fi

  # Parse pre-push stdin FIRST: a delete-only push carries no content, so it
  # needs no scanner at all — the availability check below only applies when
  # there is actually content to scan (ordering matters: in scanner-less
  # environments a branch deletion must still be possible).
  # A multi-ref push (git push --all, or multiple branches/tags in one
  # invocation) yields one range per line from get_commit_range(); scan EVERY
  # one and fail if ANY range is dirty (P3 wave-25 fix: previously only the
  # first ref tuple's range was ever scanned).
  # P1 bug fix: distinguish empty stdin (rc=3, allow) from malformed stdin
  # (rc=1, fail-closed).
  local commit_ranges
  commit_ranges=$(get_commit_range)
  local parse_exit_code=$?

  if [ $parse_exit_code -eq 2 ]; then
    # Delete-only push: no content pushed, nothing to scan. Explicitly allowed
    # (rc=2 is only emitted when tuples WERE parsed and all were deletions);
    # unparseable/empty stdin still fails closed below.
    log_event "secret_scan_skipped_delete_only_push"
    return 0
  fi

  if [ $parse_exit_code -eq 3 ]; then
    # Empty stdin: no tuples at all (e.g., up-to-date push), nothing to scan (P1 fix: rc=3)
    # This is distinct from malformed stdin (rc=1) and is explicitly allowed
    log_event "secret_scan_skipped_empty_stdin"
    return 0
  fi

  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local scan_script="$aesop_root/tools/secret_scan.py"

  if [ ! -f "$scan_script" ] || [ ! -x "$scan_script" ]; then
    # Scanner not found or not executable: fail-closed (cannot verify => deny)
    log_block "secret_scan_unavailable"
    printf 'FATAL: secret_scan.py not found or not executable at %s\n' "$scan_script" >&2
    return 1
  fi

  if [ $parse_exit_code -ne 0 ] || [ -z "$commit_ranges" ]; then
    # Malformed stdin: fail-CLOSED (security P1 fix)
    # Only fail-open for missing scanner tool, not for malformed input
    # rc=1 is returned for malformed tuples (e.g., 3-field lines), which must fail-closed
    log_block "secret_scan_stdin_parse_failed"
    printf 'Error: Could not parse pre-push stdin for commit range (malformed tuple)\n' >&2
    return 1
  fi

  local overall_exit_code=0
  local range
  while IFS= read -r range || [ -n "$range" ]; do
    [ -z "$range" ] && continue

    # Run scanner on this range and capture output
    local scan_output
    scan_output=$("$scan_bin" "$scan_script" --range "$range" 2>&1)
    local scan_exit_code=$?

    # Surface all output including ALLOWED-DOC findings to stderr
    if [ -n "$scan_output" ]; then
      printf '%s\n' "$scan_output" >&2
    fi

    if [ $scan_exit_code -ne 0 ]; then
      overall_exit_code=$scan_exit_code
    fi
  done <<< "$commit_ranges"

  return $overall_exit_code
}

check_tracker_guard() {
  # Tracker zombie-resurrection gate (tools/tracker_guard.py --check).
  # Runs against the LIVE runtime state (AESOP_STATE_ROOT, default
  # $AESOP_ROOT/state). This gate is wired here -- not in CI -- because
  # state/tracker.json is git-ignored runtime state: a CI checkout never
  # has it, so a CI step would be a permanently-green decoration. The
  # pre-push hook is the point where the real state exists.
  #
  # Fail-open ONLY for missing optional tooling (hook is installed into
  # repos without an aesop checkout; no aesop install == no tracker state
  # to guard -- consistent with the hooks/ key invariant). An actual
  # zombie detection (exit 1 from --check) stays fail-closed and blocks
  # the push. tracker_guard itself exits 0 when tracker.json is absent.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local guard_script="$aesop_root/tools/tracker_guard.py"

  if [ ! -f "$guard_script" ]; then
    log_event "tracker_guard_skipped_tool_missing"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    printf 'Warning: no python interpreter found; tracker guard skipped\n' >&2
    log_event "tracker_guard_skipped_no_python"
    return 0
  fi

  local guard_output
  guard_output=$(AESOP_STATE_ROOT="${AESOP_STATE_ROOT:-$aesop_root/state}" "$py_bin" "$guard_script" --check 2>&1)
  local guard_exit_code=$?

  if [ $guard_exit_code -ne 0 ]; then
    if [ -n "$guard_output" ]; then
      printf '%s\n' "$guard_output" >&2
    fi
    return 1
  fi

  return 0
}

check_import_resolution() {
  # Python import resolution validator (guardrail G5).
  # Parses all staged .py files via AST, extracts import/from-import statements,
  # resolves each module against repo structure and sys.stdlib_module_names.
  # Fail-closed (exit 1) if any import cannot resolve.
  #
  # Root cause: Agent wrote file with unresolvable import to primary tree
  # during worktree isolation, bypassing any import-resolution gate.
  # This guardrail validates all staged .py imports before push.
  #
  # Fail-open ONLY for missing optional tool (repo without aesop checkout);
  # actual resolution failures stay fail-closed.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local import_check_script="$aesop_root/tools/import_resolution_check.py"

  if [ ! -f "$import_check_script" ]; then
    log_event "import_check_skipped_tool_missing"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    printf 'Warning: no python interpreter found; import resolution check skipped\n' >&2
    log_event "import_check_skipped_no_python"
    return 0
  fi

  local check_output
  check_output=$("$py_bin" "$import_check_script" 2>&1)
  local check_exit_code=$?

  if [ $check_exit_code -ne 0 ]; then
    if [ -n "$check_output" ]; then
      printf '%s\n' "$check_output" >&2
    fi
    return 1
  fi

  return 0
}

check_claudemd_sync() {
  # CLAUDE.md synchronization gate (tools/claudemd_sync_gate.py --check).
  # Ensures code changes are accompanied by domain CLAUDE.md updates.
  # Runs at push time so authors see drift immediately.
  #
  # Fail-open for missing optional tooling; fail-closed for actual drift.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local sync_script="$aesop_root/tools/claudemd_sync_gate.py"

  if [ ! -f "$sync_script" ] || [ ! -x "$sync_script" ]; then
    log_event "claudemd_sync_skipped_tool_missing"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    printf 'Warning: no python interpreter found; CLAUDE.md sync gate skipped\n' >&2
    log_event "claudemd_sync_skipped_no_python"
    return 0
  fi

  local sync_output
  sync_output=$("$py_bin" "$sync_script" --check 2>&1)
  local sync_exit_code=$?

  if [ $sync_exit_code -ne 0 ]; then
    if [ -n "$sync_output" ]; then
      printf '%s\n' "$sync_output" >&2
    fi
    return 1
  fi

  return 0
}

check_metrics() {
  # Metrics verification gate (tools/metrics_gate.py).
  # Ensures hard numeric claims in markdown are verified with source markers.
  # Runs at push time so authors see unverified metrics immediately.
  #
  # Fail-open for missing optional tooling; fail-closed for unverified metrics.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local metrics_script="$aesop_root/tools/metrics_gate.py"

  if [ ! -f "$metrics_script" ] || [ ! -x "$metrics_script" ]; then
    log_event "metrics_gate_skipped_tool_missing"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    printf 'Warning: no python interpreter found; metrics gate skipped\n' >&2
    log_event "metrics_gate_skipped_no_python"
    return 0
  fi

  local metrics_output
  metrics_output=$("$py_bin" "$metrics_script" 2>&1)
  local metrics_exit_code=$?

  if [ $metrics_exit_code -ne 0 ]; then
    if [ -n "$metrics_output" ]; then
      printf '%s\n' "$metrics_output" >&2
    fi
    return 1
  fi

  return 0
}

check_test_suite_count() {
  # Test suite count drift detection gate (tools/verify_test_suite_count.py --check).
  # Verifies that test suite counts documented in tests/CLAUDE.md match the actual
  # number of test files on disk. This gate is wired here (not just in CI) because
  # CI only runs after push; a local pre-push check catches the drift immediately.
  #
  # Fail-open ONLY for missing optional tooling (hook is installed into
  # repos without an aesop checkout; no aesop install == no verify tool).
  # An actual drift detection (exit 1 from --check) stays fail-closed and blocks
  # the push. verify_test_suite_count exits 0 when counts match, 1 on drift.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local verify_script="$aesop_root/tools/verify_test_suite_count.py"

  if [ ! -f "$verify_script" ]; then
    log_event "test_suite_count_skipped_tool_missing"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    printf 'Warning: no python interpreter found; test suite count check skipped\n' >&2
    log_event "test_suite_count_skipped_no_python"
    return 0
  fi

  local verify_output
  verify_output=$("$py_bin" "$verify_script" --check 2>&1)
  local verify_exit_code=$?

  if [ $verify_exit_code -ne 0 ]; then
    if [ -n "$verify_output" ]; then
      printf '%s\n' "$verify_output" >&2
    fi
    return 1
  fi

  return 0
}

log_event() {
  # Finding 1 & 2: Acquire lock before read-modify-append, add seq field, update sidecar
  local event_type="$1"
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local state_dir="$aesop_root/state"
  local audit_log="$state_dir/SECURITY-AUDIT.log"
  local lock_dir="$state_dir/.audit-log-lock"
  local ts
  ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  local repo_name
  repo_name=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || echo 'unknown')")
  local user
  user=$(git config user.name 2>/dev/null || echo "unknown")

  mkdir -p "$state_dir" 2>/dev/null

  # Acquire write lock
  if ! acquire_audit_lock "$lock_dir"; then
    # Log write blocked by lock; fail-open (don't block push)
    printf 'Warning: audit log write skipped, lock contention\n' >&2
    return 0
  fi

  local prev_hash
  prev_hash=$(get_previous_hash "$audit_log")
  local seq
  seq=$(get_next_seq "$audit_log")

  printf '{"seq":%d,"prev_hash":"%s","ts":"%s","repo":"%s","event":"%s","user":"%s"}\n' "$seq" "$prev_hash" "$ts" "$(json_escape "$repo_name")" "$(json_escape "$event_type")" "$(json_escape "$user")" >> "$audit_log" 2>/dev/null

  # Update tail hash sidecar
  if [ -s "$audit_log" ]; then
    tail -n 1 "$audit_log" | tr -d '\n' | compute_sha256 > "$state_dir/.audit-tail-hash" 2>/dev/null
  fi

  release_audit_lock "$lock_dir"
}

log_block() {
  # Finding 1 & 2: Acquire lock before read-modify-append, add seq field, update sidecar
  local reason="$1"
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local state_dir="$aesop_root/state"
  local audit_log="$state_dir/SECURITY-AUDIT.log"
  local lock_dir="$state_dir/.audit-log-lock"
  local ts
  ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  local repo_name
  repo_name=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || echo 'unknown')")
  local user
  user=$(git config user.name 2>/dev/null || echo "unknown")

  mkdir -p "$state_dir" 2>/dev/null

  # Acquire write lock
  if ! acquire_audit_lock "$lock_dir"; then
    # Log write blocked by lock; fail-open (don't block push)
    printf 'Warning: audit log write skipped, lock contention\n' >&2
    return 0
  fi

  local prev_hash
  prev_hash=$(get_previous_hash "$audit_log")
  local seq
  seq=$(get_next_seq "$audit_log")

  printf '{"seq":%d,"prev_hash":"%s","ts":"%s","repo":"%s","event":"push_blocked","reason":"%s","user":"%s"}\n' "$seq" "$prev_hash" "$ts" "$(json_escape "$repo_name")" "$(json_escape "$reason")" "$(json_escape "$user")" >> "$audit_log" 2>/dev/null

  # Update tail hash sidecar
  if [ -s "$audit_log" ]; then
    tail -n 1 "$audit_log" | tr -d '\n' | compute_sha256 > "$state_dir/.audit-tail-hash" 2>/dev/null
  fi

  release_audit_lock "$lock_dir"
}

check_encoding_lint() {
  # Guardrail G10 extension: encoding lint for subprocess calls.
  # Runs tools/encoding_lint.py --check against staged Python files.
  # Fail-open if tool missing (optional tooling); fail-closed on actual findings.
  local aesop_root="${AESOP_ROOT:-$HOME/aesop}"
  local lint_script="$aesop_root/tools/encoding_lint.py"

  if [ ! -f "$lint_script" ] || [ ! -x "$lint_script" ]; then
    log_event "encoding_lint_skipped_tool_missing"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    printf 'Warning: no python interpreter found; encoding lint skipped\n' >&2
    log_event "encoding_lint_skipped_no_python"
    return 0
  fi

  local lint_output
  lint_output=$("$py_bin" "$lint_script" --check 2>&1)
  local lint_exit_code=$?

  if [ $lint_exit_code -ne 0 ]; then
    if [ -n "$lint_output" ]; then
      printf '%s\n' "$lint_output" >&2
    fi
    return 1
  fi

  return 0
}

check_test_coverage() {
  # Guardrail G2 extension: verify all on-disk test files are run by CI.
  # Runs tools/verify_test_coverage.py --check to detect orphaned tests.
  # Fail-open if tool missing (optional tooling); fail-closed on findings.
  local aesop_root="${AESOP_ROOT:-$HOME/aesop}"
  local coverage_script="$aesop_root/tools/verify_test_coverage.py"

  if [ ! -f "$coverage_script" ] || [ ! -x "$coverage_script" ]; then
    log_event "test_coverage_skipped_tool_missing"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    printf 'Warning: no python interpreter found; test coverage check skipped\n' >&2
    log_event "test_coverage_skipped_no_python"
    return 0
  fi

  local coverage_output
  coverage_output=$("$py_bin" "$coverage_script" --check 2>&1)
  local coverage_exit_code=$?

  if [ $coverage_exit_code -ne 0 ]; then
    if [ -n "$coverage_output" ]; then
      printf '%s\n' "$coverage_output" >&2
    fi
    return 1
  fi

  return 0
}

run_test_mode() {
  local test_passed=0
  local test_failed=0
  local tmpdir
  tmpdir=$(mktemp -d)
  trap "rm -rf '$tmpdir'" EXIT

  printf '\n=== Test 1: Branch policy check ===\n'
  (
    cd "$tmpdir" || exit 1
    git init -q
    git config user.email "test@example.com"
    git config user.name "Test User"
    echo "dummy" > file.txt
    git add file.txt
    git commit -q -m "initial"
    git checkout -q -b main 2>/dev/null || git branch -M main

    if check_branch_policy; then
      printf 'FAIL: Should have blocked on main branch\n'
      exit 1
    fi
    printf 'PASS: Correctly blocked on main branch\n'
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 2: Branch policy allows feature branches ===\n'
  (
    cd "$tmpdir" || exit 1
    git checkout -q -b feature/test 2>/dev/null

    if check_branch_policy; then
      printf 'PASS: Correctly allowed feature branch\n'
    else
      printf 'FAIL: Should have allowed feature branch\n'
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 3: Audit log format ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop"
    mkdir -p "$AESOP_ROOT/state"
    log_block "test_reason"

    if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
      printf 'FAIL: Audit log not created\n'
      exit 1
    fi

    audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")
    if ! printf '%s' "$audit_line" | python3 -m json.tool >/dev/null 2>&1; then
      printf 'FAIL: Audit log entry is not valid JSON\n'
      printf 'Entry: %s\n' "$audit_line"
      exit 1
    fi

    if printf '%s' "$audit_line" | grep -q '"event":"push_blocked"'; then
      printf 'PASS: Audit log entry valid JSON with correct event type\n'
    else
      printf 'FAIL: Audit log entry missing correct event\n'
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 4: JSON escaping with special characters ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop"
    mkdir -p "$AESOP_ROOT/state"
    # SCOPED (identity-polluter class): quoted-name check runs inside an
    # isolated fixture repo; must NEVER touch the live repo config.
    SELFTEST_REPO="$tmpdir/selftest_identity_repo"
    mkdir -p "$SELFTEST_REPO"
    cd "$SELFTEST_REPO" || exit 1
    git init -q
    git config user.email "test@example.com"
    git config user.name 'John "Jack" Doe'
    log_block "reason_with_backslash\\test"

    if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
      printf 'FAIL: Audit log not created for special chars test\n'
      exit 1
    fi

    audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")
    if ! printf '%s' "$audit_line" | python3 -m json.tool >/dev/null 2>&1; then
      printf 'FAIL: JSON with escaped chars is invalid\n'
      printf 'Entry: %s\n' "$audit_line"
      exit 1
    fi

    if printf '%s' "$audit_line" | grep -q 'John.*Jack.*Doe'; then
      printf 'PASS: JSON correctly escapes quotes in user names\n'
    else
      printf 'FAIL: JSON escaping incomplete\n'
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 5: stdin handling (git hook compatibility) ===\n'
  (
    cd "$tmpdir" || exit 1
    git checkout -q feature/test 2>/dev/null

    # Simulate git pre-push stdin with ref info
    local_sha=$(git rev-parse HEAD 2>/dev/null || echo "0000000")
    printf '%s\n' "refs/heads/feature/test $local_sha refs/heads/feature/test 0000000000000000000000000000000000000000" | {
      if check_branch_policy >/dev/null 2>&1; then
        printf 'PASS: Hook accepts stdin without choking\n'
      else
        printf 'FAIL: Hook failed with stdin input\n'
        exit 1
      fi
    }
  ) || {
    printf 'FAIL: stdin test exited with error\n'
    test_failed=$((test_failed + 1))
  }
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  fi

  printf '\n=== Test 6: stdin refspec bypass detection (HEAD:main) ===\n'
  (
    cd "$tmpdir" || exit 1
    git checkout -q feature/test 2>/dev/null || git checkout -q -b feature/bypass_test 2>/dev/null

    # Simulate git pre-push stdin for: git push origin HEAD:main
    # This is an explicit refspec that pushes to main even though local HEAD is feature/test
    # The fixed check_branch_policy MUST block this by checking remote-ref in stdin
    local_sha=$(git rev-parse HEAD 2>/dev/null || echo "0000000")
    remote_main_sha="0000000000000000000000000000000000000000"

    # Stdin format: <local-ref> <local-sha> <remote-ref> <remote-sha>
    # For "git push origin HEAD:main": refs/heads/feature/test <sha> refs/heads/main 0000...
    printf '%s\n' "refs/heads/feature/test $local_sha refs/heads/main $remote_main_sha" | {
      if check_branch_policy >/dev/null 2>&1; then
        printf 'FAIL: Should have blocked push to main via stdin refspec\n'
        exit 1
      else
        printf 'PASS: Correctly blocked push to main via stdin refspec\n'
      fi
    }
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 7: Secret scan unavailable logs audit event ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_no_scanner"
    mkdir -p "$AESOP_ROOT/state"

    # Provide a valid 4-field tuple to test the scanner-missing condition
    # (empty stdin would now skip the scan with log_event "secret_scan_skipped_empty_stdin")
    # Format: <local-ref> <local-sha> <remote-ref> <remote-sha>
    stdin_input="refs/heads/feature/test abc123def456 refs/heads/feature/test 0000000000000000000000000000000000000000"

    # Capture stderr to verify warning is printed
    stderr_output=$( { printf '%s\n' "$stdin_input" | check_secret_scan; } 2>&1 1>/dev/null )
    exit_code=$?

    # Should return 1 (fail-closed, security-safe default)
    if [ "$exit_code" -ne 1 ]; then
      printf 'FAIL: check_secret_scan should return 1 when scanner missing (fail-closed)\n'
      exit 1
    fi

    # Should have logged a "secret_scan_unavailable" event
    if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
      printf 'FAIL: Audit log not created when scanner is unavailable\n'
      exit 1
    fi

    audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")

    # Verify JSON is valid
    if ! printf '%s' "$audit_line" | python3 -m json.tool >/dev/null 2>&1; then
      printf 'FAIL: Audit log entry is not valid JSON\n'
      printf 'Entry: %s\n' "$audit_line"
      exit 1
    fi

    # Verify event type is "push_blocked" (fail-closed logged as blocked)
    if ! printf '%s' "$audit_line" | grep -q '"event":"push_blocked"'; then
      printf 'FAIL: Audit log entry missing correct event type\n'
      printf 'Expected event: "push_blocked"\n'
      printf 'Entry: %s\n' "$audit_line"
      exit 1
    fi

    # Verify a warning was printed to stderr
    if ! printf '%s' "$stderr_output" | grep -q -i 'unavailable\|missing\|not found\|fatal'; then
      printf 'FAIL: No warning message printed to stderr\n'
      printf 'stderr was: %s\n' "$stderr_output"
      exit 1
    fi

    printf 'PASS: Secret scan unavailable fails-closed and logged as push_blocked\n'
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 8: Hash-chain audit log (GENESIS first event) ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_hashchain"
    mkdir -p "$AESOP_ROOT/state"
    log_block "test_block_1"

    if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
      printf 'FAIL: Audit log not created\n'
      exit 1
    fi

    audit_line=$(tail -n 1 "$AESOP_ROOT/state/SECURITY-AUDIT.log")
    if ! printf '%s' "$audit_line" | grep -q '"prev_hash":"GENESIS"'; then
      printf 'FAIL: First event should have prev_hash=GENESIS\n'
      printf 'Entry: %s\n' "$audit_line"
      exit 1
    fi

    if ! printf '%s' "$audit_line" | python3 -m json.tool >/dev/null 2>&1; then
      printf 'FAIL: Hash-chained entry is not valid JSON\n'
      printf 'Entry: %s\n' "$audit_line"
      exit 1
    fi

    printf 'PASS: First event has GENESIS prev_hash and valid JSON\n'
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 9: Hash-chain builds across 2+ events ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_hashchain2"
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

    line1_hash=$(printf '%s' "$line1" | tr -d '\n' | compute_sha256)
    line2_prev=$(printf '%s' "$line2" | python3 -c "import sys, json; print(json.load(sys.stdin).get('prev_hash', ''))")

    if [ "$line1_hash" != "$line2_prev" ]; then
      printf 'FAIL: Second event prev_hash does not match first line hash\n'
      printf 'Expected: %s\n' "$line1_hash"
      printf 'Got: %s\n' "$line2_prev"
      exit 1
    fi

    printf 'PASS: Hash chain builds correctly across 2 events\n'
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 10: verify-audit-log passes on intact chain ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_verify"
    mkdir -p "$AESOP_ROOT/state"
    log_block "entry_1"
    log_block "entry_2"
    log_event "entry_3"

    if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
      printf 'FAIL: Audit log not created\n'
      exit 1
    fi

    if verify_audit_log "$AESOP_ROOT/state/SECURITY-AUDIT.log" >/dev/null 2>&1; then
      printf 'PASS: Verification passed on intact chain\n'
    else
      printf 'FAIL: Verification should pass on intact chain\n'
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 11: verify-audit-log detects tampered line ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_tamper"
    mkdir -p "$AESOP_ROOT/state"
    log_block "entry_1"
    log_block "entry_2"
    log_block "entry_3"

    if [ ! -f "$AESOP_ROOT/state/SECURITY-AUDIT.log" ]; then
      printf 'FAIL: Audit log not created\n'
      exit 1
    fi

    sed -i '2s/"reason":"[^"]*"/"reason":"TAMPERED"/g' "$AESOP_ROOT/state/SECURITY-AUDIT.log"

    if ! verify_audit_log "$AESOP_ROOT/state/SECURITY-AUDIT.log" >/dev/null 2>&1; then
      printf 'PASS: Verification detected tampered middle line\n'
    else
      printf 'FAIL: Verification should detect tampering\n'
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 12: get_commit_range emits ALL ref tuples (multi-ref push) ===\n'
  (
    cd "$tmpdir" || exit 1
    git checkout -q feature/test 2>/dev/null || git checkout -q -b feature/multiref 2>/dev/null
    local_sha=$(git rev-parse HEAD 2>/dev/null || echo "0000000")

    # Simulate a multi-ref push (git push --all / multiple branches in one
    # invocation): two ref tuples on stdin. Wave-25 P3 regression: the old
    # get_commit_range() `return 0`'d after the FIRST tuple, so a multi-ref
    # push only ever produced ONE range.
    stdin_input="refs/heads/branch-a $local_sha refs/heads/branch-a 0000000000000000000000000000000000000000
refs/heads/branch-b $local_sha refs/heads/branch-b 0000000000000000000000000000000000000000"

    ranges=$(printf '%s\n' "$stdin_input" | get_commit_range)
    range_count=$(printf '%s\n' "$ranges" | grep -c '\.\.')

    if [ "$range_count" -eq 2 ]; then
      printf 'PASS: get_commit_range emitted %d ranges for a 2-ref push\n' "$range_count"
    else
      printf 'FAIL: Expected 2 ranges for a 2-ref push, got %d. Output: %s\n' "$range_count" "$ranges"
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 13: check_secret_scan scans EVERY ref range, not just the first ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_multiref"
    mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"

    # Mock scanner: fails only when the --range argument's local-sha side
    # matches the "dirty" ref's local sha. This proves BOTH ranges from a
    # multi-ref push are actually scanned -- not just the first -- since the
    # dirty ref is deliberately placed SECOND in stdin.
    cat > "$AESOP_ROOT/tools/secret_scan.py" <<'SCANNER'
#!/usr/bin/env python3
import sys
args = sys.argv[1:]
range_arg = args[args.index("--range") + 1] if "--range" in args else ""
if "2222222222222222222222222222222222222222" in range_arg:
    sys.exit(1)
sys.exit(0)
SCANNER
    chmod +x "$AESOP_ROOT/tools/secret_scan.py"

    # First ref tuple is clean, SECOND ref tuple is dirty. Pre-fix,
    # get_commit_range only ever emitted the first tuple's range, so this
    # second dirty ref would have silently bypassed the scan entirely.
    stdin_input="refs/heads/clean-branch 1111111111111111111111111111111111111111 refs/heads/clean-branch 0000000000000000000000000000000000000000
refs/heads/dirty-branch 2222222222222222222222222222222222222222 refs/heads/dirty-branch 0000000000000000000000000000000000000000"

    if printf '%s\n' "$stdin_input" | check_secret_scan >/dev/null 2>&1; then
      printf 'FAIL: check_secret_scan should have blocked on the second (dirty) ref range\n'
      exit 1
    fi
    printf 'PASS: check_secret_scan blocked on a dirty range from a NON-first ref tuple\n'
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 14: main() stdin capture-once does not starve the second consumer ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_stdin_double_read"
    mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"
    cat > "$AESOP_ROOT/tools/secret_scan.py" <<'SCANNER'
#!/usr/bin/env python3
import sys
sys.exit(0)
SCANNER
    chmod +x "$AESOP_ROOT/tools/secret_scan.py"

    cd "$tmpdir" || exit 1
    git checkout -q feature/test 2>/dev/null || git checkout -q -b feature/stdin_double 2>/dev/null
    local_sha=$(git rev-parse HEAD 2>/dev/null || echo "0000000")

    stdin_input="refs/heads/feature/test $local_sha refs/heads/feature/test 0000000000000000000000000000000000000000"

    # Replicate exactly what main() now does: capture stdin ONCE, then feed
    # each consumer its own here-string copy. Before this fix, main() called
    # check_branch_policy (reading fd0 directly) then check_secret_scan
    # (also reading fd0 directly) against the SAME real pipe -- the first
    # call drained it, so get_commit_range inside the second call always
    # saw EOF and fail-closed, blocking EVERY push regardless of content.
    captured=$(printf '%s\n' "$stdin_input" | cat)

    if ! check_branch_policy <<< "$captured" >/dev/null 2>&1; then
      printf 'FAIL: check_branch_policy unexpectedly blocked the feature branch\n'
      exit 1
    fi

    stderr_output=$( { check_secret_scan <<< "$captured"; } 2>&1 1>/dev/null )
    scan_exit=$?

    if [ $scan_exit -ne 0 ]; then
      printf 'FAIL: check_secret_scan failed after check_branch_policy already read the SAME captured stdin (stdin-starvation regression). stderr: %s\n' "$stderr_output"
      exit 1
    fi

    if printf '%s' "$stderr_output" | grep -q 'parse_failed\|malformed'; then
      printf 'FAIL: check_secret_scan reported malformed/empty stdin -- it never saw the ref tuple\n'
      exit 1
    fi

    printf 'PASS: check_secret_scan still sees the ref tuple after check_branch_policy read the same captured stdin\n'
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 15: Tag-only push from main checkout (allowed) ===\n'
  (
    cd "$tmpdir" || exit 1
    git checkout -q main 2>/dev/null

    # Simulate git pre-push stdin for: git push origin v1.0.0
    # Tag-only push from main checkout should be allowed (administrative operation)
    local_sha=$(git rev-parse HEAD 2>/dev/null || echo "0000000")
    tag_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    # Stdin format: <local-ref> <local-sha> <remote-ref> <remote-sha>
    # For "git push origin v1.0.0": refs/tags/v1.0.0 <sha> refs/tags/v1.0.0 0000...
    printf '%s\n' "refs/tags/v1.0.0 $tag_sha refs/tags/v1.0.0 0000000000000000000000000000000000000000" | {
      if check_branch_policy >/dev/null 2>&1; then
        printf 'PASS: Tag-only push from main checkout allowed\n'
      else
        printf 'FAIL: Tag-only push should be allowed from main checkout\n'
        exit 1
      fi
    }
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 16: Mixed push (tags + main ref) blocked ===\n'
  (
    cd "$tmpdir" || exit 1
    git checkout -q feature/test 2>/dev/null || git checkout -q -b feature/mixed 2>/dev/null

    # Simulate git pre-push stdin for: git push origin v1.0.0 HEAD:main
    # Mixed push with tag + main ref should be blocked (contains non-tag, non-delete push)
    local_sha=$(git rev-parse HEAD 2>/dev/null || echo "0000000")
    tag_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    # Two tuples: one tag, one branch to main
    stdin_input="refs/tags/v1.0.0 $tag_sha refs/tags/v1.0.0 0000000000000000000000000000000000000000
refs/heads/feature/test $local_sha refs/heads/main 0000000000000000000000000000000000000000"

    printf '%s\n' "$stdin_input" | {
      if check_branch_policy >/dev/null 2>&1; then
        printf 'FAIL: Mixed push should be blocked when containing push to main\n'
        exit 1
      else
        printf 'PASS: Mixed push correctly blocked\n'
      fi
    }
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 17: check_claudemd_sync skipped when tool missing ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_no_claudemd"
    mkdir -p "$AESOP_ROOT"

    if check_claudemd_sync >/dev/null 2>&1; then
      printf 'PASS: check_claudemd_sync returns 0 (fail-open) when tool missing\n'
    else
      printf 'FAIL: check_claudemd_sync should fail-open when tool missing\n'
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 18: check_metrics skipped when tool missing ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_no_metrics"
    mkdir -p "$AESOP_ROOT"

    if check_metrics >/dev/null 2>&1; then
      printf 'PASS: check_metrics returns 0 (fail-open) when tool missing\n'
    else
      printf 'FAIL: check_metrics should fail-open when tool missing\n'
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test Results ===\n'
  printf 'PASSED: %d\n' "$test_passed"
  printf 'FAILED: %d\n' "$test_failed"

  if [ "$test_failed" -eq 0 ]; then
    printf '\nAll 18 tests passed.\n'
    return 0
  else
    printf '\nSome tests failed.\n'
    return 1
  fi
}

main() {
  if [ "${1:-}" = "--test" ]; then
    run_test_mode
    exit $?
  fi

  if [ "${1:-}" = "--verify-audit-log" ]; then
    local audit_log="${2:-${AESOP_ROOT:-$HOME/aesop}/state/SECURITY-AUDIT.log}"
    verify_audit_log "$audit_log"
    exit $?
  fi

  # Option B: Fail-closed TTY semantics — block interactive invocation before stdin capture.
  # git pre-push ALWAYS pipes stdin; tty means human ran hook directly (not via git push).
  # Refusing to skip security checks in interactive mode closes the window where empty stdin
  # could be misinterpreted as a legitimate up-to-date push.
  if [ -t 0 ]; then
    printf 'Error: interactive invocation: this hook runs under git push; refusing to skip security checks (fail-closed)\n' >&2
    log_block "interactive_invocation_blocked"
    exit 1
  fi

  # git pre-push provides ref info on stdin, and BOTH check_branch_policy and
  # check_secret_scan (via get_commit_range) need to read every ref tuple.
  # Capture the real pipe ONCE here and hand each consumer its own here-string
  # copy -- reading a pipe twice on the same fd starves the second reader
  # (once check_branch_policy drains it, get_commit_range would see nothing
  # but EOF and fail-closed on every push). A here-string preserves each
  # function's existing tty-vs-pipe read semantics unchanged.
  local prepush_stdin=""
  if [ ! -t 0 ]; then
    prepush_stdin=$(cat)
  fi

  if ! check_branch_policy <<< "$prepush_stdin"; then
    printf 'Error: Push to main/master is blocked by policy\n' >&2
    log_block "push_to_protected_branch"
    exit 1
  fi

  if ! check_secret_scan <<< "$prepush_stdin"; then
    printf 'Error: Secret scan failed. Push blocked.\n' >&2
    log_block "secret_scan_failure"
    exit 1
  fi

  if ! check_import_resolution; then
    printf 'Error: Python import resolution check failed. Push blocked.\n' >&2
    log_block "import_check_failure"
    exit 1
  fi

  if ! check_tracker_guard; then
    printf 'Error: Tracker zombie-resurrection gate failed. Push blocked.\n' >&2
    log_block "tracker_guard_failure"
    exit 1
  fi

  if ! check_claudemd_sync; then
    printf 'Error: CLAUDE.md synchronization gate failed. Push blocked.\n' >&2
    log_block "claudemd_sync_failure"
    exit 1
  fi

  if ! check_metrics; then
    printf 'Error: Metrics verification gate failed. Push blocked.\n' >&2
    log_block "metrics_gate_failure"
    exit 1
  fi

  if ! check_test_suite_count; then
    printf 'Error: Test suite count drift detected. Push blocked.\n' >&2
    log_block "test_suite_count_drift"
    exit 1
  fi

  if ! check_encoding_lint; then
    printf 'Error: Encoding lint check failed. Push blocked.\n' >&2
    log_block "encoding_lint_failure"
    exit 1
  fi

  if ! check_test_coverage; then
    printf 'Error: Test coverage check failed. Push blocked.\n' >&2
    log_block "test_coverage_failure"
    exit 1
  fi

  exit 0
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
