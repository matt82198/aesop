#!/usr/bin/env bash
set -uo pipefail

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  printf 'FATAL: pre-push-policy.sh requires python3 or python on PATH (guardrail G12: interpreter health check)\n' >&2
  printf 'Without Python, secret_scan and other tools cannot run. Push blocked.\n' >&2
  exit 1
fi

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

gate_tool_status() {
  # Classify a missing gate script: is the whole aesop toolchain absent, or is
  # this one gate gone from a repo that has the rest of it?
  #
  # Every gate used to treat both cases as "skip", so deleting, renaming, or
  # failing to ship a single gate script silently disabled that gate in the very
  # repo that owns it -- a push could go green having verified nothing. The hook
  # genuinely does install into repos without an aesop checkout, so the skip is
  # still needed; it is now conditioned on tools/ being absent as a whole rather
  # than on one file being missing.
  #
  # Executability is deliberately NOT required: gate scripts are run as
  # "$py_bin" "$script", so the exec bit is irrelevant, and demanding it turned
  # any checkout without exec bits into a silently ungated one.
  #
  # Prints one of: ok | skip | missing
  local aesop_root="$1"
  local script="$2"

  if [ -f "$script" ]; then
    printf 'ok'
    return 0
  fi
  if [ ! -d "$aesop_root/tools" ]; then
    printf 'skip'
    return 0
  fi
  printf 'missing'
}

gate_tool_missing_block() {
  # Shared fail-closed message for a gate script that vanished from a repo that
  # still has tools/. Cannot verify => deny.
  local label="$1"
  local script="$2"
  printf 'FATAL: %s not found at %s, but this repo has a tools/ directory.\n' "$label" "$script" >&2
  printf 'A gate that cannot run must not report success. Push blocked.\n' >&2
}

gate_no_python_block() {
  # Shared fail-closed message for a gate whose interpreter is unavailable.
  # The top-of-file guard already requires python, so reaching this means the
  # interpreter disappeared mid-run; either way, unverifiable => denied.
  local label="$1"
  printf 'FATAL: no python interpreter found; %s cannot run. Push blocked.\n' "$label" >&2
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
    # If remote_sha is all zeros (new branch), use merge-base with remote default branch
    if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
      # New branch: find merge-base with remote main/master, NOT local
      # (local main may be stale; remote is the authoritative ref)
      local base_sha=""

      # Try remote main first
      if git rev-parse "origin/main" >/dev/null 2>&1; then
        base_sha=$(git merge-base "$local_sha" "origin/main" 2>/dev/null || echo "")
      fi

      # Fall back to remote master
      if [ -z "$base_sha" ] && git rev-parse "origin/master" >/dev/null 2>&1; then
        base_sha=$(git merge-base "$local_sha" "origin/master" 2>/dev/null || echo "")
      fi

      # Fall back to local main
      if [ -z "$base_sha" ] && git rev-parse "main" >/dev/null 2>&1; then
        base_sha=$(git merge-base "$local_sha" "main" 2>/dev/null || echo "")
      fi

      # Last resort: local master
      if [ -z "$base_sha" ] && git rev-parse "master" >/dev/null 2>&1; then
        base_sha=$(git merge-base "$local_sha" "master" 2>/dev/null || echo "")
      fi

      # If no base found, use all-zeros (will scan all commits)
      if [ -z "$base_sha" ]; then
        base_sha="0000000000000000000000000000000000000000"
      fi

      printf '%s..%s\n' "$base_sha" "$local_sha"
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

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$guard_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "tracker_guard_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "tracker_guard.py" "$guard_script"
    log_event "tracker_guard_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "tracker guard"
    log_event "tracker_guard_no_python"
    return 1
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
  # AST-parses the pushed .py files, resolves each import against repo
  # structure and sys.stdlib_module_names. Fail-closed if any cannot resolve.
  #
  # VACUITY FIX (guard/g5-import-check-actually-runs): this used to invoke the
  # tool with NO arguments, so it evaluated `git diff --cached`. A pre-push hook
  # runs AFTER the commit -- the index is EMPTY -- so the gate printed "No
  # staged Python files found" and exited 0 on EVERY normal push. It had never
  # actually run. It now evaluates the files ACTUALLY BEING PUSHED.
  #
  # The file-list mechanism is REUSED, not reinvented: get_commit_range()
  # already parses git pre-push stdin into one "remote-sha..local-sha" range per
  # ref tuple, and check_secret_scan() already consumes it exactly this way
  # (multi-ref loop, rc=2 delete-only, rc=3 empty-stdin, rc=1 malformed =>
  # fail-closed). Same parser, same loop shape, same semantics.
  #
  # Fail-open ONLY for missing optional tool (repo without aesop checkout);
  # actual resolution failures and malformed stdin stay fail-closed.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local import_check_script="$aesop_root/tools/import_resolution_check.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$import_check_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "import_check_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "import_resolution_check.py" "$import_check_script"
    log_event "import_check_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "import resolution check"
    log_event "import_check_no_python"
    return 1
  fi

  local commit_ranges
  commit_ranges=$(get_commit_range)
  local parse_exit_code=$?

  if [ $parse_exit_code -eq 2 ]; then
    # Delete-only push: no content pushed, nothing to check.
    log_event "import_check_skipped_delete_only_push"
    return 0
  fi

  if [ $parse_exit_code -eq 3 ]; then
    # Empty stdin: no tuples at all (e.g. up-to-date push), nothing to check.
    log_event "import_check_skipped_empty_stdin"
    return 0
  fi

  if [ $parse_exit_code -ne 0 ] || [ -z "$commit_ranges" ]; then
    # Malformed stdin: fail-CLOSED (never silently degrade to "no files").
    log_block "import_check_stdin_parse_failed"
    printf 'Error: Could not parse pre-push stdin for commit range (import resolution check)\n' >&2
    return 1
  fi

  local overall_exit_code=0
  local range
  while IFS= read -r range || [ -n "$range" ]; do
    [ -z "$range" ] && continue

    local check_output
    check_output=$("$py_bin" "$import_check_script" --range "$range" 2>&1)
    local check_exit_code=$?

    if [ $check_exit_code -ne 0 ]; then
      if [ -n "$check_output" ]; then
        printf '%s\n' "$check_output" >&2
      fi
      overall_exit_code=$check_exit_code
    fi
  done <<< "$commit_ranges"

  return $overall_exit_code
}

check_conflict_markers() {
  # Literal conflict-marker gate (tools/conflict_marker_check.py --staged).
  # Catches a clean-merge landing a literal <<<<<<<< / ======= / >>>>>>>>
  # block in a tracked text file (PR #834 incident, merge commit b2c4db77 --
  # claudemd_lint, claudemd_sync_gate, and all PR CI passed on it because none
  # of them scan for literal conflict markers).
  #
  # Same file-list mechanism as check_secret_scan/check_import_resolution:
  # get_commit_range() parses pre-push stdin into one "remote-sha..local-sha"
  # range per ref tuple; this scans only ADDED lines in that range's diff
  # (not the whole tree -- the full-tree sweep is CI's job), so it catches a
  # marker introduced BY this push.
  #
  # Fail-open ONLY for missing optional tooling; an actual marker finding and
  # malformed stdin stay fail-closed.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local marker_script="$aesop_root/tools/conflict_marker_check.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$marker_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "conflict_marker_check_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "conflict_marker_check.py" "$marker_script"
    log_event "conflict_marker_check_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "conflict marker check"
    log_event "conflict_marker_check_no_python"
    return 1
  fi

  local commit_ranges
  commit_ranges=$(get_commit_range)
  local parse_exit_code=$?

  if [ $parse_exit_code -eq 2 ]; then
    # Delete-only push: no content pushed, nothing to scan.
    log_event "conflict_marker_check_skipped_delete_only_push"
    return 0
  fi

  if [ $parse_exit_code -eq 3 ]; then
    # Empty stdin: no tuples at all (e.g. up-to-date push), nothing to scan.
    log_event "conflict_marker_check_skipped_empty_stdin"
    return 0
  fi

  if [ $parse_exit_code -ne 0 ] || [ -z "$commit_ranges" ]; then
    # Malformed stdin: fail-CLOSED (never silently degrade to "no files").
    log_block "conflict_marker_check_stdin_parse_failed"
    printf 'Error: Could not parse pre-push stdin for commit range (conflict marker check)\n' >&2
    return 1
  fi

  local overall_exit_code=0
  local range
  while IFS= read -r range || [ -n "$range" ]; do
    [ -z "$range" ] && continue

    local check_output
    check_output=$("$py_bin" "$marker_script" --staged "$range" 2>&1)
    local check_exit_code=$?

    if [ $check_exit_code -ne 0 ]; then
      if [ -n "$check_output" ]; then
        printf '%s\n' "$check_output" >&2
      fi
      overall_exit_code=$check_exit_code
    fi
  done <<< "$commit_ranges"

  return $overall_exit_code
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

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$sync_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "claudemd_sync_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "claudemd_sync_gate.py" "$sync_script"
    log_event "claudemd_sync_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "CLAUDE.md sync gate"
    log_event "claudemd_sync_no_python"
    return 1
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

check_gen_tool_index() {
  # Tool index synchronization gate (tools/gen_tool_index.py --check).
  # Ensures tools/INDEX.md stays in sync with per-tool INDEX: docstring lines.
  # Any new tool without an INDEX: line fails closed to prevent undocumented tools.
  #
  # Fail-open for missing optional tooling; fail-closed for actual drift or missing INDEX lines.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local index_script="$aesop_root/tools/gen_tool_index.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$index_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "gen_tool_index_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "gen_tool_index.py" "$index_script"
    log_event "gen_tool_index_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "tool index gate"
    log_event "gen_tool_index_no_python"
    return 1
  fi

  local index_output
  index_output=$("$py_bin" "$index_script" --check 2>&1)
  local index_exit_code=$?

  if [ $index_exit_code -ne 0 ]; then
    if [ -n "$index_output" ]; then
      printf '%s\n' "$index_output" >&2
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

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$metrics_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "metrics_gate_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "metrics_gate.py" "$metrics_script"
    log_event "metrics_gate_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "metrics gate"
    log_event "metrics_gate_no_python"
    return 1
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
  # CI-shard-coverage gate (tools/verify_test_suite_count.py --check).
  # Verifies the CI workflow's shard matrix (.github/workflows/ci.yml) actually
  # covers every git-tracked tests/test_*.py file -- a gap there means some
  # shard index never runs in CI, so test files assigned to it are silently
  # never executed. Counts are computed live (PR #830 removed the committed
  # tests/SUITE-COUNTS.json this gate used to compare against); this gate is
  # wired here (not just in CI) because CI only runs after push; a local
  # pre-push check catches a shard-matrix gap immediately.
  #
  # Fail-open ONLY for missing optional tooling (hook is installed into
  # repos without an aesop checkout; no aesop install == no verify tool).
  # An actual gap (exit 1 from --check) stays fail-closed and blocks the push.
  # verify_test_suite_count exits 0 when fully covered (or N/A for a repo with
  # no shard matrix), 1 on a coverage gap, 2 if it cannot evaluate.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local verify_script="$aesop_root/tools/verify_test_suite_count.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$verify_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "test_suite_count_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "verify_test_suite_count.py" "$verify_script"
    log_event "test_suite_count_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "test suite count check"
    log_event "test_suite_count_no_python"
    return 1
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

check_claudemd_headroom() {
  # CLAUDE.md merge-union cap gate (tools/claudemd_lint.py --headroom).
  #
  # The working-tree line-cap check only ever sees the BRANCH. A branch can sit
  # at 149/150 and pass while origin/main independently grew, so the merge lands
  # at 151 and busts the cap on main with nothing red on the way in (three such
  # cascades in one day). This previews the merge against origin/main and lints
  # the UNION's line count, catching the cascade before the push.
  #
  # Tool exit contract: 0=clean, 1=a union busts its cap (fail-CLOSED, push
  # blocked), 2=merge union UNREADABLE. Exit 2 is an environment condition (no
  # origin/main fetched yet, shallow clone, un-previewable merge), not a policy
  # violation, so it fails OPEN with an audit event -- the same philosophy as the
  # missing-tool fail-open below.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local lint_script="$aesop_root/tools/claudemd_lint.py"

  if [ ! -f "$lint_script" ]; then
    log_event "claudemd_headroom_skipped_tool_missing"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    printf 'Warning: no python interpreter found; CLAUDE.md headroom gate skipped\n' >&2
    log_event "claudemd_headroom_skipped_no_python"
    return 0
  fi

  local base_ref="${AESOP_HEADROOM_BASE_REF:-origin/main}"
  local headroom_output
  headroom_output=$("$py_bin" "$lint_script" --root "$aesop_root" --headroom --base-ref "$base_ref" 2>&1)
  local headroom_exit_code=$?

  if [ $headroom_exit_code -eq 2 ]; then
    if [ -n "$headroom_output" ]; then
      printf '%s\n' "$headroom_output" >&2
    fi
    log_event "claudemd_headroom_skipped_unreadable"
    return 0
  fi

  if [ $headroom_exit_code -ne 0 ]; then
    if [ -n "$headroom_output" ]; then
      printf '%s\n' "$headroom_output" >&2
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
  # Skips only when there is no aesop checkout; fail-closed when tools/ exists
  # but this gate's script does not, and on actual findings.
  #
  # resolve_aesop_root(), not $HOME/aesop: the hardcoded fallback ran the
  # primary tree's script when pushing from a worktree, and silently skipped
  # the gate entirely on any machine without ~/aesop.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local lint_script="$aesop_root/tools/encoding_lint.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$lint_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "encoding_lint_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "encoding_lint.py" "$lint_script"
    log_event "encoding_lint_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "encoding lint"
    log_event "encoding_lint_no_python"
    return 1
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
  # Skips only when there is no aesop checkout; fail-closed when tools/ exists
  # but this gate's script does not, and on actual findings.
  # resolve_aesop_root(), not $HOME/aesop -- see check_encoding_lint.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local coverage_script="$aesop_root/tools/verify_test_coverage.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$coverage_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "test_coverage_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "verify_test_coverage.py" "$coverage_script"
    log_event "test_coverage_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "test coverage check"
    log_event "test_coverage_no_python"
    return 1
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

check_linux_shape() {
  # Linux shape checker: run shell/Node tests under WSL to catch platform-specific
  # failures before push (e.g., isolated-home USERPROFILE assumption, shell test
  # failures on Ubuntu). Detects changes to *.sh, hooks/*, .github/workflows/*.yml,
  # and tests/**/*.test.mjs, then runs owning suites under WSL.
  #
  # Skips only when there is no aesop checkout; fail-closed when tools/ exists
  # but this gate's script does not.
  #
  # Returns 0 (skip) if:
  #   - No aesop checkout (tools/ absent)
  #   - No shell/workflow/node changes detected
  #   - WSL unavailable (unless AESOP_REQUIRE_LINUX_SHAPE=1)
  # Returns 1 (fail) if:
  #   - Gate script missing from aesop repo
  #   - Python unavailable
  #   - WSL tests fail
  #   - AESOP_REQUIRE_LINUX_SHAPE=1 and WSL unavailable
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local shape_script="$aesop_root/tools/linux_shape_check.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$shape_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "linux_shape_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "linux_shape_check.py" "$shape_script"
    log_event "linux_shape_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "linux shape check"
    log_event "linux_shape_no_python"
    return 1
  fi

  # Get commit range from pre-push stdin (same as secret_scan, import_resolution)
  local commit_ranges
  commit_ranges=$(get_commit_range)
  local range_exit_code=$?
  if [ $range_exit_code -ne 0 ] || [ -z "$commit_ranges" ]; then
    # Delete-only / empty push: no content to check
    log_event "linux_shape_skipped_delete_only"
    return 0
  fi

  # Run the linux shape checker against the commit range
  local shape_output
  shape_output=$("$py_bin" "$shape_script" --range "$commit_ranges" 2>&1)
  local shape_exit_code=$?

  if [ $shape_exit_code -ne 0 ]; then
    if [ -n "$shape_output" ]; then
      printf '%s\n' "$shape_output" >&2
    fi
    return 1
  fi

  return 0
}

check_generated_paths() {
  # Generated-path registry gate (tools/generated_paths.py --check).
  # Machine-generated files have exactly ONE legitimate writer -- their
  # generator. A hand edit is silently reverted on the next regeneration AND
  # collides with every concurrent lane that regenerates the same file, which
  # is the contended-file conflict class this gate exists to kill.
  #
  # Reads the pushed ref tuples from stdin (same capture main() feeds every
  # other stdin consumer), turns each range into its changed-path list, and
  # hands the union to the registry. Paths go over stdin, not argv, so a large
  # diff cannot blow the command-line length limit.
  #
  # Escape hatch: AESOP_ALLOW_GENERATED=1 (honored inside the tool) is the
  # DESIGNED writer path for generators/daemon regeneration pushes, not a
  # weakening -- an ordinary push never sets it.
  #
  # Fail-open ONLY for missing optional tooling / no python (the hook installs
  # into repos without an aesop checkout). Malformed or unparseable stdin is
  # already fail-closed by check_secret_scan, which runs first; here it simply
  # means there is no diff to classify.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local gen_script="$aesop_root/tools/generated_paths.py"

  if [ ! -f "$gen_script" ]; then
    log_event "generated_paths_skipped_tool_missing"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    printf 'Warning: no python interpreter found; generated-path gate skipped\n' >&2
    log_event "generated_paths_skipped_no_python"
    return 0
  fi

  local commit_ranges
  commit_ranges=$(get_commit_range)
  local range_exit_code=$?
  if [ $range_exit_code -ne 0 ] || [ -z "$commit_ranges" ]; then
    # Delete-only / empty / malformed: no content diff to classify here.
    return 0
  fi

  local changed=""
  local range
  while IFS= read -r range || [ -n "$range" ]; do
    [ -z "$range" ] && continue
    local range_paths
    range_paths=$(git diff --name-only "$range" 2>/dev/null)
    if [ -n "$range_paths" ]; then
      changed="${changed}${range_paths}"$'\n'
    fi
  done <<< "$commit_ranges"

  if [ -z "$(printf '%s' "$changed" | tr -d '[:space:]')" ]; then
    return 0
  fi

  local gen_output
  gen_output=$(printf '%s\n' "$changed" | "$py_bin" "$gen_script" --check 2>&1)
  local gen_exit_code=$?

  if [ $gen_exit_code -ne 0 ]; then
    if [ -n "$gen_output" ]; then
      printf '%s\n' "$gen_output" >&2
    fi
    return 1
  fi

  return 0
}

ensure_merge_drivers() {
  # Register aesop's custom git merge drivers (tools/install_merge_drivers.py)
  # in this clone's config, idempotently. `.gitattributes` names the drivers
  # but git never reads a driver COMMAND out of the repository, so each clone
  # registers once; doing it here means no clone that pushes can forget.
  # Linked worktrees share the config. Fail-open: registration is a
  # convenience, the byte-identity gate below is the enforcement.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local install_script="$aesop_root/tools/install_merge_drivers.py"
  if [ ! -f "$install_script" ]; then
    return 0
  fi
  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    return 0
  fi
  if ! "$py_bin" "$install_script" --quiet >/dev/null 2>&1; then
    log_event "merge_driver_registration_failed"
  fi
  return 0
}

check_generated_regen() {
  # Generated-artifact byte-identity gate on COMMITTED content
  # (tools/generated_push_gate.py). check_gen_tool_index() above verifies the
  # WORKING TREE; CI verifies the pushed COMMIT. The two differ exactly when a
  # lane regenerated but did not commit, or when a merge-from-main commit
  # carries a stale union of tools/INDEX.md (#784, #856, #739). For every
  # pushed range that touches a registered regenerable path -- or whenever the
  # merge driver left its `.needs-regen` stamp -- the tool checks the tip out
  # in a throwaway worktree, runs the registered generator there, and fails
  # with the exact one-line repair instruction when the bytes differ.
  #
  # No escape hatch: a stale generated artifact is never a legitimate write.
  # Fail-open only when there is no aesop checkout; fail-closed when tools/
  # exists but the gate script or python is missing.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local gate_script="$aesop_root/tools/generated_push_gate.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$gate_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "generated_regen_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "generated_push_gate.py" "$gate_script"
    log_event "generated_regen_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "generated-artifact gate"
    log_event "generated_regen_no_python"
    return 1
  fi

  local commit_ranges
  commit_ranges=$(get_commit_range)
  local range_exit_code=$?
  if [ $range_exit_code -ne 0 ] || [ -z "$commit_ranges" ]; then
    # Delete-only / empty / malformed: nothing pushed to verify.
    return 0
  fi

  local range_args=()
  local range
  while IFS= read -r range || [ -n "$range" ]; do
    [ -z "$range" ] && continue
    range_args+=(--range "$range")
  done <<< "$commit_ranges"
  if [ ${#range_args[@]} -eq 0 ]; then
    return 0
  fi

  local gate_output
  gate_output=$("$py_bin" "$gate_script" "${range_args[@]}" 2>&1)
  local gate_exit_code=$?
  if [ $gate_exit_code -ne 0 ]; then
    if [ -n "$gate_output" ]; then
      printf '%s\n' "$gate_output" >&2
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

  # Tests 19-22: a repo that HAS tools/ but is missing one gate script must be
  # blocked, not skipped. Tests 17-18 above cover the legitimate skip (no aesop
  # checkout at all); these cover the escape that skip used to hide -- deleting
  # or renaming a gate script silently disabled it and the push still went green.
  printf '\n=== Test 19: check_claudemd_sync BLOCKS when tools/ exists but gate script is gone ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_tools_no_claudemd"
    mkdir -p "$AESOP_ROOT/tools"

    if check_claudemd_sync >/dev/null 2>&1; then
      printf 'FAIL: check_claudemd_sync fail-opened despite tools/ being present\n'
      exit 1
    else
      printf 'PASS: check_claudemd_sync returns 1 (fail-closed) when its script is missing from tools/\n'
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 20: check_metrics BLOCKS when tools/ exists but gate script is gone ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_tools_no_metrics"
    mkdir -p "$AESOP_ROOT/tools"

    if check_metrics >/dev/null 2>&1; then
      printf 'FAIL: check_metrics fail-opened despite tools/ being present\n'
      exit 1
    else
      printf 'PASS: check_metrics returns 1 (fail-closed) when its script is missing from tools/\n'
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 21: check_tracker_guard and check_import_resolution both fail closed ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_tools_empty"
    mkdir -p "$AESOP_ROOT/tools"

    if check_tracker_guard >/dev/null 2>&1; then
      printf 'FAIL: check_tracker_guard fail-opened despite tools/ being present\n'
      exit 1
    fi
    if check_import_resolution >/dev/null 2>&1; then
      printf 'FAIL: check_import_resolution fail-opened despite tools/ being present\n'
      exit 1
    fi
    printf 'PASS: both gates return 1 when their scripts are missing from tools/\n'
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 22: a gate script present but non-executable still RUNS ===\n'
  (
    # Gates are invoked as "$py_bin" "$script", so the exec bit is irrelevant.
    # Requiring -x turned any checkout without exec bits into a silent skip.
    export AESOP_ROOT="$tmpdir/aesop_tools_noexec"
    mkdir -p "$AESOP_ROOT/tools"
    printf 'import sys\nsys.exit(1)\n' > "$AESOP_ROOT/tools/metrics_gate.py"
    chmod -x "$AESOP_ROOT/tools/metrics_gate.py" 2>/dev/null

    if check_metrics >/dev/null 2>&1; then
      printf 'FAIL: non-executable gate script was skipped instead of run\n'
      exit 1
    else
      printf 'PASS: non-executable gate script ran and its exit 1 blocked the push\n'
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 23: check_claudemd_headroom exit contract (missing tool / unreadable / bust) ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_headroom"
    mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"

    # 23a: tool absent -> fail-open (hook installs into repos without an aesop checkout)
    if ! check_claudemd_headroom >/dev/null 2>&1; then
      printf 'FAIL: check_claudemd_headroom should fail-open when tool missing\n'
      exit 1
    fi

    # 23b: tool reports exit 2 (merge union UNREADABLE) -> fail-open, not a policy block
    cat > "$AESOP_ROOT/tools/claudemd_lint.py" <<'HEADROOM_UNREADABLE'
#!/usr/bin/env python3
import sys
print("Error: merge union unreadable: ref 'origin/main' does not resolve", file=sys.stderr)
sys.exit(2)
HEADROOM_UNREADABLE
    if ! check_claudemd_headroom >/dev/null 2>&1; then
      printf 'FAIL: exit 2 (unreadable) must fail-open, not block the push\n'
      exit 1
    fi

    # 23c: tool reports exit 1 (a union busts its cap) -> fail-CLOSED
    cat > "$AESOP_ROOT/tools/claudemd_lint.py" <<'HEADROOM_BUST'
#!/usr/bin/env python3
import sys
print("1. [headroom-line-count] tools/CLAUDE.md: merge union is 151 lines, exceeds max 150")
sys.exit(1)
HEADROOM_BUST
    if check_claudemd_headroom >/dev/null 2>&1; then
      printf 'FAIL: exit 1 (union busts cap) must fail-closed and block the push\n'
      exit 1
    fi

    printf 'PASS: headroom gate fails open on missing tool + unreadable, fails closed on a busted union\n'
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 24: check_generated_paths blocks a registered generated path ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_genpaths"
    mkdir -p "$AESOP_ROOT/state" "$AESOP_ROOT/tools"
    # Stand-in registry tool: exits 1 iff tools/INDEX.md appears on stdin. The
    # real tool is exercised by tests/test_generated_paths.py; this proves the
    # HOOK collects the diff and propagates the tool's exit code.
    cat > "$AESOP_ROOT/tools/generated_paths.py" <<'GENPATHS'
#!/usr/bin/env python3
import sys
paths = sys.stdin.read().split()
sys.exit(1 if "tools/INDEX.md" in paths else 0)
GENPATHS

    REPO="$tmpdir/genpaths_repo"
    mkdir -p "$REPO/tools"
    cd "$REPO" || exit 1
    git init -q
    git config user.email "test@example.com"
    git config user.name "Test User"
    printf 'seed\n' > seed.txt
    git add seed.txt
    git commit -q -m "base"
    base_sha=$(git rev-parse HEAD)
    printf 'generated\n' > tools/INDEX.md
    git add tools/INDEX.md
    git commit -q -m "hand-edit a generated file"
    head_sha=$(git rev-parse HEAD)

    tuple="refs/heads/feature/x $head_sha refs/heads/feature/x $base_sha"
    if printf '%s\n' "$tuple" | check_generated_paths >/dev/null 2>&1; then
      printf 'FAIL: should have blocked a push touching a registered generated path\n'
      exit 1
    fi
    printf 'PASS: push touching a registered generated path blocked\n'
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 25: check_generated_paths passes on ordinary paths ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_genpaths"

    REPO="$tmpdir/genpaths_repo_clean"
    mkdir -p "$REPO/tools"
    cd "$REPO" || exit 1
    git init -q
    git config user.email "test@example.com"
    git config user.name "Test User"
    printf 'seed\n' > seed.txt
    git add seed.txt
    git commit -q -m "base"
    base_sha=$(git rev-parse HEAD)
    printf 'authored\n' > tools/regular_tool.py
    git add tools/regular_tool.py
    git commit -q -m "ordinary change"
    head_sha=$(git rev-parse HEAD)

    tuple="refs/heads/feature/y $head_sha refs/heads/feature/y $base_sha"
    if printf '%s\n' "$tuple" | check_generated_paths >/dev/null 2>&1; then
      printf 'PASS: ordinary push allowed through the generated-path gate\n'
    else
      printf 'FAIL: ordinary push should not be blocked\n'
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 26: check_generated_paths skipped when tool missing ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_no_genpaths"
    mkdir -p "$AESOP_ROOT"

    if printf '' | check_generated_paths >/dev/null 2>&1; then
      printf 'PASS: check_generated_paths returns 0 (fail-open) when tool missing\n'
    else
      printf 'FAIL: check_generated_paths should fail-open when tool missing\n'
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 27: get_commit_range uses origin/main for new branches, not stale local main ===\n'
  (
    local fixture_tmpdir
    fixture_tmpdir=$(mktemp -d)
    trap "rm -rf '$fixture_tmpdir'" EXIT

    cd "$fixture_tmpdir" || exit 1

    # Create a bare "origin" repo
    origin_repo="$fixture_tmpdir/origin.git"
    mkdir -p "$origin_repo"
    cd "$origin_repo" || exit 1
    git init -q --bare

    # Create a working repo that pushes to origin
    work_repo="$fixture_tmpdir/work"
    git clone -q "$origin_repo" "$work_repo"
    cd "$work_repo" || exit 1

    # Create initial commit on main
    git config user.email "test@example.com"
    git config user.name "Test User"
    echo "initial" > file.txt
    git add file.txt
    git commit -q -m "initial commit"
    git branch -M main
    git push -q -u origin main

    # Create several commits on origin/main
    for i in 1 2 3; do
      echo "origin commit $i" >> file.txt
      git add file.txt
      git commit -q -m "origin commit $i"
      git push -q origin main
    done

    # Fetch all the new commits from origin
    git fetch -q origin

    # Create a new branch from origin/main (the actual main tip)
    git checkout -q origin/main
    git checkout -q -b feature/new-branch

    # Add a commit on the feature branch
    echo "feature commit" >> file.txt
    git add file.txt
    git commit -q -m "feature commit on new branch"

    # Now reset local main to point back to the initial commit (simulating stale state)
    git checkout -q main
    git reset -q --hard HEAD~3

    # Verify local main is behind origin/main
    commits_behind=$(git rev-list --count main..origin/main)
    if [ "$commits_behind" -lt 3 ]; then
      printf 'FAIL: Setup failed; local main should be 3+ commits behind origin/main\n'
      exit 1
    fi

    # Simulate the git pre-push stdin for pushing a new branch
    # Format: <local-ref> <local-sha> <remote-ref> <remote-sha>
    local_ref="refs/heads/feature/new-branch"
    local_sha=$(git rev-parse feature/new-branch)
    remote_ref="refs/heads/feature/new-branch"
    remote_sha="0000000000000000000000000000000000000000"  # New branch (all zeros)

    prepush_stdin="$local_ref $local_sha $remote_ref $remote_sha"

    # Call get_commit_range with the simulated stdin
    range=$(get_commit_range <<< "$prepush_stdin" || true)

    # Parse the range and verify it uses origin/main, not local main
    if [[ $range =~ ^([^.]+)\.\.([^.]+)$ ]]; then
      base_sha="${BASH_REMATCH[1]}"
      tip_sha="${BASH_REMATCH[2]}"

      # Count commits in the range
      commit_count=$(git rev-list --count "$base_sha..$tip_sha" 2>/dev/null || echo "ERROR")

      # Should be exactly 1 commit (the feature commit on the new branch)
      # NOT 4+ commits (if using stale local main)
      if [ "$commit_count" = "1" ]; then
        printf 'PASS: get_commit_range correctly uses origin/main (1 commit in range)\n'
      else
        printf 'FAIL: get_commit_range should find 1 commit, found %s\n' "$commit_count"
        printf '  Computed range: %s\n' "$range"
        exit 1
      fi
    else
      printf 'FAIL: Could not parse range: %s\n' "$range"
      exit 1
    fi
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 28: check_conflict_markers skipped when tool missing, BLOCKS on a real marker ===\n'
  (
    export AESOP_ROOT="$tmpdir/aesop_no_conflict_check"
    mkdir -p "$AESOP_ROOT"

    if ! printf '' | check_conflict_markers >/dev/null 2>&1; then
      printf 'FAIL: check_conflict_markers should fail-open (return 0) when tool missing\n'
      exit 1
    fi

    real_repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

    marker_repo="$tmpdir/conflict_marker_repo"
    rm -rf "$marker_repo"
    mkdir -p "$marker_repo/tools"
    cp "$real_repo_root/tools/conflict_marker_check.py" "$marker_repo/tools/conflict_marker_check.py"
    git init -q "$marker_repo"
    git -C "$marker_repo" config user.email "test@example.com"
    git -C "$marker_repo" config user.name "Test User"
    printf 'clean\n' > "$marker_repo/file.md"
    git -C "$marker_repo" add -A
    git -C "$marker_repo" commit -q -m base
    base_sha=$(git -C "$marker_repo" rev-parse HEAD)
    printf 'clean\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\n' > "$marker_repo/file.md"
    git -C "$marker_repo" add -A
    git -C "$marker_repo" commit -q -m "introduces marker"
    tip_sha=$(git -C "$marker_repo" rev-parse HEAD)

    (
      cd "$marker_repo" || exit 1
      unset AESOP_ROOT
      stdin_line="$tip_sha $tip_sha refs/heads/feature $base_sha"
      if printf '%s\n' "$stdin_line" | check_conflict_markers >/dev/null 2>&1; then
        printf 'FAIL: check_conflict_markers should BLOCK a push introducing a literal marker\n'
        exit 1
      fi
      printf 'PASS: check_conflict_markers fail-opens on missing tool and blocks a real marker\n'
    )
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
    printf '\nAll 28 tests passed.\n'
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
    local aesop_root
    aesop_root=$(resolve_aesop_root)
    local audit_log="${2:-$aesop_root/state/SECURITY-AUDIT.log}"
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

  # Feed the SAME captured stdin: this gate reads the pushed ref range via
  # get_commit_range(), exactly like check_secret_scan above. Calling it without
  # stdin is what made it vacuously green (empty index at push time).
  if ! check_import_resolution <<< "$prepush_stdin"; then
    printf 'Error: Python import resolution check failed. Push blocked.\n' >&2
    log_block "import_check_failure"
    exit 1
  fi

  # Same captured stdin, same reason as above: reads the pushed ref range.
  if ! check_conflict_markers <<< "$prepush_stdin"; then
    printf 'Error: Literal conflict marker found in pushed content. Push blocked.\n' >&2
    log_block "conflict_marker_check_failure"
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

  if ! check_gen_tool_index; then
    printf 'Error: Tool index synchronization gate failed. Push blocked.\n' >&2
    log_block "gen_tool_index_failure"
    exit 1
  fi

  if ! check_metrics; then
    printf 'Error: Metrics verification gate failed. Push blocked.\n' >&2
    log_block "metrics_gate_failure"
    exit 1
  fi

  if ! check_claudemd_headroom; then
    printf 'Error: CLAUDE.md merge-union line cap busted. Push blocked.\n' >&2
    log_block "claudemd_headroom_failure"
    exit 1
  fi

  if ! check_test_suite_count; then
    printf 'Error: CI shard matrix would silently drop tracked test file(s). Push blocked.\n' >&2
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

  if ! check_generated_paths <<< "$prepush_stdin"; then
    printf 'Error: Push touches a machine-generated path. Push blocked.\n' >&2
    log_block "generated_path_hand_edit"
    exit 1
  fi

  ensure_merge_drivers

  # Same captured stdin: verifies the pushed TIP's generated artifacts against
  # their generators (committed bytes, not the working tree).
  if ! check_generated_regen <<< "$prepush_stdin"; then
    printf 'Error: A generated artifact is stale in the pushed commit. Push blocked.\n' >&2
    log_block "generated_artifact_stale"
    exit 1
  fi

  if ! check_linux_shape; then
    printf 'Error: Linux shape check failed. Push blocked.\n' >&2
    log_block "linux_shape_check_failure"
    exit 1
  fi

  exit 0
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
