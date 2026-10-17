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
  #
  # Each candidate is PROBED with a bounded `timeout 5` run, not just looked
  # up on PATH: a python3 that is present but blocks (a stub that sleeps, a
  # wedged shim) would otherwise hang every gate and turn a push into a
  # multi-minute hang. A candidate that fails or times out is skipped with a
  # WARN line; candidate order is unchanged. If no `timeout` binary exists the
  # probe runs unbounded and one WARN says so.
  #
  # main() resolves ONCE and stores the result in _AESOP_PY_BIN; subshells
  # inherit it, so the probe cost is paid once per push, not once per gate.
  if [ -n "${_AESOP_PY_BIN:-}" ]; then
    printf '%s' "$_AESOP_PY_BIN"
    return 0
  fi
  local have_timeout=""
  if command -v timeout >/dev/null 2>&1 && timeout --version >/dev/null 2>&1; then
    have_timeout="1"
  else
    printf 'WARN: timeout binary not found; python interpreter probe is unbounded\n' >&2
  fi
  local c rc
  for c in python3 python; do
    if ! command -v "$c" >/dev/null 2>&1; then
      continue
    fi
    if [ -n "$have_timeout" ]; then
      timeout 5 "$c" -c "import sys" </dev/null >/dev/null 2>&1
    else
      "$c" -c "import sys" </dev/null >/dev/null 2>&1
    fi
    rc=$?
    if [ "$rc" -eq 0 ]; then
      printf '%s' "$c"
      return 0
    fi
    printf "WARN: python candidate '%s' unusable (rc=%s); trying next\n" "$c" "$rc" >&2
  done
  printf 'Error: no usable python interpreter; push blocked.\n' >&2
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

# Trunk ref on origin: origin/HEAD's target, else origin/main, else origin/master.
# Prints the ref and returns 0; prints nothing and returns 1 when origin has no trunk
# yet (a fresh remote), which is the BOOTSTRAP condition.
gate_trunk_ref() {
  local head_ref name
  head_ref=$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null) || head_ref=""
  if [ -n "$head_ref" ]; then
    name="origin/${head_ref#refs/remotes/origin/}"
    if git rev-parse --verify -q "$name^{commit}" >/dev/null 2>&1; then
      printf '%s\n' "$name"
      return 0
    fi
  fi
  for name in origin/main origin/master; do
    if git rev-parse --verify -q "$name^{commit}" >/dev/null 2>&1; then
      printf '%s\n' "$name"
      return 0
    fi
  done
  return 1
}

# Artifacts a bootstrap push must find committed at HEAD (the hook reads them).
GATE_BOOTSTRAP_REQUIRED="tools/INDEX.md .pyflakes-baseline.json .subprocess-guard-baseline.json"

# The regenerate command for a push-gate artifact (same strings scaffold and doctor print).
gate_regen_command() {
  case "$1" in
    tools/INDEX.md) printf '%s' "python tools/gen_tool_index.py --regenerate" ;;
    .pyflakes-baseline.json) printf '%s' "python tools/pyflakes_gate.py --root . --baseline .pyflakes-baseline.json --update-baseline" ;;
    .subprocess-guard-baseline.json) printf '%s' "python tools/subprocess_guard.py --baseline .subprocess-guard-baseline.json --update-baseline" ;;
    .portability-baseline.json) printf '%s' "python tools/portability_check.py --root . --baseline .portability-baseline.json --update-baseline" ;;
    .stateapi-baseline.json) printf '%s' "python tools/stateapi_lint.py --root . --baseline .stateapi-baseline.json --update-baseline" ;;
    *) printf '%s' "see tools/doctor.js" ;;
  esac
}

gate_base_dir() {
  # Prints a directory holding the trunk's tools/ and .pyflakes-baseline.json:
  # the BASE gate copies. Every gate script this hook runs comes from here, never
  # from the pushed tree, so a branch that weakens a gate cannot judge its own
  # push (F-04 / adv-enforcement F-03). Gate DATA roots (--root) still point at
  # the pushed tree; only gate code and gate baselines are pinned to base.
  # Content-addressed by the base commit, so one base is extracted once.
  # The directory name carries a cache-format version (v2): v1 extractions held
  # only tools/ and are never reused, because the base gates import state_store/
  # too (tools/tracker_guard.py -> state_store.read_api) and a v1 copy fails
  # with ModuleNotFoundError on every push.
  # Every root package the base gates import is extracted: tools/ always, and
  # state_store/ whenever origin/main carries it (state_store/ itself imports
  # tools.*, which the same archive already provides).
  # The base is the trunk on origin (gate_trunk_ref), never a hardcoded name. On a
  # fresh remote with no trunk yet, the base is HEAD (BOOTSTRAP): every gate copy and
  # baseline then comes from the local committed tree, and every bootstrap-required
  # artifact must be committed at HEAD or the function fails closed, naming it.
  # Fail-closed: prints nothing and returns 1 when the base cannot be read.
  local base_ref base_sha tmp_root dir staging roots="" root f
  if base_ref=$(gate_trunk_ref); then
    base_sha=$(git rev-parse --verify -q "$base_ref^{commit}" 2>/dev/null) || return 1
  else
    base_sha=$(git rev-parse --verify -q "HEAD^{commit}" 2>/dev/null) || return 1
    for f in $GATE_BOOTSTRAP_REQUIRED; do
      if ! git cat-file -e "$base_sha:$f" 2>/dev/null; then
        printf 'FATAL: BOOTSTRAP (no trunk on origin: checked origin/HEAD, origin/main, origin/master): %s is not committed at HEAD. Push blocked (fail-closed).\n  Regenerate with: %s\n  then commit it before pushing.\n' "$f" "$(gate_regen_command "$f")" >&2
        return 1
      fi
    done
  fi
  [ -n "$base_sha" ] || return 1
  tmp_root="${TMPDIR:-/tmp}"
  dir="$tmp_root/aesop-gate-base-v4-$base_sha"
  if [ ! -f "$dir/.complete" ]; then
    for root in tools state_store; do
      if git cat-file -e "$base_sha:$root" 2>/dev/null; then
        roots="$roots $root"
      fi
    done
    staging=$(mktemp -d "$tmp_root/aesop-gate-stage.XXXXXX" 2>/dev/null) || return 1
    if ! git archive "$base_sha" -- $roots 2>/dev/null | tar -x -C "$staging" 2>/dev/null \
       || [ ! -d "$staging/tools" ]; then
      rm -rf "$staging"
      return 1
    fi
    # git records tools/*.py as 100644, and POSIX `[ -x ]` (check_secret_scan) then rejects the
    # scanner on Linux. The base copy is private to this hook and content-addressed, so its
    # gate scripts are made executable here; the -x check itself is unchanged.
    find "$staging/tools" -maxdepth 1 -name '*.py' -exec chmod +x {} + 2>/dev/null
    if git cat-file -e "$base_sha:.pyflakes-baseline.json" 2>/dev/null; then
      if ! git show "$base_sha:.pyflakes-baseline.json" > "$staging/.pyflakes-baseline.json" 2>/dev/null; then
        rm -rf "$staging"
        return 1
      fi
    fi
    # The inprocess-timeout trunk baseline (check_inprocess_http_timeout judges against it).
    if git cat-file -e "$base_sha:.inprocess-timeout-baseline.json" 2>/dev/null; then
      if ! git show "$base_sha:.inprocess-timeout-baseline.json" > "$staging/.inprocess-timeout-baseline.json" 2>/dev/null; then
        rm -rf "$staging"
        return 1
      fi
    fi
    touch "$staging/.complete"
    rm -rf "$dir"
    mv "$staging" "$dir" 2>/dev/null || rm -rf "$staging"
    [ -f "$dir/.complete" ] || return 1
  fi
  printf '%s\n' "$dir"
}

gate_base_block() {
  # Shared fail-closed message: the base gate copy cannot be read, so no gate can judge the push.
  local label="$1"
  log_block "gate_base_unavailable"
  printf 'FATAL: %s cannot run its base gate copy: the trunk (or HEAD in BOOTSTRAP) tools/ is unreadable. Push blocked (fail-closed).\n' "$label" >&2
}

check_gate_tamper() {
  # GATE-TAMPER banner (non-blocking). A push that edits a gate file is still judged
  # by the BASE gate copy (gate_base_dir), so the edit cannot pass itself; the banner
  # makes the gate change loud in the push log. Gate improvements still push.
  local tuples local_ref local_sha remote_ref remote_sha changed hits=""
  tuples=$(cat)
  while IFS=' ' read -r local_ref local_sha remote_ref remote_sha || [ -n "$local_ref" ]; do
    [ -z "$local_sha" ] && continue
    [ "$local_sha" = "0000000000000000000000000000000000000000" ] && continue
    changed=$(git diff --name-only "origin/main...$local_sha" 2>/dev/null) || changed=""
    hits+=$(printf '%s\n' "$changed" | grep -E '^(tools/[^/]*gate[^/]*\.py|tools/secret_scan\.py|tools/gen_[^/]*\.py|tools/generated_[^/]*\.py|hooks/pre-push-policy\.sh|\.github/workflows/)' || true)
    hits+=$'\n'
  done <<< "$tuples"
  hits=$(printf '%s\n' "$hits" | sed '/^$/d' | sort -u)
  if [ -n "$hits" ]; then
    printf '\n*** GATE-CHANGED: this push modifies gate files. They are judged by the BASE (origin/main) copies; the pushed copies do not judge this push. ***\n' >&2
    printf '%s\n' "$hits" | sed 's/^/***   gate file: /' >&2
    log_event "gate_changed_in_push"
  fi
  return 0
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

    # Stdin carried ref tuples and none targets refs/heads/main|master (checked
    # above): the verdict is decided by the REMOTE refs being pushed. The local
    # checkout is irrelevant, so `git push origin HEAD:refs/heads/checkpoint/x`
    # from a local main checkout is allowed. Falling through to the current-branch
    # check below would wrongly block it.
    if [ "$saw_tuple" = "1" ]; then
      return 0
    fi
  fi

  # Reached only when stdin carried no ref tuples at all (empty stdin, or the
  # tty path): fall back to the current branch for safety. Any push that supplied
  # tuples was already judged above by its remote refs.
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
    # New TAG (refs/tags/*, remote sha all zeros): scan the TAGGED COMMIT ALONE,
    # <commit>^..<commit> (the empty tree for a root commit). A tag names a commit that
    # is already in history, so the merge-base branch below collapsed it to an empty
    # X..X range and the tag push scanned zero files (v0.10.1 incident). The tag object
    # is peeled to its commit with ^{commit}; a tag that does not peel is fail-closed.
    if [ "$remote_sha" = "0000000000000000000000000000000000000000" ] && [ "${remote_ref#refs/tags/}" != "$remote_ref" ]; then
      local tag_commit tag_base
      tag_commit=$(git rev-parse --verify -q "${local_sha}^{commit}" 2>/dev/null) || tag_commit=""
      if [ -z "$tag_commit" ]; then
        printf 'Error: tag %s does not peel to a commit; cannot derive its scan range (fail-closed)\n' "$remote_ref" >&2
        return 1
      fi
      tag_base=$(git rev-parse --verify -q "${tag_commit}^" 2>/dev/null) || tag_base="4b825dc642cb6eb9a060e54bf8d69288fbee4904"
      printf '%s..%s\n' "$tag_base" "$tag_commit"
    # If remote_sha is all zeros (new branch), use merge-base with remote default branch
    elif [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
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

  if [ $parse_exit_code -ne 0 ] || [ -z "$commit_ranges" ]; then
    # Malformed stdin: fail-CLOSED (security P1 fix)
    # Only fail-open for missing scanner tool, not for malformed input
    # rc=1 is returned for malformed tuples (e.g., 3-field lines), which must fail-closed
    log_block "secret_scan_stdin_parse_failed"
    printf 'Error: Could not parse pre-push stdin for commit range (malformed tuple)\n' >&2
    return 1
  fi

  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_secret_scan"; return 1; }
  local scan_script="$gate_dir/tools/secret_scan.py"

  if [ ! -f "$scan_script" ] || [ ! -x "$scan_script" ]; then
    # Scanner not found or not executable: fail-closed (cannot verify => deny)
    log_block "secret_scan_unavailable"
    printf 'FATAL: secret_scan.py not found or not executable at %s\n' "$scan_script" >&2
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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_tracker_guard"; return 1; }
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
  local guard_script="$gate_dir/tools/tracker_guard.py"

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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_import_resolution"; return 1; }
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
  local import_check_script="$gate_dir/tools/import_resolution_check.py"

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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_conflict_markers"; return 1; }
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
  local marker_script="$gate_dir/tools/conflict_marker_check.py"

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

check_symbol_survival() {
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_symbol_survival"; return 1; }
  # PR symbol survival gate (Guardrail G13, tools/pr_symbol_survival_check.py).
  # The script runs from the BASE gate copy (gate_base_dir), never the pushed tree,
  # so a push cannot stub its own symbol gate to pass.
  # BLOCKING since the G13 promotion: a symbol the pushed branch added that a
  # merge commit in range dropped (conflict-resolution loss, PR #745 class)
  # fails the push. Exit 1 = symbols missing; exit 2 = tool/git error. Both
  # block. Same range mechanism as check_conflict_markers (one remote..local
  # range per ref tuple from get_commit_range()).
  #
  # Fail-closed: the base copy's tools/ always exists (gate_base_dir), so a missing
  # script in it blocks the push. There is no skip branch here: skipping on a pushed
  # tools/ deletion would reopen the bypass this function closes.
  # Base-pinned (same rule as every other gate): the tool runs from the origin/main
  # gate copy, never the pushed tree. A PR that weakens the tool to exit 0 must not
  # pass a lossy merge it is judged on (lens L2 F2, G13 not base-pinned).
  local symbol_script="$gate_dir/tools/pr_symbol_survival_check.py"

  local tool_status
  tool_status=$(gate_tool_status "$gate_dir" "$symbol_script")
  if [ "$tool_status" != "ok" ]; then
    gate_tool_missing_block "pr_symbol_survival_check.py" "$symbol_script"
    log_event "symbol_survival_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "PR symbol survival check"
    log_event "symbol_survival_no_python"
    return 1
  fi

  local commit_ranges
  commit_ranges=$(get_commit_range)
  local parse_exit_code=$?

  if [ $parse_exit_code -eq 2 ]; then
    log_event "symbol_survival_skipped_delete_only_push"
    return 0
  fi

  if [ $parse_exit_code -eq 3 ]; then
    log_event "symbol_survival_skipped_empty_stdin"
    return 0
  fi

  if [ $parse_exit_code -ne 0 ] || [ -z "$commit_ranges" ]; then
    # Malformed stdin: fail-CLOSED.
    log_block "symbol_survival_stdin_parse_failed"
    printf 'Error: Could not parse pre-push stdin for commit range (symbol survival check)\n' >&2
    return 1
  fi

  local overall_exit_code=0
  local range
  while IFS= read -r range || [ -n "$range" ]; do
    [ -z "$range" ] && continue
    local base_ref="${range%%..*}"
    local head_ref="${range##*..}"

    local check_output
    check_output=$("$py_bin" "$symbol_script" --base "$base_ref" --head "$head_ref" 2>&1)
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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_claudemd_sync"; return 1; }
  # CLAUDE.md synchronization gate (tools/claudemd_sync_gate.py --check).
  # Ensures code changes are accompanied by domain CLAUDE.md updates.
  # Runs at push time so authors see drift immediately.
  #
  # Fail-open for missing optional tooling; fail-closed for actual drift.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local sync_script="$gate_dir/tools/claudemd_sync_gate.py"

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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_gen_tool_index"; return 1; }
  # Tool index synchronization gate (tools/gen_tool_index.py --check).
  # Ensures tools/INDEX.md stays in sync with per-tool INDEX: docstring lines.
  # Any new tool without an INDEX: line fails closed to prevent undocumented tools.
  #
  # Fail-open for missing optional tooling; fail-closed for actual drift or missing INDEX lines.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local index_script="$gate_dir/tools/gen_tool_index.py"

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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_metrics"; return 1; }
  # Metrics verification gate (tools/metrics_gate.py).
  # Ensures hard numeric claims in markdown are verified with source markers.
  # Runs at push time so authors see unverified metrics immediately.
  #
  # Fail-open for missing optional tooling; fail-closed for unverified metrics.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local metrics_script="$gate_dir/tools/metrics_gate.py"

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

  # Scan the PUSHED ranges read from stdin, derived by get_commit_range exactly as
  # check_secret_scan derives them: one metrics_gate.py run per range, any failing
  # range blocks. Before this, the gate always ran with its default range
  # origin/main...HEAD, so a tag-only push was judged by the current branch's
  # unpushed commits (v0.10.1 incident). Empty, delete-only and unparseable stdin
  # fall through to the default range below (today's behaviour, fail-closed).
  local push_ranges push_range push_rc range_output range_exit overall_rc=0
  push_ranges=$(get_commit_range)
  push_rc=$?
  if [ $push_rc -eq 0 ] && [ -n "$push_ranges" ]; then
    while IFS= read -r push_range || [ -n "$push_range" ]; do
      [ -z "$push_range" ] && continue
      range_output=$("$py_bin" "$metrics_script" "$push_range" 2>&1)
      range_exit=$?
      if [ $range_exit -ne 0 ]; then
        if [ -n "$range_output" ]; then
          printf '%s\n' "$range_output" >&2
        fi
        overall_rc=1
      fi
    done <<< "$push_ranges"
    return $overall_rc
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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_test_suite_count"; return 1; }
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
  local verify_script="$gate_dir/tools/verify_test_suite_count.py"

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

check_pyflakes_ratchet() {
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_pyflakes_ratchet"; return 1; }
  # Pyflakes ratchet gate (guardrail G15, tools/pyflakes_gate.py).
  # Wraps `python -m pyflakes` to enforce a baseline: never let unused-import /
  # unused-variable debt grow. A new finding blocks the push. A branch that burns
  # the trunk baseline down passes with a note (D40); any addition blocks. Scans tools/,
  # bin/, ui/, state_store/, driver/, monitor/, daemons/, tests/, bench/.
  #
  # Pyflakes is a dev-only lint dependency (not installed by default;
  # CI installs it in the "Install Python test dependencies" step).
  # Exit codes: 0 = clean/baselined, 1 = new-or-stale findings (fail-CLOSED),
  # 2 = error (pyflakes not installed, baseline unreadable, etc; fail-OPEN with WARN).
  #
  # Opt-out: AESOP_PYFLAKES_SKIP=1 (exact match "1", not just truthy).
  # Fail-open when the pyflakes package is not installed or the gate tool is
  # absent; actual baseline mismatches stay fail-closed.
  if [ "${AESOP_PYFLAKES_SKIP:-}" = "1" ]; then
    log_event "pyflakes_ratchet_skipped_env_opt_out"
    return 0
  fi

  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local pyflakes_script="$gate_dir/tools/pyflakes_gate.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$pyflakes_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "pyflakes_ratchet_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    # Pyflakes is a dev-only dependency: tool missing => fail-open with a WARN
    printf 'Warning: pyflakes_gate.py not found; pyflakes ratchet check skipped (fail-open)\n' >&2
    log_event "pyflakes_ratchet_tool_missing_dev_only"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "pyflakes ratchet"
    log_event "pyflakes_ratchet_no_python"
    return 1
  fi

  local pyflakes_output
  # D40: the trunk baseline ($gate_dir) is the authority for additions; the branch
  # baseline ($aesop_root, the pushed tree's copy) may burn the trunk down. The gate
  # fails closed on a missing/malformed branch baseline, an addition, or a new finding.
  pyflakes_output=$("$py_bin" "$pyflakes_script" --root "$aesop_root" --baseline "$aesop_root/.pyflakes-baseline.json" --trunk-baseline "$gate_dir/.pyflakes-baseline.json" 2>&1)
  local pyflakes_exit_code=$?

  if [ $pyflakes_exit_code -eq 0 ]; then
    # Burn-down note (one line) is surfaced in the push log; a clean PASS stays silent.
    case "$pyflakes_output" in
      *"pyflakes baseline burned down:"*) printf '%s\n' "$pyflakes_output" | grep -F 'pyflakes baseline burned down:' >&2 ;;
    esac
    return 0
  fi

  if [ -n "$pyflakes_output" ]; then
    printf '%s\n' "$pyflakes_output" >&2
  fi

  # Exit code 2 is an environment condition (pyflakes not installed, etc.):
  # fail-open with a warning audit event, not a push-block
  if [ $pyflakes_exit_code -eq 2 ]; then
    printf 'Warning: pyflakes ratchet check unavailable (pyflakes not installed or other error); skipping (fail-open)\n' >&2
    log_event "pyflakes_ratchet_skipped_tool_error"
    return 0
  fi

  # Exit code 1: new-or-stale findings => fail-CLOSED, block the push
  return 1
}


check_inprocess_http_timeout() {
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_inprocess_http_timeout"; return 1; }
  # Escape main-full-windows-shard2-ui-acceptance-timeout (tools/inprocess_http_timeout_lint.py).
  # Scans the pushed tests/ for a constant client timeout under 30 s in any module that starts
  # an in-process server. Fail-CLOSED on any hit, an unparseable test module, or an escape total
  # or per-file hit count above the TRUNK baseline ($gate_dir, the authority, like D40).
  # Fail-CLOSED when the trunk copy of the tool is missing from a repo that has tools/
  # (the tool is on trunk once guard/inprocess-http-timeout-lint-v2 merges); skip only
  # when there is no aesop tools/ directory at all (gate_tool_status = skip).
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local lint_script="$gate_dir/tools/inprocess_http_timeout_lint.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$lint_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "inprocess_timeout_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "inprocess_http_timeout_lint.py" "$lint_script"
    log_event "inprocess_timeout_tool_missing"
    return 1
  fi
  # No tests/ in the pushed tree: nothing to scan, UNLESS the trunk still has tests/ (a
  # branch that deletes tests/ must not skip the lint). Bootstrap (no trunk) skips.
  if [ ! -d "$aesop_root/tests" ]; then
    local trunk_ref=""
    if trunk_ref=$(gate_trunk_ref) && git -C "$aesop_root" cat-file -e "$trunk_ref:tests" 2>/dev/null; then
      printf 'FATAL: tests/ is missing from the pushed tree but %s still has it. Push blocked.\n' "$trunk_ref" >&2
      log_event "inprocess_timeout_tests_dir_removed"
      return 1
    fi
    log_event "inprocess_timeout_no_tests_dir"
    return 0
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    gate_no_python_block "inprocess timeout lint"
    log_event "inprocess_timeout_no_python"
    return 1
  fi

  local lint_output
  lint_output=$("$py_bin" "$lint_script" --root "$aesop_root" --baseline "$gate_dir/.inprocess-timeout-baseline.json" 2>&1)
  local lint_exit_code=$?

  if [ $lint_exit_code -eq 0 ]; then
    case "$lint_output" in
      *"burned down:"*) printf '%s\n' "$lint_output" | grep -F 'burned down:' >&2 ;;
    esac
    return 0
  fi

  if [ -n "$lint_output" ]; then
    printf '%s\n' "$lint_output" >&2
  fi
  # Exit 1 (hit or ratchet above trunk baseline) and exit 2 (unreadable input) both block.
  return 1
}

check_subprocess_guard_ratchet() {
  # Subprocess anti-pattern ratchet (guardrail G6, tools/subprocess_guard.py).
  # Runs the EXACT invocation ci.yml runs, from the repo root, against the pushed
  # tree: `python tools/subprocess_guard.py --check --baseline .subprocess-guard-baseline.json`.
  # One command, one baseline path, one message: a push that adds a shell=True above
  # the baseline fails here with the same output CI prints. Not pinned to origin/main
  # on purpose: pinning needs a flag the base copy may not have yet (bootstrap), and
  # CI judges the pushed script the same way. tests/test_pre_push_policy.sh Test 36
  # asserts the ci.yml line and this function agree on the same fixtures.
  # Fail-closed: missing baseline, missing tool (when tools/ exists), no python, or
  # any non-zero exit blocks the push. Skip only when there is no aesop checkout.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local sg_script="$aesop_root/tools/subprocess_guard.py"

  local tool_status
  tool_status=$(gate_tool_status "$aesop_root" "$sg_script")
  if [ "$tool_status" = "skip" ]; then
    log_event "subprocess_guard_ratchet_skipped_no_aesop_tools"
    return 0
  fi
  if [ "$tool_status" = "missing" ]; then
    gate_tool_missing_block "subprocess_guard.py" "$sg_script"
    log_event "subprocess_guard_ratchet_tool_missing"
    return 1
  fi

  local baseline="$aesop_root/.subprocess-guard-baseline.json"
  local baseline_arg=".subprocess-guard-baseline.json" head_copy=""
  if ! gate_trunk_ref >/dev/null 2>&1; then
    # BOOTSTRAP (no trunk on origin): judge against the committed HEAD copy, never the working tree.
    head_copy=$(mktemp "${TMPDIR:-/tmp}/aesop-sg-baseline.XXXXXX" 2>/dev/null) || head_copy=""
    if [ -z "$head_copy" ] || ! git show "HEAD:.subprocess-guard-baseline.json" > "$head_copy" 2>/dev/null; then
      [ -n "$head_copy" ] && rm -f "$head_copy"
      printf 'FATAL: .subprocess-guard-baseline.json is not committed at HEAD (BOOTSTRAP). Push blocked (fail-closed).\n  Regenerate with: %s\n  then commit it before pushing.\n' "$(gate_regen_command .subprocess-guard-baseline.json)" >&2
      log_event "subprocess_guard_ratchet_baseline_missing"
      return 1
    fi
    baseline_arg="$head_copy"
  elif [ ! -f "$baseline" ]; then
    printf 'FATAL: .subprocess-guard-baseline.json not found at %s. A ratchet with no baseline cannot judge the push (fail-closed).\n' "$baseline" >&2
    log_event "subprocess_guard_ratchet_baseline_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    [ -n "$head_copy" ] && rm -f "$head_copy"
    gate_no_python_block "subprocess guard ratchet"
    log_event "subprocess_guard_ratchet_no_python"
    return 1
  fi

  local sg_output
  sg_output=$(cd "$aesop_root" && "$py_bin" tools/subprocess_guard.py --check --baseline "$baseline_arg" 2>&1)
  local sg_exit_code=$?
  [ -n "$head_copy" ] && rm -f "$head_copy"
  if [ $sg_exit_code -eq 0 ]; then
    return 0
  fi
  if [ -n "$sg_output" ]; then
    printf '%s\n' "$sg_output" >&2
  fi
  log_event "subprocess_guard_ratchet_failure"
  return 1
}


check_claudemd_headroom() {
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_claudemd_headroom"; return 1; }
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
  local lint_script="$gate_dir/tools/claudemd_lint.py"

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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_encoding_lint"; return 1; }
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
  local lint_script="$gate_dir/tools/encoding_lint.py"

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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_test_coverage"; return 1; }
  # Guardrail G2 extension: verify all on-disk test files are run by CI.
  # Runs tools/verify_test_coverage.py --check to detect orphaned tests.
  # Skips only when there is no aesop checkout; fail-closed when tools/ exists
  # but this gate's script does not, and on actual findings.
  # resolve_aesop_root(), not $HOME/aesop -- see check_encoding_lint.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local coverage_script="$gate_dir/tools/verify_test_coverage.py"

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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_linux_shape"; return 1; }
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
  local shape_script="$gate_dir/tools/linux_shape_check.py"

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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_generated_paths"; return 1; }
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
  local gen_script="$gate_dir/tools/generated_paths.py"

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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "ensure_merge_drivers"; return 1; }
  # Register aesop's custom git merge drivers (tools/install_merge_drivers.py)
  # in this clone's config, idempotently. `.gitattributes` names the drivers
  # but git never reads a driver COMMAND out of the repository, so each clone
  # registers once; doing it here means no clone that pushes can forget.
  # Linked worktrees share the config. Fail-open: registration is a
  # convenience, the byte-identity gate below is the enforcement.
  local aesop_root
  aesop_root=$(resolve_aesop_root)
  local install_script="$gate_dir/tools/install_merge_drivers.py"
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
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_generated_regen"; return 1; }
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
  local gate_script="$gate_dir/tools/generated_push_gate.py"

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
  gate_output=$("$py_bin" "$gate_script" --base-dir "$gate_dir" "${range_args[@]}" 2>&1)
  local gate_exit_code=$?
  if [ $gate_exit_code -ne 0 ]; then
    if [ -n "$gate_output" ]; then
      printf '%s\n' "$gate_output" >&2
    fi
    return 1
  fi
  # Pass: echo the gate's note lines (e.g. D46 "inherited unchanged from base") so the
  # pass is visible in the push log, not silently swallowed.
  if [ -n "$gate_output" ]; then
    printf '%s\n' "$gate_output"
  fi
  return 0
}

check_emit_receipt() {
  local gate_dir
  gate_dir=$(gate_base_dir) || { gate_base_block "check_emit_receipt"; return 1; }
  # Receipt-gate auto-emission (docs/RECEIPT-GATE.md). Day-1 measurement found
  # 41 merged PRs and ZERO receipts: lanes were never told to run
  # `emit_receipt.py` and nothing ran it automatically, so the honesty signal
  # had no data. This runs it for every push, ONCE every gate above has
  # already passed, so the receipt is bound to a tree this hook itself just
  # verified.
  #
  # CONCLUSION GATE (2026-10-15, PR #990 pushed a known-red head): a matrix that
  # RAN and came back red blocks the push (emit_receipt.py --fail-on-red exits 3;
  # the failure receipt is still posted as the trace). An unreadable conclusion
  # (timeout, emitter exit other than 0/3/4) also blocks: fail closed. A
  # skipped-with-reason part is not in the matrix result and never blocks.
  # AESOP_PUSH_RED=1 is the one recorded emergency override: emit_receipt exits 4,
  # the receipt is stamped pushed_red: true, and the push is allowed loudly.
  #
  # Precondition skips still print a one-line WARN and `return 0`: a missing
  # tool, missing python, missing `gh`, or missing signing-key material means the
  # matrix never ran, so there is no conclusion to gate on (see hooks/CLAUDE.md).
  # Return value: 0 = allowed, 1 = blocked. main() exits 1 on 1.
  #
  # BOUNDED, not just fail-open: the default matrix re-runs all 4 python
  # shards (the whole repo's test suite), which measured ~500s for the
  # slowest shard alone on this box -- a git hook must never be able to hang
  # a push indefinitely. Wrapped in `timeout`; AESOP_RECEIPT_TIMEOUT overrides
  # the default 900s bound. A timeout WARNs and fails open exactly like every
  # other branch here -- it is not a verification failure, just a slow run
  # that ran out of patience.
  #
  # Opt-out: AESOP_RECEIPT_EMIT=0 skips emission entirely (e.g. a lane that
  # intentionally wants no receipt, or a CI checkout that should not try to
  # sign anything).
  if [ "${AESOP_RECEIPT_EMIT:-1}" = "0" ]; then
    log_event "receipt_emit_skipped_opted_out"
    return 0
  fi

  local aesop_root
  aesop_root=$(resolve_aesop_root)

  # Flush any spooled receipts from prior pushes before emitting a new one.
  # This ensures that once shas are pushed and exist on GitHub, their receipts
  # are published. Fail-open: errors in flushing do not block the push.
  # Both this flush and the emitter below run from the BASE gate copy (gate_base_dir);
  # --repo still names the pushed tree, which is the data being receipted.
  local flush_script="$gate_dir/tools/receipt_flush.py"
  local py_bin=""
  if [ -f "$flush_script" ]; then
    if py_bin=$(resolve_py_bin); then
      "$py_bin" "$flush_script" --repo "$aesop_root" < /dev/null > /dev/null 2>&1 || true
    fi
  fi

  local receipt_script="$gate_dir/tools/emit_receipt.py"

  if [ ! -f "$receipt_script" ]; then
    # Fail-closed: the emitter is read from the base copy, and a base copy without it
    # cannot produce a conclusion to gate on. Blocks the push with a named message.
    printf 'FATAL: emit_receipt.py not found in the base gate copy (%s). Push blocked (fail-closed).\n' "$receipt_script" >&2
    log_block "receipt_emit_base_tool_missing"
    return 1
  fi

  local py_bin=""
  if ! py_bin=$(resolve_py_bin); then
    printf 'WARN: no python interpreter found; receipt not emitted (push continues).\n' >&2
    log_event "receipt_emit_skipped_no_python"
    return 0
  fi

  if ! command -v gh >/dev/null 2>&1 || ! gh --version >/dev/null 2>&1; then
    printf 'WARN: gh CLI not found or not working; receipt not emitted (push continues).\n' >&2
    log_event "receipt_emit_skipped_no_gh"
    return 0
  fi

  if [ -z "${AESOP_RECEIPT_KEY:-}" ] && [ -z "${AESOP_RECEIPT_HMAC_SECRET:-}" ] && [ ! -f "${AESOP_HOME:-$HOME}/.aesop/receipt_key.pem" ]; then
    printf 'WARN: no receipt signing key material (%s or %s unset, and default path not found); receipt not emitted (push continues).\n' \
      "AESOP_RECEIPT_KEY" "AESOP_RECEIPT_HMAC_SECRET" >&2
    log_event "receipt_emit_skipped_no_key_material"
    return 0
  fi

  local timeout_bin=""
  if command -v timeout >/dev/null 2>&1 && timeout --version >/dev/null 2>&1; then
    timeout_bin="timeout"
  elif command -v gtimeout >/dev/null 2>&1 && gtimeout --version >/dev/null 2>&1; then
    timeout_bin="gtimeout"
  fi
  local timeout_secs="${AESOP_RECEIPT_TIMEOUT:-900}"

  # --fail-on-red: exit 3 on a red required part (the hook blocks). The override
  # is only ever passed explicitly, so a default push cannot be red-allowed.
  local receipt_flags=(--fail-on-red)
  if [ "${AESOP_PUSH_RED:-}" = "1" ]; then
    receipt_flags+=(--pushed-red)
  fi

  local receipt_output
  local receipt_exit_code
  local receipt_tmpfile
  receipt_tmpfile=$(mktemp)
  trap "rm -f '$receipt_tmpfile'" RETURN

  if [ -n "$timeout_bin" ]; then
    # Use timeout with --kill-after to ensure process group cleanup on both Linux and macOS.
    # Redirect to temp file instead of command substitution to avoid bash subshell hanging
    # when the process group is killed but the pipe reader doesn't get EOF notification.
    # Unset GIT_* environment variables to prevent matrix parts from committing to this repo
    # via inherited GIT_DIR/GIT_WORK_TREE (see docs/RECEIPT-GATE.md for the mechanism).
    env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_PREFIX -u GIT_COMMON_DIR -u GIT_OBJECT_DIRECTORY \
      "$timeout_bin" --kill-after=5 "$timeout_secs" "$py_bin" "$receipt_script" --repo "$aesop_root" --post "${receipt_flags[@]}" \
      < /dev/null > "$receipt_tmpfile" 2>&1
    receipt_exit_code=$?
  else
    # No `timeout` on this box: use background job with explicit deadline.
    # Still fail-open on timeout, just without coreutils timeout binary.
    # Logged so the gap is visible, not invisible.
    log_event "receipt_emit_unbounded_no_timeout_bin"
    # Unset GIT_* environment variables to prevent matrix parts from committing to this repo.
    env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_PREFIX -u GIT_COMMON_DIR -u GIT_OBJECT_DIRECTORY \
      "$py_bin" "$receipt_script" --repo "$aesop_root" --post "${receipt_flags[@]}" \
      < /dev/null > "$receipt_tmpfile" 2>&1 &
    local receipt_pid=$!
    local elapsed=0
    local timed_out=0

    # Poll until process exits or timeout expires
    while [ $elapsed -lt "$timeout_secs" ]; do
      if ! kill -0 "$receipt_pid" 2>/dev/null; then
        # Process has already exited
        break
      fi
      sleep 1
      elapsed=$((elapsed + 1))
    done

    # If still running after timeout, kill it
    if kill -0 "$receipt_pid" 2>/dev/null; then
      timed_out=1
      # Send SIGTERM to the process and its children
      kill -TERM "$receipt_pid" 2>/dev/null || true
      # Also try pkill to get any shell/python subprocesses
      pkill -P "$receipt_pid" 2>/dev/null || true
      sleep 1
      # Force kill if still running
      if kill -0 "$receipt_pid" 2>/dev/null; then
        kill -KILL "$receipt_pid" 2>/dev/null || true
        pkill -9 -P "$receipt_pid" 2>/dev/null || true
      fi
    fi

    # Wait for the process to clean up, with a small timeout to avoid hanging
    ( sleep 2; kill -KILL "$receipt_pid" 2>/dev/null || true ) &
    # Capture the child's real exit code: `wait ... || true; $?` always reads 0 (fail-open).
    local wait_rc=0
    wait "$receipt_pid" 2>/dev/null || wait_rc=$?
    receipt_exit_code=$wait_rc

    # If we timed out, report exit code 124 (same as timeout command)
    if [ $timed_out -eq 1 ]; then
      receipt_exit_code=124
    fi
  fi
  receipt_output=$(cat "$receipt_tmpfile" 2>/dev/null || true)

  # Timeout: the matrix never reported a conclusion, so there is nothing to allow on.
  if [ $receipt_exit_code -eq 124 ]; then
    printf 'BLOCKED: receipt matrix timed out after %ss; conclusion unreadable, push blocked (fail closed). Raise AESOP_RECEIPT_TIMEOUT, or AESOP_RECEIPT_EMIT=0 to opt out.\n' "$timeout_secs" >&2
    log_block "receipt_emit_timed_out"
    return 1
  fi

  # Exit 4: a red matrix under the AESOP_PUSH_RED=1 override. Allowed, loudly; the
  # receipt on GitHub carries pushed_red: true.
  if [ $receipt_exit_code -eq 4 ]; then
    if [ -n "$receipt_output" ]; then
      printf '%s\n' "$receipt_output"
    fi
    printf 'WARN: AESOP_PUSH_RED=1: pushing a RED head. A required receipt part failed; the override allows this push and the receipt is stamped pushed_red: true.\n' >&2
    log_event "receipt_push_red_override"
    return 0
  fi

  # Exit 3: a required part is red. This is the block that PR #990 slipped through.
  if [ $receipt_exit_code -eq 3 ]; then
    if [ -n "$receipt_output" ]; then
      printf '%s\n' "$receipt_output" >&2
    fi
    printf 'BLOCKED: a required receipt part is RED; push blocked. Fix the failing part (the failure receipt is recorded on the head sha). Emergency only: AESOP_PUSH_RED=1 git push.\n' >&2
    log_block "receipt_red_blocked"
    return 1
  fi

  # Any other exit (2 = could not sign/publish, 1 = crash, ...): no readable conclusion.
  if [ $receipt_exit_code -ne 0 ]; then
    printf 'BLOCKED: receipt emission failed (exit %d); conclusion unreadable, push blocked (fail closed).\n' "$receipt_exit_code" >&2
    if [ -n "$receipt_output" ]; then
      printf '%s\n' "$receipt_output" >&2
    fi
    log_block "receipt_emit_unreadable_blocked"
    return 1
  fi

  if [ -n "$receipt_output" ]; then
    printf '%s\n' "$receipt_output"
  fi
  # A "spooled" receipt means its sha is not on GitHub yet (we are inside pre-push), so
  # nothing would post it until a later push or the watchdog. Start a DETACHED poller
  # that posts it as soon as the sha appears. --spawn forks and returns at once: the
  # push never waits on it, and the poller's stdio goes to <sha>.flush.log.
  # AESOP_RECEIPT_AUTOFLUSH=0 opts out. Fail-open like every branch here.
  if [ "${AESOP_RECEIPT_AUTOFLUSH:-1}" != "0" ] && printf '%s' "$receipt_output" | grep -q "spooled receipt for"; then
    local spool_sha=""
    spool_sha=$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_PREFIX -u GIT_COMMON_DIR -u GIT_OBJECT_DIRECTORY \
      git -C "$aesop_root" rev-parse HEAD 2>/dev/null) || spool_sha=""
    if [ -n "$spool_sha" ] && [ -f "$gate_dir/tools/receipt_autoflush.py" ]; then
      env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_PREFIX -u GIT_COMMON_DIR -u GIT_OBJECT_DIRECTORY \
        "$py_bin" "$gate_dir/tools/receipt_autoflush.py" --repo "$aesop_root" --sha "$spool_sha" --spawn \
        < /dev/null 2>&1 || true
      log_event "receipt_autoflush_spawned"
    fi
  fi
  log_event "receipt_emit_posted"
  return 0
}

run_test_mode() {
  # Gate code is judged from origin/main (gate_base_dir). A fixture that needs a
  # specific gate script publishes its tools/ as origin/main:tools/ (below).
  test_publish_base() {
    local repo="$1" tools_src="$2" base="$1.base"
    rm -rf "$base"
    mkdir -p "$base/tools"
    if [ -d "$tools_src" ]; then cp -R "$tools_src"/. "$base/tools/"; fi
    : > "$base/tools/.keep"
    git -C "$base" init -q -b main
    git -C "$base" add -A
    git -C "$base" -c user.email=t@example.com -c user.name=t -c core.autocrlf=false commit -q -m base
    git -C "$repo" remote remove origin >/dev/null 2>&1 || true
    git -C "$repo" remote add origin "$base"
    git -C "$repo" fetch -q origin main
  }
  test_clone_base() {
    local clone="$1" tools_src="$2"
    rm -rf "$clone"
    mkdir -p "$clone"
    git -C "$clone" init -q -b main
    git -C "$clone" -c user.email=t@example.com -c user.name=t -c core.autocrlf=false commit -q --allow-empty -m seed
    test_publish_base "$clone" "$tools_src"
    git -C "$clone" symbolic-ref HEAD refs/heads/feature/test
  }
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
    test_clone_base "$tmpdir/noscan_clone" ""
    cd "$tmpdir/noscan_clone" || exit 1
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
    test_publish_base "$tmpdir" "$AESOP_ROOT/tools"

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

    if check_metrics </dev/null >/dev/null 2>&1; then
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
    test_clone_base "$tmpdir/tn19_clone" "$AESOP_ROOT/tools"
    cd "$tmpdir/tn19_clone" || exit 1

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
    test_clone_base "$tmpdir/tn20_clone" "$AESOP_ROOT/tools"
    cd "$tmpdir/tn20_clone" || exit 1

    if check_metrics </dev/null >/dev/null 2>&1; then
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
    test_clone_base "$tmpdir/tn21_clone" "$AESOP_ROOT/tools"
    cd "$tmpdir/tn21_clone" || exit 1

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
    test_clone_base "$tmpdir/tn22_clone" "$AESOP_ROOT/tools"
    cd "$tmpdir/tn22_clone" || exit 1

    if check_metrics </dev/null >/dev/null 2>&1; then
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
    test_clone_base "$tmpdir/hr_clone_a" "$AESOP_ROOT/tools"
    cd "$tmpdir/hr_clone_a" || exit 1

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
    test_clone_base "$tmpdir/hr_clone_b" "$AESOP_ROOT/tools"
    cd "$tmpdir/hr_clone_b" || exit 1
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
    test_clone_base "$tmpdir/hr_clone_c" "$AESOP_ROOT/tools"
    cd "$tmpdir/hr_clone_c" || exit 1
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

    git -C "$REPO" update-ref refs/remotes/origin/main "$head_sha"
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

  printf '\n=== Test 29: check_symbol_survival BLOCKS a merge that drops a branch symbol (main as first parent), passes a clean push ===\n'
  (
    sym_real_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    sym_aesop_root="$tmpdir/symbol_survival_aesop"
    rm -rf "$sym_aesop_root"
    mkdir -p "$sym_aesop_root/tools"
    cp "$sym_real_root/tools/pr_symbol_survival_check.py" "$sym_aesop_root/tools/"

    sym_repo="$tmpdir/symbol_survival_repo"
    rm -rf "$sym_repo"
    git init -q -b main "$sym_repo"
    git -C "$sym_repo" config user.email "test@example.com"
    git -C "$sym_repo" config user.name "Test User"
    git -C "$sym_repo" config core.autocrlf false
    mkdir -p "$sym_repo/tools"
    cp "$sym_real_root/tools/pr_symbol_survival_check.py" "$sym_repo/tools/"
    printf 'def existing():\n    pass\n' > "$sym_repo/mod.py"
    # The G13 tool is base-pinned: the hook runs origin/main's copy, so the trunk must
    # carry it and origin/main must exist (gate_base_dir fails closed on a bare repo,
    # which would make the BLOCK assertion pass for the wrong reason).
    mkdir -p "$sym_repo/tools"
    cp "$sym_real_root/tools/pr_symbol_survival_check.py" "$sym_repo/tools/"
    git -C "$sym_repo" add -A
    git -C "$sym_repo" commit -q -m base
    sym_base=$(git -C "$sym_repo" rev-parse HEAD)
    # The trunk on origin carries the real gate: that is the base copy the check runs.
    git -C "$sym_repo" update-ref refs/remotes/origin/main "$sym_base"

    git -C "$sym_repo" checkout -q -b feature
    printf 'def existing():\n    pass\n\n\ndef alpha():\n    pass\n' > "$sym_repo/mod.py"
    git -C "$sym_repo" commit -qam "feat: add alpha"

    git -C "$sym_repo" checkout -q main
    printf 'def existing():\n    pass\n\n\ndef gamma():\n    pass\n' > "$sym_repo/mod.py"
    git -C "$sym_repo" commit -qam "main: add gamma"

    # Lossy merge with main as FIRST parent: resolution keeps main's whole file.
    git -C "$sym_repo" merge -q --no-edit feature >/dev/null 2>&1 || true
    git -C "$sym_repo" checkout -q --ours mod.py
    git -C "$sym_repo" add mod.py
    git -C "$sym_repo" commit -q -m "merge feature (resolution drops alpha)"
    sym_tip=$(git -C "$sym_repo" rev-parse HEAD)

    (
      cd "$sym_repo" || exit 1
      export AESOP_ROOT="$sym_aesop_root"
      stdin_line="$sym_tip $sym_tip refs/heads/main $sym_base"
      sym_block_out=$(printf '%s\n' "$stdin_line" | check_symbol_survival 2>&1)
      if [ $? -eq 0 ]; then
        printf 'FAIL: check_symbol_survival should BLOCK a push whose merge dropped a branch symbol\n'
        exit 1
      fi
      case "$sym_block_out" in
        *MISSING*) ;;
        *) printf 'FAIL: block did not come from the symbol-survival verdict (no MISSING): %s\n' "$sym_block_out"; exit 1 ;;
      esac

      git checkout -q -b clean "$sym_base"
      printf 'def existing():\n    pass\n\n\ndef beta():\n    pass\n' > mod.py
      git commit -qam "feat: add beta (no merge)"
      clean_tip=$(git rev-parse HEAD)
      clean_line="$clean_tip $clean_tip refs/heads/clean $sym_base"
      if ! printf '%s\n' "$clean_line" | check_symbol_survival >/dev/null 2>&1; then
        printf 'FAIL: check_symbol_survival should PASS a clean non-merge push\n'
        exit 1
      fi
      printf 'PASS: check_symbol_survival blocks the lossy merge and passes a clean push\n'
    )
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 30: check_symbol_survival runs the BASE tool, not the pushed copy (a pushed always-pass stub cannot pass a lossy merge) ===\n'
  (
    sb_real_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    sb_repo="$tmpdir/symbol_base_repo"
    rm -rf "$sb_repo"
    git init -q -b main "$sb_repo"
    git -C "$sb_repo" config user.email "test@example.com"
    git -C "$sb_repo" config user.name "Test User"
    git -C "$sb_repo" config core.autocrlf false
    mkdir -p "$sb_repo/tools"
    cp "$sb_real_root/tools/pr_symbol_survival_check.py" "$sb_repo/tools/"
    printf 'def existing():\n    pass\n' > "$sb_repo/mod.py"
    git -C "$sb_repo" add -A
    git -C "$sb_repo" commit -q -m base
    sb_base=$(git -C "$sb_repo" rev-parse HEAD)
    # origin/main (the trunk) carries the REAL gate: that is the base copy.
    git -C "$sb_repo" update-ref refs/remotes/origin/main "$sb_base"

    git -C "$sb_repo" checkout -q -b feature
    printf 'def existing():\n    pass\n\n\ndef alpha():\n    pass\n' > "$sb_repo/mod.py"
    git -C "$sb_repo" commit -qam "feat: add alpha"
    git -C "$sb_repo" checkout -q main
    printf 'def existing():\n    pass\n\n\ndef gamma():\n    pass\n' > "$sb_repo/mod.py"
    git -C "$sb_repo" commit -qam "main: add gamma"
    # Lossy merge with main as FIRST parent: resolution keeps main's whole file (drops alpha).
    git -C "$sb_repo" merge -q --no-edit feature >/dev/null 2>&1 || true
    git -C "$sb_repo" checkout -q --ours mod.py
    git -C "$sb_repo" add mod.py
    git -C "$sb_repo" commit -q -m "merge feature (resolution drops alpha)"
    # The PUSHED tree stubs the gate to always pass. The base copy must still judge the push.
    printf 'import sys\nsys.exit(0)\n' > "$sb_repo/tools/pr_symbol_survival_check.py"
    git -C "$sb_repo" commit -qam "push: stub the symbol gate to always pass"
    sb_tip=$(git -C "$sb_repo" rev-parse HEAD)

    (
      cd "$sb_repo" || exit 1
      unset AESOP_ROOT
      stdin_line="$sb_tip $sb_tip refs/heads/feature $sb_base"
      if printf '%s\n' "$stdin_line" | check_symbol_survival >/dev/null 2>&1; then
        printf 'FAIL: check_symbol_survival ran the pushed always-pass stub; it must run the base copy and BLOCK\n'
        exit 1
      fi
      printf 'PASS: check_symbol_survival ran the base copy and blocked the lossy merge despite the pushed stub\n'
    )
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 31: check_symbol_survival fails CLOSED when the base copy of the gate is missing ===\n'
  (
    sb_real_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    sbm_repo="$tmpdir/symbol_base_missing_repo"
    rm -rf "$sbm_repo"
    git init -q -b main "$sbm_repo"
    git -C "$sbm_repo" config user.email "test@example.com"
    git -C "$sbm_repo" config user.name "Test User"
    git -C "$sbm_repo" config core.autocrlf false
    mkdir -p "$sbm_repo/tools"
    printf 'placeholder\n' > "$sbm_repo/tools/other_gate.txt"
    printf 'def existing():\n    pass\n' > "$sbm_repo/mod.py"
    git -C "$sbm_repo" add -A
    git -C "$sbm_repo" commit -q -m base
    sbm_base=$(git -C "$sbm_repo" rev-parse HEAD)
    git -C "$sbm_repo" update-ref refs/remotes/origin/main "$sbm_base"

    # The pushed tree ADDS the gate script the trunk never had: no base copy exists to judge it.
    git -C "$sbm_repo" checkout -q -b feature
    cp "$sb_real_root/tools/pr_symbol_survival_check.py" "$sbm_repo/tools/"
    printf 'def existing():\n    pass\n\n\ndef beta():\n    pass\n' > "$sbm_repo/mod.py"
    git -C "$sbm_repo" add -A
    git -C "$sbm_repo" commit -q -m "feat: add symbol gate and beta"
    sbm_tip=$(git -C "$sbm_repo" rev-parse HEAD)

    (
      cd "$sbm_repo" || exit 1
      unset AESOP_ROOT
      stdin_line="$sbm_tip $sbm_tip refs/heads/feature $sbm_base"
      sbm_out=$(printf '%s\n' "$stdin_line" | check_symbol_survival 2>&1)
      sbm_rc=$?
      if [ "$sbm_rc" -eq 0 ]; then
        printf 'FAIL: check_symbol_survival must fail closed when the base gate copy is missing (rc=0)\n'
        exit 1
      fi
      case "$sbm_out" in
        *pr_symbol_survival_check.py*) ;;
        *) printf 'FAIL: missing base gate must name the script in its message; got: %s\n' "$sbm_out"; exit 1 ;;
      esac
      printf 'PASS: check_symbol_survival fails closed (rc=%s) and names the missing base gate\n' "$sbm_rc"
    )
  )
  if [ $? -eq 0 ]; then
    test_passed=$((test_passed + 1))
  else
    test_failed=$((test_failed + 1))
  fi

  printf '\n=== Test 32: check_emit_receipt runs the BASE emit_receipt.py, not a stubbed pushed copy (a RED base matrix blocks) ===\n'
  (
    er_repo="$tmpdir/emit_base_repo"
    rm -rf "$er_repo"
    git init -q -b main "$er_repo"
    git -C "$er_repo" config user.email "test@example.com"
    git -C "$er_repo" config user.name "Test User"
    git -C "$er_repo" config core.autocrlf false
    mkdir -p "$er_repo/tools"
    printf 'import sys\nsys.exit(3)\n' > "$er_repo/tools/emit_receipt.py"
    printf 'seed\n' > "$er_repo/seed.txt"
    git -C "$er_repo" add -A
    git -C "$er_repo" commit -q -m base
    er_base=$(git -C "$er_repo" rev-parse HEAD)
    git -C "$er_repo" update-ref refs/remotes/origin/main "$er_base"

    # The PUSHED tree stubs the receipt emitter to always exit 0 (green).
    git -C "$er_repo" checkout -q -b feature
    printf 'import sys\nsys.exit(0)\n' > "$er_repo/tools/emit_receipt.py"
    git -C "$er_repo" commit -qam "push: stub the receipt emitter to always pass"

    # Shim: gh --version must succeed so the check reaches the emitter on any host.
    er_fakebin="$tmpdir/emit_fakebin"
    rm -rf "$er_fakebin"
    mkdir -p "$er_fakebin"
    printf '#!/bin/sh\nexit 0\n' > "$er_fakebin/gh"
    chmod +x "$er_fakebin/gh"

    (
      cd "$er_repo" || exit 1
      unset AESOP_ROOT
      export PATH="$er_fakebin:$PATH"
      export AESOP_RECEIPT_EMIT=1
      export AESOP_RECEIPT_KEY="test-key-material-not-a-secret"
      export AESOP_RECEIPT_TIMEOUT=60
      if check_emit_receipt < /dev/null >/dev/null 2>&1; then
        printf 'FAIL: check_emit_receipt ran the pushed always-pass stub; it must run the base copy and BLOCK on red\n'
        exit 1
      fi
      printf 'PASS: check_emit_receipt ran the base copy and blocked on its red matrix\n'
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
    printf '\nAll 32 tests passed.\n'
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

  # Resolve the python interpreter ONCE for this push (bounded probe, see
  # resolve_py_bin). Every gate calls resolve_py_bin inside a $(...) subshell,
  # which inherits this variable, so a hung python3 costs one bounded probe.
  # No usable interpreter -> fail closed here; the hook body never runs without one.
  if ! _AESOP_PY_BIN=$(resolve_py_bin); then
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

  # BOOTSTRAP: no trunk on origin yet (fresh remote); the base is HEAD. Printed once per push.
  if ! gate_trunk_ref >/dev/null 2>&1; then
    printf 'BOOTSTRAP: no trunk on origin; base = HEAD\n' >&2
    log_event "bootstrap_base_head"
  fi

  # Base gate copies must be readable before any gate runs (fail-closed; see gate_base_dir).
  if ! gate_base_dir >/dev/null; then
    printf 'FATAL: the base gate copies cannot run (trunk or HEAD tools/ unreadable). Push blocked (fail-closed).\n' >&2
    log_block "gate_base_unavailable"
    exit 1
  fi

  # GATE-CHANGED banner for pushes that edit gate files (non-blocking; see check_gate_tamper).
  check_gate_tamper <<< "$prepush_stdin"

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

  # G13 symbol survival (BLOCKING). Same captured stdin, same range mechanism.
  if ! check_symbol_survival <<< "$prepush_stdin"; then
    printf 'Error: PR symbol survival check failed (a merge commit dropped a symbol this branch added). Push blocked.\n' >&2
    log_block "symbol_survival_failure"
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

  # Same captured stdin as the other range gates: check_metrics scans the pushed refs,
  # not the current branch (v0.10.1 incident).
  if ! check_metrics <<< "$prepush_stdin"; then
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

  if ! check_pyflakes_ratchet; then
    printf 'Error: Pyflakes ratchet check failed (new or stale findings). Push blocked.\n' >&2
    log_block "pyflakes_ratchet_failure"
    exit 1
  fi

  # Escape main-full-windows-shard2-ui-acceptance-timeout: in-process HTTP test timeout lint.
  if ! check_inprocess_http_timeout; then
    printf 'Error: In-process HTTP timeout lint failed (short client timeout on an in-process server). Push blocked.\n' >&2
    log_block "inprocess_http_timeout_failure"
    exit 1
  fi

  # G6 subprocess ratchet: the same invocation ci.yml runs, judged locally (see check_subprocess_guard_ratchet).
  if ! check_subprocess_guard_ratchet; then
    printf 'Error: Subprocess anti-pattern ratchet failed (new finding above baseline). Push blocked.\n' >&2
    log_block "subprocess_guard_ratchet_failure"
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

  # Receipt conclusion gate: runs last, after every gate has passed. A red
  # required part or an unreadable conclusion blocks (see check_emit_receipt);
  # check_emit_receipt has already logged the block reason to the audit log.
  if ! check_emit_receipt; then
    printf 'Error: receipt gate blocked the push (a required part is red, or the conclusion is unreadable).\n' >&2
    exit 1
  fi

  exit 0
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
